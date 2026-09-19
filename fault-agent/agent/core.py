"""Fault agent core: applies and reverts faults on allowlisted Docker containers.

Safety properties (all covered by tests):
  * only containers labelled faultscope.injectable=true can be touched
  * every fault has a TTL and is reverted by the agent itself (dead-man's switch)
  * intent is persisted BEFORE acting, and everything is reverted on agent startup
  * apply verifies the effect; undo is idempotent and verifies recovery
  * one active fault per container / per experiment id
"""
import json
import logging
import os
import threading
import time
from typing import Callable

logger = logging.getLogger("fault-agent")

ALLOW_LABEL = "faultscope.injectable"


class FaultError(Exception):
    pass


class NotAllowed(FaultError):
    pass


class UnknownContainer(FaultError):
    pass


class UnsupportedFault(FaultError):
    pass


class Conflict(FaultError):
    pass


def _now() -> float:
    return time.time()


class FaultManager:
    def __init__(self, client, state_path: str, netem_image: str,
                 timer_factory: Callable = threading.Timer,
                 wait_running_s: float = 60.0, poll_s: float = 0.5,
                 expire_retry_s: float = 10.0, expire_max_retries: int = 5):
        self.client = client
        self.state_path = state_path
        self.netem_image = netem_image
        self._timer_factory = timer_factory
        self.wait_running_s = wait_running_s
        self.poll_s = poll_s
        self.expire_retry_s = expire_retry_s
        self.expire_max_retries = expire_max_retries
        self._lock = threading.RLock()
        self._timers: dict[str, object] = {}
        self._history: list[dict] = []
        self._active: dict[str, dict] = self._load()
        self._handlers = {
            "stop_container": (self._apply_stop, self._undo_stop),
            "pause_container": (self._apply_pause, self._undo_pause),
            "restart_container": (self._apply_restart, self._undo_restart),
            "latency": (self._apply_latency, self._undo_latency),
        }

    # ---- public ---------------------------------------------------------------

    def supported(self) -> list[str]:
        return sorted(self._handlers)

    def apply(self, experiment_id: str, container: str, fault_type: str,
              parameters: dict | None, ttl_s: int) -> dict:
        if fault_type not in self._handlers:
            raise UnsupportedFault(f"unsupported fault '{fault_type}' (supported: {', '.join(self.supported())})")
        params = parameters or {}
        c = self._get_allowed(container)

        with self._lock:
            if experiment_id in self._active:
                raise Conflict(f"experiment {experiment_id} already has an active fault")
            busy = [e for e, r in self._active.items() if r["container"] == container]
            if busy:
                raise Conflict(f"container '{container}' already has an active fault (experiment {busy[0]})")
            record = {"experiment_id": experiment_id, "container": container, "fault_type": fault_type,
                      "parameters": params, "ttl_s": ttl_s, "applied_at": _now(),
                      "expires_at": _now() + ttl_s}
            self._active[experiment_id] = record  # intent first: a crash mid-apply is still reverted
            self._save()

        apply_fn, undo_fn = self._handlers[fault_type]
        try:
            details = apply_fn(c, params)
        except Exception:
            logger.exception("apply failed for %s; undoing partial effects", experiment_id)
            try:
                undo_fn(self._get_allowed(container), params)
            except Exception:
                logger.exception("undo after failed apply also failed for %s", experiment_id)
            with self._lock:
                self._active.pop(experiment_id, None)
                self._save()
            raise

        with self._lock:
            self._arm_timer(experiment_id, ttl_s)
        return {"experiment_id": experiment_id, "container": container, "fault_type": fault_type,
                "applied": True, "verified": True, "ttl_s": ttl_s, "details": details}

    def rollback(self, experiment_id: str, reverted_by: str = "rollback") -> dict:
        with self._lock:
            record = self._active.get(experiment_id)
        if record is None:
            previous = next((h for h in reversed(self._history) if h["experiment_id"] == experiment_id), None)
            return {"experiment_id": experiment_id, "status": "not_active",
                    "note": "no active fault (never applied, or already reverted)",
                    "previous": previous}

        c = self._get_container(record["container"])
        details = self._handlers[record["fault_type"]][1](c, record["parameters"])  # raises -> stays active

        with self._lock:
            self._active.pop(experiment_id, None)
            timer = self._timers.pop(experiment_id, None)
            if timer is not None:
                timer.cancel()
            entry = {"experiment_id": experiment_id, "container": record["container"],
                     "fault_type": record["fault_type"], "reverted_by": reverted_by,
                     "reverted_at": _now(), "applied_at": record["applied_at"]}
            self._history.append(entry)
            self._save()
        return {"experiment_id": experiment_id, "status": "reverted", "reverted_by": reverted_by,
                "details": details}

    def list_active(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._active.values()]

    def history(self) -> list[dict]:
        with self._lock:
            return list(self._history)

    def revert_all_on_startup(self) -> dict:
        """Revert every fault left in the state file (agent or host crashed while faults were active)."""
        with self._lock:
            ids = list(self._active)
        result = {"reverted": [], "failed": []}
        for exp_id in ids:
            try:
                self.rollback(exp_id, reverted_by="startup")
                result["reverted"].append(exp_id)
            except Exception as exc:
                logger.error("startup revert of %s failed: %s", exp_id, exc)
                result["failed"].append({"experiment_id": exp_id, "error": str(exc)})
                with self._lock:
                    self._arm_timer(exp_id, self.expire_retry_s)  # keep trying
        return result

    # ---- ttl dead-man's switch -----------------------------------------------

    def _arm_timer(self, experiment_id: str, delay_s: float, attempt: int = 0) -> None:
        timer = self._timer_factory(delay_s, self._expire, args=(experiment_id, attempt))
        timer.daemon = True
        self._timers[experiment_id] = timer
        timer.start()

    def _expire(self, experiment_id: str, attempt: int = 0) -> None:
        try:
            self.rollback(experiment_id, reverted_by="ttl")
            logger.warning("fault for experiment %s reverted by TTL (control plane did not roll back)", experiment_id)
        except Exception as exc:
            logger.error("TTL revert of %s failed (attempt %d): %s", experiment_id, attempt + 1, exc)
            if attempt + 1 < self.expire_max_retries:
                with self._lock:
                    if experiment_id in self._active:
                        self._arm_timer(experiment_id, self.expire_retry_s, attempt + 1)

    # ---- state ----------------------------------------------------------------

    def _load(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, ValueError):
            return {}

    def _save(self) -> None:
        directory = os.path.dirname(self.state_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._active, fh)
        os.replace(tmp, self.state_path)

    # ---- docker helpers -------------------------------------------------------

    def _get_container(self, name: str):
        try:
            return self.client.containers.get(name)
        except Exception as exc:
            raise UnknownContainer(f"container '{name}' not found: {exc}")

    def _get_allowed(self, name: str):
        c = self._get_container(name)
        if (c.labels or {}).get(ALLOW_LABEL) != "true":
            raise NotAllowed(f"container '{name}' is not labelled {ALLOW_LABEL}=true; refusing")
        return c

    def _wait_running(self, c) -> str:
        deadline = time.monotonic() + self.wait_running_s
        while True:
            c.reload()
            health = (c.attrs or {}).get("State", {}).get("Health", {}).get("Status")
            if c.status == "running" and health in (None, "healthy"):
                return f"running{'/' + health if health else ''}"
            if time.monotonic() >= deadline:
                raise FaultError(f"container '{c.name}' not running/healthy after {self.wait_running_s}s "
                                 f"(status={c.status}, health={health})")
            time.sleep(self.poll_s)

    def _tc(self, c, args: list[str]) -> str:
        out = self.client.containers.run(
            self.netem_image, command=args, entrypoint="tc", network_mode=f"container:{c.id}",
            cap_add=["NET_ADMIN"], remove=True, detach=False)
        return out.decode() if isinstance(out, (bytes, bytearray)) else str(out or "")

    # ---- fault implementations ------------------------------------------------

    def _apply_stop(self, c, params):
        c.stop(timeout=10)
        c.reload()
        if c.status == "running":
            raise FaultError(f"container '{c.name}' still running after stop")
        return {"action": "docker stop", "status": c.status}

    def _undo_stop(self, c, params):
        c.reload()
        if c.status != "running":
            c.start()
        return {"action": "docker start", "status": self._wait_running(c)}

    def _apply_pause(self, c, params):
        c.pause()
        c.reload()
        if c.status != "paused":
            raise FaultError(f"container '{c.name}' not paused (status={c.status})")
        return {"action": "docker pause", "status": c.status}

    def _undo_pause(self, c, params):
        c.reload()
        if c.status == "paused":
            c.unpause()
        elif c.status != "running":
            c.start()
        return {"action": "docker unpause", "status": self._wait_running(c)}

    def _apply_restart(self, c, params):
        c.restart(timeout=10)
        return {"action": "docker restart", "status": self._wait_running(c)}

    def _undo_restart(self, c, params):
        c.reload()
        if c.status != "running":
            c.start()
        return {"action": "ensure running", "status": self._wait_running(c)}

    def _apply_latency(self, c, params):
        c.reload()
        if c.status != "running":
            raise FaultError(f"cannot add latency: container '{c.name}' is {c.status}")
        latency = int(params["latency_ms"])
        jitter = int(params.get("jitter_ms", 0))
        args = ["qdisc", "replace", "dev", "eth0", "root", "netem", "delay", f"{latency}ms"]
        if jitter:
            args.append(f"{jitter}ms")
        self._tc(c, args)
        shown = self._tc(c, ["qdisc", "show", "dev", "eth0"])
        if "netem" not in shown:
            raise FaultError(f"netem not active after apply: {shown.strip()}")
        return {"action": "tc netem", "latency_ms": latency, "jitter_ms": jitter, "qdisc": shown.strip()}

    def _undo_latency(self, c, params):
        c.reload()
        if c.status != "running":
            return {"action": "tc netem del", "note": f"container is {c.status}; qdisc vanishes with its network"}
        try:
            self._tc(c, ["qdisc", "del", "dev", "eth0", "root"])
        except Exception:
            pass  # already absent; verified below
        shown = self._tc(c, ["qdisc", "show", "dev", "eth0"])
        if "netem" in shown:
            raise FaultError(f"netem still active after undo: {shown.strip()}")
        return {"action": "tc netem del", "qdisc": shown.strip()}
