from tests.conftest import ingest_docs


def test_ingest_and_query(ctx):
    res = ingest_docs(ctx, ["django", "postgres", "redis"])
    assert all(r["status"] == "completed" for r in res.values())
    out = ctx.retrieval.query("how does django map url patterns to views", 3, True)
    assert out["results"][0]["module_id"] == "mod_django"
    assert out["trace"]["seed_modules"][0]["module_id"] == "mod_django"
    assert ctx.validator.check()["passed"]
