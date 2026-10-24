"""Startup reconciliation: finish or undo interrupted operations and repair safe cross-store inconsistencies.

Rules (safe repairs only; anything else is reported, never silently "fixed"):
* structural operations journaled as `applying` (or flagged needs_recovery) are rolled back to their saved checkpoint;
* `pending` records (interrupted ingestion / replacement) are removed — they were never retrievable;
* a verified active replacement whose originals are still active completes its retirement (roll forward);
* module counts/profiles are recomputed from active records; dangling edges are dropped; the graph is rebuilt from MongoDB.
"""
from __future__ import annotations

from typing import Any

from .common import get_logger, iso, log_event
from .memory.models import ACTIVE, DELETED, PENDING, SUPERSEDED

log = get_logger("reconcile")


def reconcile_startup(ctx) -> dict[str, Any]:
    q, m = ctx.qdrant, ctx.mongo
    report: dict[str, Any] = {"at": iso(), "actions": []}

    def act(msg: str) -> None:
        report["actions"].append(msg)
        log_event(log, "reconcile_action", action=msg)

    # 1. interrupted operations
    needs = m.get_state("needs_recovery")
    interrupted = m.open_operations()
    for op in interrupted:
        m.col("operations").update_one({"operation_id": op["operation_id"]},
                                       {"$set": {"status": "failed", "error": "interrupted by restart",
                                                 "finished_at": iso()}})
        ev = ctx.events.get_adaptation(op["operation_id"])
        if ev and ev.get("status") in ("running",):
            ctx.events.update_adaptation(op["operation_id"], "interrupted by restart", status="failed",
                                         error="interrupted by restart", finished_at=iso())
        before = op.get("before_version") or ((op.get("meta") or {}).get("before_version"))
        if op.get("phase") == "applying" and before and not needs:
            needs = {"operation_id": op["operation_id"], "before_version": before}
        act(f"marked interrupted operation {op['operation_id']} ({op.get('kind')}) as failed")
    if needs:
        try:
            ctx.lifecycle.recover(needs["before_version"])
            m.set_state("needs_recovery", None)
            act(f"recovered architecture to {needs['before_version']} after interrupted {needs['operation_id']}")
        except Exception as exc:
            report["recovery_error"] = f"{type(exc).__name__}: {exc}"[:300]
            act("recovery to the checkpoint FAILED; manual restore required")

    # 2. pending records
    pend_q = [mid for mid, p, _ in q.scroll_memories(status=PENDING)]
    pend_m = [d["memory_id"] for d in m.col("memory_metadata").find({"status": PENDING}, {"memory_id": 1})]
    pend = sorted(set(pend_q) | set(pend_m))
    if pend:
        q.delete_memories(pend)
        m.col("memory_metadata").delete_many({"memory_id": {"$in": pend}})
        act(f"removed {len(pend)} pending (never active) records")

    # 3. Qdrant is the source of truth for active vectors/text: align MongoDB lifecycle metadata with it
    q_active = {mid: p for mid, p, _ in q.scroll_memories()}
    m_docs = {d["memory_id"]: d for d in m.col("memory_metadata").find({"status": ACTIVE})}
    for mid in sorted(set(m_docs) - set(q_active)):
        m.update_memory_metadata([mid], {"status": DELETED, "deleted_at": iso(), "note": "vector missing at startup"})
        act(f"marked {mid} deleted: active in MongoDB but absent from Qdrant")
    for mid in sorted(set(q_active) - set(m_docs)):
        p = q_active[mid]
        doc = {k: v for k, v in p.items() if k != "text"} | {"text_preview": p.get("text", "")[:240], "access_count": 0,
                                                              "last_accessed_at": None}
        m.upsert_memory_metadata([doc])
        act(f"rebuilt MongoDB metadata for {mid}")
    for mid in sorted(set(q_active) & set(m_docs)):
        if q_active[mid].get("module_id") != m_docs[mid].get("module_id"):
            m.update_memory_metadata([mid], {"module_id": q_active[mid]["module_id"]})
            act(f"aligned module assignment of {mid} to the vector store")

    # 4. roll forward half-finished replacements
    for mid, p in q_active.items():
        srcs = p.get("summary_of") or []
        if not srcs:
            continue
        live_srcs = [s for s in srcs if s in q_active]
        if live_srcs:
            q.set_status(live_srcs, SUPERSEDED, {"superseded_by": mid})
            m.update_memory_metadata(live_srcs, {"status": SUPERSEDED, "superseded_by": mid, "retired_at": iso()})
            act(f"completed retirement of {len(live_srcs)} originals replaced by {mid}")

    # 5. modules: counts, profiles, dangling edges
    active_modules = {d["module_id"] for d in m.list_modules()}
    used = {d["module_id"] for d in m.col("memory_metadata").find({"status": ACTIVE}, {"module_id": 1})}
    for mod in sorted(used - active_modules):
        m.upsert_modules([{"module_id": mod, "name": mod, "status": "active", "parent_id": None, "created_at": iso(),
                           "keywords": [], "description": "recreated during reconciliation"}])
        act(f"recreated missing module record {mod}")
    ctx.modules.refresh_profiles(sorted(m.module_active_counts()) + [d["module_id"] for d in m.list_modules()])
    mods = {d["module_id"] for d in m.list_modules()}
    dangling = [e["edge_id"] for e in m.list_edges() if e["source"] not in mods or e["target"] not in mods]
    if dangling:
        m.col("graph_edges").delete_many({"edge_id": {"$in": dangling}})
        act(f"dropped {len(dangling)} dangling edges")
    ctx.persistence.reload_graph()

    # 6. baseline version
    if not ctx.versions.active_id():
        try:
            v = ctx.versions.create(ctx.persistence.export_live_state(), reason="initial baseline",
                                    operation_type="baseline", summary={"note": "created at first startup"})
            act(f"created baseline architecture version {v['version_id']}")
        except Exception as exc:
            report["baseline_error"] = str(exc)[:200]

    report["validation"] = ctx.validator.check()
    m.set_state("startup_reconciliation", report)
    return report
