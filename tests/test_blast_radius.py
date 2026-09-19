from app.analysis.blast_radius import analyze as _analyze, percentile

TIMELINE = {  # epoch-like seconds
    "baseline_started_at": 0.0, "inject_started_at": 10.0, "fault_applied_at": 12.0,
    "rollback_started_at": 27.0, "inject_ended_at": 28.0, "finished_at": 45.0,
}
EDGES = [("java", "cpp"), ("gateway", "java"), ("java", "postgres"), ("cpp", "kafka")]


def analyze(exp, http_events, call_events, edges, entry_service=None):
    return _analyze(experiment=exp, http_events=http_events, call_events=call_events,
                    declared_edges=edges, entry_service=entry_service)


def stream(start, end, rps=4, latency=20.0, status=200):
    n = int((end - start) * rps)
    return [{"ts": start + i / rps, "latency_ms": latency, "status": status} for i in range(n)]


def healthy(lo=0.0, hi=45.0):
    return stream(lo, hi)


def experiment(target="cpp", fault="stop_container", dry_run=False, **timeline):
    return {"target": target, "fault_type": fault, "dry_run": dry_run, "timeline": {**TIMELINE, **timeline}}


def run(events, target="cpp", **kw):
    return analyze(experiment(target=target, **kw.pop("timeline", {})), events, kw.pop("calls", []),
                   kw.pop("edges", EDGES), entry_service="gateway")


def failing_chain():
    """cpp down 12..27 -> java and gateway return 502; healthy again ~31s."""
    java = stream(0, 12) + stream(12, 27, latency=1000, status=503) + stream(27, 31, latency=900, status=503) + stream(31, 45)
    gateway = stream(0, 12) + stream(12, 27, latency=1010, status=502) + stream(27, 31, latency=950, status=502) + stream(31, 45)
    return {"java": java, "gateway": gateway}


def test_percentile_interpolates():
    assert percentile([10, 20, 30, 40], 0.5) == 25
    assert percentile([5], 0.95) == 5
    assert percentile([], 0.5) is None


def test_failure_propagates_direct_then_indirect_with_path():
    r = run(failing_chain())
    assert r["status"] == "analyzed" and r["basis"] == "measured"
    assert r["affected_services"] == ["java", "gateway"]
    assert r["direct"] == ["java"] and r["indirect"] == ["gateway"]
    assert r["services"]["java"]["impact_pct"] == 100.0
    assert r["services"]["java"]["fault"]["error_pct"] == 100.0
    assert r["propagation"]["paths"] == ["cpp -> java -> gateway"]
    assert r["entry_impact"]["service"] == "gateway" and r["entry_impact"]["impact_pct"] == 100.0
    assert r["affected_requests"] == r["services"]["java"]["fault"]["degraded"] + r["services"]["gateway"]["fault"]["degraded"]


def test_recovery_time_is_last_degraded_request_after_rollback():
    r = run(failing_chain())
    java = r["recovery"]["per_service"]["java"]
    # degraded until ~30.75s, rollback at 27 -> ~3.75s
    assert java["status"] == "recovered" and 3.0 <= java["recovery_s"] <= 4.0
    assert r["recovery"]["status"] == "recovered"
    assert r["recovery"]["restore_operation_s"] == 1.0


def test_latency_amplification_without_any_error_is_detected():
    java = stream(0, 12) + stream(12, 27, latency=1200) + stream(27, 45)
    gateway = stream(0, 12) + stream(12, 27, latency=1230) + stream(27, 45)
    r = analyze(experiment(target="postgres", fault="latency"), {"java": java, "gateway": gateway}, [], EDGES,
                entry_service="gateway")
    assert r["services"]["java"]["fault"]["error_pct"] == 0.0
    assert r["services"]["java"]["status"] == "affected" and r["services"]["java"]["impact_pct"] == 100.0
    assert r["services"]["java"]["p95_ratio"] > 50
    assert r["affected_services"] == ["java", "gateway"]


def test_structurally_at_risk_but_unaffected_service_is_reported_as_resilient():
    # kafka down: cpp declares a dependency on kafka but keeps serving normally
    r = analyze(experiment(target="kafka"), {"cpp": healthy(), "gateway": healthy()}, [], EDGES, entry_service="gateway")
    assert r["affected_services"] == []
    # transitive: java depends on cpp and gateway on java, so all three are structurally at risk
    assert r["structural"]["at_risk"] == ["cpp", "java", "gateway"]
    assert r["structural"]["at_risk_but_unaffected"] == ["cpp", "gateway"]  # measured healthy
    assert r["structural"]["at_risk_but_not_measurable"] == ["java"]        # no telemetry given for it
    assert r["recovery"]["status"] == "nothing_to_recover"


def test_service_with_too_little_traffic_is_insufficient_not_guessed():
    sparse = [{"ts": 15.0, "latency_ms": 5000.0, "status": 503}, {"ts": 20.0, "latency_ms": 5000.0, "status": 503}]
    r = run({"java": sparse, "gateway": healthy()})
    assert r["services"]["java"]["status"] == "insufficient_data"
    assert r["services"]["java"]["impact_pct"] is None
    assert "java" not in r["affected_services"]
    assert "java" in r["structural"]["at_risk_but_not_measurable"]


def test_affected_service_that_the_graph_did_not_predict_is_flagged():
    other = stream(0, 12) + stream(12, 27, status=500) + stream(27, 45)
    r = analyze(experiment(target="cpp"), {"mystery": other, "gateway": healthy()}, [], EDGES, entry_service="gateway")
    assert r["structural"]["affected_but_not_predicted"] == ["mystery"]
    assert r["services"]["mystery"]["role"] == "unexplained"


def test_dry_run_is_labelled_a_control_measurement():
    r = analyze(experiment(dry_run=True), {"java": healthy(), "gateway": healthy()}, [], EDGES, entry_service="gateway")
    assert r["dry_run"] is True and r["affected_services"] == []
    assert any("CONTROL" in w for w in r["quality"]["warnings"])


def test_service_not_recovered_within_the_observation_window():
    java = stream(0, 12) + stream(12, 45, status=503, latency=900)
    r = analyze(experiment(), {"java": java, "gateway": healthy()}, [], EDGES, entry_service="gateway")
    assert r["recovery"]["per_service"]["java"]["status"] == "not_recovered_in_window"
    assert r["recovery"]["status"] == "not_recovered_in_window"


def test_experiment_that_never_reached_the_fault_is_not_analyzable():
    r = analyze(experiment(fault_applied_at=None, rollback_started_at=None), {}, [], EDGES)
    assert r["status"] == "not_analyzable" and "fault_applied_at" in r["reason"]


def test_short_baseline_produces_a_warning():
    r = analyze(experiment(inject_started_at=3.0), {"java": healthy(), "gateway": healthy()}, [], EDGES)
    assert any("baseline is only" in w for w in r["quality"]["warnings"])


def test_observed_calls_are_evidence_and_failed_calls_are_counted():
    calls = [{"ts": 15.0 + i, "source": "java", "target": "cpp", "latency_ms": 900, "status": 503,
              "error_type": "connection_error"} for i in range(5)]
    r = run(failing_chain(), calls=calls)
    edge = next(e for e in r["propagation"]["edges"] if e["source"] == "java" and e["target"] == "cpp")
    assert edge["evidence"] == "observed" and edge["calls_in_fault_window"] == 5 and edge["failed_calls"] == 5
    declared_only = next(e for e in r["propagation"]["edges"] if e["source"] == "gateway")
    assert declared_only["evidence"] == "declared"


def test_client_observer_is_reported_as_end_user_impact_not_as_an_affected_service():
    events = {**failing_chain(), "workload-client": stream(0, 12) + stream(12, 27, latency=1100, status=502) + stream(27, 45)}
    r = _analyze(experiment=experiment(), http_events=events, call_events=[], declared_edges=EDGES,
                 entry_service="workload-client", client_services=frozenset({"workload-client"}))
    assert r["services"]["workload-client"]["role"] == "client"
    assert r["services"]["workload-client"]["status"] == "affected"
    assert "workload-client" not in r["affected_services"]
    assert r["structural"]["affected_but_not_predicted"] == []  # the observer is not a mystery service
    assert r["entry_impact"]["service"] == "workload-client" and r["entry_impact"]["impact_pct"] == 100.0


def test_client_sees_no_impact_even_when_service_telemetry_went_blind():
    # kafka down: gateway telemetry is lost during the fault, but the synthetic user saw nothing wrong
    events = {"gateway": stream(0, 12) + stream(27, 45), "workload-client": healthy()}
    r = _analyze(experiment=experiment(target="kafka"), http_events=events, call_events=[], declared_edges=EDGES,
                 entry_service="workload-client", client_services=frozenset({"workload-client"}))
    assert r["services"]["gateway"]["status"] == "insufficient_data"
    assert r["entry_impact"]["status"] == "unaffected" and r["entry_impact"]["degraded_requests"] == 0
    assert any("gateway: no telemetry during the fault window" in w for w in r["quality"]["warnings"])


def test_the_target_itself_is_labelled_target_not_unexplained():
    # http_error on java: java has telemetry and is degraded, gateway (its caller) too
    java = stream(0, 12) + stream(12, 27, status=503) + stream(27, 45)
    gateway = stream(0, 12) + stream(12, 27, status=502) + stream(27, 45)
    r = analyze(experiment(target="java", fault="http_error"), {"java": java, "gateway": gateway}, [], EDGES,
                entry_service="gateway")
    assert r["services"]["java"]["role"] == "target"
    assert r["target_impact"]["service"] == "java" and r["target_impact"]["impact_pct"] == 100.0
    assert r["affected_services"] == ["gateway"] and r["direct"] == ["gateway"]
    assert r["structural"]["affected_but_not_predicted"] == []
    assert r["propagation"]["paths"] == ["java -> gateway"]
