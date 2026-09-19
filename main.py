import logging

from fastapi import FastAPI

from app.api import router
from app.experiments import models as _experiment_models  # noqa: F401  (registers tables)
from app.experiments.engine import get_engine
from app.experiments.routes import router as experiments_router
from app.database import Base, SessionLocal, engine
from app.topology_graph import TopologyError
from app.topology_registry import seed_registry
from app.topology_routes import router as topology_router
from app.telemetry_middleware import install as install_telemetry

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Aegis",
    description="AI-Native Distributed Observability & Incident Intelligence Platform",
    version="1.0.0",
)

app.include_router(router)
app.include_router(topology_router)
app.include_router(experiments_router)


install_telemetry(app)


@app.on_event("startup")
def init_db():
    try:
        Base.metadata.create_all(bind=engine)
        with SessionLocal() as db:
            seed_registry(db)
        recovered = get_engine().recover_orphans()
        if recovered:
            logger.warning("rolled back experiments orphaned by a restart: %s", recovered)
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