"""Merge planning. Profile similarity alone never approves a merge: content overlap, fact conflicts and structure
are checked, and usage/metadata evidence is recorded."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

import numpy as np

from ..common import iso
from ..memory.modules import top_keywords
from ..versioning.state import (CO_ACCESS, HIERARCHY, ArchitectureState, EdgeSpec, centroid, cosine, edge_id_for,
                                hierarchy_edges_from_parents)

_NUM = re.compile(r"(?<![\w.])\d+(?:[.,:/\-]\d+)*(?:%|ms|s|gb|mb|kb|x)?(?![\w])", re.I)


def fact_tokens(text: str) -> set[str]:
    """Numeric facts (ports, versions, durations, percentages, dates) that must agree between merged duplicates."""
    return {m.group(0).lower() for m in _NUM.finditer(text)}


def facts_conflict(a: str, b: str) -> bool:
    fa, fb = fact_tokens(a), fact_tokens(b)
    return bool(fa - fb) and bool(fb - fa)  # each side asserts a number the other lacks


@dataclass
class MergePlan:
    survivor: str
    absorbed: str
    new_state: ArchitectureState
    evidence: dict[str, Any] = field(default_factory=dict)


def assess_pair(state: ArchitectureState, a: str, b: str, vectors: dict[str, np.ndarray],
                meta: dict[str, dict[str, Any]], settings, window: dict[str, Any] | None = None) -> dict[str, Any]:
    pa, pb = state.modules[a].profile_vector, state.modules[b].profile_vector
    ev: dict[str, Any] = {"modules": [a, b], "profile_similarity": None, "overlap_ratio": 0.0,
                          "overlapping_records": 0, "conflicts": 0, "eligible": False, "blockers": []}
    if pa is None or pb is None:
        ev["blockers"].append("a module has no profile vector")
        return ev
    sim = cosine(pa, pb)
    ev["profile_similarity"] = round(sim, 4)
    small, large = (a, b) if state.record_count(a) <= state.record_count(b) else (b, a)
    S = np.stack([vectors[i] for i in state.assignments[small]])
    L = np.stack([vectors[i] for i in state.assignments[large]])
    sims = S @ L.T  # unit vectors -> cosine
    best_idx = sims.argmax(axis=1)
    best = sims.max(axis=1)
    dup_mask = best >= settings.merge_pair_similarity
    ev["overlapping_records"] = int(dup_mask.sum())
    ev["overlap_ratio"] = round(float(dup_mask.mean()), 4)
    conflicts = []
    small_ids, large_ids = state.assignments[small], state.assignments[large]
    for i in np.flatnonzero(dup_mask):
        ta, tb = meta[small_ids[i]].get("text", ""), meta[large_ids[int(best_idx[i])]].get("text", "")
        if facts_conflict(ta, tb):
            conflicts.append([small_ids[i], large_ids[int(best_idx[i])]])
    ev["conflicts"] = len(conflicts)
    ev["conflicting_pairs"] = conflicts[:5]
    ka, kb = set(state.modules[a].keywords), set(state.modules[b].keywords)
    ev["shared_keywords"] = sorted(ka & kb)
    ev["keyword_jaccard"] = round(len(ka & kb) / len(ka | kb), 3) if ka | kb else 0.0
    ev["shared_sources"] = sorted({meta[i].get("source_name") for i in state.assignments[a]} &
                                  {meta[i].get("source_name") for i in state.assignments[b]})
    existing = [e for e in state.edges.values() if e.relation_type == CO_ACCESS and {e.source, e.target} == {a, b}]
    pair_key = "|".join(sorted((a, b)))
    ev["usage"] = {"co_access_edge": bool(existing),
                   "window_co_access_count": int((window or {}).get("pair_counts", {}).get(pair_key, 0))}
    ev["unique_records_preserved"] = state.record_count(a) + state.record_count(b)
    blockers = ev["blockers"]
    if a in state.ancestors(b) or b in state.ancestors(a) or state.children_of(a) or state.children_of(b):
        blockers.append("modules are hierarchically related or have children")
    if sim < settings.merge_profile_similarity:
        blockers.append(f"profile similarity {sim:.3f} < {settings.merge_profile_similarity}")
    if ev["overlap_ratio"] < settings.merge_overlap_min:
        blockers.append(f"content overlap {ev['overlap_ratio']:.2f} < {settings.merge_overlap_min}")
    if len(conflicts) > settings.merge_max_conflicts:
        blockers.append(f"{len(conflicts)} overlapping record pairs state conflicting numeric facts")
    ev["eligible"] = not blockers
    ev["thresholds"] = {"profile_similarity": settings.merge_profile_similarity, "overlap": settings.merge_overlap_min,
                        "pair_similarity": settings.merge_pair_similarity, "max_conflicts": settings.merge_max_conflicts}
    return ev


def find_merge_candidates(state: ArchitectureState, vectors: dict[str, np.ndarray], meta: dict[str, dict[str, Any]],
                          settings, window: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    out = []
    mods = state.content_modules()
    for a, b in combinations(mods, 2):
        pa, pb = state.modules[a].profile_vector, state.modules[b].profile_vector
        if pa is None or pb is None or cosine(pa, pb) < settings.merge_profile_similarity - 0.1:
            continue  # cheap pre-filter; full assessment below
        out.append(assess_pair(state, a, b, vectors, meta, settings, window))
    return sorted(out, key=lambda e: (-int(e["eligible"]), -(e["profile_similarity"] or 0)))


def plan_merge(state: ArchitectureState, a: str, b: str, vectors: dict[str, np.ndarray],
               meta: dict[str, dict[str, Any]], settings, operation_id: str, now: str,
               window: dict[str, Any] | None = None) -> tuple[MergePlan | None, dict[str, Any]]:
    for m in (a, b):
        if m not in state.modules or not state.assignments.get(m):
            return None, {"eligible": False, "blockers": [f"module {m} does not exist or has no records"]}
    ev = assess_pair(state, a, b, vectors, meta, settings, window)
    if not ev["eligible"]:
        return None, ev
    survivor, absorbed = sorted((a, b), key=lambda m: (-state.record_count(m), state.modules[m].created_at or "", m))
    new = state.copy()
    members = sorted(new.assignments[survivor] + new.assignments.pop(absorbed))
    new.assignments[survivor] = members
    abs_spec, sur_spec = new.modules.pop(absorbed), new.modules[survivor]
    if not sur_spec.parent_id and abs_spec.parent_id and abs_spec.parent_id in new.modules:
        sur_spec.parent_id = abs_spec.parent_id
    sur_spec.profile_vector = centroid([vectors[i] for i in members])
    sur_spec.keywords = top_keywords([meta[i].get("text", "") for i in members])
    sur_spec.description = f"{sur_spec.description or ', '.join(sur_spec.keywords)} (merged with {abs_spec.name})".strip()
    sur_spec.last_change_op, sur_spec.last_change_at = "merge", now
    for e in [e for e in list(new.edges.values()) if absorbed in (e.source, e.target)]:
        del new.edges[e.edge_id]
        if e.relation_type != CO_ACCESS:
            continue
        other = e.target if e.source == absorbed else e.source
        if other == survivor:
            continue
        eid = edge_id_for(CO_ACCESS, other, survivor)
        if eid in new.edges:
            ex = new.edges[eid]
            ex.co_access_count += e.co_access_count
            ex.weight = max(ex.weight, e.weight)
        else:
            new.edges[eid] = EdgeSpec(eid, *sorted((other, survivor)), CO_ACCESS, e.weight, e.co_access_count,
                                      e.created_at, operation_id, {**e.evidence, "merged_from": absorbed})
    hierarchy_edges_from_parents(new, now, operation_id)
    ev.update({"survivor": survivor, "absorbed": absorbed, "records_moved": len(state.assignments[absorbed]),
               "merged_name": sur_spec.name, "absorbed_name": abs_spec.name})
    return MergePlan(survivor, absorbed, new, ev), ev
