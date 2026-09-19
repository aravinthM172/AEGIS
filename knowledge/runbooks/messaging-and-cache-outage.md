# Runbook: message broker or cache outage

## Symptoms
- If the broker or cache is not on the synchronous request path, user-facing requests keep succeeding; measured end-user impact stays near zero.
- Asynchronous work stops: telemetry events, incident events and live state updates are delayed or lost. Services that emit events without buffering drop them while the broker is down; services that buffer send them after recovery.
- Observability has a blind spot: the metrics used to detect problems may travel through the failing broker, so the outage can hide itself.

## Diagnosis
1. Confirm whether the component is on the request path. Measure end-user latency and errors from a client-side probe that does not depend on the broker.
2. Check consumer lag and producer errors when the broker returns.
3. Note which services lost telemetry during the outage (services with traffic before the fault but no events during it).

## Mitigation
- Restart the broker or cache (RESTART_SERVICE) and verify consumers catch up.
- After recovery, check for lost or duplicated events; consumers that commit offsets after processing give at-least-once delivery.

## Prevention
- Keep a client-side (black-box) probe so user impact is visible even when telemetry is not.
- Buffer events locally with a bounded queue, or use an outbox, so short broker outages do not lose data.
- Do not put a broker or cache on the synchronous request path unless it is required.
