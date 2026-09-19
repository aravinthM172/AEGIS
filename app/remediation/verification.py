"""Recovery verification (pure given injected probe/clock functions).

"Recovery verified" is only ever claimed from real end-to-end probes: enough probes, a high
success rate, and latency back near the baseline. If that never happens within the time
limit the remediation is reported as failed, not assumed to have worked.
"""
import time
from typing import Callable

MIN_PROBES = 8
MIN_SUCCESS_RATE = 0.9
FALLBACK_P95_MS = 1500.0  # used only when no baseline is known


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def latency_threshold(reference_p95_ms: float | None) -> float:
    if not reference_p95_ms:
        return FALLBACK_P95_MS
    return max(2.0 * reference_p95_ms, reference_p95_ms + 100.0)


def judge_probes(probes: list[dict], reference_p95_ms: float | None) -> dict:
    """probes: [{"ok": bool, "latency_ms": float, "status": int}]."""
    threshold = latency_threshold(reference_p95_ms)
    n = len(probes)
    if n < MIN_PROBES:
        return {"verified": False, "reason": f"only {n} probes (need {MIN_PROBES})", "probes": n,
                "threshold_ms": threshold}
    ok = [p for p in probes if p["ok"]]
    success = len(ok) / n
    p95 = percentile([p["latency_ms"] for p in ok], 0.95) if ok else None
    verified = success >= MIN_SUCCESS_RATE and p95 is not None and p95 <= threshold
    reason = None if verified else (
        f"success rate {success:.0%} (need >= {MIN_SUCCESS_RATE:.0%})" if success < MIN_SUCCESS_RATE
        else f"p95 {p95:.0f} ms exceeds {threshold:.0f} ms")
    return {"verified": verified, "reason": reason, "probes": n, "success_rate": round(success, 3),
            "p95_ms": None if p95 is None else round(p95, 1), "threshold_ms": round(threshold, 1)}


def verify_recovery(probe: Callable[[int], list[dict]], reference_p95_ms: float | None, *,
                    settle_s: float = 3.0, max_wait_s: float = 45.0, interval_s: float = 3.0,
                    probes_per_round: int = 10, clock: Callable[[], float] = time.monotonic,
                    sleep: Callable[[float], None] = time.sleep) -> dict:
    """Probe until healthy or out of time. recovery_time_s counts from the call (i.e. from action completion)."""
    started = clock()
    sleep(settle_s)  # let restarted processes and cached DNS entries settle
    rounds, last = 0, None
    while True:
        rounds += 1
        last = judge_probes(probe(probes_per_round), reference_p95_ms)
        if last["verified"]:
            return {"verified": True, "recovery_time_s": round(clock() - started, 1), "rounds": rounds, "final": last}
        if clock() - started >= max_wait_s:
            return {"verified": False, "recovery_time_s": None, "rounds": rounds, "final": last,
                    "reason": f"not healthy after {max_wait_s:.0f}s: {last['reason']}"}
        sleep(interval_s)
