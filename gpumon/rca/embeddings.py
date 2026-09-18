"""Embedding backends for semantic retrieval.

The knowledge base can retrieve either lexically (TF-IDF) or semantically
(dense embeddings). Two embedders implement one interface:

* ``TitanEmbedder``   - Amazon Bedrock Titan Text Embeddings (AWS mode). This
  is the production retriever the roadmap called for.
* ``HashingEmbedder`` - a dependency-free deterministic dense embedder
  (feature hashing over token unigrams+bigrams). It lets the embedding
  retrieval path run and be tested locally with no network, standing in for
  Titan exactly as the offline diagnoser stands in for Claude.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

from ..config import settings

_WORD = re.compile(r"[a-z0-9_]+")


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


def _l2(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


class HashingEmbedder:
    """Deterministic dense embeddings via feature hashing.

    Not a learned model, but it captures token overlap in a fixed-width dense
    vector, which is enough to exercise and validate the semantic-retrieval
    code path offline.
    """

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def _hash(self, token: str) -> int:
        digest = hashlib.md5(token.encode()).digest()
        return int.from_bytes(digest[:4], "little") % self.dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            toks = _tokens(text)
            grams = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
            vec = [0.0] * self.dim
            for g in grams:
                vec[self._hash(g)] += 1.0
            out.append(_l2(vec))
        return out


class TitanEmbedder:  # pragma: no cover - needs AWS
    """Amazon Bedrock Titan Text Embeddings v2."""

    def __init__(self, model: str = "amazon.titan-embed-text-v2:0") -> None:
        self.model = model
        self.dim = 1024
        self._client = None

    def _lazy_client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        import json

        client = self._lazy_client()
        vectors = []
        for text in texts:
            resp = client.invoke_model(
                modelId=self.model,
                body=json.dumps({"inputText": text[:8000], "dimensions": self.dim, "normalize": True}),
            )
            vectors.append(json.loads(resp["body"].read())["embedding"])
        return vectors


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
