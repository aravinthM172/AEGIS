"""Control-plane telemetry ingest.

Kafka `telemetry` -> validate -> Postgres (history) + Redis (live state).
Run with: python -m app.telemetry_consumer
"""
import json
import logging
import os

from kafka import KafkaConsumer
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert

from app.database import Base, SessionLocal, engine
from app.models import TelemetryEventRow
from app.redis_client import get_redis
from app.telemetry import TELEMETRY_TOPIC, KAFKA_BOOTSTRAP_SERVERS, TelemetryEvent


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("telemetry-consumer")

GROUP_ID = os.getenv("KAFKA_GROUP_ID", "faultscope-telemetry-ingest")


def store_event(event: TelemetryEvent) -> None:
    with SessionLocal() as db:
        stmt = insert(TelemetryEventRow).values(
            event_id=event.event_id,
            timestamp=event.timestamp,
            service=event.service,
            event_type=event.event_type,
            severity=event.severity,
            request_id=event.request_id,
            trace_id=event.trace_id,
            latency_ms=event.latency_ms,
            status_code=event.status_code,
            error_type=event.error_type,
            meta=event.metadata,
        ).on_conflict_do_nothing(index_elements=["event_id"])
        db.execute(stmt)
        db.commit()


def update_live_state(event: TelemetryEvent) -> None:
    key = f"service:health:{event.service}"
    r = get_redis()

    pipe = r.pipeline()
    pipe.hset(key, mapping={
        "last_seen": event.timestamp.isoformat(),
        "last_status_code": event.status_code if event.status_code is not None else "",
        "last_latency_ms": event.latency_ms if event.latency_ms is not None else "",
    })
    pipe.hincrby(key, "total_events", 1)
    if event.severity == "ERROR":
        pipe.hincrby(key, "error_events", 1)
    pipe.execute()


def main() -> None:
    Base.metadata.create_all(bind=engine)

    consumer = KafkaConsumer(
        TELEMETRY_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=GROUP_ID,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
    )
    logger.info("telemetry ingest started: topic=%s group=%s", TELEMETRY_TOPIC, GROUP_ID)

    for message in consumer:
        try:
            event = TelemetryEvent.model_validate(message.value)
        except ValidationError as exc:
            logger.warning("dropping invalid event at offset %s: %s", message.offset, exc)
            continue

        try:
            store_event(event)
            update_live_state(event)
        except Exception:
            logger.exception("failed to ingest event %s", event.event_id)


if __name__ == "__main__":
    main()
