"""LLM and embedding clients (Ollama). Failures surface as LLMUnavailable, never as fake output."""
import json
import os

import httpx

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
LLM_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")


class LLMUnavailable(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, base_url: str = OLLAMA_URL, model: str = LLM_MODEL, timeout_s: float = 240.0,
                 client: httpx.Client | None = None):
        self.model = model
        self._client = client or httpx.Client(base_url=base_url, timeout=httpx.Timeout(timeout_s, connect=3.0))

    def available(self) -> bool:
        try:
            tags = self._client.get("/api/tags", timeout=3.0).json()
            return any(m.get("name", "").split(":")[0] == self.model.split(":")[0] for m in tags.get("models", []))
        except Exception:
            return False

    def chat(self, messages: list[dict], schema: dict | None = None) -> str:
        """Return the model's message content. `schema` constrains the output to that JSON schema."""
        body = {"model": self.model, "messages": messages, "stream": False,
                "options": {"temperature": 0.1, "num_ctx": 8192, "num_predict": 1100}}
        if schema is not None:
            body["format"] = schema
        try:
            response = self._client.post("/api/chat", json=body)
            response.raise_for_status()
            return response.json()["message"]["content"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise LLMUnavailable(f"{type(exc).__name__}: {exc}")


class OllamaEmbeddings:
    """nomic-embed-text style embeddings; `available()` is False if the model is not pulled."""

    def __init__(self, base_url: str = OLLAMA_URL, model: str = EMBED_MODEL, client: httpx.Client | None = None):
        self.model = model
        self._client = client or httpx.Client(base_url=base_url, timeout=httpx.Timeout(120.0, connect=3.0))

    def available(self) -> bool:
        try:
            tags = self._client.get("/api/tags", timeout=3.0).json()
            return any(m.get("name", "").split(":")[0] == self.model.split(":")[0] for m in tags.get("models", []))
        except Exception:
            return False

    def embed(self, texts: list[str], kind: str = "document") -> list[list[float]]:
        prefix = "search_query: " if kind == "query" else "search_document: "
        try:
            response = self._client.post("/api/embed", json={"model": self.model, "input": [prefix + t for t in texts]})
            response.raise_for_status()
            vectors = response.json()["embeddings"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise LLMUnavailable(f"embedding failed: {type(exc).__name__}: {exc}")
        if len(vectors) != len(texts):
            raise LLMUnavailable("embedding count mismatch")
        return vectors


def parse_json_object(content: str) -> dict:
    """Tolerate code fences around the JSON; anything that is not a JSON object is an error."""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):] if "{" in text else text
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("model output is not a JSON object")
    return value
