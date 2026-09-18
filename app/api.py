import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Incident, TelemetryEventRow
from app.kafka_client import check_kafka, publish_incident
from app.redis_client import get_redis

router = APIRouter(prefix="/api", tags=["Aegis"])

METRICS_CACHE_KEY = "cache:metrics"
METRICS_CACHE_TTL_SECONDS = 10

INCIDENT_RATE_LIMIT = 10
INCIDENT_RATE_WINDOW_SECONDS = 60


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/health")
def health():
    return {
        "service": "Aegis",
        "status": "healthy",
        "version": "1.0.0"
    }


@router.get("/database")
def database_health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))

    return {
        "service": "postgresql",
        "status": "connected"
    }


@router.get("/kafka")
def kafka_health():
    try:
        details = check_kafka()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Kafka unavailable: {type(exc).__name__}: {exc}"
        )

    return {
        "service": "kafka",
        "status": "available",
        **details
    }


@router.get("/redis")
def redis_health():
    try:
        get_redis().ping()

        return {
            "service": "redis",
            "status": "connected"
        }
    except Exception as exc:
        return {
            "service": "redis",
            "status": "disconnected",
            "error": str(exc)
        }


def _check_rate_limit(service: str):
    try:
        r = get_redis()
        key = f"ratelimit:incidents:{service}"

        count = r.incr(key)

        if count == 1:
            r.expire(key, INCIDENT_RATE_WINDOW_SECONDS)

        if count > INCIDENT_RATE_LIMIT:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Rate limit exceeded: max {INCIDENT_RATE_LIMIT} "
                    f"incidents per {INCIDENT_RATE_WINDOW_SECONDS}s for "
                    f"service '{service}'"
                )
            )
    except HTTPException:
        raise
    except Exception:
        pass


@router.post("/incidents")
def create_incident(
    service: str,
    severity: str,
    message: str,
    db: Session = Depends(get_db)
):

    _check_rate_limit(service)

    incident = Incident(
        service=service,
        severity=severity,
        message=message,
        status="open"
    )

    db.add(incident)
    db.commit()
    db.refresh(incident)

    event = {
        "id": incident.id,
        "service": incident.service,
        "severity": incident.severity,
        "message": incident.message,
        "status": incident.status
    }

    kafka_result = publish_incident(event)

    try:
        r = get_redis()
        r.hset(f"service:state:{service}", mapping={
            "last_severity": incident.severity,
            "last_status": incident.status,
            "last_incident_id": incident.id,
            "last_message": incident.message,
            "updated_at": incident.created_at.isoformat() if incident.created_at else ""
        })
        r.delete(METRICS_CACHE_KEY)
    except Exception:
        pass

    return {
        "incident": event,
        "kafka": kafka_result
    }


@router.get("/services/{service}/state")
def get_service_state(service: str):
    try:
        state = get_redis().hgetall(f"service:state:{service}")
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Redis unavailable: {exc}"
        )

    if not state:
        raise HTTPException(
            status_code=404,
            detail=f"No known state for service '{service}'"
        )

    return {
        "service": service,
        **state
    }


@router.get("/incidents")
def get_incidents(db: Session = Depends(get_db)):

    incidents = (
        db.query(Incident)
        .order_by(Incident.id.desc())
        .all()
    )

    return {
        "count": len(incidents),
        "incidents": [
            {
                "id": incident.id,
                "service": incident.service,
                "severity": incident.severity,
                "message": incident.message,
                "status": incident.status
            }
            for incident in incidents
        ]
    }


@router.get("/metrics")
def metrics(db: Session = Depends(get_db)):

    try:
        cached = get_redis().get(METRICS_CACHE_KEY)
        if cached is not None:
            return json.loads(cached)
    except Exception:
        pass

    total = db.query(Incident).count()

    open_count = (
        db.query(Incident)
        .filter(Incident.status == "open")
        .count()
    )

    by_severity = dict(
        db.query(Incident.severity, func.count(Incident.id))
        .group_by(Incident.severity)
        .all()
    )

    result = {
        "service": "aegis",
        "total_incidents": total,
        "open_incidents": open_count,
        "incidents_by_severity": by_severity
    }

    try:
        get_redis().setex(
            METRICS_CACHE_KEY,
            METRICS_CACHE_TTL_SECONDS,
            json.dumps(result)
        )
    except Exception:
        pass

    return result

@router.get("/telemetry/services")
def telemetry_services():
    """Live per-service state from Redis (written by the telemetry consumer)."""
    try:
        r = get_redis()
        services = {}
        for key in r.scan_iter("service:health:*"):
            services[key.removeprefix("service:health:")] = r.hgetall(key)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {exc}")

    return {"count": len(services), "services": services}


@router.get("/telemetry/events")
def telemetry_events(
    service: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Most recent stored telemetry events (Postgres)."""
    query = db.query(TelemetryEventRow).order_by(TelemetryEventRow.timestamp.desc())
    if service:
        query = query.filter(TelemetryEventRow.service == service)

    rows = query.limit(min(max(limit, 1), 500)).all()

    return {
        "count": len(rows),
        "events": [
            {
                "event_id": row.event_id,
                "timestamp": row.timestamp.isoformat(),
                "service": row.service,
                "event_type": row.event_type,
                "severity": row.severity,
                "request_id": row.request_id,
                "trace_id": row.trace_id,
                "latency_ms": row.latency_ms,
                "status_code": row.status_code,
                "error_type": row.error_type,
                "metadata": row.meta,
            }
            for row in rows
        ],
    }
