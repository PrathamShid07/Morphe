"""Versions, cross-store restoration, partial-failure recovery, post-commit verification and monitoring rollback."""
import pytest

from app.adaptation.controller import PostCommitVerificationError
from app.common import ConflictError, IntegrityError, MorpheError
from app.reconcile import reconcile_startup
from tests.conftest import ingest_docs
from tests.test_adaptation import reload_dataset


def mixed(seeded):
    ctx = seeded
    ingest_docs(ctx, ["py_web", "py_data", "py_tools"])
    reload_dataset(ctx, include_mixed=True)
    return ctx


def stores_snapshot(ctx):
    """Everything restoration must put back."""
    live = ctx.persistence.export_live_state()
    q = {mid: p["module_id"] for mid, p, _ in ctx.qdrant.scroll_memories()}
    m = {d["memory_id"]: d["module_id"] for d in ctx.mongo.active_memory_docs()}
    profiles = {k: tuple(round(float(x), 4) for x in v[1]) for k, v in ctx.qdrant.list_module_profiles().items()}
    graph = sorted((d["edge_id"]) for *_, d in ctx.graph.graph.edges(keys=True, data=True))
    return {"checksum": live.checksum(), "qdrant": q, "mongo": m, "profiles": profiles, "graph": graph,
            "nodes": sorted(ctx.graph.graph.nodes)}


def test_snapshot_checksum_detects_corruption(seeded):
    ctx = seeded
    vid = ctx.versions.active_id()
    assert ctx.versions.verify(vid)["valid"]
    ctx.mongo.col("architecture_snapshots").update_one(
        {"version_id": vid}, {"$set": {"state.assignments.mod_django": ["mem_tampered"]}})
    assert not ctx.versions.verify(vid)["valid"]
    with pytest.raises(IntegrityError):
        ctx.versions.snapshot_state(vid)
    d = ctx.versions.describe(ctx.versions.manifest(vid))
    assert d["restorable"] is False
    with pytest.raises(MorpheError):
        ctx.lifecycle.restore(vid)
    ev = ctx.mongo.col("adaptation_events").find_one({"operation": "restore_version"})
    assert ev["status"] == "failed"  # failure is recorded truthfully


def test_restore_reverts_every_store_after_a_split(seeded):
    ctx = mixed(seeded)
    baseline = ctx.lifecycle.ensure_checkpoint("before split")
    before = stores_snapshot(ctx)
    ev = ctx.adaptation.process("split_module", ["mod_python"], apply=True)
    assert ev["status"] == "completed"
    assert stores_snapshot(ctx)["checksum"] != before["checksum"]

    preview = ctx.lifecycle.preview_restore(baseline["version_id"])
    assert preview["restorable"] and preview["diff"]["modules_removed"] and preview["current_version"]["version_id"] == ev["new_version"]

    res = ctx.lifecycle.restore(baseline["version_id"], reason="test rollback")
    assert res["status"] == "completed" and res["restored_from"] == baseline["version_id"]
    after = stores_snapshot(ctx)
    for key in ("qdrant", "mongo", "nodes", "graph"):
        assert after[key] == before[key], key
    assert after["profiles"].keys() == before["profiles"].keys()
    assert ctx.validator.check()["passed"]
    # version history stays readable; restore itself is a new version; audit events are retained
    versions, _ = ctx.versions.list_versions(50)
    ids = [v["version_id"] for v in versions]
    assert ev["new_version"] in ids and res["new_version"]["version_id"] == ctx.versions.active_id()
    assert res["new_version"]["restored_from"] == baseline["version_id"]
    split_event = ctx.events.get_adaptation(ev["event_id"])
    assert split_event["status"] == "rolled_back" and split_event["rolled_back_to"] == baseline["version_id"]
    assert ctx.events.get_adaptation(res["operation_id"])["status"] == "completed"
    # a split can be re-applied after rollback and the rollback itself is undoable
    again = ctx.lifecycle.restore(ev["new_version"])
    assert again["status"] == "completed" and "mod_python" in ctx.graph.graph and \
        ctx.graph.graph.nodes["mod_python"]["active_record_count"] == 0


def test_restore_does_not_revive_deleted_or_superseded_records_and_places_new_ones(seeded):
    ctx = mixed(seeded)
    cp = ctx.lifecycle.ensure_checkpoint("cp")
    mid = sorted(ctx.mongo.module_active_counts().keys())[0]
    victim = ctx.mongo.col("memory_metadata").find_one({"module_id": "mod_django", "status": "active"})["memory_id"]
    ctx.memories.delete_memory(victim)
    ctx.ingestion.ingest("new_redis_note.md", "## Streams\n\nRedis streams append entries with consumer groups.",
                         {"topic": "redis", "parent_topic": "data"})
    ctx.adaptation.process("split_module", ["mod_python"], apply=True)
    res = ctx.lifecycle.restore(cp["version_id"])
    assert res["status"] == "completed"
    assert ctx.mongo.get_memory_metadata(victim)["status"] == "deleted"
    assert victim not in {m for m, _, _ in ctx.qdrant.scroll_memories()}
    assert res["reconciliation"]["new_memories_kept_in_current_module"] >= 1
    assert ctx.validator.check()["passed"]


def test_restore_is_idempotent_for_a_repeated_operation_id(seeded):
    ctx = mixed(seeded)
    cp = ctx.lifecycle.ensure_checkpoint("cp")
    ctx.adaptation.process("split_module", ["mod_python"], apply=True)
    first = ctx.lifecycle.restore(cp["version_id"], operation_id="adapt_fixed_id")
    n = ctx.mongo.col("architecture_versions").count_documents({})
    second = ctx.lifecycle.restore(cp["version_id"], operation_id="adapt_fixed_id")
    assert second["already_recorded"] and second["status"] == "completed"
    assert ctx.mongo.col("architecture_versions").count_documents({}) == n and first["status"] == "completed"


def test_converge_is_idempotent_and_repairs_partial_application(seeded):
    ctx = mixed(seeded)
    ev = ctx.adaptation.process("split_module", ["mod_python"], apply=False)
    target = ctx.adaptation.detector.build("split_module", ["mod_python"], ctx.adaptation.planning_context(), None,
                                           "t", "op_test")[0].plan.new_state
    ctx.persistence.converge_to(target)
    once = stores_snapshot(ctx)
    ctx.persistence.converge_to(target)
    assert stores_snapshot(ctx) == once
    # simulate a crash that left Qdrant half-moved: converge repairs it
    some = [m for m, mod in target.module_of().items() if mod != "mod_python"][:3]
    ctx.qdrant.set_module(some, "mod_python")
    assert not ctx.validator.check()["passed"]
    ctx.persistence.converge_to(target)
    assert ctx.validator.check()["passed"] and stores_snapshot(ctx) == once


# ------------------------------------------------------ failure during apply
def test_failure_after_partial_commit_is_recovered_and_reported_truthfully(seeded, monkeypatch):
    ctx = mixed(seeded)
    before = stores_snapshot(ctx)
    real = ctx.persistence.converge_to
    calls = {"n": 0}

    def crash_after_apply(target, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            real(target, **kw)  # everything written...
            raise RuntimeError("simulated crash right after applying")
        return real(target, **kw)  # ...recovery uses the real routine
    monkeypatch.setattr(ctx.persistence, "converge_to", crash_after_apply)
    ev = ctx.adaptation.process("split_module", ["mod_python"], apply=True)
    assert ev["status"] != "completed"
    assert ev["status"] in ("failed", "rolled_back") and "simulated crash" in ev["error"]
    assert ev["recovered"] is True and ev.get("new_version") is None
    after = stores_snapshot(ctx)
    for key in ("qdrant", "mongo", "nodes", "graph"):
        assert after[key] == before[key], key
    assert ctx.validator.check()["passed"]
    assert ctx.mongo.get_state("needs_recovery") is None


def test_failure_before_any_write_reports_failed_not_rolled_back(seeded, monkeypatch):
    ctx = mixed(seeded)
    monkeypatch.setattr(ctx.persistence, "converge_to",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("write refused")))
    # recovery calls converge_to too and also fails -> must be flagged for startup recovery, never "completed"
    ev = ctx.adaptation.process("split_module", ["mod_python"], apply=True)
    assert ev["status"] == "failed" and ev["recovered"] is False
    assert ctx.mongo.get_state("needs_recovery")["before_version"]


def test_startup_reconciliation_recovers_a_failed_recovery(seeded, monkeypatch):
    ctx = mixed(seeded)
    before = stores_snapshot(ctx)
    real = ctx.persistence.converge_to
    state = {"n": 0}

    def flaky(target, **kw):
        state["n"] += 1
        if state["n"] == 1:
            real(target, **kw)  # applied...
        raise RuntimeError("disk gone")  # ...and recovery fails too
    monkeypatch.setattr(ctx.persistence, "converge_to", flaky)
    ev = ctx.adaptation.process("split_module", ["mod_python"], apply=True)
    assert ev["status"] == "failed" and ctx.mongo.get_state("needs_recovery")
    assert stores_snapshot(ctx)["checksum"] != before["checksum"]  # genuinely inconsistent right now
    monkeypatch.undo()
    report = reconcile_startup(ctx)  # "restart"
    assert any("recovered architecture" in a for a in report["actions"])
    assert ctx.mongo.get_state("needs_recovery") is None
    after = stores_snapshot(ctx)
    for key in ("qdrant", "mongo", "nodes"):
        assert after[key] == before[key], key
    assert ctx.validator.check()["passed"]


def test_post_commit_verification_failure_rolls_back_and_status_is_rolled_back(seeded, monkeypatch):
    ctx = mixed(seeded)
    before = stores_snapshot(ctx)
    n_versions = ctx.mongo.col("architecture_versions").count_documents({})

    def boom(*a, **k):
        raise PostCommitVerificationError("live retrieval does not reproduce the evaluated candidate")
    monkeypatch.setattr(ctx.adaptation, "_verify_post_commit", boom)
    ev = ctx.adaptation.process("split_module", ["mod_python"], apply=True)
    assert ev["status"] == "rolled_back" and ev["recovered"] is True
    assert ev["post_commit_verification"]["status"] == "failed"
    after = stores_snapshot(ctx)
    for key in ("qdrant", "mongo", "nodes", "graph"):
        assert after[key] == before[key], key
    assert ctx.validator.check()["passed"]
    assert ctx.versions.active_id() == ev["rolled_back_to"]
    assert ctx.mongo.col("architecture_versions").count_documents({}) <= n_versions + 1  # at most the checkpoint


def test_stale_plan_is_refused_without_changes(seeded, monkeypatch):
    ctx = mixed(seeded)
    orig = ctx.evaluator.evaluate_state

    def mutate_midway(*a, **k):
        r = orig(*a, **k)
        if not getattr(mutate_midway, "done", False):
            mutate_midway.done = True
            ctx.ingestion.ingest("late.md", "## Late\n\nA late arriving note about docker swarm.", {"topic": "docker"})
        return r
    monkeypatch.setattr(ctx.evaluator, "evaluate_state", mutate_midway)
    with pytest.raises(ConflictError) as exc:
        ctx.adaptation.process("split_module", ["mod_python"], apply=True, event_id="adapt_stale")
    assert exc.value.code == "stale_plan"
    ev = ctx.events.get_adaptation("adapt_stale")
    assert ev["status"] == "failed" and "changed while" in ev["error"]
    assert ctx.graph.graph.nodes["mod_python"]["active_record_count"] == 15


def test_monitoring_rolls_back_a_committed_change_that_regresses(seeded):
    ctx = mixed(seeded)
    ev = ctx.adaptation.process("split_module", ["mod_python"], apply=True)
    assert ev["status"] == "completed"
    assert ctx.adaptation.monitor_regression()["status"] == "ok"
    # tighten the approved monitoring policy so that the change no longer qualifies (requires a 0.5 improvement)
    ctx.settings.max_recall_regression = -0.5
    res = ctx.adaptation.monitor_regression()
    assert res["status"] == "rolled_back"
    assert ctx.events.get_adaptation(ev["event_id"])["status"] == "rolled_back"
    assert ctx.graph.graph.nodes["mod_python"]["active_record_count"] == 15
    assert ctx.validator.check()["passed"]
