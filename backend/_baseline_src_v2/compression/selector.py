"""Candidate selection for compression: what may be summarised, shortened, or (in an emergency) deleted."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

import numpy as np

from ..memory.models import is_protected

_SECRET = re.compile(r"(?i)(api[_-]?key|secret|passw(or)?d|token|bearer)\s*[:=]|\b[A-Za-z0-9+/_\-]{32,}\b|"
                     r"\b\w+://[^\s:@]+:[^\s@]+@")
IMPORTANCE_RANK = {"low": 0, "normal": 1, "high": 2, "critical": 3}


def is_structured(text: str) -> bool:
    """Exact-content records (code, keys, connection strings) are never summarised or shortened."""
    return "```" in text or "~~~" in text or bool(_SECRET.search(text)) or bool(re.search(r"^( {4}|\t)\S", text, re.M))


def can_rewrite(doc: dict[str, Any]) -> bool:
    return (not is_protected(doc) and doc.get("kind", "chunk") == "chunk" and not is_structured(doc["text"]))


def summary_groups(docs: dict[str, dict[str, Any]], vectors: dict[str, np.ndarray], min_size: int,
                   similarity: float, max_group: int = 8) -> list[list[str]]:
    """Cluster rewritable records within each module by embedding similarity; groups of >= min_size are candidates."""
    from sklearn.cluster import AgglomerativeClustering
    by_mod: dict[str, list[str]] = defaultdict(list)
    for mid, d in docs.items():
        if can_rewrite(d):
            by_mod[d["module_id"]].append(mid)
    groups: list[list[str]] = []
    for _, ids in sorted(by_mod.items()):
        if len(ids) < min_size:
            continue
        ids = sorted(ids)
        X = np.stack([vectors[i] for i in ids])
        labels = AgglomerativeClustering(n_clusters=None, metric="cosine", linkage="average",
                                         distance_threshold=1 - similarity).fit_predict(X)
        clusters: dict[int, list[str]] = defaultdict(list)
        for i, lab in zip(ids, labels):
            clusters[int(lab)].append(i)
        for members in clusters.values():
            if len(members) >= min_size:
                groups.append(members[:max_group])
    return sorted(groups, key=lambda g: (-sum(docs[m]["token_count"] for m in g), g[0]))


def emergency_order(docs: dict[str, dict[str, Any]]) -> list[str]:
    """Deletion order: never pinned/critical; obsolete (low importance) first, then least-used, then oldest.
    Low access alone is a supporting signal, not proof of uselessness, hence importance dominates the ordering."""
    eligible = [m for m, d in docs.items() if not is_protected(d)]
    return sorted(eligible, key=lambda m: (IMPORTANCE_RANK.get(docs[m].get("importance", "normal"), 1),
                                           docs[m].get("access_count", 0),
                                           docs[m].get("last_accessed_at") or docs[m].get("created_at", ""), m))
