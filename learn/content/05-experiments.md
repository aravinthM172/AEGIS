# AREA experiments | 7. Experiments | Run a controlled failure, safely, and record exactly what happened
An **experiment** = "break *this* thing in *this* way for *this* long, while watching". The engine turns that into a strict, safe sequence:

`baseline` (watch normal behaviour) → `injecting` (apply the fault) → **rollback** (undo it) → `recovering` (watch it come back) → analysis.

Guardrails enforced in code:
- **Only one experiment at a time.** Enforced by a database rule, so two clicks in the same instant can't both win.
- **Rollback always runs** once injection was attempted — on success, abort, crash or error.
- If rollback *fails*, the experiment is `ROLLBACK_FAILED` and **blocks all new experiments** (the fault may still be active).
- The control plane and telemetry-consumer are **protected targets**: you can't break the tools that measure the break.
- If the control plane itself restarts mid-experiment, start-up rolls the experiment back.

@diagram lifecycle

## FILE app/experiments/catalog.py | The menu of faults and the rules for a valid request | code
### What it is
Pure data + validation (no database, no Docker). It describes every fault type — what it does, which kinds of target it can hit, its parameters and their allowed ranges — and validates a request before anything runs.

### How it works
- `FAULTS` is a dictionary of `FaultSpec`s: `stop_container`, `pause_container`, `restart_container`, `latency` (10–10000 ms, optional jitter), `http_error` (error rate 0.01–1, status 500–599; services only), `cpu_stress` (0.1–4 cores, 0–8 workers) and `memory_stress` (16–1024 MB).
- `implemented=True` means a real injector exists. **The engine never pretends**: a fault marked not implemented is accepted only as a dry run.
- `LIMITS` bound the times: `duration_s` 5–300, `baseline_s` 0–120, `recovery_s` 5–300.
- `validate_request` returns a list of human-readable errors (empty = OK) and the normalised parameters (defaults filled in). It refuses unknown targets, **protected targets**, targets without a container mapping, fault types that don't fit the target kind (e.g. `http_error` on a database), unexpected or out-of-range parameters, and booleans pretending to be numbers.

### Key parts
@snippet 9-15 | Protected targets and time limits
@snippet 69-82 | Two fault definitions with their parameter ranges
@snippet 113-155 | validate_request: target checks and fault checks
@snippet 157-178 | Parameter checking and defaults

??Why does `_is_number` reject `True` as an integer? || In Python `True == 1`, so a request like `"duration_s": true` would silently mean 1 second. The code rejects booleans explicitly so a typo can't become a surprising value.

## FILE app/experiments/injectors.py | The interface between the engine and the thing that breaks stuff | code
### What it is
The engine never touches Docker. It talks to an **injector** with two methods: `inject(exp)` and `rollback(exp)`. There are two injectors:
- `DryRunInjector` — applies nothing and labels everything `applied: False`. Used for control (no-fault) runs and for tests.
- `DockerInjector` — an HTTP client for the fault-agent. It sends `POST /faults` and `DELETE /faults/<id>`.

### How it works
- The contract says `rollback` **must be idempotent and safe even if inject never ran**, because the engine calls it on every exit path.
- The TTL sent to the agent is `duration_s + 20 s` — a safety margin so normal rollbacks always happen first; the TTL only fires if the control plane died.
- `real_run_preflight()` gives reasons a real run can't start: it needs `FAULTSCOPE_ENV=local` (real fault injection is only allowed on a local machine) and the agent must answer its health check (or, with `FAULTSCOPE_RUNTIME=kubernetes`, the Kubernetes API must answer).
- `injector_for` picks the runtime: the Docker fault-agent by default, or the in-cluster [[app/experiments/k8s_injector.py]]. `supported_real_faults()` tells the engine which faults the runtime can really inject, so an unsupported one is refused *before* anything starts.
- Network problems become `InjectorError` / `InjectorUnavailable`, which the engine handles.

### Key parts
@snippet 37-51 | DryRunInjector: does nothing, says so
@snippet 74-94 | DockerInjector.inject and rollback
@snippet 173-188 | Preflight: the conditions for a real experiment (Docker or Kubernetes)

### Connects to
- [[fault-agent/agent/main.py]] — the receiver
- [[app/experiments/engine.py]] — the only caller

## FILE app/experiments/models.py | The experiment tables, including the "only one active" rule | code
### What it is
Two tables: `experiments` (one row per experiment, with settings, status, timeline and result) and `experiment_events` (a step-by-step log: created, baseline_started, fault_injected, fault_removed…).

### How it works
- **The one-active-experiment rule** is a *unique partial index* on `active_slot`. That column is `TRUE` while the experiment is active and `NULL` otherwise; `NULL`s never collide, so at most one row can be `TRUE`. Race-free without any application-level locking.
- `ACTIVE_STATUSES` includes `ROLLBACK_FAILED`: the slot stays occupied until the fault is removed.
- Timeline columns matter for measurement: `fault_applied_at` (when the injector returned — the fault is truly in effect) and `rollback_started_at` (fault window ends). The analysis slices telemetry with these.
- `result` holds the blast-radius analysis as JSON (JSONB on Postgres, plain JSON in SQLite tests).
- `ensure_columns` adds columns that were introduced after the first deployment, because `create_all` never alters existing tables.

### Key parts
@snippet 16-25 | The unique partial index: one active experiment
@snippet 48-56 | The timestamps the analysis relies on
@snippet 69-83 | Adding columns to an existing database

??What happens if two people click "Run" at the same instant? || Both try to insert a row with `active_slot = TRUE`. The database allows only one; the loser gets an `IntegrityError`, which the engine translates to "another experiment is already active" (HTTP 409).

## FILE app/experiments/engine.py | The experiment runner: lifecycle, guardrails, rollback discipline | code
### What it is
The most important file in the control plane. `ExperimentEngine` creates experiments, runs each one on its own background thread, and guarantees the safety rules.

### How it works — create
1. Load the registered services and `validate_request`. Check workload limits (≤ 25 requests/s). For a **real** run, also check the preflight.
2. Insert the row with `active_slot=True` and a `created` event. `flush()` writes the parent row first so the child event's foreign key is valid.
3. If an `IntegrityError` occurs and another active experiment exists → `ActiveExperimentExists`. If none exists, the error is re-raised — it must **never be misreported** as "one at a time".
4. Start the runner thread and return immediately.

### How it works — the lifecycle (`_lifecycle`)
1. **baseline** — set `RUNNING/baseline`; wait `baseline_s` (checking for abort).
2. **injecting** — `injector.inject(exp)`, stamp `fault_applied_at`, record a `fault_injected` event with what was *actually* done; wait `duration_s`.
3. **rollback** — *always* if injection was attempted: stamp `rollback_started_at`, call `injector.rollback`, record `fault_removed`. If rollback raises → `ROLLBACK_FAILED`, stop, **keep the slot occupied**.
4. **recovering** — wait `recovery_s` while the system heals and telemetry keeps flowing.
5. `_finish` writes the final status and the final event **in one transaction** (an observer that sees "COMPLETED" must also see the event explaining it), then schedules the analysis a few seconds later so the telemetry consumer can catch up.

### Other paths
- **abort**: sets `abort_requested` in the DB *and* trips an in-process `threading.Event`. The Event exists because if the experiment targets Postgres itself, the DB flag may be unreachable.
- **retry_rollback**: re-run rollback for a `ROLLBACK_FAILED` experiment; success turns it into `FAILED` and frees the slot.
- **recover_orphans**: at start-up, roll back every experiment still `PENDING`/`RUNNING` (the control plane was restarted mid-run).
- **workload**: if `workload_rps > 0`, synthetic traffic runs for the whole experiment and its results are stored with it.

### Key parts
@snippet 141-156 | create: validate everything before touching anything
@snippet 167-181 | Insert, and translate the race into a clear error
@snippet 303-318 | Baseline then inject, catching any failure
@snippet 320-332 | Rollback on every path; a failed rollback blocks everything
@snippet 334-345 | Recovery window, stop workload, finish
@snippet 347-357 | _wait: sleeps, but wakes instantly on abort
@snippet 376-387 | _finish: status + final event in one transaction

??Why does the engine record `fault_applied_at` *after* `inject()` returns instead of before calling it? || Between "asked to inject" and "inject finished" the fault may only be partly in effect (e.g. a container still stopping). Analysis compares baseline vs fault windows, so the fault window must start when the fault is truly on.

### Connects to
- [[app/experiments/catalog.py]], [[app/experiments/injectors.py]], [[app/experiments/workload.py]]
- [[app/analysis/service.py]] — `on_finished` triggers the blast-radius analysis

## FILE app/experiments/workload.py | Synthetic users: fake traffic that measures from the outside | code
### What it is
A load generator that runs *inside* an experiment. It sends requests at a fixed rate (`workload_rps`) and records, for each one, the time, latency and HTTP status. Because it sits **outside** the system, it still sees failures when a service is completely dead and can't report anything itself.

### How it works
- `WorkloadRunner` runs a loop that fires one request every `1/rps` seconds into a thread pool (so a slow request doesn't delay the schedule).
- Default behaviour: `POST /api/jobs?n=…` to the gateway.
- **Per-target profiles** (`config/workloads.json`): for a target like `job-tracker`, `load_profile` returns a different base URL and a weighted list of requests (GET/POST). This was added so your Job Tracker could be measured from the outside.
- Every request outcome is counted; status `0` means "no HTTP answer at all" (connection error or timeout). Up to 5000 samples are kept.
- `stop()` returns totals (sent, ok, failed, status counts) plus the raw samples; the analysis uses these as the **`workload-client`** view — "what a real user experienced".

### Key parts
@snippet 28-46 | load_profile: read and validate a per-target profile
@snippet 92-105 | The pacing loop
@snippet 107-122 | One request: record latency and status (0 = no answer)

### Connects to
- [[config/workloads.json]] — the per-target profiles
- [[app/analysis/service.py]] — turns the samples into the client-side measurement

## FILE app/experiments/routes.py | The experiment HTTP endpoints | code
### What it is
The URLs the dashboard and scripts use under `/api/experiments`.

### How it works
| Method + URL | Who | Purpose |
|---|---|---|
| `GET /catalog` | anyone | Fault types, limits, protected targets |
| `POST /` | **operator** | Create and start an experiment |
| `GET /` and `GET /{id}` | anyone | List / inspect (with events) |
| `GET /{id}/result` | anyone | Measured blast radius (404 until analysis is ready) |
| `POST /{id}/analyze` | **operator** | Recompute analysis from stored telemetry |
| `POST /{id}/abort` | **operator** | Kill switch (202 accepted) |
| `POST /{id}/rollback` | **operator** | Retry a failed rollback |

`require_role("operator")` is the permission check (see [[app/remediation/auth.py]]). Errors map to statuses: validation → 422, another experiment active → 409 (with its id), unknown id → 404.

### Key parts
@snippet 20-31 | The request body schema
@snippet 40-50 | Create: operator only; 422/409 handling
@snippet 97-105 | The abort kill switch

## FILE app/experiments/campaign.py | Run a whole plan of experiments, several times | code
### What it is
One experiment is one measurement. A **campaign** runs many, repeatedly, so results become statistics with confidence instead of anecdotes.

### How it works
- `expand(plan)` builds the run list **repetition-major** (repeat 1 of every scenario, then repeat 2 …). Interleaving spreads slow drift (warm caches, background load) evenly across scenarios.
- `validate_plan`: non-empty list, 1–10 repetitions, each item has `target`, `fault_type`, `duration_s`, at most 60 runs.
- `CampaignRunner._run` starts one experiment at a time, waits until it is finished **and analysed**, then a **cooldown** (default 20 s) so one run's aftermath doesn't contaminate the next baseline.
- If another experiment is active it waits for the slot (up to 5 min). If a run ends `ROLLBACK_FAILED` the campaign **stops** — continuing would be unsafe.
- `abort` stops the campaign and the experiment in flight. `recover_interrupted` marks campaigns as `INTERRUPTED` after a control-plane restart.

### Key parts
@snippet 45-48 | expand: repetition-major order
@snippet 159-175 | The run loop
@snippet 187-203 | Wait for finish, refuse to continue after ROLLBACK_FAILED

??Why interleave repetitions instead of running all repeats of one scenario back to back? || If something slowly changes (Docker caches warming, disk filling), back-to-back runs would make the last scenario look different only because of *when* it ran. Interleaving gives every scenario the same mix of early and late conditions.

## FILE app/experiments/campaign_routes.py | The campaign HTTP endpoints | code
### What it is
`POST /api/campaigns` starts a campaign from a named **suite** file in `config/campaigns/` (for example `{"suite": "standard"}`) or from an inline list. You can override `repetitions`, `name` and `cooldown_s`. `GET` lists/inspects; `POST /{id}/abort` stops one. Starting and aborting need the **operator** role.

Suite names are checked (letters, digits, `-`, `_` only) so nobody can read arbitrary files with `../`.

### Key parts
@snippet 25-32 | Load a suite safely
@snippet 35-48 | Start a campaign

## FILE config/campaigns/standard.json | The standard 8-scenario resilience suite | config
### What it is
A campaign plan: eight scenarios × 3 repetitions, 15 s cooldown, all under the same 4 requests/s load.

### How it works
| # | Scenario |
|---|---|
| 1 | stop `cpp-service` |
| 2 | starve `cpp-service` of CPU (heavy requests) |
| 3 | freeze (`pause`) `java-service` |
| 4 | 60% HTTP 503 errors on `java-service` |
| 5 | 300 ms latency on `postgres` |
| 6 | stop `kafka` |
| 7 | freeze `redis` |
| 8 | **control**: a dry run — no fault — measures the normal noise |

The control run is essential: it tells you how much "impact" is just normal variation.

### Key parts
@snippet 7-14 | The eight scenarios

## FILE config/workloads.json | Per-target synthetic traffic profiles | config
### What it is
For targets other than the demo chain, this file says where to send synthetic users and which requests to make. Currently one entry: `job-tracker` → `http://job-tracker:8000` with weighted requests: `GET /jobs` (4), `GET /dashboard` (3), `GET /health` (1), `POST /jobs` (2).

### Key parts
@snippet 1-12 | The whole file

## FILE app/experiments/k8s_injector.py | Fault injection through the Kubernetes API (stop and restart) | code
### What it is
The Kubernetes version of "break something safely", used when `FAULTSCOPE_RUNTIME=kubernetes` (see [[app/experiments/injectors.py]]). It talks to the Kubernetes API using the pod's own **service account**, whose permissions ([[k8s/rbac.yaml]]) reach only Deployments and pods in one namespace.

### How it works
- **Allowlist**: only a Deployment labelled `faultscope.injectable: "true"` can be touched, otherwise it refuses — the same rule as the Docker agent.
- **Intent first**: before changing anything it writes three **annotations** on the Deployment: the experiment id, the original replica count, and an expiry time. Because the record lives on the Kubernetes object itself, *any* later process can find and undo the fault.
- **`stop_container`** scales the Deployment to 0 and waits until no live pod remains (pods that are merely terminating don't count). **`restart_container`** deletes every pod and waits until all the old ones are gone.
- **Rollback** scales back to the original count, waits for that many *ready* replicas, and only then removes the annotations — so if recovery fails the intent is still recorded. It is idempotent.
- **Dead-man's switch**: `recover_expired` restores any Deployment whose expiry has passed; a watchdog thread runs it every 15 s, and at start-up everything left behind is restored.
- Timeouts are explicit: it never claims success it didn't verify.
- One fault per Deployment; other fault types (latency, CPU, memory, HTTP errors) are refused as unsupported.

### Key parts
@snippet 27-31 | The allowlist label and the annotation names
@snippet 148-183 | inject: allowlist, intent first, then stop or restart
@snippet 185-208 | rollback: restore, verify, then clear the intent
@snippet 210-226 | The watchdog and start-up recovery

??Why record the fault as annotations on the Deployment instead of only in memory? || Memory disappears when the control plane dies. Annotations live in Kubernetes itself, so a restarted control plane (or anyone else) can see exactly what was changed and how to undo it. It was tested by deleting the control-plane pod mid-fault.
??What is weaker about this than the Docker fault-agent? || The watchdog runs inside the control plane. If that process dies, the fault stays until Kubernetes restarts it; the Docker agent is a separate program that keeps guarding.

## FILE tests/test_k8s_injector.py | Tests for the Kubernetes injector, using a fake API | test
### What it is
11 tests with an in-memory fake of the Kubernetes API: stop then rollback restores replicas and clears the intent; **intent is recorded before anything changes**; unlabelled or unknown deployments and unsupported faults are refused untouched; one fault per Deployment; rollback is idempotent; restart replaces every pod; **pods that never terminate time out instead of claiming success**; a failed recovery keeps the intent; the watchdog restores expired faults only, and start-up recovery restores all; runtime selection.

### Key parts
@snippet 124-128 | Intent before change
@snippet 183-187 | Stuck pods: timeout, not success
@snippet 200-213 | Watchdog and start-up recovery
