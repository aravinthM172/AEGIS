# AREA analysis | 8. Analysis: blast radius, signatures, resilience | Turn raw telemetry into measured facts about how failures spread
After an experiment ends, FaultScope answers three questions **using only measured data**:

1. **Blast radius** — which services got worse, by how much, and how did the damage travel?
2. **Failure signature** — a compact, reusable fingerprint of that experiment.
3. **Resilience model** — after many experiments: how well does each service survive each failing dependency?

The design rule is *never invent a number*. If there is not enough data the result says `insufficient_data`; a prediction from the dependency graph ("structural") is always kept separate from what was actually measured.

> **Blast radius** = the set of things damaged by one failure. If the C++ service dies and Java + the gateway fail too, the blast radius is three services.

## FILE app/analysis/blast_radius.py | The pure maths that measures damage | code
### What it is
A "pure" module: no database, no clock, no network. It takes lists of events in, returns a result dictionary out — which makes it easy to test exhaustively. This is where "how bad was it?" is computed.

### How it works — the three windows
Time is cut into windows using the experiment's timeline:

| Window | From → to | Meaning |
|---|---|---|
| **baseline** | baseline start → inject start | the healthy reference |
| **fault** | `fault_applied_at` → `rollback_started_at` | the fault is truly in effect |
| **recovery** | rollback start → finish | fault removed; how long until normal? |

The gap between "inject started" and "fault applied" is excluded from both baseline and fault: the fault is half-applied (e.g. Docker's stop grace period) so it is neither.

### How it works — what counts as "degraded"
An event is **degraded** if it **failed** (status ≥ 500, status 0, or no status) **or was slow**: slower than `max(2 × baseline p95, baseline p95 + 50 ms)`. (p95 = the latency that 95% of requests beat — a robust measure of "slow".) Counting slowness matters: a 300 ms delay produces **no errors at all**, yet every request is worse.

For each service: `impact = degraded % in fault window − degraded % in baseline`, floored at 0. Impact ≥ 10 % → `affected`, else `unaffected`. If either window has fewer than **8 requests** → `insufficient_data` (never guess).

### How it works — roles and propagation
- The injected service gets role `target` (its own damage isn't "propagation").
- The synthetic user gets role `client`.
- Affected services are labelled **direct** (they call the target: 1 hop), **indirect** (2+ hops) or **unexplained** (affected but not predicted by the graph).
- `_hops` walks the dependency graph *backwards* from the target (breadth-first search) to compute how far each service is.
- The graph combines **declared** edges (from config) and **observed** ones (from `dependency_call` telemetry). Each propagation edge is labelled with its evidence: `observed` or `declared`.
- `structural` compares prediction with reality: `at_risk_but_unaffected` (predicted but not hurt — a sign of resilience), `affected_but_not_predicted` (the graph missed something).
- **Recovery**: the time of the last degraded request after rollback. `recovered`, `not_recovered_in_window`, `unknown` (no traffic), or `nothing_to_recover`.

### Key parts
@snippet 21-25 | The thresholds that define everything
@snippet 28-37 | percentile: how p50/p95 are computed
@snippet 40-50 | What is an error? What is "too slow"?
@snippet 140-149 | The three windows
@snippet 157-178 | Per-service verdict: impact, insufficient data, p95 ratio
@snippet 191-198 | Direct, indirect or unexplained
@snippet 224-239 | Recovery time per service

??A fault adds 300 ms latency and nothing fails. Would blast radius say "unaffected"? || No. Slowness counts as degradation: requests slower than about 2× the baseline p95 are counted as degraded, so a latency fault shows as heavily affected even with zero errors. (That's the measured result for the 300 ms Postgres delay: 100 % degraded, 0 errors.)

??Why is 8 the minimum number of events? || With fewer, one odd request swings the percentages wildly. The rule trades coverage for honesty: better "insufficient data" than a confident-looking number built from 3 requests.

### Connects to
- [[app/analysis/service.py]] — loads the data this module consumes
- [[tests/test_blast_radius.py]] — its tests

## FILE app/analysis/service.py | Loads telemetry from Postgres, runs the maths, stores results | code
### What it is
The database-facing half. It gathers the inputs for [[app/analysis/blast_radius.py]], calls it, then saves the result, the signature and a knowledge-base document.

### How it works
1. `analyze_experiment` reads the experiment's timeline and turns timestamps into epoch seconds.
2. Two SQL queries load `http_request` events (one list per service) and `dependency_call` events between (baseline start − 1 s) and (finish + 1 s). Events from **protected services** (the measurement plane) are dropped: the control plane observes; it isn't part of what's measured.
3. Declared dependency edges are read from the database.
4. **Client samples**: if the experiment had a workload, its raw samples are added as a pseudo-service named `workload-client`. This is the "what users experienced" view, independent of Kafka.
5. `analyze_and_store` writes the result into the experiment row, adds an `analysis_completed` event, stores the failure signature, and adds the report to the AI knowledge base (best effort — it must never make the analysis fail).
6. `rebuild_all` re-analyses every finished experiment — useful after improving the maths.
7. `resilience_report` gathers all signatures and edges and builds the resilience model.

### Key parts
@snippet 25-36 | The two SQL queries
@snippet 71-83 | Time range and the protected-services filter
@snippet 85-90 | Add the synthetic user as `workload-client` and analyse
@snippet 97-108 | Store result, signature and knowledge

## FILE app/analysis/signatures.py | A reusable fingerprint of each experiment | code
### What it is
A **failure signature** summarises an experiment in a compact, comparable form: *what was broken* (`trigger`) and *what happened* (`observed`). Signatures are the currency of the rest of the platform: the resilience model, the predictor and the digital twin all learn from them.

### How it works
- Built **only** from a finished analysis (never from guesses).
- `kind` is `fault` for real runs and `control` for dry runs (which measure the noise floor).
- `degraded_mode(entry)` classifies each service's damage: `errors`, `latency`, `errors+latency`, `none` or `unknown`.
- A service is **silent** if it had traffic in the baseline but *nothing* in the fault window — down, frozen, or starved. Silence is itself evidence.
- The **fingerprint** is a short SHA-1 hash of the target, fault type, affected services + modes and propagation paths — two experiments with the same shape produce the same fingerprint.
- `end_user` records the client-side view (impact, errors, p95 ratio, mode).

### Key parts
@snippet 14-27 | Damage mode and the silence rule
@snippet 44-47 | The fingerprint
@snippet 69-89 | The signature layout: trigger + observed + fingerprint

## FILE app/analysis/models.py | The failure_signatures table | code
### What it is
One row per experiment (`experiment_id` is unique) with searchable columns — `kind`, `target`, `fault_type`, `fingerprint` — and the full signature as JSON.

### Key parts
@snippet 8-19 | The table

## FILE app/analysis/resilience.py | From many experiments to a resilience score | code
### What it is
The **resilience model**: for each service and each dependency failure, how much did it suffer, on average, across all measured runs?

### How it works
- **Score** = `100 − median measured impact %` (higher is better). **Level**: HIGH ≥ 80, MEDIUM ≥ 40, LOW < 40.
- **Confidence** depends on how many runs share the *same* conditions: `low` < 3, `medium` 3–4, `high` ≥ 5.
- `_datapoints` decides what may count. A service is scored against a fault on something it depends on (transitively) — or something that visibly affected it. Being unaffected by an unrelated service says nothing about resilience, so it is skipped.
- **Conditions matter**: results are grouped by fault parameters *and the workload intensity* (`workload_n`). Results measured under different load are never averaged.
- A service's profile: `worst_case_score`, `average_score`, its **critical dependency**, tested combinations, and `untested_dependencies` — an honest list of what nobody has tried yet.
- **Noise floor**: the maximum impact seen in *control* experiments. Impacts near it can't be told apart from normal variation.
- `rank_criticality` ranks failing components by measured end-user impact (worst case, then mean, then services affected).

### Key parts
@snippet 18-25 | Score levels and confidence
@snippet 35-62 | Which measurements are allowed to count
@snippet 65-80 | Aggregate one group: median impact, score, level
@snippet 129-136 | The noise floor from control experiments
@snippet 142-167 | Rank components by measured end-user impact

??Why score the *worst case* rather than the average? || Resilience is about the bad day. A service that survives four of five failures perfectly but collapses on the fifth is not resilient; the worst case exposes that, while the average would hide it.

## FILE app/analysis/routes.py | Resilience and signature endpoints | code
### What it is
Read-only URLs (plus one operator-only rebuild):

| URL | Returns |
|---|---|
| `GET /api/signatures` (+ filters) | Stored signatures |
| `GET /api/experiments/{id}/signature` | The signature of one experiment |
| `POST /api/signatures/rebuild` | **operator**: re-analyse everything |
| `GET /api/resilience` | All profiles, thresholds, noise floor, criticality ranking |
| `GET /api/resilience/critical` | Only the criticality ranking |
| `GET /api/resilience/{service}` | One service's profile |

Every response carries `"basis": "measured"`.

### Key parts
@snippet 48-57 | The resilience and criticality endpoints
