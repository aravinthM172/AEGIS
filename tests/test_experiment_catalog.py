from app.experiments.catalog import FAULTS, PROTECTED_TARGETS, catalog_as_dict, validate_request

SERVICES = {
    "java-service": {"kind": "service", "container": "aegis-java-service"},
    "postgres": {"kind": "database", "container": "aegis-postgres"},
    "control-plane": {"kind": "service", "container": "aegis-api"},
    "telemetry-consumer": {"kind": "service", "container": "aegis-telemetry-consumer"},
    "orphan": {"kind": "service", "container": None},
}


def check(**overrides):
    args = dict(target="java-service", fault_type="stop_container", parameters={},
                duration_s=30, baseline_s=10, recovery_s=15, dry_run=True, services=SERVICES)
    args.update(overrides)
    return validate_request(**args)


def test_valid_request_has_no_errors():
    errors, params = check()
    assert errors == [] and params == {}


def test_protected_targets_are_rejected():
    for name in PROTECTED_TARGETS:
        errors, _ = check(target=name)
        assert any("protected" in e for e in errors), name


def test_unknown_target_and_unmapped_container_rejected():
    assert any("unknown target" in e for e in check(target="ghost")[0])
    assert any("no container mapping" in e for e in check(target="orphan")[0])


def test_unknown_fault_rejected():
    errors, _ = check(fault_type="meteor")
    assert any("unknown fault_type" in e for e in errors)


def test_fault_kind_restriction():
    errors, _ = check(target="postgres", fault_type="http_error", parameters={"error_rate": 0.5})
    assert any("cannot target a database" in e for e in errors)


def test_parameters_required_typed_bounded_and_defaulted():
    assert any("is required" in e for e in check(fault_type="latency")[0])
    assert any("between 10 and 10000" in e for e in check(fault_type="latency", parameters={"latency_ms": 5})[0])
    assert any("must be a int" in e for e in check(fault_type="latency", parameters={"latency_ms": "800"})[0])
    assert any("must be a int" in e for e in check(fault_type="latency", parameters={"latency_ms": True})[0])
    assert any("unexpected parameter" in e for e in check(fault_type="latency", parameters={"latency_ms": 800, "foo": 1})[0])
    errors, params = check(fault_type="latency", parameters={"latency_ms": 800})
    assert errors == [] and params == {"latency_ms": 800, "jitter_ms": 0}


def test_timing_limits():
    assert any("duration_s" in e for e in check(duration_s=1)[0])
    assert any("duration_s" in e for e in check(duration_s=301)[0])
    assert any("baseline_s" in e for e in check(baseline_s=-1)[0])
    assert any("recovery_s" in e for e in check(recovery_s=4)[0])
    assert check(baseline_s=0)[0] == []


def test_real_run_rejected_while_fault_has_no_injector():
    assert not any(f.implemented for f in FAULTS.values())
    errors, _ = check(dry_run=False)
    assert any("no real injector" in e for e in errors)
    assert check(dry_run=True)[0] == []


def test_catalog_lists_every_fault_with_implemented_flag():
    cat = catalog_as_dict()
    assert {f["fault_type"] for f in cat["faults"]} == set(FAULTS)
    assert all("implemented" in f for f in cat["faults"])
