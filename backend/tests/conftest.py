"""Shared fixtures. Tests use the REAL algorithms; only external boundaries are replaced:
a deterministic hashing embedder (no model download), in-memory Qdrant and mongomock instead of MongoDB."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.container import build_context  # noqa: E402
from app.main import create_app  # noqa: E402
from app.common import configure_logging  # noqa: E402
from tests.corpus import DOCS, build_eval_fixture  # noqa: E402

configure_logging("ERROR")


def make_settings(tmp_path, **over) -> Settings:
    base = dict(app_env="test", embedding_backend="hashing", mongo_backend="mongomock", qdrant_mode="memory",
                compression_mode="inline", adaptation_inline=True, auto_adaptation=False,
                eval_dataset_path=str(tmp_path / "eval.json"), fallback_min_score=0.12, min_eval_queries=6,
                split_mixed_min_records=12, split_min_group_size=3, split_min_silhouette=0.0,
                split_mixed_min_silhouette=0.0, coaccess_min_count=5, coaccess_min_window_queries=10,
                adaptation_query_batch=20, eval_repetitions=1, latency_noise_floor_ms=50.0,
                seed_module_count=2, max_candidate_modules=3, merge_profile_similarity=0.6, merge_pair_similarity=0.6, log_level="WARNING", allow_validation_override=True)
    base.update(over)
    return Settings(**base)


@pytest.fixture()
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture()
def ctx(settings):
    c = build_context(settings)
    yield c
    c.close()


def ingest_docs(ctx, names=None):
    out = {}
    for name, d in DOCS.items():
        if names and name not in names:
            continue
        out[name] = ctx.ingestion.ingest(d["source_name"], d["text"], d["metadata"])
    return out


@pytest.fixture()
def seeded(ctx, tmp_path):
    """Core corpus ingested, labeled dataset written and loaded, baseline checkpoint taken."""
    ingest_docs(ctx, [n for n, d in DOCS.items() if d["set"] == "core"])
    fixture = build_eval_fixture(ctx)
    Path(ctx.settings.eval_dataset_path).write_text(json.dumps(fixture))
    ctx.datasets.load_fixture()
    with ctx.coordinator.mutate("checkpoint"):
        ctx.lifecycle.ensure_checkpoint("test baseline")
    return ctx


@pytest.fixture()
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c
