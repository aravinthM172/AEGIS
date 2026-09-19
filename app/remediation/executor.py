"""Remediation executors. They run only what the policy engine already allowed, and only allowlisted primitives."""
import os

import httpx

from app.remediation.policy import CACHE_PREFIX

AGENT_ACTIONS = {"RESTART_SERVICE": "restart", "SCALE_SERVICE": "lift_cpu_cap"}


class ExecutionError(RuntimeError):
    pass


class AgentExecutor:
    """Docker-level actions via the fault-agent (the only component with Docker access)."""

    def __init__(self, base_url: str | None = None, token: str | None = None, client: httpx.Client | None = None):
        self._client = client or httpx.Client(
            base_url=base_url or os.getenv("FAULT_AGENT_URL", "http://fault-agent:8095"),
            headers={"X-Agent-Token": token if token is not None else os.getenv("FAULT_AGENT_TOKEN", "")},
            timeout=httpx.Timeout(90.0, connect=3.0))

    def execute(self, action: str, container: str) -> dict:
        try:
            response = self._client.post("/actions", json={"action": AGENT_ACTIONS[action], "container": container})
        except httpx.HTTPError as exc:
            raise ExecutionError(f"fault agent unreachable: {exc}")
        if response.status_code != 200:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise ExecutionError(f"fault agent refused/failed ({response.status_code}): {str(detail)[:300]}")
        return response.json()


class CacheExecutor:
    """CLEAR_CACHE: deletes keys under the cache prefix only (re-checked here, defence in depth)."""

    def __init__(self, redis_factory):
        self._redis = redis_factory

    def clear(self, prefix: str) -> dict:
        if not prefix.startswith(CACHE_PREFIX):
            raise ExecutionError(f"refusing to delete keys outside '{CACHE_PREFIX}'")
        client = self._redis()
        deleted = 0
        for key in client.scan_iter(match=f"{prefix}*", count=200):
            deleted += client.delete(key)
        return {"action": "clear_cache", "prefix": prefix, "deleted_keys": deleted}


class Executor:
    def __init__(self, agent: AgentExecutor, cache: CacheExecutor):
        self.agent, self.cache = agent, cache

    def execute(self, action: str, service: dict, params: dict) -> dict:
        if action in AGENT_ACTIONS:
            return self.agent.execute(action, service["container"])
        if action == "CLEAR_CACHE":
            return self.cache.clear(params.get("prefix", CACHE_PREFIX))
        raise ExecutionError(f"no executor for action '{action}'")
