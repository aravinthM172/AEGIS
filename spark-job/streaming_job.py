import os

from pyspark.sql import SparkSession
from pyspark.sql.functions import col, current_date, from_json
from pyspark.sql.types import IntegerType, StringType, StructField, StructType

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "aegis-incidents")

S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://minio:9000")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "aegis")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "aegis12345")
S3_BUCKET = os.getenv("S3_BUCKET", "aegis-lake")

OUTPUT_PATH = f"s3a://{S3_BUCKET}/raw/incidents/"
CHECKPOINT_PATH = "/tmp/checkpoints/incidents"

INCIDENT_SCHEMA = StructType([
    StructField("id", IntegerType()),
    StructField("service", StringType()),
    StructField("severity", StringType()),
    StructField("message", StringType()),
    StructField("status", StringType()),
])


def build_spark():
    return (
        SparkSession.builder
        .appName("aegis-kafka-to-s3")
        .config("spark.hadoop.fs.s3a.endpoint", S3_ENDPOINT)
        .config("spark.hadoop.fs.s3a.access.key", S3_ACCESS_KEY)
        .config("spark.hadoop.fs.s3a.secret.key", S3_SECRET_KEY)
        .config("spark.hadoop.fs.s3a.path.style.access", "true")
        .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
        .config("spark.hadoop.fs.s3a.connection.ssl.enabled", "false")
        .getOrCreate()
    )


def main():
    spark = build_spark()
    spark.sparkContext.setLogLevel("WARN")

    raw = (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", KAFKA_BOOTSTRAP_SERVERS)
        .option("subscribe", KAFKA_TOPIC)
        .option("startingOffsets", "earliest")
        .load()
    )

    incidents = (
        raw.selectExpr("CAST(value AS STRING) AS json_str")
        .select(from_json(col("json_str"), INCIDENT_SCHEMA).alias("data"))
        .select("data.*")
        .withColumn("ingestion_date", current_date())
    )

    query = (
        incidents.writeStream
        .format("parquet")
        .option("path", OUTPUT_PATH)
        .option("checkpointLocation", CHECKPOINT_PATH)
        .partitionBy("ingestion_date")
        .outputMode("append")
        .start()
    )

    query.awaitTermination()


if __name__ == "__main__":
    main()
