# AREA telemetry | 2. Telemetry pipeline | How every service reports what happened, and how that data is stored
**Telemetry** = small records a program writes about itself ("I handled `POST /api/jobs` in 42 ms and returned 200"). FaultScope can only measure damage because *every service emits one event per request*.

The path of one event: **service → Kafka topic `telemetry` → telemetry-consumer → Postgres (history) + Redis (latest state)**.

> Why Kafka in the middle? The service just drops the message and moves on, so reporting never slows real requests. If the database is briefly down, Kafka keeps the messages until the consumer catches up.

## FILE app/telemetry.py | The event format and the fire-and-forget sender | code
### What it is
Defines **what an event looks like** (`TelemetryEvent`) and **how to send it** to Kafka (`emit`).

### How it works
- `TelemetryEvent` has: `event_id` (unique), `timestamp`, `service`, `event_type` (`http_request` or `dependency_call`), `severity` (INFO/WARN/ERROR), `request_id`, `trace_id`, `latency_ms`, `status_code`, `error_type`, and a free-form `metadata` dictionary.
- `service` comes from the `SERVICE_NAME` environment variable — that's how one image can be several different services.
- `_get_producer()` creates the Kafka producer once. `max_block_ms=2000` means "don't wait more than 2 s for Kafka".
- `emit()` sends the event with the service name as the message key and **never raises**: if Kafka is broken it logs a warning and the user's request carries on. Losing a telemetry event is acceptable; failing a customer request because telemetry broke is not.

### Key parts
@snippet 24-35 | The event schema
@snippet 55-64 | emit: send, but never break the caller

> Honest limit: because a service reports about *itself*, a service that is completely down reports nothing. That's why experiments also use "synthetic users" that measure from outside.

### Connects to
- [[app/telemetry_middleware.py]] — creates events automatically for each HTTP request
- [[app/telemetry_consumer.py]] — the other end of the pipe
- [[java-service/src/main/java/com/aegis/service/telemetry/TelemetryPublisher.java]] and [[cpp-service/src/telemetry.hpp]] — the same format written in Java and C++

## FILE app/telemetry_middleware.py | Automatically records one event per HTTP request | code
### What it is
A **middleware** is code that wraps every request. This one measures how long the request took and what came back, then sends one telemetry event. Any FastAPI service gets full telemetry by calling `install(app)` — the control plane, the gateway and even your Job Tracker copy do exactly that.

### How it works
1. Read `X-Request-ID` from the incoming request or create a new UUID. `trace_id` defaults to the same value. **Trace propagation**: when a service calls the next one it forwards these IDs, so one user request can be followed across gateway → java → cpp.
2. Start a timer and call the real handler (`call_next`).
3. Assume status 500; overwrite it with the real status. If the handler crashes, remember the exception class name as `error_type`.
4. In `finally` (always runs) build the event: `ERROR` for 5xx, `WARN` for 4xx, else `INFO`; the route pattern (`/api/jobs`, not the raw URL) goes in `metadata.path`.
5. `emit_async` sends it on a background thread so the first (possibly slow) Kafka connection never blocks the request.

Handlers can enrich the event via `request.state` (for example `error_type = "dependency_timeout"`, `dependency = "cpp-service"`).

### Key parts
@snippet 17-19 | Keep Kafka off the event loop
@snippet 22-35 | Ids, timer, default status
@snippet 44-65 | finally: always emit exactly one event

??Why is the event built in a `finally` block? || So an event is recorded no matter what: normal responses, error responses and crashes all produce exactly one event.

### Connects to
- [[main.py]] and [[gateway/main.py]] — both call `install`
- [[app/fault_hook.py]] — must be installed *before* this so injected errors are still recorded

## FILE app/telemetry_consumer.py | Copies Kafka events into Postgres and Redis, safely | code
### What it is
A small standalone program (`python -m app.telemetry_consumer`) that runs forever, reading the `telemetry` topic. It is the bridge between the message pipe and permanent storage.

### How it works
1. Reads up to 100 messages at a time (`poll`).
2. **Validates** each with the `TelemetryEvent` model. A malformed one is skipped (it can never succeed, so retrying would block everything behind it).
3. `store_event` inserts into Postgres using `ON CONFLICT DO NOTHING` on `event_id` — if Kafka redelivers a message the row isn't duplicated. `RETURNING` tells us whether the row was really new.
4. `update_live_state` writes to Redis (`service:health:<service>`: last seen, last status, last latency) and only **increments counters when the event was new**, so redelivery never double-counts.
5. If Postgres or Redis is down, `ingest` retries with growing waits (0.5 s up to 10 s).
6. Only after the whole batch is stored does it `commit()` the Kafka offset. This is **at-least-once delivery**: a crash may repeat work but never loses events; deduplication makes repeats harmless.

### Key parts
@snippet 29-48 | store_event: insert once, tell us if it was new
@snippet 51-67 | update_live_state: counters only for new events
@snippet 70-87 | ingest: retry with backoff, remember what already succeeded
@snippet 97-105 | Manual offset commits (auto-commit is off)

??Why commit Kafka offsets *after* storing instead of before? || If the consumer crashes between reading and storing, an early commit would lose the event forever. Committing after means the worst case is processing an event twice, which the duplicate check handles.

### Connects to
- [[app/models.py]] — `TelemetryEventRow`
- [[app/redis_client.py]] — live state
- [[app/prediction/service.py]] — reads the resulting data to detect trouble

## FILE app/kafka_client.py | Publishing incidents to Kafka, and a Kafka health probe | code
### What it is
Helper code from the original Aegis project: a lazily-created Kafka producer for the `aegis-incidents` topic and a health check.

### How it works
- `publish_incident` sends an event and **waits up to 10 s for confirmation** (`future.get`), then returns where it was stored (topic, partition, offset), or `published: False` with the error — it never throws.
- `check_kafka` first tries a raw TCP connection (fast failure if the port is closed) and then asks the broker for its topic list. It runs in a thread pool so the caller gets a hard deadline even if DNS hangs.

### Key parts
@snippet 36-60 | publish_incident: send, confirm, report
@snippet 89-97 | check_kafka: a hard 3-second deadline

## FILE app/kafka_consumer.py | Legacy consumer that prints incidents | code
### What it is
A tiny script, run as the `kafka-consumer` container, that listens to `aegis-incidents` and prints each message. It's a demo of consuming Kafka from Python and is **not part of the FaultScope measurement path** (that's [[app/telemetry_consumer.py]]).

### How it works
Creates a `KafkaConsumer` on import and loops printing partition, offset and data. It uses auto-commit (simple, but less safe than the manual commits used by the telemetry consumer).

### Key parts
@snippet 23-32 | The consumer setup
@snippet 42-50 | The print loop
