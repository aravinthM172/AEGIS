# Job Tracker as a FaultScope target

An **isolated test copy** of the Job Application Tracker (`github.com/aravinthM172/Job-Tracker`, private) run beside
FaultScope. It is **not** the live site: it has an empty SQLite database, no `.env`, no Outlook/Gmail tokens, and its
own container (`aegis-job-tracker`, `127.0.0.1:8100`). The Job Tracker source is used unmodified; the wrapper
(`faultscope_entry.py`) only adds FaultScope's telemetry middleware and the HTTP-error fault hook.

```bash
docker build -t job-tracker-base:local ../Job-Tracker                       # from the Aegis parent dir
docker compose -f targets/job-tracker/docker-compose.yml up -d --build     # from the Aegis root (main stack must be up)
```

Experiments use `config/workloads.json`: for target `job-tracker` the control plane sends its own synthetic user
traffic (`GET /jobs`, `/dashboard`, `/health`, `POST /jobs`) so user impact is measured from the outside, even when
the app is down and cannot report anything itself.

## Measured (5 requests/s, real fault injection on the test copy, 1 run each unless stated)

| Fault | What users experienced (measured from outside) | Notes |
|---|---|---|
| Stop the container (20 s) | **100 %** of requests failed (83/83 in the fault window); restart took 5.3 s | The app's own telemetry is empty while it is down: `insufficient_data` for the service, only the outside observer sees it |
| 300 ms network delay (2 runs) | user p95 125 ms → 611 ms (4.9×), 0 errors; second run 130 → 1609 ms with 12 timeouts | The app's own latency stayed ~95 ms: **invisible from inside**. A first run had a 410 ms baseline p95 of unknown cause and was inconclusive |
| 50 % injected HTTP 503 | 53 % of user requests failed | Confirms the hook and measurement agree; not a finding about the app |
| CPU limited to 0.1 core + 2 stress workers | **100 %** of requests degraded, 127 timed out | The app is very CPU-sensitive; the stressor shares the container so this is a harsh scenario |
| Control (no fault) | 0 % impact, p95 ratio 0.9 | Normal variation |

## Findings about the app itself

- `GET /jobs/summary` always answers 422: `@app.get("/jobs/{job_id}")` is declared before it in `backend/main.py`, so
  `summary` is parsed as an integer id. The frontend does not call it (it uses `/api/live-jobs/summary`), so the impact
  is low; fix by declaring `/jobs/summary` first.
- The database is a SQLite file inside the same container, so there is no "database down" failure separate from the app.
  Its real external dependencies (Outlook/Gmail sync, job-board scraping) were not exercised: the copy has no credentials.

## Limits

Single runs, one host, a local test copy: the numbers describe this copy on this machine, not the live server at
`jobchicks.fyi`. No experiment was run against, and no load was sent to, the live site.
