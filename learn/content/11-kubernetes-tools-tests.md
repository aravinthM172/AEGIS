# AREA k8s | 15. Kubernetes and the cloud plan | The same system on Kubernetes (measured), and an honest plan for the cloud (not done)
**Kubernetes** (K8s) runs containers across machines and keeps them alive: you describe the *desired state* ("2 copies of java-service, healthy") and Kubernetes works to make it true. FaultScope's workload runs on a local **kind** cluster ("Kubernetes IN Docker" — a whole cluster inside Docker on your PC).

Vocabulary: a **Pod** = one or more containers scheduled together; a **Deployment** keeps N Pods running and replaces dead ones; a **Service** gives a group of Pods one stable name (like `java-service`) and load-balances between them; **probes** ask "are you alive/ready?".

What was actually measured there: kill the java-service Pod with **1 replica → 14.3 % of requests failed**; with **2 replicas → 0 %**. A bad image deployed to cpp-service left the two healthy Pods serving (0 of 560 failed) and `rollout undo` restored it.

Limits, honestly: the cluster can't run the fault-injection experiments (the fault-agent is Docker-only), and nothing here is durable (`emptyDir`).

## FILE k8s/kustomization.yaml | Apply everything with one command | config
### What it is
Kustomize's entry file. It lists the two manifest files so `kubectl apply -k k8s` applies both in one go.

### Key parts
@snippet 1-3 | The whole file

## FILE k8s/infra.yaml | Postgres, Redis and Kafka as Deployments | infra
### What it is
The infrastructure: a Namespace `faultscope`, then a **Deployment + Service** for each of Postgres, Redis and Kafka.

### How it works
- **Probes**: readiness (send traffic only when ready) and liveness (restart if it hangs) using `pg_isready`, `redis-cli ping`, and a TCP check for Kafka.
- `emptyDir` volume for Postgres: data disappears with the Pod (a demo cluster, not durable).
- Kafka uses `strategy: Recreate` (single-node KRaft: never two brokers on one data directory).
- **`enableServiceLinks: false`** on Kafka fixes a subtle bug: Kubernetes injects environment variables for every Service, and a Service named `kafka` creates `KAFKA_PORT`, which the Kafka image misreads as *its own configuration*.

### Key parts
@snippet 7-33 | Postgres: Deployment with probes and a volume
@snippet 71-106 | Kafka: Recreate strategy, service-links fix, settings

??Why does the manifest disable service links for Kafka? || Kubernetes automatically creates variables like `KAFKA_PORT=tcp://10.0.0.5:9092` for a Service named `kafka`. The Kafka image reads any `KAFKA_*` variable as configuration, so it would be misconfigured by an accident of naming. Turning links off avoids it.

## FILE k8s/workload.yaml | The demo system, the consumer and the control plane on Kubernetes | infra
### What it is
Deployments and Services for cpp-service, java-service, gateway, telemetry-consumer and control-plane. Same images as Docker Compose; service names (`gateway`, `java-service`…) resolve via Kubernetes DNS.

### How it works
- **No start-order guarantees**: Pods crash-loop until Postgres/Kafka are up, then converge. That revealed a real bug in the control plane (see [[main.py]]'s retry loop).
- Java gets a **startupProbe** (up to 30 × 5 s) so Spring Boot isn't killed by the liveness check while it is still starting.
- The control plane has **no** `FAULTSCOPE_ENV` or `FAULT_AGENT_URL`, so real fault injection is refused here: dry runs and all read/analysis features work.
- `terminationGracePeriodSeconds: 10` on cpp-service — it now handles SIGTERM cleanly.

### Key parts
@snippet 39-71 | java-service: env, startupProbe, readiness, liveness
@snippet 137-165 | control-plane: note that real faults are refused

## FILE k8s/README.md | How to run it, and what was measured | docs
### What it is
Step-by-step instructions (create the cluster, load images, apply, port-forward, delete) and the measured results with their honest limits: no Kubernetes fault injector, `ROLLBACK_DEPLOYMENT` not wired to `kubectl`, non-durable data.

### Key parts
@snippet 34-47 | What was measured on the cluster
@snippet 55-64 | Limits

## FILE docs/cloud-plan.md | The AWS plan — a plan only, nothing was created | docs
### What it is
A cost-aware plan that says up front: **blocked by design** — no AWS CLI, credentials or Terraform exist on this machine and the rule is *never assume AWS is free*. It lists each AWS service, why it would be used and a "verify cost" note. Recommendations: don't use EKS (not free-tier), prefer **one small EC2 instance** with a budget alert and auto-stop, use S3 only for archives, create a billing budget first, never expose the fault-agent (it holds Docker access).

### Key parts
@snippet 1-8 | Status: not executed
@snippet 19-28 | Recommendation

# AREA tools | 16. Tools and your Job Tracker target | Load generator, and the isolated copy of your own app
## FILE tools/loadgen.py | A steady-traffic generator for the gateway | code
### What it is
A standalone script (standard library only): `python tools/loadgen.py --rps 5 --seconds 60`. It sends `POST /api/jobs` at a fixed rate and prints one line per second: ok/error counts, p50/p95 latency, a status histogram.

### How it works
- `one_request` sends one request with an `X-Trace-Id` and returns (status, milliseconds). Status `0` means no answer.
- `run` submits a task every `1/rps` seconds to a thread pool (so slow requests don't slow the schedule), buckets results per second, and prints them.
- Used to generate steady traffic so the **predictor** has live data (prediction needs requests).

### Key parts
@snippet 20-32 | One request
@snippet 49-60 | The pacing loop with a thread pool

## FILE targets/job-tracker/README.md | Your Job Tracker as a monitored target | docs
### What it is
Explains the isolated **test copy** of your Job Application Tracker (empty database, no credentials, never the live site) and lists the measured results: stopping the app made 100 % of user requests fail; a 300 ms network delay made pages ~5× slower with zero errors; CPU starvation timed everything out. It also records the real bug found: `GET /jobs/summary` always returns 422 because of route order.

### Key parts
@snippet 17-25 | Measured results table
@snippet 27-38 | Findings and limits

## FILE targets/job-tracker/Dockerfile | Adds FaultScope to your app without changing it | infra
### What it is
Starts `FROM job-tracker-base:local` (your untouched code, built separately), installs `kafka-python-ng`, copies in three FaultScope files (telemetry, telemetry middleware, fault hook) and the wrapper, sets `PYTHONPATH`, and starts the wrapper instead of your `main:app`.

### Key parts
@snippet 1-14 | The whole file

## FILE targets/job-tracker/faultscope_entry.py | The 12-line wrapper | code
### What it is
Imports your app object (`from main import app`) and adds two things to it: the fault hook first (inner), then the telemetry middleware (outermost). That is the *entire* integration: your source code stays exactly as it was.

### Key parts
@snippet 1-12 | The whole file

??Why install the fault hook *before* the telemetry middleware? || Middleware added later wraps the earlier ones. Telemetry must be outermost so it sees, and records, the failures the hook injects — otherwise experiments would create failures that never appear in the measurements.

## FILE targets/job-tracker/docker-compose.yml | Runs the test copy next to FaultScope | infra
### What it is
One service, `aegis-job-tracker`: labelled `faultscope.injectable=true` (so the agent may break it) with `fault_port 8000`, `SERVICE_NAME=job-tracker`, its own **test** data volume, published only on `127.0.0.1:8100`, joined to the FaultScope network (external) so it can reach Kafka, with a health check.

### Key parts
@snippet 15-27 | Labels, environment and the test-only volume
@snippet 40-43 | Joins the existing FaultScope network

# AREA tests | 17. The automated tests | 216 + 32 tests that prove the safety rules and the maths
Tests are programs that check other programs. Run them with `python -m pytest tests -q` (control plane, run on Python 3.12 like the containers) and `fault-agent/tests` (agent). **Every safety promise in this project has a test.** Most tests use in-memory fakes and SQLite so they run in seconds without Docker.

How to read a test: the function name is a sentence describing the promise (`test_failed_rollback_blocks_new_experiments_until_resolved`). If you break the promise in the code, that test fails.

Fault-agent tests ([[fault-agent/tests/test_core.py]], [[fault-agent/tests/test_stress_faults.py]], [[fault-agent/tests/test_actions.py]]) are explained in the fault-agent area.

## FILE tests/test_experiment_engine.py | The experiment lifecycle and its guardrails | test
### What it is
17 tests using a fake injector and a fake workload. Highlights: the full lifecycle records ordered events; **only one experiment can be active**; abort during injection still rolls back and frees the slot; a failed injection still rolls back; **a failed rollback blocks new experiments until resolved**; orphaned experiments are rolled back on start-up; real runs are blocked by preflight while dry runs are not; the workload stops when the experiment is aborted or fails; workload rate is bounded.

### Key parts
@snippet 97-106 | Only one experiment can be active
@snippet 142-155 | A failed rollback blocks everything
@snippet 158-171 | Crash recovery on start-up

## FILE tests/test_experiment_catalog.py | Request validation | test
### What it is
10 tests: valid requests pass; protected targets, unknown targets, unmapped containers and unknown faults are rejected; kind restrictions; parameters must be required, typed, bounded and defaulted; every catalog fault has a real injector; stress parameters are bounded.

### Key parts
@snippet 24-27 | Protected targets are rejected

## FILE tests/test_docker_injector.py | The control plane's client for the fault-agent | test
### What it is
7 tests: inject posts the payload with a TTL longer than the fault; the agent's refusal is surfaced with its reason; a missing container mapping is an error; an unreachable agent becomes a clean error, not a crash; rollback failure raises so the engine marks `ROLLBACK_FAILED`.

### Key parts
@snippet 69-71 | Rollback failure must raise

## FILE tests/test_campaign.py | Campaign runner | test
### What it is
9 tests: interleaved order; plan validation; runs every experiment in order; waits for a busy slot; abort stops the campaign *and* the experiment in flight; **a `ROLLBACK_FAILED` experiment stops the campaign**; analysis that never arrives doesn't hang it; a campaign left running by a restart is marked `INTERRUPTED`.

### Key parts
@snippet 66-68 | Repetition-major interleaving
@snippet 116-119 | A failed rollback stops the campaign

## FILE tests/test_workload_profiles.py | Per-target synthetic traffic | test
### What it is
6 tests for the profile feature added for the Job Tracker: unknown target → default (gateway) workload, missing file → default, invalid profiles rejected, the repository's profile is valid and targets only the job tracker, the runner uses profile requests and records real statuses (including 503s), and **without a profile it behaves exactly as before**.

### Key parts
@snippet 21-30 | Profile validation

## FILE tests/test_blast_radius.py | The damage-measurement maths | test
### What it is
15 tests: percentiles interpolate; failure propagates direct → indirect with a path; recovery time is the last degraded request after rollback; **latency amplification with no errors is detected**; a structurally at-risk but unaffected service is reported as resilient; **too little traffic → insufficient, not guessed**; an affected service the graph didn't predict is flagged; dry runs are labelled controls; the client observer is reported as end-user impact; the client sees impact even when service telemetry went blind.

### Key parts
@snippet 46-55 | Propagation: direct then indirect, with the path
@snippet 67-75 | Latency without errors is still damage
@snippet 89-95 | Not enough data is never guessed

## FILE tests/test_resilience.py | Signatures and resilience scores | test
### What it is
17 tests: signatures capture trigger, impact, modes, paths and recovery; the fingerprint is stable for the same shape; dry runs produce **control** signatures; score/level/confidence thresholds; repeated runs are aggregated with median and range; **different fault parameters and different load are never averaged together**; services with too little data are not scored; **the noise floor comes only from control runs**; a service is only scored against faults on things it depends on.

### Key parts
@snippet 101-106 | Different parameters are never averaged
@snippet 126-130 | Noise floor only from controls
@snippet 158-168 | Only score against real dependencies

## FILE tests/test_topology_graph.py | Graph algorithms and the shipped topology file | test
### What it is
13 tests: dependents = structural blast radius; dependencies are transitive with hop counts; a diamond counts each node once at the shortest distance; ranking is deterministic; validation rejects unknown endpoints, self-dependency, duplicates and **cycles (reporting the path)**; the repository's `topology.json` is valid, every edge has evidence, and the workload call chain is present.

### Key parts
@snippet 76-79 | Cycle path in the error
@snippet 86-90 | The real config file is checked

## FILE tests/test_topology_registry.py | Seeding the database from the topology file | test
### What it is
4 tests: seeding is idempotent; it updates and prunes declared edges; **never touches non-declared (observed) edges**; an invalid file changes nothing.

### Key parts
@snippet 38-43 | Idempotent seeding
@snippet 62-67 | A bad file changes nothing

## FILE tests/test_reconcile.py | Declared vs observed edges | test
### What it is
6 tests for the drift report: a declared call seen in telemetry is *confirmed*; declared but no calls is *unobserved*; **uninstrumented relations (databases, Kafka) are never reported as unobserved**; observed but undeclared is *drift*; an undeclared edge to an unknown service is flagged.

### Key parts
@snippet 28-32 | Absence of evidence isn't evidence of absence
@snippet 35-38 | Drift detection

## FILE tests/test_ai.py | Retrieval, the evidence package, the validator, the retry loop | test
### What it is
17 tests: the tokenizer keeps hyphenated names whole and split; chunking respects headings and size; BM25 ranks the relevant document first; rank fusion; evidence exposes only measured facts and citable refs; **a grounded answer is accepted; ungrounded or malformed answers are rejected with reasons**; numbers in the evidence or small counts pass; **a hallucinated answer is retried once with the reasons and then accepted; two bad answers are rejected, never shown as fact**; an unreachable model degrades to `llm_unavailable`; the prompt contains rules, valid refs and evidence only.

### Key parts
@snippet 160-167 | Retry once with the reasons, then accept
@snippet 170-175 | Two bad answers: rejected

## FILE tests/test_ai_service.py | The AI service against a database | test
### What it is
11 tests: repo documents load with source and title; re-indexing is incremental; hybrid retrieval when embeddings exist and BM25 fallback when they fail; experiment reports become retrievable knowledge; accepted / rejected / unavailable / failed analyses are each stored with the right status (and the raw rejected output); unfinished experiments are refused; a free-text question is linked to the right experiment.

### Key parts
@snippet 170-172 | An unreachable model is recorded honestly

## FILE tests/test_prediction.py | Anomaly detection, matching and the backtest | test
### What it is
21 tests: features summarise a window; assess flags latency and error anomalies and refuses to judge without enough data; a developing failure matches its signature and names who is at risk; **a healthy system produces no warning**; the *character* of an anomaly matters; control signatures are never used; confidence grows with support; the replay detects a known failure early, reports a miss when there is no history, and produces no false alarms for a control run.

### Key parts
@snippet 64-71 | A developing failure names who is at risk
@snippet 79-81 | A healthy system: no warning
@snippet 124-128 | Replay detects a known failure

## FILE tests/test_remediation_policy.py | The policy rulebook | test
### What it is
10 tests: a low-risk restart is allowed; **actions outside the allowlist are denied no matter what**; allowlisted actions without a mechanism are `unsupported`, not faked; unknown/protected/unmapped targets are denied; stateful restarts need approval; `CLEAR_CACHE` is limited to the cache prefix; remediation is blocked during an experiment and while another runs; cooldown stops restart loops.

### Key parts
@snippet 37-40 | Not on the allowlist: denied
@snippet 77-79 | Blocked during an experiment

## FILE tests/test_remediation_engine.py | The remediation pipeline | test
### What it is
13 tests: dry run plans and executes nothing; an allowed action executes and **verifies recovery with a measured time**; **recovery that never happens is `VERIFICATION_FAILED`, not success**; a failed verification tries the next strategy through the policy again; a stateful restart waits for admin approval; **approval is re-checked against current conditions**; the cooldown blocks a third remediation; `CLEAR_CACHE` deletes only `cache:` keys.

### Key parts
@snippet 94-98 | Never-recovered means failed
@snippet 147-158 | Approval re-checks the policy
@snippet 176-182 | Cooldown blocks a third action
@snippet 211-217 | Cache clear stays within its prefix

## FILE tests/test_remediation_verification.py | Recovery verification | test
### What it is
6 tests: the latency threshold follows the baseline or falls back; healthy probes verify; failing, slow or too few probes do not; a delayed recovery gets a measured recovery time; verification gives up and says so when the service never recovers.

### Key parts
@snippet 37-41 | Failing, slow or too few probes: not verified
@snippet 54-59 | Give up honestly

## FILE tests/test_auth.py | API keys and roles | test
### What it is
3 tests: a missing or wrong key → 401; roles are enforced and actors are labelled **without leaking the key**; **without configured keys, protected endpoints are disabled, not open**.

### Key parts
@snippet 24-26 | Wrong key: 401
@snippet 29-35 | Roles and non-leaking labels

## FILE tests/test_twin.py | The digital twin | test
### What it is
13 tests: fault types map to classes; measured results are aggregated from real signatures; coupling is learned *soft* for Kafka and *hard* for the call chain; **unmeasured edges are assumed hard and reported as an assumption**; a class never measured for an edge borrows from another class and says so; a queue decouples the edge; fallback scales impact by the miss ratio; replicas state their assumption; an unknown mutation is rejected, not ignored; validation exposes where structure alone is wrong.

### Key parts
@snippet 35-39 | Kafka soft, call chain hard
@snippet 42-46 | Assumptions are reported
@snippet 66-74 | A queue defers work, doesn't fail requests
@snippet 92-102 | Validation: hide a combination, predict it

## FILE tests/test_python_312_safety.py | A guard against a real production bug | test
### What it is
One test that scans the code for classes that define a method named like a built-in type (`list`, `set`, …). Why: under Python 3.12 (in Docker) but not the newer 3.14 on this PC, a method named `list` shadowed the built-in inside the class and broke start-up. Tests passed locally and the container crashed — so this guard now catches the whole class of bug.

### Key parts
@snippet 14-28 | The guard

# AREA docs | 18. The project README | The summary a stranger reads first
## FILE README.md | Architecture, how to run it, measured results, limits | docs
### What it is
The front page: what FaultScope is, an architecture sketch, the commands to start it, a table of **what was measured** (real runs), the **design rules**, and the **known limits**.

### How it works
Nothing in the README is a claim without a measurement behind it: every result row says what was run, what happened and how confident to be (e.g. the early-warning backtest is labelled *optimistic*). The "Known limits" section is as important as the results.

### Key parts
@snippet 44-56 | What was measured
@snippet 58-64 | Design rules that shaped it
@snippet 66-73 | Known limits

## FILE docs/commercial-readiness.md | Can this be sold? Name, licences, security and evidence gaps | docs
### What it is
An honest first-pass checklist (not legal advice) of what stands between this prototype and a commercial offering: whether the name is safe to use, which third-party licences matter (MinIO is AGPL, Redis and Llama have conditions), the security gaps, and the evidence a buyer would ask for. Section 6 tracks what has been fixed so far.

### Key parts
@snippet 37-47 | The security gaps table
@snippet 65-74 | Progress on the security gaps
