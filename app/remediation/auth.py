"""API-key authentication and role-based authorisation for state-changing endpoints.

FAULTSCOPE_API_KEYS="key1:operator,key2:admin"  (roles: viewer < operator < admin)

Secure by default: if no keys are configured, protected endpoints are disabled (503) instead of
being left open. Keys are compared in constant time.
"""
import hmac
import os

from fastapi import Header, HTTPException

ROLES = {"viewer": 1, "operator": 2, "admin": 3}


def load_keys(raw: str | None = None) -> dict[str, str]:
    raw = os.getenv("FAULTSCOPE_API_KEYS", "") if raw is None else raw
    keys = {}
    for pair in filter(None, (p.strip() for p in raw.split(","))):
        key, _, role = pair.partition(":")
        if key and role.strip() in ROLES:
            keys[key] = role.strip()
    return keys


def authenticate(supplied: str, keys: dict[str, str]) -> tuple[str, str] | None:
    """Return (actor, role) for a valid key, else None. The actor is a non-secret label of the key."""
    for key, role in keys.items():
        if hmac.compare_digest(supplied.encode(), key.encode()):
            return f"{role}:{key[:4]}…", role
    return None


def require_role(minimum: str):
    """FastAPI dependency: returns the caller's actor label, or raises 401/403/503."""
    def dependency(x_api_key: str = Header(default="")) -> str:
        keys = load_keys()
        if not keys:
            raise HTTPException(status_code=503, detail="no API keys configured (set FAULTSCOPE_API_KEYS); "
                                                        "state-changing endpoints are disabled")
        found = authenticate(x_api_key, keys)
        if found is None:
            raise HTTPException(status_code=401, detail="missing or invalid X-API-Key")
        actor, role = found
        if ROLES[role] < ROLES[minimum]:
            raise HTTPException(status_code=403, detail=f"role '{role}' may not do this (requires {minimum})")
        return actor
    return dependency
