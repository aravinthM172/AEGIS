"""AI analysis service: assemble evidence, retrieve knowledge, run the validated LLM analysis, store the outcome."""
import logging
import re
import threading
import uuid
from datetime import datetime, timezone

from app.ai.analyzer import run_analysis
from app.ai.evidence import build_experiment_evidence, deterministic_summary, valid_refs
from app.ai.knowledge import KnowledgeBase
from app.ai.llm import OllamaClient, OllamaEmbeddings
from app.ai.models import AnalysisRow
from app.analysis.models import FailureSignatureRow
from app.analysis.service import resilience_report
from app.database import SessionLocal

logger = logging.getLogger("ai")

DEFAULT_QUESTION = ("Explain the measured behaviour of this experiment: what failed, how the failure propagated, "
                    "the most likely mechanism, and what should be done about it.")

_llm: OllamaClient | None = None
_kb: KnowledgeBase | None = None


def get_llm() -> OllamaClient:
    global _llm
    if _llm is None:
        _llm = OllamaClient()
    return _llm


def get_kb() -> KnowledgeBase:
    global _kb
    if _kb is None:
        _kb = KnowledgeBase(SessionLocal, embedder=OllamaEmbeddings())
    return _kb


class AnalysisError(Exception):
    pass


def _now():
    return datetime.now(timezone.utc)


def _clean_result(result: dict) -> dict:
    return {k: v for k, v in result.items() if k != "workload_samples"}


def _search_query(exp: dict, result: dict, question: str) -> str:
    parts = [exp["fault_type"].replace("_", " "), exp["target"], question]
    if any((e.get("fault", {}).get("error_pct") or 0) >= 10 for e in result.get("services", {}).values()):
        parts.append("errors 5xx failed requests")
    if any((e.get("p95_ratio") or 0) >= 2 for e in result.get("services", {}).values()):
        parts.append("latency slow p95")
    if exp["fault_type"] in ("stop_container", "pause_container", "restart_container"):
        parts.append("dependency outage hang timeout")
    return " ".join(parts)


def prepare_evidence(exp: dict, question: str, kb: KnowledgeBase, session_factory=SessionLocal) -> tuple[dict, str]:
    result = _clean_result(exp["result"])
    with session_factory() as db:
        rows = db.query(FailureSignatureRow).order_by(FailureSignatureRow.created_at.desc()).all()
        history = sorted(
            ({"id": r.id, "signature": r.signature} for r in rows if r.experiment_id != exp["id"]),
            key=lambda h: (h["signature"]["trigger"]["target"] != exp["target"],
                           h["signature"]["trigger"]["fault_type"] != exp["fault_type"]))
    docs = [d for d in kb.search(_search_query(exp, result, question), k=4)]
    profiles = resilience_report(session_factory)["services"]
    evidence = build_experiment_evidence(exp, result, history, profiles, docs)
    return evidence, kb.retrieval_mode()


def link_question_to_experiment(question: str, session_factory=SessionLocal) -> str | None:
    """Pick the most relevant finished fault experiment for a free-text question (entity linking)."""
    q = question.lower()
    aliases = {"postgres": ("postgres", "database", "db"), "kafka": ("kafka", "broker"), "redis": ("redis", "cache"),
               "cpp-service": ("cpp", "c++"), "java-service": ("java",), "gateway": ("gateway",)}
    best, best_score = None, 0
    with session_factory() as db:
        for row in db.query(FailureSignatureRow).filter_by(kind="fault").order_by(FailureSignatureRow.created_at):
            sig = row.signature
            score = 0
            for name, words in aliases.items():
                mentioned = any(re.search(rf"\b{re.escape(w)}\b", q) for w in words)
                if mentioned and name == sig["trigger"]["target"]:
                    score += 3
                elif mentioned and name in sig["observed"]["affected_services"]:
                    score += 1
            if sig["trigger"]["fault_type"].split("_")[0] in q:
                score += 1
            if score >= best_score and score > 0:  # later experiments win ties
                best, best_score = row.experiment_id, score
    return best


def start_analysis(experiment_id: str | None, question: str | None, engine, background: bool = True,
                   session_factory=SessionLocal) -> dict:
    if not experiment_id:
        if not question:
            raise AnalysisError("provide experiment_id or a question")
        experiment_id = link_question_to_experiment(question, session_factory)
        if experiment_id is None:
            raise AnalysisError("no finished fault experiment matches this question; run an experiment first "
                                "or pass experiment_id")
    exp = engine.get(experiment_id)
    if exp["status"] not in ("COMPLETED", "ABORTED", "FAILED"):
        raise AnalysisError(f"experiment is {exp['status']}; analyse it after it finishes")
    if not exp.get("result") or exp["result"].get("status") != "analyzed":
        raise AnalysisError("the experiment has no blast-radius analysis yet (still pending, or not analyzable)")

    analysis_id = str(uuid.uuid4())
    with session_factory() as db:
        db.add(AnalysisRow(id=analysis_id, experiment_id=experiment_id, question=question or DEFAULT_QUESTION,
                           status="running", deterministic_summary=deterministic_summary(exp, _clean_result(exp["result"]))))
        db.commit()
    if background:
        threading.Thread(target=run_job, args=(analysis_id, exp), name=f"ai-{analysis_id[:8]}", daemon=True).start()
    else:
        run_job(analysis_id, exp)
    return get_analysis(analysis_id, session_factory)


def run_job(analysis_id: str, exp: dict, llm=None, kb=None, session_factory=SessionLocal) -> None:
    llm, kb = llm or get_llm(), kb or get_kb()
    try:
        with session_factory() as db:
            question = db.get(AnalysisRow, analysis_id).question
        evidence, retrieval = prepare_evidence(exp, question, kb, session_factory)
        outcome = run_analysis(evidence, question, llm)
        update = {"status": outcome["status"], "diagnosis": outcome["diagnosis"], "errors": outcome["errors"],
                  "evidence": {**evidence, "valid_refs": sorted(valid_refs(evidence)),
                               **({"rejected_output": outcome["rejected_output"]} if "rejected_output" in outcome else {})},
                  "model": outcome.get("model"), "attempts": outcome["attempts"], "retrieval": retrieval}
    except Exception as exc:
        logger.exception("analysis %s failed", analysis_id)
        update = {"status": "failed", "errors": [f"{type(exc).__name__}: {exc}"]}
    with session_factory() as db:
        db.query(AnalysisRow).filter_by(id=analysis_id).update({**update, "finished_at": _now()})
        db.commit()


def get_analysis(analysis_id: str, session_factory=SessionLocal) -> dict | None:
    with session_factory() as db:
        row = db.get(AnalysisRow, analysis_id)
        return to_dict(row) if row else None


def list_analyses(limit: int = 20, session_factory=SessionLocal) -> list[dict]:
    with session_factory() as db:
        rows = db.query(AnalysisRow).order_by(AnalysisRow.created_at.desc()).limit(limit).all()
        return [to_dict(r, include_evidence=False) for r in rows]


def to_dict(row: AnalysisRow, include_evidence: bool = True) -> dict:
    data = {"id": row.id, "experiment_id": row.experiment_id, "question": row.question, "status": row.status,
            "basis": "llm_hypothesis" if row.status == "accepted" else None,
            "diagnosis": row.diagnosis, "errors": row.errors, "deterministic_summary": row.deterministic_summary,
            "model": row.model, "attempts": row.attempts, "retrieval": row.retrieval,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None}
    if include_evidence:
        data["evidence"] = row.evidence
    return data
