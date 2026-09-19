"""Derive OBSERVED dependency edges from dependency_call telemetry.

Computed on demand for a time window (never stored), so it is always exactly what
the telemetry says and Phase 6 can ask "which edges were active during experiment X".
A failed call still proves the dependency, so failures are included.
"""
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

_STATS_SQL = text("""
    select
        service                                        as source,
        metadata->>'target'                            as target,
        count(*)                                       as calls,
        count(*) filter (where error_type is not null) as errors,
        min(timestamp)                                 as first_seen,
        max(timestamp)                                 as last_seen,
        avg(latency_ms)                                as avg_latency_ms,
        percentile_cont(0.95) within group (order by latency_ms) as p95_latency_ms
    from telemetry_events
    where event_type = 'dependency_call'
      and metadata->>'target' is not null
      and timestamp >= :since
      and (cast(:until as timestamptz) is null or timestamp < :until)
    group by service, metadata->>'target'
    order by service, metadata->>'target'
""")

_ERRORS_SQL = text("""
    select service as source, metadata->>'target' as target, error_type, count(*) as n
    from telemetry_events
    where event_type = 'dependency_call'
      and metadata->>'target' is not null
      and error_type is not null
      and timestamp >= :since
      and (cast(:until as timestamptz) is null or timestamp < :until)
    group by service, metadata->>'target', error_type
""")


def _round(value, digits=2):
    return None if value is None else round(float(value), digits)


def observed_edges(db: Session, since: datetime, until: datetime | None = None) -> list[dict]:
    params = {"since": since, "until": until}

    error_types: dict[tuple[str, str], dict[str, int]] = {}
    for row in db.execute(_ERRORS_SQL, params):
        error_types.setdefault((row.source, row.target), {})[row.error_type] = row.n

    edges = []
    for row in db.execute(_STATS_SQL, params):
        edges.append({
            "source": row.source,
            "target": row.target,
            "relation": "calls",
            "origin": "observed",
            "calls": row.calls,
            "errors": row.errors,
            "error_rate": round(row.errors / row.calls, 4),
            "error_types": error_types.get((row.source, row.target), {}),
            "avg_latency_ms": _round(row.avg_latency_ms),
            "p95_latency_ms": _round(row.p95_latency_ms),
            "first_seen": row.first_seen.isoformat(),
            "last_seen": row.last_seen.isoformat(),
        })
    return edges
