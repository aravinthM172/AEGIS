from pydantic import BaseModel, Field


class IncidentRequest(BaseModel):

    incident: str = Field(
        ...,
        min_length=10,
        description="Production incident description"
    )


class IncidentAnalysis(BaseModel):

    summary: str

    root_cause: str

    evidence: list[str]

    affected_services: list[str]

    investigation_steps: list[str]

    remediation: list[str]

    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0
    )


class IncidentResponse(BaseModel):

    incident: str

    analysis: IncidentAnalysis