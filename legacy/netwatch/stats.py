from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import MonitorResult


def get_url_stats(
    db: Session,
    url: str
):

    total = (
        db.query(MonitorResult)
        .filter(MonitorResult.url == url)
        .count()
    )

    if total == 0:
        return {
            "url": url,
            "total_checks": 0,
            "uptime_percent": 0,
            "average_response_time_ms": 0
        }

    successful = (
        db.query(MonitorResult)
        .filter(
            MonitorResult.url == url,
            MonitorResult.is_up == True
        )
        .count()
    )

    average_response = (
        db.query(
            func.avg(
                MonitorResult.response_time_ms
            )
        )
        .filter(MonitorResult.url == url)
        .scalar()
    )

    return {
        "url": url,
        "total_checks": total,
        "successful_checks": successful,
        "failed_checks": total - successful,
        "uptime_percent": round(
            (successful / total) * 100,
            2
        ),
        "average_response_time_ms": round(
            average_response or 0,
            2
        )
    }