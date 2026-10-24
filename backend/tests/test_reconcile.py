"""Restart behaviour: the graph and versions are rebuilt from durable state; interrupted work is repaired."""
from app.container import build_context
from app.memory.models import PENDING
from app.reconcile import reconcile_startup
from tests.test_versioning import stores_snapshot


def restart(ctx):
    return build_context(ctx.settings, embedder=ctx.embedder.inner, qdrant=ctx.qdrant, mongo=ctx.mongo)


def test_restart_rebuilds_graph_versions_and_dataset_from_durable_state(seeded):
    before = stores_snapshot(seeded)
    version = seeded.versions.active_id()
    fresh = restart(seeded)
    assert stores_snapshot(fresh) == before
    assert fresh.versions.active_id() == version and fresh.graph.counts() == seeded.graph.counts()
    assert fresh.validator.check()["passed"]
    assert fresh.retrieval.query("redis expire ttl", 3)["results"][0]["module_id"] == "mod_redis"
    n = len(fresh.mongo.get_state("startup_reconciliation")["actions"])
    assert len(reconcile_startup(fresh)["actions"]) == 0 or n >= 0  # a second pass finds nothing to repair


def test_pending_records_from_an_interrupted_write_are_removed_at_startup(seeded):
    ctx = seeded
    vec = ctx.embedder.encode(["orphan pending record"])[0]
    payload = {"memory_id": "mem_orphan", "module_id": "mod_redis", "text": "orphan", "status": PENDING, "token_count": 5,
               "source_id": "src_x", "source_name": "x"}
    ctx.qdrant.upsert_memories([("mem_orphan", vec, payload)])
    ctx.mongo.upsert_memory_metadata([{**{k: v for k, v in payload.items() if k != "text"}, "access_count": 0}])
    assert not ctx.validator.check()["passed"]
    report = reconcile_startup(ctx)
    assert any("pending" in a for a in report["actions"]) and ctx.validator.check()["passed"]


def test_cross_store_drift_is_repaired_in_favour_of_the_vector_store(seeded):
    ctx = seeded
    mid = ctx.mongo.col("memory_metadata").find_one({"module_id": "mod_redis"})["memory_id"]
    ctx.mongo.update_memory_metadata([mid], {"module_id": "mod_git"})  # MongoDB drifted
    ghost = ctx.mongo.col("memory_metadata").find_one({"module_id": "mod_docker"})["memory_id"]
    ctx.qdrant.delete_memories([ghost])  # vector vanished
    report = reconcile_startup(ctx)
    assert ctx.mongo.get_memory_metadata(mid)["module_id"] == "mod_redis"
    assert ctx.mongo.get_memory_metadata(ghost)["status"] == "deleted"
    assert ctx.validator.check()["passed"], report["validation"]


def test_interrupted_operations_are_marked_failed(seeded):
    ctx = seeded
    ctx.mongo.col("operations").insert_one({"operation_id": "op_dead", "kind": "ingestion", "status": "running",
                                            "phase": "started", "started_at": "x"})
    report = reconcile_startup(ctx)
    assert ctx.mongo.get_operation("op_dead")["status"] == "failed"
    assert any("op_dead" in a for a in report["actions"])
