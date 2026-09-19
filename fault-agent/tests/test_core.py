import pytest

from agent.core import (
    ALLOW_LABEL,
    Conflict,
    FaultError,
    FaultManager,
    NotAllowed,
    UnknownContainer,
    UnsupportedFault,
)


class FakeContainer:
    def __init__(self, name, labeled=True, status="running"):
        self.name = name
        self.id = f"id-{name}"
        self.labels = {ALLOW_LABEL: "true"} if labeled else {}
        self.status = status
        self.attrs = {"State": {}}
        self.netem = False
        self.calls = []
        self.fail_stop = False

    def reload(self):
        pass

    def stop(self, timeout=10):
        self.calls.append("stop")
        if self.fail_stop:
            raise RuntimeError("daemon error")
        self.status = "exited"

    def start(self):
        self.calls.append("start")
        self.status = "running"

    def pause(self):
        self.calls.append("pause")
        self.status = "paused"

    def unpause(self):
        self.calls.append("unpause")
        self.status = "running"

    def restart(self, timeout=10):
        self.calls.append("restart")
        self.status = "running"


class FakeContainers:
    def __init__(self, containers, netem_works=True):
        self._by_name = {c.name: c for c in containers}
        self._by_id = {c.id: c for c in containers}
        self.netem_works = netem_works
        self.helper_runs = []

    def get(self, name):
        if name not in self._by_name:
            raise KeyError(name)
        return self._by_name[name]

    def run(self, image, command, entrypoint, network_mode, cap_add, remove, detach):
        target = self._by_id[network_mode.split(":", 1)[1]]
        self.helper_runs.append((entrypoint, list(command), cap_add))
        if command[:2] == ["qdisc", "replace"]:
            target.netem = self.netem_works
            return b""
        if command[:2] == ["qdisc", "del"]:
            if not target.netem:
                raise RuntimeError("Cannot delete qdisc with handle of zero")
            target.netem = False
            return b""
        return b"qdisc netem 8001: root refcnt 2 limit 1000 delay 800ms" if target.netem else b"qdisc noqueue 0: root"


class FakeClient:
    def __init__(self, containers, **kw):
        self.containers = FakeContainers(containers, **kw)


class FakeTimer:
    instances = []

    def __init__(self, delay, fn, args=()):
        self.delay, self.fn, self.args = delay, fn, args
        self.daemon = False
        self.started = False
        self.cancelled = False
        FakeTimer.instances.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.fn(*self.args)


@pytest.fixture(autouse=True)
def reset_timers():
    FakeTimer.instances = []


def manager(tmp_path, containers, **kw):
    client = FakeClient(containers, **{k: v for k, v in kw.items() if k == "netem_works"})
    return FaultManager(client, str(tmp_path / "state.json"), "img", timer_factory=FakeTimer,
                        wait_running_s=0.05, poll_s=0.01, expire_retry_s=1, expire_max_retries=3), client


def test_stop_container_is_verified_and_rolled_back(tmp_path):
    web = FakeContainer("web")
    m, _ = manager(tmp_path, [web])
    result = m.apply("e1", "web", "stop_container", {}, 30)
    assert result["verified"] and web.status == "exited"
    assert m.rollback("e1")["status"] == "reverted" and web.status == "running"
    assert m.list_active() == []


def test_unlabelled_container_is_refused_and_untouched(tmp_path):
    secret = FakeContainer("control-plane", labeled=False)
    m, _ = manager(tmp_path, [secret])
    with pytest.raises(NotAllowed):
        m.apply("e1", "control-plane", "stop_container", {}, 30)
    assert secret.calls == [] and secret.status == "running"
    assert m.list_active() == []


def test_unknown_container_and_unsupported_fault(tmp_path):
    m, _ = manager(tmp_path, [FakeContainer("web")])
    with pytest.raises(UnknownContainer):
        m.apply("e1", "ghost", "stop_container", {}, 30)
    with pytest.raises(UnsupportedFault):
        m.apply("e1", "web", "meteor", {}, 30)


def test_one_active_fault_per_container_and_per_experiment(tmp_path):
    m, _ = manager(tmp_path, [FakeContainer("web"), FakeContainer("db")])
    m.apply("e1", "web", "pause_container", {}, 30)
    with pytest.raises(Conflict):
        m.apply("e2", "web", "stop_container", {}, 30)
    with pytest.raises(Conflict):
        m.apply("e1", "db", "stop_container", {}, 30)


def test_ttl_expiry_reverts_the_fault_without_the_control_plane(tmp_path):
    web = FakeContainer("web")
    m, _ = manager(tmp_path, [web])
    m.apply("e1", "web", "stop_container", {}, 30)
    timer = FakeTimer.instances[-1]
    assert timer.delay == 30 and timer.started and timer.daemon
    timer.fire()
    assert web.status == "running" and m.list_active() == []
    assert m.history()[-1]["reverted_by"] == "ttl"


def test_rollback_is_idempotent(tmp_path):
    web = FakeContainer("web")
    m, _ = manager(tmp_path, [web])
    assert m.rollback("never-applied")["status"] == "not_active"
    m.apply("e1", "web", "pause_container", {}, 30)
    m.rollback("e1")
    again = m.rollback("e1")
    assert again["status"] == "not_active" and again["previous"]["reverted_by"] == "rollback"
    assert web.calls.count("unpause") == 1


def test_rollback_cancels_the_ttl_timer(tmp_path):
    m, _ = manager(tmp_path, [FakeContainer("web")])
    m.apply("e1", "web", "pause_container", {}, 30)
    timer = FakeTimer.instances[-1]
    m.rollback("e1")
    assert timer.cancelled


def test_failed_apply_undoes_partial_effect_and_forgets_the_fault(tmp_path):
    web = FakeContainer("web")
    web.fail_stop = True
    m, _ = manager(tmp_path, [web])
    with pytest.raises(RuntimeError):
        m.apply("e1", "web", "stop_container", {}, 30)
    assert m.list_active() == [] and web.status == "running"


def test_agent_restart_reverts_faults_persisted_by_the_previous_process(tmp_path):
    web = FakeContainer("web")
    first, _ = manager(tmp_path, [web])
    first.apply("e1", "web", "stop_container", {}, 300)
    assert web.status == "exited"

    # the agent dies; a new process starts with the same state file
    second, _ = manager(tmp_path, [web])
    assert [r["experiment_id"] for r in second.list_active()] == ["e1"]
    assert second.revert_all_on_startup() == {"reverted": ["e1"], "failed": []}
    assert web.status == "running" and second.list_active() == []
    assert second.history()[-1]["reverted_by"] == "startup"


def test_failed_rollback_keeps_fault_active_and_ttl_retries(tmp_path):
    web = FakeContainer("web")
    m, client = manager(tmp_path, [web])
    m.apply("e1", "web", "stop_container", {}, 30)
    web.start = lambda: (_ for _ in ()).throw(RuntimeError("cannot start"))
    with pytest.raises(RuntimeError):
        m.rollback("e1")
    assert [r["experiment_id"] for r in m.list_active()] == ["e1"]

    FakeTimer.instances[-1].fire()  # TTL attempt 1 fails -> re-armed
    retry = FakeTimer.instances[-1]
    assert retry.delay == 1 and retry.args == ("e1", 1)
    assert [r["experiment_id"] for r in m.list_active()] == ["e1"]


def test_latency_uses_netem_via_helper_in_target_namespace_and_verifies(tmp_path):
    web = FakeContainer("web")
    m, client = manager(tmp_path, [web])
    result = m.apply("e1", "web", "latency", {"latency_ms": 800, "jitter_ms": 50}, 30)
    assert result["details"]["latency_ms"] == 800 and "netem" in result["details"]["qdisc"]
    entrypoint, args, caps = client.containers.helper_runs[0]
    assert entrypoint == "tc" and caps == ["NET_ADMIN"]
    assert args == ["qdisc", "replace", "dev", "eth0", "root", "netem", "delay", "800ms", "50ms"]

    m.rollback("e1")
    assert web.netem is False


def test_latency_apply_fails_loudly_if_netem_is_not_active(tmp_path):
    web = FakeContainer("web")
    m, _ = manager(tmp_path, [web], netem_works=False)
    with pytest.raises(FaultError, match="netem not active"):
        m.apply("e1", "web", "latency", {"latency_ms": 800}, 30)
    assert m.list_active() == []


def test_latency_undo_is_idempotent_when_qdisc_already_gone(tmp_path):
    web = FakeContainer("web")
    m, _ = manager(tmp_path, [web])
    m.apply("e1", "web", "latency", {"latency_ms": 100}, 30)
    web.netem = False  # something else removed it
    assert m.rollback("e1")["status"] == "reverted"


def test_restart_fault_ends_with_container_running(tmp_path):
    web = FakeContainer("web")
    m, _ = manager(tmp_path, [web])
    m.apply("e1", "web", "restart_container", {}, 30)
    assert "restart" in web.calls and web.status == "running"
    m.rollback("e1")
