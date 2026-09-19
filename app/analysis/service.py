"""Database-facing part of blast-radius analysis: load telemetry, run the pure analysis, store the result."""
import logging
import os
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import text

from app.analysis.blast_radius import analyze
from app.analysis.models import FailureSignatureRow
from app.analysis.resilience import build_resilience
from app.analysis.signatures import build_signature
from app.database import SessionLocal
from app.experiments.catalog import PROTECTED_TARGETS
from app.experiments.models import ExperimentEventRow, ExperimentRow
from app.models import ServiceDependencyRow
from app.topology_graph import dependencies

logger = logging.getLogger("analysis")

ENTRY_SERVICE = os.getenv("WORKLOAD_ENTRY_SERVICE", "gateway")
CLIENT_SERVICE = "workload-client"  # synthetic user: measured client-side, independent of Kafka telemetry

_HTTP_SQL = text("""
    select service, timestamp, latency_ms, status_code
    from telemetry_events
    where event_type = 'http_request' and timestamp >= :lo and timestamp <= :hi
""")

_CALL_SQL = text("""
    select service, metadata->>'target' as target, timestamp, latency_ms, status_code, error_type
    from telemetry_events
    where event_type = 'dependency_call' and metadata->>'target' is not null
      and timestamp >= :lo and timestamp <= :hi
""")


def _epoch(value: datetime | None) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def analyze_experiment(session_factory, exp_id: str) -> dict:
    with session_factory() as db:
        exp = db.get(ExperimentRow, exp_id)
        if exp is None:
            raise KeyError(exp_id)
        timeline = {
            "baseline_started_at": _epoch(exp.baseline_started_at),
            "inject_started_at": _epoch(exp.inject_started_at),
            "fault_applied_at": _epoch(exp.fault_applied_at),
            "rollback_started_at": _epoch(exp.rollback_started_at),
            "inject_ended_at": _epoch(exp.inject_ended_at),
            "finished_at": _epoch(exp.finished_at),
        }
        experiment = {"target": exp.target, "fault_type": exp.fault_type, "dry_run": exp.dry_run,
                      "timeline": timeline}
        stored = exp.result or {}
        workload = dict(stored.get("workload") or {})
        samples = workload.pop("samples", None) or stored.get("workload_samples") or []
        workload = workload or None

        if timeline["baseline_started_at"] is None or timeline["finished_at"] is None:
            return analyze(experiment=experiment, http_events={}, call_events=[], declared_edges=[],
                           entry_service=ENTRY_SERVICE, workload=workload)

        lo = datetime.fromtimestamp(timeline["baseline_started_at"] - 1, timezone.utc)
        hi = datetime.fromtimestamp(timeline["finished_at"] + 1, timezone.utc)

        http_events = defaultdict(list)
        for row in db.execute(_HTTP_SQL, {"lo": lo, "hi": hi}):
            if row.service in PROTECTED_TARGETS:
                continue  # the measurement plane observes; it is not part of the measured workload
            http_events[row.service].append({"ts": _epoch(row.timestamp), "latency_ms": row.latency_ms,
                                             "status": row.status_code})
        call_events = [{"ts": _epoch(r.timestamp), "source": r.service, "target": r.target,
                        "latency_ms": r.latency_ms, "status": r.status_code, "error_type": r.error_type}
                       for r in db.execute(_CALL_SQL, {"lo": lo, "hi": hi})]
        declared = [(e.source, e.target) for e in db.query(ServiceDependencyRow).all()]

    if samples:
        http_events[CLIENT_SERVICE] = [{"ts": ts, "latency_ms": ms, "status": status} for ts, ms, status in samples]

    result = analyze(experiment=experiment, http_events=dict(http_events), call_events=call_events,
                     declared_edges=declared, entry_service=CLIENT_SERVICE if samples else ENTRY_SERVICE,
                     workload=workload, client_services=frozenset({CLIENT_SERVICE}))
    result["excluded_services"] = sorted(PROTECTED_TARGETS)
    if samples:
        result["workload_samples"] = samples  # kept so the analysis can be recomputed; hidden by the API
    return result


def analyze_and_store(session_factory, exp_id: str) -> dict:
    result = analyze_experiment(session_factory, exp_id)
    with session_factory() as db:
        db.query(ExperimentRow).filter_by(id=exp_id).update({"result": result})
        db.add(ExperimentEventRow(experiment_id=exp_id, timestamp=datetime.now(timezone.utc),
                                  event_type="analysis_completed",
                                  detail={"status": result.get("status"),
                                          "affected_services": result.get("affected_services", [])}))
        db.commit()
    store_signature(session_factory, exp_id, result)
    _update_knowledge(session_factory, exp_id, result)
    return result


def _update_knowledge(session_factory, exp_id: str, result: dict) -> None:
    """Best effort: an experiment's report becomes retrievable knowledge. Must never fail the analysis."""
    try:
        from app.ai.service import get_kb
        from app.experiments.engine import to_dict

        with session_factory() as db:
            exp = to_dict(db.get(ExperimentRow, exp_id))
        get_kb().upsert_experiment(exp, {k: v for k, v in result.items() if k != "workload_samples"})
    except Exception:
        logger.warning("could not add experiment %s to the knowledge base", exp_id, exc_info=True)


def store_signature(session_factory, exp_id: str, result: dict) -> str | None:
    """Derive and (re)store the failure signature of an experiment. Returns its id, or None if not analyzable."""
    with session_factory() as db:
        exp = db.get(ExperimentRow, exp_id)
        meta = {"target": exp.target, "fault_type": exp.fault_type, "parameters": exp.parameters,
                "duration_s": exp.duration_s, "workload_rps": exp.workload_rps, "workload_n": exp.workload_n,
                "dry_run": exp.dry_run}
        signature = build_signature(meta, result)
        existing = db.query(FailureSignatureRow).filter_by(experiment_id=exp_id).first()
        if signature is None:
            if existing:
                db.delete(existing)
                db.commit()
            return None
        row = existing or FailureSignatureRow(id=str(uuid.uuid4()), experiment_id=exp_id)
        row.kind, row.target, row.fault_type = signature["kind"], exp.target, exp.fault_type
        row.fingerprint, row.signature = signature["fingerprint"], signature
        db.add(row)
        db.commit()
        return row.id


def signature_dict(row: FailureSignatureRow) -> dict:
    return {"id": row.id, "experiment_id": row.experiment_id, "kind": row.kind, "target": row.target,
            "fault_type": row.fault_type, "fingerprint": row.fingerprint,
            "created_at": row.created_at.isoformat() if row.created_at else None, "signature": row.signature}


def rebuild_all(session_factory) -> dict:
    """Re-analyse every finished experiment that reached its fault window and rebuild its signature."""
    with session_factory() as db:
        ids = [r.id for r in db.query(ExperimentRow)
               .filter(ExperimentRow.status.in_(("COMPLETED", "ABORTED")))
               .filter(ExperimentRow.fault_applied_at.isnot(None)).all()]
    counts = {"experiments": len(ids), "signatures": 0, "skipped": 0, "failed": []}
    for exp_id in ids:
        try:
            result = analyze_and_store(session_factory, exp_id)
            counts["signatures" if result.get("status") == "analyzed" else "skipped"] += 1
        except Exception as exc:  # keep going: one bad experiment must not block the rest
            logger.exception("rebuild failed for %s", exp_id)
            counts["failed"].append({"experiment_id": exp_id, "error": f"{type(exc).__name__}: {exc}"})
    return counts


def resilience_report(session_factory) -> dict:
    with session_factory() as db:
        rows = db.query(FailureSignatureRow).order_by(FailureSignatureRow.created_at).all()
        signatures = [{"id": r.id, "signature": r.signature} for r in rows]
        edges = [(e.source, e.target) for e in db.query(ServiceDependencyRow).all()]
        services = {s for pair in edges for s in pair}
    dependency_map = {name: set(dependencies(name, edges)) for name in services}
    report = build_resilience(signatures, dependency_map, excluded=frozenset(PROTECTED_TARGETS))
    report["signatures_used"] = len(signatures)
    return report


def default_hook(exp_id: str) -> None:
    analyze_and_store(SessionLocal, exp_id)
