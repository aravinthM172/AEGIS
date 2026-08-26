from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, HttpUrl


router = APIRouter(
    prefix="/targets",
    tags=["Targets"],
)


class TargetCreate(BaseModel):
    name: str
    url: HttpUrl
    interval_seconds: int = 30


targets = {}


@router.post("/")
def create_target(target: TargetCreate):
    target_id = len(targets) + 1

    targets[target_id] = {
        "id": target_id,
        "name": target.name,
        "url": str(target.url),
        "interval_seconds": target.interval_seconds,
        "enabled": True,
    }

    return targets[target_id]


@router.get("/")
def list_targets():
    return {
        "count": len(targets),
        "targets": list(targets.values()),
    }


@router.get("/{target_id}")
def get_target(target_id: int):
    target = targets.get(target_id)

    if target is None:
        raise HTTPException(
            status_code=404,
            detail="Target not found",
        )

    return target


@router.delete("/{target_id}")
def delete_target(target_id: int):
    target = targets.pop(target_id, None)

    if target is None:
        raise HTTPException(
            status_code=404,
            detail="Target not found",
        )

    return {
        "message": "Target deleted",
        "target": target,
    }


def get_active_targets():
    return [
        target
        for target in targets.values()
        if target["enabled"]
    ]