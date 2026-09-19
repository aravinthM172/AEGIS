"""Campaigns: run a plan of experiments sequentially, several times over.

Repetition is what turns single measurements into a resilience model with confidence.
Runs are interleaved (rep 1 of every scenario, then rep 2, ...) so slow drift affects all
scenarios equally, and a cooldown separates runs so one experiment's aftermath (DNS caches,
warm-up) does not leak into the next baseline.
"""
import logging
import threading
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.database import Base, SessionLocal
from app.experiments.engine import ActiveExperimentExists, ValidationFailed

logger = logging.getLogger("campaigns")

JSONType = JSON().with_variant(JSONB(), "postgresql")
FINAL = ("COMPLETED", "ABORTED", "FAILED", "ROLLBACK_FAILED")


class CampaignRow(Base):
    __tablename__ = "campaigns"

    id = Column(String(36), primary_key=True)
    name = Column(String(200))
    status = Column(String(20), nullable=False, index=True)  # RUNNING | COMPLETED | ABORTED | FAILED | INTERRUPTED
    plan = Column(JSONType, nullable=False)
    total = Column(Integer, nullable=False)
    completed = Column(Integer, nullable=False, default=0)
    experiment_ids = Column(JSONType, nullable=False, default=list)
    current_experiment_id = Column(String(36))
    abort_requested = Column(Boolean, nullable=False, default=False)
    cooldown_s = Column(Integer, nullable=False, default=20)
    error = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at = Column(DateTime(timezone=True))


def expand(plan: dict) -> list[dict]:
    """Interleaved run list: repetition-major, scenario-minor."""
    reps = int(plan.get("repetitions", 1))
    return [dict(item) for _ in range(reps) for item in plan["experiments"]]


def validate_plan(plan: dict) -> list[str]:
    errors = []
    items = plan.get("experiments")
    if not isinstance(items, list) or not items:
        errors.append("plan.experiments must be a non-empty list")
    reps = plan.get("repetitions", 1)
    if isinstance(reps, bool) or not isinstance(reps, int) or not 1 <= reps <= 10:
        errors.append("repetitions must be an integer between 1 and 10")
    for i, item in enumerate(items or []):
        if not isinstance(item, dict) or not {"target", "fault_type", "duration_s"} <= set(item):
            errors.append(f"experiments[{i}] needs target, fault_type and duration_s")
    if isinstance(items, list) and isinstance(reps, int) and len(items) * reps > 60:
        errors.append("a campaign is limited to 60 runs")
    return errors


def _now():
    return datetime.now(timezone.utc)


def to_dict(row: CampaignRow) -> dict:
    return {"id": row.id, "name": row.name, "status": row.status, "total": row.total, "completed": row.completed,
            "experiment_ids": row.experiment_ids, "current_experiment_id": row.current_experiment_id,
            "abort_requested": row.abort_requested, "cooldown_s": row.cooldown_s, "error": row.error,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None, "plan": row.plan}


class CampaignRunner:
    def __init__(self, engine, session_factory=SessionLocal, poll_s: float = 1.0, analysis_timeout_s: float = 60.0,
                 busy_retry_s: float = 5.0, busy_timeout_s: float = 300.0):
        self.engine = engine
        self.session_factory = session_factory
        self.poll_s = poll_s
        self.analysis_timeout_s = analysis_timeout_s
        self.busy_retry_s = busy_retry_s
        self.busy_timeout_s = busy_timeout_s
        self._abort = {}

    # ---- API ---------------------------------------------------------------------------

    def start(self, plan: dict, name: str | None = None, cooldown_s: int = 20) -> dict:
        errors = validate_plan(plan)
        if errors:
            raise ValidationFailed(errors)
        runs = expand(plan)
        with self.session_factory() as db:
            row = CampaignRow(id=str(uuid.uuid4()), name=name or plan.get("name") or "campaign", status="RUNNING",
                              plan=plan, total=len(runs), completed=0, experiment_ids=[], cooldown_s=cooldown_s,
                              abort_requested=False)
            db.add(row)
            db.commit()
            db.refresh(row)
            data = to_dict(row)
        self._abort[data["id"]] = threading.Event()
        threading.Thread(target=self._run, args=(data["id"], runs), name=f"campaign-{data['id'][:8]}",
                         daemon=True).start()
        return data

    def get(self, campaign_id: str) -> dict | None:
        with self.session_factory() as db:
            row = db.get(CampaignRow, campaign_id)
            return to_dict(row) if row else None

    def list_campaigns(self, limit: int = 20) -> list[dict]:
        with self.session_factory() as db:
            rows = db.query(CampaignRow).order_by(CampaignRow.created_at.desc()).limit(limit).all()
            return [to_dict(r) for r in rows]

    def abort(self, campaign_id: str) -> dict | None:
        with self.session_factory() as db:
            row = db.get(CampaignRow, campaign_id)
            if row is None:
                return None
            if row.status == "RUNNING":
                row.abort_requested = True
                db.commit()
                current = row.current_experiment_id
            else:
                current = None
        self._abort.setdefault(campaign_id, threading.Event()).set()
        if current:
            try:
                self.engine.abort(current)  # stop the experiment in flight too
            except Exception:
                logger.info("could not abort experiment %s (already finished?)", current)
        return self.get(campaign_id)

    def recover_interrupted(self) -> list[str]:
        """A control-plane restart kills the runner thread: mark such campaigns INTERRUPTED."""
        with self.session_factory() as db:
            rows = db.query(CampaignRow).filter(CampaignRow.status == "RUNNING").all()
            for row in rows:
                row.status, row.finished_at = "INTERRUPTED", _now()
                row.error = "control plane restarted while the campaign was running"
            db.commit()
            return [r.id for r in rows]

    # ---- runner ------------------------------------------------------------------------

    def _update(self, campaign_id: str, **fields) -> None:
        with self.session_factory() as db:
            db.query(CampaignRow).filter_by(id=campaign_id).update(fields)
            db.commit()

    def _aborted(self, campaign_id: str) -> bool:
        return self._abort.setdefault(campaign_id, threading.Event()).is_set()

    def _run(self, campaign_id: str, runs: list[dict]) -> None:
        try:
            ids: list[str] = []
            for i, body in enumerate(runs):
                if self._aborted(campaign_id):
                    return self._finish(campaign_id, "ABORTED", None)
                exp = self._start_experiment(campaign_id, body)
                ids.append(exp["id"])
                self._update(campaign_id, experiment_ids=list(ids), current_experiment_id=exp["id"])
                self._wait_finished_and_analysed(campaign_id, exp["id"])
                self._update(campaign_id, completed=i + 1, current_experiment_id=None)
                if i + 1 < len(runs) and self._pause(campaign_id):
                    return self._finish(campaign_id, "ABORTED", None)
            self._finish(campaign_id, "ABORTED" if self._aborted(campaign_id) else "COMPLETED", None)
        except Exception as exc:
            logger.exception("campaign %s failed", campaign_id)
            self._finish(campaign_id, "FAILED", f"{type(exc).__name__}: {exc}")

    def _start_experiment(self, campaign_id: str, body: dict) -> dict:
        deadline = time.monotonic() + self.busy_timeout_s
        while True:
            try:
                return self.engine.create(**body)
            except ActiveExperimentExists:  # something else is running: wait for the slot
                if time.monotonic() > deadline or self._aborted(campaign_id):
                    raise
                time.sleep(self.busy_retry_s)

    def _wait_finished_and_analysed(self, campaign_id: str, exp_id: str) -> None:
        while True:
            exp = self.engine.get(exp_id)
            if exp["status"] in FINAL:
                break
            time.sleep(self.poll_s)
        if exp["status"] == "ROLLBACK_FAILED":
            raise RuntimeError(f"experiment {exp_id} ended in ROLLBACK_FAILED; the campaign cannot continue safely")
        deadline = time.monotonic() + self.analysis_timeout_s
        while time.monotonic() < deadline:
            exp = self.engine.get(exp_id)
            done = (exp.get("result") or {}).get("status") is not None
            failed = any(e["event_type"] == "analysis_failed" for e in exp.get("events", []))
            if done or failed:
                return
            time.sleep(self.poll_s)
        logger.warning("analysis of %s did not finish within %ss", exp_id, self.analysis_timeout_s)

    def _pause(self, campaign_id: str) -> bool:
        """Cooldown between runs; True if aborted meanwhile."""
        with self.session_factory() as db:
            cooldown = db.query(CampaignRow.cooldown_s).filter_by(id=campaign_id).scalar() or 0
        return self._abort.setdefault(campaign_id, threading.Event()).wait(cooldown)

    def _finish(self, campaign_id: str, status: str, error: str | None) -> None:
        self._update(campaign_id, status=status, error=error, finished_at=_now(), current_experiment_id=None)


_runner: CampaignRunner | None = None


def get_campaign_runner() -> CampaignRunner:
    global _runner
    if _runner is None:
        from app.experiments.engine import get_engine

        _runner = CampaignRunner(get_engine())
    return _runner
