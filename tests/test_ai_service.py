import json
import time
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai import service
from app.ai.knowledge import KnowledgeBase, experiment_report, load_repo_documents
from app.ai.llm import LLMUnavailable
from app.ai.models import AnalysisRow, KnowledgeChunkRow
from app.analysis.blast_radius import analyze as analyze_raw
from app.analysis.models import FailureSignatureRow
from app.analysis.signatures import build_signature
from app.experiments.models import ExperimentEventRow, ExperimentRow
from app.models import ServiceDependencyRow, ServiceRow
from test_ai import CLIENT, ScriptedLLM, good_answer
from test_blast_radius import EDGES, TIMELINE, stream


@pytest.fixture()
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ai.db'}", connect_args={"check_same_thread": False, "timeout": 15})
    for model in (ServiceRow, ServiceDependencyRow, ExperimentRow, ExperimentEventRow, FailureSignatureRow,
                  KnowledgeChunkRow, AnalysisRow):
        model.__table__.create(engine)
    return sessionmaker(bind=engine)


@pytest.fixture()
def docs_dir(tmp_path):
    (tmp_path / "runbooks").mkdir()
    (tmp_path / "architecture.md").write_text("# Architecture\nThe gateway calls java-service which calls cpp-service.")
    (tmp_path / "runbooks" / "outage.md").write_text(
        "# Dependency outage\nA stopped dependency refuses connections; callers fail with 502 and 503.")
    (tmp_path / "runbooks" / "latency.md").write_text(
        "# Database latency\nRequest latency rises when the database is slow: p95 grows with round trips.")
    return str(tmp_path)


class FakeEmbedder:
    model = "fake-embed"

    def __init__(self, fail_query=False):
        self.fail_query = fail_query

    def available(self):
        return True

    def embed(self, texts, kind="document"):
        if kind == "query" and self.fail_query:
            raise LLMUnavailable("embedding backend down")
        # one-hot-ish: "database"/"latency" vs "outage"/"refuses"
        return [[float("database" in t.lower() or "latency" in t.lower()),
                 float("outage" in t.lower() or "refuses" in t.lower()), 0.1] for t in texts]


# ---- knowledge base -------------------------------------------------------------------------------------

def test_repo_documents_are_loaded_with_source_and_title(docs_dir):
    docs = {d["doc_id"]: d for d in load_repo_documents(docs_dir)}
    assert docs["kb:runbooks/outage"]["source"] == "runbook" and docs["kb:runbooks/outage"]["title"] == "Dependency outage"
    assert docs["kb:architecture"]["source"] == "architecture"


def test_reindex_is_incremental_and_search_finds_the_relevant_runbook(factory, docs_dir):
    kb = KnowledgeBase(factory, docs_dir=docs_dir)
    first = kb.reindex(include_experiments=False)
    assert first["changed"] == 3 and first["retrieval"] == "bm25"
    assert kb.reindex(include_experiments=False)["changed"] == 0  # unchanged documents are skipped
    top = kb.search("stopped dependency refuses connections 502")[0]
    assert top["doc_id"] == "kb:runbooks/outage" and top["ref"].startswith("doc.kb:runbooks/outage#")

    with open(f"{docs_dir}/runbooks/outage.md", "a") as fh:
        fh.write("\nRestart the service with RESTART_SERVICE.")
    assert kb.reindex(include_experiments=False)["changed"] == 1
    assert "RESTART_SERVICE" in kb.search("RESTART_SERVICE restart")[0]["text"]


def test_hybrid_retrieval_when_embeddings_exist_and_bm25_fallback_when_they_fail(factory, docs_dir):
    kb = KnowledgeBase(factory, embedder=FakeEmbedder(), docs_dir=docs_dir)
    info = kb.reindex(include_experiments=False)
    assert info["retrieval"] == "hybrid" and info["embedded_chunks"] == info["chunks"] and info["embedding_model"] == "fake-embed"
    assert kb.search("database is slow")[0]["doc_id"] == "kb:runbooks/latency"

    broken = KnowledgeBase(factory, embedder=FakeEmbedder(fail_query=True), docs_dir=docs_dir)
    broken.reindex(include_experiments=False)
    assert broken.search("database is slow")[0]["doc_id"] == "kb:runbooks/latency"  # BM25 still answers


def test_experiment_reports_become_retrievable_knowledge(factory, docs_dir):
    exp, result = seed_experiment(factory)
    kb = KnowledgeBase(factory, docs_dir=docs_dir)
    kb.reindex(include_experiments=False)
    assert kb.upsert_experiment(exp, result) is True
    assert kb.upsert_experiment(exp, result) is False  # unchanged
    top = kb.search("stop_container cpp java gateway impact")[0]
    assert top["source"] == "experiment" and "End users" in top["text"]
    assert experiment_report(exp, {"status": "not_analyzable"}) is None


# ---- analysis job ------------------------------------------------------------------------------------------

def seed_experiment(factory, target="cpp", fault="stop_container", finished=True):
    http = {"java": stream(0, 12) + stream(12, 27, latency=900, status=503) + stream(27, 45),
            "gateway": stream(0, 12) + stream(12, 27, latency=910, status=502) + stream(27, 45),
            CLIENT: stream(0, 12) + stream(12, 27, latency=920, status=502) + stream(27, 45)}
    meta = {"target": target, "fault_type": fault, "dry_run": False, "timeline": dict(TIMELINE)}
    result = analyze_raw(experiment=meta, http_events=http, call_events=[], declared_edges=EDGES,
                         entry_service=CLIENT, client_services=frozenset({CLIENT}))
    exp_id = str(uuid.uuid4())
    sig = build_signature({**meta, "parameters": {}, "duration_s": 15, "workload_rps": 4, "workload_n": 100000}, result)
    with factory() as db:
        db.add(ServiceRow(name=target, kind="service", container="c"))
        db.add(ExperimentRow(id=exp_id, target=target, fault_type=fault, parameters={}, duration_s=15, baseline_s=8,
                             recovery_s=15, dry_run=False, status="COMPLETED" if finished else "RUNNING",
                             abort_requested=False, result=result, workload_rps=4.0, workload_n=100000))
        db.flush()
        db.add(FailureSignatureRow(id=str(uuid.uuid4()), experiment_id=exp_id, kind="fault", target=target,
                                   fault_type=fault, fingerprint=sig["fingerprint"], signature=sig))
        db.commit()
    exp = {"id": exp_id, "target": target, "fault_type": fault, "parameters": {}, "dry_run": False,
           "workload_rps": 4.0, "workload_n": 100000, "hypothesis": None, "status": "COMPLETED", "result": result}
    return exp, result


class FakeEngine:
    def __init__(self, exp):
        self.exp = exp

    def get(self, exp_id):
        return self.exp


def make_kb(factory, docs_dir):
    kb = KnowledgeBase(factory, docs_dir=docs_dir)
    kb.reindex(include_experiments=False)
    return kb


def run(factory, docs_dir, llm, question="why did it fail?"):
    exp, _ = seed_experiment(factory)
    # run synchronously with injected fakes
    analysis_id = str(uuid.uuid4())
    with factory() as db:
        db.add(AnalysisRow(id=analysis_id, experiment_id=exp["id"], question=question, status="running"))
        db.commit()
    service.run_job(analysis_id, exp, llm=llm, kb=make_kb(factory, docs_dir), session_factory=factory)
    return service.get_analysis(analysis_id, factory)


def test_grounded_answer_is_stored_as_accepted_with_evidence_and_retrieval_mode(factory, docs_dir):
    a = run(factory, docs_dir, ScriptedLLM(good_answer(evidence=[
        {"claim": "java degraded.", "refs": ["measured.services.java"]}], recommended_actions=[])))
    assert a["status"] == "accepted" and a["basis"] == "llm_hypothesis" and a["model"] == "fake-model"
    assert a["retrieval"] == "bm25" and a["diagnosis"]["affected_services"] == ["java", "gateway"]
    assert "measured.services.java" in a["evidence"]["valid_refs"]
    assert any(r.startswith("doc.kb:") for r in a["evidence"]["valid_refs"])  # retrieved knowledge is citable


def test_hallucinated_answer_is_stored_as_rejected_with_reasons_and_the_raw_output(factory, docs_dir):
    bad = good_answer(affected_services=["redis"])
    a = run(factory, docs_dir, ScriptedLLM(bad, bad))
    assert a["status"] == "rejected" and a["diagnosis"] is None and a["basis"] is None
    assert any("redis" in e for e in a["errors"]) and "rejected_output" in a["evidence"]


def test_unreachable_llm_is_recorded_as_unavailable_not_as_an_answer(factory, docs_dir):
    a = run(factory, docs_dir, ScriptedLLM(LLMUnavailable("connection refused")))
    assert a["status"] == "llm_unavailable" and a["diagnosis"] is None and "refused" in a["errors"][0]


def test_unexpected_failure_is_recorded_as_failed(factory, docs_dir):
    class Exploding:
        model = "x"

        def chat(self, *a, **k):
            raise RuntimeError("boom")

    a = run(factory, docs_dir, Exploding())
    assert a["status"] == "failed" and "boom" in a["errors"][0]


def test_start_analysis_refuses_unfinished_or_unanalysed_experiments(factory):
    exp, _ = seed_experiment(factory, finished=False)
    with pytest.raises(service.AnalysisError, match="RUNNING"):
        service.start_analysis(exp["id"], "q", FakeEngine({**exp, "status": "RUNNING"}), False, factory)
    with pytest.raises(service.AnalysisError, match="no blast-radius analysis"):
        service.start_analysis(exp["id"], "q", FakeEngine({**exp, "result": None}), False, factory)
    with pytest.raises(service.AnalysisError, match="experiment_id or a question"):
        service.start_analysis(None, None, FakeEngine(exp), False, factory)


def test_free_text_question_is_linked_to_the_matching_experiment(factory):
    exp, _ = seed_experiment(factory, target="postgres", fault="latency")
    other, _ = seed_experiment(factory, target="cpp", fault="stop_container")
    assert service.link_question_to_experiment("Why is the database slow for the gateway?", factory) == exp["id"]
    assert service.link_question_to_experiment("What happens when cpp is stopped?", factory) == other["id"]
    assert service.link_question_to_experiment("tell me a joke", factory) is None


def test_search_query_handles_services_without_a_role_and_reflects_the_failure_character():
    # unaffected services have role None in real analyses; this once crashed the query builder
    result = {"services": {"cpp": {"role": None, "fault": {"error_pct": 0.0}, "p95_ratio": 1.0},
                           "java": {"role": "direct", "fault": {"error_pct": 60.0}, "p95_ratio": 40.0}}}
    query = service._search_query({"fault_type": "stop_container", "target": "cpp"}, result, "why?")
    assert "stop container" in query and "errors" in query and "latency" in query and "outage" in query
    quiet = service._search_query({"fault_type": "latency", "target": "postgres"},
                                  {"services": {"java": {"role": None, "fault": {"error_pct": 0.0}, "p95_ratio": 1.0}}}, "q")
    assert "errors" not in quiet
