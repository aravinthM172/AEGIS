import pytest

from app.experiments.injectors import InjectorError, runtime, supported_real_faults
from app.experiments.k8s_injector import (
    ANN_EXPERIMENT,
    ANN_EXPIRES_AT,
    ANN_ORIGINAL_REPLICAS,
    SUPPORTED_FAULTS,
    KubernetesInjector,
)


class FakeApi:
    """In-memory stand-in for the Kubernetes API: Deployments, their pods and annotations."""

    namespace = "faultscope"

    def __init__(self, terminating_polls=0, never_ready_after_restore=False, pods_stay=False):
        self.calls = []
        self.deps = {}
        self.pods = {}
        self._seq = 0
        self.terminating_polls = terminating_polls
        self._terminating = {}
        self.never_ready_after_restore = never_ready_after_restore
        self.pods_stay = pods_stay  # pods ignore the scale-down (stuck), unlike merely-terminating pods

    def add(self, name, replicas=1, labelled=True):
        self.deps[name] = {"metadata": {"name": name, "labels": {"faultscope.injectable": "true"} if labelled else {},
                                        "annotations": {}},
                           "spec": {"replicas": replicas, "selector": {"matchLabels": {"app": name}}},
                           "status": {"readyReplicas": replicas}}
        self.pods[name] = [self._new_pod() for _ in range(replicas)]

    def _new_pod(self):
        self._seq += 1
        return {"metadata": {"name": f"pod-{self._seq}"}}

    def get_deployment(self, name):
        return self.deps.get(name)

    def list_deployments(self):
        return list(self.deps.values())

    def patch_deployment(self, name, body):
        self.calls.append(("patch", name))
        ann = self.deps[name]["metadata"]["annotations"]
        for key, value in body["metadata"]["annotations"].items():
            if value is None:
                ann.pop(key, None)
            else:
                ann[key] = value

    def set_replicas(self, name, replicas):
        self.calls.append(("scale", name, replicas))
        dep = self.deps[name]
        dep["spec"]["replicas"] = replicas
        if replicas == 0:
            if self.pods_stay:
                pass
            elif self.terminating_polls:
                for pod in self.pods[name]:
                    pod["metadata"]["deletionTimestamp"] = "now"
                self._terminating[name] = self.terminating_polls
            else:
                self.pods[name] = []
            dep["status"]["readyReplicas"] = 0
        else:
            self.pods[name] = [self._new_pod() for _ in range(replicas)]
            dep["status"]["readyReplicas"] = 0 if self.never_ready_after_restore else replicas

    def list_pods(self, match_labels):
        name = match_labels["app"]
        if self._terminating.get(name):
            self._terminating[name] -= 1
            if not self._terminating[name]:
                self.pods[name] = []
        return list(self.pods[name])

    def delete_pod(self, pod_name):
        self.calls.append(("delete_pod", pod_name))
        for name, pods in self.pods.items():
            if any(p["metadata"]["name"] == pod_name for p in pods):
                self.pods[name] = [p for p in pods if p["metadata"]["name"] != pod_name] + [self._new_pod()]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def make(api, **kw):
    clock = Clock()
    return KubernetesInjector(api, clock=clock, sleep=clock.sleep, wait_s=20, poll_s=1, **kw), clock


def exp(fault="stop_container", target="java-service", exp_id="e1", duration=30):
    return {"id": exp_id, "target": target, "fault_type": fault, "duration_s": duration}


def test_stop_scales_to_zero_verifies_and_rollback_restores_and_clears_the_intent():
    api = FakeApi(terminating_polls=2)
    api.add("java-service", replicas=2)
    injector, _ = make(api)

    applied = injector.inject(exp())
    assert applied["verified"] and applied["original_replicas"] == 2
    assert api.deps["java-service"]["spec"]["replicas"] == 0
    assert api.deps["java-service"]["metadata"]["annotations"][ANN_EXPERIMENT] == "e1"

    restored = injector.rollback(exp())
    assert restored["action"] == "restored"
    dep = api.deps["java-service"]
    assert dep["spec"]["replicas"] == 2 and dep["status"]["readyReplicas"] == 2
    assert dep["metadata"]["annotations"] == {}


def test_intent_is_recorded_on_the_deployment_before_anything_is_changed():
    api = FakeApi()
    api.add("java-service")
    make(api)[0].inject(exp())
    assert api.calls[0] == ("patch", "java-service") and api.calls[1] == ("scale", "java-service", 0)


def test_unlabelled_deployment_is_refused_and_untouched():
    api = FakeApi()
    api.add("postgres", labelled=False)
    with pytest.raises(InjectorError, match="not labelled"):
        make(api)[0].inject(exp(target="postgres"))
    assert api.calls == []


def test_unknown_deployment_and_unsupported_fault_are_refused():
    api = FakeApi()
    api.add("java-service")
    injector, _ = make(api)
    with pytest.raises(InjectorError, match="not found"):
        injector.inject(exp(target="nope"))
    with pytest.raises(InjectorError, match="not supported on Kubernetes"):
        injector.inject(exp(fault="latency"))
    assert api.calls == []


def test_only_one_fault_per_deployment():
    api = FakeApi()
    api.add("java-service")
    injector, _ = make(api)
    injector.inject(exp())
    with pytest.raises(InjectorError, match="already has an active fault"):
        injector.inject(exp(exp_id="e2"))


def test_rollback_is_idempotent_and_safe_if_nothing_was_injected():
    api = FakeApi()
    api.add("java-service")
    injector, _ = make(api)
    assert injector.rollback(exp())["action"] == "ensure ready"  # never injected
    injector.inject(exp())
    injector.rollback(exp())
    again = injector.rollback(exp())
    assert again["action"] == "ensure ready" and api.deps["java-service"]["spec"]["replicas"] == 1
    assert injector.rollback(exp(target="gone"))["note"].startswith("deployment 'gone' not found")


def test_restart_replaces_every_pod_and_leaves_the_deployment_ready():
    api = FakeApi()
    api.add("java-service", replicas=2)
    before = {p["metadata"]["name"] for p in api.pods["java-service"]}
    injector, _ = make(api)
    result = injector.inject(exp(fault="restart_container"))
    assert set(result["deleted_pods"]) == before
    assert not before & {p["metadata"]["name"] for p in api.pods["java-service"]}
    injector.rollback(exp(fault="restart_container"))
    assert api.deps["java-service"]["metadata"]["annotations"] == {}


def test_pods_that_never_terminate_time_out_instead_of_claiming_success():
    api = FakeApi(pods_stay=True)
    api.add("java-service")
    with pytest.raises(InjectorError, match="timed out"):
        make(api)[0].inject(exp())


def test_failed_recovery_keeps_the_intent_so_the_fault_is_still_tracked():
    api = FakeApi(never_ready_after_restore=True)
    api.add("java-service")
    injector, _ = make(api)
    injector.inject(exp())
    with pytest.raises(InjectorError, match="timed out"):
        injector.rollback(exp())
    assert ANN_ORIGINAL_REPLICAS in api.deps["java-service"]["metadata"]["annotations"]


def test_watchdog_restores_expired_faults_only_and_startup_recovery_restores_all():
    api = FakeApi()
    api.add("java-service")
    api.add("cpp-service")
    injector, clock = make(api)
    injector.inject(exp(duration=30))                                   # expires at ~ now + 50
    injector.inject(exp(target="cpp-service", exp_id="e2", duration=500))  # expires much later
    assert injector.recover_expired() == []
    clock.now += 100
    assert injector.recover_expired() == ["java-service"]
    assert api.deps["java-service"]["spec"]["replicas"] == 1 and api.deps["cpp-service"]["spec"]["replicas"] == 0
    assert injector.recover_expired(include_unexpired=True) == ["cpp-service"]
    assert all(ANN_EXPIRES_AT not in d["metadata"]["annotations"] for d in api.deps.values())


def test_runtime_selection_and_supported_faults(monkeypatch):
    monkeypatch.delenv("FAULTSCOPE_RUNTIME", raising=False)
    assert runtime() == "docker" and supported_real_faults() is None
    monkeypatch.setenv("FAULTSCOPE_RUNTIME", "kubernetes")
    assert runtime() == "kubernetes" and supported_real_faults() == SUPPORTED_FAULTS
