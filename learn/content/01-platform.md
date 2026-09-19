# AREA platform | 1. Platform basics | How the control plane starts, stores data and is packaged
The **control plane** is the brain of FaultScope: one Python program (FastAPI) that exposes an HTTP API. Everything else in the project either feeds it data, obeys it, or displays what it knows.

These files are the foundation: the entry point (`main.py`), how it talks to the database and Redis, the tables it owns, and the Docker files that package and start everything.

> Beginner tip: a **FastAPI** app is a list of "routes" (URLs) each connected to a Python function. When your browser asks `GET /api/health`, FastAPI calls the matching function and sends back its return value as JSON.

## FILE main.py | The front door: builds the API, wires every feature in, and initialises the database in the background | code
### What it is
The file that Docker runs (`uvicorn main:app`). It creates the FastAPI application object called `app`, plugs in every feature's routes (experiments, analysis, AI, prediction, twin, remediation…), and makes sure the database is ready.

The title still says "Aegis" — that's the project's earlier name. FaultScope grew out of it.

### How it works
1. **Imports** every feature's router. Lines like `from app.ai import models as _ai_models` look unused but they matter: importing a models file *registers its database tables* so `create_all` knows about them.
2. `app.include_router(...)` adds each feature's URLs to the one API.
3. `install_telemetry(app)` makes the control plane report its own requests like any other service (see [[app/telemetry_middleware.py]]). Just before it, `install_read_auth(app)` adds the optional "reads need an API key" check from [[app/security.py]], and at startup `check_startup()` warns about development secrets.
4. **CORS** lets the dashboard (running on `localhost:3000`) call this API (`localhost:8000`). Without it the browser would block the calls. Only `GET`/`POST` and the `X-API-Key`/`Content-Type` headers are allowed.
5. On startup a **background thread** runs `_initialize_with_retry`, which creates tables, adds missing columns, loads the service list from `config/topology.json`, rolls back experiments left half-done by a crash, and re-indexes the AI knowledge base.

### Key parts
@snippet 76-88 | _initialize: everything that must happen before the system is useful
@snippet 91-105 | Retry loop: keep trying until the database is reachable

### Why the retry loop exists
Docker Compose can wait for Postgres to be "healthy" (`depends_on`). **Kubernetes cannot** — pods start in any order. The first Kubernetes test found the control plane started before Postgres, gave up once, and stayed with an empty service list forever. This loop (5 s apart, up to 90 attempts) fixed a real bug found by running on Kubernetes.

Because startup work happens on a thread, the API answers `/health` immediately instead of hanging while the database wakes up.

??Why is startup work done in a background thread instead of directly in `startup`? || So the API (and its health check) is available right away, and a slow or missing database doesn't stop the web server from starting.

### Connects to
- [[app/api.py]], [[app/experiments/routes.py]], [[app/analysis/routes.py]], [[app/ai/routes.py]], [[app/prediction/routes.py]], [[app/twin/routes.py]], [[app/remediation/routes.py]] — the routers it mounts
- [[app/topology_registry.py]] — `seed_registry` loads the declared services
- [[app/experiments/engine.py]] — `recover_orphans` cleans up after a crash

## FILE docker-compose.yml | The recipe that starts the whole system with one command | infra
### What it is
Docker Compose reads this file and starts every container, connects them on one private network (`aegis-network`), and tells each one its settings. `docker compose up -d --build` starts the entire platform.

### How it works
Each block under `services:` is one container:

| Container | What it is |
|---|---|
| `postgres`, `redis`, `kafka` | The three infrastructure services (storage, fast memory, message pipe) |
| `aegis` (container `aegis-api`) | The control plane, port 8000 |
| `telemetry-consumer` | Copies Kafka telemetry into Postgres and Redis |
| `gateway`, `java-service`, `cpp-service` | The demo system that gets broken on purpose |
| `fault-agent` | The only container with Docker access |
| `frontend` | The dashboard, port 3000 |
| `kafka-consumer` | A **leftover** from the earlier "Aegis" project that only prints incidents; not used by FaultScope. (The unused `minio` and `spark-job` services were removed.) |

### Key parts
@snippet 147-170 | gateway: a monitored service, with its labels and settings
@snippet 216-231 | fault-agent: the one container that mounts the Docker socket

### Things worth noticing
- `depends_on … condition: service_healthy` — start order: a service waits until Postgres/Kafka pass their health check.
- **Labels** `faultscope.injectable: "true"` mark the containers the fault-agent is *allowed* to break. A container without the label is off-limits. `faultscope.fault_port` says where a service's fault hook listens.
- `${FAULT_AGENT_TOKEN:-dev-token-change-me}` means "use the environment variable if set, otherwise this default". Those defaults are **development-only secrets**; change them before exposing anything.
- **Every published port is bound to `127.0.0.1`** (this PC only), so Postgres, Redis, Kafka and the services are not reachable from the network. The fault-agent also mounts `/var/run/docker.sock`. Docker socket access is basically root on the host, which is why this is isolated in one small program.
- Environment variables such as `DATABASE_URL` and `KAFKA_BOOTSTRAP_SERVERS` are how each program learns where its neighbours are — by container name (`postgres:5432`), resolved by Docker's DNS.

??Why is only the fault-agent given the Docker socket? || Because whoever holds it can control every container. Keeping it in one small, heavily-restricted program means the big control plane (and the AI) can never run Docker commands themselves.

### Connects to
- [[app/Dockerfile]] — how the Python services are built
- [[fault-agent/agent/core.py]] — what runs inside `fault-agent`
- [[k8s/workload.yaml]] — the Kubernetes version of the same idea

## FILE app/Dockerfile | Builds the Python image used by the control plane, gateway and consumers | infra
### What it is
A Dockerfile is a set of build steps that produces an *image* (a frozen, ready-to-run copy of a program and everything it needs).

### How it works
1. Start from `python:3.12-slim` (a small Python 3.12 system). The choice of 3.12 matters: your PC's local Python is newer (3.14) and handles some code differently; tests are run on 3.12 to match production.
2. Copy `requirements.txt` first and install it — Docker caches this layer, so rebuilding after a code-only change is fast.
3. Copy the rest of the project in and start `uvicorn main:app` on port 8000.

The same image is reused for several containers: docker-compose overrides the start `command` for the gateway (`uvicorn gateway.main:app …`), the telemetry consumer (`python -m app.telemetry_consumer`) and so on.

### Key parts
@snippet 1-13 | The whole file

## FILE requirements.txt | The list of Python libraries the control plane needs | config
### What it is
Seven libraries, each with a clear job.

### How it works
| Library | Used for |
|---|---|
| `fastapi` + `uvicorn` | The web API and the server that runs it |
| `sqlalchemy` + `psycopg` | Talking to Postgres from Python |
| `kafka-python-ng` | Sending/receiving Kafka messages |
| `redis` | Talking to Redis |
| `httpx` | Making HTTP calls (to the fault-agent, gateway probes, Ollama) |

### Key parts
@snippet 1-7 | The whole file

> The list is deliberately short: no numpy, no ML framework. All analysis is plain Python you can read.

## FILE .env.example | Template of settings for running without Docker | config
### What it is
A sample list of environment variables. Copy it to `.env` if you run the control plane directly on your PC instead of in Docker. With Docker Compose these values are set in `docker-compose.yml`.

### How it works
- `DATABASE_URL`, `REDIS_URL`, `KAFKA_BOOTSTRAP_SERVERS` — where the infrastructure lives (here `localhost` because you'd be outside Docker).
- `FAULT_AGENT_TOKEN` — shared password between the control plane and fault-agent.
- `OLLAMA_URL` / `OLLAMA_MODEL` / `OLLAMA_EMBED_MODEL` — the local AI. **No paid API key is needed**; if Ollama isn't reachable the AI page simply shows the evidence without an explanation.
- `FAULTSCOPE_API_KEYS` — `key:role` pairs. Roles rank `viewer < operator < admin`. These control who may start experiments or approve actions.

### Key parts
@snippet 12-21 | Security-related settings

> Careful: the values here are development defaults, and they are also in `docker-compose.yml`. Never reuse them anywhere reachable from the internet.

## FILE app/database.py | Opens the connection to Postgres | code
### What it is
Creates the SQLAlchemy **engine** (the connection pool to Postgres), a **session factory** (each session is one conversation with the database) and `Base`, the parent class all table definitions inherit from.

### How it works
- `DATABASE_URL` comes from the environment, with a default that works inside Docker.
- `pool_pre_ping=True` tests a connection before using it, so a restart of Postgres doesn't leave the app holding dead connections.
- `autocommit=False, autoflush=False` — nothing is written until the code explicitly calls `commit()`, which keeps changes atomic.
- `get_db()` is the FastAPI pattern "give this request a session, always close it afterwards".

### Key parts
@snippet 12-32 | Engine, session factory, Base and get_db

## FILE app/models.py | The core database tables | code
### What it is
Python classes that describe database tables. SQLAlchemy turns each class into a table (`create_all` in [[main.py]]).

### How it works
| Class → table | Holds |
|---|---|
| `Incident` → `incidents` | Alerts (id, service, severity, message, status) — the original Aegis feature |
| `TelemetryEventRow` → `telemetry_events` | One row per event emitted by any service: timing, status code, error type, plus free-form `metadata` |
| `ServiceRow` → `services` | The services FaultScope knows about, and the Docker container name each maps to |
| `ServiceDependencyRow` → `service_dependencies` | "A depends on B" edges, each with mandatory **evidence** |

Other features define their own tables next to their code (`experiments`, `failure_signatures`, `knowledge_chunks`, `remediation_actions`…).

### Key parts
@snippet 42-61 | The telemetry table: the raw material of every measurement
@snippet 75-86 | Dependency edges: `origin` is declared or observed, `evidence` is required

> The column is named `meta` in Python but `metadata` in the database, because `metadata` is a reserved word in SQLAlchemy. A small gotcha that broke things early on.

### Connects to
- [[app/telemetry_consumer.py]] — writes `telemetry_events`
- [[app/analysis/service.py]] — reads them back to measure blast radius

## FILE app/redis_client.py | One shared Redis connection | code
### What it is
Redis is a very fast in-memory store. This file creates a single client the first time it's needed and reuses it.

### How it works
- `get_redis()` builds the client lazily (on first call) from `REDIS_URL`; `decode_responses=True` gives normal text instead of bytes.
- `ping()` returns True/False so health checks never crash.

In FaultScope, Redis holds the **newest** state of each service (`service:health:<name>`) plus a few short caches. The permanent history lives in Postgres.

### Key parts
@snippet 10-26 | get_redis and ping

## FILE app/api.py | The original Aegis endpoints: health checks, incidents, live telemetry reads | code
### What it is
The first router of the project, mounted under `/api`. It is a mix of health probes and the incident features inherited from Aegis.

### How it works
- **Health**: `/api/health`, `/api/database`, `/api/kafka`, `/api/redis` each check one dependency; Kafka has a hard 3 s deadline so a dead broker can't hang the request.
- **Incidents**: `POST /api/incidents` stores an incident in Postgres, publishes it to the Kafka topic `aegis-incidents`, and updates Redis. `GET /api/incidents` lists them.
- **Rate limit**: max 10 incidents per service per 60 s, using a Redis counter with an expiry (`INCR` then `EXPIRE`). If Redis is down the limit is skipped — failing open on purpose.
- **Cache**: `/api/metrics` caches its answer in Redis for 10 s.
- **Telemetry reads**: `/api/telemetry/services` (live state from Redis) and `/api/telemetry/events` (recent rows from Postgres).

### Key parts
@snippet 82-104 | Rate limiting with a Redis counter
@snippet 203-243 | Cache-aside: try Redis, else compute and store for 10 seconds

??What does "fail open" mean in the rate limiter? || If Redis breaks, requests are allowed rather than blocked — availability over strictness. The code swallows the exception and carries on.

### Connects to
- [[app/kafka_client.py]] — publishing incidents
- [[app/telemetry_consumer.py]] — fills the Redis keys `telemetry/services` reads

## FILE app/overview.py | One call that gives the dashboard everything it needs | code
### What it is
The Control Center's home page must show services, the active experiment, recent activity and top risks. Instead of 8 separate requests, `GET /api/overview` builds it all in one response. `GET /api/events/recent` powers the System Logs page.

### How it works
1. Reads live per-service state from Redis (`_live_state`).
2. Asks the **prediction** module for each service's status (`normal`, `anomalous`, `insufficient_data`, `no_telemetry`).
3. Reads the active experiment, counts (experiments, signatures, knowledge chunks) and the latest actions from the database.
4. Adds the most **critical dependencies** and the **noise floor** from the resilience report.
5. `recent_events` merges two SQL queries — telemetry events and experiment lifecycle events — sorts them by time and returns the newest N.

It is strictly read-only: nothing here changes state.

### Key parts
@snippet 30-68 | The overview: combines six modules into one payload

### Connects to
- [[app/prediction/service.py]], [[app/analysis/service.py]], [[app/remediation/routes.py]] — the data it aggregates
- [[frontend/app/page.tsx]] — the dashboard that calls it

## FILE app/security.py | Optional deployment protections: login for reads, and a check for development secrets | code
### What it is
Two protections that are **off by default** (local development stays as before) and can be switched on with environment variables:

| Switch | Effect |
|---|---|
| `FAULTSCOPE_REQUIRE_AUTH_FOR_READS=1` | Every `GET` under `/api/` needs a valid `X-API-Key` (any role). Health probes stay open. |
| `FAULTSCOPE_STRICT_SECURITY=1` | The control plane **refuses to start** outside `FAULTSCOPE_ENV=local` while a development secret is in use. |

### How it works
- `insecure_defaults` lists development secrets currently in use (`dev-operator-key`, `dev-admin-key`, `dev-token-change-me`). It reports **which** dev key, never a custom one.
- `check_startup` is called when the app starts. In local mode it stays quiet; elsewhere it logs a warning for each problem, or raises in strict mode.
- `install_read_auth` adds a middleware. The flag is read on every request, so it can be tested without a rebuild. If reads are required but **no keys are configured**, it answers **503** (disabled) instead of leaving reads open — the same secure-by-default rule as [[app/remediation/auth.py]].
- It is registered *before* the telemetry middleware, so rejected requests still show up in the telemetry.
- Checked live on this stack: without a key `/api/overview` returns 401, with a key 200, and `/api/health` stays 200.

### Key parts
@snippet 25-34 | Detect development secrets
@snippet 37-48 | Warn, or refuse to start in strict mode
@snippet 51-66 | The read-auth middleware

??Why does read-auth answer 503, not 200, when no keys are configured? || Requiring authentication with no way to authenticate is a misconfiguration. Failing closed (503) is safer than silently serving data to everyone.

## FILE tests/test_security.py | Tests for the optional protections | test
### What it is
6 tests: reads are open by default; with the flag on they need a key while health and non-API paths stay open; a CORS preflight is not blocked; enabling it with no keys configured is disabled, not open; development secrets are detected without echoing custom keys; local mode is quiet and strict mode refuses elsewhere.

### Key parts
@snippet 37-43 | Reads need a key when enabled
@snippet 66-72 | Local quiet, strict refuses
