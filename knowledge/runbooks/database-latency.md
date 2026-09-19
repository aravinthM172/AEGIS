# Runbook: database latency

## Symptoms
- Request latency rises on every service that talks to the database, often by a multiple of the added delay, because one request performs several sequential round trips (a handshake plus statements). A 300 ms network delay can turn into over a second per request.
- Error rates may stay at zero: the system is slow, not failing. Percentile latency (p95) is the signal, not the error rate.
- Callers further up the chain inherit the slowness (indirect impact).

## Diagnosis
1. Compare p95 latency now with the baseline for the service that owns the database calls.
2. Check whether errors rose too. If not, suspect latency, not an outage.
3. Check database-side signals: active connections, connection pool utilisation, long-running queries, CPU and memory.
4. Look at the network path between the service and the database for added delay.

## Mitigation
- Reduce the number of round trips per request (batch statements, avoid chatty access, cache read-mostly data).
- Make sure caller timeouts are shorter than the caller's own caller, so slowness does not turn into a pile-up.
- Enable a fallback (cache or degraded response) if the feature allows it.
- Restart the service only if its connection pool is exhausted or wedged; restarting does not fix a slow database.

## Prevention
- Set explicit statement and connection timeouts.
- Put slow, non-critical writes behind a queue so the request path does not wait for the database.
- Load-test with injected database latency and watch p95 at the entry point.
