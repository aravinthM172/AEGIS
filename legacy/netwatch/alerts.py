from datetime import datetime


def generate_alert(
    url: str,
    is_up: bool,
    error: str | None = None
):

    if is_up:
        return {
            "alert": False,
            "url": url,
            "message": "Service is healthy"
        }

    return {
        "alert": True,
        "severity": "CRITICAL",
        "url": url,
        "message": "Service is unavailable",
        "error": error,
        "timestamp": datetime.utcnow()
    }