import time

import psutil
import requests


def check_url(url: str):
    """
    Check whether a URL is reachable and measure response time.
    """

    start_time = time.perf_counter()

    try:
        response = requests.get(
            url,
            timeout=10
        )

        end_time = time.perf_counter()

        response_time_ms = (
            end_time - start_time
        ) * 1000

        return {
            "is_up": response.ok,
            "status_code": response.status_code,
            "response_time_ms": round(response_time_ms, 2),
            "error": None
        }

    except requests.RequestException as e:

        end_time = time.perf_counter()

        response_time_ms = (
            end_time - start_time
        ) * 1000

        return {
            "is_up": False,
            "status_code": None,
            "response_time_ms": round(response_time_ms, 2),
            "error": str(e)
        }


def get_system_stats():
    """
    Collect basic system resource metrics.
    """

    return {
        "cpu_percent": psutil.cpu_percent(interval=1),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_percent": psutil.disk_usage("/").percent
    }