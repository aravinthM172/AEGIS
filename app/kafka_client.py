import json
import os

from kafka import KafkaProducer


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