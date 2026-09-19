from app.prediction.backtest import replay_experiment, summarize
from app.prediction.core import MIN_SIMILARITY, assess, build_state, features, match, predict, signature_profile
from test_blast_radius import TIMELINE, healthy, stream
from test_resilience import CLIENT, sig, unaffected


def state(**services):
    """Build a predictor state: name=assess()-style dicts, or shorthand strings."""
    shorthand = {
        "normal": {"status": "normal", "modes": []},
        "errors": {"status": "anomalous", "modes": ["errors"], "p95_ratio": 1.0, "error_pct": 100.0},
        "latency": {"status": "anomalous", "modes": ["latency"], "p95_ratio": 40.0, "error_pct": 0.0},
        "both": {"status": "anomalous", "modes": ["errors", "latency"], "p95_ratio": 40.0, "error_pct": 100.0},
        "unknown": {"status": "insufficient_data"},
    }
    return {name: shorthand[v] for name, v in services.items()}


CPP_STOP = sig("cpp", "stop_container", sid="cpp-stop")          # java direct + gateway indirect, errors+latency
PG_LATENCY = sig("postgres", "latency", sid="pg-lat",
                 http={"java": stream(0, 12) + stream(12, 27, latency=1200) + stream(27, 45),
                       "gateway": stream(0, 12) + stream(12, 27, latency=1230) + stream(27, 45),
                       CLIENT: stream(0, 12) + stream(12, 27, latency=1250) + stream(27, 45)})


# ---- anomaly detection -----------------------------------------------------------------------------

def feat(n=20, err=0.0, p95=20.0):
    return {"n": n, "error_pct": err, "p95_ms": p95}


def test_features_summarise_a_window():
    events = [{"ts": t, "latency_ms": 10.0 * (t + 1), "status": 500 if t == 3 else 200} for t in range(10)]
    f = features(events, 0, 10)
    assert f["n"] == 10 and f["error_pct"] == 10.0 and f["p95_ms"] > 90
    assert features(events, 20, 30) is None
    assert features(events, 0, 5)["n"] == 5  # half-open window


def test_assess_flags_latency_and_error_anomalies_using_the_baseline():
    base = feat(p95=20.0)
    assert assess(feat(p95=21.0), base)["status"] == "normal"
    slow = assess(feat(p95=300.0), base)
    assert slow["status"] == "anomalous" and slow["modes"] == ["latency"] and slow["p95_ratio"] == 15.0
    failing = assess(feat(err=60.0), base)
    assert failing["status"] == "anomalous" and failing["modes"] == ["errors"]
    assert assess(feat(err=60.0, p95=900.0), base)["modes"] == ["errors", "latency"]


def test_assess_refuses_to_judge_without_enough_data():
    assert assess(feat(n=3, p95=999.0), feat())["status"] == "insufficient_data"
    assert assess(feat(), feat(n=2))["status"] == "insufficient_data"
    assert assess(None, feat())["status"] == "silent"  # normally busy, now emits nothing
    assert assess(None, None)["status"] == "insufficient_data"


# ---- matching and prediction --------------------------------------------------------------------------

def test_signature_profile_lists_affected_services_with_their_mode():
    p = signature_profile(CPP_STOP["signature"])
    assert set(p) == {"java", "gateway"} and p["java"]["modes"] == ["errors", "latency"]


def test_a_developing_failure_matches_the_signature_and_names_who_is_at_risk():
    m = match(state(java="both", gateway="normal", cpp="unknown"), CPP_STOP["signature"])
    assert m["similarity"] == 1.0 and m["matched_services"] == ["java"] and m["at_risk"] == ["gateway"]
    assert m["progress"] == 0.5
    w = predict(state(java="both", gateway="normal"), [CPP_STOP])[0]
    assert (w["likely_target"], w["likely_fault"], w["stage"]) == ("cpp", "stop_container", "developing")
    assert w["at_risk_services"] == [{"service": "gateway", "risk": 1.0, "historical_impact_pct": 100.0}]
    assert w["expected_end_user_impact_pct"] == 100.0 and w["basis"] == "historical_similarity"


def test_an_established_failure_has_nothing_left_at_risk():
    w = predict(state(java="both", gateway="both"), [CPP_STOP])[0]
    assert w["stage"] == "established" and w["at_risk_services"] == []


def test_a_healthy_system_produces_no_warning():
    assert predict(state(java="normal", gateway="normal"), [CPP_STOP, PG_LATENCY]) == []
    assert predict(state(java="unknown", gateway="unknown"), [CPP_STOP]) == []


def test_anomalies_that_the_signature_does_not_explain_lower_the_match():
    only_java_affected = state(java="both", gateway="normal", mystery="both")
    m = match(only_java_affected, CPP_STOP["signature"])
    assert m["similarity"] == 0.5  # 1 of 2 anomalous services is explained -> below the reporting threshold
    assert predict(only_java_affected, [CPP_STOP]) == []
    assert 0.5 < MIN_SIMILARITY


def test_character_of_the_anomaly_matters_latency_only_matches_the_latency_signature_better():
    latency_state = state(java="latency", gateway="latency")
    by_target = {w["likely_target"]: w["similarity"] for w in predict(latency_state, [CPP_STOP, PG_LATENCY], 0.1)}
    assert by_target["postgres"] > by_target["cpp"]  # postgres latency is latency-only; cpp stop is errors+latency
    assert predict(latency_state, [CPP_STOP, PG_LATENCY])[0]["likely_target"] == "postgres"


def test_control_signatures_and_signatures_with_no_impact_are_never_used():
    control = sig("cpp", "stop_container", http=unaffected(), dry_run=True, sid="ctl")
    no_impact = sig("kafka", "stop_container", http=unaffected(), sid="kafka")
    assert predict(state(java="both", gateway="both"), [control, no_impact]) == []


def test_confidence_grows_with_the_number_of_supporting_signatures():
    rows = [sig("cpp", "stop_container", sid=f"r{i}") for i in range(3)]
    w = predict(state(java="both", gateway="both"), rows)[0]
    assert w["supporting_signatures"] == 3 and w["confidence"] == "medium"
    assert predict(state(java="both", gateway="both"), rows[:1])[0]["confidence"] == "low"


# ---- backtest ---------------------------------------------------------------------------------------------

def experiment(target="cpp", fault="stop_container", dry_run=False):
    return {"id": "e1", "target": target, "fault_type": fault, "dry_run": dry_run, "timeline": dict(TIMELINE)}


def failing_http():
    def s(status, latency):
        return stream(0, 12) + stream(12, 27, latency=latency, status=status) + stream(27, 45)
    return {"java": s(503, 1000), "gateway": s(502, 1010)}


def test_replay_detects_a_known_failure_early_and_attributes_it_correctly():
    r = replay_experiment(experiment(), failing_http(), [CPP_STOP])
    assert r["detected"] and r["correct_attribution"] and r["detectable"]
    assert r["lead_time_s"] is not None and 0 <= r["lead_time_s"] <= 8  # within one window of the fault taking effect
    assert r["false_alarm_steps"] == 0 and r["baseline_steps"] > 0


def test_replay_without_matching_history_cannot_warn_and_is_reported_as_missed():
    r = replay_experiment(experiment(), failing_http(), [PG_LATENCY])
    assert r["detectable"] and not r["correct_attribution"] and r["lead_time_s"] is None


def test_replay_of_a_control_run_has_nothing_to_detect_and_no_false_alarms():
    healthy_http = {"java": healthy(), "gateway": healthy()}
    r = replay_experiment(experiment(dry_run=True), healthy_http, [CPP_STOP, PG_LATENCY])
    assert not r["detected"] and not r["detectable"] and r["false_alarm_steps"] == 0


def test_summary_reports_rates_with_sample_sizes_and_caveats():
    good = replay_experiment(experiment(), failing_http(), [CPP_STOP])
    missed = replay_experiment(experiment(), failing_http(), [])
    quiet = replay_experiment(experiment("kafka", dry_run=False), {"java": healthy(), "gateway": healthy()}, [CPP_STOP])
    control = replay_experiment(experiment(dry_run=True), {"java": healthy(), "gateway": healthy()}, [CPP_STOP])
    s = summarize([good, missed, quiet, control])
    assert s["fault_experiments_with_detectable_impact"] == 2 and s["fault_experiments_without_detectable_impact"] == 1
    assert s["control_experiments"] == 1 and s["detection_rate"] == 0.5 and s["correct_attribution_rate"] == 0.5
    assert s["false_alarm_rate"] == 0.0 and s["median_lead_time_s"] is not None
    assert "leave-one-out" in s["method"] and len(s["caveats"]) >= 3


def test_summary_with_no_data_reports_none_instead_of_inventing_a_rate():
    s = summarize([])
    assert s["detection_rate"] is None and s["correct_attribution_rate"] is None and s["false_alarm_rate"] is None


# ---- v2: silence as evidence, target's own telemetry, target-healthy penalty ---------------------------

def test_silence_counts_as_evidence_only_while_the_rest_of_the_system_still_has_traffic():
    base = {"gateway": feat(), "java": feat()}
    busy_gateway = build_state({"gateway": feat(n=30), "java": None}, base)
    assert busy_gateway["java"] == {"status": "anomalous", "modes": ["silent"]}
    no_load_at_all = build_state({"gateway": None, "java": None}, base)
    assert no_load_at_all["java"]["status"] == "insufficient_data"  # nobody is asking: quiet is not failure


def test_a_frozen_service_is_recognised_by_its_silence_not_confused_with_a_dead_dependency():
    frozen = sig("java", "pause_container", sid="pause",
                 http={"java": stream(0, 12), "gateway": stream(0, 12) + stream(12, 27, latency=5000, status=504) + stream(27, 45),
                       CLIENT: stream(0, 12) + stream(12, 27, latency=5000, status=504) + stream(27, 45)})
    profile = signature_profile(frozen["signature"])
    assert profile["java"] == {"modes": ["silent"], "impact_pct": 100.0, "target": True}
    now = state(java="unknown", gateway="both")
    now["java"] = {"status": "anomalous", "modes": ["silent"]}
    top = predict(now, [CPP_STOP, frozen])[0]
    assert (top["likely_target"], top["likely_fault"]) == ("java", "pause_container")  # not cpp stop


def test_the_targets_own_degradation_is_part_of_the_pattern():
    http_error = sig("java", "http_error", params={"error_rate": 0.6}, sid="he",
                     http={"java": stream(0, 12) + stream(12, 27, status=503) + stream(27, 45),
                           "gateway": stream(0, 12) + stream(12, 27, status=502) + stream(27, 45),
                           CLIENT: stream(0, 12) + stream(12, 27, status=502) + stream(27, 45)})
    profile = signature_profile(http_error["signature"])
    assert profile["java"]["target"] is True and set(profile) == {"java", "gateway"}
    w = predict(state(java="errors", gateway="errors"), [http_error])[0]
    assert (w["likely_target"], w["likely_fault"]) == ("java", "http_error") and w["similarity"] == 1.0


def test_a_healthy_target_rules_out_the_fault_that_would_have_degraded_it():
    # cpu starvation on cpp degrades cpp itself; database latency leaves cpp healthy but looks the same upstream
    cpu = sig("cpp", "cpu_stress", sid="cpu", http={
        "cpp": stream(0, 12) + stream(12, 27, latency=400) + stream(27, 45),
        "java": stream(0, 12) + stream(12, 27, latency=900) + stream(27, 45),
        "gateway": stream(0, 12) + stream(12, 27, latency=910) + stream(27, 45),
        CLIENT: stream(0, 12) + stream(12, 27, latency=920) + stream(27, 45)})
    postgres_like = state(cpp="normal", java="latency", gateway="latency")
    assert match(postgres_like, cpu["signature"])["similarity"] < MIN_SIMILARITY
    top = predict(postgres_like, [cpu, PG_LATENCY])
    assert [w["likely_target"] for w in top] == ["postgres"]
    cpu_like = state(cpp="latency", java="latency", gateway="latency")
    assert predict(cpu_like, [cpu, PG_LATENCY])[0]["likely_target"] == "cpp"


def test_warnings_during_a_control_run_count_as_false_alarms():
    noisy = {"java": stream(0, 12) + stream(20, 24, status=503, latency=900) + stream(24, 45),
             "gateway": stream(0, 12) + stream(20, 24, status=502, latency=900) + stream(24, 45)}
    control = replay_experiment(experiment(dry_run=True), noisy, [CPP_STOP])
    assert control["false_alarm_steps"] > 0 and control["baseline_steps"] > control["fault_steps"] - 1
    s = summarize([control])
    assert s["false_alarm_rate"] > 0 and any("optimistic" in c for c in s["caveats"])
