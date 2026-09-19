"""Structured diagnosis schema and the grounding validator (pure).

The model's output is never used as-is. It must parse against the schema AND be grounded:
  * every cited reference exists in the evidence package
  * affected services are ones the measurements show as affected (or the target itself)
  * numbers in claims appear in the evidence (no invented statistics)
  * actions are from the allowlist, or NONE
Anything else is rejected with the reasons, so the caller can retry once or give up.
"""
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.ai.evidence import numeric_facts, valid_refs
from app.remediation.policy import action_for_fault

ALLOWED_ACTIONS = ("RESTART_SERVICE", "SCALE_SERVICE", "ROLLBACK_DEPLOYMENT", "CLEAR_CACHE", "ENABLE_FALLBACK")


class Finding(BaseModel):
    claim: str = Field(min_length=3)
    refs: list[str] = Field(min_length=1)


class Action(BaseModel):
    action: str = Field(min_length=3)
    kind: Literal["investigate", "mitigate", "prevent"]
    allowlisted_action: Literal["RESTART_SERVICE", "SCALE_SERVICE", "ROLLBACK_DEPLOYMENT", "CLEAR_CACHE",
                                "ENABLE_FALLBACK", "NONE"] = "NONE"
    refs: list[str] = Field(default_factory=list)


class Diagnosis(BaseModel):
    root_cause: str = Field(min_length=3)
    confidence: float = Field(ge=0.0, le=1.0)
    affected_services: list[str]
    evidence: list[Finding] = Field(min_length=1)
    recommended_actions: list[Action] = Field(default_factory=list)
    alternative_hypotheses: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


# Hand-written (no $ref/$defs) so Ollama's JSON-schema-to-grammar conversion handles it reliably.
_FINDING = {"type": "object", "required": ["claim", "refs"], "properties": {
    "claim": {"type": "string"}, "refs": {"type": "array", "items": {"type": "string"}}}}
_ACTION = {"type": "object", "required": ["action", "kind", "allowlisted_action"], "properties": {
    "action": {"type": "string"},
    "kind": {"type": "string", "enum": ["investigate", "mitigate", "prevent"]},
    "allowlisted_action": {"type": "string", "enum": list(ALLOWED_ACTIONS) + ["NONE"]},
    "refs": {"type": "array", "items": {"type": "string"}}}}
DIAGNOSIS_JSON_SCHEMA = {
    "type": "object",
    "required": ["root_cause", "confidence", "affected_services", "evidence", "recommended_actions",
                 "alternative_hypotheses", "limitations"],
    "properties": {
        "root_cause": {"type": "string"},
        "confidence": {"type": "number"},
        "affected_services": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": _FINDING},
        "recommended_actions": {"type": "array", "items": _ACTION},
        "alternative_hypotheses": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
}

_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _number_is_supported(value: float, facts: list[float]) -> bool:
    if value == int(value) and value <= 10:
        return True  # counts, steps and ordinals ("2 services", "step 3") are not statistics
    return any(abs(value - f) <= max(0.5, 0.02 * abs(f)) or round(f) == value for f in facts)


def validate_diagnosis(raw: dict, evidence: dict) -> tuple[Diagnosis | None, list[str]]:
    try:
        diagnosis = Diagnosis.model_validate(raw)
    except ValidationError as exc:
        return None, [f"schema: {'; '.join(f'{'.'.join(map(str, e['loc']))}: {e['msg']}' for e in exc.errors()[:6])}"]

    errors: list[str] = []
    refs = valid_refs(evidence)
    cited = [r for f in diagnosis.evidence for r in f.refs] + [r for a in diagnosis.recommended_actions for r in a.refs]
    for ref in sorted(set(cited) - refs):
        errors.append(f"unknown reference '{ref}' (valid references: {', '.join(sorted(refs))})")

    measured = evidence.get("measured", {})
    known = set(measured.get("affected_services", [])) | {evidence["experiment"]["target"]}
    for name in diagnosis.affected_services:
        if name not in known:
            errors.append(f"affected service '{name}' is not supported by the measurements "
                          f"(measured affected: {sorted(known)})")

    measured_affected = set(measured.get("affected_services", []))
    missing = sorted(measured_affected - set(diagnosis.affected_services))
    if missing:
        errors.append(f"affected_services omits services the measurements show as affected: {missing}")

    if not any(r.startswith("measured.") for f in diagnosis.evidence for r in f.refs):
        errors.append("no finding cites measured evidence (refs starting with 'measured.'); "
                      "documents alone cannot support a diagnosis of THIS experiment")

    applicable = action_for_fault(evidence["experiment"]["fault_type"])
    for action in diagnosis.recommended_actions:
        if action.allowlisted_action not in ("NONE", applicable):
            errors.append(f"action {action.allowlisted_action} does not address a "
                          f"'{evidence['experiment']['fault_type']}' fault (applicable: {applicable or 'none'}); "
                          "use NONE for advice that is not an allowlisted remediation")

    facts = numeric_facts(evidence)
    text = " ".join([diagnosis.root_cause] + [f.claim for f in diagnosis.evidence])
    for token in _NUMBER.findall(text):
        if not _number_is_supported(float(token), facts):
            errors.append(f"number {token} does not appear in the evidence")

    return (diagnosis if not errors else None), errors
