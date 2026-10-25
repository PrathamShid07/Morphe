"""Authorization-scoped views of the memory for ONE user: a store and a graph the retrieval strategies can run on unchanged.

`RetrievalEngine` only ever talks to `store.search_memories / search_modules / count_memories` and `graph_provider()`. Handing it a
`ScopedStore` and the scope's graph therefore scopes every strategy -- global dense, fixed graph, progressive expansion, soft routing --
including their global fallbacks, without touching the algorithms. Nothing here can reach another user's points or modules:

* personal plane: `UserVectorStore` always filters on the owner;
* shared plane: only records whose `visibility == "shared"` in modules that actually hold such records.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import networkx as nx

from ..storage.qdrant_store import ACTIVE, Hit, ModuleHit, QdrantStore
from ..storage.user_plane import UserVectorStore

SHARED = "shared"


@dataclass
class Scope:
    owner: str
    personal_modules: set[str]
    shared_modules: set[str]            # shared modules that hold >= 1 shared active record (the only ones searchable)
    graph: nx.MultiGraph = field(default_factory=nx.MultiGraph)
    personal_records: int = 0
    shared_records: int = 0


class ScopedStore:
    """Drop-in for the parts of QdrantStore the retrieval engine uses, restricted to one scope."""

    def __init__(self, shared: QdrantStore, personal: UserVectorStore, scope: Scope):
        self.shared, self.personal, self.scope = shared, personal, scope

    def _split(self, module_ids: Sequence[str] | None) -> tuple[list[str] | None, list[str]]:
        sc = self.scope
        if module_ids is None:  # "everything" means everything IN SCOPE: the owner's own modules and the published shared ones
            return None, sorted(sc.shared_modules)
        return [m for m in module_ids if m in sc.personal_modules], [m for m in module_ids if m in sc.shared_modules]

    def search_memories(self, vector: Sequence[float], module_ids: Sequence[str] | None, limit: int, status: str | None = ACTIVE) -> list[Hit]:
        p_ids, s_ids = self._split(module_ids)
        hits: list[Hit] = []
        if p_ids is None or p_ids:
            hits += self.personal.search_memories(self.scope.owner, vector, p_ids, limit, status)
        if s_ids:
            hits += self.shared.search_memories(vector, s_ids, limit, status, visibility=SHARED)
        return sorted(hits, key=lambda h: (-h.score, h.memory_id))[:limit]

    def search_modules(self, vector: Sequence[float], limit: int) -> list[ModuleHit]:
        hits = self.personal.search_modules(self.scope.owner, vector, limit)
        if self.scope.shared_modules:
            hits += self.shared.search_modules(vector, limit, module_ids=sorted(self.scope.shared_modules))
        return sorted(hits, key=lambda h: (-h.score, h.module_id))[:limit]

    def count_memories(self, module_ids: Sequence[str] | None, status: str | None = ACTIVE) -> int:
        p_ids, s_ids = self._split(module_ids)
        n = self.personal.count_memories(self.scope.owner, p_ids, status) if (p_ids is None or p_ids) else 0
        if s_ids:
            n += self.shared.count_memories(s_ids, status, visibility=SHARED)
        return n


def shared_visibility_counts(mongo) -> tuple[dict[str, int], dict[str, int]]:
    """(shared active records per module, not-shared active records per module) on the shared plane."""
    col = mongo.col("memory_metadata")

    def agg(match: dict[str, Any]) -> dict[str, int]:
        return {r["_id"]: r["n"] for r in col.aggregate([{"$match": {"status": "active", **match}}, {"$group": {"_id": "$module_id", "n": {"$sum": 1}}}])}

    return agg({"visibility": SHARED}), agg({"visibility": {"$ne": SHARED}})


def build_scope(owner: str, personal_graph: nx.MultiGraph, shared_graph: nx.MultiGraph, mongo, personal_records: int) -> Scope:
    """The graph a user may traverse: their own modules, plus the shared modules that hold published records (and the containers above them)."""
    shared_n, hidden_n = shared_visibility_counts(mongo)
    g = nx.MultiGraph()
    g.add_nodes_from(personal_graph.nodes(data=True))
    for u, v, k, d in personal_graph.edges(keys=True, data=True):
        g.add_edge(u, v, key=k, **d)
    included = {m for m in shared_n if m in shared_graph}
    for m in list(included):  # keep the group containers that lead to a shared module so the hierarchy still reads correctly
        cur, seen = shared_graph.nodes[m].get("parent_id"), set()
        while cur and cur in shared_graph and cur not in seen:
            seen.add(cur)
            included.add(cur)
            cur = shared_graph.nodes[cur].get("parent_id")
    for m in included:
        a = dict(shared_graph.nodes[m])
        mixed = hidden_n.get(m, 0) > 0  # a module that also holds unpublished records must not leak what they are about
        if mixed:
            a["keywords"], a["description"] = [], ""
        a.update(active_record_count=shared_n.get(m, 0), scope="shared", created_at=None, last_change_op=None, last_change_at=None)
        g.add_node(m, **a)
    for u, v, k, d in shared_graph.edges(keys=True, data=True):
        if u in included and v in included:
            g.add_edge(u, v, key=k, **{**d, "created_at": None, "evidence": {}, "operation_id": None})
    return Scope(owner=owner, personal_modules=set(personal_graph.nodes), shared_modules=set(shared_n) & included, graph=g,
                 personal_records=personal_records, shared_records=sum(shared_n.values()))
