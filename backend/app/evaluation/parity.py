"""Live-versus-sandbox parity analysis.

The benchmark evaluates architectures in an isolated in-memory sandbox (`Evaluator.build_sandbox`) while production queries run on
the live stores. Both use the same `RetrievalEngine`, so for the same architecture they must return the same ranking. This module
runs both on identical query vectors and records, for every query, the FIRST stage at which the outputs diverge and a classified cause.

Stages, in pipeline order: query_vector -> seed_modules -> candidate_modules -> guided_space -> guided_ranking -> fallback_decision
-> final_ranking. A final-ranking difference is classified as an exact score tie, a float-noise tie (<= 1e-6), or unexplained. An exact
numpy oracle over the stored vectors separates "the vector search is approximate" from "the vector search is exact but ties exist".
"""
from __future__ import annotations

from typing import Any, Sequence

import numpy as np

TOL_TIE = 0.0  # exactly equal float scores
TOL_NOISE = 1e-6  # float32 accumulation noise
STAGES = ("query_vector", "seed_modules", "candidate_modules", "guided_space", "guided_ranking", "fallback_decision", "final_ranking")


def stages_of(outcome) -> dict[str, Any]:
    """Machine-readable per-stage view of one `RetrievalOutcome`."""
    return {
        "seed_modules": [(m, float(s)) for m, s in outcome.seeds],
        "candidate_modules": [c.module_id for c in outcome.candidates],
        "guided_space": int(outcome.guided_candidate_count),
        "guided_ranking": [(h.memory_id, float(h.score)) for h in outcome.guided_results],
        "fallback_decision": {"used": bool(outcome.fallback_used), "reason": outcome.fallback_reason},
        "final_ranking": [(h.memory_id, float(h.score)) for h in outcome.results],
    }


def _scores_differ(a: Sequence[tuple[str, float]], b: Sequence[tuple[str, float]], tol: float) -> bool:
    return any(abs(x[1] - y[1]) > tol for x, y in zip(a, b))


def _ids(items: Sequence[tuple[str, float]]) -> list[str]:
    return [i for i, _ in items]


def first_divergence(a: dict[str, Any], b: dict[str, Any], depth: int = 10) -> str | None:
    """First pipeline stage at which two stage views differ (None when identical down to the top-`depth`)."""
    if _ids(a["seed_modules"]) != _ids(b["seed_modules"]) or _scores_differ(a["seed_modules"], b["seed_modules"], TOL_NOISE):
        return "seed_modules"
    if a["candidate_modules"] != b["candidate_modules"]:
        return "candidate_modules"
    if a["guided_space"] != b["guided_space"]:
        return "guided_space"
    if _ids(a["guided_ranking"])[:depth] != _ids(b["guided_ranking"])[:depth]:
        return "guided_ranking"
    if a["fallback_decision"]["used"] != b["fallback_decision"]["used"]:
        return "fallback_decision"
    if _ids(a["final_ranking"])[:depth] != _ids(b["final_ranking"])[:depth]:
        return "final_ranking"
    return None


def classify_ranking_difference(a: Sequence[tuple[str, float]], b: Sequence[tuple[str, float]], depth: int = 10) -> dict[str, Any]:
    """Why two ranked lists differ in their top-`depth`: tie ordering, float noise, membership at the cut-off, or unexplained."""
    ta, tb = list(a)[:depth], list(b)[:depth]
    ia, ib = _ids(ta), _ids(tb)
    first = next((i for i, (x, y) in enumerate(zip(ia, ib)) if x != y), None)
    info: dict[str, Any] = {"first_differing_rank": None if first is None else first + 1, "same_membership_top_k": set(ia) == set(ib)}
    if first is None:
        sa_, sb_ = dict(a), dict(b)
        moved = [i for i in ia if abs(sa_[i] - sb_.get(i, float("inf"))) > TOL_NOISE]
        info["cause"] = "score_disagreement" if moved else "identical"  # same order can still hide a different score
        if moved:
            info["score_disagreement_for_same_doc"] = [{"id": i, "a": sa_[i], "b": sb_.get(i)} for i in moved]
        return info
    sa, sb = dict(a), dict(b)  # full lists: scores of documents wherever they appear
    # an entry present in only one list: tied with the other list's boundary score?
    differing = [d for d in dict.fromkeys(ia[first:] + ib[first:]) if ia[first:].count(d) != ib[first:].count(d) or
                 (d in ia and d in ib and ia.index(d) != ib.index(d))]
    pair_scores = [(d, sa.get(d), sb.get(d)) for d in differing]
    spread = []
    for d, x, y in pair_scores:
        if x is not None and y is not None and abs(x - y) > TOL_NOISE:
            spread.append((d, x, y))
    level_scores = [x if x is not None else y for _, x, y in pair_scores]
    window = max(level_scores) - min(level_scores) if level_scores else 0.0
    info.update({"differing_documents": [d for d, _, _ in pair_scores], "score_window_of_differing_docs": window,
                 "score_disagreement_for_same_doc": [{"id": d, "a": x, "b": y} for d, x, y in spread]})
    if spread:
        info["cause"] = "score_disagreement"  # the same document has a materially different score in the two systems
    elif window <= TOL_TIE:
        info["cause"] = "exact_score_tie"
    elif window <= TOL_NOISE:
        info["cause"] = "float_noise_tie"
    else:
        info["cause"] = "unexplained"
    return info


def numpy_oracle(mat: np.ndarray, qvec: np.ndarray, ids: Sequence[str], k: int = 100) -> list[tuple[str, float]]:
    """Exact cosine ranking in float64 over the stored matrix (diagnostic baseline, not a production path)."""
    m = mat.astype(np.float64)
    m /= np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-12)
    q = np.asarray(qvec, dtype=np.float64)
    q = q / max(np.linalg.norm(q), 1e-12)
    s = m @ q
    order = np.lexsort((np.asarray(ids), -s))[:k]  # score desc, id asc
    return [(ids[i], float(s[i])) for i in order]


def tie_groups(mat: np.ndarray, ids: Sequence[str]) -> dict[str, Any]:
    """How many documents share a bit-identical vector (the precondition for exact score ties)."""
    seen: dict[bytes, list[str]] = {}
    for i, row in enumerate(mat):
        seen.setdefault(row.tobytes(), []).append(ids[i])
    groups = [v for v in seen.values() if len(v) > 1]
    return {"documents": len(ids), "groups_of_identical_vectors": len(groups), "documents_in_such_groups": sum(len(g) for g in groups),
            "largest_group": max((len(g) for g in groups), default=1), "examples": [g[:4] for g in groups[:5]]}


def qdrant_config(store) -> dict[str, Any]:
    """Collection configuration, payload schema and deployment mode actually in use."""
    out: dict[str, Any] = {"mode": "local (in-process)" if store.is_local else "server", "path": store.local_path}
    for kind, name in (("memories", store.memory_collection), ("modules", store.module_collection)):
        info = store.client.get_collection(name)
        params = info.config.params
        vec = params.vectors
        out[kind] = {"name": name, "points": info.points_count, "vector_size": getattr(vec, "size", None),
                     "distance": str(getattr(vec, "distance", None)), "payload_schema": {k: str(v) for k, v in (info.payload_schema or {}).items()},
                     "hnsw_config": str(info.config.hnsw_config), "optimizer": str(info.config.optimizer_config)[:200]}
    return out


def profile_vector_diff(live_store, state) -> dict[str, Any]:
    """Live module-profile vectors (Qdrant) versus the architecture state's profile vectors used by the sandbox."""
    live = live_store.list_module_profiles(with_vectors=True)
    worst, worst_id, missing = 0.0, None, []
    for m in state.content_modules():
        v = state.modules[m].profile_vector
        if m not in live or live[m][1] is None or v is None:
            missing.append(m)
            continue
        a = np.asarray(live[m][1], dtype=np.float64)
        b = np.asarray(v, dtype=np.float64)
        a /= max(np.linalg.norm(a), 1e-12)
        b /= max(np.linalg.norm(b), 1e-12)
        d = float(np.max(np.abs(a - b)))
        if d > worst:
            worst, worst_id = d, m
    return {"content_modules": len(state.content_modules()), "live_profiles": len(live), "missing_in_one_side": missing,
            "max_abs_diff_after_normalisation": worst, "module_with_max_diff": worst_id}


def graph_diff(a, b, limit: int = 5) -> dict[str, Any]:
    """Explicit difference between two module graphs (nodes with record counts, typed edges with weights and direction)."""
    na = {n: int(d.get("active_record_count", 0)) for n, d in a.nodes(data=True)}
    nb = {n: int(d.get("active_record_count", 0)) for n, d in b.nodes(data=True)}
    ea = {(u, v, k): (round(float(d.get("weight", 0.0)), 9), d.get("source"), d.get("target")) for u, v, k, d in a.edges(keys=True, data=True)}
    eb = {(u, v, k): (round(float(d.get("weight", 0.0)), 9), d.get("source"), d.get("target")) for u, v, k, d in b.edges(keys=True, data=True)}
    # an undirected multigraph stores (u, v, k) in either orientation: canonicalise
    canon = lambda m: {(min(u, v), max(u, v), k): val for (u, v, k), val in m.items()}  # noqa: E731
    ea, eb = canon(ea), canon(eb)
    return {"nodes": [len(na), len(nb)], "edges": [len(ea), len(eb)],
            "node_count_differences": [(n, na.get(n), nb.get(n)) for n in sorted(set(na) | set(nb)) if na.get(n) != nb.get(n)][:limit],
            "edge_differences": [(list(e), ea.get(e), eb.get(e)) for e in sorted(set(ea) | set(eb)) if ea.get(e) != eb.get(e)][:limit],
            "identical": na == nb and ea == eb}


def cutoff_ties(oracle: Sequence[tuple[str, float]], ks: Sequence[int] = (10, 100)) -> dict[str, bool]:
    """Does an exact score tie straddle rank k / k+1 (i.e. which tied document enters the top-k is arbitrary)?"""
    return {f"tie_at_{k}": len(oracle) > k and oracle[k - 1][1] == oracle[k][1] for k in ks}


def harness_style_ranking(results: dict[str, float]) -> list[str]:
    """How the pre-existing parity check ordered the SANDBOX run: score desc then BEIR document id."""
    return [d for d, _ in sorted(results.items(), key=lambda kv: (-kv[1], kv[0]))]


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_stage: dict[str, int] = {}
    by_cause: dict[str, int] = {}
    for r in rows:
        if r["diverges_at"]:
            by_stage[r["diverges_at"]] = by_stage.get(r["diverges_at"], 0) + 1
            c = r.get("cause", "unclassified")
            by_cause[c] = by_cause.get(c, 0) + 1
    return {"queries": len(rows), "identical": sum(1 for r in rows if not r["diverges_at"]), "divergent": sum(1 for r in rows if r["diverges_at"]),
            "first_divergence_stage_counts": by_stage, "cause_counts": by_cause}
