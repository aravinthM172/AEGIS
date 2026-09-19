"""Deployment-safety helpers for the control plane.

Two independent, opt-in protections (both off by default so local development is unchanged):

  FAULTSCOPE_REQUIRE_AUTH_FOR_READS=1   every GET under /api needs a valid X-API-Key (any role), except health probes.
  FAULTSCOPE_STRICT_SECURITY=1          refuse to start outside FAULTSCOPE_ENV=local while a development secret is in use.

Outside FAULTSCOPE_ENV=local, using a development secret is always logged as a warning.
"""
import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.remediation.auth import authenticate, load_keys

logger = logging.getLogger("security")

DEV_API_KEYS = frozenset({"dev-operator-key", "dev-admin-key"})
DEV_AGENT_TOKEN = "dev-token-change-me"
OPEN_PATHS = frozenset({"/health", "/api/health"})


def insecure_defaults(env=None) -> list[str]:
    """Development secrets currently in use (empty list = none)."""
    env = os.environ if env is None else env
    found = []
    used = [k for k in load_keys(env.get("FAULTSCOPE_API_KEYS", "")) if k in DEV_API_KEYS]
    if used:
        found.append(f"development API key(s) in use: {', '.join(sorted(used))}")
    if env.get("FAULT_AGENT_TOKEN", DEV_AGENT_TOKEN) == DEV_AGENT_TOKEN:
        found.append("FAULT_AGENT_TOKEN is unset or the development default")
    return found


def check_startup(env=None) -> list[str]:
    """Log (or, with FAULTSCOPE_STRICT_SECURITY=1, raise) when development secrets run outside local mode."""
    env = os.environ if env is None else env
    if env.get("FAULTSCOPE_ENV") == "local":
        return []
    problems = insecure_defaults(env)
    if problems and env.get("FAULTSCOPE_STRICT_SECURITY") == "1":
        raise RuntimeError("refusing to start with development secrets outside FAULTSCOPE_ENV=local: "
                           + "; ".join(problems))
    for problem in problems:
        logger.warning("insecure configuration: %s", problem)
    return problems


def install_read_auth(app: FastAPI) -> None:
    """Optionally require an API key for reads. The flag is read per request so it can be switched without a rebuild."""

    @app.middleware("http")
    async def require_key_for_reads(request: Request, call_next):
        if (os.getenv("FAULTSCOPE_REQUIRE_AUTH_FOR_READS") == "1"
                and request.method in ("GET", "HEAD")
                and request.url.path.startswith("/api/")
                and request.url.path not in OPEN_PATHS):
            keys = load_keys()
            if not keys:
                return JSONResponse(status_code=503, content={
                    "detail": "reads require authentication but no API keys are configured (set FAULTSCOPE_API_KEYS)"})
            if authenticate(request.headers.get("x-api-key", ""), keys) is None:
                return JSONResponse(status_code=401, content={"detail": "missing or invalid X-API-Key"})
        return await call_next(request)
