"""Exact and near-duplicate detection. Similar-but-distinct or conflicting facts are never collapsed."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from ..adaptation.module_merger import facts_conflict
from ..memory.models import is_protected, text_fingerprint
from .summarizer import critical_facts


def exact_duplicate_groups(docs: dict[str, dict[str, Any]]) -> list[list[str]]:
    """Groups (size >= 2) of active memories whose text is identical after whitespace/case normalisation."""
    by_fp: dict[str, list[str]] = defaultdict(list)
    for mid, d in docs.items():
        by_fp[text_fingerprint(d["text"])].append(mid)
    groups = []
    for ids in by_fp.values():
        if len(ids) > 1:
            ids.sort(key=lambda m: (not is_protected(docs[m]), -docs[m].get("access_count", 0),
                                    docs[m].get("created_at", ""), m))
            groups.append(ids)  # first element is the keeper
    return sorted(groups, key=lambda g: g[0])


def compatible(a: str, b: str) -> bool:
    """Two near-identical texts may be consolidated only if neither asserts a fact the other contradicts or lacks."""
    if facts_conflict(a, b):
        return False
    fa, fb = critical_facts(a), critical_facts(b)
    return fa <= fb or fb <= fa


def near_duplicate_pairs(ids: list[str], vectors: dict[str, np.ndarray], docs: dict[str, dict[str, Any]],
                         threshold: float) -> list[tuple[str, str, float]]:
    """(keeper, retired, similarity) for compatible near-duplicates within one module."""
    if len(ids) < 2:
        return []
    M = np.stack([vectors[i] for i in ids])
    S = M @ M.T
    cand = [(float(S[i, j]), i, j) for i in range(len(ids)) for j in range(i + 1, len(ids)) if S[i, j] >= threshold]
    cand.sort(reverse=True)
    retired: set[str] = set()
    kept: set[str] = set()
    out: list[tuple[str, str, float]] = []
    for sim, i, j in cand:
        a, b = ids[i], ids[j]
        if a in retired or b in retired:
            continue
        da, db = docs[a], docs[b]
        if text_fingerprint(da["text"]) == text_fingerprint(db["text"]):
            continue  # exact duplicates are handled by the exact step
        if is_protected(da) and is_protected(db):
            continue
        if not compatible(da["text"], db["text"]):
            continue
        # keeper: protected first, else the more informative (more tokens), then older
        order = sorted((a, b), key=lambda m: (not is_protected(docs[m]), -docs[m].get("token_count", 0),
                                              docs[m].get("created_at", ""), m))
        keep, drop = order
        if is_protected(docs[drop]):
            continue
        out.append((keep, drop, sim))
        retired.add(drop)
        kept.add(keep)
    return out
