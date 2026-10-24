"""Metrics, dataset validation, acceptance policy, isolated candidate evaluation and rejection without side effects."""
import copy
import json
import random
from pathlib import Path

import pytest

from app.evaluation.dataset import load_dataset_file, parse_dataset
from app.evaluation.metrics import percentile, recall_at_k, reciprocal_rank
from app.evaluation.version_comparator import AcceptancePolicy
from app.versioning.state import ArchitectureState, centroid
from tests.conftest import make_settings


# ------------------------------------------------------------------ metrics
def test_recall_at_k_counts_fraction_of_relevant_in_top_k():
    assert recall_at_k({"a", "b"}, ["x", "a", "y", "z", "w", "b"], 5) == 0.5
    assert recall_at_k({"a"}, ["a"], 5) == 1.0
    assert recall_at_k({"a", "b", "c", "d"}, ["a", "b", "c", "d", "e"], 5) == 1.0
    assert recall_at_k({"a"}, ["x", "y", "z", "w", "v", "a"], 5) == 0.0
    with pytest.raises(ValueError):
        recall_at_k(set(), ["a"])


def test_reciprocal_rank_first_relevant_and_zero_when_missing():
    assert reciprocal_rank({"a"}, ["a", "b"]) == 1.0
    assert reciprocal_rank({"b", "c"}, ["a", "b", "c"]) == 0.5
    assert reciprocal_rank({"z"}, ["a", "b", "c"]) == 0.0
    assert reciprocal_rank({"a"}, ["x", "y", "z", "w", "v", "a"], k=5) == 0.0  # beyond the cut-off


def test_percentile_linear_interpolation_and_empty():
    vals = list(range(1, 101))
    assert percentile(vals, 95) == pytest.approx(95.05)
    assert percentile([7.0], 95) == 7.0
    assert percentile([], 95) is None


# ------------------------------------------------------------------ dataset
def test_dataset_validation_rejects_malformed_entries(tmp_path):
    ds = parse_dataset([{"query_id": "a", "query": "", "relevant_memory_ids": ["m"]},
                        {"query_id": "b", "query": "ok", "relevant_memory_ids": []},
                        {"query_id": "c", "query": "ok", "relevant_memory_ids": ["m"], "split": "bogus"},
                        {"query_id": "d", "query": "fine", "relevant_memory_ids": ["m"]},
                        {"query_id": "d", "query": "dup", "relevant_memory_ids": ["m"]}], "t")
    assert len(ds.queries) == 1 and len(ds.issues) == 4
    assert not load_dataset_file(str(tmp_path / "missing.json")).valid
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert not load_dataset_file(str(bad)).valid


def test_broken_fixture_never_replaces_a_good_dataset(seeded):
    ctx = seeded
    n = len(ctx.datasets.current().queries)
    assert n >= 10
    Path(ctx.settings.eval_dataset_path).write_text("{corrupt")
    ds = ctx.datasets.load_fixture()
    assert not ds.valid
    assert len(ctx.datasets.current().queries) == n


def test_dataset_hash_is_stable(seeded):
    assert seeded.datasets.current().dataset_hash == load_dataset_file(seeded.settings.eval_dataset_path).dataset_hash


# ------------------------------------------------------------ acceptance policy
def report(recall=0.9, mrr=0.8, p95=10.0, n=20, valid=True, cand=5.0, h="h"):
    return {"valid": valid, "invalid_reason": None if valid else "insufficient",
            "metrics": {"recall_at_5": recall, "mrr": mrr, "p95_latency_ms": p95, "sample_size": n,
                        "mean_candidate_count": cand, "fallback_rate": 0.0},
            "conditions": {"dataset_hash": h, "queries_evaluated": n}}


@pytest.fixture()
def policy(tmp_path):
    return AcceptancePolicy(make_settings(tmp_path, latency_noise_floor_ms=0.5))


def test_policy_accepts_within_thresholds(policy):
    d = policy.decide(report(), report(recall=0.885, mrr=0.785, p95=10.9))
    assert d["accepted"], d["reason"]


@pytest.mark.parametrize("cand,fragment", [
    (dict(recall=0.87), "recall_at_5"), (dict(mrr=0.77), "mrr"), (dict(p95=12.0), "p95_latency_ms")])
def test_policy_rejects_each_regression(policy, cand, fragment):
    d = policy.decide(report(), report(**cand))
    assert not d["accepted"] and d["reason"].startswith("Rejected")
    assert [c for c in d["checks"] if c["name"] == fragment and not c["passed"]]


def test_policy_latency_noise_floor_tolerates_tiny_absolute_changes(policy):
    assert policy.decide(report(p95=0.10), report(p95=0.45))["accepted"]  # +350% but < 0.5ms noise floor
    assert not policy.decide(report(p95=1.0), report(p95=1.6))["accepted"]


def test_policy_rejects_missing_or_insufficient_data_and_mismatched_conditions(policy):
    d = policy.decide(report(), report(valid=False, n=3))
    assert not d["accepted"] and "insufficient" in d["reason"].lower()
    assert not policy.decide(report(h="a"), report(h="b"))["accepted"]


def test_override_only_bypasses_missing_data_and_only_in_dev(tmp_path):
    dev = AcceptancePolicy(make_settings(tmp_path, allow_validation_override=True))
    d = dev.decide(report(valid=False, n=3), report(valid=False, n=3), override=True)
    assert d["accepted"] and d["override_used"]
    # a real regression is never overridden
    assert not dev.decide(report(valid=False), report(recall=0.5, valid=False), override=True)["accepted"]
    off = AcceptancePolicy(make_settings(tmp_path, allow_validation_override=False))
    assert not off.decide(report(valid=False), report(valid=False), override=True)["accepted"]
    prod = AcceptancePolicy(make_settings(tmp_path, app_env="production", allow_validation_override=True,
                                          embedding_backend="sentence_transformers", mongo_backend="pymongo"))
    assert not prod.decide(report(valid=False), report(valid=False), override=True)["accepted"]


# ---------------------------------------------- isolated evaluation, parity, rejection
def test_candidate_evaluation_is_isolated_from_production(seeded):
    ctx = seeded
    live = ctx.persistence.export_live_state()
    checksum = live.checksum()
    ectx = ctx.evaluator.build_context()
    bad = scrambled(live, ctx, seed=1)
    ctx.evaluator.evaluate_state(bad, ectx, "candidate")
    after = ctx.persistence.export_live_state()
    assert after.checksum() == checksum and ctx.validator.check()["passed"]


def scrambled(state: ArchitectureState, ctx, seed: int) -> ArchitectureState:
    """A deliberately harmful candidate: records are shuffled across modules but routing profiles stay stale."""
    rnd = random.Random(seed)
    bad = state.copy()
    ids = sorted(state.module_of())
    mods = state.content_modules()
    bad.assignments = {m: [] for m in mods}
    for mid in ids:
        bad.assignments[rnd.choice(mods)].append(mid)
    for m in mods:
        bad.assignments[m].sort()
    return bad


def test_sandbox_evaluation_reproduces_live_retrieval_exactly(seeded):
    ctx = seeded
    ectx = ctx.evaluator.build_context()
    sandbox = ctx.evaluator.evaluate_state(ctx.persistence.export_live_state(), ectx, "s")
    live = ctx.evaluator.evaluate_live(ectx, "l")
    for k in ("recall_at_5", "mrr", "mean_candidate_count", "fallback_rate", "sample_size"):
        assert sandbox["metrics"][k] == pytest.approx(live["metrics"][k]), k
    assert [r["retrieved"] for r in sandbox["per_query"]] == [r["retrieved"] for r in live["per_query"]]


def test_harmful_architecture_measurably_regresses_and_policy_rejects_it(seeded):
    ctx = seeded
    ctx.settings.fallback_enabled = False  # otherwise the global fallback rescues recall by design
    ectx = ctx.evaluator.build_context()
    live = ctx.persistence.export_live_state()
    base = ctx.evaluator.evaluate_state(live, ectx, "base")
    cand = ctx.evaluator.evaluate_state(scrambled(live, ctx, 3), ectx, "cand")
    assert cand["metrics"]["recall_at_5"] < base["metrics"]["recall_at_5"] - 0.1
    dec = ctx.policy.decide(base, cand)
    assert not dec["accepted"] and any(c["name"] == "recall_at_5" and not c["passed"] for c in dec["checks"])


def test_rejected_candidate_does_not_change_the_active_architecture(seeded, monkeypatch):
    ctx = seeded
    ctx.settings.fallback_enabled = False
    before_checksum = ctx.persistence.export_live_state().checksum()
    before_version = ctx.versions.active_id()
    n_versions = ctx.mongo.col("architecture_versions").count_documents({})
    ctx.tracker.reset_window()
    for _ in range(12):
        ctx.retrieval.query("django url views postgresql indexes vacuum", 6)
    # inject a harmful candidate plan; evaluation, decision and rejection handling are the real code paths
    orig = ctx.adaptation.detector.build

    def harmful(operation, module_ids, c, window, trigger, operation_id):
        proposal, diag = orig(operation, module_ids, c, window, trigger, operation_id)
        proposal.plan.new_state = scrambled(proposal.plan.new_state, ctx, 3)
        return proposal, diag
    monkeypatch.setattr(ctx.adaptation.detector, "build", harmful)
    ev = ctx.adaptation.process("add_shortcut_edge", ["mod_django", "mod_postgresql"], apply=True)
    assert ev["status"] == "rejected" and ev["rejection_stage"] == "pre_commit", ev
    assert ev["decision"] == "reject" and "recall" in ev["decision_reason"].lower()
    assert ctx.persistence.export_live_state().checksum() == before_checksum
    assert ctx.versions.active_id() == before_version
    assert ctx.mongo.col("architecture_versions").count_documents({}) == n_versions
    assert not any(k == "co_access" for *_, k, d in ctx.graph.graph.edges(keys=True, data=True))
    assert ctx.validator.check()["passed"]


def test_insufficient_evaluation_data_blocks_commit_without_override(seeded):
    ctx = seeded
    ctx.mongo.col("evaluation_queries").delete_many({})
    ctx.tracker.reset_window()
    for _ in range(12):
        ctx.retrieval.query("django url views postgresql indexes vacuum", 6)
    ev = ctx.adaptation.process("add_shortcut_edge", ["mod_django", "mod_postgresql"], apply=True)
    assert ev["status"] == "rejected" and "insufficient" in ev["decision_reason"].lower() or \
        "missing" in ev["decision_reason"].lower()
    assert not any(k == "co_access" for *_, k, d in ctx.graph.graph.edges(keys=True, data=True))


def test_manual_evaluation_records_proposal_without_committing(seeded):
    ctx = seeded
    ctx.tracker.reset_window()
    for _ in range(12):
        ctx.retrieval.query("django url views postgresql indexes vacuum", 6)
    v = ctx.versions.active_id()
    ev = ctx.adaptation.process("add_shortcut_edge", ["mod_django", "mod_postgresql"], apply=False)
    assert ev["status"] == "proposed" and ev["evaluation"]["decision"]["accepted"]
    assert ctx.versions.active_id() == v
    assert not any(k == "co_access" for *_, k, d in ctx.graph.graph.edges(keys=True, data=True))
