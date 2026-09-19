import pytest

from app.remediation.policy import (
    ALLOWLIST,
    COOLDOWN_S,
    PROTECTED_TARGETS,
    SUPPORTED,
    action_for_fault,
    evaluate,
)

REGISTRY = {
    "gateway": {"kind": "service", "container": "aegis-gateway"},
    "java-service": {"kind": "service", "container": "aegis-java-service"},
    "cpp-service": {"kind": "service", "container": "aegis-cpp-service"},
    "postgres": {"kind": "database", "container": "aegis-postgres"},
    "redis": {"kind": "cache", "container": "aegis-redis"},
    "kafka": {"kind": "broker", "container": "aegis-kafka"},
    "control-plane": {"kind": "service", "container": "aegis-api"},
    "telemetry-consumer": {"kind": "service", "container": "aegis-telemetry-consumer"},
    "unmapped": {"kind": "service", "container": None},
}
NOW = 10_000.0


def judge(action="RESTART_SERVICE", target="cpp-service", params=None, **kw):
    ctx = dict(active_experiment=False, running_remediation=False, recent_actions=[], now=NOW, approved=False)
    ctx.update(kw)
    return evaluate({"action": action, "target": target, "params": params or {}}, REGISTRY, **ctx)


def test_low_risk_restart_of_a_stateless_service_is_allowed():
    v = judge()
    assert v.verdict == "allow" and v.risk == "low"


def test_actions_outside_the_allowlist_are_denied_no_matter_what():
    for bad in ("DROP_DATABASE", "docker rm -f aegis-postgres", "rm -rf /", "restart_service", ""):
        v = judge(action=bad)
        assert v.verdict == "deny" and "allowlist" in v.reasons[0]


def test_allowlisted_actions_without_a_mechanism_are_reported_as_unsupported_not_faked():
    for action in set(ALLOWLIST) - set(SUPPORTED):
        v = judge(action=action)
        assert v.verdict == "unsupported" and action in v.reasons[0]


def test_unknown_protected_and_unmapped_targets_are_denied():
    assert judge(target="ghost").verdict == "deny"
    for name in PROTECTED_TARGETS:
        v = judge(target=name)
        assert v.verdict == "deny" and "protected" in v.reasons[0]
    assert "no container mapping" in judge(target="unmapped").reasons[0]


def test_restarting_stateful_components_needs_approval_and_is_allowed_once_approved():
    for target in ("postgres", "kafka", "redis"):
        v = judge(target=target)
        assert v.verdict == "needs_approval" and v.risk == "high", target
        assert judge(target=target, approved=True).verdict == "allow"


def test_scale_is_low_risk_even_on_a_database():
    assert judge(action="SCALE_SERVICE", target="postgres").verdict == "allow"


def test_clear_cache_is_restricted_to_the_cache_prefix_on_a_cache():
    assert judge(action="CLEAR_CACHE", target="redis").verdict == "allow"
    assert judge(action="CLEAR_CACHE", target="redis", params={"prefix": "cache:metrics"}).verdict == "allow"
    wide = judge(action="CLEAR_CACHE", target="redis", params={"prefix": "service:health:"})
    assert wide.verdict == "deny" and "cache:" in wide.reasons[0]
    assert judge(action="CLEAR_CACHE", target="redis", params={"prefix": ""}).verdict == "deny"
    assert judge(action="CLEAR_CACHE", target="cpp-service").verdict == "deny"


def test_remediation_is_blocked_during_an_experiment_and_while_another_runs():
    assert "experiment is active" in judge(active_experiment=True).reasons[0]
    assert "one at a time" in judge(running_remediation=True).reasons[0]


def test_cooldown_stops_restart_loops():
    recent = [{"target": "cpp-service", "ts": NOW - 60, "status": "VERIFICATION_FAILED"},
              {"target": "cpp-service", "ts": NOW - 200, "status": "VERIFIED"}]
    v = judge(recent_actions=recent)
    assert v.verdict == "deny" and "cooldown" in v.reasons[0]
    other_target = judge(target="java-service", recent_actions=recent)
    assert other_target.verdict == "allow"
    old = [{"target": "cpp-service", "ts": NOW - COOLDOWN_S - 1, "status": "VERIFIED"}] * 3
    assert judge(recent_actions=old).verdict == "allow"


@pytest.mark.parametrize("fault,action", [("stop_container", "RESTART_SERVICE"), ("pause_container", "RESTART_SERVICE"),
                                          ("http_error", "RESTART_SERVICE"), ("cpu_stress", "SCALE_SERVICE"),
                                          ("latency", None), ("memory_stress", None)])
def test_recognised_failure_maps_to_a_deterministic_remedy_or_none(fault, action):
    assert action_for_fault(fault) == action
