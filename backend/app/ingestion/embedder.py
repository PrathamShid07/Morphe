"""Embedding and token counting. The model is loaded once; query and memory text share one code path."""
from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol, Sequence

import numpy as np

from ..common import DependencyUnavailable, ValidationFailure


class Embedder(Protocol):
    dimension: int
    model_name: str
    tokenizer_name: str

    def encode(self, texts: Sequence[str]) -> np.ndarray: ...
    def count_tokens(self, text: str) -> int: ...


def validate_vectors(arr: np.ndarray, expected_dim: int, expected_n: int | None = None) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[1] != expected_dim:
        raise ValidationFailure(f"embedding has shape {arr.shape}, expected (n, {expected_dim})",
                                code="bad_embedding_dimension")
    if expected_n is not None and arr.shape[0] != expected_n:
        raise ValidationFailure("embedding count does not match input count", code="bad_embedding_count")
    if not np.isfinite(arr).all():
        raise ValidationFailure("embedding contains NaN or infinite values", code="non_finite_embedding")
    return arr


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str, dimension: int, batch_size: int = 64):
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(model_name)
        except Exception as exc:
            raise DependencyUnavailable(
                f"Embedding model '{model_name}' could not be loaded: {exc}. The first run needs internet access to "
                "download it; afterwards it is cached locally. MORPHE does not substitute a different model.",
                code="embedding_model_unavailable") from exc
        actual = self._model.get_sentence_embedding_dimension()
        if actual != dimension:
            raise DependencyUnavailable(
                f"Model '{model_name}' produces {actual}-dim vectors but EMBEDDING_DIMENSION={dimension}.",
                code="dimension_mismatch")
        self.dimension = dimension
        self.model_name = model_name
        self.tokenizer_name = f"{model_name}#wordpiece"
        self.max_seq_length = int(getattr(self._model, "max_seq_length", 256))
        self.batch_size = batch_size
        self._tok = self._model.tokenizer

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)
        arr = self._model.encode(list(texts), batch_size=self.batch_size, normalize_embeddings=True,
                                 convert_to_numpy=True, show_progress_bar=False)
        return validate_vectors(arr, self.dimension, len(texts))

    def count_tokens(self, text: str) -> int:
        return len(self._tok.encode(text, add_special_tokens=False))


_WORD = re.compile(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_]")
_STOP = frozenset("a an the of to in on for and or is are was were be been it its this that with as by at from".split())


class HashingEmbedder:
    """Deterministic bag-of-words embedder for tests ONLY (never selectable in production)."""

    def __init__(self, dimension: int = 384):
        self.dimension = dimension
        self.model_name = "test/hashing-embedder"
        self.tokenizer_name = "test/regex-wordpiece"

    def count_tokens(self, text: str) -> int:
        return len(_WORD.findall(text))

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dimension, dtype=np.float32)
        words = [w.lower() for w in re.findall(r"[A-Za-z0-9_]+", text) if w.lower() not in _STOP]
        feats = words + [f"{a}_{b}" for a, b in zip(words, words[1:])]
        for f in feats:
            h = int.from_bytes(hashlib.md5(f.encode()).digest()[:8], "big")
            v[h % self.dimension] += 1.0 if (h >> 32) & 1 else -1.0
            v[(h >> 8) % self.dimension] += 0.5
        n = math.sqrt(float((v * v).sum()))
        if n == 0:
            v[0] = 1.0
            return v
        return v / n

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)
        return validate_vectors(np.stack([self._vec(t) for t in texts]), self.dimension, len(texts))


def build_embedder(settings) -> Embedder:
    if settings.embedding_backend == "hashing":
        return HashingEmbedder(settings.embedding_dimension)
    return SentenceTransformerEmbedder(settings.embedding_model, settings.embedding_dimension,
                                       settings.embedding_batch_size)


class CachedEmbedder:
    """Wraps an embedder with a text->vector cache for repeated evaluation queries."""

    def __init__(self, inner: Embedder, max_items: int = 4096):
        self.inner = inner
        self.dimension = inner.dimension
        self.model_name = inner.model_name
        self.tokenizer_name = inner.tokenizer_name
        self._cache: dict[str, np.ndarray] = {}
        self._max = max_items

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        missing = [t for t in dict.fromkeys(texts) if t not in self._cache]
        if missing:
            if len(self._cache) + len(missing) > self._max:
                self._cache.clear()
            for t, v in zip(missing, self.inner.encode(missing)):
                self._cache[t] = v
        return np.stack([self._cache[t] for t in texts]) if texts else np.zeros((0, self.dimension), np.float32)

    def count_tokens(self, text: str) -> int:
        return self.inner.count_tokens(text)
