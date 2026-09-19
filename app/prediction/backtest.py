"""Leave-one-out backtest of the predictor over stored experiments (pure).

For each experiment E the telemetry is replayed second by second. At each step the predictor
sees ONLY events up to that moment and ONLY signatures from other experiments, so it cannot
have learned E's own outcome. Reported accuracy comes exclusively from this replay.
"""
from app.prediction.core import MIN_EVENTS, assess, build_state, features, predict

WINDOW_S = 8.0
STEP_S = 1.0


def replay_experiment(exp: dict, http_events: dict[str, list[dict]], signatures: list[dict],
                      window_s: float = WINDOW_S, step_s: float = STEP_S) -> dict:
    """exp: {id, target, fault_type, dry_run, timeline{baseline_started_at, inject_started_at, fault_applied_at,
    rollback_started_at, finished_at}} (epoch floats). signatures: other experiments' signature rows only."""
    t = exp["timeline"]
    b0, i0, fa, rb, fin = (t["baseline_started_at"], t["inject_started_at"], t["fault_applied_at"],
                           t["rollback_started_at"], t["finished_at"])
    # baseline reference: the healthy period before injection (what the predictor would have learned live)
    baseline = {name: features(events, b0, i0) for name, events in http_events.items()}

    steps = []
    now = b0 + window_s
    while now <= fin:
        state = build_state({name: features(events, now - window_s, now) for name, events in http_events.items()},
                            baseline)
        warnings = predict(state, signatures)
        steps.append({"t": round(now - b0, 1), "phase": "baseline" if now < i0 else "fault" if fa <= now < rb else "other",
                      "top": warnings[0] if warnings else None})
        now += step_s

    fault_steps = [s for s in steps if s["phase"] == "fault"]
    baseline_steps = [s for s in steps if s["phase"] == "baseline"]
    first = next((s for s in fault_steps if s["top"]), None)
    correct = next((s for s in fault_steps if s["top"] and s["top"]["likely_target"] == exp["target"]
                    and s["top"]["likely_fault"] == exp["fault_type"]), None)
    return {
        "experiment_id": exp["id"], "target": exp["target"], "fault_type": exp["fault_type"],
        "dry_run": exp["dry_run"],
        "detected": first is not None,
        "correct_attribution": correct is not None,
        "lead_time_s": round(correct["t"] - (fa - b0), 1) if correct else None,
        "first_warning_target": first["top"]["likely_target"] if first else None,
        # a control run applies no fault, so a warning at ANY point of it is a false alarm
        "false_alarm_steps": sum(1 for s in (baseline_steps + fault_steps if exp["dry_run"] else baseline_steps)
                                 if s["top"]),
        "baseline_steps": len(baseline_steps) + (len(fault_steps) if exp["dry_run"] else 0),
        "fault_steps": len(fault_steps),
        "detectable": _detectable(exp, http_events, baseline, fa, rb),
    }


def _detectable(exp: dict, http_events: dict, baseline: dict, fa: float, rb: float) -> bool:
    """Whether the fault window contains ANY anomalous service at all. If not, there is nothing to warn about."""
    state = build_state({name: features(events, fa, rb) for name, events in http_events.items()}, baseline)
    return any(s["status"] == "anomalous" for s in state.values())


def summarize(rows: list[dict]) -> dict:
    faults = [r for r in rows if not r["dry_run"] and r["detectable"]]
    undetectable = [r for r in rows if not r["dry_run"] and not r["detectable"]]
    controls = [r for r in rows if r["dry_run"]]
    baseline_steps = sum(r["baseline_steps"] for r in rows)
    false_steps = sum(r["false_alarm_steps"] for r in rows)
    leads = sorted(r["lead_time_s"] for r in faults if r["lead_time_s"] is not None)

    def rate(k, n):
        return None if n == 0 else round(k / n, 3)

    return {
        "method": "leave-one-out replay: each experiment is replayed second by second using only signatures "
                  "from other experiments and only telemetry available up to that second",
        "experiments": len(rows),
        "fault_experiments_with_detectable_impact": len(faults),
        "fault_experiments_without_detectable_impact": len(undetectable),
        "control_experiments": len(controls),
        "detection_rate": rate(sum(r["detected"] for r in faults), len(faults)),
        "correct_attribution_rate": rate(sum(r["correct_attribution"] for r in faults), len(faults)),
        "median_lead_time_s": leads[len(leads) // 2] if leads else None,
        "false_alarm_rate": rate(false_steps, baseline_steps),
        "false_alarm_steps": false_steps,
        "baseline_steps_evaluated": baseline_steps,
        "caveats": [
            "the predictor's rules were adjusted after inspecting an earlier version of this backtest, so these "
            "numbers are optimistic; a fresh set of experiments would be needed for an unbiased estimate",
            "small sample: rates are only as reliable as the number of experiments behind them",
            "signatures come from the same system and workload; accuracy elsewhere is unknown",
            "lead time is measured from the moment the fault took effect until the first correct warning",
            f"a service needs >= {MIN_EVENTS} requests in the window to be judged; sparse traffic means no warning",
        ],
    }
