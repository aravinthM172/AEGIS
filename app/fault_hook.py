"""Service-side HTTP-error fault hook (used by the gateway).

The fault agent activates it through POST /_faults/http-error and removes it with DELETE.
Protected by FAULT_HOOK_TOKEN and self-expiring (ttl_s), so a dead agent cannot leave a
service failing requests. Register it BEFORE the telemetry middleware so the telemetry
middleware stays outermost and records the injected failures like any other response.
"""
import os
import random
import threading
import time

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

EXEMPT_PREFIXES = ("/health", "/_faults", "/docs", "/openapi")


class HttpErrorHook:
    def __init__(self):
        self._lock = threading.Lock()
        self._rate = 0.0
        self._status = 500
        self._expires_at = 0.0

    def activate(self, rate: float, status: int, ttl_s: int) -> dict:
        with self._lock:
            self._rate, self._status, self._expires_at = rate, status, time.monotonic() + ttl_s
        return self.state()

    def clear(self) -> dict:
        with self._lock:
            self._rate, self._expires_at = 0.0, 0.0
        return self.state()

    def state(self) -> dict:
        with self._lock:
            remaining = self._expires_at - time.monotonic()
            active = remaining > 0 and self._rate > 0
            return {"active": active, "rate": self._rate if active else 0.0, "status": self._status,
                    "expires_in_s": round(remaining, 1) if active else 0}

    def should_fail(self) -> int | None:
        """The status code to return for this request, or None to let it through."""
        with self._lock:
            if self._rate <= 0 or time.monotonic() >= self._expires_at:
                return None
            return self._status if random.random() < self._rate else None


hook = HttpErrorHook()


def install(app: FastAPI) -> None:
    router = APIRouter(prefix="/_faults")
    token = os.getenv("FAULT_HOOK_TOKEN", "")

    def authorize(supplied: str) -> None:
        if not token:
            raise HTTPException(status_code=503, detail="fault hook disabled: FAULT_HOOK_TOKEN not set")
        if supplied != token:
            raise HTTPException(status_code=401, detail="invalid fault hook token")

    @router.post("/http-error")
    async def activate(request: Request, x_fault_token: str = Header(default="")):
        authorize(x_fault_token)
        body = await request.json()
        rate, status, ttl = float(body["rate"]), int(body.get("status", 500)), int(body.get("ttl_s", 60))
        if not (0 < rate <= 1 and 500 <= status <= 599 and 1 <= ttl <= 900):
            raise HTTPException(status_code=422, detail="rate in (0,1], status 500-599, ttl_s 1-900")
        return hook.activate(rate, status, ttl)

    @router.delete("/http-error")
    def deactivate(x_fault_token: str = Header(default="")):
        authorize(x_fault_token)
        return hook.clear()

    @router.get("/http-error")
    def current(x_fault_token: str = Header(default="")):
        authorize(x_fault_token)
        return hook.state()

    app.include_router(router)

    @app.middleware("http")
    async def inject_errors(request: Request, call_next):
        if not request.url.path.startswith(EXEMPT_PREFIXES):
            status = hook.should_fail()
            if status is not None:
                request.state.error_type = "injected_fault"
                return JSONResponse(status_code=status, content={"error": "injected_fault"})
        return await call_next(request)
