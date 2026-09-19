# Runbook: dependency hang (process alive but not responding)

## Symptoms
- Callers see timeouts (504) rather than refusals. Latency at the caller sits at exactly its read timeout (for example, a p95 equal to a 5 s client timeout).
- Every request to the hung service ties up a worker or connection for the whole timeout, so a small request rate can exhaust caller capacity.
- The hung service emits no telemetry while it is frozen.

## Diagnosis
1. A p95 that equals a configured timeout value points to a hang, not to slow processing.
2. Check whether the process is alive but not scheduled (frozen, paused, deadlocked, saturated CPU).
3. Compare with an outage: an outage fails fast with connection errors; a hang fails slowly with timeouts.

## Mitigation
- Restart the hung service (RESTART_SERVICE). A restart clears deadlocks and frozen processes.
- Lower the caller's read timeout temporarily so failures are cheaper.

## Prevention
- Use short, explicit timeouts and a circuit breaker on every remote call.
- Add health checks that exercise real work, not just a static response, so a hang is detected.
- Bound concurrency per dependency (bulkhead) so one hung dependency cannot consume all capacity.
