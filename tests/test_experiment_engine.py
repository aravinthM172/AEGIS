import time

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.experiments.engine import (
    ActiveExperimentExists,
    ExperimentEngine,
    InvalidState,
    NotFound,
    ValidationFailed,
)
from app.experiments.models import ExperimentEventRow, ExperimentRow
from app.models import ServiceRow


class RecordingInjector:
    name = "recording"

    def __init__(self, fail_inject=False, fail_rollback=False):
        self.calls = []
        self.fail_inject = fail_inject
        self.fail_rollback = fail_rollback

    def inject(self, exp):
        self.calls.append("inject")
        if self.fail_inject:
            raise RuntimeError("inject exploded")
        return {"applied": False, "note": "recorded"}

    def rollback(self, exp):
        self.calls.append("rollback")
        if self.fail_rollback:
            raise RuntimeError("rollback exploded")
        return {"applied": False}


@pytest.fixture()
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'exp.db'}", connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(engine, "connect")
    def enforce_foreign_keys(dbapi_connection, _):  # SQLite ignores FKs unless asked; Postgres does not
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    for table in (ServiceRow.__table__, ExperimentRow.__table__, ExperimentEventRow.__table__):
        table.create(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as db:
        db.add(ServiceRow(name="java-service", kind="service", container="aegis-java-service"))
        db.add(ServiceRow(name="control-plane", kind="service", container="aegis-api"))
        db.commit()
    return session_factory


def make_engine(factory, injector=None, scale=0.02, preflight=lambda: []):
    injector = injector or RecordingInjector()
    return ExperimentEngine(session_factory=factory, injector_for=lambda exp: injector,
                            time_scale=scale, poll_interval_s=0.01,
                            real_run_preflight=preflight), injector


def create(engine, **overrides):
    args = dict(target="java-service", fault_type="stop_container", duration_s=5, baseline_s=0, recovery_s=5)
    args.update(overrides)
    return engine.create(**args)


def wait_for(engine, exp_id, predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        exp = engine.get(exp_id)
        if predicate(exp):
            return exp
        time.sleep(0.02)
    raise AssertionError(f"condition not reached; last state: {engine.get(exp_id)['status']}/{engine.get(exp_id)['phase']}")


def event_types(exp):
    return [e["event_type"] for e in exp["events"]]


def test_full_lifecycle_records_ordered_events_and_timeline(factory):
    engine, injector = make_engine(factory)
    exp = wait_for(engine, create(engine)["id"], lambda e: e["status"] == "COMPLETED")

    assert event_types(exp) == ["created", "baseline_started", "fault_injected", "fault_removed",
                                "recovery_started", "completed"]
    assert injector.calls == ["inject", "rollback"]
    t = exp["timeline"]
    assert (t["baseline_started_at"] <= t["inject_started_at"] <= t["fault_applied_at"]
            <= t["rollback_started_at"] <= t["inject_ended_at"] <= t["finished_at"])
    assert exp["phase"] is None and exp["dry_run"] is True


def test_only_one_experiment_can_be_active(factory):
    engine, _ = make_engine(factory, scale=0.1)
    first = create(engine)
    with pytest.raises(ActiveExperimentExists) as info:
        create(engine)
    assert info.value.active_id == first["id"]

    wait_for(engine, first["id"], lambda e: e["status"] == "COMPLETED")
    second = create(engine)  # slot is free again
    wait_for(engine, second["id"], lambda e: e["status"] == "COMPLETED")


def test_abort_during_injection_rolls_back_and_frees_slot(factory):
    engine, injector = make_engine(factory, scale=0.5)
    exp = create(engine)
    wait_for(engine, exp["id"], lambda e: e["phase"] == "injecting")
    engine.abort(exp["id"])
    done = wait_for(engine, exp["id"], lambda e: e["status"] == "ABORTED")

    assert "abort_requested" in event_types(done) and "fault_removed" in event_types(done)
    assert injector.calls == ["inject", "rollback"]
    assert done["timeline"]["inject_ended_at"] is not None
    third = create(engine)  # not blocked
    engine.abort(third["id"])
    wait_for(engine, third["id"], lambda e: e["status"] == "ABORTED")


def test_abort_of_finished_or_unknown_experiment_is_rejected(factory):
    engine, _ = make_engine(factory)
    exp = wait_for(engine, create(engine)["id"], lambda e: e["status"] == "COMPLETED")
    with pytest.raises(InvalidState):
        engine.abort(exp["id"])
    with pytest.raises(NotFound):
        engine.abort("nope")


def test_inject_failure_marks_failed_and_still_rolls_back(factory):
    engine, injector = make_engine(factory, RecordingInjector(fail_inject=True))
    exp = wait_for(engine, create(engine)["id"], lambda e: e["status"] == "FAILED")

    assert "inject exploded" in exp["error"]
    assert injector.calls == ["inject", "rollback"]  # rollback attempted even though inject raised
    create(engine)  # slot freed


def test_failed_rollback_blocks_new_experiments_until_resolved(factory):
    injector = RecordingInjector(fail_rollback=True)
    engine, _ = make_engine(factory, injector)
    exp = wait_for(engine, create(engine)["id"], lambda e: e["status"] == "ROLLBACK_FAILED")

    with pytest.raises(ActiveExperimentExists):
        create(engine)
    with pytest.raises(InvalidState):
        engine.retry_rollback(exp["id"])  # still failing

    injector.fail_rollback = False
    resolved = engine.retry_rollback(exp["id"])
    assert resolved["status"] == "FAILED"
    create(engine)  # slot freed after successful retry


def test_orphaned_active_experiment_is_rolled_back_on_startup(factory):
    engine, injector = make_engine(factory)
    with factory() as db:
        db.add(ExperimentRow(id="orphan-1", target="java-service", fault_type="stop_container",
                             parameters={}, duration_s=30, baseline_s=0, recovery_s=5, dry_run=True,
                             status="RUNNING", phase="injecting", abort_requested=False, active_slot=True))
        db.commit()

    assert engine.recover_orphans() == ["orphan-1"]
    exp = engine.get("orphan-1")
    assert exp["status"] == "ABORTED" and "restarted" in exp["error"]
    assert "orphan_recovered" in event_types(exp)
    assert injector.calls == ["rollback"]
    create(engine)  # slot freed


def test_invalid_request_creates_nothing(factory):
    engine, _ = make_engine(factory)
    with pytest.raises(ValidationFailed) as info:
        create(engine, target="control-plane")
    assert any("protected" in e for e in info.value.errors)
    with pytest.raises(ValidationFailed):
        create(engine, fault_type="meteor", dry_run=False)
    assert engine.list_experiments() == []


def test_real_run_is_blocked_by_preflight_and_dry_run_is_not(factory):
    engine, _ = make_engine(factory, preflight=lambda: ["fault agent unreachable"])
    with pytest.raises(ValidationFailed) as info:
        create(engine, dry_run=False)
    assert info.value.errors == ["fault agent unreachable"]
    assert engine.list_experiments() == []

    dry = create(engine, dry_run=True)  # preflight is never consulted for dry runs
    wait_for(engine, dry["id"], lambda e: e["status"] == "COMPLETED")


def test_real_run_passes_the_container_to_the_injector(factory):
    seen = {}

    class Capturing(RecordingInjector):
        def inject(self, exp):
            seen.update(exp)
            return super().inject(exp)

    engine, _ = make_engine(factory, Capturing())
    exp = create(engine, dry_run=False)
    wait_for(engine, exp["id"], lambda e: e["status"] == "COMPLETED")
    assert seen["container"] == "aegis-java-service" and seen["id"] == exp["id"]
    assert engine.get(exp["id"])["injector"] == "docker"


class FakeWorkload:
    instances = []

    def __init__(self, exp):
        self.exp = exp
        self.started = False
        self.stopped = 0
        FakeWorkload.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped += 1
        return {"target_rps": self.exp["workload_rps"], "sent": 42, "ok": 40, "failed": 2}


def engine_with_workload(factory, hook=None, injector=None, scale=0.02):
    FakeWorkload.instances = []
    injector = injector or RecordingInjector()
    return ExperimentEngine(session_factory=factory, injector_for=lambda exp: injector, time_scale=scale,
                            poll_interval_s=0.01, real_run_preflight=lambda: [],
                            workload_factory=FakeWorkload, on_finished=hook, settle_s=1.0), injector


def test_workload_runs_for_the_experiment_and_its_stats_are_stored(factory):
    engine, _ = engine_with_workload(factory)
    exp = wait_for(engine, create(engine, workload_rps=4)["id"], lambda e: e["status"] == "COMPLETED")
    (runner,) = FakeWorkload.instances
    assert runner.started and runner.stopped >= 1
    assert exp["workload_rps"] == 4
    assert exp["result"] == {"workload": {"target_rps": 4, "sent": 42, "ok": 40, "failed": 2}}


def test_no_workload_is_created_when_rps_is_zero(factory):
    engine, _ = engine_with_workload(factory)
    wait_for(engine, create(engine)["id"], lambda e: e["status"] == "COMPLETED")
    assert FakeWorkload.instances == []


def test_workload_is_stopped_when_the_experiment_is_aborted_or_fails(factory):
    engine, _ = engine_with_workload(factory, scale=0.5)
    exp = create(engine, workload_rps=2)
    wait_for(engine, exp["id"], lambda e: e["phase"] == "injecting")
    engine.abort(exp["id"])
    wait_for(engine, exp["id"], lambda e: e["status"] == "ABORTED")
    assert FakeWorkload.instances[0].stopped >= 1

    engine2, _ = engine_with_workload(factory, injector=RecordingInjector(fail_inject=True))
    failing = wait_for(engine2, create(engine2, workload_rps=2)["id"], lambda e: e["status"] == "FAILED")
    assert FakeWorkload.instances[0].stopped >= 1 and failing["result"]["workload"]["sent"] == 42


def test_workload_rate_is_bounded(factory):
    engine, _ = engine_with_workload(factory)
    with pytest.raises(ValidationFailed, match="workload_rps"):
        create(engine, workload_rps=500)
    with pytest.raises(ValidationFailed, match="workload_rps"):
        create(engine, workload_rps=-1)


def test_analysis_hook_runs_after_the_experiment_finishes(factory):
    called = []
    engine, _ = engine_with_workload(factory, hook=called.append)
    exp = create(engine)
    wait_for(engine, exp["id"], lambda e: e["status"] == "COMPLETED")
    deadline = time.monotonic() + 3
    while not called and time.monotonic() < deadline:
        time.sleep(0.02)
    assert called == [exp["id"]]


def test_failing_analysis_hook_is_recorded_not_swallowed(factory):
    def boom(exp_id):
        raise RuntimeError("telemetry unavailable")

    engine, _ = engine_with_workload(factory, hook=boom)
    exp = create(engine)
    wait_for(engine, exp["id"], lambda e: e["status"] == "COMPLETED")
    done = wait_for(engine, exp["id"], lambda e: "analysis_failed" in event_types(e))
    failed = next(e for e in done["events"] if e["event_type"] == "analysis_failed")
    assert "telemetry unavailable" in failed["detail"]["error"]
    assert done["status"] == "COMPLETED"  # the experiment itself is unaffected


def test_workload_intensity_is_validated_and_passed_to_the_runner(factory):
    engine, _ = engine_with_workload(factory)
    with pytest.raises(ValidationFailed, match="workload_n"):
        create(engine, workload_rps=2, workload_n=0)
    with pytest.raises(ValidationFailed, match="workload_n"):
        create(engine, workload_rps=2, workload_n=10_000_000)
    exp = create(engine, workload_rps=2, workload_n=2_000_000)
    assert exp["workload_n"] == 2_000_000
    wait_for(engine, exp["id"], lambda e: e["status"] == "COMPLETED")
    assert FakeWorkload.instances[0].exp["workload_n"] == 2_000_000
