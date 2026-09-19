# Commercial readiness review (first pass)

Status: an honest checklist, **not legal advice**. Items marked *verify* come from general knowledge and must be
checked against the current licence text before anyone relies on them. Written 2026-09-19.

## 1. Name

- Two web searches for "FaultScope" (chaos engineering / trademark) found no product or trademark with that name.
  That is **not** a trademark search: search engines miss registrations. Before using the name commercially, search
  the official databases (USPTO TESS/Trademark Search, EUIPO, and the national office where you will sell) and check
  domains and package names (PyPI, npm, GitHub, Docker Hub).
- The repository directory and some code and docs still say "Aegis" (the earlier project). Decide on one name and
  clean up the leftovers before publishing anything.

## 2. Third-party licences (what would be shipped or depended on)

| Component | Where used | Licence (from memory: *verify*) | Note |
|---|---|---|---|
| FastAPI, Starlette, Pydantic, SQLAlchemy, httpx, redis-py, uvicorn | control plane | MIT / BSD | permissive |
| psycopg 3 | control plane DB driver | LGPL-3.0 | fine if used unmodified as a library; keep the licence notice |
| kafka-python-ng, docker SDK | Python | Apache-2.0 | keep NOTICE text |
| Spring Boot, Spring Kafka, Hibernate | java-service | Apache-2.0 (Hibernate: LGPL-2.1 *verify*) | |
| librdkafka | cpp-service | BSD-2-Clause | |
| Next.js, React, TypeScript | dashboard | MIT / Apache-2.0 | |
| PostgreSQL | database | PostgreSQL licence (permissive) | |
| Apache Kafka | broker | Apache-2.0 | |
| **Redis** (`redis:7-alpine`) | cache | BSD up to 7.2; **later versions changed to source-available or AGPL options** | the floating `7` tag may pull a newer licence: pin a version or consider Valkey (BSD) |
| **MinIO** | legacy service in `docker-compose.yml` | **AGPL-3.0** | unused leftover: **remove before distributing** |
| Ollama | local model runner | MIT | |
| **Llama 3.2 model** | AI analyst default | Llama Community Licence: commercial use allowed with conditions (attribution, usage policy, very large-user threshold) | read it; or switch to a permissively licensed model |
| nomic-embed-text | embeddings | Apache-2.0 | |

Before shipping: generate a real bill of materials (`pip-licenses`, `mvn license:add-third-party`,
`npx license-checker`) instead of relying on this table, and choose **your own** licence for your code (this repository
has no LICENSE file yet, which means "all rights reserved" by default).

## 3. Security gaps to close before any customer runs it

| Gap | Where | Why it matters | Fix |
|---|---|---|---|
| Dev secrets committed as defaults (`dev-operator-key`, `dev-admin-key`, `dev-token-change-me`, database and MinIO passwords) | `docker-compose.yml`, `k8s/*.yaml`, `.env.example` | anyone who reads the repo knows the keys | require secrets from the environment; refuse to start on defaults outside `FAULTSCOPE_ENV=local` |
| Read endpoints have no login | control plane | telemetry, experiment results and AI evidence are visible to anyone who can reach port 8000 | put authentication in front of every route, not only state-changing ones |
| Infrastructure ports published on all host interfaces (Postgres 5432, Redis 6379, Kafka 9092, services) | `docker-compose.yml` | reachable from the network | bind to `127.0.0.1` or drop the published ports |
| Fault-agent holds the Docker socket | `fault-agent` | Docker access is effectively root on the host | keep it local-only (already `127.0.0.1`); never expose it; document this prominently; consider a rootless or Kubernetes-native injector for customers |
| No TLS, no per-user identity, no tenant separation | everywhere | a shared or hosted deployment would mix customers' data | add TLS, real user accounts, per-tenant isolation before any hosted offering |
| Kafka and Redis have no authentication | infra | anything on the network can inject fake telemetry | enable auth/ACLs when leaving a single trusted machine |
| Real fault injection is Docker-only | `k8s/README.md` | most paying users run Kubernetes or cloud VMs | Kubernetes injector (pod delete, network policy, resource limits) |

## 4. Product-evidence gaps (what a buyer will ask)

- Only one demo workload plus one test copy of another app has been measured; most scenarios have 3-5 runs.
- Prediction accuracy is labelled optimistic (rules were tuned on the same data); a fresh set of experiments is needed.
- The local 3B model's explanations are mediocre; grounding stops fabrication but not weak reasoning.
- No customer has run it. **Validating demand with 10 real conversations comes before more engineering.**

## 5. Cheapest next actions (any business goal)

1. Pick a name after a proper trademark search; pick a licence for your code.
2. Remove the unused MinIO/Spark services and the unused `legacy/` and `llm-service/` directories from anything you
   distribute.
3. Record a 3-minute demo of the dashboard running an experiment, plus a written case study (the Job Tracker results
   in `targets/job-tracker/README.md` are a good start).
4. Talk to potential users before building features.

## 6. Progress on the security gaps (2026-09-19)

| Gap | Status |
|---|---|
| Infrastructure ports on all interfaces | **Done**: every published port is bound to `127.0.0.1` (`docker-compose.yml`) |
| MinIO (AGPL) and Spark leftovers | **Done**: removed from `docker-compose.yml` (the `legacy/`, `spark-job/`, `llm-service/` directories are still in the repository) |
| Read endpoints have no login | **Available, off by default**: `FAULTSCOPE_REQUIRE_AUTH_FOR_READS=1` (`app/security.py`, tested live) |
| Dev secrets as defaults | **Partly**: startup warns outside local mode; `FAULTSCOPE_STRICT_SECURITY=1` refuses to start. The defaults themselves are still in `docker-compose.yml`, `k8s/*.yaml` and `.env.example` |
| Kafka/Redis without authentication, TLS, per-user identity, tenant separation | Open |
| Kubernetes fault injector | Open |
