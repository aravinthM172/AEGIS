# FaultScope demo workload: architecture

This document describes the system that FaultScope experiments run against. Everything here is taken from the code and configuration.

## Request path

A client calls the gateway: `POST /api/jobs?n=<bound>`. The chain is:

gateway -> java-service -> cpp-service, and java-service -> postgres.

- **gateway** (Python/FastAPI, port 8090) forwards the request to java-service `POST /jobs`. Its HTTP client uses a 1 s connect timeout and a 5 s read timeout. A downstream failure is returned as 503 (connection error), 504 (timeout) or 502 (upstream 5xx).
- **java-service** (Spring Boot, port 8081) handles `POST /jobs`: it calls cpp-service `GET /compute?n=...` with a 1 s connect timeout and a 2 s read timeout, then saves the result as a row in the Postgres table `jobs`, and returns it. Each request therefore makes one HTTP call and several database round trips. If cpp-service fails, java-service answers 503, 504 or 502.
- **cpp-service** (C++, port 8082) is a single-threaded HTTP server. `/compute` counts primes up to n (CPU-bound; cost grows with n; n is limited to 5,000,000). One slow request delays the next ones because requests are handled one at a time.
- **postgres** stores jobs, incidents, telemetry history and experiment data.

## Other components

- **kafka** carries telemetry events (topic `telemetry`) and incident events (topic `aegis-incidents`). java-service also consumes `aegis-incidents`.
- **redis** holds live per-service state and rate-limit counters used by the control plane.
- **telemetry-consumer** reads the `telemetry` topic and writes events to Postgres and live state to Redis.
- **control-plane** (FastAPI) exposes the topology, telemetry, experiment, analysis and resilience APIs.
- **fault-agent** is the only component with Docker access. It applies faults to allowlisted containers and reverts them after a TTL.

## Telemetry

Every service emits one `http_request` event per request and, for outgoing calls, one `dependency_call` event, to Kafka. Events carry a request id and a trace id that are propagated across services. Services emit events asynchronously: when Kafka is unavailable, cpp-service and the gateway drop events, while java-service buffers and retries.

## What is not on the request path

Kafka and Redis are not needed to serve `POST /api/jobs`. They are needed for telemetry, live state and incident events. Stopping Kafka does not fail user requests, but blinds parts of the telemetry.
