# Runbook: dependency outage (process stopped or crashed)

## Symptoms
- Callers of the dependency see connection errors or refused connections; upstream callers see 5xx (502/503).
- Failures propagate up the call chain: the direct caller fails first, then its callers.
- The failed service emits no telemetry of its own, so its own metrics show a gap, not errors.
- After the service is restarted, callers may keep failing for several seconds: clients and runtimes cache failed name lookups (the JVM caches failed DNS lookups for about 10 seconds by default), and container names disappear from DNS while a container is stopped.

## Diagnosis
1. Find the first service that reports connection errors to the target: that is the direct caller. Follow the call chain upward to see the blast radius.
2. Confirm the target is down (health check, container state).
3. Distinguish "refused quickly" (process gone) from "timed out" (process hung).

## Mitigation
- Restart the stopped service (allowed action: RESTART_SERVICE), then verify recovery with a health check and by watching the callers' error rate return to baseline.
- If callers keep failing after the target is healthy, suspect cached failed lookups or exhausted connection pools; give them time or restart the direct caller.

## Prevention
- Give callers a circuit breaker so they fail fast instead of paying the full timeout on every request.
- Keep caller timeouts consistent along the chain: an upstream timeout shorter than the downstream one hides the real root cause and blames the wrong service.
- Handle SIGTERM gracefully so a stop is fast and in-flight telemetry is flushed.
