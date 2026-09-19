import logging
import os
import threading
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router
from app.ai import models as _ai_models  # noqa: F401  (registers tables)
from app.ai.routes import router as ai_router
from app.analysis import models as _analysis_models  # noqa: F401  (registers tables)
from app.analysis.routes import router as analysis_router
from app.overview import router as overview_router
from app.twin.routes import router as twin_router
from app.experiments import campaign as _campaign  # noqa: F401  (registers tables)
from app.experiments import models as _experiment_models  # noqa: F401  (registers tables)
from app.experiments.campaign import get_campaign_runner
from app.experiments.campaign_routes import router as campaign_router
from app.prediction.routes import router as prediction_router
from app.remediation import models as _remediation_models  # noqa: F401  (registers tables)
from app.remediation.routes import router as remediation_router
from app.experiments.engine import get_engine
from app.experiments.injectors import start_runtime_recovery
from app.experiments.models import ensure_columns
from app.experiments.routes import router as experiments_router
from app.database import Base, SessionLocal, engine
from app.topology_graph import TopologyError
from app.topology_registry import seed_registry
from app.topology_routes import router as topology_router
from app.security import check_startup, install_read_auth
from app.telemetry_middleware import install as install_telemetry

logger = logging.getLogger(__name__)


def _refresh_knowledge() -> None:
    """Index repo documents and experiment reports; never blocks or breaks startup."""
    try:
        from app.ai.service import get_kb

        logger.info("knowledge base: %s", get_kb().reindex())
    except Exception:
        logger.warning("knowledge base refresh failed", exc_info=True)

app = FastAPI(
    title="Aegis",
    description="AI-Native Distributed Observability & Incident Intelligence Platform",
    version="1.0.0",
)

app.include_router(router)
app.include_router(topology_router)
app.include_router(experiments_router)
app.include_router(campaign_router)
app.include_router(analysis_router)
app.include_router(ai_router)
app.include_router(prediction_router)
app.include_router(overview_router)
app.include_router(twin_router)
app.include_router(remediation_router)


install_read_auth(app)   # optional (FAULTSCOPE_REQUIRE_AUTH_FOR_READS=1); registered before telemetry so 401s are recorded
install_telemetry(app)

# The Control Center (browser) calls this API from another origin. Reads and the API-key header only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("FRONTEND_ORIGINS", "http://localhost:3000").split(",") if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key", "Content-Type"],
)


def _initialize() -> None:
    """One initialisation attempt. Raises if the database is not reachable yet."""
    Base.metadata.create_all(bind=engine)
    ensure_columns(engine)
    with SessionLocal() as db:
        seed_registry(db)
    recovered = get_engine().recover_orphans()
    if recovered:
        logger.warning("rolled back experiments orphaned by a restart: %s", recovered)
    interrupted = get_campaign_runner().recover_interrupted()
    if interrupted:
        logger.warning("campaigns interrupted by a restart: %s", interrupted)
    threading.Thread(target=_refresh_knowledge, name="kb-refresh", daemon=True).start()


def _initialize_with_retry(attempts: int = 90, delay_s: float = 5.0) -> None:
    """Start-order independent: keep trying until the database is up (Kubernetes gives no ordering guarantee)."""
    for attempt in range(1, attempts + 1):
        try:
            _initialize()
            logger.info("control plane initialised (attempt %d)", attempt)
            return
        except TopologyError:
            logger.error("config/topology.json is invalid; service registry NOT updated", exc_info=True)
            return
        except Exception as exc:
            logger.warning("initialisation attempt %d/%d failed (%s); retrying in %.0fs",
                           attempt, attempts, type(exc).__name__, delay_s)
            time.sleep(delay_s)
    logger.error("control plane could not initialise after %d attempts", attempts)


@app.on_event("startup")
def init_db():
    start_runtime_recovery()  # Kubernetes runtime only: revert leftover faults, start the expiry watchdog
    check_startup()  # warns about development secrets outside local mode; FAULTSCOPE_STRICT_SECURITY=1 refuses to start
    # Off the startup path so the API (and its health probe) is available immediately.
    threading.Thread(target=_initialize_with_retry, name="init", daemon=True).start()


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