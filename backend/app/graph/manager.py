"""Live module graph (NetworkX MultiGraph). Nodes are modules; edges are typed (hierarchy / co_access).

The graph is never the only copy of architecture state: it is rebuilt from durable Mongo module/edge documents
(see persistence.py) at startup and swapped atomically after every committed mutation.
"""
from __future__ import annotations

import threading
from typing import Any, Iterable, Mapping

import networkx as nx

from ..versioning.state import HIERARCHY, ArchitectureState, EdgeSpec, ModuleSpec


def graph_from_parts(modules: Mapping[str, ModuleSpec], edges: Iterable[EdgeSpec],
                     counts: Mapping[str, int]) -> nx.MultiGraph:
    g = nx.MultiGraph()
    for mid, m in modules.items():
        g.add_node(mid, name=m.name, parent_id=m.parent_id, description=m.description, keywords=list(m.keywords),
                   active_record_count=int(counts.get(mid, 0)), created_at=m.created_at,
                   last_change_op=m.last_change_op, last_change_at=m.last_change_at)
    for e in edges:
        if e.source in g and e.target in g:
            g.add_edge(e.source, e.target, key=e.relation_type, edge_id=e.edge_id, relation_type=e.relation_type,
                       weight=e.weight, co_access_count=e.co_access_count, created_at=e.created_at,
                       source=e.source, target=e.target, evidence=e.evidence, operation_id=e.operation_id)
    return g


def graph_from_state(state: ArchitectureState) -> nx.MultiGraph:
    return graph_from_parts(state.modules, state.edges.values(), {m: len(v) for m, v in state.assignments.items()})


class GraphManager:
    """Holds the live graph; readers take the reference once, writers swap in a freshly built graph."""

    def __init__(self) -> None:
        self._graph: nx.MultiGraph = nx.MultiGraph()
        self._lock = threading.Lock()

    @property
    def graph(self) -> nx.MultiGraph:
        return self._graph

    def replace(self, g: nx.MultiGraph) -> None:
        with self._lock:
            self._graph = g

    def replace_from_state(self, state: ArchitectureState) -> None:
        self.replace(graph_from_state(state))

    def counts(self) -> tuple[int, int]:
        g = self._graph
        return g.number_of_nodes(), g.number_of_edges()

    def neighbors(self, module_id: str) -> list[dict[str, Any]]:
        g = self._graph
        if module_id not in g:
            return []
        out = []
        for _, other, key, data in g.edges(module_id, keys=True, data=True):
            out.append({"module_id": other, "relation_type": key, "weight": data.get("weight", 1.0),
                        "co_access_count": data.get("co_access_count", 0),
                        "direction": ("parent" if key == HIERARCHY and data.get("target") == module_id else
                                      "child" if key == HIERARCHY else "shortcut")})
        return sorted(out, key=lambda x: (x["relation_type"], -x["weight"], x["module_id"]))

    def export(self, with_layout: bool = True) -> dict[str, Any]:
        g = self._graph
        positions = compute_layout(g) if with_layout else {}
        nodes = []
        for mid, d in sorted(g.nodes(data=True)):
            nodes.append({"id": mid, "type": "moduleNode", "position": positions.get(mid, {"x": 0, "y": 0}),
                          "data": {"label": d.get("name", mid), "recordCount": d.get("active_record_count", 0),
                                   "parentId": d.get("parent_id"), "isContainer": d.get("active_record_count", 0) == 0,
                                   "lastChangeOp": d.get("last_change_op"), "lastChangeAt": d.get("last_change_at")}})
        edges = []
        for u, v, key, d in sorted(g.edges(keys=True, data=True), key=lambda x: x[3].get("edge_id", "")):
            edges.append({"id": d["edge_id"], "source": d.get("source", u), "target": d.get("target", v),
                          "label": key, "type": "default",
                          "data": {"relationType": key, "weight": d.get("weight", 1.0),
                                   "coAccessCount": d.get("co_access_count", 0), "createdAt": d.get("created_at"),
                                   "evidence": d.get("evidence") or {}, "operationId": d.get("operation_id")}})
        return {"nodes": nodes, "edges": edges}


SLOT_W, ROW_H, MAX_ROW_W = 200.0, 130.0, 1700.0


def compute_layout(g: nx.MultiGraph) -> dict[str, dict[str, float]]:
    """Deterministic hierarchical layout: each hierarchy tree is a cluster with parents centred over their children;
    clusters are packed left-to-right and wrapped into rows. Co-access shortcuts are drawn across clusters."""
    children: dict[str, list[str]] = {n: [] for n in g.nodes}
    has_parent: set[str] = set()
    for u, v, key, d in g.edges(keys=True, data=True):
        if key == HIERARCHY:
            parent, child = d.get("source", u), d.get("target", v)
            if parent in children and child in children and child not in has_parent and child != parent:
                children[parent].append(child)
                has_parent.add(child)
    for k in children:
        children[k].sort(key=lambda m: (g.nodes[m].get("name", m).lower(), m))

    def leaves(n: str, seen: frozenset[str] = frozenset()) -> int:
        kids = [c for c in children[n] if c not in seen]
        return max(1, sum(leaves(c, seen | {n}) for c in kids))

    def depth(n: str, seen: frozenset[str] = frozenset()) -> int:
        kids = [c for c in children[n] if c not in seen]
        return 1 + max((depth(c, seen | {n}) for c in kids), default=0)

    roots = sorted((n for n in g.nodes if n not in has_parent), key=lambda m: (-leaves(m), g.nodes[m].get("name", m).lower(), m))
    pos: dict[str, dict[str, float]] = {}

    def place(n: str, left: float, top: float, seen: frozenset[str] = frozenset()) -> float:
        """Place subtree with its left edge at `left`; returns the x-centre of n."""
        kids = [c for c in children[n] if c not in seen]
        if not kids:
            pos[n] = {"x": left + SLOT_W / 2, "y": top}
            return left + SLOT_W / 2
        x, centres = left, []
        for c in kids:
            centres.append(place(c, x, top + ROW_H, seen | {n}))
            x += leaves(c, seen | {n}) * SLOT_W
        pos[n] = {"x": (centres[0] + centres[-1]) / 2, "y": top}
        return pos[n]["x"]

    x, y, row_depth = 0.0, 0.0, 1
    for r in roots:
        w = leaves(r) * SLOT_W
        if x > 0 and x + w > MAX_ROW_W:
            x, y, row_depth = 0.0, y + row_depth * ROW_H + 60, 1
        place(r, x, y)
        x += w + 40
        row_depth = max(row_depth, depth(r))
    return {n: {"x": round(p["x"] - SLOT_W / 2, 1), "y": round(p["y"], 1)} for n, p in pos.items()}
