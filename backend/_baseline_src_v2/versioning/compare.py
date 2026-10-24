"""Structural diff between two architecture states (used by version comparison, restore preview, adaptation events)."""
from __future__ import annotations

from collections import Counter
from typing import Any

from .state import ArchitectureState


def diff_states(old: ArchitectureState, new: ArchitectureState, sample: int = 8) -> dict[str, Any]:
    old_mod, new_mod = set(old.modules), set(new.modules)
    added, removed = sorted(new_mod - old_mod), sorted(old_mod - new_mod)
    renamed = sorted(m for m in old_mod & new_mod if old.modules[m].name != new.modules[m].name)
    reparented = sorted(m for m in old_mod & new_mod if old.modules[m].parent_id != new.modules[m].parent_id)
    old_of, new_of = old.module_of(), new.module_of()
    moved = {mid: (old_of[mid], new_of[mid]) for mid in set(old_of) & set(new_of) if old_of[mid] != new_of[mid]}
    flows = Counter(moved.values())
    only_old, only_new = sorted(set(old_of) - set(new_of)), sorted(set(new_of) - set(old_of))
    e_old, e_new = set(old.edges), set(new.edges)
    changed_edges = sorted(e for e in e_old & e_new
                           if (old.edges[e].co_access_count, round(old.edges[e].weight, 4)) !=
                           (new.edges[e].co_access_count, round(new.edges[e].weight, 4)))
    return {
        "modules_added": [{"module_id": m, "name": new.modules[m].name, "records": new.record_count(m)} for m in added],
        "modules_removed": [{"module_id": m, "name": old.modules[m].name, "records": old.record_count(m)} for m in removed],
        "modules_renamed": renamed, "modules_reparented": reparented,
        "memories_moved": len(moved),
        "memory_flows": [{"from": a, "to": b, "count": n} for (a, b), n in flows.most_common(sample)],
        "memories_only_in_old": len(only_old), "memories_only_in_new": len(only_new),
        "edges_added": [{"edge_id": e, "relation_type": new.edges[e].relation_type, "source": new.edges[e].source,
                         "target": new.edges[e].target} for e in sorted(e_new - e_old)],
        "edges_removed": [{"edge_id": e, "relation_type": old.edges[e].relation_type, "source": old.edges[e].source,
                           "target": old.edges[e].target} for e in sorted(e_old - e_new)],
        "edges_changed": changed_edges,
        "counts": {"old": {"modules": len(old.modules), "edges": len(old.edges), "memories": old.total_assigned()},
                   "new": {"modules": len(new.modules), "edges": len(new.edges), "memories": new.total_assigned()}},
        "identical": not (added or removed or renamed or reparented or moved or only_old or only_new or
                          e_old != e_new or changed_edges),
    }


def summarize_diff(d: dict[str, Any]) -> str:
    parts = []
    if d["modules_added"]:
        parts.append(f"+{len(d['modules_added'])} module(s): " + ", ".join(m["name"] for m in d["modules_added"][:4]))
    if d["modules_removed"]:
        parts.append(f"-{len(d['modules_removed'])} module(s): " + ", ".join(m["name"] for m in d["modules_removed"][:4]))
    if d["memories_moved"]:
        parts.append(f"{d['memories_moved']} memories reassigned")
    if d["edges_added"]:
        parts.append(f"+{len(d['edges_added'])} edge(s)")
    if d["edges_removed"]:
        parts.append(f"-{len(d['edges_removed'])} edge(s)")
    return "; ".join(parts) or "no structural change"
