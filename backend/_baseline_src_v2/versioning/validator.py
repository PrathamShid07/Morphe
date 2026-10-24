"""Cross-store integrity checks: Qdrant vs MongoDB vs the live NetworkX graph."""
from __future__ import annotations

from typing import Any

from ..graph.manager import GraphManager
from ..graph.persistence import ArchitecturePersistence
from ..storage.mongo_store import MongoStore
from ..storage.qdrant_store import QdrantStore


class LiveValidator:
    def __init__(self, qdrant: QdrantStore, mongo: MongoStore, graph: GraphManager,
                 persistence: ArchitecturePersistence):
        self.qdrant = qdrant
        self.mongo = mongo
        self.graph = graph
        self.persistence = persistence

    def check(self) -> dict[str, Any]:
        problems: list[str] = []
        q = {mid: p for mid, p, _ in self.qdrant.scroll_memories(with_vectors=False)}
        m = {d["memory_id"]: d for d in self.mongo.active_memory_docs()}
        if set(q) != set(m):
            only_q, only_m = sorted(set(q) - set(m)), sorted(set(m) - set(q))
            problems.append(f"active memories differ between Qdrant and MongoDB "
                            f"(only in Qdrant: {len(only_q)} {only_q[:2]}, only in MongoDB: {len(only_m)} {only_m[:2]})")
        for mid in set(q) & set(m):
            if q[mid].get("module_id") != m[mid].get("module_id"):
                problems.append(f"memory {mid} is in module {q[mid].get('module_id')} in Qdrant but "
                                f"{m[mid].get('module_id')} in MongoDB")
                if len(problems) > 20:
                    break
        pending = self.qdrant.count_memories(None, status="pending")
        if pending or self.mongo.col("memory_metadata").count_documents({"status": "pending"}):
            problems.append("records are stuck in status 'pending'")
        state = self.persistence.export_live_state()
        problems += state.validate(self.qdrant.dimension)
        profiles = set(self.qdrant.list_module_profiles(with_vectors=False))
        content = set(state.content_modules())
        if profiles != content:
            problems.append(f"module routing profiles do not match content modules "
                            f"(missing {sorted(content - profiles)[:3]}, extra {sorted(profiles - content)[:3]})")
        g = self.graph.graph
        if set(g.nodes) != set(state.modules):
            problems.append("live graph nodes differ from the persisted modules")
        if g.number_of_edges() != len(state.edges):
            problems.append(f"live graph has {g.number_of_edges()} edges but {len(state.edges)} are persisted")
        for n, d in g.nodes(data=True):
            if d.get("active_record_count", 0) != state.record_count(n):
                problems.append(f"graph node {n} reports {d.get('active_record_count')} records, "
                                f"stores have {state.record_count(n)}")
                break
        tq = sum(int(p.get("token_count", 0)) for p in q.values())
        tm = sum(int(d.get("token_count", 0)) for d in m.values())
        if tq != tm:
            problems.append(f"token totals differ (Qdrant {tq}, MongoDB {tm})")
        return {"passed": not problems, "problems": problems,
                "stats": {"active_memories": len(q), "modules": len(state.modules),
                          "content_modules": len(content), "edges": len(state.edges), "active_tokens": tm}}
