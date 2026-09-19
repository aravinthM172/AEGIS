"""Anomaly detection and similarity-based failure prediction (pure, deterministic; no ML, no DB).

State  = per-service features over a short recent window, judged against that service's baseline.
Match  = how well the set of currently anomalous services (and their error/latency character)
         resembles the affected set of a stored failure signature.
Output = which known fault this resembles, which services are not yet affected but were in that
         signature (at risk), and the historical end-user impact and recovery.

Nothing here claims accuracy. Accuracy is measured separately by the backtest.
"""
from collections import defaultdict

from app.analysis.blast_radius import percentile

MIN_EVENTS = 6          # events needed in the recent window and in the baseline
SLOW_FACTOR = 2.0       # same latency rule as the blast-radius analysis
SLOW_FLOOR_MS = 50.0
ERROR_RISE_PCT = 10.0   # error rate must exceed baseline by this many points
MIN_SIMILARITY = 0.6    # below this a match is not reported
MIN_ANOMALOUS = 1
TARGET_HEALTHY_PENALTY = 0.3  # signature says the target itself degrades, but it is measurably healthy now


def features(events: list[dict], lo: float, hi: float) -> dict | None:
    """{n, error_pct, p95_ms} for events with lo <= ts < hi, or None if there are none."""
    window = [e for e in events if lo <= e["ts"] < hi]
    if not window:
        return None
    lat = [e["latency_ms"] for e in window if e.get("latency_ms") is not None]
    errors = sum(1 for e in window if e.get("status") is None or e["status"] >= 500 or e["status"] == 0)
    return {"n": len(window), "error_pct": 100.0 * errors / len(window), "p95_ms": percentile(lat, 0.95)}


def assess(current: dict | None, baseline: dict | None) -> dict:
    """Judge one service's recent window against its baseline."""
    if baseline and baseline["n"] >= MIN_EVENTS and (not current or current["n"] == 0):
        # it normally has traffic and now emits nothing; build_state decides whether that is meaningful
        return {"status": "silent", "modes": ["silent"]}
    if not current or not baseline or current["n"] < MIN_EVENTS or baseline["n"] < MIN_EVENTS \
            or current["p95_ms"] is None or baseline["p95_ms"] is None:
        return {"status": "insufficient_data"}
    base_p95 = max(baseline["p95_ms"], 1e-6)
    slow = current["p95_ms"] > max(SLOW_FACTOR * base_p95, base_p95 + SLOW_FLOOR_MS)
    errors = current["error_pct"] >= max(ERROR_RISE_PCT, baseline["error_pct"] + ERROR_RISE_PCT)
    modes = [m for m, on in (("errors", errors), ("latency", slow)) if on]
    return {"status": "anomalous" if modes else "normal", "modes": modes,
            "p95_ratio": round(current["p95_ms"] / base_p95, 2), "error_pct": round(current["error_pct"], 1)}


def build_state(recent: dict[str, dict | None], baselines: dict[str, dict | None]) -> dict[str, dict]:
    """Assess every service. Silence only counts as evidence while other services still receive traffic:
    if nothing is being requested at all, quiet services are not failing, there is just no load."""
    state = {name: assess(recent.get(name), baselines[name]) for name in baselines}
    traffic = any((f or {}).get("n", 0) >= MIN_EVENTS for f in recent.values())
    for name, s in state.items():
        if s["status"] == "silent":
            state[name] = ({"status": "anomalous", "modes": ["silent"]} if traffic
                           else {"status": "insufficient_data"})
    return state


def signature_profile(signature: dict) -> dict[str, dict]:
    """{service: {"modes": [...], "impact_pct": x}} for the services a signature says were affected."""
    profile = {}
    for name, entry in signature["observed"]["services"].items():
        if entry["role"] == "client":
            continue
        if entry.get("silent"):
            # frozen/stopped targets and services starved by an upstream failure emit nothing
            profile[name] = {"modes": ["silent"], "impact_pct": entry["impact_pct"] or 100.0,
                             "target": entry["role"] == "target"}
            continue
        if entry["status"] != "affected":
            continue
        mode = entry["degraded_mode"]
        modes = ["errors", "latency"] if mode == "errors+latency" else [mode] if mode in ("errors", "latency") else []
        profile[name] = {"modes": modes, "impact_pct": entry["impact_pct"], "target": entry["role"] == "target"}
    return profile


def _mode_agreement(current: list[str], expected: list[str]) -> float:
    if not expected:
        return 0.5
    overlap = len(set(current) & set(expected))
    return overlap / max(len(set(current) | set(expected)), 1)


def match(state: dict[str, dict], signature: dict) -> dict | None:
    """How well the current anomalies resemble one signature. state: {service: assess() result}."""
    anomalous = {n: s for n, s in state.items() if s["status"] == "anomalous"}
    profile = signature_profile(signature)
    if len(anomalous) < MIN_ANOMALOUS or not profile:
        return None
    shared = set(anomalous) & set(profile)
    if not shared:
        return None
    precision = len(shared) / len(anomalous)  # every currently anomalous service should be one the fault affects
    agreement = sum(_mode_agreement(anomalous[n]["modes"], profile[n]["modes"]) for n in shared) / len(shared)
    similarity = precision * agreement
    for name, p in profile.items():
        if p["target"] and state.get(name, {}).get("status") == "normal":
            similarity *= TARGET_HEALTHY_PENALTY  # the fault's own target is measurably fine: a different fault
            break
    similarity = round(similarity, 3)
    return {"similarity": similarity, "progress": round(len(shared) / len(profile), 2),
            "matched_services": sorted(shared), "at_risk": sorted(set(profile) - set(anomalous))}


def predict(state: dict[str, dict], signatures: list[dict], min_similarity: float = MIN_SIMILARITY) -> list[dict]:
    """signatures: [{"id","signature"}] (fault kind only). Returns warnings, best first, one per (target, fault)."""
    best: dict[tuple, dict] = {}
    support: dict[tuple, int] = defaultdict(int)
    for row in signatures:
        sig = row["signature"]
        if sig["kind"] != "fault":
            continue
        m = match(state, sig)
        if m is None or m["similarity"] < min_similarity:
            continue
        key = (sig["trigger"]["target"], sig["trigger"]["fault_type"])
        support[key] += 1
        if key not in best or m["similarity"] > best[key]["similarity"]:
            profile = signature_profile(sig)
            eu = (sig["observed"].get("end_user") or {}).get("impact_pct")
            best[key] = {
                "likely_target": key[0], "likely_fault": key[1], **m, "signature_id": row["id"],
                "stage": "developing" if m["progress"] < 1.0 else "established",
                "at_risk_services": [{"service": s, "risk": round(m["similarity"] * profile[s]["impact_pct"] / 100, 2),
                                      "historical_impact_pct": profile[s]["impact_pct"]} for s in m["at_risk"]],
                "expected_end_user_impact_pct": eu,
                "expected_recovery_s": sig["observed"]["recovery"].get("recovery_s"),
            }
    warnings = []
    for key, w in best.items():
        n = support[key]
        w["supporting_signatures"] = n
        w["confidence"] = "low" if n < 3 else "medium" if n < 5 else "high"
        w["basis"] = "historical_similarity"
        warnings.append(w)
    warnings.sort(key=lambda w: (-w["similarity"], w["likely_target"]))
    return warnings
