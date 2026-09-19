import pytest

from agent.core import FaultError, FaultManager
from test_core import ALLOW_LABEL, FakeClient, FakeContainer, FakeTimer


class StressContainer(FakeContainer):
    """Simulates the effects of the stress commands on stats() and the cgroup quota."""

    def __init__(self, name, port=None, mem_base_mb=100, broken_stress=False):
        super().__init__(name)
        if port:
            self.labels["faultscope.fault_port"] = port
        self.attrs = {"State": {}, "HostConfig": {"CpuQuota": 0, "CpuPeriod": 0}}
        self.workers = 0
        self.mem_hold_mb = 0
        self.quota_cores = None
        self.mem_base_mb = mem_base_mb
        self.broken_stress = broken_stress  # commands "succeed" but consume nothing
        self._cpu_total = 0.0
        self._cpu_sys = 0.0
        self.exec_log = []

    def update(self, **kwargs):
        self.exec_log.append(("update", kwargs))
        if "cpu_quota" in kwargs:
            self.attrs["HostConfig"]["CpuQuota"] = kwargs["cpu_quota"]
            self.quota_cores = kwargs["cpu_quota"] / 100000 if kwargs["cpu_quota"] > 0 else None
            if kwargs["cpu_quota"] == 0:
                return  # docker treats 0 as "unchanged" -- only -1 removes a limit
            self.attrs["HostConfig"]["CpuQuota"] = kwargs["cpu_quota"]

    def exec_run(self, cmd, detach=False, **kw):
        script = cmd[2]
        self.exec_log.append(("exec", detach, script))
        if "kill_tree" in script:
            self.workers, self.mem_hold_mb = 0, 0
        elif "while :; do :; done" in script:
            assert cmd[3] == "fs_stress_marker"  # $0 marker so the cleanup can find it
            if not self.broken_stress:
                self.workers += 1
        elif "awk" in script and "sprintf" in script:
            if not self.broken_stress:
                self.mem_hold_mb = int(script.split("i < ")[1].split(";")[0])

    def stats(self, stream=False):
        burn = 0.0
        if self.workers:
            burn = min(self.quota_cores or 99, self.workers)
        self._cpu_total += burn * 1e9
        self._cpu_sys += 1e9
        return {"cpu_stats": {"cpu_usage": {"total_usage": self._cpu_total}, "system_cpu_usage": self._cpu_sys,
                              "online_cpus": 1},
                "memory_stats": {"usage": (self.mem_base_mb + self.mem_hold_mb + 20) * 1024 * 1024,
                                 "stats": {"inactive_file": 20 * 1024 * 1024}}}


class FakeHttp:
    def __init__(self, active=True):
        self.calls = []
        self.active = active

    class R:
        def __init__(self, status, payload):
            self.status_code, self._p, self.text = status, payload, str(payload)

        def json(self):
            return self._p

    def post(self, url, json=None, headers=None):
        self.calls.append(("POST", url, json, headers))
        return self.R(200, {"active": self.active, "rate": json["rate"], "status": json["status"], "expires_in_s": json["ttl_s"]})

    def delete(self, url, headers=None):
        self.calls.append(("DELETE", url, None, headers))
        return self.R(200, {"active": False})


def mgr(tmp_path, container, http=None):
    FakeTimer.instances = []
    return FaultManager(FakeClient([container]), str(tmp_path / "s.json"), "img", timer_factory=FakeTimer,
                        wait_running_s=0.05, poll_s=0.01, http=http, hook_token="hook-tok", settle_s=0.0)


def test_cpu_stress_caps_quota_runs_workers_and_measures_the_effect(tmp_path):
    c = StressContainer("svc")
    m = mgr(tmp_path, c)
    result = m.apply("e1", "svc", "cpu_stress", {"cpu_limit_cores": 0.5, "workers": 2}, 60)
    assert c.attrs["HostConfig"]["CpuQuota"] == 50000
    assert c.workers == 2
    assert result["details"]["measured_cores_used"] == pytest.approx(0.5, abs=0.01)

    m.rollback("e1")
    assert c.workers == 0 and c.attrs["HostConfig"]["CpuQuota"] == -1  # -1 = limit removed


def test_cpu_stress_that_burns_nothing_fails_loudly_and_cleans_up(tmp_path):
    c = StressContainer("svc", broken_stress=True)
    m = mgr(tmp_path, c)
    with pytest.raises(FaultError, match="cpu stress ineffective"):
        m.apply("e1", "svc", "cpu_stress", {"workers": 1}, 60)
    assert c.attrs["HostConfig"]["CpuQuota"] <= 0 and m.list_active() == []  # limit removed (0 or -1)


def test_cpu_throttle_only_with_zero_workers_skips_measurement(tmp_path):
    c = StressContainer("svc")
    m = mgr(tmp_path, c)
    result = m.apply("e1", "svc", "cpu_stress", {"cpu_limit_cores": 0.25, "workers": 0}, 60)
    assert "measured_cores_used" not in result["details"] and c.attrs["HostConfig"]["CpuQuota"] == 25000


def test_memory_stress_holds_memory_and_verifies_growth(tmp_path):
    c = StressContainer("svc")
    m = mgr(tmp_path, c)
    result = m.apply("e1", "svc", "memory_stress", {"memory_mb": 200}, 60)
    assert result["details"]["measured_growth_mb"] == 200 and c.mem_hold_mb == 200
    m.rollback("e1")
    assert c.mem_hold_mb == 0


def test_memory_stress_that_does_not_grow_the_working_set_fails(tmp_path):
    c = StressContainer("svc", broken_stress=True)
    m = mgr(tmp_path, c)
    with pytest.raises(FaultError, match="memory stress ineffective"):
        m.apply("e1", "svc", "memory_stress", {"memory_mb": 200}, 60)
    assert m.list_active() == []


def test_cleanup_script_cannot_match_its_own_command_line():
    from agent.core import FaultManager as FM
    assert "fs_stress_marker" not in FM._KILL_TREE  # spelled as two quoted halves


def test_http_error_activates_the_service_hook_with_token_and_ttl(tmp_path):
    c = StressContainer("svc", port="8090")
    http = FakeHttp()
    m = mgr(tmp_path, c, http)
    m.apply("e1", "svc", "http_error", {"error_rate": 0.5, "status_code": 503}, 45)
    method, url, body, headers = http.calls[0]
    assert (method, url) == ("POST", "http://svc:8090/_faults/http-error")
    assert body == {"rate": 0.5, "status": 503, "ttl_s": 45}  # the hook expires by itself with the fault TTL
    assert headers == {"X-Fault-Token": "hook-tok"}

    m.rollback("e1")
    assert http.calls[-1][:2] == ("DELETE", "http://svc:8090/_faults/http-error")


def test_http_error_fails_if_hook_does_not_report_active(tmp_path):
    m = mgr(tmp_path, StressContainer("svc", port="8090"), FakeHttp(active=False))
    with pytest.raises(FaultError, match="did not activate"):
        m.apply("e1", "svc", "http_error", {"error_rate": 0.5}, 45)
    assert m.list_active() == []


def test_http_error_on_a_container_without_a_hook_is_refused(tmp_path):
    m = mgr(tmp_path, StressContainer("db"), FakeHttp())
    with pytest.raises(FaultError, match="no http fault hook"):
        m.apply("e1", "db", "http_error", {"error_rate": 0.5}, 45)


def test_cpu_rollback_fails_loudly_if_the_quota_is_still_applied(tmp_path):
    c = StressContainer("svc")
    m = mgr(tmp_path, c)
    m.apply("e1", "svc", "cpu_stress", {"cpu_limit_cores": 0.5, "workers": 0}, 60)
    c.update = lambda **kw: None  # docker silently ignores the update
    with pytest.raises(FaultError, match="cpu quota still"):
        m.rollback("e1")
    assert [r["experiment_id"] for r in m.list_active()] == ["e1"]  # stays active so the TTL keeps retrying


def test_memory_hold_script_works_without_awk_via_the_fallback(tmp_path):
    c = StressContainer("svc")
    m = mgr(tmp_path, c)
    m.apply("e1", "svc", "memory_stress", {"memory_mb": 64}, 60)
    script = next(e[2] for e in c.exec_log if e[0] == "exec" and "awk" in e[2])
    assert "if awk" in script and "tail -n 1" in script and "sleep 65" in script  # probe, then pick a technique
