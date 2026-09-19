"""Kubernetes fault injector (used when FAULTSCOPE_RUNTIME=kubernetes).

Same contract as the Docker injector (see injectors.py): inject() applies and VERIFIES a fault, rollback() is
idempotent and verifies recovery. It talks to the Kubernetes API with the pod's own service account, which RBAC
restricts to Deployments/pods in ONE namespace (k8s/rbac.yaml).

Safety rules, mirroring the Docker fault-agent:
  * allowlist: only Deployments labelled faultscope.injectable=true can be touched
  * intent is recorded ON the Deployment (annotations) before acting, so any process can undo it later
  * every fault has an expiry; a watchdog and start-up recovery revert expired or orphaned faults
  * one fault per Deployment

Honest limit: the watchdog lives inside the control plane. If the control plane dies, Kubernetes restarts it and
start-up recovery reverts the fault; unlike the Docker agent there is no separate process guarding the gap.
"""
import logging
import os
import threading
import time

import httpx

from app.experiments.injectors import InjectorError

logger = logging.getLogger("k8s-injector")

ALLOW_LABEL = "faultscope.injectable"
ANN_EXPERIMENT = "faultscope.io/experiment"
ANN_ORIGINAL_REPLICAS = "faultscope.io/original-replicas"
ANN_EXPIRES_AT = "faultscope.io/expires-at"
SUPPORTED_FAULTS = frozenset({"stop_container", "restart_container"})

_SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"


class K8sError(RuntimeError):
    pass


class K8sApi:
    """The few Kubernetes API calls the injector needs (plain REST, no extra dependency)."""

    def __init__(self, namespace: str | None = None, client: httpx.Client | None = None):
        self.namespace = namespace or os.getenv("FAULTSCOPE_K8S_NAMESPACE") or self._read(f"{_SA_DIR}/namespace") or "default"
        if client is not None:
            self._client = client
            return
        token = self._read(f"{_SA_DIR}/token")
        if not token:
            raise K8sError("no service account token: FAULTSCOPE_RUNTIME=kubernetes only works inside a cluster")
        self._client = httpx.Client(
            base_url=os.getenv("FAULTSCOPE_K8S_API", "https://kubernetes.default.svc"),
            headers={"Authorization": f"Bearer {token}"},
            verify=f"{_SA_DIR}/ca.crt", timeout=httpx.Timeout(15.0, connect=3.0))

    @staticmethod
    def _read(path: str) -> str | None:
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return None

    def _call(self, method: str, path: str, ok=(200, 201), **kwargs):
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise K8sError(f"kubernetes API unreachable: {exc}")
        if response.status_code == 404:
            return None
        if response.status_code not in ok:
            raise K8sError(f"kubernetes API {method} {path} -> {response.status_code}: {response.text[:200]}")
        return response.json()

    def _deployments(self) -> str:
        return f"/apis/apps/v1/namespaces/{self.namespace}/deployments"

    def get_deployment(self, name: str) -> dict | None:
        return self._call("GET", f"{self._deployments()}/{name}")

    def list_deployments(self) -> list[dict]:
        return (self._call("GET", self._deployments()) or {}).get("items", [])

    def patch_deployment(self, name: str, body: dict) -> dict | None:
        return self._call("PATCH", f"{self._deployments()}/{name}", json=body,
                          headers={"Content-Type": "application/merge-patch+json"})

    def set_replicas(self, name: str, replicas: int) -> None:
        self._call("PATCH", f"{self._deployments()}/{name}/scale", json={"spec": {"replicas": replicas}},
                   headers={"Content-Type": "application/merge-patch+json"})

    def list_pods(self, match_labels: dict) -> list[dict]:
        selector = ",".join(f"{k}={v}" for k, v in sorted(match_labels.items()))
        return (self._call("GET", f"/api/v1/namespaces/{self.namespace}/pods", params={"labelSelector": selector})
                or {}).get("items", [])

    def delete_pod(self, name: str) -> None:
        self._call("DELETE", f"/api/v1/namespaces/{self.namespace}/pods/{name}", ok=(200, 202))


def _annotations(dep: dict) -> dict:
    return (dep.get("metadata") or {}).get("annotations") or {}


def _live_pods(pods: list[dict]) -> list[dict]:
    return [p for p in pods if not (p.get("metadata") or {}).get("deletionTimestamp")]


class KubernetesInjector:
    name = "kubernetes"

    def __init__(self, api: K8sApi, ttl_margin_s: int = 20, wait_s: float = 90.0, poll_s: float = 1.0,
                 clock=time.time, sleep=time.sleep):
        self.api, self.ttl_margin_s, self.wait_s, self.poll_s = api, ttl_margin_s, wait_s, poll_s
        self._clock, self._sleep = clock, sleep

    # ---- helpers -----------------------------------------------------------------------------

    def _wait(self, predicate, what: str) -> None:
        deadline = self._clock() + self.wait_s
        while True:
            if predicate():
                return
            if self._clock() >= deadline:
                raise InjectorError(f"timed out after {self.wait_s:.0f}s waiting for {what}")
            self._sleep(self.poll_s)

    def _deployment(self, name: str) -> dict:
        try:
            dep = self.api.get_deployment(name)
        except K8sError as exc:
            raise InjectorError(str(exc))
        if dep is None:
            raise InjectorError(f"deployment '{name}' not found in namespace '{self.api.namespace}'")
        return dep

    @staticmethod
    def _selector(dep: dict) -> dict:
        return (dep.get("spec") or {}).get("selector", {}).get("matchLabels") or {}

    def _ready(self, name: str, replicas: int) -> bool:
        dep = self.api.get_deployment(name) or {}
        st = dep.get("status") or {}
        return (st.get("readyReplicas") or 0) >= replicas

    # ---- injector contract -------------------------------------------------------------------

    def inject(self, exp: dict) -> dict:
        fault, name = exp["fault_type"], exp["target"]
        if fault not in SUPPORTED_FAULTS:
            raise InjectorError(f"fault '{fault}' is not supported on Kubernetes (supported: "
                                f"{', '.join(sorted(SUPPORTED_FAULTS))})")
        try:
            dep = self._deployment(name)
            if ((dep.get("metadata") or {}).get("labels") or {}).get(ALLOW_LABEL) != "true":
                raise InjectorError(f"deployment '{name}' is not labelled {ALLOW_LABEL}=true; refusing")
            if ANN_EXPERIMENT in _annotations(dep):
                raise InjectorError(f"deployment '{name}' already has an active fault "
                                    f"(experiment {_annotations(dep)[ANN_EXPERIMENT]})")
            replicas = (dep.get("spec") or {}).get("replicas")
            if not replicas:
                raise InjectorError(f"deployment '{name}' has no running replicas to disturb")
            expires = self._clock() + exp["duration_s"] + self.ttl_margin_s
            selector = self._selector(dep)

            # intent first: record it on the Deployment before changing anything
            self.api.patch_deployment(name, {"metadata": {"annotations": {
                ANN_EXPERIMENT: exp["id"], ANN_ORIGINAL_REPLICAS: str(replicas), ANN_EXPIRES_AT: f"{expires:.0f}"}}})

            if fault == "stop_container":
                self.api.set_replicas(name, 0)
                self._wait(lambda: not _live_pods(self.api.list_pods(selector)), "all pods to terminate")
                return {"mode": "kubernetes", "applied": True, "verified": True, "action": "scale to 0",
                        "deployment": name, "original_replicas": replicas}
            old = {p["metadata"]["name"] for p in self.api.list_pods(selector)}
            for pod in old:
                self.api.delete_pod(pod)
            self._wait(lambda: not old & {p["metadata"]["name"] for p in _live_pods(self.api.list_pods(selector))},
                       "old pods to disappear")
            return {"mode": "kubernetes", "applied": True, "verified": True, "action": "delete all pods",
                    "deployment": name, "deleted_pods": sorted(old)}
        except K8sError as exc:
            raise InjectorError(str(exc))

    def rollback(self, exp: dict) -> dict:
        name = exp["target"]
        try:
            dep = self.api.get_deployment(name)
            if dep is None:
                return {"mode": "kubernetes", "note": f"deployment '{name}' not found; nothing to restore"}
            return self._restore(name, dep)
        except K8sError as exc:
            raise InjectorError(str(exc))

    def _restore(self, name: str, dep: dict) -> dict:
        ann = _annotations(dep)
        original = ann.get(ANN_ORIGINAL_REPLICAS)
        replicas = int(original) if original else (dep.get("spec") or {}).get("replicas") or 1
        if original:
            self.api.set_replicas(name, replicas)
        self._wait(lambda: self._ready(name, replicas), f"{replicas} ready replica(s) of '{name}'")
        if original:  # clear the intent only after recovery is verified
            self.api.patch_deployment(name, {"metadata": {"annotations": {
                ANN_EXPERIMENT: None, ANN_ORIGINAL_REPLICAS: None, ANN_EXPIRES_AT: None}}})
        return {"mode": "kubernetes", "action": "restored" if original else "ensure ready",
                "deployment": name, "ready_replicas": replicas}

    # ---- dead-man's switch -------------------------------------------------------------------

    def recover_expired(self, include_unexpired: bool = False) -> list[str]:
        """Restore every Deployment carrying a fault annotation that has expired (or any, at start-up)."""
        restored = []
        for dep in self.api.list_deployments():
            ann = _annotations(dep)
            if ANN_ORIGINAL_REPLICAS not in ann:
                continue
            expires = float(ann.get(ANN_EXPIRES_AT, "0") or 0)
            if include_unexpired or expires <= self._clock():
                name = dep["metadata"]["name"]
                try:
                    self._restore(name, dep)
                    restored.append(name)
                    logger.warning("fault on %s reverted by the watchdog", name)
                except Exception:
                    logger.exception("watchdog could not restore %s", name)
        return restored

    def start_watchdog(self, interval_s: float = 15.0) -> threading.Thread:
        def loop():
            while True:
                try:
                    self.recover_expired()
                except Exception:
                    logger.exception("watchdog pass failed")
                time.sleep(interval_s)

        thread = threading.Thread(target=loop, name="k8s-fault-watchdog", daemon=True)
        thread.start()
        return thread
