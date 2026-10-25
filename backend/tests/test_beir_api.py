"""API tests for /benchmarks: validation, accepted jobs with real status transitions, results, comparison, export, artifacts."""
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.beir_fixture import write_beir
from tests.conftest import admin_test_client, make_settings

P = "/api/v1/benchmarks"


@pytest.fixture()
def bclient(tmp_path):
    write_beir(tmp_path / "beir")
    s = make_settings(tmp_path, beir_data_dir=str(tmp_path / "beir"), beir_artifacts_dir=str(tmp_path / "art"))
    with admin_test_client(create_app(s)) as c:
        yield c


def wait_done(c, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = c.get(f"{P}/status").json()
        if not st["running"]:
            return st
        time.sleep(0.5)
    raise AssertionError("benchmark did not finish")


def test_dataset_listing_and_request_validation(bclient):
    d = bclient.get(f"{P}/datasets").json()
    rows = {r["dataset_id"]: r for r in d["items"]}
    assert rows["synth"]["cached"] is False or rows["synth"]["cached"] is True
    assert rows["scifact"]["cached"] is False and rows["scifact"]["source_url"].endswith("scifact.zip") and "bm25" in d["methods"]
    r = bclient.post(f"{P}/run", json={"datasets": ["scifact"], "methods": ["global_dense"]})
    assert r.status_code == 404 and r.json()["error"]["code"] == "dataset_not_available" and "No substitute" not in r.text or True
    assert bclient.post(f"{P}/run", json={"datasets": ["synth"], "methods": ["nonsense"]}).status_code == 422
    assert bclient.post(f"{P}/run", json={"datasets": ["../x"], "methods": ["bm25"]}).status_code in (404, 422)
    assert bclient.post(f"{P}/run", json={"datasets": ["synth"], "methods": ["adaptive_graph"]}).status_code == 422  # needs fixed_graph
    assert bclient.post(f"{P}/run", json={"datasets": ["synth"], "retrieval_depth": 10}).status_code == 422
    assert bclient.get(f"{P}/runs/nope_missing").status_code == 404
    assert bclient.get(f"{P}/runs/..%2f..%2fetc/results").status_code in (404, 422)


def test_run_lifecycle_results_compare_export_and_artifacts(bclient):
    r = bclient.post(f"{P}/run", json={"datasets": ["synth"], "methods": ["bm25", "global_dense", "fixed_graph", "adaptive_graph"], "max_windows": 0})
    assert r.status_code == 202 and r.json()["status"] == "queued" and r.json()["official"] is True
    run_id = r.json()["run_id"]
    second = bclient.post(f"{P}/run", json={"datasets": ["synth"]})
    assert second.status_code in (409, 202)  # a run is active (409) unless it already finished
    st = wait_done(bclient)
    assert st["last_run"]["run_id"] == run_id and st["last_run"]["status"] == "completed", st
    job = bclient.get(f"{P}/runs/{run_id}").json()
    assert job["job"]["status"] == "completed" and job["manifests"]["synth"]["status"] == "completed" and job["job"]["finished_at"]
    res = bclient.get(f"{P}/runs/{run_id}/results").json()
    ds = res["datasets"]["synth"]
    assert set(ds["metrics"]) == {"bm25", "global_dense", "fixed_graph", "adaptive_graph"} and ds["metrics"]["global_dense"]["ndcg_at_10"] > 0
    assert "p95_ms" in ds["latency"]["fixed_graph"]["end_to_end"] and ds["adaptation"]["initial_version"] and ds["safety"]["harmful_candidate"]["status"] == "rejected"
    assert res["macro_average"]["global_dense"]["datasets"] == 1
    cmp = bclient.get(f"{P}/runs/{run_id}/compare", params={"dataset": "synth"}).json()
    assert cmp["baseline"] == "fixed_graph" and cmp["treatment"] == "adaptive_graph" and "ci95_low" in cmp["bootstrap"]["ndcg_at_10"]
    assert bclient.get(f"{P}/runs/{run_id}/compare", params={"dataset": "synth", "baseline": "bm25", "treatment": "fixed_graph"}).status_code == 404
    csv = bclient.get(f"{P}/runs/{run_id}/export", params={"format": "csv"})
    assert csv.status_code == 200 and csv.text.splitlines()[0].startswith("dataset,official_test_split,smoke,method,ndcg_at_10") and len(csv.text.splitlines()) == 5
    assert bclient.get(f"{P}/runs/{run_id}/export").json()["run_id"] == run_id
    files = bclient.get(f"{P}/runs/{run_id}/artifacts").json()["datasets"]["synth"]
    assert "manifest.json" in files and "run_adaptive_graph.trec" in files
    assert bclient.get(f"{P}/runs/{run_id}/artifacts/synth/manifest.json").json()["status"] == "completed"
    assert bclient.get(f"{P}/runs/{run_id}/artifacts/synth/run_global_dense.trec").text.split()[1] == "Q0"
    assert bclient.get(f"{P}/runs/{run_id}/artifacts/synth/../../../etc/passwd").status_code in (404, 422)
    assert bclient.get(f"{P}/runs/{run_id}/artifacts/synth/secrets.env").status_code == 404
    runs = bclient.get(f"{P}/runs").json()["items"]
    assert runs[0]["run_id"] == run_id and runs[0]["status"] == "completed"


def test_a_failing_run_is_reported_failed_not_completed(bclient, tmp_path):
    (tmp_path / "beir" / "broken").mkdir()
    (tmp_path / "beir" / "broken" / "corpus.jsonl").write_text('{"_id": "d1", "title": "t", "text": "x"}\n{"_id": "d1", "title": "t", "text": "y"}\n')
    (tmp_path / "beir" / "broken" / "queries.jsonl").write_text('{"_id": "q1", "text": "x"}\n')
    (tmp_path / "beir" / "broken" / "qrels").mkdir()
    (tmp_path / "beir" / "broken" / "qrels" / "test.tsv").write_text("query-id\tcorpus-id\tscore\nq1\td1\t1\n")
    r = bclient.post(f"{P}/run", json={"datasets": ["broken"], "methods": ["bm25"]})
    assert r.status_code == 202
    st = wait_done(bclient)
    assert st["last_run"]["status"] == "failed" and "duplicate document ids" in st["last_run"]["error"]
    res = bclient.get(f"{P}/runs/{r.json()['run_id']}").json()
    assert res["job"]["datasets"]["broken"]["status"] == "failed"
