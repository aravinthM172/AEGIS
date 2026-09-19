from app.topology_graph import reconcile

DECLARED = [
    {"source": "gateway", "target": "java", "relation": "calls"},
    {"source": "java", "target": "cpp", "relation": "calls"},
    {"source": "java", "target": "postgres", "relation": "reads_writes"},
    {"source": "cpp", "target": "kafka", "relation": "produces"},
]
KNOWN = ["gateway", "java", "cpp", "postgres", "kafka"]


def obs(source, target, calls=5):
    return {"source": source, "target": target, "calls": calls}


def test_declared_call_edges_seen_in_telemetry_are_confirmed_with_stats():
    r = reconcile(DECLARED, [obs("gateway", "java", 7), obs("java", "cpp", 3)], KNOWN)
    assert [(c["source"], c["target"]) for c in r["confirmed"]] == [("gateway", "java"), ("java", "cpp")]
    assert r["confirmed"][0]["observed"]["calls"] == 7
    assert r["unobserved"] == [] and r["undeclared"] == []


def test_declared_call_edge_with_no_calls_is_unobserved():
    r = reconcile(DECLARED, [obs("gateway", "java")], KNOWN)
    assert [(u["source"], u["target"]) for u in r["unobserved"]] == [("java", "cpp")]


def test_uninstrumented_relations_are_never_reported_as_unobserved():
    r = reconcile(DECLARED, [], KNOWN)
    assert {(n["source"], n["target"]) for n in r["not_instrumented"]} == {("java", "postgres"), ("cpp", "kafka")}
    assert all(u["relation"] == "calls" for u in r["unobserved"])
    assert r["summary"] == {"confirmed": 0, "unobserved": 2, "not_instrumented": 2, "undeclared": 0}


def test_observed_but_undeclared_edge_is_drift():
    r = reconcile(DECLARED, [obs("gateway", "java"), obs("gateway", "cpp", 2)], KNOWN)
    assert [(u["source"], u["target"]) for u in r["undeclared"]] == [("gateway", "cpp")]
    assert r["undeclared"][0]["unknown_services"] == []


def test_undeclared_edge_to_unknown_service_is_flagged():
    r = reconcile(DECLARED, [obs("java", "llm-service")], KNOWN)
    assert r["undeclared"][0]["unknown_services"] == ["llm-service"]


def test_observation_confirms_edge_even_if_declared_relation_is_not_observable():
    # a real call between two services declared as e.g. produces still proves the dependency exists
    r = reconcile(DECLARED, [obs("cpp", "kafka")], KNOWN)
    assert [(c["source"], c["target"]) for c in r["confirmed"]] == [("cpp", "kafka")]
    assert ("cpp", "kafka") not in {(n["source"], n["target"]) for n in r["not_instrumented"]}
