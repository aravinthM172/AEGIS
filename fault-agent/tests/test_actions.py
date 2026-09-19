import pytest

from agent.core import Conflict, FaultError, NotAllowed, UnknownContainer, UnsupportedFault
from test_core import FakeClient, FakeContainer, FakeTimer
from test_stress_faults import StressContainer, mgr


def test_restart_of_a_stopped_container_starts_it():
    c = FakeContainer("svc", status="exited")
    m = mgr_for(c)
    result = m.run_action("restart", "svc")
    assert c.status == "running" and c.calls == ["start"]
    assert result["before"]["status"] == "exited" and result["after"]["status"] == "running"


def test_restart_of_a_paused_container_unpauses_it_instead_of_restarting():
    c = FakeContainer("svc", status="paused")
    result = mgr_for(c).run_action("restart", "svc")
    assert c.calls == ["unpause"] and result["after"]["status"] == "running"


def test_restart_of_a_running_container_restarts_it():
    c = FakeContainer("svc")
    mgr_for(c).run_action("restart", "svc")
    assert c.calls == ["restart"]


def test_lift_cpu_cap_removes_the_quota_and_verifies_it():
    c = StressContainer("svc")
    c.attrs["HostConfig"]["CpuQuota"] = 15000
    result = mgr_for(c).run_action("lift_cpu_cap", "svc")
    assert result["before"]["cpu_quota"] == 15000 and result["after"]["cpu_quota"] == -1


def test_lift_cpu_cap_fails_loudly_if_docker_ignores_the_update():
    c = StressContainer("svc")
    c.attrs["HostConfig"]["CpuQuota"] = 15000
    c.update = lambda **kw: None
    with pytest.raises(FaultError, match="cpu quota still"):
        mgr_for(c).run_action("lift_cpu_cap", "svc")


def test_actions_obey_the_allowlist_and_reject_unknown_actions():
    with pytest.raises(NotAllowed):
        mgr_for(FakeContainer("db", labeled=False)).run_action("restart", "db")
    with pytest.raises(UnknownContainer):
        mgr_for(FakeContainer("svc")).run_action("restart", "ghost")
    with pytest.raises(UnsupportedFault):
        mgr_for(FakeContainer("svc")).run_action("format_disk", "svc")


def test_no_remediation_while_an_experiment_fault_is_active_on_the_container():
    c = FakeContainer("svc")
    m = mgr_for(c)
    m.apply("exp-1", "svc", "pause_container", {}, 60)
    with pytest.raises(Conflict, match="active experiment fault"):
        m.run_action("restart", "svc")


def mgr_for(container):
    FakeTimer.instances = []
    from agent.core import FaultManager
    import tempfile, os
    path = os.path.join(tempfile.mkdtemp(), "s.json")
    return FaultManager(FakeClient([container]), path, "img", timer_factory=FakeTimer, wait_running_s=0.05, poll_s=0.01)
