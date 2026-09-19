"""Fault agent HTTP API. Holds the Docker socket; exposes a narrow, token-protected surface."""
import hmac
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from agent.core import (
    Conflict,
    FaultError,
    FaultManager,
    NotAllowed,
    UnknownContainer,
    UnsupportedFault,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fault-agent")

TOKEN = os.getenv("FAULT_AGENT_TOKEN", "")
STATE_PATH = os.getenv("FAULT_AGENT_STATE", "/data/state.json")
NETEM_IMAGE = os.getenv("FAULT_AGENT_IMAGE", "faultscope/fault-agent:local")

manager: FaultManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global manager
    if not TOKEN:
        raise RuntimeError("FAULT_AGENT_TOKEN must be set; refusing to start unauthenticated")
    import docker  # imported here so the core stays importable without the SDK

    manager = FaultManager(docker.from_env(), STATE_PATH, NETEM_IMAGE,
                           hook_token=os.getenv("FAULT_HOOK_TOKEN", TOKEN))
    result = manager.revert_all_on_startup()
    if result["reverted"] or result["failed"]:
        logger.warning("startup revert: %s", result)
    yield


app = FastAPI(title="FaultScope Fault Agent", version="1.0.0", lifespan=lifespan)


def require_token(x_agent_token: str = Header(default="")) -> None:
    if not hmac.compare_digest(x_agent_token.encode(), TOKEN.encode()):
        raise HTTPException(status_code=401, detail="invalid agent token")


class FaultRequest(BaseModel):
    experiment_id: str
    container: str
    fault_type: str
    parameters: dict = Field(default_factory=dict)
    ttl_s: int = Field(ge=5, le=900)


@app.get("/health")
def health():
    return {"status": "UP", "supported_faults": manager.supported() if manager else []}


@app.post("/faults", status_code=201, dependencies=[Depends(require_token)])
def apply_fault(body: FaultRequest):
    try:
        return manager.apply(body.experiment_id, body.container, body.fault_type, body.parameters, body.ttl_s)
    except NotAllowed as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except UnknownContainer as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except UnsupportedFault as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except FaultError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/faults/{experiment_id}", dependencies=[Depends(require_token)])
def rollback_fault(experiment_id: str):
    try:
        return manager.rollback(experiment_id)
    except (UnknownContainer, FaultError) as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/faults", dependencies=[Depends(require_token)])
def list_faults():
    return {"active": manager.list_active()}


@app.get("/faults/history", dependencies=[Depends(require_token)])
def fault_history():
    return {"history": manager.history()}
