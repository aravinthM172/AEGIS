# LESSON why | What is FaultScope, and why break things on purpose? | 6
Imagine you run a website. One night a small part breaks — say the database gets slow. Do you know **which pages will fail, how badly, and for how long**? Most teams don't, until it happens for real.

**FaultScope answers that before it happens.** It deliberately breaks a small demo system in controlled ways (this is called *chaos engineering*), measures exactly what got worse, and remembers it. From those measurements it can:

1. **Measure** how far a failure spreads (*blast radius*).
2. **Score** how resilient each part is.
3. **Predict** "this looks like the failure we saw last Tuesday".
4. **Explain** what happened in plain words (with an AI that isn't allowed to make things up).
5. **Fix** some problems automatically — but only with safety checks, and only "fixed" once real requests prove it.

The project's motto: *See how systems fail. Predict what breaks next. Make them resilient.*

> Careful: this is a learning-and-engineering project running on **one laptop** with a **small demo system**. Every number it reports comes from a real experiment here — and its limits are written down too.

### The rules the whole project follows
- **Never fake a result.** If there isn't enough data, it says "insufficient data".
- **Plain code first, AI second.** The AI only explains; it never measures or decides alone.
- **The AI never gets a command line.** It can only *name* an approved action.
- **Only approved things can be broken or fixed.** Everything is allow-listed.
- **Prove recovery** with real requests instead of assuming.

??Why break a system on purpose instead of waiting for a real failure? || A real failure happens at a bad time, without measurements, and without a safe way to undo it. A planned experiment is measured, limited in time, reversible, and repeatable.
??What does "blast radius" mean? || The set of things damaged by one failure — how far the damage spreads.

# LESSON parts | The big picture: every box and what it does | 8
Click any box in the diagram to see what it is and to jump to its code.

@diagram architecture

Think of FaultScope as **four groups**:

| Group | Parts | Job |
|---|---|---|
| **Brain** | Control plane (FastAPI) + Postgres + Redis | Runs experiments, does all the maths, stores everything |
| **Eyes** | Kafka + telemetry-consumer | Collect a small "what happened" record from every request |
| **Hands** | fault-agent + Docker | The *only* part allowed to break containers, with strict rules |
| **Subject** | gateway → java-service → cpp-service (+ your job-tracker copy) | The system that gets broken and measured |

Plus two helpers: the **Control Center** (the web dashboard you look at) and **Ollama** (a language model running on your PC that can explain results).

### Why so many pieces?
- **Safety by separation.** The brain can't touch Docker; only the small hands-program can. The subject is separate from the brain, so breaking the subject never breaks the thing measuring it.
- **Realism.** Three languages (Python, Java, C++), a database, a message queue and a cache is a small but *real* microservice system.

### What is Docker doing here?
Docker runs each part in its own **container** (a light, isolated box). `docker compose up` (see [[docker-compose.yml]]) starts them all and connects them on a private network where they find each other by name, like `postgres` or `kafka`.

??Which single component may control Docker, and why? || The fault-agent. Docker control is powerful (close to root access), so it's isolated in one small program with an allowlist, time limits and crash recovery — and the AI and the big control plane never get it.
??What's the difference between the control plane and the subject system? || The control plane is the measuring/thinking part; the subject (gateway, Java, C++) is what gets broken. They're separate on purpose.

# LESSON journey | Follow one request through the system | 7
The best way to understand a system is to follow a single request from start to finish. Use **Next step →** to walk through it.

@diagram journey

Key ideas you just saw:
- **Trace ids**: the gateway labels each request and passes the label along (`X-Request-ID`, `X-Trace-Id`). Later, everything belonging to one user request can be found by that label.
- **Timeouts**: each hop only waits so long (1 s to connect; 5 s at the gateway, 2 s at C++). Without timeouts one stuck service would freeze everyone above it.
- **Telemetry is fire-and-forget**: services drop small events into Kafka and move on. If Kafka is broken, users are **not** affected — but the measurements get a blind spot.

### Read the real code for each hop
1. [[gateway/main.py]] — forwards the request
2. [[java-service/src/main/java/com/aegis/service/jobs/JobController.java]] — calls C++ then saves
3. [[cpp-service/src/main.cpp]] — counts primes (real CPU work)
4. [[app/telemetry_consumer.py]] — stores the events

??If cpp-service is stopped, which services will users see errors from? || All of them, in a chain: java-service fails first (it can't reach C++ → 503), then the gateway (java returned an error → 502/503). That is "propagation".
??Why is telemetry sent to Kafka instead of directly to the database? || So reporting never slows a request, and if the database is briefly down Kafka holds the events until the consumer catches up.

# LESSON telemetry | Telemetry: how every service reports what happened | 7
**Telemetry** = a small record a program writes about itself. Every request produces one JSON event like:

```
{ "service": "java-service", "event_type": "http_request",
  "latency_ms": 42.1, "status_code": 200, "trace_id": "…",
  "metadata": {"path": "/jobs", "method": "POST"} }
```

There are two event types:
- `http_request` — "someone called me, and here's how it went".
- `dependency_call` — "I called someone else, and here's how it went". These let FaultScope *discover* which service depends on which.

### One format, three languages
The same JSON is produced by Python ([[app/telemetry.py]] and [[app/telemetry_middleware.py]]), Java ([[java-service/src/main/java/com/aegis/service/telemetry/TelemetryPublisher.java]] and [[java-service/src/main/java/com/aegis/service/telemetry/TelemetryFilter.java]]) and C++ ([[cpp-service/src/telemetry.hpp]]). That's why any service can be added as long as it speaks this format — which is how your Job Tracker was connected with a 12-line wrapper.

### The pipeline
`service → Kafka → telemetry-consumer → Postgres (history) + Redis (latest state)`

The consumer is careful ([[app/telemetry_consumer.py]]):
- **At-least-once**: it commits its Kafka position only *after* storing, so a crash never loses events…
- …and **de-duplicates** by event id, so a repeat is harmless.
- It skips malformed events instead of getting stuck on them.

### The honest blind spot
A service reports about *itself*. A service that is **completely down reports nothing** — so its own telemetry shows a *gap*, not errors. That's why experiments also use **synthetic users** measuring from the outside. (This exact gap was found while testing your Job Tracker.)

??Why does the consumer commit offsets after storing instead of before? || A crash between reading and storing would lose the event if the position were saved first. Saving after means the worst case is doing the work twice, which de-duplication makes harmless.
??Why can't a dead service tell you it's dead? || Telemetry comes from inside the service. If the process is stopped, nothing emits. You need an outside observer.

# LESSON agent | The fault-agent: breaking things safely | 8
This is the most safety-critical part. It's the one program allowed to touch Docker, and it follows five rules:

1. **Allowlist** — only containers labelled `faultscope.injectable=true`.
2. **Time limit** — every fault has a TTL and the agent undoes it *itself* (a "dead-man's switch").
3. **Write intent first** — the fault is saved to disk *before* being applied; after a crash the agent reads it and repairs everything.
4. **Verify** — after applying, it checks the effect is real; after undoing, it checks the system is really back.
5. **One at a time** per container.

### The seven faults
| Fault | Real-world story |
|---|---|
| `stop_container` | The service crashed |
| `pause_container` | The service froze (alive but not answering) |
| `restart_container` | A crash and quick recovery |
| `latency` | The network got slow |
| `cpu_stress` | The machine is overloaded |
| `memory_stress` | Memory is being eaten |
| `http_error` | The service returns errors for some requests |

### Real bugs the safety rules caught
- **CPU rollback lied**: `docker update --cpu-quota 0` means "unchanged", not "remove the limit". Rollback *reported success* while the CPU cap stayed. Now it uses `-1` and **checks the result**.
- The memory-hold trick behaved differently on two Linux flavours, so a capability check chooses the technique first.

Read the real thing: [[fault-agent/agent/core.py]] (start with `apply` and `rollback`), then [[fault-agent/tests/test_core.py]] to see every promise tested.

??Why does the agent save a fault to disk before applying it? || If the agent or PC crashes in the middle, there's still a record that something may be broken, and the agent repairs it on restart.
??What happens if the control plane crashes while a fault is active? || The agent's timer fires at the fault's TTL and reverts it automatically — no control plane needed.

# LESSON experiment | Running an experiment | 8
An experiment follows a strict path. Step through it:

@diagram lifecycle

### The guardrails ([[app/experiments/engine.py]])
- **Only one experiment at a time**, enforced by the *database itself* (a unique index) so two simultaneous clicks can't both win.
- **Rollback always runs** once injection was attempted — after success, abort, error or crash.
- If rollback fails → `ROLLBACK_FAILED`, which **blocks new experiments**: the fault may still be active.
- The measuring tools (`control-plane`, `telemetry-consumer`) are protected targets.
- You can press **abort** at any time; the engine wakes instantly and rolls back.

### Synthetic users
With `workload_rps` set, a fake user sends requests during the whole experiment ([[app/experiments/workload.py]]). They measure from the outside, so damage is visible even when a service can't report.

### Control experiments
A **dry run** applies nothing but measures normal behaviour under the same load. Without it you can't tell "the fault caused this" from "this is just how noisy the system is".

### Try it yourself
With the stack running, open the **Experiments** page of the dashboard (`http://localhost:3000/experiments`), paste `dev-operator-key` into the API-key box in the sidebar, and fill the form: target `cpp-service`, fault `stop_container`, a 15-second duration, workload 4 requests/s, and switch **dry run** off. Launch it and watch the phases change. Repeat with dry run **on** to see the "control" measurement.

??Why is there a "control" (dry-run) experiment? || To measure the noise floor: how much the numbers move when nothing is broken. An "impact" smaller than that noise can't be trusted.
??What should happen to the experiment slot if rollback fails? || It stays occupied (`ROLLBACK_FAILED`) so nobody starts another experiment on top of a possibly still-broken system.

# LESSON blast | Measuring the damage: blast radius | 9
After the experiment, [[app/analysis/blast_radius.py]] compares three time windows:

```
 baseline            fault window              recovery
|──────────────|─┬──────────────────────|──────────────────────|
 healthy         │ fault applied        │ fault removed
 (reference)     └ (excluded: half-applied)  (how long to heal?)
```

### What counts as "degraded"?
A request is degraded if it **failed** or was **slow** — slower than `max(2 × baseline p95, baseline p95 + 50 ms)`. (p95 = the latency 95 % of requests beat.) Counting slowness matters: a network delay causes **zero errors** yet every request is worse.

`impact % = degraded % during the fault − degraded % in the baseline`. At 10 % or more, the service is **affected**. With fewer than 8 requests in a window → **insufficient data** (never guessed).

### Who was hurt, and how?
- **target** — the service we broke (its own damage isn't "propagation").
- **direct** — calls the target (1 hop away).
- **indirect** — 2 or more hops away.
- **unexplained** — hurt, but the dependency map didn't predict it (the map is missing something).
- **client** — the synthetic user; this is "what real users felt".

### Measured on this project
- Stopping `cpp-service`: java-service (direct) + gateway (indirect) hurt, **100 % end-user impact**, recovery ≈ 3.4 s.
- 300 ms latency on Postgres: p95 went ~13 ms → ~1.2 s (4× the delay, because one request makes several round trips), **zero errors**.
- Stopping Kafka: **0 %** user impact — but the telemetry itself gets a blind spot.

??Why can a 300 ms delay turn into over a second per request? || One request makes several sequential round trips to the database (connection handshake plus several statements); each one pays the delay.
??Why does a window need at least 8 requests? || With fewer, a single odd request swings the percentages wildly. Better "insufficient data" than a confident number from 3 samples.

# LESSON knowledge | From measurements to knowledge: signatures and resilience | 8
### Failure signatures ([[app/analysis/signatures.py]])
Each experiment is condensed into a **signature**: *what was broken* + *what happened* (which services, errors vs slowness, silent or not, propagation path, recovery). Same shape → same fingerprint. Real runs give **fault** signatures; dry runs give **control** signatures.

### The resilience model ([[app/analysis/resilience.py]])
For each service and each dependency failure:

`resilience score = 100 − median measured impact %`   (HIGH ≥ 80, MEDIUM ≥ 40, LOW < 40)

- **Worst case** is what's reported, not the average: resilience is about the bad day.
- **Confidence** grows with repeated runs: low < 3, medium 3–4, high ≥ 5.
- Results measured under **different load or parameters are never averaged**.
- Combinations nobody has tried are listed as **untested**, never scored.

### The noise floor
Control runs show how much impact appears with *no* fault. In this project that reached up to **8.3 %**, so an impact near that can't be told apart from normal variation. Good measurement includes admitting what you can't measure.

### Campaigns
One run is an anecdote. A **campaign** ([[app/experiments/campaign.py]]) runs a whole plan (8 scenarios × 3 repeats in [[config/campaigns/standard.json]]) with a cooldown between runs, **interleaved** so slow drift affects every scenario equally.

??Why score the worst case rather than the average? || Averages hide the one failure that breaks everything. The worst case shows where the system is fragile.
??What does "untested dependency" mean in a resilience profile? || A dependency this service relies on that no experiment has ever broken — so its resilience to that failure is unknown, and it is reported as such instead of scored.

# LESSON predict | Predicting what breaks next | 7
Prediction is **not machine learning**. It's transparent pattern matching ([[app/prediction/core.py]]):

1. Judge each service's last few seconds against its own recent normal → `normal`, `anomalous`, `silent`, or `insufficient_data`.
2. Compare the anomalous set with every stored signature ("when postgres was slow, java-service and gateway got slower").
3. If it resembles one closely (similarity ≥ 0.6), warn: *"this looks like latency on postgres; still at risk: …; last time end-user impact was X %, recovery Y s."*

### The subtle rule
**Silence only counts while others have traffic.** A stopped service emits nothing — suspicious if users are sending requests, meaningless at 3 a.m. with zero traffic.

### How good is it? (measured, not claimed)
The **backtest** ([[app/prediction/backtest.py]]) replays each stored experiment second by second, showing the predictor only earlier data and *other* experiments' signatures.
- First version: 77 % detected, only **36 % correctly attributed**.
- After tuning: **96 % / 82 %**, false alarms 2 of 105 — but **optimistic**, because it was tuned on the same data.
- On real incidents (a real `docker stop` / `docker pause`, not experiments): detected in 2.3 s and 5.4 s, yet the **first warning was misattributed both times**.

The lesson: publish the failures, not just the good numbers.

??Why is the backtest labelled "optimistic"? || The predictor's rules were adjusted after looking at an earlier backtest of the same data, so scores on that data are flattering. A fresh set of experiments would be needed for an unbiased estimate.
??What does "silent" mean and when does it count as evidence? || A service that normally has traffic but now emits nothing. It counts only when other services still receive traffic; if nothing is being requested anywhere, silence is just an idle system.

# LESSON ai | The AI analyst that isn't allowed to lie | 9
Large language models can sound confident while being wrong. FaultScope uses one (a small **local** model via Ollama — no cloud, no API key, no cost) only to **explain**, and wraps it in layers of distrust ([[app/ai/analyzer.py]], [[app/ai/schema.py]]):

1. **It only sees evidence** built by code: measured numbers, retrieved runbooks and past experiment reports.
2. **Retrieval (RAG)**: first search the library — BM25 keyword search plus optional embeddings, merged — then answer from what was found ([[app/ai/retrieval.py]]).
3. **Structured answer**: JSON with root cause, findings that each *cite* evidence, and recommended actions.
4. **The grounding validator** rejects the answer if it: cites a reference that doesn't exist; names a service that wasn't measured as affected; omits an affected service; has no measured citation; quotes a **number not in the evidence**; or recommends an action that doesn't address that fault.
5. **One retry** with the exact reasons; otherwise the answer is **rejected** and never shown as fact.
6. If the model is down you still get the code-written summary (`llm_unavailable`).

### What it can't do
It never sees a shell, Docker or the database, and **never produces a command** — at most it names one of five approved actions, which then goes through the policy engine.

### Honest limits
The 3-billion-parameter local model's explanations are *mediocre* (it once labelled a ratio as a percentage). Grounding prevents fabrication, not weak reasoning. A bigger model is one setting away (`OLLAMA_MODEL`).

??A model claims "p95 rose to 900 ms" but the evidence says 240 ms. What happens? || The validator finds a number that doesn't appear in the evidence and rejects the answer with that reason; the model gets one retry, and if it fails again the answer is shown as rejected.
??Why is the deterministic summary always shown first? || It's produced by ordinary code from the measurements, so it's guaranteed consistent with the data. The model's text is an extra hypothesis, clearly labelled.

# LESSON remediate | Fixing things safely: controlled remediation | 8
Automation that changes a live system is risky, so the pipeline has many gates ([[app/remediation/engine.py]]):

**request → policy → (admin approval if risky) → execute → verify with real requests → (next strategy)**

- **Allowlist of names**, never commands: `RESTART_SERVICE`, `SCALE_SERVICE` (implemented as lifting the CPU cap), `CLEAR_CACHE`, plus `ROLLBACK_DEPLOYMENT` and `ENABLE_FALLBACK` which are honestly `unsupported` here.
- **Policy** ([[app/remediation/policy.py]]): refuses protected targets, blocks actions during an experiment, allows at most 2 actions per target per 10 minutes (loop protection), and requires **admin approval** for restarting stateful services (database, broker, cache).
- **Approval re-checks the policy** — a stale "yes" can't bypass rules.
- **Verified recovery** ([[app/remediation/verification.py]]): at least 8 real probes through the gateway, ≥ 90 % succeed, latency back near normal. Otherwise it is `VERIFICATION_FAILED` — never assumed fixed.
- **Audit log**: append-only record of every decision.
- **Roles**: `viewer < operator < admin`; with no keys configured, protected endpoints are **disabled**, not open.

### A real test
A real `docker stop` (not an experiment) was detected in ~2 s; the operator restart was **verified by real probes** in 5.7 s with 434 of 440 user requests succeeding.

??Why is `dry_run` the default mode? || So proposing an action just records the policy verdict and changes nothing until someone deliberately asks to execute.
??Why re-run the policy when an admin approves? || Conditions change between request and approval (an experiment may have started). Re-checking makes sure approval can never override the safety rules.

# LESSON twin | What-if: the digital twin | 6
The twin ([[app/twin/model.py]]) answers "what if Postgres is slow?" and "what if we added a queue?" **without running an experiment**.

Each dependency edge has a **coupling** (0–1): how much of a callee's damage reaches its caller — *learned* from your measurements. If nothing was measured, it **assumes the worst** (a hard dependency) and lists that under `assumptions`.

Architecture changes it can simulate: **queue** (work deferred, not failed), **fallback** (cache serves some calls), **replicas** (N copies).

### Always labelled
Every number is tagged **measured** or **simulated**. A simulated change is a *hypothesis to test with a real experiment*.

### And it checks itself
*Leave-one-combination-out* validation hides all runs of one (target, fault), predicts them from the rest, and compares: **86 % right about "affected or not", 24-point mean error**. It can't know Kafka is a soft dependency from structure alone. On Kubernetes it also predicted 50 % impact for 2 replicas while the real measurement was **0 %** — so the text now calls that assumption a *pessimistic bound*.

??Why does the twin assume hard dependencies where nothing was measured? || It's the cautious default: better to overestimate the risk and flag it as an assumption than to silently assume a dependency is harmless.

# LESSON ui | The dashboard, Kubernetes, and your Job Tracker | 8
### The Control Center
A Next.js app ([[frontend/app/page.tsx]]) with 12 pages. It holds **no logic** about failures: `usePoll` ([[frontend/lib/hooks.ts]]) fetches the control plane's API every few seconds and draws it. Paste an API key in the sidebar to run experiments and actions.

### Kubernetes
The workload also runs on a local kind cluster ([[k8s/workload.yaml]]). Measured: killing the java-service Pod with **1 replica failed 14.3 % of requests; with 2 replicas, 0 %**; a bad deployment was contained and rolled back with 0 failed requests. Running there exposed a real bug (the control plane didn't retry its database setup).

### The cloud
[[docs/cloud-plan.md]] is only a **plan**: nothing was created, and it insists on never assuming AWS is free.

### Your Job Tracker
Your own app runs as an isolated **test copy** with a 12-line wrapper ([[targets/job-tracker/faultscope_entry.py]]) — your source code is unchanged and the live site was never touched. Measured: stopping it → 100 % of requests failed; 300 ms delay → ~5× slower with no errors; CPU starvation → total timeouts. It also found a real bug: `GET /jobs/summary` always returns 422 because of route order.

??Why was your live site never used for the experiments? || Breaking or load-testing a live service affects real users (and possibly real data). A local test copy gives the same insight safely.

# LESSON next | How to keep learning: a reading order and things to try | 6
### A good order to read the code
1. [[main.py]] — how everything is wired
2. [[app/telemetry_middleware.py]] then [[app/telemetry_consumer.py]] — where data comes from
3. [[fault-agent/agent/core.py]] — the safety rules
4. [[app/experiments/engine.py]] — the lifecycle
5. [[app/analysis/blast_radius.py]] — the measuring maths
6. [[app/ai/schema.py]] — the grounding validator
7. [[app/remediation/policy.py]] — the rulebook

### Try these (in order of difficulty)
1. Open `http://localhost:3000` and read the Dashboard.
2. Run a **dry-run** experiment from the Experiments page, then read its result and note the noise.
3. Run a real `stop_container` on `cpp-service` with the workload on; open the result and find *direct* and *indirect* services.
4. Ask the **AI Analyst** about it; compare its answer with the "Measured (no model)" line.
5. Open the **Twin**, simulate a `queue` between gateway and java-service, then think how you'd test it for real.
6. Run the tests: `python -m pytest tests -q`. Then break a rule on purpose (e.g. change `MIN_EVENTS`) and see which test fails.

### When something looks odd
- Check `docker ps` first: an old process on port 8000 can answer instead of the container.
- The dev keys and token in `docker-compose.yml` are for local use only — never expose these services with them.

??Which one change would you make first to make the predictor more trustworthy? || Collect a *fresh* set of experiments the predictor was never tuned on and re-run the backtest — the current numbers are flagged optimistic exactly because the rules were tuned on the same data.
