"""Control-plane telemetry ingest.

Kafka `telemetry` -> validate -> Postgres (history) + Redis (live state).
Run with: python -m app.telemetry_consumer
"""
import json
import logging
import os
import signal
import threading
import time

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


def store_event(event: TelemetryEvent) -> bool:
    """Insert the event. Returns True if it was new, False if already stored."""
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
        ).on_conflict_do_nothing(index_elements=["event_id"]).returning(TelemetryEventRow.event_id)
        # RETURNING yields a row only if inserted (ORM insert reports rowcount=-1)
        inserted = db.execute(stmt).scalar_one_or_none() is not None
        db.commit()
        return inserted


def update_live_state(event: TelemetryEvent, count: bool) -> None:
    """Refresh live state in Redis. Counters only move for newly stored events,
    so an event redelivered by Kafka is never counted twice."""
    key = f"service:health:{event.service}"
    r = get_redis()

    pipe = r.pipeline()
    pipe.hset(key, mapping={
        "last_seen": event.timestamp.isoformat(),
        "last_status_code": event.status_code if event.status_code is not None else "",
        "last_latency_ms": event.latency_ms if event.latency_ms is not None else "",
    })
    if count:
        pipe.hincrby(key, "total_events", 1)
        if event.severity == "ERROR":
            pipe.hincrby(key, "error_events", 1)
    pipe.execute()


def ingest(event: TelemetryEvent, stop: threading.Event) -> bool:
    """Store one event, retrying transient failures (Postgres/Redis down) with
    backoff. Returns False only if asked to stop before it succeeded."""
    inserted = None  # remembered across retries so Redis is not double-counted
    delay = 0.5

    while not stop.is_set():
        try:
            if inserted is None:
                inserted = store_event(event)
            update_live_state(event, count=inserted)
            return True
        except Exception:
            logger.exception("ingest of %s failed; retrying in %.1fs", event.event_id, delay)
            stop.wait(delay)
            delay = min(delay * 2, 10.0)

    return False


def main() -> None:
    Base.metadata.create_all(bind=engine)

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    # At-least-once: offsets are committed only after a batch is fully stored.
    consumer = KafkaConsumer(
        TELEMETRY_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=GROUP_ID,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        value_deserializer=lambda v: v,  # validate ourselves so bad payloads don't kill the loop
    )
    logger.info("telemetry ingest started: topic=%s group=%s", TELEMETRY_TOPIC, GROUP_ID)

    while not stop.is_set():
        batches = consumer.poll(timeout_ms=1000, max_records=100)
        if not batches:
            continue

        completed = True
        for records in batches.values():
            for message in records:
                try:
                    event = TelemetryEvent.model_validate(json.loads(message.value))
                except (ValueError, ValidationError) as exc:
                    # Malformed events can never succeed; skip them (they get committed past).
                    logger.warning("skipping malformed event at offset %s: %s", message.offset, exc)
                    continue

                if not ingest(event, stop):
                    completed = False
                    break
            if not completed:
                break

        if completed:
            consumer.commit()
        # else: shutting down mid-batch; uncommitted events are redelivered and de-duplicated

    consumer.close()
    logger.info("telemetry ingest stopped")


if __name__ == "__main__":
    main()
