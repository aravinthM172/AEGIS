"""LLM analysis orchestration (pure given an `llm` object with .chat() and .model).

accepted        the model's answer parsed AND passed every grounding check
rejected        it did not, after the allowed attempts: the reasons are returned, the answer is NOT presented as fact
llm_unavailable the model could not be reached: only the deterministic summary and evidence exist
"""
import json

from app.ai.evidence import valid_refs
from app.ai.llm import LLMUnavailable, parse_json_object
from app.ai.schema import ALLOWED_ACTIONS, DIAGNOSIS_JSON_SCHEMA, validate_diagnosis

SYSTEM_PROMPT = """You are a reliability analyst for a distributed system. You answer ONLY from the EVIDENCE provided.

Rules:
- EVIDENCE.summary is the authoritative measured result. Build your answer on it.
- Every claim must cite one or more references from VALID_REFS (for example measured.services.java-service, doc.<id>).
- At least one finding must cite a measured.* reference and quote a number from it (impact, p95 latency, error rate).
- Never invent numbers, services, components or measurements. Use the numbers exactly as they appear in the evidence.
- affected_services must include EVERY service listed in measured.affected_services (and may include the fault target).
- root_cause must explain the MECHANISM (why the failure propagated and why it looks the way it does), using the retrieved
  documents, not just repeat that a service is affected. Name where the failure originated.
- recommended_actions: allowlisted_action must be NONE unless the fault type is one an allowlisted action actually fixes
  (allowed: {actions}). Architectural advice (timeouts, circuit breakers, queues, fewer round trips) is kind "prevent" with NONE.
- If the evidence is insufficient, say so in limitations and lower confidence. Do not guess.
- Respond with a single JSON object and nothing else.""".format(actions=", ".join(ALLOWED_ACTIONS))


def build_messages(question: str, evidence: dict) -> list[dict]:
    refs = sorted(valid_refs(evidence))
    user = (f"QUESTION: {question}\n\nVALID_REFS: {json.dumps(refs)}\n\n"
            f"EVIDENCE: {json.dumps(evidence, separators=(',', ':'), default=str)}")
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def run_analysis(evidence: dict, question: str, llm, max_attempts: int = 2) -> dict:
    messages = build_messages(question, evidence)
    errors: list[str] = []
    last_raw = None

    for attempt in range(1, max_attempts + 1):
        try:
            content = llm.chat(messages, DIAGNOSIS_JSON_SCHEMA)
        except LLMUnavailable as exc:
            return {"status": "llm_unavailable", "diagnosis": None, "errors": [str(exc)], "attempts": attempt,
                    "model": getattr(llm, "model", None)}
        last_raw = content
        try:
            raw = parse_json_object(content)
        except ValueError as exc:  # includes json.JSONDecodeError
            errors = [f"output is not valid JSON: {exc}"]
        else:
            diagnosis, errors = validate_diagnosis(raw, evidence)
            if diagnosis is not None:
                return {"status": "accepted", "diagnosis": diagnosis.model_dump(), "errors": [], "attempts": attempt,
                        "model": getattr(llm, "model", None)}
        # feed the concrete problems back and ask for a corrected answer
        messages = messages + [
            {"role": "assistant", "content": content},
            {"role": "user", "content": "Your answer was rejected for these reasons:\n- " + "\n- ".join(errors)
             + "\nReturn a corrected JSON object. Use only VALID_REFS and numbers from the EVIDENCE."},
        ]

    return {"status": "rejected", "diagnosis": None, "errors": errors, "attempts": max_attempts,
            "model": getattr(llm, "model", None), "rejected_output": (last_raw or "")[:4000]}
