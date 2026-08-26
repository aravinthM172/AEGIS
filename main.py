import logging

from fastapi import FastAPI

from app.api import router
from app.database import Base, engine

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Aegis",
    description="AI-Native Distributed Observability & Incident Intelligence Platform",
    version="1.0.0",
)

app.include_router(router)


@app.on_event("startup")
def init_db():
    try:
        Base.metadata.create_all(bind=engine)
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