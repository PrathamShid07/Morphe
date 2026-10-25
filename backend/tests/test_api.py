"""HTTP contract: status codes, error shape, request validation, end-to-end flows through the real routers."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import admin_test_client, make_settings
from tests.corpus import DOCS, build_eval_fixture

P = "/api/v1"


def seed(client):
    for name, d in DOCS.items():
        if d["set"] == "core":
            r = client.post(f"{P}/ingestion", json={"source_name": d["source_name"], "text": d["text"],
                                                    "metadata": d["metadata"]})
            assert r.status_code == 200, r.text
    ctx = client.app.state.ctx
    Path(ctx.settings.eval_dataset_path).write_text(json.dumps(build_eval_fixture(ctx)))
    assert client.post(f"{P}/evaluation/dataset/load").json()["valid"]
    assert client.post(f"{P}/versions/checkpoint").status_code == 200
    return ctx


def test_health_reports_dependencies(client):
    r = client.get("/health")
    body = r.json()
    assert r.status_code == 200 and body["ready"] and body["checks"]["mongodb"]["ok"] and body["checks"]["qdrant"]["ok"]
    assert body["checks"]["embedding_model"]["dimension"] == 384 and body["architecture_version"] == "arch_0001"


def test_error_shape_validation_and_request_id(client):
    r = client.post(f"{P}/ingestion", json={"source_name": "", "text": ""})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    assert r.json()["error"]["request_id"] and r.headers["x-request-id"] == r.json()["error"]["request_id"]
    r = client.get(f"{P}/memories/mem_nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "memory_not_found"
    r = client.post(f"{P}/ingestion", json={"source_name": "x", "text": "a" * 1_000_001})
    assert r.status_code == 413 and r.json()["error"]["code"] == "text_too_large"
    assert client.post(f"{P}/query", json={"query": "x", "top_k": 500}).status_code == 422
    assert "stack" not in json.dumps(r.json()).lower()


def test_request_size_limit(tmp_path):
    app = create_app(make_settings(tmp_path, max_request_bytes=500))
    with admin_test_client(app) as c:
        r = c.post(f"{P}/ingestion", json={"source_name": "x", "text": "a" * 2000})
        assert r.status_code == 413 and r.json()["error"]["code"] == "request_too_large"


def test_cors_allows_only_configured_origins(client):
    ok = client.options(f"{P}/query", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options(f"{P}/query", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in bad.headers


def test_ingest_query_memories_modules_graph_flow(client):
    seed(client)
    q = client.post(f"{P}/query", json={"query": "how does django map url patterns to views", "top_k": 3, "debug_trace": True})
    body = q.json()
    assert q.status_code == 200 and body["results"][0]["module_id"] == "mod_django"
    assert body["trace"]["candidate_count"] > 0 and body["trace"]["seed_modules"][0]["module_id"] == "mod_django"
    assert body["architecture_version"] and body["query_id"]
    assert client.post(f"{P}/query", json={"query": "git stash"}).json().get("trace") is None

    lst = client.get(f"{P}/memories", params={"module_id": "mod_django", "limit": 2, "offset": 1}).json()
    assert lst["total"] == 4 and len(lst["items"]) == 2 and "text" not in lst["items"][0]
    assert all(i["module_name"] == "Django" for i in lst["items"])
    mid = body["results"][0]["memory_id"]
    d = client.get(f"{P}/memories/{mid}").json()
    assert d["text"] and d["access_count"] >= 1 and d["status"] == "active" and "_id" not in d

    mods = client.get(f"{P}/modules").json()["items"]
    assert {m["module_id"] for m in mods} >= {"mod_django", "mod_web"}
    det = client.get(f"{P}/modules/mod_django").json()
    assert det["parent"]["module_id"] == "mod_web" and det["memories"] and det["neighbors"]
    g = client.get(f"{P}/graph").json()
    assert {n["id"] for n in g["nodes"]} >= {"mod_django", "mod_web"} and g["edges"][0]["data"]["relationType"] == "hierarchy"


def test_pinned_memory_needs_force_to_delete_and_patch_validates(client):
    seed(client)
    mid = client.get(f"{P}/memories", params={"limit": 1}).json()["items"][0]["memory_id"]
    assert client.patch(f"{P}/memories/{mid}", json={"pinned": True, "importance": "high"}).json()["pinned"] is True
    assert client.patch(f"{P}/memories/{mid}", json={"importance": "bogus"}).status_code == 422
    r = client.delete(f"{P}/memories/{mid}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "protected_memory"
    r = client.delete(f"{P}/memories/{mid}", params={"force": True})
    assert r.status_code == 200 and r.json()["status"] == "deleted" and r.json()["active_tokens_reclaimed"] > 0
    assert client.get(f"{P}/memories/{mid}").json()["status"] == "deleted"
    assert client.delete(f"{P}/memories/{mid}").status_code == 404


def test_file_ingestion(client):
    r = client.post(f"{P}/ingestion/file", files={"file": ("notes.md", b"## Hi\n\nRedis keys expire with TTL.", "text/markdown")},
                    data={"topic": "redis"})
    assert r.status_code == 200 and r.json()["module_ids"] == ["mod_redis"]
    r = client.post(f"{P}/ingestion/file", files={"file": ("bin.dat", b"\xff\xfe\x00\x01", "application/octet-stream")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unsupported_file"


def test_metrics_never_report_unavailable_values_as_zero(client):
    m = client.get(f"{P}/metrics/summary").json()
    assert m["p95_latency_ms"] is None and "Not enough data" in m["latency_note"]
    assert m["retrieval_quality"] is None and m["retrieval_quality_note"]
    assert m["average_candidate_count"] is None and m["fallback_rate"] is None
    seed(client)
    for _ in range(6):
        client.post(f"{P}/query", json={"query": "docker volumes persist"})
    m = client.get(f"{P}/metrics/summary").json()
    assert m["p95_latency_ms"] > 0 and m["latency_samples"] >= 6 and m["process_rss_mb"] > 0
    run = client.post(f"{P}/evaluation/run", json={"mode": "graph"}).json()
    assert run["valid"] and 0 <= run["metrics"]["recall_at_5"] <= 1 and run["conditions"]["queries_evaluated"] >= 10
    m = client.get(f"{P}/metrics/summary").json()
    assert m["retrieval_quality"]["metrics"]["recall_at_5"] == run["metrics"]["recall_at_5"]
    h = client.get(f"{P}/metrics/history", params={"window": "1h"}).json()
    assert h["points"] and h["evaluation_points"]
    assert client.get(f"{P}/metrics/history", params={"window": "9y"}).status_code == 422


def test_evaluation_endpoints_compare_modes_and_versions(client):
    seed(client)
    ds = client.get(f"{P}/evaluation/dataset").json()
    assert ds["valid"] and ds["usable_queries"] >= 10 and ds["thresholds"]["max_recall_regression"] == 0.02
    cm = client.post(f"{P}/evaluation/compare-modes").json()
    assert set(cm["modes"]) == {"global", "fixed_graph", "adaptive_graph"}
    assert cm["modes"]["global"]["metrics"]["mean_candidate_count"] >= cm["modes"]["adaptive_graph"]["metrics"]["mean_candidate_count"]
    runs = client.get(f"{P}/evaluation/runs").json()
    assert runs["total"] >= 3 and "per_query" not in runs["items"][0]
    assert client.get(f"{P}/evaluation/runs/{runs['items'][0]['run_id']}").json()["per_query"]
    assert client.get(f"{P}/evaluation/runs/nope").status_code == 404


def test_adaptation_endpoints_and_version_restore_flow(client):
    ctx = seed(client)
    for name in ("py_web", "py_data", "py_tools"):
        d = DOCS[name]
        client.post(f"{P}/ingestion", json={"source_name": d["source_name"], "text": d["text"], "metadata": d["metadata"]})
    Path(ctx.settings.eval_dataset_path).write_text(json.dumps(build_eval_fixture(ctx, True)))
    client.post(f"{P}/evaluation/dataset/load")
    st = client.get(f"{P}/adaptation/status").json()
    assert st["queries_per_window"] == 20 and st["rollback_enabled"] and st["thresholds"]["max_recall_regression"] == 0.02
    cands = client.get(f"{P}/adaptation/candidates").json()
    assert any(p["operation"] == "split_module" and p["reason"] for p in cands["proposals"])
    body = {"operation": "split_module", "module_ids": ["mod_python"]}
    ev = client.post(f"{P}/adaptation/evaluate", json=body).json()
    assert ev["status"] == "proposed" and ev["evaluation"]["decision"]["checks"]
    assert client.get(f"{P}/adaptation/events", params={"status": "proposed"}).json()["total"] == 1
    base_version = client.get(f"{P}/versions/active").json()["version_id"]
    applied = client.post(f"{P}/adaptation/events/{ev['event_id']}/apply").json()
    assert applied["status"] == "completed" and applied["new_version"]
    counts = client.get(f"{P}/adaptation/events").json()["status_counts"]
    assert counts["completed"] == 1 and counts["proposed"] == 1

    versions = client.get(f"{P}/versions").json()
    assert versions["active_version"] == applied["new_version"] and versions["items"][0]["is_active"]
    assert all(v["restorable"] for v in versions["items"])
    det = client.get(f"{P}/versions/{applied['new_version']}").json()
    assert det["event"]["event_id"] == applied["event_id"] and det["modules"] and det["parent_version_id"]
    assert client.get(f"{P}/versions/{applied['new_version']}/evaluation").json()["evaluation"]["decision"] == "accept"
    cmp = client.get(f"{P}/versions/compare", params={"from_version": applied["before_version"], "to_version": applied["new_version"]}).json()
    assert cmp["diff"]["modules_added"] and cmp["evaluation"]["decision"]["accepted"]

    prev = client.get(f"{P}/versions/{applied['before_version']}/restore-preview").json()
    assert prev["restorable"] and prev["diff"]["modules_removed"] and prev["current_version"]["version_id"] == applied["new_version"]
    res = client.post(f"{P}/versions/{applied['before_version']}/restore", json={"reason": "api test"})
    assert res.status_code == 200 and res.json()["status"] == "completed"
    assert client.get(f"{P}/adaptation/events/{applied['event_id']}").json()["status"] == "rolled_back"
    assert client.get(f"{P}/integrity").json()["passed"]
    assert client.post(f"{P}/versions/arch_9999/restore").status_code == 404


def test_restore_of_corrupt_version_fails_visibly(client):
    ctx = seed(client)
    vid = ctx.versions.active_id()
    ctx.mongo.col("architecture_snapshots").update_one({"version_id": vid}, {"$set": {"checksum": "bad"}})
    r = client.post(f"{P}/versions/{vid}/restore")
    assert r.status_code == 500 and r.json()["error"]["code"] == "snapshot_invalid"
    assert client.get(f"{P}/versions/{vid}").json()["restorable"] is False
    ev = client.get(f"{P}/adaptation/events", params={"status": "failed"}).json()
    assert ev["total"] == 1 and "not restorable" in ev["items"][0]["error"]


def test_compression_endpoints_and_admin_guards(client):
    seed(client)
    assert client.get(f"{P}/compression/status").json()["state"] == "idle"
    r = client.put(f"{P}/compression/budget", json={"budget_tokens": 450})
    assert r.status_code == 200 and r.json()["budget_tokens"] == 450 and r.json()["usage_percent"] > 90
    sim = client.post(f"{P}/compression/run", json={"mode": "auto", "dry_run": True}).json()
    assert sim["status"] == "simulated" and sim["mode"] == "emergency"
    ev = client.get(f"{P}/compression/events", params={"scope": "job"}).json()
    assert ev["total"] >= 1 and ev["items"][0]["status"] == "simulated"
    assert client.post(f"{P}/admin/reset", json={"confirm": "no"}).status_code == 422
    r = client.post(f"{P}/admin/reset", json={"confirm": "RESET"})
    assert r.status_code == 200 and client.get(f"{P}/metrics/summary").json()["active_memory_count"] == 0


def test_admin_endpoints_can_be_disabled(tmp_path):
    app = create_app(make_settings(tmp_path, allow_admin_endpoints=False))
    with admin_test_client(app) as c:
        assert c.post(f"{P}/admin/reset", json={"confirm": "RESET"}).status_code == 403
        assert c.put(f"{P}/compression/budget", json={"budget_tokens": 10}).status_code == 403
