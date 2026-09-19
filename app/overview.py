"""Aggregated read models for the Control Center (dashboard and log feed). Read-only."""
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from sqlalchemy import text

from app.ai.models import KnowledgeChunkRow
from app.analysis.models import FailureSignatureRow
from app.analysis.service import resilience_report
from app.database import SessionLocal
from app.experiments.engine import get_engine
from app.experiments.models import ExperimentRow
from app.models import ServiceRow
from app.prediction.service import live_prediction
from app.redis_client import get_redis
from app.remediation.engine import RemediationEngine  # noqa: F401  (type only)
from app.remediation.routes import get_remediation_engine

router = APIRouter(prefix="/api", tags=["Control Center"])


def _live_state() -> dict[str, dict]:
    try:
        r = get_redis()
        return {key.removeprefix("service:health:"): r.hgetall(key) for key in r.scan_iter("service:health:*")}
    except Exception:
        return {}


@router.get("/overview")
def overview():
    """One call for the dashboard: services with live status, active experiment, recent activity, top risks."""
    prediction = live_prediction()
    live = _live_state()
    with SessionLocal() as db:
        services = []
        for s in db.query(ServiceRow).order_by(ServiceRow.name).all():
            state = prediction["services"].get(s.name)
            emitted = int((live.get(s.name) or {}).get("total_events", 0) or 0) > 0
            services.append({
                "name": s.name, "kind": s.kind, "technology": s.technology, "container": s.container,
                "last_seen": (live.get(s.name) or {}).get("last_seen"),
                "total_events": int((live.get(s.name) or {}).get("total_events", 0) or 0),
                # normal | anomalous | insufficient_data (emits telemetry but no recent traffic) | no_telemetry
                "status": (state or {}).get("status", "insufficient_data" if emitted else "no_telemetry"),
                "modes": (state or {}).get("modes", []),
            })
        active = db.query(ExperimentRow).filter(ExperimentRow.active_slot.is_(True)).first()
        counts = {
            "experiments": db.query(ExperimentRow).count(),
            "signatures": db.query(FailureSignatureRow).count(),
            "knowledge_chunks": db.query(KnowledgeChunkRow).count(),
        }
    engine = get_engine()
    recent = engine.list_experiments(limit=6)
    report = resilience_report(SessionLocal)
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "services": services,
        "active_experiment": engine.get(active.id) if active else None,
        "recent_experiments": recent,
        "prediction": {"status": prediction["status"], "anomalous_services": prediction["anomalous_services"],
                       "warnings": prediction["warnings"][:3]},
        "critical_dependencies": report["criticality"][:4],
        "noise_floor": report["noise_floor"],
        "recent_actions": get_remediation_engine().list_actions(5),
        "counts": counts,
    }


@router.get("/events/recent")
def recent_events(limit: int = Query(80, ge=1, le=300), service: str | None = None):
    """Merged feed of recent telemetry and experiment lifecycle events for the log view (newest first)."""
    with SessionLocal() as db:
        params = {"n": limit, "service": service}
        telemetry = db.execute(text("""
            select timestamp, service, event_type, severity, status_code, error_type, latency_ms, trace_id,
                   metadata->>'path' as path
            from telemetry_events where (cast(:service as text) is null or service = :service)
            order by timestamp desc limit :n"""), params).all()
        lifecycle = db.execute(text("""
            select e.timestamp, x.target as service, e.event_type, e.experiment_id
            from experiment_events e join experiments x on x.id = e.experiment_id
            where (cast(:service as text) is null or x.target = :service)
            order by e.timestamp desc limit :n"""), params).all()
    entries = [{"ts": r.timestamp.isoformat(), "source": "telemetry", "service": r.service, "type": r.event_type,
                "severity": r.severity, "status": r.status_code, "error": r.error_type, "latency_ms": r.latency_ms,
                "trace_id": r.trace_id, "path": r.path} for r in telemetry]
    entries += [{"ts": r.timestamp.isoformat(), "source": "experiment", "service": r.service, "type": r.event_type,
                 "severity": "INFO", "experiment_id": r.experiment_id} for r in lifecycle]
    entries.sort(key=lambda e: e["ts"], reverse=True)
    return {"count": min(len(entries), limit), "events": entries[:limit]}
