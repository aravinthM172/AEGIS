"""Sync the declared topology file into the services / service_dependencies tables."""
import logging
import os

from sqlalchemy.orm import Session

from app.models import ServiceDependencyRow, ServiceRow
from app.topology_graph import load_file

logger = logging.getLogger(__name__)

TOPOLOGY_FILE = os.getenv(
    "TOPOLOGY_FILE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "topology.json"),
)


def seed_registry(db: Session, path: str = TOPOLOGY_FILE) -> dict:
    """Make the DB match the file for origin='declared' rows (idempotent).
    Rows with another origin (e.g. observed, later) are never touched."""
    services, dependencies = load_file(path)  # validates; raises TopologyError
    counts = {"services": len(services), "dependencies": len(dependencies), "removed_dependencies": 0}

    existing = {row.name: row for row in db.query(ServiceRow).all()}
    for svc in services:
        row = existing.get(svc["name"]) or ServiceRow(name=svc["name"])
        row.kind = svc["kind"]
        row.technology = svc.get("technology")
        row.container = svc.get("container")
        row.description = svc.get("description")
        db.add(row)
    db.flush()

    wanted = {(d["source"], d["target"], d["relation"]): d for d in dependencies}
    current = {
        (r.source, r.target, r.relation): r
        for r in db.query(ServiceDependencyRow).filter_by(origin="declared").all()
    }

    for key, row in current.items():
        if key not in wanted:
            db.delete(row)
            counts["removed_dependencies"] += 1

    for key, dep in wanted.items():
        row = current.get(key) or ServiceDependencyRow(
            source=dep["source"], target=dep["target"], relation=dep["relation"], origin="declared"
        )
        row.evidence = dep["evidence"]
        db.add(row)

    db.commit()
    logger.info("topology registry synced: %s", counts)
    return counts
