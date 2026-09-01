from fastapi import FastAPI
from pydantic import BaseModel

from app.rag import RAGPipeline


app = FastAPI(
    title="Aegis Local LLM + RAG",
    description="Free local incident intelligence service",
    version="1.2.0"
)


rag = RAGPipeline()


class IncidentRequest(BaseModel):
    incident: str


@app.get("/")
def root():
    return {
        "service": "Aegis Local LLM + RAG",
        "status": "running"
    }


@app.get("/health")
def health():
    return {
        "status": "healthy"
    }


@app.post("/api/v1/analyze")
def analyze_incident(request: IncidentRequest):

    analysis = rag.analyze_incident(request.incident)

    return {
        "incident": request.incident,
        "analysis": analysis
    }
