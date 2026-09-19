from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.analysis.models import FailureSignatureRow
from app.database import SessionLocal
from app.experiments.catalog import FAULTS
from app.models import ServiceDependencyRow, ServiceRow
from app.twin.model import TwinModel, validate

router = APIRouter(prefix="/api/twin", tags=["Digital twin"])


def _load():
    with SessionLocal() as db:
        edges = [(e.source, e.target) for e in db.query(ServiceDependencyRow).all()]
        services = {s.name: s.kind for s in db.query(ServiceRow).all()}
        signatures = [{"id": r.id, "signature": r.signature} for r in db.query(FailureSignatureRow).all()]
    return edges, services, signatures


def _check(target: str, fault_type: str, services: dict) -> None:
    if target not in services:
        raise HTTPException(status_code=404, detail=f"unknown service '{target}'")
    if fault_type not in FAULTS:
        raise HTTPException(status_code=422, detail=f"unknown fault_type '{fault_type}'")


class Mutation(BaseModel):
    type: str
    source: str | None = None
    target: str | None = None
    service: str | None = None
    hit_ratio: float | None = Field(default=None, ge=0, le=1)
    count: int | None = Field(default=None, ge=2, le=10)


class ArchitectureRequest(BaseModel):
    target: str
    fault_type: str
    mutations: list[Mutation]


@router.get("/what-if")
def what_if(target: str, fault_type: str = Query("stop_container")):
    """What happens if `target` suffers `fault_type`? Measured results (if any) next to the twin's simulation."""
    edges, services, signatures = _load()
    _check(target, fault_type, services)
    twin = TwinModel(edges, signatures)
    return {"measured": twin.measured(target, fault_type), "simulated": twin.simulate(target, fault_type)}


@router.post("/architecture")
def architecture(body: ArchitectureRequest):
    """Compare the current architecture with a modified one under the same fault. The modified result is a SIMULATION."""
    edges, services, signatures = _load()
    _check(body.target, body.fault_type, services)
    mutations = [m.model_dump(exclude_none=True) for m in body.mutations]
    for m in mutations:
        for key in ("source", "target", "service"):
            if key in m and m[key] not in services:
                raise HTTPException(status_code=404, detail=f"unknown service '{m[key]}'")
        if m["type"] in ("queue", "fallback") and not {"source", "target"} <= set(m):
            raise HTTPException(status_code=422, detail=f"{m['type']} needs source and target")
        if m["type"] == "replicas" and not {"service", "count"} <= set(m):
            raise HTTPException(status_code=422, detail="replicas needs service and count")
        if m["type"] in ("queue", "fallback") and (m["source"], m["target"]) not in set(edges):
            raise HTTPException(status_code=422, detail=f"there is no dependency {m['source']} -> {m['target']} to change")
    try:
        return TwinModel(edges, signatures).compare(body.target, body.fault_type, mutations)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/validation")
def validation():
    """How accurate is the twin? Leave-one-combination-out against the measured experiments."""
    edges, _, signatures = _load()
    return validate(edges, signatures)
