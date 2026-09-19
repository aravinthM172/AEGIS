"""Database-facing part of blast-radius analysis: load telemetry, run the pure analysis, store the result."""
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import text

from app.analysis.blast_radius import analyze
from app.database import SessionLocal
from app.experiments.catalog import PROTECTED_TARGETS
from app.experiments.models import ExperimentEventRow, ExperimentRow
from app.models import ServiceDependencyRow

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
    return result


def default_hook(exp_id: str) -> None:
    analyze_and_store(SessionLocal, exp_id)
