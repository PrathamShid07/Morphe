"""Normalisation, chunking, validation, deduplication, module assignment, atomic failure."""
import numpy as np
import pytest

from app.common import ValidationFailure
from app.ingestion.chunker import chunk_text, normalize_text
from app.ingestion.embedder import HashingEmbedder, validate_vectors
from tests.conftest import ingest_docs

count = lambda t: len(t.split())  # noqa: E731


def test_normalize_preserves_code_blocks_and_fixes_line_endings():
    raw = "Title  \r\n\r\n\r\n\r\ntext   with   spaces\r\n```python\r\ndef f():\r\n    return   1\r\n```\r\n"
    out = normalize_text(raw)
    assert "\r" not in out and "text with spaces" in out
    assert "    return   1" in out  # code untouched
    assert "\n\n\n" not in out


def test_chunker_respects_sections_and_max_tokens():
    text = "# A\n\n" + " ".join(["alpha"] * 50) + "\n\n# B\n\n" + " ".join(["beta"] * 50)
    chunks = chunk_text(text, count, max_tokens=120, overlap_tokens=10)
    assert [c.section for c in chunks] == ["A", "B"]  # a heading always starts a new chunk
    assert all(c.token_count <= 120 and c.text.strip() for c in chunks)


def test_long_paragraph_is_split_with_overlap_and_nothing_is_lost():
    sents = [f"Sentence number {i} talks about topic {i % 7} in some detail." for i in range(60)]
    chunks = chunk_text(" ".join(sents), count, max_tokens=60, overlap_tokens=12)
    assert len(chunks) > 3 and all(c.token_count <= 60 for c in chunks)
    joined = " ".join(c.text for c in chunks)
    assert all(f"Sentence number {i} " in joined for i in range(60))
    assert chunks[1].text.split(".")[0] in chunks[0].text  # overlap: the next chunk starts with the previous tail


def test_code_block_is_not_split_when_it_fits():
    code = "```\n" + "\n".join(f"line_{i} = {i}" for i in range(10)) + "\n```"
    chunks = chunk_text("Intro paragraph.\n\n" + code, count, max_tokens=200)
    assert len(chunks) == 1 and "line_9 = 9" in chunks[0].text


def test_oversized_single_word_stream_still_terminates():
    chunks = chunk_text("x" * 5000, count, max_tokens=50)
    assert len(chunks) >= 1


def test_embedding_dimension_and_finiteness_are_validated():
    emb = HashingEmbedder(384)
    good = emb.encode(["a b c", "d e f"])
    assert good.shape == (2, 384) and np.allclose(np.linalg.norm(good, axis=1), 1.0, atol=1e-5)
    with pytest.raises(ValidationFailure):
        validate_vectors(np.zeros((2, 100)), 384)
    bad = good.copy()
    bad[0, 0] = np.nan
    with pytest.raises(ValidationFailure):
        validate_vectors(bad, 384)


def test_empty_and_oversized_input_rejected(ctx):
    for text in ("", "   \n\t "):
        with pytest.raises(ValidationFailure) as e:
            ctx.ingestion.ingest("x.txt", text)
        assert e.value.code == "empty_text"
    with pytest.raises(ValidationFailure) as e:
        ctx.ingestion.ingest("x.txt", "a" * (ctx.settings.max_ingest_chars + 1))
    assert e.value.status_code == 413
    with pytest.raises(ValidationFailure):
        ctx.ingestion.ingest("x.txt", "ok", importance="bogus")
    assert ctx.mongo.col("memory_metadata").count_documents({}) == 0


def test_reingesting_identical_content_is_detected_and_ids_are_stable(ctx):
    d = ingest_docs(ctx, ["django"])["django"]
    again = ctx.ingestion.ingest("renamed.md", ctx.qdrant and __import__("tests.corpus", fromlist=["DOCS"]).DOCS["django"]["text"], {"topic": "django"})
    assert again["status"] == "duplicate" and again["source_id"] == d["source_id"]
    assert ctx.mongo.col("memory_metadata").count_documents({"status": "active"}) == d["chunks_created"]
    assert all(m.startswith("mem_" + d["source_id"][4:]) for m in d["memory_ids"])


def test_ingestion_is_idempotent_for_a_repeated_operation_id(ctx):
    a = ctx.ingestion.ingest("a.md", "## T\n\nSome text about redis.", {"topic": "redis"}, operation_id="op_x")
    b = ctx.ingestion.ingest("a2.md", "## T\n\nSome other text about git.", {"topic": "git"}, operation_id="op_x")
    assert a["status"] == "completed" and b["message"] == "operation already completed"
    assert ctx.mongo.col("sources").count_documents({}) == 1


def test_module_assignment_topic_nearest_and_new_source_module(ctx):
    ingest_docs(ctx, ["django", "redis"])
    # no topic: close to redis -> joins mod_redis; unrelated -> new module named after the source
    r1 = ctx.ingestion.ingest("more_redis.md", "## Keys\n\nRedis keys expire with EXPIRE and TTL and eviction policies.")
    r2 = ctx.ingestion.ingest("quantum_notes.md", "## Qubits\n\nQuantum qubits entangle via superposition gates zzzz.")
    assert r1["module_ids"] == ["mod_redis"] and r1["new_module_ids"] == []
    assert r2["new_module_ids"] == ["mod_quantum_notes"]
    assert ctx.graph.graph.nodes["mod_django"]["parent_id"] == "mod_web"  # parent_topic creates a hierarchy edge
    assert ctx.graph.graph.has_edge("mod_web", "mod_django", key="hierarchy")


def test_failed_ingestion_leaves_nothing_active(ctx, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("qdrant exploded")
    monkeypatch.setattr(ctx.qdrant, "set_status", boom)
    with pytest.raises(RuntimeError):
        ctx.ingestion.ingest("x.md", "## A\n\nText one.\n\n## B\n\nText two.", {"topic": "redis"})
    monkeypatch.undo()
    assert ctx.mongo.col("memory_metadata").count_documents({}) == 0
    assert ctx.qdrant.count_memories(None, status=None) == 0
    assert ctx.mongo.col("sources").count_documents({}) == 0
    assert "mod_redis" not in ctx.graph.graph  # newly created module retired again
    assert ctx.validator.check()["passed"]
