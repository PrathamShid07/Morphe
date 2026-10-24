"""Structural adaptation: detection, evaluation-gated commit, explanation, integrity."""
import json
from pathlib import Path

from tests.conftest import ingest_docs
from tests.corpus import build_eval_fixture, DOCS


def reload_dataset(ctx, include_mixed=False):
    Path(ctx.settings.eval_dataset_path).write_text(json.dumps(build_eval_fixture(ctx, include_mixed)))
    ctx.datasets.load_fixture()


def test_mixed_module_split_is_proposed_evaluated_and_committed(seeded):
    ctx = seeded
    ingest_docs(ctx, ["py_web", "py_data", "py_tools"])
    reload_dataset(ctx, include_mixed=True)
    assert ctx.graph.graph.nodes["mod_python"]["active_record_count"] == 15
    cands = ctx.adaptation.candidates()
    split = [p for p in cands["proposals"] if p["operation"] == "split_module"]
    assert split and split[0]["module_ids"] == ["mod_python"]
    assert "mixes distinct topics" in split[0]["reason"]
    before_version = ctx.versions.active_id()

    ev = ctx.adaptation.process("split_module", ["mod_python"], apply=True)
    assert ev["status"] == "completed", ev.get("error") or ev.get("decision_reason")
    assert ev["decision"] == "accept" and ev["integrity_check"] == "passed"
    assert ev["evaluation"]["decision"]["checks"], "decision must expose its threshold checks"
    assert ev["created_module_ids"] and ev["new_version"] != before_version
    # structure really changed in every store
    g = ctx.graph.graph
    children = [m for m, d in g.nodes(data=True) if d.get("parent_id") == "mod_python"]
    assert len(children) >= 2
    assert g.nodes["mod_python"]["active_record_count"] == 0  # container
    assert sum(g.nodes[c]["active_record_count"] for c in children) == 15
    assert ctx.validator.check()["passed"]
    # profiles exist only for content modules
    assert "mod_python" not in ctx.qdrant.list_module_profiles(with_vectors=False)
    # old version retained and restorable, new version active
    assert ctx.versions.active_id() == ev["new_version"]
    assert ctx.versions.verify(ev["before_version"])["valid"]


def test_redundant_modules_are_merged_only_when_overlap_and_no_conflicts(seeded):
    ctx = seeded
    ingest_docs(ctx, ["postgres_dup"])
    reload_dataset(ctx)
    cands = ctx.adaptation.candidates()
    merges = [p for p in cands["proposals"] if p["operation"] == "merge_modules"]
    assert merges, cands["not_proposed"]
    ev = ctx.adaptation.process("merge_modules", merges[0]["module_ids"], apply=True)
    assert ev["status"] == "completed", ev.get("error") or ev.get("decision_reason")
    assert len(ev["removed_module_ids"]) == 1
    # every record survived, assigned to the survivor
    survivor = ev["evidence"]["survivor"]
    assert ctx.graph.graph.nodes[survivor]["active_record_count"] == 8
    assert ctx.validator.check()["passed"]


def test_merge_blocked_by_conflicting_facts(seeded):
    ctx = seeded
    d = DOCS["postgres_dup"]
    # an overlapping chunk that states the same thing with a different port number
    text = d["text"].replace("Autovacuum in PostgreSQL reclaims dead tuples automatically and the server listens on port 5432 by default.",
                             "PostgreSQL vacuum reclaims dead tuples and autovacuum runs automatically on the default port 6543.")
    assert text != d["text"]
    ctx.ingestion.ingest(d["source_name"], text, d["metadata"])
    reload_dataset(ctx)
    ev = ctx.adaptation.process("merge_modules", ["mod_postgres", "mod_postgresql"], apply=True)
    assert ev["status"] == "rejected" and ev["rejection_stage"] == "planning"
    assert "conflicting numeric facts" in ev["reason"]
    assert "mod_postgres" in ctx.graph.graph  # nothing changed


def queries_for_pair(ctx, n=8):
    for _ in range(n):
        ctx.retrieval.query("django views and postgresql indexes explain", 5)


def test_shortcut_edge_from_recorded_coaccess_and_it_changes_routing(seeded):
    ctx = seeded
    ctx.tracker.reset_window()
    for _ in range(12):
        ctx.retrieval.query("django url views postgresql indexes vacuum", 6)
    status = ctx.adaptation.status()
    pairs = {tuple(p["modules"]): p["count"] for p in status["window_pairs"]}
    assert pairs.get(("mod_django", "mod_postgresql"), 0) >= 5
    q = ctx.retrieval.query("fastapi pydantic validation", 3, True)  # unrelated before
    ev = ctx.adaptation.process("add_shortcut_edge", ["mod_django", "mod_postgresql"], apply=True)
    assert ev["status"] == "completed", ev.get("error") or ev.get("decision_reason")
    edges = [d for _, _, k, d in ctx.graph.graph.edges(keys=True, data=True) if k == "co_access"]
    assert len(edges) == 1 and edges[0]["co_access_count"] >= 5 and edges[0]["weight"] > 0
    # the graph now changes the candidate-module set of a django-seeded query
    out = ctx.retrieval.query("django url routing views", 3, True)
    cand = {c["module_id"]: c["origin"] for c in out["trace"]["candidate_modules"]}
    assert cand.get("mod_postgresql") == "co_access" or "mod_postgresql" in cand
    # duplicate shortcut is refused
    again = ctx.adaptation.process("add_shortcut_edge", ["mod_django", "mod_postgresql"], apply=True)
    assert again["status"] == "rejected" and "already exists" in again["reason"]


def test_window_run_applies_at_most_configured_operations_and_records_window(seeded):
    ctx = seeded
    ingest_docs(ctx, ["py_web", "py_data", "py_tools", "postgres_dup"])
    reload_dataset(ctx, include_mixed=True)
    summary = ctx.adaptation.run_window("test_window")
    assert summary["applied"] == 1  # adaptation_max_ops_per_window default
    assert ctx.mongo.get_state("last_window_run")["applied"] == 1
    assert ctx.validator.check()["passed"]
