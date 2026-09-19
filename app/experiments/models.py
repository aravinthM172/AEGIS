from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.database import Base

# JSONB on Postgres, plain JSON elsewhere (unit tests run on SQLite)
JSONType = JSON().with_variant(JSONB(), "postgresql")

# Lifecycle. ROLLBACK_FAILED keeps the active slot occupied on purpose: the fault may
# still be applied, so no new experiment may start until it is resolved.
ACTIVE_STATUSES = ("PENDING", "RUNNING", "ROLLBACK_FAILED")
FINAL_STATUSES = ("COMPLETED", "FAILED", "ABORTED")


class ExperimentRow(Base):
    __tablename__ = "experiments"
    __table_args__ = (
        # active_slot is TRUE while an experiment is active and NULL otherwise; NULLs do
        # not collide, so this allows at most ONE active experiment (race-free).
        Index(
            "uq_one_active_experiment", "active_slot", unique=True,
            postgresql_where=text("active_slot"), sqlite_where=text("active_slot"),
        ),
    )

    id = Column(String(36), primary_key=True)
    name = Column(String(200))
    hypothesis = Column(Text)

    target = Column(String(100), ForeignKey("services.name"), nullable=False, index=True)
    fault_type = Column(String(50), nullable=False)
    parameters = Column(JSONType, nullable=False, default=dict)
    duration_s = Column(Integer, nullable=False)
    baseline_s = Column(Integer, nullable=False)
    recovery_s = Column(Integer, nullable=False)
    dry_run = Column(Boolean, nullable=False, default=True)
    workload_n = Column(Integer, default=100000)  # prime-count bound per request: scales CPU cost
    workload_rps = Column(Float, default=0.0)  # traffic generated through the gateway during the experiment
    injector = Column(String(50))

    status = Column(String(20), nullable=False, index=True)
    phase = Column(String(20))  # baseline | injecting | recovering (while RUNNING)
    abort_requested = Column(Boolean, nullable=False, default=False)
    active_slot = Column(Boolean)
    error = Column(Text)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    baseline_started_at = Column(DateTime(timezone=True))
    inject_started_at = Column(DateTime(timezone=True))   # injector.inject() called
    fault_applied_at = Column(DateTime(timezone=True))    # inject() returned: the fault is really in effect
    rollback_started_at = Column(DateTime(timezone=True)) # injector.rollback() called: fault window ends
    inject_ended_at = Column(DateTime(timezone=True))     # rollback() returned: target restored
    finished_at = Column(DateTime(timezone=True))

    result = Column(JSONType)  # filled by blast-radius analysis (Phase 6)


class ExperimentEventRow(Base):
    __tablename__ = "experiment_events"

    id = Column(Integer, primary_key=True)
    experiment_id = Column(String(36), ForeignKey("experiments.id"), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    event_type = Column(String(40), nullable=False)
    detail = Column(JSONType, nullable=False, default=dict)


# create_all() never alters existing tables; add columns introduced after first deploy.
_ADDED_COLUMNS = (
    ("experiments", "fault_applied_at", "TIMESTAMPTZ"),
    ("experiments", "rollback_started_at", "TIMESTAMPTZ"),
    ("experiments", "workload_rps", "DOUBLE PRECISION DEFAULT 0"),
    ("experiments", "workload_n", "INTEGER DEFAULT 100000"),
)


def ensure_columns(engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        for table, column, ddl in _ADDED_COLUMNS:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}"))
