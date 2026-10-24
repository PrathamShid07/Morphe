"""Shortcut (co-access) edge planning with duplicate prevention and an edge budget."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..versioning.state import CO_ACCESS, ArchitectureState, EdgeSpec, edge_id_for


@dataclass
class EdgePlan:
    a: str
    b: str
    new_state: ArchitectureState
    evidence: dict[str, Any] = field(default_factory=dict)


def plan_shortcut(state: ArchitectureState, a: str, b: str, count: int, window_queries: int, settings,
                  operation_id: str, now: str, window_id: str | None = None) -> tuple[EdgePlan | None, dict[str, Any]]:
    ev: dict[str, Any] = {"modules": sorted((a, b)), "co_access_count": count, "window_queries": window_queries,
                          "window_id": window_id, "blockers": [],
                          "thresholds": {"min_count": settings.coaccess_min_count,
                                         "min_window_queries": settings.coaccess_min_window_queries,
                                         "max_edges_total": settings.max_edges_total,
                                         "max_coaccess_per_node": settings.max_coaccess_per_node}}
    blockers = ev["blockers"]
    if a == b:
        blockers.append("a module cannot be linked to itself")
    for m in (a, b):
        if not state.assignments.get(m):
            blockers.append(f"module {m} does not exist or has no records")
    if blockers:
        return None, ev
    if edge_id_for(CO_ACCESS, a, b) in state.edges:
        blockers.append("an equivalent shortcut edge already exists")
    if count < settings.coaccess_min_count:
        blockers.append(f"co-access count {count} < {settings.coaccess_min_count}")
    if window_queries < settings.coaccess_min_window_queries:
        blockers.append(f"only {window_queries} queries observed; need {settings.coaccess_min_window_queries}")
    edges = state.co_access_edges()
    if len(edges) >= settings.max_edges_total:
        blockers.append(f"edge budget reached ({len(edges)}/{settings.max_edges_total})")
    for m in (a, b):
        deg = sum(1 for e in edges if m in (e.source, e.target))
        if deg >= settings.max_coaccess_per_node:
            blockers.append(f"module {m} already has {deg} shortcut edges (limit {settings.max_coaccess_per_node})")
    if blockers:
        return None, ev
    new = state.copy()
    eid = edge_id_for(CO_ACCESS, a, b)
    s, t = sorted((a, b))
    weight = round(count / max(window_queries, 1), 4)
    ev.update({"weight": weight, "window_fraction": weight})
    new.edges[eid] = EdgeSpec(eid, s, t, CO_ACCESS, weight, count, now, operation_id, dict(ev))
    for m in (a, b):
        new.modules[m].last_change_op, new.modules[m].last_change_at = "shortcut", now
    return EdgePlan(a, b, new, ev), ev
