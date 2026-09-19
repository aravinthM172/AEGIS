# AREA gateway | 3. The demo system: gateway + fault hook | The front door of the system that gets broken on purpose
FaultScope needs something to break. The **demo workload** is a deliberately small system with a real chain of dependencies:

`user → gateway (Python) → java-service (Spring Boot) → cpp-service (C++)` and `java-service → postgres`

Why three languages? To prove the platform can observe *any* service, as long as it emits the same telemetry JSON. The chain gives failures somewhere to **propagate**: if `cpp-service` dies, `java-service` fails and so does the `gateway`.

Each service also has a **fault hook**: a token-protected switch that makes a chosen fraction of requests fail on purpose (used by the `http_error` fault). The hook is self-expiring, so a crash in the controller can never leave a service failing forever.

## FILE gateway/main.py | The public entry point: forwards POST /api/jobs to the Java service | code
### What it is
A small FastAPI app (port 8090). Users call `POST /api/jobs?n=100000` here; it forwards to `java-service` and returns the answer. It's kept **separate from the control plane** on purpose: breaking the workload must never take down the thing that measures it.

### How it works
1. `lifespan` creates one shared async HTTP client with a 1 s connect timeout and a 5 s read timeout. The read timeout is deliberately longer than the Java service's own downstream timeouts (2 s for C++ + database), so the gateway doesn't give up before Java does.
2. The fault hook is installed first and the telemetry middleware second. Order matters: the later one is *outermost*, so injected errors are still recorded.
3. `create_job` forwards the request, **propagating `X-Request-ID` and `X-Trace-Id`** so the same trace follows the request down the chain.
4. Whatever happens, a `finally` block emits a `dependency_call` event describing the call to `java-service` (its latency, outcome and error type).
5. Result mapping:
   - success → return Java's JSON
   - 4xx from Java (e.g. a bad `n`) → pass through: the caller's mistake, not a dependency failure
   - timeout → **504**, connection refused → **503**, anything else → **502**, with a body naming the failed dependency. The middleware also gets `error_type = dependency_timeout` etc.

### Key parts
@snippet 39-41 | App creation and the important middleware order
@snippet 57-84 | Forward the call; always record a dependency_call event
@snippet 93-104 | Turn a downstream failure into a clear 5xx

??Why does the gateway emit its own `dependency_call` event as well as the middleware's `http_request` event? || The `http_request` event says how the gateway served its caller. The `dependency_call` event is the gateway's view of the *edge* to java-service. Comparing both views is how the analysis can tell "the gateway is slow because java-service is slow".

### Connects to
- [[app/telemetry_middleware.py]], [[app/fault_hook.py]]
- [[java-service/src/main/java/com/aegis/service/jobs/JobController.java]] — the next hop
- [[app/experiments/workload.py]] — the synthetic users that call this endpoint

## FILE app/fault_hook.py | A token-protected, self-expiring "fail some requests" switch for Python services | code
### What it is
Lets the fault-agent make a service return HTTP 5xx for a chosen fraction of requests — without touching Docker. It's how the **`http_error` fault** works for Python services (the Java and C++ services have the same feature written in their own languages).

### How it works
- `HttpErrorHook` stores `rate` (0–1), `status` (500–599) and an expiry time, guarded by a lock. `should_fail()` rolls the dice per request.
- `install(app)` adds three routes under `/_faults/http-error`: `POST` (turn on), `DELETE` (turn off), `GET` (see state). Each needs the `X-Fault-Token` header to equal `FAULT_HOOK_TOKEN`. If that variable isn't set, the hook answers 503 "disabled" — **secure by default**.
- Input is validated: `rate` in (0, 1], `status` 500–599, `ttl_s` 1–900 seconds.
- A middleware fails matching requests with `{"error": "injected_fault"}` and marks `error_type = "injected_fault"` so analysis can tell fake failures from real ones. `/health`, `/_faults`, `/docs` are exempt.
- **The dead-man's switch**: `expires_at` — after `ttl_s` the hook silently stops, even if nobody ever calls DELETE.

### Key parts
@snippet 15-52 | The hook state: rate, status, expiry, should_fail
@snippet 58-71 | Token check and validated activation
@snippet 85-92 | The middleware that injects the failures

??Why must `ttl_s` be capped at 900 seconds? || A safety limit: even a mistaken or malicious request can only cause 15 minutes of injected failures, and the hook expires by itself.

### Connects to
- [[fault-agent/agent/core.py]] — the caller of these routes
- [[java-service/src/main/java/com/aegis/service/faults/HttpErrorFault.java]] and [[cpp-service/src/main.cpp]] — the same hook in Java and C++

# AREA java | 4. The Java service (Spring Boot) | The middle of the chain: calls C++, saves to Postgres
`java-service` is a **Spring Boot 3.5 / Java 21** application (port 8081). Spring is a Java framework: you write small classes annotated with things like `@RestController` and Spring wires them together.

Its business job: `POST /jobs?n=…` asks the C++ service to count prime numbers up to `n`, saves the result in Postgres and returns it. Everything else in the folder is either telemetry, the fault hook, or leftovers from the earlier Aegis project (incidents, the `faultscope` client).

Not explained file by file: `mvnw`, `mvnw.cmd` and `.mvn/` are Maven's standard "wrapper" (lets anyone build without installing Maven), unchanged from the Spring template.

## FILE java-service/src/main/java/com/aegis/service/AegisJavaServiceApplication.java | The program's start button | code
### What it is
Eleven lines. `@SpringBootApplication` tells Spring "scan this package for components (controllers, filters, repositories) and start a web server". `main` just launches it.

### Key parts
@snippet 6-11 | The whole class

## FILE java-service/src/main/java/com/aegis/service/jobs/JobController.java | POST /jobs: compute via C++, persist, respond | code
### What it is
The one endpoint that matters for the experiments.

### How it works
1. Validates `n` is between 1 and 5,000,000 (otherwise **400** — a client error, not a failure).
2. Reads the request/trace IDs that [[java-service/src/main/java/com/aegis/service/telemetry/TelemetryFilter.java]] stored on the request.
3. Calls `cpp.compute(n, …)` — an HTTP call to the C++ service.
4. Saves a `Job` row (`n`, `primes`, `compute_ms`, `trace_id`) via the repository — **a real database write per request**, so a database fault has visible effects.
5. Returns `id`, `n`, `primes`, `compute_ms`, `trace_id`.
6. If the C++ call fails, `@ExceptionHandler(DependencyException)` turns it into an HTTP status: timeout → 504, connection error → 503, otherwise 502. It also tags the request (`error_type = dependency_…`, `dependency = cpp-service`) so telemetry records *which* dependency broke.

### Key parts
@snippet 34-57 | create: validate, compute, save, respond
@snippet 59-77 | Translate a dependency failure into the right status

??Which two things does `POST /jobs` depend on? || `cpp-service` (over HTTP) and `postgres` (via JPA). Those are exactly the two dependency edges declared for java-service in the topology.

## FILE java-service/src/main/java/com/aegis/service/jobs/CppClient.java | The HTTP client for cpp-service, with strict timeouts and telemetry | code
### What it is
The Java side of the edge `java-service → cpp-service`.

### How it works
- Builds a `RestClient` with a **1 s connect timeout** and **2 s read timeout** (configurable). Without timeouts a stuck C++ service would hang Java threads forever — a classic cause of cascading failure.
- `compute` calls `GET /compute?n=…`, forwarding `X-Request-ID` and `X-Trace-Id`.
- Failure classification: HTTP error status → `http_4xx`/`http_5xx`; read timeout → `timeout`; anything else → `connection_error`. Each throws a `DependencyException`.
- `finally` publishes one `dependency_call` telemetry event with target, latency, status and error type — whether the call succeeded or not.

### Key parts
@snippet 27-37 | Client with connect/read timeouts
@snippet 39-74 | compute: call, classify failures, always emit telemetry

??Why is a read timeout important here? || If cpp-service freezes (a paused container, for example), without a timeout Java waits forever. With 2 s, Java gives up, returns 504 and keeps serving others. The experiments measure exactly how this behaves.

## FILE java-service/src/main/java/com/aegis/service/jobs/Job.java | The `jobs` database table as a Java class | code
### What it is
A JPA **entity**: annotations map this class to a Postgres table `jobs` with columns `id`, `n`, `primes`, `compute_ms`, `trace_id`, `created_at`. `spring.jpa.hibernate.ddl-auto=update` lets Hibernate create the table automatically.

### Key parts
@snippet 13-31 | Fields and column mapping
@snippet 43-46 | created_at is filled just before the first insert

## FILE java-service/src/main/java/com/aegis/service/jobs/JobRepository.java | Free database access code | code
### What it is
An empty interface that `extends JpaRepository<Job, Long>`. Spring generates the implementation (`save`, `findAll`, `findById`…) at runtime — no SQL written.

### Key parts
@snippet 5-5 | The entire interface

## FILE java-service/src/main/java/com/aegis/service/jobs/ComputeResult.java | The shape of the C++ service's answer | code
### What it is
A Java `record` (an immutable data holder) with `n`, `primes`, `compute_ms`. `@JsonIgnoreProperties(ignoreUnknown = true)` means extra fields such as `"service"` are ignored instead of causing errors — a small robustness choice.

### Key parts
@snippet 6-10 | The record

## FILE java-service/src/main/java/com/aegis/service/jobs/DependencyException.java | The error type meaning "a dependency call failed" | code
### What it is
A custom exception carrying *which* dependency failed and *how* (`timeout`, `connection_error`, `http_4xx`, `http_5xx`). [[java-service/src/main/java/com/aegis/service/jobs/CppClient.java]] throws it; [[java-service/src/main/java/com/aegis/service/jobs/JobController.java]] catches it and converts it to a response and telemetry tags.

### Key parts
@snippet 3-13 | Class and constructor

## FILE java-service/src/main/java/com/aegis/service/telemetry/TelemetryFilter.java | Java's version of the telemetry middleware | code
### What it is
A servlet **filter** (Java's equivalent of middleware) that records one `http_request` event per request. Same behaviour as [[app/telemetry_middleware.py]], in Java.

### How it works
1. Reads or generates `X-Request-ID`; trace ID defaults to the request ID; stores both as request attributes for other classes.
2. Times the request around `chain.doFilter`.
3. Assumes status 500, replaces it with the real one; an exception records its class name.
4. In `finally`, lets handlers override the error type (via the `ATTR_ERROR_TYPE` attribute) and publishes the event with the matched route pattern as `path`.
5. `@Order(10)` puts it **outside** the fault filter (order 20), so injected failures are still measured.

### Key parts
@snippet 44-51 | Correlation ids
@snippet 57-85 | Time it, classify it, always publish

## FILE java-service/src/main/java/com/aegis/service/telemetry/TelemetryPublisher.java | Builds the event JSON and sends it to Kafka | code
### What it is
Java's version of `emit()` from [[app/telemetry.py]]. It creates the same event fields (`event_id`, `timestamp`, `service`, …) and sends the JSON to the `telemetry` topic.

### How it works
- Service name and topic come from `application.properties` (`faultscope.service-name`, `faultscope.telemetry.topic`).
- `severityFor(status)`: 5xx → ERROR, 4xx → WARN, else INFO; a missing status counts as ERROR.
- The send is asynchronous; a failure is only logged. A `try/catch` guarantees telemetry can **never break the request path**.

### Key parts
@snippet 40-45 | Severity rule
@snippet 47-74 | Build the event and send without ever throwing

## FILE java-service/src/main/java/com/aegis/service/faults/HttpErrorFault.java | Java's in-memory HTTP-error fault state | code
### What it is
Same idea as the Python hook: a `rate`, a `status` and an expiry time. Fields are `volatile` because many request threads read them while the fault-agent's thread writes them.

### How it works
`set(rate, status, ttl)` turns the fault on with an expiry; `clear()` turns it off; `shouldFail()` returns the status to inject or `-1` to let the request through; `state()` reports whether it's active and how many seconds remain.

### Key parts
@snippet 20-31 | set and clear
@snippet 44-50 | The per-request decision

## FILE java-service/src/main/java/com/aegis/service/faults/HttpErrorFaultController.java | The token-protected control surface for the fault | code
### What it is
Exposes `POST/DELETE/GET /_faults/http-error` for the fault-agent. Every method checks the `X-Fault-Token` header first.

### How it works
- No token configured → **503** (disabled). Wrong token → **401**.
- Input validation identical to Python: rate in (0,1], status 500–599, ttl 1–900 s → otherwise **422**.

### Key parts
@snippet 29-38 | denied(): the two refusal cases
@snippet 40-56 | activate: validate then switch on

## FILE java-service/src/main/java/com/aegis/service/faults/HttpErrorFaultFilter.java | Actually fails the requests while the fault is on | code
### What it is
A filter that, while the fault is active, answers a fraction of requests with the chosen 5xx status instead of running the real code.

### How it works
- Skips `/health`, `/actuator` and `/_faults` so health checks and the control surface keep working.
- Otherwise asks `fault.shouldFail()`. If it says fail: sets `error_type = injected_fault` (so the failure is identifiable as an experiment), writes a small JSON body and **doesn't call the rest of the chain**.
- `@Order(20)` runs *inside* the telemetry filter (order 10), so the failure gets recorded.

### Key parts
@snippet 29-50 | shouldNotFilter and doFilterInternal

## FILE java-service/src/main/java/com/aegis/service/HealthController.java | GET /health | code
### What it is
Returns `{"service": "Aegis Java Service", "status": "running"}`. Health checks (and the gateway probes used to verify recovery) use it.

### Key parts
@snippet 8-18 | The class

## FILE java-service/src/main/java/com/aegis/service/Incident.java | Legacy: the incidents table | code
### What it is
Leftover from the original Aegis project: an entity holding `service`, `severity`, `message`, `status`. Not used by the FaultScope experiments.

### Key parts
@snippet 8-27 | Entity and constructors

## FILE java-service/src/main/java/com/aegis/service/IncidentRepository.java | Legacy: database access for incidents | code
### What it is
Empty interface; Spring generates `save`/`findAll` (like [[java-service/src/main/java/com/aegis/service/jobs/JobRepository.java]]).

### Key parts
@snippet 5-6 | The interface

## FILE java-service/src/main/java/com/aegis/service/IncidentController.java | Legacy: POST/GET /incidents | code
### What it is
An API to receive and list incidents. It prints each received incident to the console and saves it.

### Key parts
@snippet 17-30 | receiveIncident
@snippet 32-35 | getIncidents

## FILE java-service/src/main/java/com/aegis/service/IncidentConsumer.java | Legacy: a Kafka listener that stores incidents | code
### What it is
`@KafkaListener` makes Spring call `consume` for every message on `aegis-incidents`. It parses the JSON and saves an `Incident`. This is the Java half of the original Aegis pipeline (Python publishes, Java consumes).

### Key parts
@snippet 18-38 | The listener

## FILE java-service/src/main/java/com/aegis/service/faultscope/FaultScopeClient.java | Legacy: calls an old AI service that no longer exists | code
### What it is
Calls `http://127.0.0.1:8003/api/v1/analyze` — the earlier `llm-service`, which isn't part of FaultScope any more. Calling it would fail; nothing in the experiments uses it.

> Honest note: this and the two files below are dead code kept from the earlier project. FaultScope's AI lives in the control plane ([[app/ai/service.py]]).

### Key parts
@snippet 11-28 | The client

## FILE java-service/src/main/java/com/aegis/service/faultscope/FaultScopeController.java | Legacy: exposes /api/v1/faultscope/analyze | code
### What it is
Passes the request body to [[java-service/src/main/java/com/aegis/service/faultscope/FaultScopeClient.java]] and returns its answer.

### Key parts
@snippet 15-20 | The endpoint

## FILE java-service/src/main/java/com/aegis/service/faultscope/FaultScopeRequest.java | Legacy: request body for the old AI call | code
### What it is
A plain class with one field, `incident` (text).

### Key parts
@snippet 3-12 | Fields and constructors

## FILE java-service/src/main/java/com/aegis/service/faultscope/FaultScopeResponse.java | Legacy: response shape of the old AI call | code
### What it is
A data class (`incident`, and an `Analysis` object with `summary`, `root_cause`, `evidence`…) matching what the earlier AI service returned.

### Key parts
@snippet 5-30 | Fields

## FILE java-service/src/main/resources/application.properties | Java service settings | config
### What it is
Configuration that Spring reads at start-up. `${NAME:default}` means "use environment variable NAME, otherwise default".

### How it works
- `server.port=8081`
- Database: `SPRING_DATASOURCE_URL/USERNAME/PASSWORD` (docker-compose points them at the `postgres` container). `ddl-auto=update` creates missing tables.
- Kafka: bootstrap servers, a consumer group, and `max.block.ms=2000` for the producer (don't wait more than 2 s for Kafka).
- FaultScope: telemetry topic, this service's name, the C++ URL, and the fault-hook token (empty = hook disabled).

### Key parts
@snippet 1-21 | The whole file

## FILE java-service/pom.xml | Maven build file: which Java libraries the service uses | config
### What it is
`pom.xml` is Maven's project description. It lists the Java version (21), the Spring Boot version (3.5.4) and the libraries: `spring-boot-starter-web` (HTTP server), `spring-kafka`, `spring-boot-starter-actuator` (health/metrics), `spring-boot-starter-data-jpa` (database) and the `postgresql` driver.

### Key parts
@snippet 1-30 | Project, parent and first dependencies

## FILE java-service/Dockerfile | Two-stage build for the Java service | infra
### What it is
**Stage 1** (Maven + JDK 21) downloads dependencies and builds the JAR; **stage 2** (JRE only) copies just that JAR. The final image is much smaller because it has no build tools.

### How it works
Copying `pom.xml` and running `dependency:go-offline` before copying `src` lets Docker cache the downloads, so code-only changes rebuild quickly.

### Key parts
@snippet 1-21 | The whole file

# AREA cpp | 5. The C++ service | A hand-written HTTP server that does real CPU work
`cpp-service` is deliberately low-level: no framework, just sockets. It answers `GET /compute?n=…` by counting primes with the **sieve of Eratosthenes** — real, deterministic CPU work whose cost grows with `n` (that's why "cost of a request" matters in the CPU-starvation experiment).

Two facts to remember:
1. It handles **one request at a time** (a single-threaded loop). A slow request delays every one behind it.
2. It sends its telemetry using **librdkafka**, the C library behind most Kafka clients.

## FILE cpp-service/src/main.cpp | The whole server: HTTP parsing, prime counting, fault hook, telemetry | code
### What it is
One file containing a tiny HTTP/1.1 server. Beginners: a **socket** is an operating-system handle for a network connection; the program `bind`s to port 8082, `listen`s, then loops on `accept` to receive one client after another.

### How it works
1. `main` opens the socket and starts the telemetry emitter, then installs signal handlers for SIGTERM/SIGINT (graceful stop, so pending telemetry is flushed — fixing a bug where it ignored `docker stop`).
2. `readRequest` reads until the headers end **and** `Content-Length` bytes of body have arrived. An earlier version did a single `recv()` and lost bodies split across packets. A 2-second receive timeout stops half-open connections from freezing the single-threaded loop.
3. Routing is a chain of `if/else` on the request text:
   - `/_faults/http-error` — the fault hook (token check, validation, TTL — same rules as Python/Java)
   - `/compute` — parses `n`, validates it (1…5,000,000 → else 400), optionally fails on purpose (hook), otherwise calls `countPrimes` and times it
   - `/health` and `/metrics` (request counter and how many telemetry events were dropped)
4. After sending the response it builds a `telemetry::Event` (request ID, trace ID, latency, status, `injected_fault` marker) and emits it.

### Key parts
@snippet 99-116 | countPrimes: the sieve — the real CPU work
@snippet 57-92 | readRequest: read headers, then the full body
@snippet 342-375 | The /compute route
@snippet 427-445 | Emit telemetry for every request

??Why does CPU starvation only hurt *expensive* requests? || Cheap requests (small `n`) need almost no CPU, so a smaller CPU quota barely changes them. Expensive ones (n = 2,000,000) need a lot, and a quota makes them take many times longer — an observed ~40× p95 in the campaign.

### Connects to
- [[cpp-service/src/telemetry.hpp]] — the emitter
- [[java-service/src/main/java/com/aegis/service/jobs/CppClient.java]] — its only caller

## FILE cpp-service/src/telemetry.hpp | Builds telemetry JSON and sends it with librdkafka | code
### What it is
The C++ counterpart of [[app/telemetry.py]]: an `Event` struct, a JSON builder, and an `Emitter` class wrapping Kafka.

### How it works
- Hand-written helpers: `newUuid` (random version-4 UUID), `nowIso8601` (UTC timestamp with milliseconds), `jsonEscape` (safe JSON strings), `toJson` (builds the exact same fields as the Python schema).
- `Emitter` configures librdkafka: `linger.ms=20` (batch briefly) and `message.timeout.ms=5000` (give up on undeliverable events instead of buffering forever).
- `emit` **never blocks or throws**; if the message can't be queued it increments a `dropped` counter that `/metrics` exposes.
- If built without Kafka (`FAULTSCOPE_HAVE_KAFKA` undefined) the emitter is a counter of dropped events. The Docker build passes `-DREQUIRE_KAFKA=ON` so a shipped image can never silently lack telemetry.
- The destructor flushes pending events (2 s) so a graceful stop doesn't lose the last ones.

### Key parts
@snippet 95-114 | toJson: the event as JSON text
@snippet 131-135 | Kafka producer settings
@snippet 164-182 | emit: queue it or count it as dropped

## FILE cpp-service/src/CMakeLists.txt | How the C++ program is built | config
### What it is
CMake is a build-system generator. This file says: C++17, build `aegis-cpp-service` from `main.cpp`, link `librdkafka` if found (and **fail** if `REQUIRE_KAFKA=ON` and it isn't), link Winsock on Windows, and statically link the C++ runtime on Linux.

### Key parts
@snippet 1-31 | The whole file

## FILE cpp-service/Dockerfile | Compile in one image, run in a small one | infra
### What it is
Stage 1 (`gcc:13`) installs cmake and the Kafka library headers and compiles; stage 2 (`debian:bookworm-slim`) installs only the runtime Kafka library and copies the finished binary.

### Key parts
@snippet 1-25 | The whole file
