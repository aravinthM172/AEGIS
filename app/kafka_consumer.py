import json
import os

from kafka import KafkaConsumer


KAFKA_BOOTSTRAP_SERVERS = os.getenv(
    "KAFKA_BOOTSTRAP_SERVERS",
    "kafka:9092"
)

KAFKA_TOPIC = os.getenv(
    "KAFKA_TOPIC",
    "aegis-incidents"
)

KAFKA_GROUP_ID = os.getenv(
    "KAFKA_GROUP_ID",
    "aegis-python-consumer"
)


consumer = KafkaConsumer(
    KAFKA_TOPIC,
    bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
    group_id=KAFKA_GROUP_ID,
    auto_offset_reset="earliest",
    enable_auto_commit=True,
    value_deserializer=lambda value: json.loads(
        value.decode("utf-8")
    )
)


print(
    f"Kafka consumer started. "
    f"Listening to topic: {KAFKA_TOPIC}",
    flush=True
)


for message in consumer:

    print(
        f"Received incident: "
        f"partition={message.partition}, "
        f"offset={message.offset}, "
        f"data={message.value}",
        flush=True
    )