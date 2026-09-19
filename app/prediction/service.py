"""Prediction service: live early warning from current telemetry, and the leave-one-out backtest."""
import time
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import text

from app.analysis.models import FailureSignatureRow
from app.database import SessionLocal
from app.experiments.catalog import PROTECTED_TARGETS
from app.experiments.models import ExperimentRow
from app.prediction.backtest import WINDOW_S, replay_experiment, summarize
from app.prediction.core import build_state, features, predict

REFERENCE_S = 300  # live baseline: the 5 minutes before the current window

_HTTP_SQL = text("""
    select service, timestamp, latency_ms, status_code
    from telemetry_events
    where event_type = 'http_request' and timestamp >= :lo and timestamp <= :hi
""")


def _epoch(value: datetime) -> float:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp()


def _load_events(db, lo: float, hi: float) -> dict[str, list[dict]]:
    events: dict[str, list[dict]] = defaultdict(list)
    for row in db.execute(_HTTP_SQL, {"lo": datetime.fromtimestamp(lo, timezone.utc),
                                      "hi": datetime.fromtimestamp(hi, timezone.utc)}):
        if row.service in PROTECTED_TARGETS:
            continue  # the measurement plane is not part of the monitored workload
        events[row.service].append({"ts": _epoch(row.timestamp), "latency_ms": row.latency_ms,
                                    "status": row.status_code})
    return events


def _fault_signatures(db, exclude_experiment: str | None = None) -> list[dict]:
    return [{"id": r.id, "signature": r.signature}
            for r in db.query(FailureSignatureRow).filter_by(kind="fault").all()
            if r.experiment_id != exclude_experiment]


def live_prediction(session_factory=SessionLocal, window_s: float = WINDOW_S, now: float | None = None) -> dict:
    now = now if now is not None else time.time()
    with session_factory() as db:
        events = _load_events(db, now - REFERENCE_S - window_s, now)
        signatures = _fault_signatures(db)
    state = build_state(
        {name: features(ev, now - window_s, now) for name, ev in events.items()},
        {name: features(ev, now - REFERENCE_S - window_s, now - window_s) for name, ev in events.items()})
    judged = [n for n, s in state.items() if s["status"] != "insufficient_data"]
    return {
        "as_of": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "window_s": window_s,
        "services": state,
        "anomalous_services": sorted(n for n, s in state.items() if s["status"] == "anomalous"),
        "warnings": predict(state, signatures) if judged else [],
        "signatures_available": len(signatures),
        "status": "ok" if judged else "insufficient_data",
        "note": None if judged else "no service has enough recent traffic to be judged; prediction needs live requests",
    }


def run_backtest(session_factory=SessionLocal) -> dict:
    rows = []
    with session_factory() as db:
        experiments = (db.query(ExperimentRow)
                       .filter(ExperimentRow.status.in_(("COMPLETED", "ABORTED")))
                       .filter(ExperimentRow.fault_applied_at.isnot(None)).all())
        for exp in experiments:
            if (exp.result or {}).get("status") != "analyzed":
                continue
            timeline = {"baseline_started_at": _epoch(exp.baseline_started_at), "inject_started_at": _epoch(exp.inject_started_at),
                        "fault_applied_at": _epoch(exp.fault_applied_at), "rollback_started_at": _epoch(exp.rollback_started_at),
                        "finished_at": _epoch(exp.finished_at)}
            events = _load_events(db, timeline["baseline_started_at"] - 1, timeline["finished_at"] + 1)
            if not events:
                continue
            record = {"id": exp.id, "target": exp.target, "fault_type": exp.fault_type, "dry_run": exp.dry_run,
                      "timeline": timeline}
            rows.append(replay_experiment(record, events, _fault_signatures(db, exclude_experiment=exp.id)))
    return {"summary": summarize(rows), "experiments": rows}
