from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy.sql import func

from app.database import Base
from app.experiments.models import JSONType


class FailureSignatureRow(Base):
    """One measured signature per experiment (see app/analysis/signatures.py)."""
    __tablename__ = "failure_signatures"

    id = Column(String(36), primary_key=True)
    experiment_id = Column(String(36), ForeignKey("experiments.id"), nullable=False, unique=True)
    kind = Column(String(10), nullable=False, index=True)  # fault | control
    target = Column(String(100), nullable=False, index=True)
    fault_type = Column(String(50), nullable=False)
    fingerprint = Column(String(40), nullable=False, index=True)
    signature = Column(JSONType, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
