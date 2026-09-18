from sqlalchemy import Column, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.database import Base


class Incident(Base):
    __tablename__ = "incidents"

    id = Column(Integer, primary_key=True, index=True)

    service = Column(
        String(100),
        nullable=False,
        index=True,
    )

    severity = Column(
        String(20),
        nullable=False,
    )

    message = Column(
        Text,
        nullable=False,
    )

    status = Column(
        String(30),
        default="open",
        nullable=False,
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class TelemetryEventRow(Base):
    __tablename__ = "telemetry_events"

    event_id = Column(String(64), primary_key=True)

    timestamp = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    service = Column(String(100), nullable=False, index=True)
    event_type = Column(String(50), nullable=False)
    severity = Column(String(10), nullable=False)
    request_id = Column(String(64))
    trace_id = Column(String(64))
    latency_ms = Column(Float)
    status_code = Column(Integer)
    error_type = Column(String(100))
    meta = Column("metadata", JSONB, nullable=False, default=dict)
