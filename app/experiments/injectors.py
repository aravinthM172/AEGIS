"""Fault injectors. The engine only talks to this interface.

Contract:
  inject(exp)    apply the fault; return a dict describing what was ACTUALLY done
  rollback(exp)  undo it; must be idempotent and safe to call even if inject never ran
                 or only partly ran (the engine calls it on every exit path)

DryRunInjector applies nothing and labels its results applied=False.
DockerInjector delegates to the fault-agent, the only component with Docker access.
The agent independently enforces a container allowlist and reverts every fault after
a TTL, so a dead control plane cannot leave a fault applied.
"""
import os
from typing import Protocol

import httpx


class InjectorUnavailable(RuntimeError):
    pass


class InjectorError(RuntimeError):
    pass


class Injector(Protocol):
    name: str

    def inject(self, exp: dict) -> dict: ...

    def rollback(self, exp: dict) -> dict: ...


class DryRunInjector:
    name = "dry_run"

    def inject(self, exp: dict) -> dict:
        return {
            "mode": "dry_run",
            "applied": False,
            "fault_type": exp["fault_type"],
            "target": exp["target"],
            "parameters": exp["parameters"],
            "note": "no fault was applied",
        }

    def rollback(self, exp: dict) -> dict:
        return {"mode": "dry_run", "applied": False, "note": "nothing to roll back"}


class DockerInjector:
    name = "docker"

    def __init__(self, base_url: str | None = None, token: str | None = None,
                 ttl_margin_s: int = 20, client: httpx.Client | None = None):
        self.ttl_margin_s = ttl_margin_s
        self._client = client or httpx.Client(
            base_url=base_url or os.getenv("FAULT_AGENT_URL", "http://fault-agent:8095"),
            headers={"X-Agent-Token": token if token is not None else os.getenv("FAULT_AGENT_TOKEN", "")},
            timeout=httpx.Timeout(90.0, connect=3.0),  # stop/start of a database can take a while
        )

    def health(self) -> dict:
        try:
            response = self._client.get("/health", timeout=3.0)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise InjectorUnavailable(f"fault agent unreachable: {exc}")

    def inject(self, exp: dict) -> dict:
        container = exp.get("container")
        if not container:
            raise InjectorError(f"target '{exp['target']}' has no container mapping")
        payload = {
            "experiment_id": exp["id"],
            "container": container,
            "fault_type": exp["fault_type"],
            "parameters": exp["parameters"],
            "ttl_s": exp["duration_s"] + self.ttl_margin_s,
        }
        response = self._request("POST", "/faults", json=payload)
        if response.status_code != 201:
            raise InjectorError(f"fault agent rejected the fault ({response.status_code}): {_detail(response)}")
        return {"mode": "docker", **response.json()}

    def rollback(self, exp: dict) -> dict:
        response = self._request("DELETE", f"/faults/{exp['id']}")
        if response.status_code != 200:
            raise InjectorError(f"fault agent rollback failed ({response.status_code}): {_detail(response)}")
        return {"mode": "docker", **response.json()}

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            return self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise InjectorError(f"fault agent unreachable: {exc}")


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("detail", response.text))[:300]
    except ValueError:
        return response.text[:300]


_docker: DockerInjector | None = None


def docker_injector() -> DockerInjector:
    global _docker
    if _docker is None:
        _docker = DockerInjector()
    return _docker


def injector_for(exp: dict) -> Injector:
    if exp["dry_run"]:
        return DryRunInjector()
    return docker_injector()


def real_run_preflight() -> list[str]:
    """Reasons a REAL (non-dry-run) experiment cannot start right now; empty means go."""
    errors = []
    if os.getenv("FAULTSCOPE_ENV") != "local":
        errors.append("real fault injection is only allowed when FAULTSCOPE_ENV=local")
        return errors
    try:
        docker_injector().health()
    except InjectorUnavailable as exc:
        errors.append(str(exc))
    return errors
