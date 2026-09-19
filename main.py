import logging
import threading

from fastapi import FastAPI

from app.api import router
from app.ai import models as _ai_models  # noqa: F401  (registers tables)
from app.ai.routes import router as ai_router
from app.analysis import models as _analysis_models  # noqa: F401  (registers tables)
from app.analysis.routes import router as analysis_router
from app.experiments import campaign as _campaign  # noqa: F401  (registers tables)
from app.experiments import models as _experiment_models  # noqa: F401  (registers tables)
from app.experiments.campaign import get_campaign_runner
from app.experiments.campaign_routes import router as campaign_router
from app.prediction.routes import router as prediction_router
from app.remediation import models as _remediation_models  # noqa: F401  (registers tables)
from app.remediation.routes import router as remediation_router
from app.experiments.engine import get_engine
from app.experiments.models import ensure_columns
from app.experiments.routes import router as experiments_router
from app.database import Base, SessionLocal, engine
from app.topology_graph import TopologyError
from app.topology_registry import seed_registry
from app.topology_routes import router as topology_router
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
app.include_router(remediation_router)


install_telemetry(app)


@app.on_event("startup")
def init_db():
    try:
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