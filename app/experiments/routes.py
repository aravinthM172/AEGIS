from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.experiments.catalog import catalog_as_dict
from app.experiments.engine import (
    ActiveExperimentExists,
    InvalidState,
    NotFound,
    ValidationFailed,
    get_engine,
)

router = APIRouter(prefix="/api/experiments", tags=["Experiments"])


class ExperimentRequest(BaseModel):
    target: str
    fault_type: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    duration_s: int
    baseline_s: int = 10
    recovery_s: int = 15
    dry_run: bool = True
    workload_rps: float = 0.0
    workload_n: int = 100000
    hypothesis: str | None = None
    name: str | None = None


@router.get("/catalog")
def catalog():
    """Supported fault types, parameter bounds, protected targets and which faults have a real injector."""
    return catalog_as_dict()


@router.post("", status_code=201)
def create_experiment(body: ExperimentRequest):
    try:
        return get_engine().create(**body.model_dump())
    except ValidationFailed as exc:
        return JSONResponse(status_code=422, content={"errors": exc.errors})
    except ActiveExperimentExists as exc:
        return JSONResponse(status_code=409, content={
            "error": "another experiment is already active",
            "active_experiment_id": exc.active_id,
        })


@router.get("")
def list_experiments(status: str | None = None, target: str | None = None,
                     limit: int = Query(50, ge=1, le=200)):
    experiments = get_engine().list_experiments(status=status, target=target, limit=limit)
    return {"count": len(experiments), "experiments": experiments}


@router.get("/{experiment_id}")
def get_experiment(experiment_id: str):
    try:
        return get_engine().get(experiment_id)
    except NotFound:
        raise HTTPException(status_code=404, detail=f"Unknown experiment '{experiment_id}'")


@router.get("/{experiment_id}/result")
def get_result(experiment_id: str):
    """Measured blast radius for a finished experiment (computed automatically a few seconds after it ends)."""
    try:
        exp = get_engine().get(experiment_id)
    except NotFound:
        raise HTTPException(status_code=404, detail=f"Unknown experiment '{experiment_id}'")
    result = exp["result"]
    if not result or result.get("status") is None:
        raise HTTPException(status_code=404, detail="no analysis yet (experiment still running, or analysis pending)")
    return {k: v for k, v in result.items() if k != "workload_samples"}


@router.post("/{experiment_id}/analyze")
def reanalyze(experiment_id: str):
    """Recompute the analysis from stored telemetry (e.g. after late-arriving events)."""
    from app.analysis.service import analyze_and_store
    from app.database import SessionLocal

    try:
        exp = get_engine().get(experiment_id)
    except NotFound:
        raise HTTPException(status_code=404, detail=f"Unknown experiment '{experiment_id}'")
    if exp["status"] not in ("COMPLETED", "ABORTED", "FAILED"):
        raise HTTPException(status_code=409, detail=f"experiment is {exp['status']}; analysis needs a finished experiment")
    result = analyze_and_store(SessionLocal, experiment_id)
    return {k: v for k, v in result.items() if k != "workload_samples"}


@router.post("/{experiment_id}/abort", status_code=202)
def abort_experiment(experiment_id: str):
    """Kill switch: the runner stops waiting, rolls the fault back and finishes as ABORTED."""
    try:
        return get_engine().abort(experiment_id)
    except NotFound:
        raise HTTPException(status_code=404, detail=f"Unknown experiment '{experiment_id}'")
    except InvalidState as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{experiment_id}/rollback")
def retry_rollback(experiment_id: str):
    """Retry rollback for an experiment in ROLLBACK_FAILED (frees the active slot when it succeeds)."""
    try:
        return get_engine().retry_rollback(experiment_id)
    except NotFound:
        raise HTTPException(status_code=404, detail=f"Unknown experiment '{experiment_id}'")
    except InvalidState as exc:
        raise HTTPException(status_code=409, detail=str(exc))
