"""Graph-guided module selection: profile-vector seeds, then NetworkX neighbourhood expansion.

This is the functional link between graph structure and retrieval: the set of module IDs returned here is exactly
what the filtered Qdrant memory search is restricted to, so adding/removing edges or moving records between
modules changes which memories can be found by the guided path.
"""
from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from ..versioning.state import CO_ACCESS, HIERARCHY


@dataclass
class Candidate:
    module_id: str
    origin: str  # seed | co_access | sibling | hierarchy
    via: str | None = None
    score: float | None = None
    stage: int | None = None  # progressive expansion: the stage in which the module was searched


def _is_content(g: nx.MultiGraph, m: str) -> bool:
    return m in g and g.nodes[m].get("active_record_count", 0) > 0


def expand_candidates(seeds: list[tuple[str, float]], g: nx.MultiGraph, max_candidates: int) -> list[Candidate]:
    """Ordered candidate modules: seeds first, then shortcut neighbours (by weight), then hierarchy neighbours."""
    out: list[Candidate] = []
    seen: set[str] = set()

    def add(c: Candidate) -> bool:
        if len(out) >= max_candidates or c.module_id in seen or not _is_content(g, c.module_id):
            return False
        seen.add(c.module_id)
        out.append(c)
        return True

    for m, score in seeds:
        add(Candidate(m, "seed", None, score))

    # tier 2: co-access shortcuts, strongest first across all seeds
    shortcuts: list[tuple[float, int, str, str]] = []
    for si, (m, _) in enumerate(seeds):
        if m not in g:
            continue
        for _, other, key, data in g.edges(m, keys=True, data=True):
            if key == CO_ACCESS:
                shortcuts.append((-float(data.get("weight", 0.0)), si, other, m))
    for _, _, other, via in sorted(shortcuts):
        add(Candidate(other, "co_access", via))

    # tier 3: hierarchy — direct children with records, then siblings through the parent container
    for m, _ in seeds:
        if m not in g:
            continue
        hier = sorted((o, d) for _, o, k, d in g.edges(m, keys=True, data=True) if k == HIERARCHY)
        for other, data in hier:
            if data.get("source") == m:  # m is the parent of `other`
                add(Candidate(other, "hierarchy", m))
    for m, _ in seeds:
        if m not in g:
            continue
        for _, parent, k, d in g.edges(m, keys=True, data=True):
            if k == HIERARCHY and d.get("target") == m:  # `parent` is m's parent
                for _, sib, k2, d2 in sorted(g.edges(parent, keys=True, data=True), key=lambda x: x[1]):
                    if k2 == HIERARCHY and d2.get("source") == parent and sib != m:
                        add(Candidate(sib, "sibling", m))
    return out
