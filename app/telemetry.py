"""FaultScope telemetry: event schema + fire-and-forget Kafka emitter.

Every monitored service emits TelemetryEvent JSON to the `telemetry` topic.
The control plane (app/telemetry_consumer.py) ingests them.
"""
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from kafka import KafkaProducer
from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
TELEMETRY_TOPIC = os.getenv("TELEMETRY_TOPIC", "telemetry")
SERVICE_NAME = os.getenv("SERVICE_NAME", "control-plane")


class TelemetryEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    service: str
    event_type: str
    severity: str = "INFO"  # INFO | WARN | ERROR
    request_id: Optional[str] = None
    trace_id: Optional[str] = None
    latency_ms: Optional[float] = None
    status_code: Optional[int] = None
    error_type: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


_producer: Optional[KafkaProducer] = None


def _get_producer() -> KafkaProducer:
    global _producer

    if _producer is None:
        _producer = KafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            max_block_ms=2000,
            linger_ms=20,
        )

    return _producer


def emit(event: TelemetryEvent) -> None:
    """Send an event without ever raising into the caller's request path."""
    try:
        _get_producer().send(
            TELEMETRY_TOPIC,
            key=event.service.encode("utf-8"),
            value=event.model_dump(mode="json"),
        )
    except Exception:
        logger.warning("telemetry emit failed", exc_info=True)
