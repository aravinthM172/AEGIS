"""Failure signatures: a reusable, measured fingerprint of one experiment (pure, no DB).

A signature is derived ONLY from a finished blast-radius analysis. Dry-run experiments
produce `control` signatures (no fault applied) that measure the noise floor; real runs
produce `fault` signatures.
"""
import hashlib
import json

ERROR_MODE_PCT = 10.0      # fault-window error rate at/above this => the service returned errors
LATENCY_MODE_RATIO = 2.0   # p95 at/above this multiple of baseline => the service got slow


def degraded_mode(entry: dict | None) -> str:
    """errors | latency | errors+latency | none | unknown, from one analysed service entry."""
    if not entry or entry.get("status") == "insufficient_data":
        return "unknown"
    errors = (entry["fault"].get("error_pct") or 0) >= ERROR_MODE_PCT
    slow = (entry.get("p95_ratio") or 0) >= LATENCY_MODE_RATIO
    if errors and slow:
        return "errors+latency"
    return "errors" if errors else "latency" if slow else "none"


def _is_silent(entry: dict) -> bool:
    """Had traffic before the fault but emitted nothing during it: down, frozen, or starved of requests."""
    return entry["baseline"]["requests"] >= 8 and entry["fault"]["requests"] == 0


def _service_summary(entry: dict) -> dict:
    return {
        "silent": _is_silent(entry),
        "role": entry.get("role"),
        "status": entry["status"],
        "impact_pct": entry["impact_pct"],
        "error_pct": entry["fault"].get("error_pct"),
        "p95_ratio": entry.get("p95_ratio"),
        "baseline_p95_ms": entry["baseline"].get("p95_ms"),
        "fault_p95_ms": entry["fault"].get("p95_ms"),
        "degraded_mode": degraded_mode(entry),
    }


def _fingerprint(trigger: dict, affected: dict[str, str], paths: list[str]) -> str:
    shape = {"target": trigger["target"], "fault_type": trigger["fault_type"],
             "affected": sorted(affected.items()), "paths": sorted(paths)}
    return hashlib.sha1(json.dumps(shape, sort_keys=True).encode()).hexdigest()[:16]


def build_signature(experiment: dict, result: dict) -> dict | None:
    """experiment: {target, fault_type, parameters, duration_s, workload_rps, dry_run}; result: analysis output."""
    if result.get("status") != "analyzed":
        return None

    services = {name: _service_summary(e) for name, e in result["services"].items()
                if e["status"] != "insufficient_data" or e.get("role") in ("target", "client") or _is_silent(e)}
    trigger = {"target": experiment["target"], "fault_type": experiment["fault_type"],
               "parameters": experiment.get("parameters") or {}, "duration_s": experiment.get("duration_s"),
               "workload_rps": experiment.get("workload_rps"), "workload_n": experiment.get("workload_n")}

    affected_modes = {n: services[n]["degraded_mode"] for n in result["affected_services"] if n in services}
    end_user = None
    client = next((n for n, e in result["services"].items() if e.get("role") == "client"), None)
    if client:
        end_user = {k: services[client][k] for k in ("status", "impact_pct", "error_pct", "p95_ratio", "degraded_mode")}
    rec = result["recovery"]
    structural = result.get("structural", {})

    return {
        "version": 1,
        "kind": "control" if experiment.get("dry_run") else "fault",
        "basis": "measured",
        "trigger": trigger,
        "observed": {
            "services": services,
            "target": result.get("target_impact"),
            "end_user": end_user,
            "affected_services": result["affected_services"],
            "direct": result["direct"],
            "indirect": result["indirect"],
            "propagation_paths": result["propagation"]["paths"],
            "structural": {"at_risk_but_unaffected": structural.get("at_risk_but_unaffected", []),
                           "affected_but_not_predicted": structural.get("affected_but_not_predicted", [])},
            "recovery": {"status": rec["status"], "recovery_s": rec.get("recovery_s"),
                         "restore_operation_s": rec.get("restore_operation_s")},
        },
        "fingerprint": _fingerprint(trigger, affected_modes, result["propagation"]["paths"]),
        "quality": {"warnings": result["quality"]["warnings"], "workload": result.get("workload")},
    }
