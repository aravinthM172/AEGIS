import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import ServiceDependencyRow, ServiceRow
from app.topology_graph import TopologyError
from app.topology_registry import seed_registry


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    ServiceRow.__table__.create(engine)
    ServiceDependencyRow.__table__.create(engine)
    with Session(engine) as session:
        yield session


def write(tmp_path, services, deps):
    path = tmp_path / "topology.json"
    path.write_text(json.dumps({"services": services, "dependencies": deps}))
    return str(path)


SERVICES = [
    {"name": "api", "kind": "service", "container": "c-api"},
    {"name": "db", "kind": "database", "container": "c-db"},
    {"name": "cache", "kind": "cache"},
]
DEPS = [
    {"source": "api", "target": "db", "relation": "reads_writes", "evidence": "config A"},
    {"source": "api", "target": "cache", "relation": "reads_writes", "evidence": "config B"},
]


def test_seed_is_idempotent(db, tmp_path):
    path = write(tmp_path, SERVICES, DEPS)
    seed_registry(db, path)
    seed_registry(db, path)
    assert db.query(ServiceRow).count() == 3
    assert db.query(ServiceDependencyRow).count() == 2


def test_seed_updates_and_prunes_declared_edges(db, tmp_path):
    seed_registry(db, write(tmp_path, SERVICES, DEPS))
    result = seed_registry(db, write(tmp_path, SERVICES, DEPS[:1]))
    assert result["removed_dependencies"] == 1
    assert [(d.source, d.target) for d in db.query(ServiceDependencyRow)] == [("api", "db")]


def test_seed_never_touches_non_declared_edges(db, tmp_path):
    seed_registry(db, write(tmp_path, SERVICES, DEPS))
    db.add(ServiceDependencyRow(source="db", target="cache", relation="calls", origin="observed", evidence="trace"))
    db.commit()
    seed_registry(db, write(tmp_path, SERVICES, []))  # file now declares no edges
    remaining = db.query(ServiceDependencyRow).all()
    assert [(d.source, d.target, d.origin) for d in remaining] == [("db", "cache", "observed")]


def test_invalid_file_changes_nothing(db, tmp_path):
    seed_registry(db, write(tmp_path, SERVICES, DEPS))
    cyclic = DEPS + [{"source": "db", "target": "api", "relation": "calls", "evidence": "x"}]
    with pytest.raises(TopologyError):
        seed_registry(db, write(tmp_path, SERVICES, cyclic))
    assert db.query(ServiceDependencyRow).count() == 2
