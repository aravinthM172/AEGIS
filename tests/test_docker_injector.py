import json

import httpx
import pytest

from app.experiments.injectors import DockerInjector, InjectorError, InjectorUnavailable

EXP = {"id": "exp-1", "target": "java-service", "container": "aegis-java-service",
       "fault_type": "latency", "parameters": {"latency_ms": 800}, "duration_s": 30}


def injector(handler):
    client = httpx.Client(base_url="http://agent", headers={"X-Agent-Token": "t0k"},
                          transport=httpx.MockTransport(handler))
    return DockerInjector(client=client, ttl_margin_s=20)


def test_inject_posts_payload_with_ttl_longer_than_the_fault():
    seen = {}

    def handler(request):
        seen["req"] = request
        return httpx.Response(201, json={"applied": True, "verified": True})

    result = injector(handler).inject(EXP)
    body = json.loads(seen["req"].content)
    assert seen["req"].method == "POST" and seen["req"].url.path == "/faults"
    assert seen["req"].headers["x-agent-token"] == "t0k"
    assert body == {"experiment_id": "exp-1", "container": "aegis-java-service", "fault_type": "latency",
                    "parameters": {"latency_ms": 800}, "ttl_s": 50}
    assert result["mode"] == "docker" and result["applied"] is True


def test_agent_refusal_is_surfaced_with_its_reason():
    def handler(request):
        return httpx.Response(403, json={"detail": "container 'x' is not labelled faultscope.injectable=true"})

    with pytest.raises(InjectorError, match="403.*not labelled"):
        injector(handler).inject(EXP)


def test_missing_container_mapping_is_an_error():
    with pytest.raises(InjectorError, match="no container mapping"):
        injector(lambda r: httpx.Response(201, json={})).inject({**EXP, "container": None})


def test_unreachable_agent_is_an_injector_error_not_a_crash():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(InjectorError, match="unreachable"):
        injector(handler).inject(EXP)
    with pytest.raises(InjectorError, match="unreachable"):
        injector(handler).rollback(EXP)


def test_rollback_calls_delete_and_accepts_not_active():
    seen = {}

    def handler(request):
        seen["req"] = request
        return httpx.Response(200, json={"status": "not_active"})

    result = injector(handler).rollback(EXP)
    assert seen["req"].method == "DELETE" and seen["req"].url.path == "/faults/exp-1"
    assert result["status"] == "not_active"


def test_rollback_failure_raises_so_the_engine_marks_rollback_failed():
    with pytest.raises(InjectorError, match="rollback failed"):
        injector(lambda r: httpx.Response(500, json={"detail": "cannot start"})).rollback(EXP)


def test_health_reports_unreachable_agent():
    def handler(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(InjectorUnavailable):
        injector(handler).health()
