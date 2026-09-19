"""Remediation engine: request -> policy -> (approval) -> execute -> verify -> (next strategy).

Every step is written to the audit log. Nothing runs unless the policy engine returned `allow`
(or an administrator approved a `needs_approval` action, after the policy was re-evaluated).
"""
import logging
import threading
import time
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import text

from app.database import SessionLocal
from app.experiments.models import ExperimentRow
from app.models import ServiceRow
from app.remediation.executor import ExecutionError
from app.remediation.models import AuditLogRow, RemediationRow
from app.remediation.policy import ALLOWLIST, COOLDOWN_S, evaluate
from app.remediation.verification import verify_recovery

logger = logging.getLogger("remediation")

EXECUTED_STATUSES = ("RUNNING", "VERIFIED", "VERIFICATION_FAILED", "FAILED")
MAX_FOLLOWUP_STRATEGIES = 2


class RemediationError(Exception):
    pass


class NotFound(RemediationError):
    pass


class InvalidState(RemediationError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _epoch(value: datetime) -> float:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp()


def to_dict(row: RemediationRow) -> dict:
    return {"id": row.id, "parent_id": row.parent_id, "action": row.action, "target": row.target,
            "params": row.params, "mode": row.mode, "status": row.status, "requested_by": row.requested_by,
            "approved_by": row.approved_by, "source": row.source, "source_ref": row.source_ref, "policy": row.policy,
            "result": row.result, "verification": row.verification, "remaining_strategies": row.remaining_strategies,
            "error": row.error, "created_at": row.created_at.isoformat() if row.created_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None}


class GatewayProbe:
    """Real end-to-end requests through the gateway: the same path users take."""

    def __init__(self, base_url: str = "http://gateway:8090", n: int = 100000, spacing_s: float = 0.25):
        self.base_url, self.n, self.spacing_s = base_url, n, spacing_s

    def __call__(self, count: int) -> list[dict]:
        results = []
        with httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(6.0, connect=2.0)) as client:
            for _ in range(count):
                started = time.perf_counter()
                try:
                    status = client.post("/api/jobs", params={"n": self.n}).status_code
                except httpx.HTTPError:
                    status = 0
                results.append({"ok": 200 <= status < 300, "status": status,
                                "latency_ms": (time.perf_counter() - started) * 1000})
                time.sleep(self.spacing_s)
        return results


def gateway_reference_p95(session_factory=SessionLocal, service: str = "gateway") -> float | None:
    """Healthy p95 of the entry service over the last 15 minutes (excluding the most recent minute)."""
    sql = text("""
        select percentile_cont(0.95) within group (order by latency_ms) as p95, count(*) as n
        from telemetry_events
        where service = :service and event_type = 'http_request' and status_code < 500
          and timestamp > now() - interval '15 minutes' and timestamp < now() - interval '60 seconds'
    """)
    try:
        with session_factory() as db:
            row = db.execute(sql, {"service": service}).first()
        return float(row.p95) if row and row.n >= 20 and row.p95 is not None else None
    except Exception:
        return None  # sqlite (tests) or no data: verification then falls back to an absolute threshold


class RemediationEngine:
    def __init__(self, executor, probe, session_factory=SessionLocal, reference_p95=gateway_reference_p95,
                 verify=verify_recovery, verify_kwargs: dict | None = None):
        self.executor = executor
        self.probe = probe
        self.session_factory = session_factory
        self.reference_p95 = lambda: reference_p95(session_factory)
        self.verify = verify
        self.verify_kwargs = verify_kwargs or {}

    # ---- public API ---------------------------------------------------------------------------

    def request(self, *, action: str, target: str, params: dict | None = None, mode: str = "dry_run",
                requested_by: str, source: str = "manual", source_ref: str | None = None,
                strategies: list[str] | None = None, parent_id: str | None = None) -> dict:
        if mode not in ("dry_run", "execute"):
            raise RemediationError("mode must be 'dry_run' or 'execute'")
        strategies = list(strategies or [])
        if len(strategies) > MAX_FOLLOWUP_STRATEGIES or any(s not in ALLOWLIST for s in strategies):
            raise RemediationError(f"strategies must be at most {MAX_FOLLOWUP_STRATEGIES} allowlisted actions")

        request = {"action": action, "target": target, "params": params or {}}
        with self.session_factory() as db:
            verdict = self._evaluate(db, request)
            status = self._initial_status(verdict.verdict, mode)
            row = RemediationRow(id=str(uuid.uuid4()), parent_id=parent_id, action=action, target=target,
                                 params=params or {}, mode=mode, status=status, requested_by=requested_by,
                                 source=source, source_ref=source_ref, policy=verdict.as_dict(),
                                 remaining_strategies=strategies)
            db.add(row)
            self._audit(db, requested_by, "requested", row.id,
                        {"action": action, "target": target, "mode": mode, "source": source, "source_ref": source_ref})
            self._audit(db, "policy", "policy_verdict", row.id, verdict.as_dict())
            db.commit()
            data = to_dict(row)
        if status == "RUNNING":
            self._start(data["id"])
        return data

    def approve(self, action_id: str, actor: str) -> dict:
        with self.session_factory() as db:
            row = db.get(RemediationRow, action_id)
            if row is None:
                raise NotFound(action_id)
            if row.status != "PENDING_APPROVAL":
                raise InvalidState(f"action is {row.status}; only PENDING_APPROVAL can be approved")
            verdict = self._evaluate(db, {"action": row.action, "target": row.target, "params": row.params},
                                     approved=True, exclude_id=row.id)
            row.policy = verdict.as_dict()
            row.approved_by = actor
            if verdict.verdict == "allow":
                row.status = "RUNNING"
                self._audit(db, actor, "approved", row.id, verdict.as_dict())
            else:  # circumstances changed since the request (e.g. an experiment started): do not run
                row.status, row.finished_at = "DENIED", _now()
                self._audit(db, actor, "approval_denied_by_policy", row.id, verdict.as_dict())
            db.commit()
            run = row.status == "RUNNING"
        if run:
            self._start(action_id)
        return self.get(action_id)

    def reject(self, action_id: str, actor: str) -> dict:
        with self.session_factory() as db:
            row = db.get(RemediationRow, action_id)
            if row is None:
                raise NotFound(action_id)
            if row.status != "PENDING_APPROVAL":
                raise InvalidState(f"action is {row.status}; only PENDING_APPROVAL can be rejected")
            row.status, row.finished_at = "REJECTED", _now()
            self._audit(db, actor, "rejected", row.id, {})
            db.commit()
        return self.get(action_id)

    def get(self, action_id: str) -> dict:
        with self.session_factory() as db:
            row = db.get(RemediationRow, action_id)
            if row is None:
                raise NotFound(action_id)
            return to_dict(row)

    def list_actions(self, limit: int = 30) -> list[dict]:
        with self.session_factory() as db:
            rows = db.query(RemediationRow).order_by(RemediationRow.created_at.desc()).limit(limit).all()
            return [to_dict(r) for r in rows]

    def audit_log(self, limit: int = 100) -> list[dict]:
        with self.session_factory() as db:
            rows = db.query(AuditLogRow).order_by(AuditLogRow.id.desc()).limit(limit).all()
            return [{"id": r.id, "ts": r.ts.isoformat(), "actor": r.actor, "event": r.event, "ref_id": r.ref_id,
                     "detail": r.detail} for r in rows]

    def dry_evaluate(self, action: str, target: str, params: dict | None = None) -> dict:
        """Policy verdict without recording anything (used to annotate recommendations)."""
        with self.session_factory() as db:
            return self._evaluate(db, {"action": action, "target": target, "params": params or {}}).as_dict()

    # ---- internals ------------------------------------------------------------------------------

    @staticmethod
    def _initial_status(verdict: str, mode: str) -> str:
        if verdict == "deny":
            return "DENIED"
        if verdict == "unsupported":
            return "UNSUPPORTED"
        if mode == "dry_run":
            return "PLANNED"
        return "RUNNING" if verdict == "allow" else "PENDING_APPROVAL"

    def _evaluate(self, db, request: dict, approved: bool = False, exclude_id: str | None = None):
        registry = {s.name: {"kind": s.kind, "container": s.container} for s in db.query(ServiceRow).all()}
        now = time.time()
        active = db.query(ExperimentRow).filter(ExperimentRow.active_slot.is_(True)).first() is not None
        running = db.query(RemediationRow).filter(RemediationRow.status == "RUNNING")
        if exclude_id:
            running = running.filter(RemediationRow.id != exclude_id)
        recent = [{"target": r.target, "ts": _epoch(r.created_at), "status": r.status}
                  for r in db.query(RemediationRow).filter(RemediationRow.mode == "execute",
                                                           RemediationRow.status.in_(EXECUTED_STATUSES)).all()
                  if now - _epoch(r.created_at) < COOLDOWN_S and r.id != exclude_id]
        return evaluate(request, registry, active_experiment=active, running_remediation=running.first() is not None,
                        recent_actions=recent, now=now, approved=approved)

    def _audit(self, db, actor: str, event: str, ref_id: str, detail: dict) -> None:
        db.add(AuditLogRow(ts=_now(), actor=actor, event=event, ref_id=ref_id, detail=detail))

    def _update(self, action_id: str, audit: tuple | None = None, **fields) -> None:
        with self.session_factory() as db:
            db.query(RemediationRow).filter_by(id=action_id).update(fields)
            if audit:
                self._audit(db, *audit)
            db.commit()

    def _start(self, action_id: str) -> None:
        threading.Thread(target=self._run, args=(action_id,), name=f"remediation-{action_id[:8]}", daemon=True).start()

    def _run(self, action_id: str) -> None:
        try:
            self._execute_and_verify(action_id)
        except Exception as exc:
            logger.exception("remediation %s crashed", action_id)
            self._update(action_id, status="FAILED", error=f"{type(exc).__name__}: {exc}", finished_at=_now(),
                         audit=("system", "crashed", action_id, {"error": str(exc)}))

    def _execute_and_verify(self, action_id: str) -> None:
        row = self.get(action_id)
        with self.session_factory() as db:
            service = db.get(ServiceRow, row["target"])
            service = {"kind": service.kind, "container": service.container}
        try:
            result = self.executor.execute(row["action"], service, row["params"])
        except ExecutionError as exc:
            self._update(action_id, status="FAILED", error=str(exc), finished_at=_now(),
                         audit=("system", "execution_failed", action_id, {"error": str(exc)}))
            return
        self._update(action_id, result=result, audit=("system", "executed", action_id, result))

        verification = self.verify(self.probe, self.reference_p95(), **self.verify_kwargs)
        status = "VERIFIED" if verification["verified"] else "VERIFICATION_FAILED"
        self._update(action_id, status=status, verification=verification, finished_at=_now(),
                     audit=("system", "verification", action_id,
                            {"verified": verification["verified"], "recovery_time_s": verification.get("recovery_time_s")}))

        remaining = row["remaining_strategies"]
        if status == "VERIFICATION_FAILED" and remaining:
            nxt, rest = remaining[0], remaining[1:]
            self.request(action=nxt, target=row["target"], mode="execute", requested_by="system:next-strategy",
                         source="strategy", source_ref=action_id, strategies=rest, parent_id=action_id)
