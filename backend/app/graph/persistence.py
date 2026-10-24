"""Durable architecture persistence: export the live architecture from the stores, and converge the stores to a
target ArchitectureState.

`converge_to` is *declarative and idempotent*: it computes the difference between each store and the target and
fixes only what differs, so re-running it after a partial failure converges instead of duplicating work. Structural
adaptation, post-failure recovery, and version restoration all use this single routine.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from ..common import IntegrityError, get_logger, iso, log_event, sha256_hex
from ..storage.mongo_store import MongoStore
from ..storage.qdrant_store import QdrantStore
from ..versioning.state import ArchitectureState, EdgeSpec, ModuleSpec
from .manager import GraphManager, graph_from_parts

log = get_logger("graph.persistence")


def module_spec_from_doc(d: dict[str, Any], vector: np.ndarray | None) -> ModuleSpec:
    return ModuleSpec(
        module_id=d["module_id"], name=d.get("name", d["module_id"]), parent_id=d.get("parent_id"),
        description=d.get("description", ""), keywords=list(d.get("keywords", [])),
        profile_vector=None if vector is None else [float(x) for x in vector], created_at=d.get("created_at"),
        created_by_op=d.get("created_by_op"), last_change_op=d.get("last_change_op"),
        last_change_at=d.get("last_change_at"))


def edge_spec_from_doc(d: dict[str, Any]) -> EdgeSpec:
    return EdgeSpec(edge_id=d["edge_id"], source=d["source"], target=d["target"], relation_type=d["relation_type"],
                    weight=float(d.get("weight", 1.0)), co_access_count=int(d.get("co_access_count", 0)),
                    created_at=d.get("created_at"), operation_id=d.get("operation_id"),
                    evidence=d.get("evidence", {}))


class ArchitecturePersistence:
    def __init__(self, qdrant: QdrantStore, mongo: MongoStore, graph: GraphManager):
        self.qdrant = qdrant
        self.mongo = mongo
        self.graph = graph

    # ---------------------------------------------------------------- export
    def export_live_state(self) -> ArchitectureState:
        profiles = self.qdrant.list_module_profiles(with_vectors=True)
        state = ArchitectureState()
        for d in self.mongo.list_modules():
            vec = profiles.get(d["module_id"], (None, None))[1]
            state.modules[d["module_id"]] = module_spec_from_doc(d, vec)
        for d in self.mongo.active_memory_docs({"memory_id": 1, "module_id": 1}):
            state.assignments.setdefault(d["module_id"], []).append(d["memory_id"])
        for d in self.mongo.list_edges():
            state.edges[d["edge_id"]] = edge_spec_from_doc(d)
        for ids in state.assignments.values():
            ids.sort()
        return state

    def reload_graph(self) -> None:
        modules = {d["module_id"]: module_spec_from_doc(d, None) for d in self.mongo.list_modules()}
        edges = [edge_spec_from_doc(d) for d in self.mongo.list_edges()]
        self.graph.replace(graph_from_parts(modules, edges, self.mongo.module_active_counts()))

    # -------------------------------------------------------------- converge
    def converge_to(self, target: ArchitectureState, *, operation_id: str | None = None) -> dict[str, Any]:
        problems = target.validate(self.qdrant.dimension)
        if problems:
            raise IntegrityError("target architecture failed integrity validation: " + "; ".join(problems[:5]),
                                 details=problems)
        want = target.module_of()
        q_live = {mid: p.get("module_id") for mid, p, _ in self.qdrant.scroll_memories(with_vectors=False)}
        m_live = {d["memory_id"]: d["module_id"] for d in self.mongo.active_memory_docs({"memory_id": 1, "module_id": 1})}
        unknown = (set(q_live) | set(m_live)) - set(want)
        if unknown:
            raise IntegrityError(f"{len(unknown)} active memories have no module in the target architecture "
                                 f"(e.g. {sorted(unknown)[:3]})")
        ghosts = set(want) - set(q_live)
        if ghosts:
            raise IntegrityError(f"target assigns {len(ghosts)} memories that are not active in the vector store "
                                 f"(e.g. {sorted(ghosts)[:3]})")
        now = iso()
        content = target.content_modules()

        # 1. routing profiles first (additive), so no query can route to a module without a profile
        self.qdrant.upsert_module_profiles([
            (m, target.modules[m].profile_vector, {  # type: ignore[arg-type]
                "display_name": target.modules[m].name, "description": target.modules[m].description,
                "parent_id": target.modules[m].parent_id, "active_record_count": target.record_count(m),
                "updated_at": now}) for m in content])

        # 2. vector-store payload assignments (batched per destination module)
        moves_q: dict[str, list[str]] = defaultdict(list)
        for mid, mod in want.items():
            if q_live.get(mid) != mod:
                moves_q[mod].append(mid)
        for mod, ids in moves_q.items():
            self.qdrant.set_module(ids, mod)

        # 3. durable metadata
        existing = {d["module_id"]: d for d in self.mongo.list_modules(include_retired=True)}
        docs: list[dict[str, Any]] = []
        for m, spec in target.modules.items():
            vec = spec.profile_vector
            docs.append({
                "module_id": m, "name": spec.name, "parent_id": spec.parent_id, "description": spec.description,
                "keywords": spec.keywords, "created_at": spec.created_at or existing.get(m, {}).get("created_at") or now,
                "created_by_op": spec.created_by_op, "last_change_op": spec.last_change_op,
                "last_change_at": spec.last_change_at, "status": "active", "retired_at": None, "merged_into": None,
                "active_record_count": target.record_count(m), "updated_at": now,
                "profile_version": sha256_hex(",".join(f"{x:.4f}" for x in vec))[:10] if vec else None})
        retired = [m for m, d in existing.items() if d.get("status") == "active" and m not in target.modules]
        for m in retired:
            docs.append({"module_id": m, "status": "retired", "retired_at": now, "active_record_count": 0,
                         "retired_by_op": operation_id, "updated_at": now})
        self.mongo.upsert_modules(docs)
        moves_m: dict[str, list[str]] = defaultdict(list)
        for mid, mod in want.items():
            if m_live.get(mid) != mod:
                moves_m[mod].append(mid)
        for mod, ids in moves_m.items():
            self.mongo.update_memory_metadata(ids, {"module_id": mod})
        self.mongo.replace_edges([{**e.__dict__} for e in target.edges.values()])

        # 4. drop routing profiles that no longer correspond to a content module
        stale = [m for m in self.qdrant.list_module_profiles(with_vectors=False) if m not in set(content)]
        self.qdrant.delete_module_profiles(stale)

        # 5. swap the live graph last
        self.graph.replace(graph_from_parts(target.modules, target.edges.values(),
                                            {m: len(v) for m, v in target.assignments.items()}))
        summary = {"memories_moved": sum(len(v) for v in moves_q.values()), "modules": len(target.modules),
                   "content_modules": len(content), "edges": len(target.edges), "modules_retired": len(retired),
                   "profiles_removed": len(stale)}
        log_event(log, "converged_to_target", **summary)
        return summary
