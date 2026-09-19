# AREA prediction | 11. Prediction: early warning | "Does what's happening now look like a failure we've seen before?"
Prediction here is **not machine learning**. It is transparent pattern matching over the failure signatures you collected:

1. Look at each service's **last few seconds** and compare with its own recent normal (→ `normal`, `anomalous`, `silent` or `insufficient_data`).
2. Compare the set of currently anomalous services with each stored signature ("when postgres was slow, java-service and gateway degraded with *latency*").
3. If it resembles one closely enough (similarity ≥ 0.6), warn: *"this looks like latency on postgres; services still at risk: …; last time end users saw X % impact and recovery took Y s."*

**Accuracy is never claimed** by the predictor. It's measured separately by a **backtest**, and that number is labelled optimistic (the rules were tuned on the same data).

## FILE app/prediction/core.py | Anomaly detection and signature matching (pure) | code
### What it is
The rules, with no database and no machine learning. Deterministic: the same input always gives the same output.

### How it works — judging one service
`features` computes `n` (event count), error % and p95 for a time window. `assess` compares "recent" with "baseline":
- baseline had traffic but recent has **none** → `silent`
- too few events (< 6) → `insufficient_data`
- **slow** if p95 > max(2 × baseline p95, baseline p95 + 50 ms); **errors** if the error rate is ≥ 10 points above baseline
- either → `anomalous` (with modes `errors` and/or `latency`), else `normal`.

`build_state` adds a subtle rule: **silence only counts as evidence while other services still receive traffic.** If nothing is being requested anywhere, quiet services aren't failing — there is just no load.

### How it works — matching
- `signature_profile` extracts, from a stored signature, which services were affected and how (including *silent* ones, marked by role).
- `match` computes `similarity = precision × agreement`. *Precision*: of the services that are anomalous now, how many does the signature explain? *Agreement*: do the failure modes (errors/latency) match? If the signature's own target is currently **measurably healthy**, similarity is multiplied by 0.3 — a different fault, probably.
- `progress` says how much of the signature has appeared so far; `at_risk` are the signature's services **not yet** anomalous.
- `predict` returns one warning per (target, fault) with `stage` (`developing` if progress < 1, else `established`), the *risk* of each at-risk service, expected end-user impact and recovery from history, and **confidence** from the number of supporting signatures (low < 3, medium < 5, high ≥ 5).

### Key parts
@snippet 15-21 | The tuning knobs
@snippet 34-47 | assess: normal, anomalous or silent?
@snippet 50-59 | Silence counts only if others have traffic
@snippet 88-106 | match: similarity from precision × mode agreement
@snippet 109-141 | predict: best warning per (target, fault)

??Why is silence treated as evidence only when other services have traffic? || A stopped service emits nothing — that's a strong clue *if* users are still sending requests. At 3 a.m. with zero load, every service is silent and nothing is wrong. Without this rule the predictor would raise false alarms whenever traffic paused.

## FILE app/prediction/service.py | Live early warning and the backtest, from real telemetry | code
### What it is
The database-facing part: loads recent telemetry and stored signatures, and calls the pure core.

### How it works
- `live_prediction`: "recent" = the last `window_s` seconds (default 8); the **baseline** = the 5 minutes before that. Result: each service's state, anomalous services, warnings, and a `status` of `ok` or `insufficient_data` — with a note that prediction *needs live requests*.
- `run_backtest` loads every finished, analysed experiment with its telemetry and replays it.
- Protected services are excluded from the data.

### Key parts
@snippet 45-63 | live_prediction

## FILE app/prediction/backtest.py | Measures how good the predictor really is | code
### What it is
A **leave-one-out replay**. For each stored experiment it re-plays the telemetry second by second; at each step the predictor sees **only events up to that moment and only signatures from *other* experiments**, so it cannot have learned this experiment's outcome. All accuracy claims come from here — nowhere else.

### How it works
- Per experiment: was the fault **detected** (any warning during the fault window), was the attribution **correct** (right target *and* fault type), the **lead time**, the first warning's target, and how many **false alarm** steps occurred in the baseline (for control runs, any warning at all is false).
- `_detectable`: a fault whose window has *no* anomalous service at all can't be detected by anything, so it is counted separately rather than as a failure of the predictor.
- `summarize` computes detection rate, correct-attribution rate, median lead time and false-alarm rate — and lists **caveats** in the result itself: tuned after inspecting an earlier version (optimistic), small sample, one system, sparse traffic means no warning.

### Key parts
@snippet 13-51 | replay_experiment: what the predictor sees at each second
@snippet 60-92 | summarize, with honest caveats

> Measured on this repo: the first version detected 77 % but attributed the right cause only 36 % of the time; after tuning: 96 % / 82 % with 2 false alarms in 105 steps — flagged as optimistic. Real incidents (a real `docker stop`) were detected in seconds, but the **first warning was misattributed both times**.

## FILE app/prediction/routes.py | Prediction endpoints | code
### What it is
`GET /api/predictions?window_s=8` (live early warning; needs traffic) and `GET /api/predictions/backtest` (the only source of accuracy numbers).

### Key parts
@snippet 8-17 | Both endpoints

# AREA remediation | 12. Remediation: controlled self-healing | An action is only allowed after policy checks — and only "fixed" once real probes prove it
"Self-healing" sounds risky: software that changes your system by itself. So the design is deliberately strict.

**The pipeline:** request → **policy** → (admin approval if risky) → execute → **verify with real requests** → (optionally try the next strategy).

Key ideas:
- **Allowlist of action *names*** — the system never accepts a command, only a name like `RESTART_SERVICE` plus a target service.
- **Policy engine** — pure code that returns `allow`, `needs_approval`, `deny` or `unsupported`, with reasons.
- **Verified recovery** — "it's fixed" is only claimed after real end-to-end probes succeed, not because the restart command returned.
- **Audit log** — every decision and execution is recorded.
- **Roles** — `viewer < operator < admin`. Operators propose; only admins approve risky actions.
- The AI can *suggest* an allowlisted action name; it goes through exactly the same policy.

## FILE app/remediation/policy.py | The rulebook that decides whether an action may run | code
### What it is
A pure function `evaluate` that never executes anything. It returns a **Verdict** (`allow` / `needs_approval` / `deny` / `unsupported`) with a risk level and the reasons.

### How it works — the checks, in order
1. Action must be on the **allowlist** (`RESTART_SERVICE`, `SCALE_SERVICE`, `ROLLBACK_DEPLOYMENT`, `CLEAR_CACHE`, `ENABLE_FALLBACK`). Unknown → deny.
2. Must be **supported** in this environment. `ROLLBACK_DEPLOYMENT` and `ENABLE_FALLBACK` are honestly `unsupported` (no deployment history / feature flags exist here).
3. Target must exist and **not be protected** (`control-plane`, `telemetry-consumer`: the measurement plane is never auto-remediated).
4. `CLEAR_CACHE` only applies to a cache and only deletes keys starting with `cache:`. Other actions need a container mapping.
5. **Blocked while an experiment is active** (so they don't fight), while another remediation is running (one at a time), and by a **cooldown** — 2 actions per target per 10 minutes (loop protection: escalate to a human).
6. **Risk**: restarting a *stateful* service (database, broker, cache) is `high` → `needs_approval` unless already approved.
- `action_for_fault` maps a fault character to a sensible remedy (`stop_container` → `RESTART_SERVICE`, `cpu_stress` → `SCALE_SERVICE`, …).

### Key parts
@snippet 13-26 | The allowlist, unsupported reasons and limits
@snippet 46-57 | Allowlist, supported, known target, protected target
@snippet 69-85 | Experiment block, cooldown, risk and approval
@snippet 88-92 | Fault → remedy mapping

??Why is `SCALE_SERVICE` implemented as "lift the CPU cap"? || In a plain Docker setup there is no autoscaler. The only real "give it more capacity" primitive available is removing the container's CPU quota (vertical scaling). The system implements what actually exists rather than pretending.

## FILE app/remediation/auth.py | API keys and roles | code
### What it is
Who may do what. `FAULTSCOPE_API_KEYS="key1:operator,key2:admin"` defines keys and roles (`viewer` 1 < `operator` 2 < `admin` 3).

### How it works
- `require_role("operator")` is a FastAPI dependency placed on state-changing routes. It reads the `X-API-Key` header.
- **Secure by default**: if no keys are configured, protected endpoints answer **503** instead of being left open.
- Keys are compared with `hmac.compare_digest` (constant time, so timing can't leak the key).
- Errors: missing/invalid → **401**, role too low → **403**.
- The "actor" recorded in the audit log is the role plus the first 4 characters of the key — never the whole secret.

### Key parts
@snippet 16-31 | Parse keys and check one safely
@snippet 34-48 | The role dependency

> The dev keys (`dev-operator-key`, `dev-admin-key`) exist only for local use. Reading data is open; only actions are protected.

## FILE app/remediation/models.py | Action and audit-log tables | code
### What it is
- `remediation_actions` — every proposed action with what was decided and observed: `mode` (`dry_run`/`execute`), `status`, who requested and approved, `source` (manual / analysis / prediction), the policy verdict, the executor result, the verification result and any remaining strategies.
- `audit_logs` — **append-only**: timestamp, actor, event, reference id, details.

### Key parts
@snippet 8-29 | The actions table
@snippet 32-41 | The audit log

## FILE app/remediation/executor.py | Runs an allowed action using only safe primitives | code
### What it is
The hands. It runs **only what policy already allowed**.
- `AgentExecutor` — Docker-level actions via the fault-agent (`RESTART_SERVICE` → `restart`, `SCALE_SERVICE` → `lift_cpu_cap`). The control plane still has no Docker access.
- `CacheExecutor` — `CLEAR_CACHE` deletes keys under `cache:` only. The prefix is **re-checked here** (defence in depth: even if the policy were bypassed, it can't delete other keys).

### Key parts
@snippet 8-8 | Which actions go to the agent
@snippet 24-35 | Call the agent and translate failures
@snippet 44-51 | Cache clear with a second prefix check

## FILE app/remediation/verification.py | Proving recovery with real probes | code
### What it is
"Recovery verified" is claimed **only** if, within the time limit, real end-to-end probes show: at least 8 probes, a success rate ≥ 90 %, and p95 latency back within roughly normal (max(2× reference p95, reference + 100 ms); 1500 ms if no reference is known).

### How it works
`verify_recovery` waits a settling period (3 s: restarted processes and DNS caches need it), probes in rounds of 10, and judges each round; it returns the recovery time, or a failure with the reason after 45 s. The probe and the clock are **injected functions**, so tests need no real time or network.

### Key parts
@snippet 25-46 | judge_probes: the definition of "healthy"
@snippet 49-65 | verify_recovery: probe until healthy or out of time

## FILE app/remediation/engine.py | Request → policy → approval → execute → verify → next strategy | code
### What it is
The remediation orchestrator, written in the same careful style as the experiment engine.

### How it works
- `request` validates mode (`dry_run` default, or `execute`) and at most 2 follow-up strategies (allowlisted), asks the policy, stores the action and **audit entries** ("requested", "policy_verdict"), and — only if the verdict was `allow` and mode is `execute` — starts execution on a thread.
- `_initial_status`: deny → `DENIED`, unsupported → `UNSUPPORTED`, dry run → `PLANNED`, allow → `RUNNING`, risky → `PENDING_APPROVAL`.
- `approve` **re-evaluates the policy** before running: if circumstances changed (an experiment started meanwhile), the action becomes `DENIED` instead of running on stale approval. `reject` ends it.
- `_execute_and_verify`: execute → store the result → verify → `VERIFIED` or `VERIFICATION_FAILED`. If verification failed and strategies remain, it requests the next one (`system:next-strategy`).
- `GatewayProbe` sends **real requests through the gateway** — the same path users take. `gateway_reference_p95` computes healthy p95 from telemetry (15 minutes, excluding the last minute).
- A crash marks the action `FAILED` with an audit entry.

### Key parts
@snippet 58-76 | GatewayProbe: real user-path requests
@snippet 107-132 | request: policy first, audit everything
@snippet 134-155 | approve: re-check the policy at approval time
@snippet 194-202 | Statuses decided by the verdict
@snippet 239-262 | Execute, verify, and try the next strategy

??Why re-run the policy when an admin approves? || Time passes between request and approval. If an experiment began or a cooldown kicked in, the earlier "OK" is stale. Re-evaluating means approval can never bypass the safety rules.

## FILE app/remediation/routes.py | Remediation endpoints | code
### What it is
| URL | Role | Purpose |
|---|---|---|
| `POST /api/actions` | operator | Propose an action; default `dry_run` records the verdict and runs nothing |
| `POST /api/actions/from-analysis` | operator | Turn an AI recommendation into a request — **only** from an `accepted` (validated) analysis, only for services the evidence supports, and never for an "advice only" (`NONE`) item |
| `GET /api/actions`, `/{id}` | anyone | Read |
| `POST /api/actions/{id}/approve` and `/reject` | **admin** | Decide risky actions |
| `GET /api/audit-log` | operator | The audit trail |
| `GET /api/remediation/recommendations` | anyone | Suggestions from live predictions, each annotated with its policy verdict — suggestions only |

### Key parts
@snippet 62-67 | Default mode is dry run
@snippet 70-93 | AI → policy: the model only names an allowlisted action
@snippet 107-114 | Only admins approve or reject

# AREA twin | 13. The digital twin | A small simulator of "what if?" — always labelled as a simulation
The **digital twin** answers questions like: *"What would happen to users if Postgres were slow?"* and *"Would adding a queue between the gateway and Java help?"* — **without** running an experiment.

It is a deliberately simple model: each dependency edge has a **coupling** (0–1): how much of a callee's damage reaches its caller. The twin learns couplings from your measured signatures. Where nothing was measured, it **assumes a hard dependency (coupling 1.0) and says so** in an `assumptions` list.

Every output is tagged `measured` (aggregated from real runs) or `simulated`. A simulated architecture change is a *hypothesis to test with a real experiment*, never a result.

## FILE app/twin/model.py | The twin: learned couplings, simulation, what-if, self-validation | code
### What it is
`TwinModel(edges, signatures)` — pure Python.

### How it works
- **Measured facts** (`measured`): for a (target, fault type), the median end-user impact, per-service impact, recovery time and a confidence from the number of runs.
- **Learning couplings** (`_learn_coupling`): for each edge (caller → callee) and **fault class** (`outage`, `latency`, `degradation`, `errors`), coupling = caller impact ÷ callee impact, taken only from experiments where the callee really was degraded. A callee that wasn't degraded says nothing about coupling.
- **Looking one up** (`coupling`): the exact class if measured; else the same edge under another class (`measured_other_class`); else `assumed_hard` (1.0).
- **Simulation** (`simulate`): start with the target at 100 % impact and propagate up the graph until stable: `caller impact = callee impact × coupling × mutation factor`.
- **Mutations** (what-if architecture changes):
  - `queue` — the caller hands work to a queue: factor 0 for that edge (work is deferred, not failed).
  - `fallback` — a cache serves `hit_ratio` of calls: factor `1 − hit_ratio`.
  - `replicas` — N replicas: impact `100/N` for an *abrupt* failure. A real Kubernetes test measured **0 %** with 2 replicas, so the text now says this is a **pessimistic bound**.
- **Validation** (`validate`): *leave-one-combination-out* — hide every run of a (target, fault), predict its end-user impact from the structure and the remaining experiments, compare with what was measured.

### Key parts
@snippet 14-19 | Fault classes and the "affected" threshold
@snippet 63-84 | Learning coupling from measurements
@snippet 86-94 | Measured, measured-other-class, or assumed
@snippet 106-126 | The three architecture mutations
@snippet 128-143 | Propagate impact up the dependency chain
@snippet 174-206 | Validation against real measurements

??What does the twin do for an edge it has never seen fail? || It assumes the worst — a *hard* dependency (the caller inherits the callee's full impact) — and lists that under `assumptions` so you know which parts to verify with a real experiment.

> Measured on this repo: 86 % correct "affected or not", mean error 24 points. It **cannot know** Kafka is a soft dependency from structure alone, because it assumes hard dependencies until it sees otherwise.

## FILE app/twin/routes.py | Twin endpoints | code
### What it is
| URL | Purpose |
|---|---|
| `GET /api/twin/what-if?target=…&fault_type=…` | Measured results (if any) side by side with the simulation |
| `POST /api/twin/architecture` | Baseline vs **modified** architecture under the same fault (the modified figure is a simulation, with a disclaimer) |
| `GET /api/twin/validation` | How accurate is the twin? |

Inputs are validated: services must exist, mutation types need their fields, and you can only change a dependency that really exists.

### Key parts
@snippet 28-40 | The mutation request shape
@snippet 52-71 | Validate the mutations, then compare
@snippet 74-78 | Validation endpoint
