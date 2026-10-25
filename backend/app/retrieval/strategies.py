"""Experimental retrieval strategies. They reuse the exact primitives of `RetrievalEngine` (Qdrant memory/profile search, the NetworkX
module graph, `rank_hits`, `weak_reason`) and are reached through `RetrievalEngine.retrieve(mode="progressive" | "soft")`; the existing
"global" and "graph" (fixed hard-filter) paths are untouched.

PROGRESSIVE GRAPH EXPANSION  (mode="progressive") -- search a few modules, look at the evidence, widen only if needed
  stage 1   rank every module profile by similarity to the query; search the top `progressive_seed_modules`
  stage n   add `progressive_step_modules` more modules (policy: profile rank | graph neighbours first | blend) and search ONLY the new ones;
            hits are merged by memory id (modules are disjoint, so scores are never recomputed or double counted)
  stop      (query-time signals only; no relevance labels)
              * "stable"   the top-k membership did not change after `progressive_patience` consecutive expansion steps
              * "budget"   `progressive_max_modules` or `progressive_max_steps` reached
              * "all modules searched"
  fallback  global search when the result is weak (no/too few results or best score < fallback_min_score: the existing rule) OR when the
            budget ended while the top-k was still changing (`progressive_fallback_on_budget`): that means the evidence is unsettled.

SOFT GRAPH ROUTING  (mode="soft") -- nothing is excluded; module information only re-orders a global dense candidate pool
  pool    global dense top-N (`soft_candidate_pool`), cosine similarity, ties by memory id
  prior   prior(m) = (1-g) * P(m) + g * G(m)     in [0, 1]
            P(m) = min-max normalised query-to-module-profile similarity over all modules for this query
            G(m) = 1 / (1 + d(m))  with d = graph distance from the top `soft_seed_modules` profile matches, over hierarchy edges and over
                   co-access edges that have at least `coaccess_min_count` observations (0 beyond `soft_graph_depth`)
  score   final = dense + lambda * (prior - mean prior over the pool)        lambda = `soft_weight`, so |shift| <= lambda
            * lambda = 0 reproduces the dense ranking exactly; a module with no profile gets the pool-mean prior (shift 0)
            * the shift is a ranking prior, NOT a relevance label, and every component is reported in the debug trace
  Soft routing runs a full global search: it can change ranking quality, it cannot reduce the searched set.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any

from ..storage.qdrant_store import Hit
from ..versioning.state import CO_ACCESS, HIERARCHY
from .graph_selector import Candidate
from .ranker import rank_hits, weak_reason


# ------------------------------------------------------------------------------------------------ shared helpers
def _content_modules(g) -> list[str]:
    return [n for n, d in g.nodes(data=True) if int(d.get("active_record_count", 0)) > 0]


def _records(g, module_id: str) -> int:
    return int(g.nodes[module_id].get("active_record_count", 0)) if module_id in g else 0


def _name(g, module_id: str) -> str:
    return g.nodes[module_id].get("name", module_id) if module_id in g else module_id


def _linked(g, searched: set[str]) -> set[str]:
    """Modules joined to a searched module by a real edge: hierarchy parent/children, hierarchy siblings (through the shared parent) and
    co-access shortcuts. Only structure that exists in the graph; nothing is inferred."""
    out: set[str] = set()
    for m in searched:
        if m not in g:
            continue
        for _, other, key, d in g.edges(m, keys=True, data=True):
            out.add(other)
            if key == HIERARCHY and d.get("target") == m:  # `other` is the parent: its other children are siblings
                for _, sib, k2, d2 in g.edges(other, keys=True, data=True):
                    if k2 == HIERARCHY and d2.get("source") == other:
                        out.add(sib)
    return out - searched


def graph_distance(g, sources: list[str], depth: int, min_coaccess: int) -> dict[str, int]:
    """Unweighted BFS distance from the sources over hierarchy edges and sufficiently observed co-access edges (<= depth)."""
    dist = {m: 0 for m in sources if m in g}
    q = deque(dist)
    while q:
        m = q.popleft()
        if dist[m] >= depth:
            continue
        for other, keys in g.adj[m].items():
            if other in dist:
                continue
            ok = any(k == HIERARCHY or (k == CO_ACCESS and int(d.get("co_access_count", 0)) >= min_coaccess) for k, d in keys.items())
            if ok:
                dist[other] = dist[m] + 1
                q.append(other)
    return dist


def _next_modules(policy: str, remaining: list[str], sim: dict[str, float], g, searched: set[str], step: int, bonus: float) -> list[str]:
    if policy == "profile":
        return remaining[:step]
    linked = _linked(g, searched)
    if policy == "graph":  # modules linked to what was already searched first (profile order inside each group), then the rest
        return ([m for m in remaining if m in linked] + [m for m in remaining if m not in linked])[:step]
    return sorted(remaining, key=lambda m: (-(sim[m] + (bonus if m in linked else 0.0)), m))[:step]  # blend


def _empty_global(engine, qvec, top_k: int, reason: str, mode: str):
    from .controller import RetrievalOutcome, _graph_total
    g = engine.graph_provider()
    total = _graph_total(g) if g.number_of_nodes() else engine.store.count_memories(None)
    hits = rank_hits(engine.store.search_memories(qvec, None, top_k), top_k)
    return RetrievalOutcome(mode, hits, candidate_count=total, guided_candidate_count=total, fallback_used=True, fallback_reason=reason)


# --------------------------------------------------------------------------------------- progressive graph expansion
def progressive(engine, qvec, top_k: int, *, seed_count: int | None = None, max_candidates: int | None = None,
                fallback: bool | None = None, fallback_k: int | None = None, explain: bool = False):
    from .controller import RetrievalOutcome, _graph_total
    s, store, pc = engine.s, engine.store, time.perf_counter
    g = engine.graph_provider()
    fallback = s.fallback_enabled if fallback is None else fallback
    stab_k = fallback_k or top_k
    seed_n = seed_count or s.progressive_seed_modules
    budget = s.progressive_max_modules if max_candidates is None else max_candidates  # 0 = no module cap
    content = _content_modules(g)
    total = _graph_total(g)
    if not content:
        return _empty_global(engine, qvec, top_k, "no module profiles exist, so there is nothing to route through", "progressive")

    t0 = pc()
    ranking = store.search_modules(qvec, len(content))
    order = [h.module_id for h in ranking]
    sim = {h.module_id: float(h.score) for h in ranking}
    t_seed = (pc() - t0) * 1000
    t_pick = t_search = t_rank = 0.0

    per = max(top_k, s.per_module_candidates)
    searched: list[str] = []
    seen: set[str] = set()
    hits: list[Hit] = []
    ranked: list[Hit] = []
    stages: list[dict[str, Any]] = []
    cand: list[Candidate] = []
    cum = 0
    prev: set[str] | None = None
    stable = 0
    stop = ""
    changed: bool | None = None
    batch = order[:seed_n]
    step_i = 0
    while True:
        step_i += 1
        if budget:
            batch = batch[: max(0, budget - len(searched))]
        if not batch:
            stop = "budget" if budget and len(searched) >= budget else "all modules searched"
            break
        ts = pc()
        new_hits = store.search_memories(qvec, batch, per)
        t_search += (pc() - ts) * 1000
        tr = pc()
        hits = hits + new_hits
        ranked = rank_hits(hits, top_k)
        t_rank += (pc() - tr) * 1000
        new_records = sum(_records(g, m) for m in batch)
        searched += batch
        seen.update(batch)
        cum += new_records
        topk = {h.memory_id for h in ranked[:stab_k]}
        if prev is None:
            changed = None
        else:
            changed = topk != prev
            stable = 0 if changed else stable + 1
        stages.append({
            "stage": step_i, "kind": "seed" if step_i == 1 else "expansion", "policy": "profile" if step_i == 1 else s.progressive_policy,
            "modules": [{"module_id": m, "name": _name(g, m), "profile_rank": order.index(m) + 1, "profile_score": round(sim[m], 4),
                         "records": _records(g, m)} for m in batch],
            "new_candidates": new_records, "cumulative_candidates": cum, "hits_returned": len(new_hits),
            "best_score": round(ranked[0].score, 4) if ranked else None,
            "kth_score": round(ranked[stab_k - 1].score, 4) if len(ranked) >= stab_k else None,
            "top_k_changed": changed, "new_in_top_k": None if prev is None else len(topk - prev), "search_ms": round((pc() - ts) * 1000, 3),
            "decision": "continue"})
        cand += [Candidate(m, "seed" if step_i == 1 else "expansion", None, sim[m], stage=step_i) for m in batch]
        prev = topk
        remaining = [m for m in order if m not in seen]
        if not remaining:
            stop = "all modules searched"
            break
        if step_i >= 2 and stable >= max(1, s.progressive_patience) and len(ranked) >= min(stab_k, total):
            stop = "stable"
            break
        if (budget and len(searched) >= budget) or step_i - 1 >= s.progressive_max_steps:
            stop = "budget"
            break
        tp = pc()
        batch = _next_modules(s.progressive_policy, remaining, sim, g, seen, s.progressive_step_modules, s.progressive_graph_bonus)
        t_pick += (pc() - tp) * 1000
    reasons = {"stable": f"the top-{stab_k} did not change for {max(1, s.progressive_patience)} expansion step(s)",
               "budget": "the search budget was reached", "all modules searched": "every module was searched"}
    stages[-1]["decision"] = f"stop: {reasons.get(stop, stop)}"

    out = RetrievalOutcome("progressive", ranked, [(m, sim[m]) for m in order[:seed_n]], cand, cum, cum, False, None, list(ranked),
                           {"seed": t_seed, "expand": t_pick, "search": t_search, "rank": t_rank, "fallback": 0.0})
    unsettled = stop == "budget" and stable < max(1, s.progressive_patience)
    reason = None
    if fallback and cum < total:
        reason = weak_reason(ranked, stab_k, total, s.fallback_min_score)
        if not reason and unsettled and s.progressive_fallback_on_budget:
            reason = f"search budget reached while the top-{stab_k} was still changing"
        if reason:
            t4 = pc()
            out.results = rank_hits(store.search_memories(qvec, None, top_k), top_k)
            out.fallback_used, out.fallback_reason = True, reason
            out.candidate_count = cum + total
            out.timings["fallback"] = (pc() - t4) * 1000
    out.stages = stages
    out.extra = {"progressive": {"stop_reason": stop, "stop_explanation": reasons.get(stop, stop), "stages_run": len(stages),
                                 "modules_searched": len(searched), "modules_total": len(content), "policy": s.progressive_policy,
                                 "stability_k": stab_k, "patience": max(1, s.progressive_patience), "fallback_on_budget": s.progressive_fallback_on_budget,
                                 "unsettled_at_stop": unsettled, "budget_modules": budget, "max_steps": s.progressive_max_steps}}
    return out


# ------------------------------------------------------------------------------------------------ soft graph routing
def soft(engine, qvec, top_k: int, *, seed_count: int | None = None, explain: bool = False, weight: float | None = None,
         graph_share: float | None = None, **_: Any):
    from .controller import RetrievalOutcome, _graph_total
    s, store, pc = engine.s, engine.store, time.perf_counter
    g = engine.graph_provider()
    lam = s.soft_weight if weight is None else weight
    gamma = s.soft_graph_share if graph_share is None else graph_share
    total = _graph_total(g) if g.number_of_nodes() else store.count_memories(None)
    content = _content_modules(g)
    pool_n = max(top_k, s.soft_candidate_pool)

    t0 = pc()
    pool = rank_hits(store.search_memories(qvec, None, pool_n), pool_n)  # dense order, ties by memory id
    t_search = (pc() - t0) * 1000

    t1 = pc()
    ranking = store.search_modules(qvec, len(content)) if content else []
    sim = {h.module_id: float(h.score) for h in ranking}
    rank_of = {h.module_id: i + 1 for i, h in enumerate(ranking)}
    lo, hi = (min(sim.values()), max(sim.values())) if sim else (0.0, 0.0)
    prof = {m: ((v - lo) / (hi - lo) if hi > lo else 0.5) for m, v in sim.items()}
    seeds = [h.module_id for h in ranking[: (seed_count or s.soft_seed_modules)]]
    dist = graph_distance(g, seeds, s.soft_graph_depth, s.coaccess_min_count)
    graph_c = {m: 1.0 / (1.0 + d) for m, d in dist.items() if d <= s.soft_graph_depth}
    prior: dict[str, float] = {m: (1 - gamma) * prof[m] + gamma * graph_c.get(m, 0.0) for m in prof}
    t_seed = (pc() - t1) * 1000

    t2 = pc()
    known = [prior[h.module_id] for h in pool if h.module_id in prior]
    neutral = (sum(known) / len(known)) if known else 0.0  # pool-mean prior: a candidate without module information is shifted by exactly 0
    scored = []
    for i, h in enumerate(pool):
        p = prior.get(h.module_id, neutral)
        final = h.score + lam * (p - neutral)
        scored.append((final, h, p, i + 1))
    scored.sort(key=lambda x: (-x[0], x[1].memory_id))
    results: list[Hit] = []
    detail: list[dict[str, Any]] = []
    promoted = demoted = same = 0
    for j, (final, h, p, before) in enumerate(scored[:top_k]):
        after = j + 1
        promoted += after < before
        demoted += after > before
        same += after == before
        payload = {**h.payload, "dense_score": h.score, "module_prior": round(p, 6), "soft_final_score": final}
        results.append(Hit(h.memory_id, h.module_id, final, payload))
        if explain:
            shift = lam * (p - neutral)
            detail.append({"memory_id": h.memory_id, "module_id": h.module_id, "module_name": _name(g, h.module_id),
                           "dense_score": round(h.score, 6), "dense_rank": before,
                           "profile_score": round(sim[h.module_id], 6) if h.module_id in sim else None,
                           "profile_rank": rank_of.get(h.module_id), "profile_component": round(prof[h.module_id], 4) if h.module_id in prof else None,
                           "graph_distance": dist.get(h.module_id), "graph_component": round(graph_c.get(h.module_id, 0.0), 4),
                           "prior": round(p, 4), "prior_centered": round(p - neutral, 4), "score_shift": round(shift, 6),
                           "final_score": round(final, 6), "final_rank": after,
                           "change": ("promoted" if after < before else "demoted" if after > before else "unchanged"),
                           "reason": (f"prior {p:.2f} vs pool mean {neutral:.2f}: shifted {shift:+.4f}, rank {before}→{after}" if after != before
                                      else "rank unchanged (prior shift too small to reorder neighbours)")})
    t_rank = (pc() - t2) * 1000
    dense_top = {h.memory_id for h in pool[:top_k]}
    out = RetrievalOutcome("soft", results, [(m, sim[m]) for m in seeds], [Candidate(m, "seed", None, sim[m]) for m in seeds], total, total,
                           False, None, results, {"seed": t_seed, "expand": 0.0, "search": t_search, "rank": t_rank, "fallback": 0.0})
    out.extra = {"soft": {"weight": lam, "graph_share": gamma, "pool_size": len(pool), "seed_modules": seeds, "graph_depth": s.soft_graph_depth,
                          "min_coaccess_observations": s.coaccess_min_count, "pool_mean_prior": round(neutral, 4),
                          "formula": "final = dense + weight * (prior - pool_mean_prior);  prior = (1-share)*profile + share*graph",
                          "promoted": promoted, "demoted": demoted, "unchanged": same,
                          "top_k_membership_changed": dense_top != {h.memory_id for h in results},
                          "full_global_search": True, "results": detail}}
    return out
