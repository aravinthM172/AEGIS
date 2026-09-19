import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.security import check_startup, insecure_defaults, install_read_auth


@pytest.fixture()
def app_client(monkeypatch):
    monkeypatch.setenv("FAULTSCOPE_API_KEYS", "viewkey:viewer,adminkey:admin")
    app = FastAPI()

    @app.get("/api/secret")
    def secret():
        return {"data": 1}

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/other")
    def other():
        return {"public": True}

    @app.post("/api/thing")
    def thing():
        return {"posted": True}

    install_read_auth(app)
    return TestClient(app)


def test_reads_are_open_by_default(app_client):
    assert app_client.get("/api/secret").status_code == 200


def test_reads_need_a_key_when_enabled_but_health_and_non_api_paths_stay_open(app_client, monkeypatch):
    monkeypatch.setenv("FAULTSCOPE_REQUIRE_AUTH_FOR_READS", "1")
    assert app_client.get("/api/secret").status_code == 401
    assert app_client.get("/api/secret", headers={"X-API-Key": "wrong"}).status_code == 401
    assert app_client.get("/api/secret", headers={"X-API-Key": "viewkey"}).status_code == 200
    assert app_client.get("/api/health").status_code == 200
    assert app_client.get("/other").status_code == 200


def test_cors_preflight_is_not_blocked_and_writes_are_left_to_their_own_role_checks(app_client, monkeypatch):
    monkeypatch.setenv("FAULTSCOPE_REQUIRE_AUTH_FOR_READS", "1")
    assert app_client.options("/api/secret").status_code != 401
    assert app_client.post("/api/thing").status_code == 200  # this test route has no role dependency of its own


def test_required_auth_without_configured_keys_is_disabled_not_open(app_client, monkeypatch):
    monkeypatch.setenv("FAULTSCOPE_REQUIRE_AUTH_FOR_READS", "1")
    monkeypatch.setenv("FAULTSCOPE_API_KEYS", "")
    assert app_client.get("/api/secret").status_code == 503


def test_development_secrets_are_detected():
    env = {"FAULTSCOPE_API_KEYS": "dev-operator-key:operator,mine:admin", "FAULT_AGENT_TOKEN": "dev-token-change-me"}
    found = insecure_defaults(env)
    assert any("dev-operator-key" in f for f in found) and any("FAULT_AGENT_TOKEN" in f for f in found)
    assert "mine" not in " ".join(found)  # custom keys are never reported, and never echoed
    assert insecure_defaults({"FAULTSCOPE_API_KEYS": "mine:admin", "FAULT_AGENT_TOKEN": "s3cret-value"}) == []


def test_local_mode_is_quiet_and_strict_mode_refuses_elsewhere():
    dev = {"FAULTSCOPE_API_KEYS": "dev-admin-key:admin", "FAULT_AGENT_TOKEN": "dev-token-change-me"}
    assert check_startup({**dev, "FAULTSCOPE_ENV": "local", "FAULTSCOPE_STRICT_SECURITY": "1"}) == []
    assert check_startup(dev)  # outside local: reported (warning), not fatal
    with pytest.raises(RuntimeError, match="development secrets"):
        check_startup({**dev, "FAULTSCOPE_STRICT_SECURITY": "1"})
    assert check_startup({"FAULTSCOPE_API_KEYS": "mine:admin", "FAULT_AGENT_TOKEN": "x9", "FAULTSCOPE_STRICT_SECURITY": "1"}) == []
