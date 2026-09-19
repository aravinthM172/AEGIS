import time
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.experiments.models import ExperimentRow
from app.models import ServiceRow
from app.remediation.auth import authenticate, load_keys
from app.remediation.engine import InvalidState, NotFound, RemediationEngine, RemediationError
from app.remediation.executor import CacheExecutor, ExecutionError
from app.remediation.models import AuditLogRow, RemediationRow
from app.remediation.verification import verify_recovery


class FakeExecutor:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def execute(self, action, service, params):
        self.calls.append((action, service["container"], params))
        if self.fail:
            raise ExecutionError("agent refused")
        return {"action": action, "ok": True}


def probe_factory(good_after_calls=0):
    state = {"calls": 0}

    def probe(n):
        state["calls"] += 1
        healthy = state["calls"] > good_after_calls
        return [{"ok": healthy, "latency_ms": 25.0, "status": 200 if healthy else 502} for _ in range(n)]
    return probe, state


@pytest.fixture()
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'r.db'}", connect_args={"check_same_thread": False, "timeout": 15})
    for model in (ServiceRow, ExperimentRow, RemediationRow, AuditLogRow):
        model.__table__.create(engine)
    fac = sessionmaker(bind=engine)
    with fac() as db:
        for name, kind in (("cpp-service", "service"), ("java-service", "service"), ("postgres", "database"),
                           ("redis", "cache"), ("control-plane", "service")):
            db.add(ServiceRow(name=name, kind=kind, container=f"aegis-{name}"))
        db.commit()
    return fac


def make(factory, executor=None, good_after=0, max_wait=1.0):
    probe, state = probe_factory(good_after)
    engine = RemediationEngine(
        executor or FakeExecutor(), probe, factory, reference_p95=lambda _f: 20.0,
        verify=lambda p, ref, **kw: verify_recovery(p, ref, settle_s=0, interval_s=0.01, max_wait_s=max_wait,
                                                    probes_per_round=10, sleep=lambda s: time.sleep(0.005)))
    return engine, state


def wait_for(engine, action_id, statuses, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        row = engine.get(action_id)
        if row["status"] in statuses:
            return row
        time.sleep(0.02)
    raise AssertionError(f"stuck: {engine.get(action_id)['status']}")


def events(engine):
    return [e["event"] for e in reversed(engine.audit_log())]


def test_dry_run_plans_and_executes_nothing(factory):
    ex = FakeExecutor()
    engine, _ = make(factory, ex)
    row = engine.request(action="RESTART_SERVICE", target="cpp-service", requested_by="op")
    assert row["status"] == "PLANNED" and row["mode"] == "dry_run" and row["policy"]["verdict"] == "allow"
    assert ex.calls == []


def test_allowed_action_executes_then_verifies_recovery_with_measured_time(factory):
    ex = FakeExecutor()
    engine, _ = make(factory, ex)
    row = engine.request(action="RESTART_SERVICE", target="cpp-service", mode="execute", requested_by="op")
    done = wait_for(engine, row["id"], ("VERIFIED", "VERIFICATION_FAILED", "FAILED"))
    assert done["status"] == "VERIFIED" and done["verification"]["verified"]
    assert done["verification"]["recovery_time_s"] is not None
    assert ex.calls == [("RESTART_SERVICE", "aegis-cpp-service", {})]
    assert events(engine) == ["requested", "policy_verdict", "executed", "verification"]


def test_recovery_that_never_happens_is_reported_as_verification_failed_not_success(factory):
    engine, _ = make(factory, good_after=10_000, max_wait=0.3)
    row = engine.request(action="RESTART_SERVICE", target="cpp-service", mode="execute", requested_by="op")
    done = wait_for(engine, row["id"], ("VERIFIED", "VERIFICATION_FAILED"))
    assert done["status"] == "VERIFICATION_FAILED" and "not healthy" in done["verification"]["reason"]


def test_a_failed_verification_tries_the_next_approved_strategy_through_the_policy_again(factory):
    ex = FakeExecutor()
    engine, state = make(factory, ex, good_after=100_000, max_wait=0.3)
    first = engine.request(action="RESTART_SERVICE", target="cpp-service", mode="execute", requested_by="op",
                           strategies=["SCALE_SERVICE"])
    wait_for(engine, first["id"], ("VERIFICATION_FAILED",))
    deadline = time.monotonic() + 8
    child = None
    while time.monotonic() < deadline and child is None:
        child = next((a for a in engine.list_actions() if a["parent_id"] == first["id"]), None)
        time.sleep(0.02)
    assert child and child["action"] == "SCALE_SERVICE" and child["requested_by"] == "system:next-strategy"
    # the cooldown allows 2 actions on a target, so the follow-up is allowed; both went through policy
    assert child["policy"]["verdict"] == "allow" and child["mode"] == "execute"


def test_executor_errors_mark_the_action_failed(factory):
    engine, _ = make(factory, FakeExecutor(fail=True))
    row = engine.request(action="RESTART_SERVICE", target="cpp-service", mode="execute", requested_by="op")
    done = wait_for(engine, row["id"], ("FAILED",))
    assert "agent refused" in done["error"] and "execution_failed" in events(engine)


def test_stateful_restart_waits_for_admin_approval_then_runs(factory):
    ex = FakeExecutor()
    engine, _ = make(factory, ex)
    row = engine.request(action="RESTART_SERVICE", target="postgres", mode="execute", requested_by="op")
    assert row["status"] == "PENDING_APPROVAL" and row["policy"]["risk"] == "high" and ex.calls == []
    approved = engine.approve(row["id"], "admin:abcd")
    done = wait_for(engine, row["id"], ("VERIFIED", "VERIFICATION_FAILED"))
    assert approved["approved_by"] == "admin:abcd" and ex.calls and done["status"] == "VERIFIED"
    assert "approved" in events(engine)


def test_rejected_or_unapprovable_actions_never_run(factory):
    ex = FakeExecutor()
    engine, _ = make(factory, ex)
    row = engine.request(action="RESTART_SERVICE", target="postgres", mode="execute", requested_by="op")
    assert engine.reject(row["id"], "admin:abcd")["status"] == "REJECTED"
    with pytest.raises(InvalidState):
        engine.approve(row["id"], "admin:abcd")
    with pytest.raises(NotFound):
        engine.approve("nope", "admin:abcd")
    assert ex.calls == []


def test_approval_is_re_checked_against_current_conditions(factory):
    ex = FakeExecutor()
    engine, _ = make(factory, ex)
    row = engine.request(action="RESTART_SERVICE", target="postgres", mode="execute", requested_by="op")
    with factory() as db:  # an experiment starts while the request waits for approval
        db.add(ExperimentRow(id=str(uuid.uuid4()), target="cpp-service", fault_type="stop_container", parameters={},
                             duration_s=10, baseline_s=0, recovery_s=5, dry_run=True, status="RUNNING",
                             abort_requested=False, active_slot=True))
        db.commit()
    result = engine.approve(row["id"], "admin:abcd")
    assert result["status"] == "DENIED" and "experiment is active" in result["policy"]["reasons"][0] and ex.calls == []
    assert "approval_denied_by_policy" in events(engine)


@pytest.mark.parametrize("action,target,expected", [
    ("DROP_DATABASE", "postgres", "DENIED"),
    ("RESTART_SERVICE", "control-plane", "DENIED"),
    ("RESTART_SERVICE", "ghost", "DENIED"),
    ("ROLLBACK_DEPLOYMENT", "cpp-service", "UNSUPPORTED"),
    ("ENABLE_FALLBACK", "cpp-service", "UNSUPPORTED"),
])
def test_denied_and_unsupported_requests_are_recorded_and_never_executed(factory, action, target, expected):
    ex = FakeExecutor()
    engine, _ = make(factory, ex)
    row = engine.request(action=action, target=target, mode="execute", requested_by="op")
    assert row["status"] == expected and ex.calls == []
    assert "policy_verdict" in events(engine)


def test_cooldown_blocks_a_third_remediation_on_the_same_target(factory):
    engine, _ = make(factory)
    for _ in range(2):
        row = engine.request(action="RESTART_SERVICE", target="cpp-service", mode="execute", requested_by="op")
        wait_for(engine, row["id"], ("VERIFIED", "VERIFICATION_FAILED", "FAILED"))
    third = engine.request(action="RESTART_SERVICE", target="cpp-service", mode="execute", requested_by="op")
    assert third["status"] == "DENIED" and "cooldown" in third["policy"]["reasons"][0]


def test_bad_request_shapes_are_rejected(factory):
    engine, _ = make(factory)
    with pytest.raises(RemediationError):
        engine.request(action="RESTART_SERVICE", target="cpp-service", mode="yolo", requested_by="op")
    with pytest.raises(RemediationError):
        engine.request(action="RESTART_SERVICE", target="cpp-service", requested_by="op",
                       strategies=["SCALE_SERVICE", "SCALE_SERVICE", "SCALE_SERVICE"])
    with pytest.raises(RemediationError):
        engine.request(action="RESTART_SERVICE", target="cpp-service", requested_by="op", strategies=["rm -rf /"])


# ---- cache executor and auth ----------------------------------------------------------------------------

class FakeRedis:
    def __init__(self, keys):
        self.keys = set(keys)

    def scan_iter(self, match, count):
        prefix = match.rstrip("*")
        return [k for k in sorted(self.keys) if k.startswith(prefix)]

    def delete(self, key):
        self.keys.discard(key)
        return 1


def test_clear_cache_deletes_only_keys_under_the_cache_prefix():
    redis = FakeRedis({"cache:metrics", "cache:x", "service:health:gateway", "ratelimit:incidents:a"})
    result = CacheExecutor(lambda: redis).clear("cache:")
    assert result["deleted_keys"] == 2
    assert redis.keys == {"service:health:gateway", "ratelimit:incidents:a"}  # live state and rate limits untouched
    with pytest.raises(ExecutionError):
        CacheExecutor(lambda: redis).clear("service:")


def test_api_keys_map_to_roles_and_bad_input_is_ignored():
    keys = load_keys("opkey:operator, adminkey:admin, junk, k:root")
    assert keys == {"opkey": "operator", "adminkey": "admin"}
    assert authenticate("adminkey", keys)[1] == "admin"
    assert authenticate("nope", keys) is None and load_keys("") == {}
