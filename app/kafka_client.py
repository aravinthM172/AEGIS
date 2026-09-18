import json
import os
import socket
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from kafka import KafkaProducer
from kafka.admin import KafkaAdminClient


KAFKA_BOOTSTRAP_SERVERS = os.getenv(
    "KAFKA_BOOTSTRAP_SERVERS",
    "kafka:9092"
)

KAFKA_TOPIC = os.getenv(
    "KAFKA_TOPIC",
    "aegis-incidents"
)


_producer = None


def _get_producer():
    global _producer

    if _producer is None:
        _producer = KafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
            value_serializer=lambda value: json.dumps(value).encode("utf-8")
        )

    return _producer


def publish_incident(event: dict):

    try:
        producer = _get_producer()

        future = producer.send(
            KAFKA_TOPIC,
            value=event
        )

        metadata = future.get(timeout=10)

        producer.flush()

        return {
            "published": True,
            "topic": metadata.topic,
            "partition": metadata.partition,
            "offset": metadata.offset
        }
    except Exception as exc:
        return {
            "published": False,
            "error": str(exc)
        }


_health_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="kafka-health")


def _probe_kafka(timeout_ms: int) -> dict:
    # kafka-python retries a dead broker for ~10s; fail fast on an unreachable port first
    host, _, port = KAFKA_BOOTSTRAP_SERVERS.split(",")[0].rpartition(":")
    socket.create_connection((host, int(port)), timeout=timeout_ms / 1000).close()

    admin = KafkaAdminClient(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        request_timeout_ms=timeout_ms,
        api_version_auto_timeout_ms=timeout_ms,
    )

    try:
        topics = sorted(t for t in admin.list_topics() if not t.startswith("__"))
    finally:
        admin.close()

    return {
        "bootstrap_servers": KAFKA_BOOTSTRAP_SERVERS,
        "topic_count": len(topics),
        "topics": topics,
    }


def check_kafka(timeout_ms: int = 3000) -> dict:
    """Ask the broker for its topic list. Raises if it cannot be reached
    within timeout_ms (hard deadline, including DNS resolution)."""
    future = _health_pool.submit(_probe_kafka, timeout_ms)

    try:
        return future.result(timeout=timeout_ms / 1000)
    except FutureTimeout:
        raise TimeoutError(f"no response from {KAFKA_BOOTSTRAP_SERVERS} within {timeout_ms}ms")
