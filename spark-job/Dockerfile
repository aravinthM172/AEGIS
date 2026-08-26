FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-21-jre-headless \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
ENV SPARK_PACKAGES=org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.1,org.apache.hadoop:hadoop-aws:3.3.4,com.amazonaws:aws-java-sdk-bundle:1.12.262

RUN pip install --no-cache-dir pyspark==3.5.1

WORKDIR /app
COPY warmup.py streaming_job.py ./

RUN spark-submit --packages "$SPARK_PACKAGES" warmup.py

CMD spark-submit --packages "$SPARK_PACKAGES" streaming_job.py
