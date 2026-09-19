# Resilience patterns and how to test them

## Timeouts
Every remote call needs an explicit timeout, and timeouts must shrink as you move down the call chain: the caller's timeout must be longer than the sum of what its callee may legitimately take, otherwise the caller gives up first and reports the wrong culprit.

## Retries
Retry only idempotent operations, with exponential backoff and jitter, and cap the total retry budget. Unbounded retries turn a partial failure into an outage.

## Circuit breaker
After a threshold of failures, stop calling the dependency for a cooling-off period and fail fast or serve a fallback. This turns a slow failure (paying the full timeout on every request) into a fast one and gives the dependency room to recover.

## Bulkhead
Limit the concurrency used for each dependency, so one hung dependency cannot consume every worker.

## Queue-based decoupling
Move work that does not need to complete inside the user request behind a queue and a worker. A slow or failed backend then delays the work instead of failing the request. This is the standard fix when a synchronous dependency dominates user-visible impact.

## Graceful degradation
Serve a cached or reduced response when a non-critical dependency is unavailable.

## How to verify with FaultScope
Run the same fault before and after a change, with the same workload, and compare the measured end-user impact and recovery time. Only claim an improvement that the two measurements show. Repeat runs for confidence: a single run has low confidence.
