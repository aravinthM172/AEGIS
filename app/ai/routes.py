from fastapi import Depends, APIRouter, HTTPException, Query
from app.remediation.auth import require_role
from pydantic import BaseModel

from app.ai import service
from app.experiments.engine import NotFound, get_engine

router = APIRouter(prefix="/api", tags=["AI"])


class AnalysisRequest(BaseModel):
    experiment_id: str | None = None
    question: str | None = None


@router.post("/analysis", status_code=202, dependencies=[Depends(require_role("operator"))])
def request_analysis(body: AnalysisRequest):
    """Start an evidence-grounded LLM analysis. Poll GET /api/analysis/{id}; inference can take a minute."""
    try:
        return service.start_analysis(body.experiment_id, body.question, get_engine())
    except NotFound:
        raise HTTPException(status_code=404, detail=f"Unknown experiment '{body.experiment_id}'")
    except service.AnalysisError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/analysis")
def list_analyses(limit: int = Query(20, ge=1, le=100)):
    analyses = service.list_analyses(limit)
    return {"count": len(analyses), "analyses": analyses}


@router.get("/analysis/{analysis_id}")
def get_analysis(analysis_id: str):
    analysis = service.get_analysis(analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail=f"Unknown analysis '{analysis_id}'")
    return analysis


@router.get("/knowledge")
def knowledge_stats():
    return service.get_kb().stats()


@router.post("/knowledge/reindex", dependencies=[Depends(require_role("operator"))])
def knowledge_reindex():
    """Reload repo documents and experiment reports; embed new chunks if an embedding model is available."""
    return service.get_kb().reindex()


@router.get("/knowledge/search")
def knowledge_search(q: str, k: int = Query(4, ge=1, le=10)):
    results = service.get_kb().search(q, k=k)
    return {"query": q, "retrieval": service.get_kb().retrieval_mode(), "count": len(results), "results": results}
