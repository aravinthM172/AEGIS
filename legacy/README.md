# legacy/

Quarantined code from the pre-FaultScope Aegis repo. **Not imported by any running
service.** Kept for reference; safe to delete once nothing here is needed.

## netwatch/

An earlier URL-uptime-monitoring app that got mixed into `app/`. These modules
reference SQLAlchemy models (`MonitorResult`, `Target`, `Service`) and helper
functions that no longer exist, so they do not run as-is. `main.py` never wired
them in.

Possibly worth salvaging later:

- `services/monitoring.py` — a clean async circuit-breaker + retry/backoff client.
  Useful reference for FaultScope's resilience / fallback work.
- `core/config.py` — pydantic-settings `Settings` pattern for the control plane.
