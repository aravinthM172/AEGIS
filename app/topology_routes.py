from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import ServiceDependencyRow, ServiceRow
from app.redis_client import get_redis
from app.topology_graph import dependencies, dependents, impact_ranking

router = APIRouter(prefix="/api", tags=["Topology"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _service_dict(row: ServiceRow) -> dict:
    return {
        "name": row.name,
        "kind": row.kind,
        "technology": row.technology,
        "container": row.container,
        "description": row.description,
    }


def _edge_dict(row: ServiceDependencyRow) -> dict:
    return {
        "source": row.source,
        "target": row.target,
        "relation": row.relation,
        "origin": row.origin,
        "evidence": row.evidence,
    }


def _edges(db: Session) -> list[ServiceDependencyRow]:
    return db.query(ServiceDependencyRow).order_by(ServiceDependencyRow.source, ServiceDependencyRow.target).all()


def _require_service(db: Session, name: str) -> ServiceRow:
    row = db.get(ServiceRow, name)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown service '{name}'")
    return row


def _telemetry_facts(name: str) -> dict | None:
    """Last-seen / event counts from the telemetry consumer (Redis). Facts only — no health inference."""
    try:
        state = get_redis().hgetall(f"service:health:{name}")
    except Exception:
        return None
    return state or None


@router.get("/services")
def list_services(db: Session = Depends(get_db)):
    rows = db.query(ServiceRow).order_by(ServiceRow.name).all()
    return {
        "count": len(rows),
        "services": [{**_service_dict(r), "telemetry": _telemetry_facts(r.name)} for r in rows],
    }


@router.get("/services/{name}")
def get_service(name: str, db: Session = Depends(get_db)):
    row = _require_service(db, name)
    edges = _edges(db)
    return {
        **_service_dict(row),
        "telemetry": _telemetry_facts(name),
        "depends_on": [_edge_dict(e) for e in edges if e.source == name],
        "depended_on_by": [_edge_dict(e) for e in edges if e.target == name],
    }


@router.get("/services/{name}/dependencies")
def service_dependencies(name: str, db: Session = Depends(get_db)):
    """Everything this service transitively depends on."""
    _require_service(db, name)
    reach = dependencies(name, [(e.source, e.target) for e in _edges(db)])
    return {
        "service": name,
        "dependencies": [{"service": s, "hops": h} for s, h in sorted(reach.items(), key=lambda kv: (kv[1], kv[0]))],
    }


@router.get("/services/{name}/dependents")
def service_dependents(name: str, db: Session = Depends(get_db)):
    """If `name` fails, these services are structurally at risk (declared dependencies, NOT measured impact)."""
    _require_service(db, name)
    reach = dependents(name, [(e.source, e.target) for e in _edges(db)])
    return {
        "service": name,
        "basis": "declared_structure",
        "at_risk": [{"service": s, "hops": h} for s, h in sorted(reach.items(), key=lambda kv: (kv[1], kv[0]))],
    }


@router.get("/topology")
def topology(db: Session = Depends(get_db)):
    nodes = db.query(ServiceRow).order_by(ServiceRow.name).all()
    edges = _edges(db)
    return {
        "nodes": [_service_dict(n) for n in nodes],
        "edges": [_edge_dict(e) for e in edges],
    }


@router.get("/topology/impact")
def topology_impact(db: Session = Depends(get_db)):
    """Which component has the largest STRUCTURAL blast radius (most transitive dependents)."""
    names = [n.name for n in db.query(ServiceRow).all()]
    ranking = impact_ranking(names, [(e.source, e.target) for e in _edges(db)])
    return {"basis": "declared_structure", "ranking": ranking}
