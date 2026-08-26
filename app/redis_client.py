import os

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

_client = None


def get_redis():
    global _client

    if _client is None:
        _client = redis.from_url(
            REDIS_URL,
            decode_responses=True,
        )

    return _client


def ping() -> bool:
    try:
        return bool(get_redis().ping())
    except Exception:
        return False
