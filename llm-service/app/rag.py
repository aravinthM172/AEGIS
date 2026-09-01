import json

from app.retriever import Retriever
from app.ollama import OllamaService


class RAGPipeline:

    def __init__(self):

        self.retriever = Retriever()

        self.llm = OllamaService()


    def analyze_incident(
        self,
        incident: str
    ):

        retrieved = (
            self.retriever.retrieve(
                incident,
                top_k=3
            )
        )

        context_parts = []

        for item in retrieved:

            context_parts.append(
                f"""
SOURCE:
{item["metadata"].get("source")}

CONTENT:
{item["text"]}
"""
            )

        context = "\n---\n".join(
            context_parts
        )

        prompt = f"""
You are Aegis, an incident intelligence
assistant.

Analyze the production incident using ONLY:

1. The supplied incident.
2. The retrieved knowledge.

Do NOT invent evidence.

INCIDENT:
{incident}

RETRIEVED KNOWLEDGE:
{context}

Return ONLY valid JSON.

Do not use Markdown.

Use exactly this structure:

{{
    "summary": "short incident summary",

    "root_cause": "most likely root cause or state that it is unknown",

    "evidence": [
        "fact or evidence"
    ],

    "affected_services": [
        "service name"
    ],

    "investigation_steps": [
        "investigation step"
    ],

    "remediation": [
        "recommended remediation"
    ],

    "confidence": 0.0
}}

Rules:

- confidence must be a number between 0.0 and 1.0.
- Evidence must come only from the incident or retrieved knowledge.
- Do not claim that logs contain information unless the supplied information contains those logs.
- Clearly distinguish facts from hypotheses.
- If there is insufficient information, say so.
- Return JSON only.
"""

        raw_response = self.llm.generate(
            prompt
        )

        return self._parse_response(
            raw_response
        )


    def _parse_response(
        self,
        response: str
    ):

        response = response.strip()

        # Remove accidental Markdown code fences.
        if response.startswith("```json"):

            response = response[
                7:
            ]

        elif response.startswith("```"):

            response = response[
                3:
            ]

        if response.endswith("```"):

            response = response[
                :-3
            ]

        response = response.strip()

        try:

            data = json.loads(
                response
            )

        except json.JSONDecodeError:

            return {
                "summary": response,
                "root_cause": "Unable to parse structured root cause.",
                "evidence": [],
                "affected_services": [],
                "investigation_steps": [],
                "remediation": [],
                "confidence": 0.0
            }

        return {
            "summary": data.get(
                "summary",
                ""
            ),

            "root_cause": data.get(
                "root_cause",
                ""
            ),

            "evidence": data.get(
                "evidence",
                []
            ),

            "affected_services": data.get(
                "affected_services",
                []
            ),

            "investigation_steps": data.get(
                "investigation_steps",
                []
            ),

            "remediation": data.get(
                "remediation",
                []
            ),

            "confidence": float(
                data.get(
                    "confidence",
                    0.0
                )
            )
        }