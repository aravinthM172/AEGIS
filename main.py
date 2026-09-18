import asyncio
import logging
import time
import uuid

from fastapi import FastAPI, Request

from app.api import router
from app.database import Base, SessionLocal, engine
from app.topology_graph import TopologyError
from app.topology_registry import seed_registry
from app.topology_routes import router as topology_router
from app.telemetry import SERVICE_NAME, TelemetryEvent, emit

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Aegis",
    description="AI-Native Distributed Observability & Incident Intelligence Platform",
    version="1.0.0",
)

app.include_router(router)
app.include_router(topology_router)


@app.middleware("http")
async def telemetry_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    trace_id = request.headers.get("x-trace-id") or request_id
    started = time.perf_counter()
    status_code = 500
    error_type = None

    try:
        response = await call_next(request)
        status_code = response.status_code
        if status_code >= 400:
            error_type = f"http_{status_code // 100}xx"
        return response
    except Exception as exc:
        error_type = type(exc).__name__
        raise
    finally:
        route = request.scope.get("route")
        event = TelemetryEvent(
            service=SERVICE_NAME,
            event_type="http_request",
            severity="ERROR" if status_code >= 500 else "WARN" if status_code >= 400 else "INFO",
            request_id=request_id,
            trace_id=trace_id,
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            status_code=status_code,
            error_type=error_type,
            metadata={
                "method": request.method,
                "path": getattr(route, "path", request.url.path),
            },
        )
        # emit() can block on first Kafka connect; keep it off the event loop
        asyncio.get_running_loop().run_in_executor(None, emit, event)


@app.on_event("startup")
def init_db():
    try:
        Base.metadata.create_all(bind=engine)
        with SessionLocal() as db:
            seed_registry(db)
    except TopologyError:
        logger.error("config/topology.json is invalid; service registry NOT updated", exc_info=True)
    except Exception:
        logger.warning("Database unavailable at startup; will retry on first use", exc_info=True)


@app.get("/")
def root():
    return {
        "service": "Aegis",
        "status": "running",
        "version": "1.0.0",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy"
    }