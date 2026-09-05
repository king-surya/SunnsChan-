"""Embedding abstraction. Core memory never depends on a specific backend.

Conceptual surface: embed(text) / embed_batch(texts) -> unit-ish float vectors.
Backends: HashingEmbedder (local, deterministic, dependency-free) and
OllamaEmbedder (HTTP /api/embed, honest failures). Cosine similarity helper
included so retrieval code stays backend-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Protocol
from urllib.request import Request, urlopen

from .memory import tokenize


class EmbeddingProvider(Protocol):
    kind: str
    dim: int

    def embed(self, text: str) -> list[float]:
        ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        ...


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("vectors must be non-empty and same length")
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


@dataclass
class HashingEmbedder:
    """Deterministic local embedder (hashing trick over stemmed tokens).

    No model, no network, no downloads. Quality is modest — it captures
    token overlap in vector form — but the interface is identical to real
    backends, so swapping later changes no retrieval code.
    """

    kind: str = "hashing"
    dim: int = 128

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for token in tokenize(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0.0:
            return vector
        return [v / norm for v in vector]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


@dataclass
class OllamaEmbedder:
    """Ollama /api/embed backend. Unreachable/invalid server output raises."""

    kind: str = "ollama"
    model: str = "nomic-embed-text"
    base_url: str = "http://localhost:11434"
    timeout_secs: float = 30.0
    dim: int = 0  # unknown until first embed; updated from response length

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {"model": self.model, "input": texts}
        request = Request(
            self.base_url.rstrip("/") + "/api/embed",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_secs) as response:
                raw = response.read().decode("utf-8")
        except OSError as exc:
            raise RuntimeError(f"ollama embed server unreachable at {self.base_url}: {exc}") from exc
        try:
            data = json.loads(raw)
            vectors = data["embeddings"]
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(f"ollama embed returned invalid data: {exc}") from exc
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise RuntimeError("ollama embed returned a mismatched embedding count")
        for vector in vectors:
            if not isinstance(vector, list) or not vector or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) for v in vector
            ):
                raise RuntimeError("ollama embed returned a malformed vector")
        self.dim = len(vectors[0])
        return [[float(v) for v in vector] for vector in vectors]
