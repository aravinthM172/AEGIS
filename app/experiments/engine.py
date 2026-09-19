r"""Experiment engine: lifecycle, guardrails and rollback discipline.

Lifecycle:  PENDING -> RUNNING[baseline -> injecting -> recovering] -> COMPLETED
                                    \-> ABORTED (operator) / FAILED (error)
                               ROLLBACK_FAILED  (fault may still be applied; blocks new experiments)

Safety rules enforced here:
  * at most one active experiment (DB unique index on experiments.active_slot)
  * injector.rollback() runs on EVERY exit path once injection was attempted
  * a failed rollback keeps the slot occupied until it is resolved
  * experiments left active by a control-plane restart are rolled back on startup
"""
import logging
import threading
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.experiments.catalog import validate_request
from app.experiments.injectors import (
    InjectorUnavailable,
    injector_for as default_injector_for,
    real_run_preflight as default_real_run_preflight,
)
from app.experiments.models import ExperimentEventRow, ExperimentRow
from app.experiments.workload import WorkloadRunner
from app.models import ServiceRow

logger = logging.getLogger("experiments")


class ExperimentError(Exception):
    pass


class ValidationFailed(ExperimentError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class ActiveExperimentExists(ExperimentError):
    def __init__(self, active_id: str | None):
        super().__init__(f"experiment {active_id} is already active")
        self.active_id = active_id


class NotFound(ExperimentError):
    pass


class InvalidState(ExperimentError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def to_dict(exp: ExperimentRow, events: list[ExperimentEventRow] | None = None) -> dict:
    data = {
        "id": exp.id,
        "name": exp.name,
        "hypothesis": exp.hypothesis,
        "target": exp.target,
        "fault_type": exp.fault_type,
        "parameters": exp.parameters,
        "duration_s": exp.duration_s,
        "baseline_s": exp.baseline_s,
        "recovery_s": exp.recovery_s,
        "dry_run": exp.dry_run,
        "workload_rps": exp.workload_rps or 0.0,
        "workload_n": exp.workload_n or 100000,
        "injector": exp.injector,
        "status": exp.status,
        "phase": exp.phase,
        "abort_requested": exp.abort_requested,
        "error": exp.error,
        "timeline": {
            "created_at": _iso(exp.created_at),
            "baseline_started_at": _iso(exp.baseline_started_at),
            "inject_started_at": _iso(exp.inject_started_at),
            "fault_applied_at": _iso(exp.fault_applied_at),
            "rollback_started_at": _iso(exp.rollback_started_at),
            "inject_ended_at": _iso(exp.inject_ended_at),
            "finished_at": _iso(exp.finished_at),
        },
        "result": exp.result,
    }
    if events is not None:
        data["events"] = [
            {"timestamp": _iso(e.timestamp), "event_type": e.event_type, "detail": e.detail}
            for e in events
        ]
    return data


def default_workload_factory(exp: dict) -> WorkloadRunner:
    return WorkloadRunner(rps=exp["workload_rps"], n=exp["workload_n"], trace_prefix=f"exp-{exp['id'][:8]}")


class ExperimentEngine:
    def __init__(self, session_factory=SessionLocal, injector_for=default_injector_for,
                 time_scale: float = 1.0, poll_interval_s: float = 1.0,
                 real_run_preflight=default_real_run_preflight,
                 workload_factory=default_workload_factory, on_finished=None, settle_s: float = 6.0):
        self.workload_factory = workload_factory
        self.on_finished = on_finished  # (exp_id) -> None, e.g. blast-radius analysis
        self.settle_s = settle_s        # let the telemetry consumer catch up before analysing
        self.session_factory = session_factory
        self.injector_for = injector_for
        # In-process abort signal: the DB flag is unreachable/slow when the experiment targets Postgres.
        self._abort_signals: dict[str, threading.Event] = {}
        self.real_run_preflight = real_run_preflight  # () -> list[str]; consulted only for real runs
        self.time_scale = time_scale  # test seam: shrink waits without changing recorded values
        self.poll_interval_s = poll_interval_s

    # ---- public API -------------------------------------------------------------

    def create(self, *, target: str, fault_type: str, duration_s: int, parameters: dict | None = None,
               baseline_s: int = 10, recovery_s: int = 15, dry_run: bool = True,
               hypothesis: str | None = None, name: str | None = None, workload_rps: float = 0.0,
               workload_n: int = 100000) -> dict:
        """Validate, persist and start an experiment. Raises ValidationFailed / ActiveExperimentExists."""
        with self.session_factory() as db:
            services = {
                s.name: {"kind": s.kind, "container": s.container}
                for s in db.query(ServiceRow).all()
            }
            errors, params = validate_request(
                target=target, fault_type=fault_type, parameters=parameters or {},
                duration_s=duration_s, baseline_s=baseline_s, recovery_s=recovery_s,
                dry_run=dry_run, services=services,
            )
            if not 0 <= workload_rps <= 25:
                errors.append("workload_rps must be between 0 and 25")
            if isinstance(workload_n, bool) or not isinstance(workload_n, int) or not 1 <= workload_n <= 5_000_000:
                errors.append("workload_n must be an integer between 1 and 5000000")
            if not errors and not dry_run:
                errors = self.real_run_preflight()
            if errors:
                raise ValidationFailed(errors)

            exp = ExperimentRow(
                id=str(uuid.uuid4()),
                name=name or f"{fault_type} on {target}",
                hypothesis=hypothesis,
                target=target, fault_type=fault_type, parameters=params,
                duration_s=duration_s, baseline_s=baseline_s, recovery_s=recovery_s,
                dry_run=dry_run, workload_rps=workload_rps, workload_n=workload_n, injector="dry_run" if dry_run else "docker",
                status="PENDING", abort_requested=False, active_slot=True,
            )
            try:
                db.add(exp)
                db.flush()  # parent row first: no relationship() exists to order the FK insert for us
                db.add(ExperimentEventRow(
                    experiment_id=exp.id, timestamp=_now(), event_type="created",
                    detail={"target": target, "fault_type": fault_type, "parameters": params,
                            "dry_run": dry_run},
                ))
                db.commit()
            except IntegrityError:
                db.rollback()
                active = db.query(ExperimentRow).filter(ExperimentRow.active_slot.is_(True)).first()
                if active is None:
                    raise  # some other integrity problem: never misreport it as "one at a time"
                raise ActiveExperimentExists(active.id)
            db.refresh(exp)
            data = to_dict(exp)

        threading.Thread(target=self._run, args=(data["id"],), name=f"experiment-{data['id'][:8]}",
                         daemon=True).start()
        return data

    def get(self, exp_id: str) -> dict:
        with self.session_factory() as db:
            exp = db.get(ExperimentRow, exp_id)
            if exp is None:
                raise NotFound(exp_id)
            events = (db.query(ExperimentEventRow).filter_by(experiment_id=exp_id)
                      .order_by(ExperimentEventRow.id).all())
            return to_dict(exp, events)

    def list_experiments(self, status: str | None = None, target: str | None = None, limit: int = 50) -> list[dict]:
        with self.session_factory() as db:
            query = db.query(ExperimentRow)
            if status:
                query = query.filter(ExperimentRow.status == status.upper())
            if target:
                query = query.filter(ExperimentRow.target == target)
            rows = query.order_by(ExperimentRow.created_at.desc()).limit(min(max(limit, 1), 200)).all()
            return [to_dict(r) for r in rows]

    def abort(self, exp_id: str) -> dict:
        """Ask a PENDING/RUNNING experiment to stop; the runner rolls back and finishes as ABORTED."""
        with self.session_factory() as db:
            exp = db.get(ExperimentRow, exp_id)
            if exp is None:
                raise NotFound(exp_id)
            if exp.status not in ("PENDING", "RUNNING"):
                raise InvalidState(f"experiment is {exp.status}; only PENDING or RUNNING can be aborted")
            exp.abort_requested = True
            db.add(ExperimentEventRow(experiment_id=exp_id, timestamp=_now(),
                                      event_type="abort_requested", detail={}))
            db.commit()
        self._abort_signals.setdefault(exp_id, threading.Event()).set()
        return self.get(exp_id)

    def retry_rollback(self, exp_id: str) -> dict:
        """Re-run rollback for a ROLLBACK_FAILED experiment; on success it becomes FAILED and frees the slot."""
        exp = self.get(exp_id)
        if exp["status"] != "ROLLBACK_FAILED":
            raise InvalidState(f"experiment is {exp['status']}; only ROLLBACK_FAILED can be retried")
        try:
            detail = self.injector_for(exp).rollback(exp)
        except Exception as exc:
            self._event(exp_id, "rollback_failed", {"error": f"{type(exc).__name__}: {exc}", "retry": True})
            raise InvalidState(f"rollback failed again: {exc}")
        self._event(exp_id, "fault_removed", {**detail, "retry": True})
        self._update(exp_id, status="FAILED", phase=None, active_slot=None, finished_at=_now(),
                     inject_ended_at=_now())
        self._event(exp_id, "failed", {"note": "rollback succeeded on retry"})
        return self.get(exp_id)

    def recover_orphans(self) -> list[str]:
        """Startup safety net: roll back experiments left active by a control-plane restart."""
        with self.session_factory() as db:
            ids = [e.id for e in db.query(ExperimentRow)
                   .filter(ExperimentRow.status.in_(("PENDING", "RUNNING"))).all()]
        for exp_id in ids:
            exp = self.get(exp_id)
            try:
                detail = self.injector_for(exp).rollback(exp)
            except Exception as exc:
                logger.error("orphaned experiment %s: rollback failed: %s", exp_id, exc)
                self._event(exp_id, "rollback_failed", {"error": f"{type(exc).__name__}: {exc}", "orphan": True})
                self._update(exp_id, status="ROLLBACK_FAILED", phase=None,
                             error="control plane restarted mid-experiment and rollback failed")
                continue
            self._event(exp_id, "orphan_recovered", detail)
            self._finish(exp_id, "ABORTED", "control plane restarted while the experiment was active; rollback executed")
        return ids

    # ---- runner -----------------------------------------------------------------

    def _run(self, exp_id: str) -> None:
        try:
            self._run_lifecycle(exp_id)
        except Exception:
            logger.exception("experiment %s runner crashed", exp_id)

    def _run_lifecycle(self, exp_id: str) -> None:
        exp = self.get(exp_id)
        runner = None
        if exp["workload_rps"]:
            runner = self.workload_factory(exp)
            runner.start()
        state = {"stopped": False}

        def stop_workload() -> None:
            """Stop traffic and store what was sent. Idempotent; runs before the experiment is finished."""
            if runner is not None and not state["stopped"]:
                state["stopped"] = True
                self._update(exp_id, result={"workload": runner.stop()})

        try:
            self._lifecycle(exp_id, stop_workload)
        finally:
            try:
                stop_workload()
            except Exception:
                logger.exception("experiment %s: could not stop the workload", exp_id)

    def _lifecycle(self, exp_id: str, stop_workload) -> None:
        exp = self.get(exp_id)
        with self.session_factory() as db:
            service = db.get(ServiceRow, exp["target"])
            exp["container"] = service.container if service else None
        try:
            injector = self.injector_for(exp)
        except InjectorUnavailable as exc:
            stop_workload()
            self._finish(exp_id, "FAILED", str(exc))
            return

        outcome, error = "COMPLETED", None
        inject_attempted = False

        try:
            self._update(exp_id, status="RUNNING", phase="baseline", baseline_started_at=_now())
            self._event(exp_id, "baseline_started", {"seconds": exp["baseline_s"]})
            if self._wait(exp_id, exp["baseline_s"]):
                outcome = "ABORTED"
            else:
                self._update(exp_id, phase="injecting", inject_started_at=_now())
                inject_attempted = True
                detail = injector.inject(exp)
                self._update(exp_id, fault_applied_at=_now())
                self._event(exp_id, "fault_injected", detail)
                if self._wait(exp_id, exp["duration_s"]):
                    outcome = "ABORTED"
        except Exception as exc:
            logger.exception("experiment %s failed", exp_id)
            outcome, error = "FAILED", f"{type(exc).__name__}: {exc}"

        if inject_attempted:
            try:
                self._update(exp_id, rollback_started_at=_now())
                detail = injector.rollback(exp)
                self._update(exp_id, inject_ended_at=_now())
                self._event(exp_id, "fault_removed", detail)
            except Exception as exc:
                logger.error("experiment %s: ROLLBACK FAILED: %s", exp_id, exc)
                self._event(exp_id, "rollback_failed", {"error": f"{type(exc).__name__}: {exc}"})
                self._update(exp_id, status="ROLLBACK_FAILED", phase=None,
                             error=f"rollback failed: {type(exc).__name__}: {exc}")
                stop_workload()
                return  # keep the slot occupied: the fault may still be applied

        if outcome == "COMPLETED":
            try:
                self._update(exp_id, phase="recovering")
                self._event(exp_id, "recovery_started", {"seconds": exp["recovery_s"]})
                if self._wait(exp_id, exp["recovery_s"]):
                    outcome = "ABORTED"
            except Exception as exc:
                logger.exception("experiment %s failed during recovery", exp_id)
                outcome, error = "FAILED", f"{type(exc).__name__}: {exc}"

        stop_workload()
        self._finish(exp_id, outcome, error)

    def _wait(self, exp_id: str, seconds: float) -> bool:
        """Sleep for `seconds`; return True early if an abort was requested."""
        deadline = time.monotonic() + seconds * self.time_scale
        signal = self._abort_signals.setdefault(exp_id, threading.Event())
        while True:
            if signal.is_set() or self._abort_requested(exp_id):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            signal.wait(min(self.poll_interval_s, remaining))

    # ---- persistence helpers ----------------------------------------------------

    def _abort_requested(self, exp_id: str) -> bool:
        with self.session_factory() as db:
            return bool(db.query(ExperimentRow.abort_requested).filter_by(id=exp_id).scalar())

    def _update(self, exp_id: str, **fields) -> None:
        with self.session_factory() as db:
            db.query(ExperimentRow).filter_by(id=exp_id).update(fields)
            db.commit()

    def _event(self, exp_id: str, event_type: str, detail: dict) -> None:
        with self.session_factory() as db:
            db.add(ExperimentEventRow(experiment_id=exp_id, timestamp=_now(),
                                      event_type=event_type, detail=detail))
            db.commit()

    def _finish(self, exp_id: str, status: str, error: str | None) -> None:
        # status change and its final event commit together: an observer that sees the final
        # status must also see the event that explains it
        with self.session_factory() as db:
            db.query(ExperimentRow).filter_by(id=exp_id).update(
                {"status": status, "phase": None, "active_slot": None, "finished_at": _now(), "error": error})
            db.add(ExperimentEventRow(
                experiment_id=exp_id, timestamp=_now(),
                event_type={"COMPLETED": "completed", "ABORTED": "aborted"}.get(status, "failed"),
                detail={"error": error} if error else {}))
            db.commit()
        self._schedule_analysis(exp_id)

    def _schedule_analysis(self, exp_id: str) -> None:
        if self.on_finished is None:
            return

        def job() -> None:
            time.sleep(self.settle_s * self.time_scale)
            try:
                self.on_finished(exp_id)
            except Exception as exc:
                logger.exception("analysis of %s failed", exp_id)
                try:
                    self._event(exp_id, "analysis_failed", {"error": f"{type(exc).__name__}: {exc}"})
                except Exception:
                    pass

        threading.Thread(target=job, name=f"analysis-{exp_id[:8]}", daemon=True).start()


_engine: ExperimentEngine | None = None


def get_engine() -> ExperimentEngine:
    global _engine
    if _engine is None:
        from app.analysis.service import default_hook  # late import: analysis depends on the models

        _engine = ExperimentEngine(on_finished=default_hook)
    return _engine
