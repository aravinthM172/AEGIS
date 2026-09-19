"""FaultScope demo workload: API gateway.

Entry point of the monitored call chain
    gateway -> java-service -> cpp-service
                            -> postgres
It is deliberately separate from the control plane so that experiments on the
workload never take down the component that measures them.
"""
import os
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.telemetry import SERVICE_NAME, TelemetryEvent
from app.fault_hook import install as install_fault_hook
from app.telemetry_middleware import emit_async, install

JAVA_SERVICE_URL = os.getenv("JAVA_SERVICE_URL", "http://java-service:8081")
TARGET = "java-service"

# Must exceed java-service's own downstream timeouts (cpp-service read timeout 2s + DB).
CONNECT_TIMEOUT_S = float(os.getenv("JAVA_CONNECT_TIMEOUT_S", "1.0"))
READ_TIMEOUT_S = float(os.getenv("JAVA_READ_TIMEOUT_S", "5.0"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = httpx.AsyncClient(
        base_url=JAVA_SERVICE_URL,
        timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
    )
    yield
    await app.state.client.aclose()


app = FastAPI(title="FaultScope Gateway", version="1.0.0", lifespan=lifespan)
install_fault_hook(app)  # registered first => inner; telemetry stays outermost and records injected errors
install(app)


@app.get("/health")
def health():
    return {"service": SERVICE_NAME, "status": "UP"}


@app.post("/api/jobs")
async def create_job(request: Request, n: int = 100000):
    """Forward to java-service POST /jobs, propagating request/trace ids."""
    started = time.perf_counter()
    response = None
    status = None
    error_type = None

    try:
        response = await request.app.state.client.post(
            "/jobs",
            params={"n": n},
            headers={
                "X-Request-ID": request.state.request_id,
                "X-Trace-Id": request.state.trace_id,
            },
        )
        status = response.status_code
        if status >= 400:
            error_type = f"http_{status // 100}xx"
    except httpx.TimeoutException:
        error_type = "timeout"
    except httpx.TransportError:
        error_type = "connection_error"
    finally:
        emit_async(TelemetryEvent(
            service=SERVICE_NAME,
            event_type="dependency_call",
            severity="INFO" if error_type is None else "WARN" if status and status < 500 else "ERROR",
            request_id=request.state.request_id,
            trace_id=request.state.trace_id,
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            status_code=status,
            error_type=error_type,
            metadata={"target": TARGET, "method": "POST", "path": "/jobs"},
        ))

    if response is not None and status < 400:
        return response.json()

    if response is not None and status < 500:
        # the caller's mistake (e.g. bad n), not a dependency failure
        return JSONResponse(status_code=status, content=_body(response))

    request.state.error_type = f"dependency_{error_type}"
    request.state.dependency = TARGET
    http_status = {"timeout": 504, "connection_error": 503}.get(error_type, 502)
    return JSONResponse(
        status_code=http_status,
        content={
            "error": "dependency_failure",
            "dependency": TARGET,
            "error_type": error_type,
            "upstream": _body(response) if response is not None else None,
        },
    )


def _body(response: httpx.Response):
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text[:200]}
