Allowlist :: A list of the only things that are permitted. Anything not on it is refused. FaultScope allowlists which containers can be broken and which remediation actions exist.
API :: A set of URLs a program answers, so other programs can talk to it. `GET /api/health` is one endpoint of the control plane's API.
API key :: A secret string sent in the `X-API-Key` header that says who you are. Here it maps to a role (viewer, operator, admin).
Async / asynchronous :: Doing something without waiting for it to finish. Telemetry is sent asynchronously so requests aren't slowed.
At-least-once delivery :: A message is guaranteed to arrive, possibly more than once. The telemetry consumer copes by ignoring duplicates.
Audit log :: An append-only diary of every decision and action, so you can see who did what and why.
Backtest :: Replaying the past to measure how well a predictor would have done. Here it is leave-one-out so the predictor can't peek at the answer.
Baseline :: The healthy reference period before a fault. Everything is compared against it.
Blast radius :: How far the damage of one failure spreads — which services are hurt and by how much.
BM25 :: A classic keyword-ranking formula for search. It rewards rare words and adjusts for document length.
Broker :: A program that passes messages between other programs. Kafka is a broker.
C++ :: A low-level programming language used for the cpp-service, which handles requests one at a time.
Chaos engineering :: Deliberately breaking a system in a controlled way to learn how it fails.
Circuit breaker :: A pattern that stops calling a failing dependency for a while, instead of piling more requests on it.
Container :: A light, isolated box holding one program and everything it needs. Docker runs containers.
Control experiment :: An experiment with no fault (a dry run) used to measure normal variation. See noise floor.
Control plane :: The FaultScope brain (FastAPI on port 8000): experiments, analysis, prediction, AI and remediation.
Coupling :: In the digital twin, how much of a callee's damage reaches its caller (0 = none, 1 = all).
CORS :: A browser rule that blocks a web page from calling another address unless that address allows it. The control plane allows the dashboard.
Cooldown :: A waiting period. Between campaign runs, and between remediation actions on the same target (loop protection).
Dead-man's switch :: A safety that triggers when nobody is watching. Every fault has a TTL, and the fault-agent undoes it itself if the control plane disappears.
Declared topology :: The service map written by a human in `config/topology.json`, where every edge must cite evidence.
Degraded (request) :: A request that failed or was much slower than normal.
Dependency :: Something a service needs to do its job. If A calls B, A depends on B.
Deterministic :: Always the same output for the same input. Most FaultScope logic is deterministic; only the LLM is not.
Digital twin :: A simplified model of the system used for what-if questions. Its results are always labelled simulated.
Direct / indirect (impact) :: Direct: the service calls the broken one (1 hop away). Indirect: 2 or more hops away.
Docker :: Software that runs containers. `docker compose up` starts the whole FaultScope stack.
Docker socket :: A special file that lets a program control Docker. Whoever holds it can control every container, so only the fault-agent gets it.
DAG :: Directed acyclic graph: arrows with no loops. The dependency map must be one.
Dry run :: An experiment (or action) that changes nothing. It records what would happen.
Embedding :: A list of numbers representing the meaning of a text, so similar texts are close together. Used for semantic search.
Endpoint :: One URL of an API, like `POST /api/experiments`.
Entity linking :: Matching words in a question ("db is slow") to real things (the postgres experiment).
Event :: One telemetry record: which service, what happened, how long, what status.
Evidence package :: The only information the LLM sees: measured facts plus retrieved documents, each with a reference it can cite.
Experiment :: A controlled failure: break this thing in this way for this long, while measuring.
FastAPI :: A Python framework for building APIs. The control plane and gateway use it.
Fault :: The thing that is deliberately broken: a stopped container, added latency, CPU starvation and so on.
Fault hook :: A token-protected switch inside a service that makes a fraction of requests fail on purpose (the `http_error` fault).
Fault-agent :: The single program allowed to control Docker, guarded by an allowlist, TTLs and crash recovery.
Fingerprint :: A short hash summarising a failure's shape. Same shape, same fingerprint.
Foreign key :: A database column that must point to an existing row in another table.
Gateway :: The front door of the demo system. Users call it and it forwards to the Java service.
Grounding :: Making sure an answer is backed by real evidence. The validator rejects ungrounded AI output.
Hallucination :: When an AI states something that isn't true or isn't in its evidence.
Health check :: A small request ("are you OK?") used to decide if a service is running properly.
Hop :: One step along a dependency chain. `cpp-service` is 1 hop from `java-service` and 2 hops from `gateway`.
Hypothesis :: A guess to be tested. An AI diagnosis is only ever a hypothesis.
Idempotent :: Doing it twice has the same effect as doing it once. Rolling back a fault must be idempotent.
Injector :: The piece that applies and removes a fault. There is a dry-run injector and a Docker injector.
Insufficient data :: The honest answer when there isn't enough evidence to say anything. FaultScope prefers it to guessing.
Java / Spring Boot :: A language and framework used for the java-service.
JSON :: A text format for data with `{"name": "value"}` pairs. All the APIs and telemetry events use it.
JSONB :: A Postgres column type that stores JSON efficiently and lets you query inside it.
Kafka :: A durable message pipe. Services drop telemetry events in; the consumer reads them out.
kind :: A tool that runs a whole Kubernetes cluster inside Docker, used for the local Kubernetes tests.
Kubernetes :: A system that runs containers across machines and keeps the desired number healthy.
Latency :: How long a request takes.
Leave-one-out :: Testing by hiding one item, predicting it from all the others, then comparing with the truth.
LLM :: Large language model, an AI that writes text. FaultScope uses a small local one (llama3.2:3b) only to explain results.
Label (Docker) :: A tag on a container. `faultscope.injectable=true` marks it as breakable.
Measured :: Came from a real experiment on this system. Opposite of simulated or assumed.
Middleware :: Code that wraps every request, like a checkpoint. Telemetry is recorded by middleware.
Netem :: A Linux feature (`tc netem`) that adds network delay, used by the latency fault.
Noise floor :: How much apparent impact appears even with no fault. Impacts near it can't be trusted.
Ollama :: A program that runs language models locally on your PC.
Operator / admin / viewer :: The three roles. Viewer < operator < admin. Operators run experiments; admins approve risky actions.
Orphaned experiment :: One left active because the control plane restarted mid-run. Start-up rolls it back.
p50 / p95 :: The latency that 50% (median) or 95% of requests beat. p95 shows the slow tail.
Partial unique index :: A database rule that limits rows matching a condition. It allows only one active experiment.
Policy engine :: Pure code that decides whether an action is allowed, needs approval, is denied or unsupported.
Postgres :: The main database: experiments, telemetry history, signatures, knowledge and the audit log.
Predictor :: The early-warning code that matches current anomalies to past failure signatures.
Probe :: A small real request used to check health, for example the gateway probes that verify recovery.
Propagation :: Damage travelling from the broken service to the ones that depend on it.
Protected target :: A service that may never be broken or auto-remediated: the control plane and telemetry-consumer.
Pydantic :: A Python library that checks data has the right shape. It validates the AI's JSON.
RAG :: Retrieval-augmented generation: search documents first, then let a model answer from them.
Recovery time :: How long after the fault was removed until requests are healthy again.
Redis :: A very fast in-memory store used for the latest state per service and a few caches.
Remediation :: Fixing a problem: restarting a service, lifting a CPU cap, clearing a cache.
Replica :: One of several identical copies of a service.
Resilience score :: 100 minus the median measured impact. Higher means the service survives that failure better.
Role :: A permission level attached to an API key.
Rollback :: Undoing a change or fault. The engine always rolls back after injecting.
Runbook :: A checklist for handling one kind of incident: symptoms, diagnosis, fix, prevention.
Signature (failure) :: A compact, measured description of what one experiment did to the system.
Silent (service) :: A service that normally emits telemetry but emits nothing now, meaning it is down, frozen or not receiving traffic.
Simulated :: Produced by the digital twin's model, not measured. Always labelled.
SQLAlchemy :: A Python library for talking to databases using Python classes.
Synthetic user :: A fake user (the workload runner) that sends requests during an experiment and measures from outside.
Target :: The service an experiment breaks.
Telemetry :: Small records a program writes about itself (latency, status, errors) so it can be measured.
Thread :: A path of execution inside a program. Experiments, campaigns and analysis run on background threads.
Timeout :: How long a caller waits before giving up. Every remote call needs one.
Trace id :: A label passed along a request's whole journey so its events can be joined up.
TTL :: Time to live: how long something lasts before expiring. Faults have TTLs so they can't last forever.
Uvicorn :: The server that runs FastAPI apps.
Validator :: Code that checks something is acceptable, such as the grounding validator for AI answers.
Verification :: Proving something worked with real evidence, such as real probes after a restart.
Workload :: The traffic flowing through a system. The workload runner generates fake traffic during experiments.
