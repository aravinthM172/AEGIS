import requests

from app.config import (
    OLLAMA_URL,
    OLLAMA_MODEL
)


class OllamaService:

    def __init__(self):

        self.url = (
            f"{OLLAMA_URL}/api/generate"
        )

        self.model = OLLAMA_MODEL

    def generate(
        self,
        prompt: str
    ) -> str:

        response = requests.post(
            self.url,
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": False
            },
            timeout=300
        )

        response.raise_for_status()

        data = response.json()

        return data["response"]
