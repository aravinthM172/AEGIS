import json

import pytest

from app.ai.analyzer import build_messages, run_analysis
from app.ai.evidence import build_experiment_evidence, deterministic_summary, numeric_facts, valid_refs
from app.ai.llm import LLMUnavailable, parse_json_object
from app.ai.retrieval import BM25Index, chunk_text, cosine, dense_search, reciprocal_rank_fusion, tokenize
from app.ai.schema import DIAGNOSIS_JSON_SCHEMA, validate_diagnosis
from app.analysis.blast_radius import analyze as analyze_raw
from test_blast_radius import EDGES, TIMELINE, healthy, stream

CLIENT = "workload-client"


# ---- retrieval -------------------------------------------------------------------------------------

DOCS = [
    ("kb:db", "runbook", "Database latency", "Symptoms: request latency rises when the database is slow. p95 latency, round trips."),
    ("kb:out", "runbook", "Dependency outage", "A stopped dependency refuses connections; callers fail with 502 and 503."),
    ("kb:cpu", "runbook", "CPU starvation", "CPU throttling makes single-threaded servers slow; latency grows with request cost."),
]


def chunks():
    return [c for d in DOCS for c in chunk_text(*d)]


def test_tokenizer_keeps_hyphenated_names_whole_and_split_and_drops_stopwords():
    tokens = tokenize("The cpp-service is down")
    assert "cpp-service" in tokens and "cpp" in tokens and "service" in tokens
    assert "the" not in tokens and "is" not in tokens


def test_chunking_splits_on_headings_and_respects_size():
    text = "# A\n" + "para one. " * 30 + "\n\n" + "para two. " * 30 + "\n# B\nsecond section"
    parts = chunk_text("d", "runbook", "T", text, max_chars=400)
    assert len(parts) >= 3 and all(len(p.text) <= 700 for p in parts)
    assert parts[0].id == "d#0" and any("second section" in p.text for p in parts)


def test_bm25_ranks_the_relevant_document_first():
    index = BM25Index(chunks())
    assert index.search("database latency p95 round trips")[0][0].doc_id == "kb:db"
    assert index.search("stopped dependency connection refused 502")[0][0].doc_id == "kb:out"
    assert index.search("cpu throttling single-threaded")[0][0].doc_id == "kb:cpu"
    assert index.search("zzzz nonsense") == []


def test_dense_ranking_and_rank_fusion():
    assert cosine([1, 0], [1, 0]) == 1.0 and cosine([1, 0], [0, 1]) == 0.0 and cosine([0, 0], [1, 1]) == 0.0
    cs = {c.id: c for c in chunks()}
    vectors = {"kb:db#0": [1.0, 0.0], "kb:out#0": [0.0, 1.0], "kb:cpu#0": [0.7, 0.7]}
    top = dense_search([1.0, 0.1], vectors, cs, k=2)
    assert [c.id for c, _ in top] == ["kb:db#0", "kb:cpu#0"]
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["c", "a", "d"]])
    assert fused[0] == "a" and set(fused) == {"a", "b", "c", "d"}  # agreement across rankings wins


# ---- evidence + validation --------------------------------------------------------------------------

def make_evidence():
    http = {"java": stream(0, 12) + stream(12, 27, latency=900, status=503) + stream(27, 45),
            "gateway": stream(0, 12) + stream(12, 27, latency=910, status=502) + stream(27, 45),
            CLIENT: stream(0, 12) + stream(12, 27, latency=920, status=502) + stream(27, 45)}
    exp = {"target": "cpp", "fault_type": "stop_container", "dry_run": False, "timeline": dict(TIMELINE)}
    result = analyze_raw(experiment=exp, http_events=http, call_events=[], declared_edges=EDGES,
                         entry_service=CLIENT, client_services=frozenset({CLIENT}))
    experiment = {"id": "abcdef12-0000", "target": "cpp", "fault_type": "stop_container", "parameters": {},
                  "dry_run": False, "workload_rps": 4, "workload_n": 100000}
    docs = [{"ref": "doc.kb:out#0", "title": "Dependency outage", "source": "runbook",
             "text": "A stopped dependency refuses connections. The JVM caches failed lookups for 10 seconds."}]
    evidence = build_experiment_evidence(experiment, result, [], {}, docs)
    return experiment, result, evidence


def good_answer(**over):
    base = {"root_cause": "cpp-service was stopped, so java-service connection attempts failed and the gateway returned 502.",
            "confidence": 0.8, "affected_services": ["java", "gateway"],
            "evidence": [{"claim": "java-service degraded at 100.0% impact.", "refs": ["measured.services.java"]},
                         {"claim": "Callers fail with refused connections.", "refs": ["doc.kb:out#0"]}],
            "recommended_actions": [{"action": "Restart cpp-service", "kind": "mitigate",
                                     "allowlisted_action": "RESTART_SERVICE", "refs": ["measured.recovery"]}],
            "alternative_hypotheses": [], "limitations": ["single run"]}
    base.update(over)
    return base


def test_evidence_exposes_only_measured_facts_and_citable_refs():
    _, _, ev = make_evidence()
    refs = valid_refs(ev)
    assert {"measured.services.java", "measured.services.gateway", "measured.end_user", "measured.recovery",
            "measured.propagation.0", "doc.kb:out#0"} <= refs
    assert ev["measured"]["affected_services"] == ["java", "gateway"]
    assert 100.0 in numeric_facts(ev)


def test_a_grounded_answer_is_accepted():
    _, _, ev = make_evidence()
    diagnosis, errors = validate_diagnosis(good_answer(), ev)
    assert errors == [] and diagnosis.confidence == 0.8


@pytest.mark.parametrize("mutation,expected", [
    ({"evidence": [{"claim": "made up", "refs": ["measured.services.postgres"]}]}, "unknown reference"),
    ({"affected_services": ["java", "redis"]}, "not supported by the measurements"),
    ({"evidence": [{"claim": "java-service p95 rose by 777.7 percent.", "refs": ["measured.services.java"]}]}, "does not appear in the evidence"),
    ({"confidence": 1.7}, "schema"),
    ({"evidence": []}, "schema"),
    ({"recommended_actions": [{"action": "wipe disk", "kind": "mitigate", "allowlisted_action": "FORMAT_DISK"}]}, "schema"),
    ({"affected_services": ["gateway"]}, "omits services the measurements show as affected"),
    ({"evidence": [{"claim": "Callers fail with refused connections.", "refs": ["doc.kb:out#0"]}]}, "no finding cites measured evidence"),
    ({"recommended_actions": [{"action": "Scale it", "kind": "mitigate", "allowlisted_action": "SCALE_SERVICE"}]}, "does not address"),
])
def test_ungrounded_or_malformed_answers_are_rejected_with_reasons(mutation, expected):
    _, _, ev = make_evidence()
    diagnosis, errors = validate_diagnosis(good_answer(**mutation), ev)
    assert diagnosis is None and any(expected in e for e in errors), errors


def test_numbers_that_are_in_the_evidence_or_are_small_counts_pass():
    _, _, ev = make_evidence()
    ok = good_answer(evidence=[{"claim": "2 services were affected, java at 100.0% and 900 ms latency.",
                                "refs": ["measured.services.java"]}])
    assert validate_diagnosis(ok, ev)[1] == []


def test_deterministic_summary_needs_no_model():
    exp, result, _ = make_evidence()
    text = deterministic_summary(exp, result)
    assert "stop_container on cpp" in text and "End users:" in text and "cpp -> java -> gateway" in text
    assert "could not be analysed" in deterministic_summary(exp, {"status": "not_analyzable", "reason": "x"})


# ---- orchestration -----------------------------------------------------------------------------------

class ScriptedLLM:
    model = "fake-model"

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    def chat(self, messages, schema=None):
        self.calls.append((messages, schema))
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out if isinstance(out, str) else json.dumps(out)


def test_accepts_a_grounded_answer_on_the_first_attempt():
    _, _, ev = make_evidence()
    llm = ScriptedLLM(good_answer())
    r = run_analysis(ev, "why?", llm)
    assert r["status"] == "accepted" and r["attempts"] == 1 and r["model"] == "fake-model"
    assert llm.calls[0][1] == DIAGNOSIS_JSON_SCHEMA  # output is constrained by the schema


def test_a_hallucinated_answer_is_retried_once_with_the_reasons_and_then_accepted():
    _, _, ev = make_evidence()
    bad = good_answer(affected_services=["java", "redis"])
    llm = ScriptedLLM(bad, good_answer())
    r = run_analysis(ev, "why?", llm)
    assert r["status"] == "accepted" and r["attempts"] == 2
    retry_prompt = llm.calls[1][0][-1]["content"]
    assert "rejected for these reasons" in retry_prompt and "redis" in retry_prompt


def test_two_bad_answers_are_rejected_not_presented_as_fact():
    _, _, ev = make_evidence()
    llm = ScriptedLLM(good_answer(affected_services=["redis"]), "not json at all")
    r = run_analysis(ev, "why?", llm)
    assert r["status"] == "rejected" and r["diagnosis"] is None and r["attempts"] == 2
    assert r["errors"] and "rejected_output" in r


def test_unreachable_model_degrades_to_llm_unavailable():
    _, _, ev = make_evidence()
    r = run_analysis(ev, "why?", ScriptedLLM(LLMUnavailable("connection refused")))
    assert r["status"] == "llm_unavailable" and r["diagnosis"] is None and "refused" in r["errors"][0]


def test_prompt_contains_rules_valid_refs_and_evidence_only():
    _, _, ev = make_evidence()
    system, user = build_messages("why did it fail?", ev)
    assert "ONLY from the EVIDENCE" in system["content"] and "RESTART_SERVICE" in system["content"]
    assert "measured.services.java" in user["content"] and "why did it fail?" in user["content"]


def test_parse_json_object_tolerates_fences_but_rejects_non_objects():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError):
        parse_json_object("[1, 2]")
    with pytest.raises(ValueError):
        parse_json_object("plain text")


def test_advice_that_is_not_an_allowlisted_remediation_must_use_none():
    _, _, ev = make_evidence()
    advice = good_answer(recommended_actions=[{"action": "Add a circuit breaker to java-service callers",
                                              "kind": "prevent", "allowlisted_action": "NONE", "refs": []}])
    assert validate_diagnosis(advice, ev)[1] == []


def test_evidence_carries_the_deterministic_summary_the_prompt_tells_the_model_to_use():
    _, _, ev = make_evidence()
    assert "End users:" in ev["summary"] and "cpp -> java -> gateway" in ev["summary"]
    system, user = build_messages("why?", ev)
    assert "EVIDENCE.summary is the authoritative" in system["content"] and "End users:" in user["content"]
