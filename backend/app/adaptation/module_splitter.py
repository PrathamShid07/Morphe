"""Split planning: propose coherent child modules for an overloaded or mixed-topic module.

Candidate groupings come from metadata boundaries (source, section) and from KMeans (k = 2..max). Each is scored with
the cosine silhouette of the actual embeddings; clustering output is never trusted blindly — groups must be large
enough and sufficiently coherent/separated, otherwise no split is proposed.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..common import humanize_name, slugify
from ..memory.modules import top_keywords
from ..versioning.state import (CO_ACCESS, ArchitectureState, EdgeSpec, ModuleSpec, centroid, cosine, edge_id_for,
                                hierarchy_edges_from_parents)


@dataclass
class SplitPlan:
    module_id: str
    strategy: str
    groups: list[dict[str, Any]]
    silhouette: float
    new_state: ArchitectureState
    evidence: dict[str, Any] = field(default_factory=dict)


def _silhouette(X: np.ndarray, labels: list[Any]) -> float | None:
    from sklearn.metrics import silhouette_score
    uniq = sorted(set(map(str, labels)))
    if len(uniq) < 2 or len(uniq) >= len(labels):
        return None
    try:
        return float(silhouette_score(X, [str(l) for l in labels], metric="cosine"))
    except ValueError:
        return None


def _candidate_groupings(ids: list[str], X: np.ndarray, meta: dict[str, dict[str, Any]], settings
                         ) -> list[dict[str, Any]]:
    cands: list[dict[str, Any]] = []
    for strategy, keyfn in (("by_source", lambda p: p.get("source_id")),
                            ("by_section", lambda p: (p.get("source_id"), p.get("section") or ""))):
        labels = [keyfn(meta[i]) for i in ids]
        cands.append({"strategy": strategy, "labels": [str(l) for l in labels], "kind": "metadata"})
    from sklearn.cluster import KMeans
    for k in range(2, settings.split_max_groups + 1):
        if len(ids) >= k * settings.split_min_group_size:
            km = KMeans(n_clusters=k, n_init=10, random_state=0).fit(X)
            cands.append({"strategy": f"kmeans_k{k}", "labels": [f"c{l}" for l in km.labels_], "kind": "kmeans"})
    return cands


def plan_split(state: ArchitectureState, module_id: str, vectors: dict[str, np.ndarray],
               meta: dict[str, dict[str, Any]], settings, operation_id: str, now: str
               ) -> tuple[SplitPlan | None, dict[str, Any]]:
    """Returns (plan, diagnostics). plan is None when no valid split exists (diagnostics explain why)."""
    ids = sorted(state.assignments.get(module_id, []))
    diag: dict[str, Any] = {"module_id": module_id, "record_count": len(ids), "alternatives": []}
    if len(ids) < 2 * settings.split_min_group_size:
        diag["reason"] = f"module has {len(ids)} records; need at least {2 * settings.split_min_group_size}"
        return None, diag
    X = np.stack([vectors[i] for i in ids])
    best: dict[str, Any] | None = None
    for cand in _candidate_groupings(ids, X, meta, settings):
        sizes = Counter(cand["labels"])
        alt: dict[str, Any] = {"strategy": cand["strategy"], "groups": len(sizes), "sizes": sorted(sizes.values(), reverse=True)}
        if not 2 <= len(sizes) <= settings.split_max_groups:
            alt["valid"], alt["why"] = False, f"{len(sizes)} groups (allowed 2-{settings.split_max_groups})"
        elif min(sizes.values()) < settings.split_min_group_size:
            alt["valid"], alt["why"] = False, f"smallest group has {min(sizes.values())} < {settings.split_min_group_size} records"
        else:
            sil = _silhouette(X, cand["labels"])
            alt["silhouette"] = sil
            if sil is None:
                alt["valid"], alt["why"] = False, "silhouette undefined"
            elif sil < settings.split_min_silhouette:
                alt["valid"], alt["why"] = False, f"silhouette {sil:.3f} < {settings.split_min_silhouette}"
            else:
                alt["valid"] = True
                cand["silhouette"] = sil
                if best is None or sil > best["silhouette"] + (0.0 if cand["kind"] == best["kind"] else
                                                               (0.03 if cand["kind"] == "kmeans" else -0.03)):
                    best = cand
        diag["alternatives"].append(alt)
    if best is None:
        diag["reason"] = "no candidate grouping was both large enough and coherent"
        return None, diag
    diag["silhouette"] = best["silhouette"]
    diag["strategy"] = best["strategy"]

    groups: dict[str, list[str]] = defaultdict(list)
    for i, lab in zip(ids, best["labels"]):
        groups[lab].append(i)
    new = state.copy()
    parent = new.modules[module_id]
    used = set(new.modules)
    children: list[dict[str, Any]] = []
    for lab, members in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        texts = [meta[i].get("text", "") for i in members]
        kws = top_keywords(texts)
        src = Counter(meta[i].get("source_name") for i in members).most_common(1)[0][0]
        sec = Counter(meta[i].get("section") for i in members).most_common(1)[0][0]
        if best["strategy"] == "by_source":
            name = humanize_name(src or "group")
        elif best["strategy"] == "by_section":
            name = f"{humanize_name(src or '')} · {sec}" if sec else humanize_name(src or "group")
        else:
            name = " / ".join(k.title() for k in kws[:2]) or f"Group {len(children) + 1}"
        base = f"mod_{slugify(name)}"
        mid, n = base, 2
        while mid in used:
            mid, n = f"{base}_{n}", n + 1
        used.add(mid)
        purity = Counter(meta[i].get("source_id") for i in members).most_common(1)[0][1] / len(members)
        new.modules[mid] = ModuleSpec(
            module_id=mid, name=name, parent_id=module_id, description=", ".join(kws), keywords=kws,
            profile_vector=centroid([vectors[i] for i in members]), created_at=now, created_by_op=operation_id,
            last_change_op="split", last_change_at=now)
        new.assignments[mid] = sorted(members)
        children.append({"module_id": mid, "name": name, "records": len(members), "keywords": kws,
                         "dominant_source": src, "source_purity": round(purity, 3)})
    new.assignments[module_id] = []  # the original becomes a routing-only container (hierarchy parent)
    parent.last_change_op, parent.last_change_at = "split", now
    # shortcut edges that touched the old module move to the child nearest the neighbour's profile
    moved_edges = 0
    for e in [e for e in list(new.edges.values()) if e.relation_type == CO_ACCESS and module_id in (e.source, e.target)]:
        other = e.target if e.source == module_id else e.source
        del new.edges[e.edge_id]
        ov = new.modules.get(other)
        if ov is None or ov.profile_vector is None:
            continue
        best_child = max(children, key=lambda c: cosine(ov.profile_vector, new.modules[c["module_id"]].profile_vector))
        eid = edge_id_for(CO_ACCESS, other, best_child["module_id"])
        if eid not in new.edges:
            new.edges[eid] = EdgeSpec(eid, *sorted((other, best_child["module_id"])), CO_ACCESS, e.weight,
                                      e.co_access_count, e.created_at, operation_id,
                                      {**e.evidence, "moved_from": module_id})
            moved_edges += 1
    hierarchy_edges_from_parents(new, now, operation_id)
    plan = SplitPlan(module_id, best["strategy"], children, best["silhouette"], new,
                     {"strategy": best["strategy"], "silhouette": round(best["silhouette"], 4), "children": children,
                      "alternatives": diag["alternatives"], "shortcut_edges_reattached": moved_edges,
                      "min_group_size": settings.split_min_group_size,
                      "min_silhouette": settings.split_min_silhouette})
    return plan, diag
