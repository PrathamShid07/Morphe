"""ArchitectureState: a pure, serializable description of the memory architecture.

It holds module metadata + routing profile vectors, memory->module assignments and typed graph edges. Candidate
architectures, snapshots, restores and sandbox evaluations all operate on this one structure, so "what we evaluated"
and "what we applied" are the same object.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from ..common import canonical_json, sha256_hex

HIERARCHY = "hierarchy"
CO_ACCESS = "co_access"


def edge_id_for(relation: str, a: str, b: str) -> str:
    if relation == CO_ACCESS:
        a, b = sorted((a, b))
    return f"edge_{relation}_{a}__{b}"


@dataclass
class ModuleSpec:
    module_id: str
    name: str
    parent_id: str | None = None
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    profile_vector: list[float] | None = None
    created_at: str | None = None
    created_by_op: str | None = None
    last_change_op: str | None = None
    last_change_at: str | None = None


@dataclass
class EdgeSpec:
    edge_id: str
    source: str
    target: str
    relation_type: str
    weight: float = 1.0
    co_access_count: int = 0
    created_at: str | None = None
    operation_id: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class ArchitectureState:
    modules: dict[str, ModuleSpec] = field(default_factory=dict)
    assignments: dict[str, list[str]] = field(default_factory=dict)  # module_id -> memory_ids (active only)
    edges: dict[str, EdgeSpec] = field(default_factory=dict)

    # ---------------------------------------------------------- derived views
    def copy(self) -> "ArchitectureState":
        return copy.deepcopy(self)

    def module_of(self) -> dict[str, str]:
        return {mid: m for m, ids in self.assignments.items() for mid in ids}

    def record_count(self, module_id: str) -> int:
        return len(self.assignments.get(module_id, []))

    def content_modules(self) -> list[str]:
        return sorted(m for m in self.modules if self.assignments.get(m))

    def children_of(self, module_id: str) -> list[str]:
        return sorted(m for m, spec in self.modules.items() if spec.parent_id == module_id)

    def ancestors(self, module_id: str) -> list[str]:
        out, cur, seen = [], self.modules.get(module_id), set()
        while cur and cur.parent_id and cur.parent_id not in seen:
            seen.add(cur.parent_id)
            out.append(cur.parent_id)
            cur = self.modules.get(cur.parent_id)
        return out

    def co_access_edges(self) -> list[EdgeSpec]:
        return [e for e in self.edges.values() if e.relation_type == CO_ACCESS]

    def total_assigned(self) -> int:
        return sum(len(v) for v in self.assignments.values())

    # ---------------------------------------------------------- serialization
    def to_dict(self) -> dict[str, Any]:
        return {
            "modules": {k: asdict(v) for k, v in sorted(self.modules.items())},
            "assignments": {k: sorted(v) for k, v in sorted(self.assignments.items()) if v},
            "edges": {k: asdict(v) for k, v in sorted(self.edges.items())},
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ArchitectureState":
        return cls(
            modules={k: ModuleSpec(**v) for k, v in d.get("modules", {}).items()},
            assignments={k: list(v) for k, v in d.get("assignments", {}).items()},
            edges={k: EdgeSpec(**v) for k, v in d.get("edges", {}).items()},
        )

    def checksum(self) -> str:
        """Structure-defining content only (names, parents, assignments, edges, rounded profile vectors)."""
        core = {
            "modules": {
                k: {"name": v.name, "parent": v.parent_id,
                    "vec": None if v.profile_vector is None else [round(float(x), 4) for x in v.profile_vector]}
                for k, v in sorted(self.modules.items())},
            "assignments": {k: sorted(v) for k, v in sorted(self.assignments.items()) if v},
            "edges": {k: [e.relation_type, e.source, e.target, e.co_access_count, round(e.weight, 4)]
                      for k, e in sorted(self.edges.items())},
        }
        return sha256_hex(canonical_json(core))

    # -------------------------------------------------------------- integrity
    def validate(self, dimension: int | None = None) -> list[str]:
        """Return a list of integrity problems (empty means valid)."""
        problems: list[str] = []
        seen: dict[str, str] = {}
        for mod, ids in self.assignments.items():
            if mod not in self.modules:
                problems.append(f"assignment references unknown module {mod}")
            for mid in ids:
                if mid in seen:
                    problems.append(f"memory {mid} assigned to both {seen[mid]} and {mod}")
                seen[mid] = mod
        for mod, spec in self.modules.items():
            if spec.parent_id is not None and spec.parent_id not in self.modules:
                problems.append(f"module {mod} has unknown parent {spec.parent_id}")
            if spec.parent_id == mod:
                problems.append(f"module {mod} is its own parent")
            if self.assignments.get(mod):
                v = spec.profile_vector
                if v is None:
                    problems.append(f"content module {mod} has no profile vector")
                elif dimension is not None and len(v) != dimension:
                    problems.append(f"module {mod} profile has dimension {len(v)}, expected {dimension}")
                elif not np.isfinite(np.asarray(v, dtype=np.float64)).all():
                    problems.append(f"module {mod} profile vector is not finite")
        for e in self.edges.values():
            if e.source not in self.modules or e.target not in self.modules:
                problems.append(f"edge {e.edge_id} references a missing module")
            if e.source == e.target:
                problems.append(f"edge {e.edge_id} is a self loop")
            if e.edge_id != edge_id_for(e.relation_type, e.source, e.target):
                problems.append(f"edge {e.edge_id} has an inconsistent id")
            if e.relation_type not in (HIERARCHY, CO_ACCESS):
                problems.append(f"edge {e.edge_id} has unknown relation {e.relation_type}")
        # parent cycles
        for m in self.modules:
            if m in self.ancestors(m):
                problems.append(f"module {m} is part of a parent cycle")
        return problems


def hierarchy_edges_from_parents(state: ArchitectureState, created_at: str | None = None,
                                 operation_id: str | None = None) -> None:
    """Ensure every parent_id has exactly one matching hierarchy edge (and no stale ones)."""
    wanted = {edge_id_for(HIERARCHY, s.parent_id, m): (s.parent_id, m)
              for m, s in state.modules.items() if s.parent_id}
    for eid in [e.edge_id for e in state.edges.values() if e.relation_type == HIERARCHY and e.edge_id not in wanted]:
        del state.edges[eid]
    for eid, (p, c) in wanted.items():
        if eid not in state.edges:
            state.edges[eid] = EdgeSpec(eid, p, c, HIERARCHY, 1.0, 0, created_at, operation_id, {"kind": "parent_child"})


def centroid(vectors: list[np.ndarray] | np.ndarray) -> list[float]:
    arr = np.asarray(vectors, dtype=np.float64)
    c = arr.mean(axis=0)
    n = np.linalg.norm(c)
    return (c / n if n > 0 else c).astype(np.float32).tolist()


def cosine(a: np.ndarray | list[float], b: np.ndarray | list[float]) -> float:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na > 0 and nb > 0 else 0.0
