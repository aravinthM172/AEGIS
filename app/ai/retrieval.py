"""Retrieval for the knowledge base (pure: no DB, no network).

BM25 handles exact technical terms (service names, fault types, timeouts) well and needs no
model. Dense embeddings are an optional second ranking, fused with reciprocal rank fusion;
when no embedding model is available the system says so and uses BM25 alone.
"""
import math
import re
from collections import Counter
from dataclasses import dataclass

STOPWORDS = frozenset("""a an and are as at be by for from has have in is it its of on or that the this to was were will with
you your not no if then than so do does did can may should would could their them they we our but into out up down over
under also such these those there here when where which who whom what how why""".split())

_TOKEN = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    """Lowercase terms; hyphenated names are kept whole AND split ('cpp-service' -> cpp-service, cpp, service)."""
    tokens = []
    for word in _TOKEN.findall(text.lower()):
        parts = re.split(r"[-_]", word)
        for term in ([word] + parts if len(parts) > 1 else [word]):
            if term not in STOPWORDS and len(term) > 1:
                tokens.append(term)
    return tokens


@dataclass(frozen=True)
class Chunk:
    id: str       # "<doc_id>#<n>"
    doc_id: str
    source: str   # architecture | runbook | experiment | incident
    title: str
    text: str


def chunk_text(doc_id: str, source: str, title: str, text: str, max_chars: int = 900) -> list[Chunk]:
    """Split on markdown headings first, then on paragraphs, keeping chunks under max_chars."""
    sections, current = [], []
    for line in text.splitlines():
        if line.startswith("#") and current:
            sections.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        sections.append("\n".join(current))

    pieces: list[str] = []
    for section in sections:
        buf = ""
        for para in re.split(r"\n\s*\n", section):
            para = para.strip()
            if not para:
                continue
            if buf and len(buf) + len(para) + 2 > max_chars:
                pieces.append(buf)
                buf = para
            else:
                buf = f"{buf}\n\n{para}" if buf else para
        if buf:
            pieces.append(buf)
    return [Chunk(f"{doc_id}#{i}", doc_id, source, title, p) for i, p in enumerate(pieces) if p.strip()]


class BM25Index:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self._tf = [Counter(tokenize(f"{c.title} {c.text}")) for c in chunks]
        self._len = [sum(tf.values()) for tf in self._tf]
        self._avg = (sum(self._len) / len(self._len)) if self._len else 0.0
        df: Counter = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        n = len(chunks)
        self._idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def search(self, query: str, k: int = 5) -> list[tuple[Chunk, float]]:
        terms = tokenize(query)
        scored = []
        for i, tf in enumerate(self._tf):
            score = 0.0
            for t in terms:
                f = tf.get(t, 0)
                if f:
                    norm = f + self.k1 * (1 - self.b + self.b * self._len[i] / (self._avg or 1))
                    score += self._idf.get(t, 0.0) * f * (self.k1 + 1) / norm
            if score > 0:
                scored.append((self.chunks[i], score))
        scored.sort(key=lambda cs: (-cs[1], cs[0].id))
        return scored[:k]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


def dense_search(query_vec: list[float], vectors: dict[str, list[float]], chunks: dict[str, Chunk],
                 k: int = 5) -> list[tuple[Chunk, float]]:
    scored = [(chunks[cid], cosine(query_vec, vec)) for cid, vec in vectors.items() if cid in chunks]
    scored.sort(key=lambda cs: (-cs[1], cs[0].id))
    return scored[:k]


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, 1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return [item for item, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]
