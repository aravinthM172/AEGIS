import os
import time

import psutil
import redis
from fastapi import APIRouter

router = APIRouter()

START_TIME = time.time()

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    decode_responses=True,
)


@router.get("/monitor")
def monitor():
    uptime = time.time() - START_TIME

    try:
        redis_status = redis_client.ping()
    except Exception:
        redis_status = False

    return {
        "service": "aegis-api",
        "status": "healthy",
        "uptime_seconds": round(uptime, 2),
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory_percent": psutil.virtual_memory().percent,
        "redis": "connected" if redis_status else "disconnected",
    }


@router.get("/monitor/system")
def system_metrics():
    memory = psutil.virtual_memory()

    return {
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory_percent": memory.percent,
        "memory_total_mb": round(memory.total / 1024 / 1024, 2),
        "memory_available_mb": round(memory.available / 1024 / 1024, 2),
    }


@router.get("/monitor/redis")
def monitor_redis():
    try:
        redis_client.ping()

        return {
            "status": "healthy",
            "redis_host": REDIS_HOST,
            "redis_port": REDIS_PORT,
        }

    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
        }