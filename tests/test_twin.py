import pytest

from app.twin.model import TwinModel, fault_class, validate
from test_blast_radius import healthy, stream
from test_resilience import CLIENT, outage, sig, unaffected

EDGES = [("gateway", "java"), ("java", "cpp"), ("java", "postgres"), ("java", "kafka"), ("cpp", "kafka"),
         ("gateway", "kafka")]

CPP_STOP = sig("cpp", "stop_container", sid="cpp-stop")
KAFKA_STOP = sig("kafka", "stop_container", http={**unaffected(), "cpp": healthy()}, sid="kafka-stop")
PG_LATENCY = sig("postgres", "latency", sid="pg", http={
    "java": stream(0, 12) + stream(12, 27, latency=1200) + stream(27, 45),
    "gateway": stream(0, 12) + stream(12, 27, latency=1230) + stream(27, 45),
    CLIENT: stream(0, 12) + stream(12, 27, latency=1250) + stream(27, 45)})
ALL = [CPP_STOP, KAFKA_STOP, PG_LATENCY]


def model(signatures=None, edges=EDGES):
    return TwinModel(edges, signatures if signatures is not None else ALL)


def test_fault_types_map_to_classes():
    assert fault_class("stop_container") == fault_class("pause_container") == "outage"
    assert fault_class("latency") == "latency" and fault_class("cpu_stress") == "degradation"


def test_measured_results_are_aggregated_from_real_signatures_and_labelled():
    m = model().measured("cpp", "stop_container")
    assert m["basis"] == "measured" and m["runs"] == 1 and m["end_user_impact_pct"] == 100.0
    assert m["service_impact_pct"]["java"] == 100.0 and m["confidence"] == "low"
    assert model().measured("redis", "stop_container") is None


def test_coupling_is_learned_soft_for_kafka_and_hard_for_the_call_chain():
    twin = model()
    assert twin.coupling("java", "kafka", "outage") == {"value": 0.0, "runs": 1, "basis": "measured"}
    assert twin.coupling("gateway", "java", "outage")["value"] == 1.0
    assert twin.coupling("java", "cpp", "outage")["basis"] == "measured"


def test_unmeasured_edges_are_assumed_hard_and_the_assumption_is_reported():
    twin = model(edges=EDGES + [("java", "cache")])
    sim = twin.simulate("cache", "stop_container")
    assert sim["end_user_impact_pct"] == 100.0 and sim["basis"] == "simulated"
    assert any("java -> cache" in a and "assumed a HARD dependency" in a for a in sim["assumptions"])


def test_simulating_a_measured_combination_reproduces_the_propagation_chain():
    sim = model().simulate("cpp", "stop_container")
    assert sim["end_user_impact_pct"] == 100.0 and sim["affected_services"] == ["gateway", "java"]
    assert "gateway -> java" in sim["propagation"] and "java -> cpp" in sim["propagation"]


def test_a_soft_dependency_learned_from_measurement_does_not_propagate():
    sim = model().simulate("kafka", "stop_container")
    assert sim["end_user_impact_pct"] == 0.0 and sim["affected_services"] == []


def test_a_class_never_measured_for_an_edge_borrows_from_another_class_and_says_so():
    # postgres was only measured under latency; an outage reuses that coupling
    c = model().coupling("java", "postgres", "outage")
    assert c["basis"] == "measured_other_class" and c["value"] == 1.0


def test_queue_decouples_the_edge_and_reports_deferred_work_not_failed_requests():
    cmp = model().compare("cpp", "stop_container", [{"type": "queue", "source": "java", "target": "cpp"}])
    assert cmp["measured_baseline"]["end_user_impact_pct"] == 100.0           # real measurement
    assert cmp["simulated_baseline"]["end_user_impact_pct"] == 100.0
    after = cmp["simulated_with_change"]
    assert after["basis"] == "simulated" and after["end_user_impact_pct"] == 0.0
    assert any("deferred" in n for n in after["notes"]) and any("queue" in a for a in after["assumptions"])
    assert cmp["simulated_change_in_end_user_impact_pct"] == -100.0
    assert "SIMULATION" in cmp["disclaimer"] and "measure" in cmp["disclaimer"]


def test_fallback_scales_the_impact_by_the_miss_ratio():
    sim = model().simulate("cpp", "stop_container", [{"type": "fallback", "source": "java", "target": "cpp", "hit_ratio": 0.8}])
    assert sim["end_user_impact_pct"] == 20.0


def test_replicas_reduce_the_target_impact_and_state_the_assumption():
    sim = model().simulate("cpp", "stop_container", [{"type": "replicas", "service": "cpp", "count": 2}])
    assert sim["end_user_impact_pct"] == 50.0 and any("2 replicas" in a for a in sim["assumptions"])


def test_unknown_mutation_is_rejected_not_ignored():
    with pytest.raises(ValueError):
        model().simulate("cpp", "stop_container", [{"type": "magic"}])


def test_validation_hides_a_combination_and_exposes_where_structure_alone_is_wrong():
    v = validate(EDGES, ALL)
    rows = {(r["target"], r["fault_type"]): r for r in v["rows"]}
    assert v["combinations_tested"] == 3 and "leave-one-combination-out" in v["method"]
    # cpp was never measured under any other fault: assumed hard -> predicted 100, measured 100
    assert rows[("cpp", "stop_container")]["abs_error_pct"] == 0.0 and rows[("cpp", "stop_container")]["assumed_edges"]
    # kafka held out: nothing says it is soft, so the twin assumes hard -> 100 predicted vs 0 measured
    kafka = rows[("kafka", "stop_container")]
    assert kafka["predicted_end_user_impact_pct"] == 100.0 and kafka["measured_end_user_impact_pct"] == 0.0
    assert kafka["affected_class_correct"] is False
    assert v["mean_abs_error_pct"] is not None and len(v["caveats"]) >= 2


def test_validation_with_no_data_reports_none():
    v = validate(EDGES, [])
    assert v["combinations_tested"] == 0 and v["mean_abs_error_pct"] is None
