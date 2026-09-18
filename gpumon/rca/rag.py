"""Retrieval over runbooks and past incidents.

Before asking the LLM to diagnose an incident we retrieve the most similar
prior incidents and the most relevant runbooks, and feed them in as grounding
context. This is what stops the model from hallucinating a plausible-sounding
but generic answer: it reasons over *this* cluster's history.

Retrieval is pluggable behind one ``Retriever`` interface (``add`` /
``search``):

* ``TfidfRetriever``      - dependency-free lexical retriever, the local
  default, so the demo needs no embedding endpoint.
* ``EmbeddingRetriever``  - dense semantic retrieval over an ``Embedder``. In
  AWS mode it is backed by Bedrock Titan embeddings; locally it can use the
  deterministic ``HashingEmbedder`` (set ``ARGUS_RETRIEVER=embedding``).

The factory ``build_retriever`` picks one from config, so switching the whole
platform to semantic search is a single env var.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ..config import settings
from .embeddings import Embedder, HashingEmbedder, cosine

_ROOT = Path(__file__).resolve().parent.parent.parent
RUNBOOK_DIR = _ROOT / "runbooks"
SEED_INCIDENTS = _ROOT / "data" / "seed_incidents.json"

_WORD = re.compile(r"[a-z0-9_]+")


def _tokenize(text: str) -> list[str]:
    return _WORD.findall(text.lower())


@dataclass
class Document:
    doc_id: str
    kind: str  # "runbook" | "incident"
    title: str
    text: str


class TfidfRetriever:
    """Small, transparent TF-IDF cosine retriever."""

    def __init__(self) -> None:
        self._docs: list[Document] = []
        self._tf: list[Counter] = []
        self._idf: dict[str, float] = {}
        self._dirty = True

    def add(self, doc: Document) -> None:
        self._docs.append(doc)
        self._tf.append(Counter(_tokenize(doc.title + " " + doc.text)))
        self._dirty = True

    def _fit_idf(self) -> None:
        n = len(self._docs)
        df: Counter = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        self._idf = {t: math.log((1 + n) / (1 + c)) + 1.0 for t, c in df.items()}
        self._dirty = False

    def _vector(self, tf: Counter) -> dict[str, float]:
        return {t: c * self._idf.get(t, 0.0) for t, c in tf.items()}

    @staticmethod
    def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
        if not a or not b:
            return 0.0
        common = set(a) & set(b)
        dot = sum(a[t] * b[t] for t in common)
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0

    def search(self, query: str, k: int = 3, kind: str | None = None) -> list[tuple[Document, float]]:
        if not self._docs:
            return []
        if self._dirty:
            self._fit_idf()
        qv = self._vector(Counter(_tokenize(query)))
        scored = []
        for doc, tf in zip(self._docs, self._tf):
            if kind and doc.kind != kind:
                continue
            scored.append((doc, self._cosine(qv, self._vector(tf))))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s for s in scored[:k] if s[1] > 0]


class EmbeddingRetriever:
    """Dense semantic retriever over a pluggable embedder.

    Documents are embedded on insert and queries at search time; ranking is
    cosine similarity. In production the embedder is Titan and the vectors
    would live in a managed vector index (OpenSearch/pgvector); the in-memory
    list here keeps the local corpus (a few dozen docs) trivial.
    """

    def __init__(self, embedder: Embedder) -> None:
        self.embedder = embedder
        self._docs: list[Document] = []
        self._vecs: list[list[float]] = []

    def add(self, doc: Document) -> None:
        self._docs.append(doc)
        self._vecs.append(self.embedder.embed([doc.title + " " + doc.text])[0])

    def search(self, query: str, k: int = 3, kind: str | None = None) -> list[tuple[Document, float]]:
        if not self._docs:
            return []
        qv = self.embedder.embed([query])[0]
        scored = [
            (doc, cosine(qv, vec))
            for doc, vec in zip(self._docs, self._vecs)
            if not kind or doc.kind == kind
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s for s in scored[:k] if s[1] > 0]


def build_retriever():
    """Select a retriever from config: Titan in AWS, else TF-IDF (or hashing)."""
    if settings.use_bedrock:
        from .embeddings import TitanEmbedder

        return EmbeddingRetriever(TitanEmbedder(settings.embed_model))
    if settings.retriever == "embedding":
        return EmbeddingRetriever(HashingEmbedder())
    return TfidfRetriever()


class KnowledgeBase:
    """Runbooks (from disk) + resolved incidents (from the store)."""

    def __init__(self, retriever=None) -> None:
        self.retriever = retriever or build_retriever()
        self._load_runbooks()
        self._load_seed_incidents()

    def _load_runbooks(self) -> None:
        if not RUNBOOK_DIR.exists():
            return
        for path in sorted(RUNBOOK_DIR.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            title = text.splitlines()[0].lstrip("# ").strip() if text else path.stem
            self.retriever.add(Document(path.stem, "runbook", title, text))

    def _load_seed_incidents(self) -> None:
        """Bootstrap the past-incident corpus so RAG has history on day one."""
        if not SEED_INCIDENTS.exists():
            return
        for item in json.loads(SEED_INCIDENTS.read_text(encoding="utf-8")):
            self.retriever.add(Document(item["id"], "incident", item["title"], item["text"]))

    def index_incident(self, incident_id: str, title: str, text: str) -> None:
        self.retriever.add(Document(incident_id, "incident", title, text))

    def retrieve(self, query: str, k: int = 3):
        runbooks = self.retriever.search(query, k=k, kind="runbook")
        incidents = self.retriever.search(query, k=k, kind="incident")
        return runbooks, incidents


kb = KnowledgeBase()
