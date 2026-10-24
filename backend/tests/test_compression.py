"""Adaptive compression: thresholds, duplicate handling, safe replacement, protection, emergency cleanup."""
import threading

import pytest

from app.common import ConflictError
from app.compression.summarizer import ExtractiveSummarizer, critical_facts, shorten_text
from app.memory.models import ACTIVE, DELETED, SUPERSEDED
from tests.conftest import make_settings
from app.container import build_context

X = "Redis keys expire with EXPIRE and TTL and eviction policies such as allkeys-lru free memory."
Y = "Redis persistence uses RDB snapshots and the append only file AOF to survive restarts."
Z = "Redis pub sub channels broadcast messages to subscribers without storing them at all."


def sec(**kw):
    return "\n\n".join(f"## {h}\n\n{b}" for h, b in kw.items())


@pytest.fixture()
def cctx(tmp_path):
    s = make_settings(tmp_path, near_duplicate_similarity=0.9, summary_group_similarity=0.15,
                      summary_group_min_size=3, min_summary_saving_pct=15, compression_cooldown_seconds=0)
    c = build_context(s)
    yield c
    c.close()


def active_tokens(ctx):
    q = sum(p["token_count"] for _, p, _ in ctx.qdrant.scroll_memories())
    m = ctx.mongo.active_token_total()[0]
    assert q == m, "Qdrant and MongoDB token totals must agree"
    return m


def set_usage(ctx, pct):
    ctx.budget.set_override(int(active_tokens(ctx) / (pct / 100)) + 1)


def ingest_dups(ctx):
    ctx.ingestion.ingest("a.md", sec(Expiry=X, Persistence=Y), {"topic": "redis"})
    ctx.ingestion.ingest("b.md", sec(Expiry=X, PubSub=Z), {"topic": "redis"})  # chunk 'Expiry' identical to a.md's


# ------------------------------------------------------------------ thresholds
def test_normal_threshold_triggers_at_75_and_emergency_at_90(cctx):
    ingest_dups(cctx)
    set_usage(cctx, 60)
    r = cctx.compression.check("t")
    assert not r["triggered"] and "below" in r["reason"]
    set_usage(cctx, 80)
    r = cctx.compression.check("t")
    assert r["triggered"] and r["mode"] == "normal" and r["job"]["tokens_reclaimed"] > 0
    ingest_dups_more = cctx.ingestion.ingest("c.md", sec(Cache="Cache aside reads Redis first and loads on a miss only."),
                                             {"topic": "redis"})
    set_usage(cctx, 95)
    r = cctx.compression.check("t")
    assert r["triggered"] and r["mode"] == "emergency"
    assert r["job"]["usage_after_pct"] <= 95


def test_threshold_configuration_is_validated(tmp_path):
    with pytest.raises(ValueError):
        make_settings(tmp_path, compression_start_pct=95, emergency_cleanup_pct=90)
    with pytest.raises(ValueError):
        make_settings(tmp_path, cleanup_target_pct=95)


# --------------------------------------------------------- exact duplicates
def test_exact_duplicates_are_retired_with_provenance_and_tokens_reclaimed(cctx):
    ingest_dups(cctx)
    before = active_tokens(cctx)
    set_usage(cctx, 80)
    job = cctx.compression.run("normal", trigger="threshold_75_percent")
    assert job["duplicates_removed"] == 1 and job["tokens_reclaimed"] > 0
    assert active_tokens(cctx) == before - job["tokens_reclaimed"]
    sup = cctx.mongo.col("memory_metadata").find_one({"status": SUPERSEDED})
    keeper = cctx.mongo.get_memory_metadata(sup["superseded_by"])
    assert keeper["status"] == ACTIVE and len(keeper["merged_source_ids"]) == 2  # both sources preserved
    # superseded records can no longer be retrieved
    out = cctx.retrieval.query("Redis keys expire EXPIRE TTL eviction", 10)
    assert sup["memory_id"] not in [r["memory_id"] for r in out["results"]]
    assert keeper["memory_id"] in [r["memory_id"] for r in out["results"]]
    ev = cctx.mongo.col("compression_events").find_one({"operation": "exact_duplicate_removal"})
    assert ev["tokens_before"] - ev["tokens_after"] == ev["tokens_reclaimed"] > 0 and ev["status"] == "completed"
    assert cctx.validator.check()["passed"]


def test_near_duplicates_with_same_facts_are_consolidated_but_conflicting_facts_are_not(cctx):
    a = "The Redis server listens on port 6379 by default and supports replication to replicas."
    b = "Basically, the Redis server listens on port 6379 by default and supports replication to replicas."
    conflicting = "The Redis server listens on port 7000 by default and supports replication to replicas."
    cctx.ingestion.ingest("n1.md", sec(Port=a), {"topic": "redis"})
    cctx.ingestion.ingest("n2.md", sec(Port=b), {"topic": "redis"})
    cctx.ingestion.ingest("n3.md", sec(Port=conflicting), {"topic": "redis"})
    set_usage(cctx, 80)
    job = cctx.compression.run("normal")
    retired = [d for d in cctx.mongo.col("memory_metadata").find({"status": SUPERSEDED})]
    texts = {d["memory_id"]: d["text_preview"] for d in cctx.mongo.col("memory_metadata").find({})}
    assert len(retired) == 1 and "7000" not in texts[retired[0]["memory_id"]]
    still = [d["text_preview"] for d in cctx.mongo.col("memory_metadata").find({"status": ACTIVE})]
    assert any("7000" in t for t in still), "a conflicting fact must never be collapsed away"
    assert job["duplicates_removed"] >= 1


# ----------------------------------------------------------- summarisation
WORDY = [
    "Redis keys expire with EXPIRE and TTL. Redis keys expire with EXPIRE and TTL. The default eviction policy is noeviction "
    "unless configured. Basically, it is important to note that maxmemory 256mb limits memory use.",
    "Redis persistence uses RDB snapshots every 3600 seconds. Redis persistence uses RDB snapshots every 3600 seconds. "
    "Basically, the append only file AOF logs each write and can be rewritten.",
    "Redis replication copies data to replicas. Replicas connect with REPLICAOF and sync an RDB snapshot first. "
    "It is important to note that replication is asynchronous by default.",
    "Redis sentinel monitors masters and promotes replicas. Sentinel needs a quorum of 2 sentinels to fail over. "
    "Basically, clients ask sentinel for the current master.",
]


def test_related_records_are_summarised_with_provenance_and_facts_preserved(cctx):
    cctx.ingestion.ingest("wordy.md", sec(**{f"S{i}": t for i, t in enumerate(WORDY)}), {"topic": "redis"})
    before = active_tokens(cctx)
    set_usage(cctx, 80)
    job = cctx.compression.run("normal")
    assert job["summaries_created"] + job["duplicates_removed"] >= 0
    summaries = list(cctx.mongo.col("memory_metadata").find({"kind": {"$in": ["summary", "shortened"]}, "status": ACTIVE}))
    assert summaries, job["operations"]
    s = summaries[0]
    assert s["summary_of"] and all(cctx.mongo.get_memory_metadata(i)["status"] == SUPERSEDED for i in s["summary_of"])
    assert s["merged_source_ids"] and s["source_name"]
    full = cctx.memories.get_memory(s["memory_id"])
    assert {x["memory_id"] for x in full["summary_sources"]} == set(s["summary_of"])
    src_texts = [p["text"] for p, _ in cctx.qdrant.get_memories(s["summary_of"]).values()]  # superseded text is retained
    original_facts = set().union(*(critical_facts(t) for t in src_texts))
    assert original_facts and original_facts <= critical_facts(full["text"]), original_facts - critical_facts(full["text"])
    assert active_tokens(cctx) < before


def test_summarizer_is_deterministic_shorter_and_keeps_exact_facts():
    summ = ExtractiveSummarizer(lambda t: len(t.split()))
    recs = [{"memory_id": f"m{i}", "text": t} for i, t in enumerate(WORDY)]
    r1, r2 = summ.summarize(recs, 40), summ.summarize(recs, 40)
    assert r1.text == r2.text and r1.source_memory_ids == ["m0", "m1", "m2", "m3"]
    assert r1.output_token_count < r1.input_token_count
    assert r1.preserved_facts, r1.missing_facts
    for fact in ("256mb", "3600", "2"):
        assert fact in critical_facts(r1.text)
    assert summ.summarize([], 40).preserved_facts is False


def test_summary_reports_dropped_facts_when_target_too_small():
    summ = ExtractiveSummarizer(lambda t: len(t.split()))
    r = summ.summarize([{"memory_id": "a", "text": "Port 6379 is default. Timeout 30 seconds applies. Token abc_def_ghi rotates."}], 2)
    assert r.warnings  # exact-fact sentences alone exceed the budget -> caller will skip, not truncate


def test_shortening_removes_filler_and_repeats_but_keeps_layout():
    out = shorten_text("## Title\n\nBasically, it is important to note that X holds. X holds. Y holds in order to work.")
    assert out.startswith("## Title\n\n") and "Basically" not in out and out.count("X holds") == 1 and "to work" in out


def test_structured_content_is_never_rewritten(cctx):
    code = "Run this exactly:\n```\nexport API_KEY=abcdef0123456789abcdef0123456789\nredis-cli -p 6379 ping\n```\n" + WORDY[0]
    cctx.ingestion.ingest("code.md", sec(A=code, B=WORDY[1], C=WORDY[2], D=WORDY[3]), {"topic": "redis"})
    set_usage(cctx, 80)
    cctx.compression.run("normal")
    first = cctx.mongo.col("memory_metadata").find_one({"section": "A"})
    assert first["status"] == ACTIVE  # untouched


# -------------------------------------------------- safe replacement / failure
@pytest.mark.parametrize("point", ["after_replacement_write", "before_retire"])
def test_failed_replacement_leaves_originals_active(cctx, point):
    cctx.ingestion.ingest("wordy.md", sec(**{f"S{i}": t for i, t in enumerate(WORDY)}), {"topic": "redis"})
    before = active_tokens(cctx)
    ids_before = {d["memory_id"] for d in cctx.mongo.active_memory_docs()}

    def fail(p):
        if p == point:
            raise RuntimeError("injected failure")
    cctx.compression_exec.fault_hook = fail
    set_usage(cctx, 80)
    job = cctx.compression.run("normal")
    assert active_tokens(cctx) == before
    assert {d["memory_id"] for d in cctx.mongo.active_memory_docs()} == ids_before
    assert not list(cctx.mongo.col("memory_metadata").find({"kind": {"$in": ["summary", "shortened"]}}))
    assert not [m for m, p, _ in cctx.qdrant.scroll_memories(status=None) if p.get("kind") in ("summary", "shortened")]
    failed = list(cctx.mongo.col("compression_events").find({"status": "failed", "scope": "operation"}))
    assert failed and "injected failure" in failed[0]["error"]
    assert cctx.validator.check()["passed"]


def test_failure_while_retiring_originals_reactivates_them_and_removes_replacement(cctx, monkeypatch):
    cctx.ingestion.ingest("wordy.md", sec(**{f"S{i}": t for i, t in enumerate(WORDY)}), {"topic": "redis"})
    before = active_tokens(cctx)
    real = cctx.compression_exec._retire
    monkeypatch.setattr(cctx.compression_exec, "_retire", lambda ids, by: (real(ids[:1], by), (_ for _ in ()).throw(RuntimeError("half retired")))[1])
    set_usage(cctx, 80)
    cctx.compression.run("normal")
    assert active_tokens(cctx) == before
    assert cctx.validator.check()["passed"]


# ----------------------------------------------------------------- emergency
def test_emergency_cleanup_stops_at_target_and_protects_pinned_and_critical(cctx):
    cctx.ingestion.ingest("pin.md", sec(P="Pinned runbook note about redis failover steps and credentials rotation."),
                          {"topic": "redis"}, pinned=True)
    cctx.ingestion.ingest("crit.md", sec(C="Critical note: never flush the production redis database ever."),
                          {"topic": "redis"}, importance="critical")
    cctx.ingestion.ingest("low.md", sec(L1="Obsolete note one about legacy redis cluster nodes and old hosts.",
                                         L2="Obsolete note two about legacy memcached nodes and old hosts.",
                                         L3="Obsolete note three about legacy varnish nodes and old hosts."),
                          {"topic": "redis"}, importance="low")
    set_usage(cctx, 95)
    job = cctx.compression.run("emergency")
    assert job["mode"] == "emergency" and job["records_deleted"] >= 1
    assert job["usage_after_pct"] <= cctx.settings.cleanup_target_pct + 0.01 or job["records_deleted"] == 3
    protected = {d["memory_id"] for d in cctx.mongo.col("memory_metadata").find({"$or": [{"pinned": True}, {"importance": "critical"}]})}
    assert protected and all(cctx.mongo.get_memory_metadata(m)["status"] == ACTIVE for m in protected)
    deleted = list(cctx.mongo.col("memory_metadata").find({"status": DELETED}))
    assert deleted and all(d["importance"] == "low" for d in deleted)  # lowest importance goes first
    assert cctx.validator.check()["passed"]


def test_emergency_with_only_protected_records_deletes_nothing(cctx):
    cctx.ingestion.ingest("pin.md", sec(P="Pinned note one about redis."), {"topic": "redis"}, pinned=True)
    set_usage(cctx, 99)
    job = cctx.compression.run("emergency")
    assert job["records_deleted"] == 0 and job["target_reached"] is False
    assert any(o["status"] == "skipped" for o in job["operations"])
    assert cctx.mongo.active_token_total()[1] == 1


def test_dry_run_simulates_without_changing_anything(cctx):
    ingest_dups(cctx)
    set_usage(cctx, 80)
    before = active_tokens(cctx)
    job = cctx.compression.run("normal", dry_run=True)
    assert job["status"] == "simulated" and job["tokens_reclaimed"] > 0
    assert active_tokens(cctx) == before and cctx.mongo.col("memory_metadata").count_documents({"status": SUPERSEDED}) == 0
    assert cctx.mongo.col("compression_events").count_documents({"status": "simulated"}) >= 1


def test_overlapping_compression_job_is_refused(cctx):
    ingest_dups(cctx)
    set_usage(cctx, 80)
    started, release = threading.Event(), threading.Event()

    def hold():
        with cctx.coordinator.mutate("adaptation"):
            started.set()
            release.wait(5)
    t = threading.Thread(target=hold)
    t.start()
    started.wait(5)
    try:
        with pytest.raises(ConflictError):
            cctx.compression.run("normal")
    finally:
        release.set()
        t.join()
    assert cctx.compression.run("normal")["status"] in ("completed", "failed")


def test_budget_override_is_validated_and_restorable(cctx):
    assert cctx.budget.set_override(5000) == 5000
    assert cctx.budget.set_override(None) == cctx.settings.memory_budget_tokens
    with pytest.raises(Exception):
        cctx.budget.set_override(-1)
