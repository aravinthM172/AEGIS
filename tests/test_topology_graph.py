import os

import pytest

from app.topology_graph import (
    TopologyError,
    dependencies,
    dependents,
    find_cycle,
    load_file,
    impact_ranking,
    validate,
)

# The spec's example chain: api -> orders -> redis, api -> payment -> postgres -> (nothing)
NODES = ["api", "orders", "payment", "redis", "postgres"]
EDGES = [
    ("api", "orders"),
    ("api", "payment"),
    ("orders", "redis"),
    ("payment", "postgres"),
]


def test_dependents_is_structural_blast_radius():
    assert dependents("postgres", EDGES) == {"payment": 1, "api": 2}
    assert dependents("redis", EDGES) == {"orders": 1, "api": 2}
    assert dependents("api", EDGES) == {}


def test_dependencies_are_transitive_with_hop_counts():
    assert dependencies("api", EDGES) == {"orders": 1, "payment": 1, "redis": 2, "postgres": 2}
    assert dependencies("postgres", EDGES) == {}


def test_diamond_counts_each_node_once_at_shortest_distance():
    edges = [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")]
    assert dependents("d", edges) == {"b": 1, "c": 1, "a": 2}


def test_impact_ranking_orders_by_transitive_dependents():
    ranking = impact_ranking(NODES, EDGES)
    # postgres and redis each have 2 transitive dependents (tie -> alphabetical), then orders/payment (1), api (0)
    assert [r["service"] for r in ranking] == ["postgres", "redis", "orders", "payment", "api"]
    top = {r["service"]: r for r in ranking}
    assert top["postgres"]["transitive_dependents"] == ["api", "payment"]
    assert top["postgres"]["transitive_count"] == 2
    assert top["postgres"]["max_depth"] == 2
    assert top["api"]["transitive_count"] == 0


def test_impact_ranking_is_deterministic_on_ties():
    ranking = impact_ranking(NODES, EDGES)
    counts = [(r["transitive_count"], r["service"]) for r in ranking]
    assert counts == sorted(counts, key=lambda c: (-c[0], c[1]))


def test_validate_accepts_valid_graph():
    validate(NODES, EDGES)


def test_validate_rejects_unknown_endpoint():
    with pytest.raises(TopologyError, match="unknown target 'ghost'"):
        validate(NODES, EDGES + [("api", "ghost")])
    with pytest.raises(TopologyError, match="unknown source 'ghost'"):
        validate(NODES, EDGES + [("ghost", "api")])


def test_validate_rejects_self_dependency_and_duplicates():
    with pytest.raises(TopologyError, match="self-dependency"):
        validate(NODES, [("api", "api")])
    with pytest.raises(TopologyError, match="duplicate edge"):
        validate(NODES, EDGES + [("api", "orders")])


def test_validate_reports_cycle_path():
    edges = [("a", "b"), ("b", "c"), ("c", "a")]
    with pytest.raises(TopologyError, match=r"dependency cycle: a -> b -> c -> a"):
        validate(["a", "b", "c"], edges)


def test_find_cycle_none_for_dag():
    assert find_cycle(NODES, EDGES) is None


def test_repository_topology_file_is_valid_and_has_evidence():
    path = os.path.join(os.path.dirname(__file__), "..", "config", "topology.json")
    services, deps = load_file(path)
    assert {s["name"] for s in services} >= {"control-plane", "java-service", "cpp-service", "postgres", "redis", "kafka"}
    assert all(d["evidence"] for d in deps)


def test_load_file_requires_evidence(tmp_path):
    f = tmp_path / "t.json"
    f.write_text('{"services":[{"name":"a","kind":"service"},{"name":"b","kind":"database"}],'
                 '"dependencies":[{"source":"a","target":"b","relation":"reads_writes"}]}')
    with pytest.raises(TopologyError, match="evidence is mandatory"):
        load_file(str(f))


def test_repository_topology_has_the_workload_call_chain():
    path = os.path.join(os.path.dirname(__file__), "..", "config", "topology.json")
    _, deps = load_file(path)
    edges = [(d["source"], d["target"]) for d in deps]
    # gateway -> java-service -> cpp-service: a failure in cpp-service must propagate two hops up
    assert dependents("cpp-service", edges) == {"java-service": 1, "gateway": 2}
    assert dependents("java-service", edges) == {"gateway": 1}
    assert dependencies("gateway", edges)["cpp-service"] == 2
    # postgres reaches the gateway only through java-service
    assert dependents("postgres", edges)["gateway"] == 2
