# FaultScope — Distributed-System Failure Intelligence & Resilience Platform

> See how systems fail. Predict what breaks next. Make them resilient.

FaultScope deliberately runs **controlled failure experiments** against a small distributed system, measures how
failures propagate (blast radius), turns each experiment into a reusable **failure signature**, builds a **resilience
model** from those measurements, and uses it for **early warning**, **evidence-grounded AI analysis**,
**architecture what-ifs** and **policy-controlled remediation with verified recovery**. Self-healing is the last layer, not the identity.

Every number in the UI and API is either **measured** (from a real experiment) or explicitly labelled **simulated** / **assumed**.

## Architecture

```
            Control Center (Next.js)  :3000
                       │
                Control plane (FastAPI)  :8000  ──────────────► Ollama (host, local LLM + embeddings)
   experiments · blast radius · signatures · resilience · prediction · twin · AI · remediation
        │                     │                         │
   Postgres + Redis      Kafka (telemetry)        fault-agent :8095   ◄── the ONLY holder of the Docker socket
                              ▲                    allowlist · TTL dead-man's switch · revert-on-startup
   monitored workload:        │
   client → gateway ─► java-service ─► cpp-service        (+ postgres)     every service emits structured telemetry
            (Python)    (Spring Boot)    (C++)                              with propagated trace ids
```

Kubernetes manifests for the workload live in [`k8s/`](k8s/README.md); a cost-aware cloud plan (not executed) is in
[`docs/cloud-plan.md`](docs/cloud-plan.md).

## Run it

```powershell
docker compose up -d --build          # stack (Docker Desktop)
# Control Center: http://localhost:3000     API: http://localhost:8000/docs
# paste an API key in the UI sidebar: dev-operator-key (operator) or dev-admin-key (admin)
ollama serve                          # optional: real LLM analysis (llama3.2:3b, nomic-embed-text)
python tools/loadgen.py --rps 4 --seconds 60   # steady traffic
POST /api/campaigns {"suite":"standard"}       # 8 scenarios x repetitions, builds the resilience model
```

Tests: `python -m pytest tests -q` (210), and the fault agent: `fault-agent/tests` (32). Run both under Python 3.12,
which is what the containers use.

## What was measured (real runs on this repository)

| Fact | Result |
|---|---|
| Stop `cpp-service` under load | `java-service` direct + `gateway` indirect, 100 % end-user impact, recovery ≈ 3.4 s (4 runs) |
| 300 ms latency on Postgres | p95 ≈ 13 ms → ≈ 1.2 s on `java-service` and `gateway` (4× the delay: several round trips), **zero errors**, 100 % degraded |
| CPU starvation of `cpp-service` | invisible with cheap requests, ~40× p95 with CPU-heavy ones: impact depends on request cost |
| Stop Kafka / freeze Redis | **0 %** end-user impact (not on the request path), but telemetry has a blind spot when Kafka is the target |
| Noise floor (4 control runs) | up to 8.3 % normal variation: impacts near that are not distinguishable from noise |
| Early-warning backtest (leave-one-out, 36 experiments) | first version 77 % detection / 36 % correct attribution; after tuning **96 % / 82 %**, false alarms 2/105 — **optimistic**, tuned on the same data |
| Real incidents (`docker stop` / `docker pause`, not experiments) | detected in 2.3 s / 5.4 s; **first warning misattributed both times**; operator restart verified by real probes, recovery 5.7 s, 434/440 user requests succeeded |
| Digital twin, leave-one-combination-out | 86 % affected-or-not, 24-point mean error; cannot know Kafka is a soft dependency from structure alone |
| Kubernetes (kind) | kill a pod: 14.3 % of requests fail with 1 replica, 0 % with 2; a bad image rolls back with 0 failed requests |

## Design rules that shaped it

- **Never fake results.** Insufficient data is reported as such; simulated ≠ measured; assumptions are listed.
- **Deterministic first.** Anomaly detection, blast radius, resilience scores and the twin are plain code. The LLM only explains.
- **Don't trust the LLM.** Its output must cite evidence that exists, name only measured-affected services, quote only numbers present in the evidence, and recommend only actions that address the fault type; otherwise it is rejected and not shown as fact. It never produces commands.
- **Safety in depth for faults.** The control plane cannot touch Docker; the agent enforces a label allowlist, reverts every fault after a TTL even if the control plane dies, persists intent before acting and reverts everything on restart, and verifies both application and rollback.
- **Verified recovery.** "Recovered" is only claimed from real end-to-end probes against the entry point.

## Known limits

- One workload, one host, small samples; accuracy elsewhere is unknown. Repetitions per scenario are 3–5.
- The local 3B model's explanations are mediocre (e.g. it mislabelled a ratio as a percentage); grounding prevents fabrication, not weak reasoning. Larger models are a config change (`OLLAMA_MODEL`).
- Early warning is unreliable in the first ~8 s of an incident (silence needs a full window), and Kafka outages are misattributed.
- Kubernetes: fault injection supports only stop (scale to zero) and restart; no executor for remediation; `ROLLBACK_DEPLOYMENT` and `ENABLE_FALLBACK` are `unsupported` in the policy engine.
- No cloud deployment. Auth is API-key roles on state-changing endpoints only; reads are open. Dev keys are in `docker-compose.yml`.
- Legacy `llm-service/`, `legacy/` and the `kafka-consumer` compose service are unused leftovers from the earlier "Aegis" project.
