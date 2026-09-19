# Runbook: elevated HTTP 5xx errors

## Symptoms
- A fraction of requests to a service return 5xx while others succeed. Latency may stay normal.
- The direct caller shows a similar error fraction; the end-user error rate tracks the fraction of failing requests.

## Diagnosis
1. Read the error rate per service and find the lowest one in the call chain that shows errors: it is the origin.
2. Check the error type recorded in telemetry. Errors that the service generated itself differ from errors it received from a dependency (dependency_* error types).
3. Check for a recent deployment, configuration change or feature flag on the origin service.

## Mitigation
- Roll back the most recent deployment of the origin service (ROLLBACK_DEPLOYMENT) if the errors began with a release.
- Restart the service if it is in a bad state (RESTART_SERVICE).
- Scale out only if the errors are load related.

## Prevention
- Retry idempotent requests with backoff and jitter, with a retry budget so retries cannot amplify an outage.
- Add a circuit breaker so callers stop sending traffic to a failing service.
- Canary new releases and watch the error rate before full rollout.
