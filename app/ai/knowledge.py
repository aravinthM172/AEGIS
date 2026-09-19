"""Knowledge base: repo documents + auto-generated experiment reports, indexed for retrieval."""
import hashlib
import logging
import os
import threading
from datetime import datetime, timezone

from app.ai.evidence import deterministic_summary
from app.ai.models import KnowledgeChunkRow
from app.ai.retrieval import BM25Index, Chunk, chunk_text, dense_search, reciprocal_rank_fusion
from app.experiments.models import ExperimentRow

logger = logging.getLogger("knowledge")

KNOWLEDGE_DIR = os.getenv("KNOWLEDGE_DIR", os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "knowledge"))


def load_repo_documents(directory: str) -> list[dict]:
    """Markdown files under `directory` -> [{doc_id, source, title, text}]."""
    docs = []
    for root, _, files in os.walk(directory):
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, directory).replace(os.sep, "/")
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            first = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), rel)
            source = "runbook" if rel.startswith("runbooks/") else "architecture"
            docs.append({"doc_id": "kb:" + rel[:-3], "source": source, "title": first, "text": text})
    return docs


def experiment_report(exp: dict, result: dict) -> dict | None:
    """A measured, deterministic report of one finished experiment: institutional memory for retrieval."""
    if result.get("status") != "analyzed":
        return None
    lines = [f"# Experiment {exp['id'][:8]}: {exp['fault_type']} on {exp['target']}",
             deterministic_summary(exp, result)]
    for name, s in result["services"].items():
        if s["status"] == "insufficient_data":
            continue
        lines.append(f"- {name} ({s.get('role') or 'service'}): {s['status']}, impact {s['impact_pct']}%, "
                     f"p95 {s['baseline'].get('p95_ms')} ms -> {s['fault'].get('p95_ms')} ms, "
                     f"errors {s['fault'].get('error_pct')}%.")
    if exp.get("hypothesis"):
        lines.append(f"Hypothesis stated before the run: {exp['hypothesis']}")
    lines.extend(f"Warning: {w}" for w in result["quality"]["warnings"])
    return {"doc_id": f"experiment:{exp['id']}", "source": "experiment",
            "title": f"Experiment {exp['id'][:8]} {exp['fault_type']} on {exp['target']}", "text": "\n".join(lines)}


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class KnowledgeBase:
    def __init__(self, session_factory, embedder=None, docs_dir: str = KNOWLEDGE_DIR):
        self.session_factory = session_factory
        self.embedder = embedder
        self.docs_dir = docs_dir
        self._lock = threading.Lock()
        self._index: BM25Index | None = None
        self._chunks: dict[str, Chunk] = {}
        self._vectors: dict[str, list[float]] = {}

    # ---- indexing -------------------------------------------------------------------------

    def reindex(self, include_experiments: bool = True) -> dict:
        docs = load_repo_documents(self.docs_dir) if os.path.isdir(self.docs_dir) else []
        if include_experiments:
            docs.extend(self._experiment_documents())
        changed = self._upsert(docs)
        embedded = self._embed_missing()
        self._rebuild()
        return {"documents": len(docs), "changed": changed, "chunks": len(self._chunks),
                "embedded_chunks": len(self._vectors), "retrieval": self.retrieval_mode(),
                "embedding_model": getattr(self.embedder, "model", None) if self._vectors else None,
                "newly_embedded": embedded}

    def upsert_experiment(self, exp: dict, result: dict) -> bool:
        """Add or refresh the report of one experiment (called after its analysis)."""
        doc = experiment_report(exp, result)
        if doc is None:
            return False
        changed = self._upsert([doc])
        if changed:
            self._embed_missing()
            self._rebuild()
        return bool(changed)

    def _experiment_documents(self) -> list[dict]:
        from app.experiments.engine import to_dict  # local: engine imports the analysis package
        docs = []
        with self.session_factory() as db:
            for row in db.query(ExperimentRow).filter(ExperimentRow.result.isnot(None)).all():
                doc = experiment_report(to_dict(row), row.result or {})
                if doc:
                    docs.append(doc)
        return docs

    def _upsert(self, docs: list[dict]) -> int:
        changed = 0
        with self.session_factory() as db:
            existing = {}
            for row in db.query(KnowledgeChunkRow.doc_id, KnowledgeChunkRow.content_hash).distinct().all():
                existing[row.doc_id] = row.content_hash
            for doc in docs:
                digest = _hash(doc["text"])
                if existing.get(doc["doc_id"]) == digest:
                    continue
                db.query(KnowledgeChunkRow).filter_by(doc_id=doc["doc_id"]).delete()
                for chunk in chunk_text(doc["doc_id"], doc["source"], doc["title"], doc["text"]):
                    db.add(KnowledgeChunkRow(id=chunk.id, doc_id=chunk.doc_id, source=chunk.source,
                                             title=chunk.title, text=chunk.text, content_hash=digest))
                changed += 1
            db.commit()
        return changed

    def _embed_missing(self, batch: int = 16) -> int:
        if self.embedder is None or not self.embedder.available():
            return 0
        done = 0
        with self.session_factory() as db:
            rows = db.query(KnowledgeChunkRow).filter(KnowledgeChunkRow.embedding.is_(None)).all()
            for i in range(0, len(rows), batch):
                group = rows[i:i + batch]
                vectors = self.embedder.embed([f"{r.title}\n{r.text}" for r in group], kind="document")
                for row, vec in zip(group, vectors):
                    row.embedding, row.embedding_model = vec, self.embedder.model
                    done += 1
                db.commit()
        return done

    def _rebuild(self) -> None:
        with self.session_factory() as db:
            rows = db.query(KnowledgeChunkRow).all()
            chunks = {r.id: Chunk(r.id, r.doc_id, r.source, r.title, r.text) for r in rows}
            vectors = {r.id: r.embedding for r in rows if r.embedding}
        with self._lock:
            self._chunks, self._vectors = chunks, vectors
            self._index = BM25Index(list(chunks.values()))

    def _ensure(self) -> None:
        if self._index is None:
            self._rebuild()

    # ---- search ---------------------------------------------------------------------------

    def retrieval_mode(self) -> str:
        if not self._chunks:
            return "none"
        return "hybrid" if self._vectors else "bm25"

    def stats(self) -> dict:
        self._ensure()
        by_source: dict[str, int] = {}
        for c in self._chunks.values():
            by_source[c.source] = by_source.get(c.source, 0) + 1
        return {"chunks": len(self._chunks), "by_source": by_source, "embedded_chunks": len(self._vectors),
                "retrieval": self.retrieval_mode()}

    def search(self, query: str, k: int = 4) -> list[dict]:
        self._ensure()
        if not self._chunks:
            return []
        lexical = self._index.search(query, k=max(k * 3, 10))
        rankings = [[c.id for c, _ in lexical]]
        scores = {c.id: s for c, s in lexical}
        if self._vectors and self.embedder is not None:
            try:
                qvec = self.embedder.embed([query], kind="query")[0]
                rankings.append([c.id for c, _ in dense_search(qvec, self._vectors, self._chunks, k=max(k * 3, 10))])
            except Exception as exc:  # embeddings are an enhancement: fall back to BM25 alone
                logger.warning("dense retrieval unavailable, using BM25 only: %s", exc)
        order = reciprocal_rank_fusion(rankings)[:k] if len(rankings) > 1 else rankings[0][:k]
        return [{"ref": f"doc.{cid}", "doc_id": self._chunks[cid].doc_id, "title": self._chunks[cid].title,
                 "source": self._chunks[cid].source, "text": self._chunks[cid].text,
                 "bm25_score": round(scores.get(cid, 0.0), 3)} for cid in order]
