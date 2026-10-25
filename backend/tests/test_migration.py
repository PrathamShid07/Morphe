"""The pre-account data migration: fail-closed labelling, idempotence, backup and exact rollback."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.container import build_context
from app.migration import migrate, plan, rollback, sample_source_names
from tests.conftest import make_settings, register_user
from tests.test_isolation import ask
from tests.corpus import DOCS
from fastapi.testclient import TestClient
from app.main import create_app

A = "/api/v1"


@pytest.fixture()
def legacy(tmp_path):
    """A database as it was before accounts: records without any visibility label (one sample-corpus source, one ad-hoc source)."""
    s = make_settings(tmp_path, migration_backup_dir=str(tmp_path / "backups"), auto_migrate=False)
    ctx = build_context(s)
    sample = sorted(sample_source_names(s.samples_dir))[0]
    d = DOCS["django"]
    a = ctx.ingestion.ingest(sample, d["text"], d["metadata"])  # named like a repository sample file
    b = ctx.ingestion.ingest("adhoc_notes.md", DOCS["docker"]["text"], DOCS["docker"]["metadata"])  # provenance unknown
    for res in (a, b):  # strip the labels the new ingestion code adds, to recreate pre-account records exactly
        ctx.qdrant.delete_payload_keys(res["memory_ids"], ["visibility", "owner_user_id"])
        ctx.mongo.col("memory_metadata").update_many({"memory_id": {"$in": res["memory_ids"]}}, {"$unset": {"visibility": "", "owner_user_id": ""}})
        ctx.mongo.col("sources").update_one({"source_id": res["source_id"]}, {"$unset": {"visibility": ""}})
    ctx.legacy = (a, b)
    yield ctx
    ctx.close()


def test_unlabelled_legacy_records_are_invisible_to_users_until_migrated(legacy):
    app = create_app(legacy.settings, legacy)
    with TestClient(app) as c:
        register_user(c)
        assert ask(c, "how does django map url patterns to views", "global")["results"] == []  # fail-closed before the migration
        assert c.get(f"{A}/me/graph").json()["nodes"] == []


def test_plan_is_read_only_and_describes_the_labelling(legacy):
    before = legacy.mongo.col("memory_metadata").count_documents({"visibility": {"$exists": True}})
    p = plan(legacy)
    assert p["records_unlabelled"] == p["records_total"] > 0 and p["would_share"] > 0 and p["would_restrict"] > 0
    assert p["would_share"] + p["would_restrict"] == p["records_unlabelled"]
    assert legacy.mongo.col("memory_metadata").count_documents({"visibility": {"$exists": True}}) == before
    assert migrate(legacy, dry_run=True)["applied"] is False


def test_sample_corpus_becomes_shared_everything_else_stays_administrator_only(legacy):
    a, b = legacy.legacy
    res = migrate(legacy)
    assert res["applied"] and res["changed"] == len(a["memory_ids"]) + len(b["memory_ids"])
    assert res["labelled_shared"] == len(a["memory_ids"]) and res["labelled_restricted"] == len(b["memory_ids"])
    md = legacy.mongo.col("memory_metadata")
    assert {d["visibility"] for d in md.find({"memory_id": {"$in": a["memory_ids"]}})} == {"shared"}
    assert {d["visibility"] for d in md.find({"memory_id": {"$in": b["memory_ids"]}})} == {"restricted"}
    pts = {mid: p for mid, p, _ in legacy.qdrant.scroll_memories(status=None)}
    assert all(pts[m]["visibility"] == "shared" and pts[m].get("owner_user_id") is None for m in a["memory_ids"])
    assert all(pts[m]["visibility"] == "restricted" for m in b["memory_ids"])
    assert legacy.mongo.col("sources").find_one({"source_id": a["source_id"]})["visibility"] == "shared"
    app = create_app(legacy.settings, legacy)
    with TestClient(app) as c:
        register_user(c)
        got = {x["memory_id"] for x in ask(c, "how does django map url patterns to views", "global")["results"]}
        assert got and got <= set(a["memory_ids"])
        assert not {x["memory_id"] for x in ask(c, "how do I build a docker image and run a container", "global", top_k=20)["results"]} & set(b["memory_ids"])
        assert {n["id"] for n in c.get(f"{A}/me/graph").json()["nodes"]} == {"mod_django", "mod_web"}  # the shared module and its group, nothing restricted


def test_migration_is_idempotent_and_changes_nothing_on_a_second_run(legacy):
    migrate(legacy)
    state = lambda: sorted((d["memory_id"], d["visibility"]) for d in legacy.mongo.col("memory_metadata").find({}))
    first = state()
    again = migrate(legacy)
    assert again["applied"] is False and again["changed"] == 0 and again["records_unlabelled"] == 0
    assert state() == first
    assert legacy.mongo.col("migrations").count_documents({}) == 1  # no second run was recorded


def test_an_interrupted_run_resumes_where_it_stopped(legacy):
    a, b = legacy.legacy
    legacy.mongo.col("memory_metadata").update_many({"memory_id": {"$in": a["memory_ids"]}}, {"$set": {"visibility": "shared", "owner_user_id": None}})  # a half-finished run
    res = migrate(legacy)
    assert res["changed"] == len(b["memory_ids"]) and res["labelled_restricted"] == len(b["memory_ids"])
    assert legacy.mongo.col("memory_metadata").count_documents({"visibility": {"$exists": False}}) == 0


def test_backup_is_written_and_rollback_removes_exactly_the_labels(legacy):
    a, b = legacy.legacy
    res = migrate(legacy)
    backup = Path(res["backup"])
    data = json.loads(backup.read_text())
    assert set(data["labelled_shared"]) == set(a["memory_ids"]) and set(data["labelled_restricted"]) == set(b["memory_ids"])
    before_text = {mid: p["text"] for mid, p, _ in legacy.qdrant.scroll_memories(status=None)}
    out = rollback(legacy, str(backup))
    assert out["labels_removed"] == len(a["memory_ids"]) + len(b["memory_ids"])
    assert legacy.mongo.col("memory_metadata").count_documents({"visibility": {"$exists": True}}) == 0
    assert all("visibility" not in p for _, p, _ in legacy.qdrant.scroll_memories(status=None))
    assert {mid: p["text"] for mid, p, _ in legacy.qdrant.scroll_memories(status=None)} == before_text  # nothing else was touched
    assert legacy.mongo.col("migrations").find_one({"run_id": res["run_id"]})["status"] == "rolled_back"
    assert migrate(legacy)["applied"] is True  # and it can be applied again


def test_migration_never_touches_architecture_versions_or_the_personal_plane(legacy):
    from app.versioning.state import ArchitectureState  # noqa: F401
    legacy.personal.save("usr_AAAAAAAAAAAAAAAA", "A personal note about gardening and tomato seedlings.", title="Garden")
    snap = lambda: (legacy.versions.active_id(), legacy.mongo.col("architecture_versions").count_documents({}), legacy.mongo.col("modules").count_documents({}),
                    legacy.mongo.col("graph_edges").count_documents({}), legacy.mongo.col("user_memories").count_documents({}),
                    legacy.user_vectors.count_memories("usr_AAAAAAAAAAAAAAAA"))
    before = snap()
    migrate(legacy)
    assert snap() == before


def test_startup_applies_the_migration_automatically_when_enabled(tmp_path):
    s = make_settings(tmp_path, migration_backup_dir=str(tmp_path / "bk"), auto_migrate=False)
    ctx = build_context(s)
    sample = sorted(sample_source_names(s.samples_dir))[0]
    r = ctx.ingestion.ingest(sample, DOCS["django"]["text"], DOCS["django"]["metadata"])
    ctx.qdrant.delete_payload_keys(r["memory_ids"], ["visibility"])
    ctx.mongo.col("memory_metadata").update_many({}, {"$unset": {"visibility": ""}})
    ctx2 = build_context(make_settings(tmp_path, migration_backup_dir=str(tmp_path / "bk"), auto_migrate=True), mongo=ctx.mongo, qdrant=ctx.qdrant)
    try:
        assert ctx2.mongo.col("memory_metadata").count_documents({"visibility": {"$exists": False}}) == 0
    finally:
        ctx2.close()
