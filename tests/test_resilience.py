from app.analysis.blast_radius import analyze as analyze_raw
from app.analysis.resilience import END_USERS, build_resilience, confidence, level, rank_criticality
from app.analysis.signatures import build_signature, degraded_mode
from test_blast_radius import EDGES, TIMELINE, healthy, stream

CLIENT = "workload-client"


def analysed(target="cpp", fault="stop_container", dry_run=False, http=None, params=None, workload_n=100000):
    exp = {"target": target, "fault_type": fault, "dry_run": dry_run, "timeline": dict(TIMELINE)}
    result = analyze_raw(experiment=exp, http_events=http, call_events=[], declared_edges=EDGES,
                         entry_service=CLIENT, client_services=frozenset({CLIENT}))
    meta = {"target": target, "fault_type": fault, "parameters": params or {}, "duration_s": 15,
            "workload_rps": 4, "workload_n": workload_n, "dry_run": dry_run}
    return meta, result


def outage(status=503, latency=1000):
    """A target outage: java and gateway and the end user all fail in the fault window."""
    def s(status_, latency_):
        return stream(0, 12) + stream(12, 27, latency=latency_, status=status_) + stream(27, 45)
    return {"java": s(status, latency), "gateway": s(502, latency), CLIENT: s(502, latency)}


def unaffected():
    return {"java": healthy(), "gateway": healthy(), CLIENT: healthy()}


def sig(target="cpp", fault="stop_container", http=None, dry_run=False, params=None, sid="s", workload_n=100000):
    meta, result = analysed(target, fault, dry_run, http if http is not None else outage(), params, workload_n)
    return {"id": sid, "signature": build_signature(meta, result)}


def test_signature_captures_trigger_impact_modes_paths_and_recovery():
    s = sig()["signature"]
    assert s["kind"] == "fault" and s["basis"] == "measured"
    assert s["trigger"] == {"target": "cpp", "fault_type": "stop_container", "parameters": {}, "duration_s": 15,
                            "workload_rps": 4, "workload_n": 100000}
    java = s["observed"]["services"]["java"]
    assert java["impact_pct"] == 100.0 and java["degraded_mode"] == "errors+latency" and java["role"] == "direct"
    assert s["observed"]["end_user"]["impact_pct"] == 100.0
    assert s["observed"]["propagation_paths"] == ["cpp -> java -> gateway"]
    assert s["observed"]["recovery"]["status"] == "recovered"


def test_fingerprint_is_stable_for_the_same_failure_shape_and_differs_otherwise():
    a, b = sig()["signature"], sig()["signature"]
    assert a["fingerprint"] == b["fingerprint"]
    assert sig(fault="pause_container")["signature"]["fingerprint"] != a["fingerprint"]
    assert sig(http=unaffected())["signature"]["fingerprint"] != a["fingerprint"]


def test_dry_run_produces_a_control_signature_and_failed_analysis_produces_none():
    assert sig(dry_run=True, http=unaffected())["signature"]["kind"] == "control"
    meta = {"target": "cpp", "fault_type": "x", "parameters": {}}
    assert build_signature(meta, {"status": "not_analyzable"}) is None


def test_degraded_mode_classification():
    def entry(err, ratio, status="affected"):
        return {"status": status, "fault": {"error_pct": err}, "p95_ratio": ratio}
    assert degraded_mode(entry(60, 1.0)) == "errors"
    assert degraded_mode(entry(0, 40)) == "latency"
    assert degraded_mode(entry(100, 50)) == "errors+latency"
    assert degraded_mode(entry(0, 1.1)) == "none"
    assert degraded_mode(entry(0, 1, "insufficient_data")) == "unknown"
    assert degraded_mode(None) == "unknown"


def test_score_level_and_confidence_thresholds():
    assert [level(s) for s in (100, 80, 79.9, 40, 39.9, 0, None)] == ["HIGH", "HIGH", "MEDIUM", "MEDIUM", "LOW", "LOW", None]
    assert [confidence(n) for n in (1, 2, 3, 4, 5, 9)] == ["low", "low", "medium", "medium", "high", "high"]


def test_profile_scores_come_from_measured_impact_and_name_the_critical_dependency():
    sigs = [sig("cpp", "stop_container", sid="a"), sig("kafka", "stop_container", http=unaffected(), sid="b")]
    r = build_resilience(sigs, {"gateway": {"java", "cpp", "postgres", "kafka"}})
    gateway = r["services"]["gateway"]
    assert gateway["worst_case_score"] == 0.0 and gateway["level"] == "LOW"
    assert gateway["critical_dependency"]["service"] == "cpp"
    by_dep = {f["dependency"]: f for f in gateway["faults"]}
    assert by_dep["kafka"]["resilience_score"] == 100.0 and by_dep["kafka"]["level"] == "HIGH"
    assert by_dep["cpp"]["degraded_mode"] == "errors+latency"
    assert gateway["confidence"] == "low"  # one run per combination
    assert gateway["untested_dependencies"] == ["java", "postgres"]  # never scored, only listed


def test_repeated_runs_are_aggregated_with_median_and_range_and_raise_confidence():
    def partial(rate_bad):  # fraction of failing requests during the fault
        bad = stream(12, 27, status=503, latency=900)
        good = stream(12, 27)
        mixed = [b if i % 10 < rate_bad else g for i, (b, g) in enumerate(zip(bad, good))]
        return {"java": stream(0, 12) + mixed + stream(27, 45), "gateway": healthy(), CLIENT: healthy()}
    sigs = [sig(http=partial(k), sid=f"r{k}") for k in (10, 8, 9)]
    java = build_resilience(sigs)["services"]["java"]["faults"][0]
    assert java["runs"] == 3 and java["confidence"] == "medium"
    assert java["impact_pct"]["min"] < java["impact_pct"]["median"] <= java["impact_pct"]["max"] == 100.0
    assert java["resilience_score"] == round(100.0 - java["impact_pct"]["median"], 1)


def test_different_fault_parameters_are_not_averaged_together():
    a = sig("postgres", "latency", params={"latency_ms": 100}, sid="a")
    b = sig("postgres", "latency", params={"latency_ms": 900}, sid="b")
    faults = build_resilience([a, b])["services"]["java"]["faults"]
    assert sorted(f["parameters"]["latency_ms"] for f in faults) == [100, 900]
    assert all(f["runs"] == 1 for f in faults)


def test_services_without_enough_data_are_not_scored():
    sparse = {"java": [{"ts": 15.0, "latency_ms": 900.0, "status": 503}], "gateway": healthy(), CLIENT: healthy()}
    r = build_resilience([sig(http=sparse)])
    assert "java" not in r["services"]  # too little traffic in the fault window: unknown, not zero


def test_end_users_get_a_system_level_profile():
    r = build_resilience([sig()])
    eu = r["services"][END_USERS]
    assert eu["worst_case_score"] == 0.0 and eu["critical_dependency"]["service"] == "cpp"


def test_target_and_client_are_never_scored_as_dependents():
    r = build_resilience([sig()])
    assert "cpp" not in r["services"] and CLIENT not in r["services"]


def test_noise_floor_comes_from_control_runs_only():
    assert build_resilience([sig()])["noise_floor"]["controls"] == 0
    control = sig(dry_run=True, http=unaffected(), sid="c")
    floor = build_resilience([sig(), control])["noise_floor"]
    assert floor["controls"] == 1 and floor["max_impact_pct"] == 0.0


def test_services_never_measured_are_reported_as_such():
    r = build_resilience([], {"gateway": {"java"}})
    assert r["services"]["gateway"]["worst_case_score"] is None
    assert r["services"]["gateway"]["untested_dependencies"] == ["java"]
    assert r["noise_floor"]["note"]


def test_criticality_ranks_targets_by_measured_end_user_impact():
    sigs = [sig("cpp", sid="a"), sig("kafka", "stop_container", http=unaffected(), sid="b"),
            sig("redis", "pause_container", http=unaffected(), sid="c")]
    ranking = rank_criticality(sigs)
    assert [r["target"] for r in ranking] == ["cpp", "kafka", "redis"]
    assert ranking[0]["end_user_impact_pct"]["max"] == 100.0 and ranking[0]["rank"] == 1
    assert ranking[1]["end_user_impact_pct"]["max"] == 0.0
    assert ranking[0]["propagation_paths"] == ["cpp -> java -> gateway"]


def test_results_measured_under_different_load_are_never_averaged_together():
    light = sig(http=unaffected(), workload_n=100_000, sid="light")   # cpu stress invisible to light requests
    heavy = sig(http=outage(), workload_n=2_000_000, sid="heavy")     # ... but crushing for heavy ones
    faults = build_resilience([light, heavy])["services"]["java"]["faults"]
    assert sorted(f["conditions"]["workload_n"] for f in faults) == [100_000, 2_000_000]
    assert sorted(f["resilience_score"] for f in faults) == [0.0, 100.0]  # not a meaningless 50


def test_a_service_is_only_scored_against_faults_on_things_it_depends_on():
    # cpp does not depend on java: being unaffected by a java fault says nothing about cpp's resilience
    deps = {"gateway": {"java", "cpp"}, "java": {"cpp"}, "cpp": set()}
    http = {"cpp": healthy(), "gateway": healthy(), CLIENT: healthy(), "java": healthy()}
    r = build_resilience([sig("java", "http_error", http=http, params={"error_rate": 0.5})], deps)
    assert "cpp" not in r["services"] or r["services"]["cpp"]["worst_case_score"] is None
    assert "gateway" in r["services"]  # gateway does depend on java
    # ... but an affected service is always kept, even if the graph did not predict it
    affected_http = {**http, "cpp": stream(0, 12) + stream(12, 27, status=500) + stream(27, 45)}
    r2 = build_resilience([sig("java", "http_error", http=affected_http, params={"error_rate": 0.5})], deps)
    assert r2["services"]["cpp"]["worst_case_score"] == 0.0


def test_measurement_plane_is_reported_as_excluded_not_unmeasured():
    r = build_resilience([], {"control-plane": {"postgres", "kafka"}, "gateway": {"java"}},
                         excluded=frozenset({"control-plane"}))
    assert "measurement plane" in r["services"]["control-plane"]["note"]
    assert r["services"]["control-plane"]["untested_dependencies"] == []
    assert "no experiment" in r["services"]["gateway"]["note"]
