import json
import os

from fastapi import Depends, APIRouter, HTTPException
from app.remediation.auth import require_role
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.experiments.campaign import get_campaign_runner
from app.experiments.engine import ValidationFailed

router = APIRouter(prefix="/api/campaigns", tags=["Campaigns"])

SUITE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config", "campaigns")


class CampaignRequest(BaseModel):
    suite: str | None = Field(default=None, description="name of a file in config/campaigns (e.g. 'standard')")
    name: str | None = None
    repetitions: int | None = None
    cooldown_s: int | None = None
    experiments: list[dict] | None = None


def _load_suite(name: str) -> dict:
    if not name.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=422, detail="invalid suite name")
    path = os.path.join(SUITE_DIR, f"{name}.json")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"unknown suite '{name}'")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@router.post("", status_code=201, dependencies=[Depends(require_role("operator"))])
def start_campaign(body: CampaignRequest):
    plan = _load_suite(body.suite) if body.suite else {}
    if body.experiments is not None:
        plan["experiments"] = body.experiments
    if body.repetitions is not None:
        plan["repetitions"] = body.repetitions
    if body.name:
        plan["name"] = body.name
    cooldown = body.cooldown_s if body.cooldown_s is not None else plan.get("cooldown_s", 20)
    try:
        return get_campaign_runner().start(plan, name=plan.get("name"), cooldown_s=cooldown)
    except ValidationFailed as exc:
        return JSONResponse(status_code=422, content={"errors": exc.errors})


@router.get("")
def list_campaigns():
    campaigns = get_campaign_runner().list_campaigns()
    return {"count": len(campaigns), "campaigns": campaigns}


@router.get("/{campaign_id}")
def get_campaign(campaign_id: str):
    campaign = get_campaign_runner().get(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail=f"Unknown campaign '{campaign_id}'")
    return campaign


@router.post("/{campaign_id}/abort", status_code=202, dependencies=[Depends(require_role("operator"))])
def abort_campaign(campaign_id: str):
    campaign = get_campaign_runner().abort(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail=f"Unknown campaign '{campaign_id}'")
    return campaign
