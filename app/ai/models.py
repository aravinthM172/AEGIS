from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.database import Base
from app.experiments.models import JSONType


class KnowledgeChunkRow(Base):
    """One retrievable chunk of a knowledge document (runbook, architecture, experiment report)."""
    __tablename__ = "knowledge_chunks"

    id = Column(String(200), primary_key=True)  # "<doc_id>#<n>"
    doc_id = Column(String(200), nullable=False, index=True)
    source = Column(String(20), nullable=False, index=True)  # architecture | runbook | experiment
    title = Column(String(300), nullable=False)
    text = Column(Text, nullable=False)
    content_hash = Column(String(40), nullable=False)  # of the whole document, to skip unchanged docs
    embedding = Column(JSONType)                        # list[float] or NULL (BM25-only until embedded)
    embedding_model = Column(String(100))
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AnalysisRow(Base):
    """An LLM analysis request: evidence in, validated diagnosis (or the reasons it was rejected) out."""
    __tablename__ = "analyses"

    id = Column(String(36), primary_key=True)
    experiment_id = Column(String(36), index=True)
    question = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, index=True)  # running | accepted | rejected | llm_unavailable | failed
    diagnosis = Column(JSONType)
    errors = Column(JSONType)
    deterministic_summary = Column(Text)
    evidence = Column(JSONType)
    model = Column(String(100))
    attempts = Column(Integer)
    retrieval = Column(String(20))  # hybrid | bm25 | none
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at = Column(DateTime(timezone=True))
