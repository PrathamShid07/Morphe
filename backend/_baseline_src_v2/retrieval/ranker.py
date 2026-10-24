"""Ranking and weak-result detection. Qdrant cosine similarity is the primary rank; ties break on memory_id."""
from __future__ import annotations

from typing import Sequence

from ..storage.qdrant_store import Hit


def rank_hits(hits: Sequence[Hit], top_k: int) -> list[Hit]:
    best: dict[str, Hit] = {}
    for h in hits:
        if h.memory_id not in best or h.score > best[h.memory_id].score:
            best[h.memory_id] = h
    return sorted(best.values(), key=lambda h: (-h.score, h.memory_id))[:top_k]


def weak_reason(hits: Sequence[Hit], top_k: int, total_active: int, min_score: float) -> str | None:
    """Why guided retrieval should be distrusted (None when it looks sufficient)."""
    if not hits:
        return "guided search returned no results"
    if len(hits) < min(top_k, total_active):
        return f"guided search returned only {len(hits)} of {top_k} requested results"
    if hits[0].score < min_score:
        return f"best guided score {hits[0].score:.3f} is below the fallback threshold {min_score:.2f}"
    return None
