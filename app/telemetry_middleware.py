"""Shared FastAPI telemetry middleware: one http_request event per request.

Handlers can enrich the event through request.state:
  request.state.request_id / trace_id   correlation ids (set here; forward them downstream)
  request.state.error_type              overrides the default error_type (e.g. dependency_timeout)
  request.state.dependency              name of the downstream service that failed
"""
import asyncio
import time
import uuid

from fastapi import FastAPI, Request

from app.telemetry import SERVICE_NAME, TelemetryEvent, emit


def emit_async(event: TelemetryEvent) -> None:
    """emit() can block on the first Kafka connect; keep it off the event loop."""
    asyncio.get_running_loop().run_in_executor(None, emit, event)


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def telemetry_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        trace_id = request.headers.get("x-trace-id") or request_id
        request.state.request_id = request_id
        request.state.trace_id = trace_id

        started = time.perf_counter()
        status_code = 500
        error_type = None

        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            if status_code >= 400:
                error_type = f"http_{status_code // 100}xx"
            return response
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            error_type = getattr(request.state, "error_type", None) or error_type
            route = request.scope.get("route")
            metadata = {
                "method": request.method,
                "path": getattr(route, "path", request.url.path),
            }
            dependency = getattr(request.state, "dependency", None)
            if dependency:
                metadata["dependency"] = dependency

            emit_async(TelemetryEvent(
                service=SERVICE_NAME,
                event_type="http_request",
                severity="ERROR" if status_code >= 500 else "WARN" if status_code >= 400 else "INFO",
                request_id=request_id,
                trace_id=trace_id,
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
                status_code=status_code,
                error_type=error_type,
                metadata=metadata,
            ))
