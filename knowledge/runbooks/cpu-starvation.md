# Runbook: CPU starvation

## Symptoms
- Latency rises, often with no errors. The effect scales with how much CPU each request needs: cheap requests barely change, CPU-heavy requests slow down a lot (their p95 can grow by tens of times).
- A single-threaded service suffers most, because one starved request delays all queued requests behind it.
- Callers see slow responses from the starved service and inherit the latency.

## Diagnosis
1. Look at CPU utilisation and CPU throttling of the affected container. A container capped by a CPU quota shows throttled periods.
2. Compare latency for cheap and expensive request types. A gap that widens under load indicates CPU pressure.
3. Check for a noisy neighbour or a runaway process inside the container.

## Mitigation
- Raise the CPU limit or scale out the service (SCALE_SERVICE) so work is spread over more capacity.
- Restart the service if a runaway process is consuming the quota (RESTART_SERVICE).
- Shed load or reject the most expensive requests until capacity recovers.

## Prevention
- Make servers multi-threaded or use an event loop so one slow request does not block the rest.
- Set realistic CPU requests and limits and alert on throttling before users notice.
- Apply timeouts at the caller so slowness does not accumulate.
