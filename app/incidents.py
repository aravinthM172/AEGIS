from sqlalchemy.orm import Session

from app.models import MonitorResult


def get_current_status(
    db: Session,
    url: str
):
    result = (
        db.query(MonitorResult)
        .filter(MonitorResult.url == url)
        .order_by(MonitorResult.created_at.desc())
        .first()
    )

    if not result:
        return {
            "url": url,
            "status": "UNKNOWN",
            "incident": False
        }

    if result.is_up:
        return {
            "url": url,
            "status": "UP",
            "incident": False,
            "response_time_ms": result.response_time_ms
        }

    return {
        "url": url,
        "status": "DOWN",
        "incident": True,
        "error": result.error
    }