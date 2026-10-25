"""Shared fixtures. Tests use the REAL algorithms; only external boundaries are replaced:
a deterministic hashing embedder (no model download), in-memory Qdrant and mongomock instead of MongoDB."""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth.passwords import fast_hasher, hash_password  # noqa: E402
from app.config import Settings  # noqa: E402
from app.container import build_context  # noqa: E402
from app.main import create_app  # noqa: E402
from app.common import configure_logging  # noqa: E402
from tests.corpus import DOCS, build_eval_fixture  # noqa: E402

configure_logging("ERROR")

# Test-only administrator account. The hash uses deliberately cheap Argon2 parameters so the suite stays fast; this password
# belongs to the throw-away in-memory test apps and is unrelated to any real credential.
ADMIN_USER = "test-admin"
ADMIN_PASSWORD = "test-only-Passw0rd!-not-a-real-credential"
SESSION_SECRET = "t" * 48
_ADMIN_HASH = hash_password(ADMIN_PASSWORD, hasher=fast_hasher())


def make_settings(tmp_path, **over) -> Settings:
    base = dict(app_env="test", embedding_backend="hashing", mongo_backend="mongomock", qdrant_mode="memory",
                compression_mode="inline", adaptation_inline=True, auto_adaptation=False,
                eval_dataset_path=str(tmp_path / "eval.json"), fallback_min_score=0.12, min_eval_queries=6,
                split_mixed_min_records=12, split_min_group_size=3, split_min_silhouette=0.0,
                split_mixed_min_silhouette=0.0, admin_username=ADMIN_USER, admin_password_hash=_ADMIN_HASH, session_secret=SESSION_SECRET,
                coaccess_min_count=5, coaccess_min_window_queries=10,
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


def login_admin(c: TestClient, username: str = ADMIN_USER, password: str = ADMIN_PASSWORD) -> TestClient:
    """Sign the client in as the administrator exactly like the SPA does: cookie session + CSRF header."""
    r = c.post("/api/v1/auth/admin/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


USER_PASSWORD = "Correct-Horse-Battery-9"  # throw-away password for in-memory test accounts


def register_user(c: TestClient, name: str = "Alice Tester", email: str = "alice@example.com", password: str = USER_PASSWORD) -> dict:
    """Create and sign in a regular account exactly like the SPA does: cookie session + CSRF header."""
    r = c.post("/api/v1/auth/register", json={"full_name": name, "email": email, "password": password, "confirm_password": password})
    assert r.status_code == 201, r.text
    body = r.json()
    c.headers["X-CSRF-Token"] = body["csrf_token"]
    return body


def login_user(c: TestClient, email: str, password: str = USER_PASSWORD) -> dict:
    r = c.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return r.json()


@contextmanager
def admin_test_client(app):
    with TestClient(app) as c:
        yield login_admin(c)


@pytest.fixture()
def client(settings):
    """Administrator-authenticated client (the existing API tests exercise administrative routes)."""
    with admin_test_client(create_app(settings)) as c:
        yield c


@pytest.fixture()
def anon_client(settings):
    """A first-time visitor: no administrator session."""
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture()
def user_client(settings):
    """A registered, signed-in ordinary user."""
    with TestClient(create_app(settings)) as c:
        register_user(c)
        yield c


@pytest.fixture()
def world(ctx, settings):
    """One shared context (one database) and a factory for independent browsers: separate cookie jars, same backend."""
    app = create_app(settings, ctx)
    opened: list[TestClient] = []

    class World:
        pass

    w = World()
    w.ctx, w.app, w.settings = ctx, app, settings

    def browser() -> TestClient:
        c = TestClient(app)
        c.__enter__()
        opened.append(c)
        return c

    def user(name: str, email: str) -> TestClient:
        c = browser()
        register_user(c, name, email)
        return c

    def admin() -> TestClient:
        return login_admin(browser())

    w.browser, w.user, w.admin = browser, user, admin
    yield w
    for c in opened:
        c.__exit__(None, None, None)
