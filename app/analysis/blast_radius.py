"""Blast-radius analysis (pure: no DB, no clock).

Turns an experiment's timeline + the telemetry it produced into MEASURED impact.
Nothing here invents values: a service with too little traffic in a window is reported
as insufficient_data, and structural predictions (from the dependency graph) are kept
separate from what was actually measured.

Windows (epoch seconds):
  baseline  [baseline_started_at, inject_started_at)       healthy reference
  fault     [fault_applied_at,    rollback_started_at)     the fault is really in effect
  recovery  [rollback_started_at, finished_at]             fault removed; time to healthy
The span [inject_started_at, fault_applied_at) is excluded from both baseline and fault:
the fault is being applied (e.g. docker stop grace period), so it is neither.

An event is `degraded` if it failed (status >= 500 or no status) or was slower than
max(2 x baseline p95, baseline p95 + 50ms). This catches latency amplification, where
nothing errors but everything is slow.
"""
from collections import Counter, defaultdict, deque

MIN_EVENTS = 8              # below this a window is not trusted
AFFECTED_THRESHOLD_PCT = 10.0
SLOW_FACTOR = 2.0
SLOW_FLOOR_MS = 50.0
MIN_BASELINE_SECONDS = 5


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def is_error(event: dict) -> bool:
    status = event.get("status")
    return status is None or status >= 500 or status == 0


def slow_threshold(baseline_events: list[dict]) -> float | None:
    latencies = [e["latency_ms"] for e in baseline_events if e.get("latency_ms") is not None and not is_error(e)]
    if len(latencies) < MIN_EVENTS:
        return None
    p95 = percentile(latencies, 0.95)
    return max(SLOW_FACTOR * p95, p95 + SLOW_FLOOR_MS)


def window_stats(events: list[dict], seconds: float, threshold: float | None) -> dict:
    n = len(events)
    errors = sum(1 for e in events if is_error(e))
    slow = 0
    if threshold is not None:
        slow = sum(1 for e in events if not is_error(e) and (e.get("latency_ms") or 0) > threshold)
    degraded = errors + slow
    lat = [e["latency_ms"] for e in events if e.get("latency_ms") is not None]
    return {
        "requests": n,
        "rps": round(n / seconds, 2) if seconds > 0 else None,
        "errors": errors,
        "error_pct": round(100.0 * errors / n, 1) if n else None,
        "slow": slow,
        "degraded": degraded,
        "degraded_pct": round(100.0 * degraded / n, 1) if n else None,
        "p50_ms": _r(percentile(lat, 0.5)),
        "p95_ms": _r(percentile(lat, 0.95)),
        "status_counts": dict(sorted(Counter(str(e.get("status")) for e in events).items())),
    }


def _r(value):
    return None if value is None else round(value, 1)


def _in(events: list[dict], lo: float, hi: float) -> list[dict]:
    return [e for e in events if lo <= e["ts"] < hi]


def _hops(target: str, edges: list[tuple[str, str]]) -> dict[str, int]:
    """Services that depend on `target` (transitively) -> hop distance."""
    reverse = defaultdict(list)
    for src, dst in edges:
        reverse[dst].append(src)
    dist, queue, seen = {}, deque([(target, 0)]), {target}
    while queue:
        node, d = queue.popleft()
        for nxt in reverse.get(node, ()):
            if nxt not in seen:
                seen.add(nxt)
                dist[nxt] = d + 1
                queue.append((nxt, d + 1))
    return dist


def _paths(target: str, edges: list[dict]) -> list[str]:
    """Failure-origin -> user-facing chains over the propagation edges."""
    incoming = defaultdict(list)  # callee -> callers within propagation set
    for e in edges:
        incoming[e["target"]].append(e["source"])
    paths: list[str] = []

    def walk(node: str, trail: list[str]) -> None:
        callers = [c for c in incoming.get(node, []) if c not in trail]
        if not callers:
            paths.append(" -> ".join(trail))
            return
        for caller in sorted(callers):
            walk(caller, trail + [caller])

    walk(target, [target])
    return sorted(set(p for p in paths if " -> " in p))


def analyze(*, experiment: dict, http_events: dict[str, list[dict]], call_events: list[dict],
            declared_edges: list[tuple[str, str]], entry_service: str | None = None,
            workload: dict | None = None, client_services: frozenset = frozenset()) -> dict:
    """
    experiment: {target, fault_type, dry_run, timeline: {baseline_started_at, inject_started_at,
                 fault_applied_at, rollback_started_at, inject_ended_at, finished_at}} (epoch floats)
    http_events: {service: [{ts, latency_ms, status}]}  (event_type=http_request)
    call_events: [{ts, source, target, latency_ms, status, error_type}] (event_type=dependency_call)
    declared_edges: [(source, target)] source depends on target
    """
    t = experiment["timeline"]
    target = experiment["target"]
    warnings: list[str] = []

    missing = [k for k in ("baseline_started_at", "inject_started_at", "fault_applied_at",
                           "rollback_started_at", "finished_at") if t.get(k) is None]
    if missing:
        return {"version": 1, "status": "not_analyzable",
                "reason": f"experiment never reached the fault window (missing: {', '.join(missing)})"}

    b0, i0, fa, rb, fin = (t["baseline_started_at"], t["inject_started_at"], t["fault_applied_at"],
                           t["rollback_started_at"], t["finished_at"])
    windows = {
        "baseline": {"start": b0, "end": i0, "seconds": round(i0 - b0, 2)},
        "fault": {"start": fa, "end": rb, "seconds": round(rb - fa, 2)},
        "recovery": {"start": rb, "end": fin, "seconds": round(fin - rb, 2)},
    }
    if windows["baseline"]["seconds"] < MIN_BASELINE_SECONDS:
        warnings.append(f"baseline is only {windows['baseline']['seconds']}s (< {MIN_BASELINE_SECONDS}s): "
                        "slow-request thresholds may be unavailable")
    if experiment.get("dry_run"):
        warnings.append("dry run: no fault was applied; this is a CONTROL measurement of normal variation")

    # observed call edges in the fault window + declared edges -> combined graph
    observed_pairs = {(c["source"], c["target"]) for c in call_events}
    combined = sorted(set(declared_edges) | observed_pairs)
    hops = _hops(target, combined)

    services: dict[str, dict] = {}
    for name, events in sorted(http_events.items()):
        base = _in(events, b0, i0)
        thr = slow_threshold(base)
        b_stats = window_stats(base, windows["baseline"]["seconds"], thr)
        f_stats = window_stats(_in(events, fa, rb), windows["fault"]["seconds"], thr)

        entry = {"baseline": b_stats, "fault": f_stats, "slow_threshold_ms": _r(thr)}
        if b_stats["requests"] < MIN_EVENTS or f_stats["requests"] < MIN_EVENTS:
            entry["status"] = "insufficient_data"
            entry["impact_pct"] = None
            entry["note"] = (f"needs >= {MIN_EVENTS} requests in baseline and fault windows "
                             f"(got {b_stats['requests']} / {f_stats['requests']})")
        else:
            impact = max(0.0, (f_stats["degraded_pct"] or 0) - (b_stats["degraded_pct"] or 0))
            entry["impact_pct"] = round(impact, 1)
            entry["status"] = "affected" if impact >= AFFECTED_THRESHOLD_PCT else "unaffected"
            if b_stats["p95_ms"] and f_stats["p95_ms"]:
                entry["p95_ratio"] = round(f_stats["p95_ms"] / b_stats["p95_ms"], 2)
            if (b_stats["error_pct"] or 0) > 20:
                warnings.append(f"{name}: baseline error rate {b_stats['error_pct']}% — baseline is unhealthy")
        entry["dependency_hops"] = hops.get(name)
        services[name] = entry

    for name, entry in services.items():
        if name in client_services:
            entry["role"] = "client"  # an external observer (synthetic user), not part of the system graph
        elif entry["baseline"]["requests"] >= MIN_EVENTS and entry["fault"]["requests"] == 0:
            warnings.append(f"{name}: no telemetry during the fault window (it may be down, its telemetry "
                            "path impaired, or it received no traffic)")

    for name, entry in services.items():
        if name == target:
            entry["role"] = "target"  # the injected fault's own effect, not propagation

    affected = sorted((n for n, s in services.items()
                       if s["status"] == "affected" and n not in client_services and n != target),
                      key=lambda n: (hops.get(n, 99), n))
    direct = [n for n in affected if hops.get(n) == 1]
    indirect = [n for n in affected if hops.get(n, 0) > 1]
    unexplained = [n for n in affected if n not in hops]
    for n in affected:
        services[n]["role"] = "direct" if n in direct else "indirect" if n in indirect else "unexplained"

    at_risk = sorted(hops, key=lambda n: (hops[n], n))
    measurable = {n for n, s in services.items() if s["status"] != "insufficient_data" and n not in client_services}
    structural = {
        "at_risk": at_risk,
        "measured_affected": affected,
        "at_risk_but_unaffected": [n for n in at_risk if n in measurable and n not in affected],
        "at_risk_but_not_measurable": [n for n in at_risk if n not in measurable],
        "affected_but_not_predicted": unexplained,
    }

    # propagation: graph edges (caller -> callee) between the target/affected set, with evidence
    in_play = {target, *affected}  # the target always heads the propagation chain
    propagation_edges = []
    for src, dst in combined:
        if src in affected and dst in in_play:
            calls = [c for c in call_events if c["source"] == src and c["target"] == dst and fa <= c["ts"] < rb]
            failed = sum(1 for c in calls if c.get("error_type"))
            propagation_edges.append({
                "source": src, "target": dst,
                "evidence": "observed" if (src, dst) in observed_pairs else "declared",
                "calls_in_fault_window": len(calls),
                "failed_calls": failed,
            })

    # recovery: last degraded request completing after the fault was removed
    recovery_per_service = {}
    for name in affected:
        thr = services[name]["slow_threshold_ms"]
        post = sorted(_in(http_events[name], rb, fin + 1e-6), key=lambda e: e["ts"])
        if not post:
            recovery_per_service[name] = {"status": "unknown", "note": "no traffic after rollback"}
            continue
        bad = [e for e in post if is_error(e) or (thr is not None and (e.get("latency_ms") or 0) > thr)]
        if not bad:
            recovery_per_service[name] = {"status": "recovered", "recovery_s": 0.0}
        elif bad[-1]["ts"] >= post[-1]["ts"]:
            recovery_per_service[name] = {"status": "not_recovered_in_window",
                                          "degraded_until_s": round(bad[-1]["ts"] - rb, 1)}
        else:
            recovery_per_service[name] = {"status": "recovered", "recovery_s": round(bad[-1]["ts"] - rb, 1)}

    recovered_times = [r["recovery_s"] for r in recovery_per_service.values() if r["status"] == "recovered"]
    if not affected:
        overall = {"status": "nothing_to_recover"}
    elif any(r["status"] == "not_recovered_in_window" for r in recovery_per_service.values()):
        overall = {"status": "not_recovered_in_window"}
    elif any(r["status"] == "unknown" for r in recovery_per_service.values()):
        overall = {"status": "unknown", "recovery_s": max(recovered_times, default=None)}
    else:
        overall = {"status": "recovered", "recovery_s": max(recovered_times, default=0.0)}
    overall["restore_operation_s"] = round(t["inject_ended_at"] - rb, 2) if t.get("inject_ended_at") else None

    entry_impact = None
    if entry_service and entry_service in services:
        s = services[entry_service]
        entry_impact = {"service": entry_service, "status": s["status"], "impact_pct": s["impact_pct"],
                        "degraded_requests": s["fault"]["degraded"], "total_requests": s["fault"]["requests"]}

    target_impact = None
    if target in services:
        t_entry = services[target]
        target_impact = {"service": target, "status": t_entry["status"], "impact_pct": t_entry["impact_pct"],
                         "error_pct": t_entry["fault"]["error_pct"], "requests": t_entry["fault"]["requests"]}

    return {
        "version": 1,
        "status": "analyzed",
        "basis": "measured",
        "dry_run": bool(experiment.get("dry_run")),
        "target": target,
        "fault_type": experiment["fault_type"],
        "windows": windows,
        "services": services,
        "affected_services": affected,
        "direct": direct,
        "indirect": indirect,
        "target_impact": target_impact,
        "affected_requests": sum(services[n]["fault"]["degraded"] for n in affected),
        "entry_impact": entry_impact,
        "structural": structural,
        "propagation": {"edges": propagation_edges, "paths": _paths(target, propagation_edges)},
        "recovery": {**overall, "per_service": recovery_per_service},
        "workload": workload,
        "quality": {"min_events": MIN_EVENTS, "affected_threshold_pct": AFFECTED_THRESHOLD_PCT,
                    "slow_rule": f"latency > max({SLOW_FACTOR}x baseline p95, baseline p95 + {SLOW_FLOOR_MS:g}ms)",
                    "warnings": warnings},
    }
