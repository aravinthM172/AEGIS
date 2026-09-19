"""Resilience model built from failure signatures (pure, no DB).

Every number comes from a measured experiment. A (service, dependency, fault) combination
that was never tested is reported as untested and is NOT scored.

  resilience_score = 100 - median measured impact_pct        (higher is better)
  level            = HIGH >= 80, MEDIUM >= 40, LOW < 40
  confidence       = low (< 3 runs) / medium (3-4) / high (>= 5) for the same trigger
"""
import json
import statistics
from collections import Counter, defaultdict

END_USERS = "end-users"
HIGH, MEDIUM = 80.0, 40.0


def level(score: float | None) -> str | None:
    if score is None:
        return None
    return "HIGH" if score >= HIGH else "MEDIUM" if score >= MEDIUM else "LOW"


def confidence(runs: int) -> str:
    return "low" if runs < 3 else "medium" if runs < 5 else "high"


_CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


def _params_key(params: dict) -> str:
    return json.dumps(params or {}, sort_keys=True)


def _datapoints(signatures: list[dict], dependency_map: dict[str, set[str]] | None):
    """Yield (subject, dependency, fault_type, key, params, entry, recovery_s, signature_id, conditions).

    A service is only scored against a fault on something it depends on (declared/observed, transitively),
    or one that demonstrably affected it. Being unaffected by a fault on an unrelated service says nothing
    about its resilience. `key` includes the measurement conditions (workload intensity): results measured
    under different load are never averaged together.
    """
    for row in signatures:
        sig = row["signature"]
        if sig["kind"] != "fault":
            continue
        trig, obs = sig["trigger"], sig["observed"]
        recovery = obs["recovery"].get("recovery_s") if obs["recovery"]["status"] == "recovered" else None
        conditions = {"workload_n": trig.get("workload_n")}
        key = _params_key({"params": trig["parameters"], "conditions": conditions})
        for name, entry in obs["services"].items():
            if entry["role"] in ("target", "client") or entry["status"] == "insufficient_data":
                continue
            related = dependency_map is None or trig["target"] in dependency_map.get(name, set())
            if not related and entry["status"] != "affected":
                continue
            yield (name, trig["target"], trig["fault_type"], key, trig["parameters"], entry, recovery, row["id"],
                   conditions)
        eu = obs.get("end_user")
        if eu and eu["status"] != "insufficient_data":
            yield (END_USERS, trig["target"], trig["fault_type"], key, trig["parameters"], eu, recovery, row["id"],
                   conditions)


def _aggregate(points: list) -> dict:
    impacts = [p[5]["impact_pct"] for p in points]
    recoveries = [p[6] for p in points if p[6] is not None]
    modes = Counter(p[5]["degraded_mode"] for p in points)
    score = round(100.0 - statistics.median(impacts), 1)
    return {
        "runs": len(points),
        "confidence": confidence(len(points)),
        "impact_pct": {"median": round(statistics.median(impacts), 1), "min": min(impacts), "max": max(impacts)},
        "degraded_mode": modes.most_common(1)[0][0],
        "recovery_s": ({"median": round(statistics.median(recoveries), 1), "max": max(recoveries)}
                       if recoveries else None),
        "resilience_score": score,
        "level": level(score),
        "signature_ids": sorted({p[7] for p in points}),
    }


def build_resilience(signatures: list[dict], dependency_map: dict[str, set[str]] | None = None,
                     excluded: frozenset = frozenset()) -> dict:
    """signatures: [{"id", "signature"}]; dependency_map: {service: transitive dependencies};
    excluded: services deliberately outside the experiments (the measurement plane)."""
    grouped: dict[tuple, list] = defaultdict(list)
    for point in _datapoints(signatures, dependency_map):
        subject, dep, fault, pkey = point[:4]
        grouped[(subject, dep, fault, pkey)].append(point)

    per_subject: dict[str, list[dict]] = defaultdict(list)
    for (subject, dep, fault, _), points in sorted(grouped.items()):
        per_subject[subject].append({"dependency": dep, "fault_type": fault, "parameters": points[0][4],
                                     "conditions": points[0][8], **_aggregate(points)})

    profiles = {}
    for subject, faults in per_subject.items():
        worst = min(faults, key=lambda f: (f["resilience_score"], f["dependency"]))
        weakest_conf = min((f["confidence"] for f in faults), key=_CONFIDENCE_ORDER.get)
        scores = [f["resilience_score"] for f in faults]
        tested = {f["dependency"] for f in faults}
        untested = sorted((dependency_map or {}).get(subject, set()) - tested) if subject != END_USERS else []
        profiles[subject] = {
            "service": subject,
            "worst_case_score": worst["resilience_score"],
            "level": worst["level"],
            "average_score": round(sum(scores) / len(scores), 1),
            "confidence": weakest_conf,
            "tested_combinations": len(faults),
            "critical_dependency": {"service": worst["dependency"], "fault_type": worst["fault_type"],
                                    "resilience_score": worst["resilience_score"],
                                    "degraded_mode": worst["degraded_mode"]},
            "faults": faults,
            "untested_dependencies": untested,
        }

    # services with structural dependencies but no measured datapoint at all
    for subject, deps in (dependency_map or {}).items():
        if subject not in profiles and deps:
            note = ("excluded from experiments: part of the measurement plane" if subject in excluded
                    else "no experiment has measured this service yet")
            profiles[subject] = {"service": subject, "worst_case_score": None, "level": None,
                                 "confidence": None, "tested_combinations": 0, "faults": [],
                                 "critical_dependency": None,
                                 "untested_dependencies": [] if subject in excluded else sorted(deps),
                                 "note": note}

    controls = [r["signature"] for r in signatures if r["signature"]["kind"] == "control"]
    noise = [e["impact_pct"] for c in controls for n, e in c["observed"]["services"].items()
             if e["role"] != "target" and e["impact_pct"] is not None]
    return {
        "basis": "measured",
        "thresholds": {"high": HIGH, "medium": MEDIUM},
        "noise_floor": {"controls": len(controls), "max_impact_pct": max(noise) if noise else None,
                        "note": None if controls else "no control (dry-run + workload) experiment has been run"},
        "services": profiles,
        "criticality": rank_criticality(signatures),
    }


def rank_criticality(signatures: list[dict]) -> list[dict]:
    """Targets ranked by MEASURED end-user impact (worst case, then mean), then services affected."""
    by_target: dict[str, list[dict]] = defaultdict(list)
    for row in signatures:
        if row["signature"]["kind"] == "fault":
            by_target[row["signature"]["trigger"]["target"]].append(row["signature"])

    ranking = []
    for target, sigs in by_target.items():
        eu = [s["observed"]["end_user"]["impact_pct"] for s in sigs
              if s["observed"].get("end_user") and s["observed"]["end_user"]["impact_pct"] is not None]
        affected = [len(s["observed"]["affected_services"]) for s in sigs]
        ranking.append({
            "target": target,
            "runs": len(sigs),
            "confidence": confidence(len(sigs)),
            "fault_types": sorted({s["trigger"]["fault_type"] for s in sigs}),
            "end_user_impact_pct": ({"max": round(max(eu), 1), "mean": round(sum(eu) / len(eu), 1)} if eu else None),
            "max_services_affected": max(affected),
            "propagation_paths": sorted({p for s in sigs for p in s["observed"]["propagation_paths"]}),
        })
    ranking.sort(key=lambda r: (-(r["end_user_impact_pct"]["max"] if r["end_user_impact_pct"] else -1),
                                -r["max_services_affected"], r["target"]))
    for i, row in enumerate(ranking, 1):
        row["rank"] = i
    return ranking
