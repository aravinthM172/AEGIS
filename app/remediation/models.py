from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.database import Base
from app.experiments.models import JSONType


class RemediationRow(Base):
    """One remediation action and everything decided and observed about it (spec entity: actions)."""
    __tablename__ = "remediation_actions"

    id = Column(String(36), primary_key=True)
    parent_id = Column(String(36), index=True)        # set when a follow-up strategy was tried after a failed one
    action = Column(String(40), nullable=False)
    target = Column(String(100), nullable=False, index=True)
    params = Column(JSONType, nullable=False, default=dict)
    mode = Column(String(10), nullable=False)          # dry_run | execute
    status = Column(String(24), nullable=False, index=True)
    requested_by = Column(String(100), nullable=False)
    approved_by = Column(String(100))
    source = Column(String(20), nullable=False)        # manual | analysis | prediction
    source_ref = Column(String(100))                   # analysis id / experiment id / warning key
    policy = Column(JSONType)                          # verdict, risk, reasons
    result = Column(JSONType)                          # what the executor reported
    verification = Column(JSONType)                    # what the recovery probes showed
    remaining_strategies = Column(JSONType, nullable=False, default=list)
    error = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at = Column(DateTime(timezone=True))


class AuditLogRow(Base):
    """Append-only record of every decision and execution."""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    ts = Column(DateTime(timezone=True), nullable=False)
    actor = Column(String(100), nullable=False)
    event = Column(String(40), nullable=False, index=True)
    ref_id = Column(String(36), index=True)
    detail = Column(JSONType, nullable=False, default=dict)
