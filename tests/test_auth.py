import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.remediation.auth import require_role


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FAULTSCOPE_API_KEYS", "opkey:operator,adminkey:admin,viewkey:viewer")
    app = FastAPI()

    @app.post("/operate")
    def operate(actor: str = Depends(require_role("operator"))):
        return {"actor": actor}

    @app.post("/administer")
    def administer(actor: str = Depends(require_role("admin"))):
        return {"actor": actor}

    return TestClient(app)


def test_missing_or_wrong_key_is_401(client):
    assert client.post("/operate").status_code == 401
    assert client.post("/operate", headers={"X-API-Key": "nope"}).status_code == 401


def test_roles_are_enforced_and_actors_are_labelled_without_leaking_the_key(client):
    ok = client.post("/operate", headers={"X-API-Key": "opkey"})
    assert ok.status_code == 200 and ok.json()["actor"].startswith("operator:") and "opkey" not in ok.json()["actor"]
    assert client.post("/operate", headers={"X-API-Key": "adminkey"}).status_code == 200  # admin >= operator
    assert client.post("/operate", headers={"X-API-Key": "viewkey"}).status_code == 403
    assert client.post("/administer", headers={"X-API-Key": "opkey"}).status_code == 403
    assert client.post("/administer", headers={"X-API-Key": "adminkey"}).status_code == 200


def test_without_configured_keys_protected_endpoints_are_disabled_not_open(monkeypatch):
    monkeypatch.delenv("FAULTSCOPE_API_KEYS", raising=False)
    app = FastAPI()

    @app.post("/operate")
    def operate(actor: str = Depends(require_role("operator"))):
        return {"actor": actor}

    r = TestClient(app).post("/operate", headers={"X-API-Key": "anything"})
    assert r.status_code == 503 and "disabled" in r.json()["detail"]
