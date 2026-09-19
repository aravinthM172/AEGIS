from fastapi import Depends, APIRouter, HTTPException, Query
from app.remediation.auth import require_role

from app.analysis.models import FailureSignatureRow
from app.analysis.service import rebuild_all, resilience_report, signature_dict
from app.database import SessionLocal

router = APIRouter(prefix="/api", tags=["Resilience"])


@router.get("/signatures")
def list_signatures(target: str | None = None, fault_type: str | None = None, kind: str | None = None,
                    limit: int = Query(50, ge=1, le=200)):
    with SessionLocal() as db:
        query = db.query(FailureSignatureRow)
        for column, value in ((FailureSignatureRow.target, target), (FailureSignatureRow.fault_type, fault_type),
                              (FailureSignatureRow.kind, kind)):
            if value:
                query = query.filter(column == value)
        rows = query.order_by(FailureSignatureRow.created_at.desc()).limit(limit).all()
        return {"count": len(rows), "signatures": [signature_dict(r) for r in rows]}


@router.get("/signatures/{signature_id}")
def get_signature(signature_id: str):
    with SessionLocal() as db:
        row = db.get(FailureSignatureRow, signature_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Unknown signature '{signature_id}'")
        return signature_dict(row)


@router.get("/experiments/{experiment_id}/signature")
def experiment_signature(experiment_id: str):
    with SessionLocal() as db:
        row = db.query(FailureSignatureRow).filter_by(experiment_id=experiment_id).first()
        if row is None:
            raise HTTPException(status_code=404, detail="no signature (experiment not analysed yet, or not analyzable)")
        return signature_dict(row)


@router.post("/signatures/rebuild", dependencies=[Depends(require_role("operator"))])
def rebuild():
    """Re-analyse every finished experiment from stored telemetry and rebuild all signatures."""
    return rebuild_all(SessionLocal)


@router.get("/resilience")
def resilience():
    """Per-service resilience profiles, critical dependencies, noise floor and coverage - all measured."""
    return resilience_report(SessionLocal)


@router.get("/resilience/critical")
def critical_dependencies():
    """Dependencies ranked by MEASURED end-user impact when they fail (see /api/topology/impact for structure)."""
    return {"basis": "measured", "ranking": resilience_report(SessionLocal)["criticality"]}


@router.get("/resilience/{service}")
def service_resilience(service: str):
    report = resilience_report(SessionLocal)
    profile = report["services"].get(service)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"no resilience data for '{service}'")
    return {"basis": "measured", "noise_floor": report["noise_floor"], "thresholds": report["thresholds"], **profile}
