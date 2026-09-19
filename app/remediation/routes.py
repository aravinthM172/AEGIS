import os

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.ai.models import AnalysisRow
from app.database import SessionLocal
from app.prediction.service import live_prediction
from app.redis_client import get_redis
from app.remediation.auth import require_role
from app.remediation.engine import (
    GatewayProbe,
    InvalidState,
    NotFound,
    RemediationEngine,
    RemediationError,
)
from app.remediation.executor import AgentExecutor, CacheExecutor, Executor
from app.remediation.policy import action_for_fault

router = APIRouter(prefix="/api", tags=["Remediation"])

_engine: RemediationEngine | None = None


def get_remediation_engine() -> RemediationEngine:
    global _engine
    if _engine is None:
        _engine = RemediationEngine(
            Executor(AgentExecutor(), CacheExecutor(get_redis)),
            GatewayProbe(base_url=os.getenv("WORKLOAD_URL", "http://gateway:8090")),
        )
    return _engine


class ActionRequest(BaseModel):
    action: str
    target: str
    params: dict = Field(default_factory=dict)
    mode: str = "dry_run"
    strategies: list[str] = Field(default_factory=list)


class FromAnalysisRequest(BaseModel):
    analysis_id: str
    service: str
    recommendation_index: int = 0
    mode: str = "dry_run"


def _guard(call):
    try:
        return call()
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=f"Unknown action '{exc}'")
    except InvalidState as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RemediationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/actions", status_code=201)
def request_action(body: ActionRequest, actor: str = Depends(require_role("operator"))):
    """Propose an allowlisted action. Default mode is dry_run: the policy verdict is recorded, nothing runs."""
    return _guard(lambda: get_remediation_engine().request(
        action=body.action, target=body.target, params=body.params, mode=body.mode, requested_by=actor,
        strategies=body.strategies))


@router.post("/actions/from-analysis", status_code=201)
def action_from_analysis(body: FromAnalysisRequest, actor: str = Depends(require_role("operator"))):
    """AI recommendation -> policy engine. The model only names an allowlisted action; it never supplies a command."""
    with SessionLocal() as db:
        analysis = db.get(AnalysisRow, body.analysis_id)
        if analysis is None:
            raise HTTPException(status_code=404, detail=f"Unknown analysis '{body.analysis_id}'")
        if analysis.status != "accepted":
            raise HTTPException(status_code=409, detail=f"analysis is {analysis.status}; only accepted (validated) "
                                                        "analyses can drive an action")
        recs = (analysis.diagnosis or {}).get("recommended_actions", [])
        evidence = analysis.evidence or {}
    if not 0 <= body.recommendation_index < len(recs):
        raise HTTPException(status_code=422, detail=f"recommendation_index must be 0..{len(recs) - 1}")
    rec = recs[body.recommendation_index]
    if rec["allowlisted_action"] == "NONE":
        raise HTTPException(status_code=422, detail="that recommendation has no allowlisted action (advice only)")
    allowed_services = set(evidence.get("measured", {}).get("affected_services", [])) | {evidence["experiment"]["target"]}
    if body.service not in allowed_services:
        raise HTTPException(status_code=422, detail=f"'{body.service}' is not supported by the analysis evidence "
                                                    f"(allowed: {sorted(allowed_services)})")
    return _guard(lambda: get_remediation_engine().request(
        action=rec["allowlisted_action"], target=body.service, mode=body.mode, requested_by=actor,
        source="analysis", source_ref=body.analysis_id))


@router.get("/actions")
def list_actions(limit: int = Query(30, ge=1, le=200)):
    actions = get_remediation_engine().list_actions(limit)
    return {"count": len(actions), "actions": actions}


@router.get("/actions/{action_id}")
def get_action(action_id: str):
    return _guard(lambda: get_remediation_engine().get(action_id))


@router.post("/actions/{action_id}/approve")
def approve_action(action_id: str, actor: str = Depends(require_role("admin"))):
    return _guard(lambda: get_remediation_engine().approve(action_id, actor))


@router.post("/actions/{action_id}/reject")
def reject_action(action_id: str, actor: str = Depends(require_role("admin"))):
    return _guard(lambda: get_remediation_engine().reject(action_id, actor))


@router.get("/audit-log")
def audit_log(limit: int = Query(100, ge=1, le=500), _: str = Depends(require_role("operator"))):
    entries = get_remediation_engine().audit_log(limit)
    return {"count": len(entries), "entries": entries}


@router.get("/remediation/recommendations")
def recommendations():
    """Suggested actions for currently developing failures. Suggestions only: executing goes through POST /api/actions."""
    engine = get_remediation_engine()
    prediction = live_prediction()
    suggestions = []
    for w in prediction["warnings"]:
        action = action_for_fault(w["likely_fault"])
        if action is None:
            continue
        suggestions.append({
            "basis": "prediction", "warning": {k: w[k] for k in ("likely_target", "likely_fault", "similarity", "stage",
                                                                "confidence", "supporting_signatures")},
            "suggested_action": action, "target": w["likely_target"],
            "policy": engine.dry_evaluate(action, w["likely_target"]),
        })
    return {"status": prediction["status"], "anomalous_services": prediction["anomalous_services"],
            "suggestions": suggestions}
