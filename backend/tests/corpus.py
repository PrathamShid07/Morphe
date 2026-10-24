"""Tiny labeled corpus for tests. Sections start new chunks, so one section == one memory record."""
from __future__ import annotations

from typing import Any


def doc(name: str, sections: dict[str, str], topic: str, parent: str, set_: str = "core") -> dict[str, Any]:
    text = "\n\n".join(f"## {h}\n\n{b}" for h, b in sections.items())
    return {"source_name": name, "text": text, "set": set_,
            "metadata": {"topic": topic, "parent_topic": parent}}


DOCS: dict[str, dict[str, Any]] = {
    "django": doc("django_routing.md", {
        "URL routing": "Django maps URL patterns to views using path() and re_path() inside urls.py urlpatterns.",
        "Views": "A Django view receives an HttpRequest and returns an HttpResponse; class based views use as_view().",
        "Templates": "Django templates render context with the template language, loops, filters and inheritance blocks.",
        "Middleware": "Django middleware wraps request and response processing, for example sessions and csrf protection.",
    }, "django", "web"),
    "fastapi": doc("fastapi_guide.md", {
        "Path operations": "FastAPI declares path operations with decorators such as app.get and app.post on async functions.",
        "Validation": "FastAPI validates request bodies with Pydantic models and returns automatic 422 errors.",
        "Dependencies": "FastAPI dependency injection uses Depends to share database sessions and authentication.",
        "Lifespan": "FastAPI lifespan handlers open and close clients when the uvicorn ASGI server starts and stops.",
    }, "fastapi", "web"),
    "postgres": doc("postgresql_basics.md", {
        "Indexes": "PostgreSQL supports btree, hash and gin indexes; EXPLAIN ANALYZE shows the query plan and costs.",
        "Vacuum": "PostgreSQL vacuum reclaims dead tuples and autovacuum runs automatically on the default port 5432.",
        "Replication": "PostgreSQL streaming replication ships write ahead log records to standby replicas.",
        "Connections": "PostgreSQL connection pooling with pgbouncer reduces the cost of many client connections.",
    }, "postgresql", "data"),
    "redis": doc("redis_caching.md", {
        "Expiry": "Redis keys expire with EXPIRE and TTL, and eviction policies such as allkeys-lru free memory.",
        "Persistence": "Redis persistence uses RDB snapshots and the append only file AOF to survive restarts.",
        "Pub sub": "Redis publish subscribe channels broadcast messages to subscribers without storing them.",
        "Cache aside": "The cache aside pattern reads Redis first and loads the database on a miss.",
    }, "redis", "data"),
    "docker": doc("docker_basics.md", {
        "Images": "A Docker image is built from a Dockerfile; each instruction creates a cached layer.",
        "Compose": "Docker compose describes multi container applications, networks and volumes in YAML.",
        "Volumes": "Docker volumes persist container data outside the writable container layer.",
        "Networking": "Docker bridge networks let containers resolve each other by service name.",
    }, "docker", "devops"),
    "git": doc("git_workflow.md", {
        "Branching": "Git branches are cheap pointers; feature branches isolate work until a pull request merges them.",
        "Rebase": "Git rebase replays commits on a new base while merge keeps history with a merge commit.",
        "Stash": "Git stash saves uncommitted changes temporarily; reflog helps recover lost commits.",
        "Cherry pick": "Git cherry-pick applies a single commit from another branch onto the current branch.",
    }, "git", "devops"),
    # --- merge scenario: redundant with postgresql_basics (same facts, reworded; no conflicting numbers)
    "postgres_dup": doc("postgres_tuning_notes.md", {
        "Index types": "PostgreSQL supports btree, hash and gin indexes; use EXPLAIN ANALYZE to see the query plan and costs.",
        "Autovacuum": "Autovacuum in PostgreSQL reclaims dead tuples automatically and the server listens on port 5432 by default.",
        "Replicas": "PostgreSQL streaming replication sends write ahead log records to standby replicas.",
        "Pooling": "With pgbouncer connection pooling PostgreSQL handles many client connections more cheaply.",
    }, "postgres", "data", "merge"),
    # --- mixed scenario: three unrelated sources under ONE topic -> overloaded mixed-topic module
    "py_web": doc("python_web_notes.md", {
        "WSGI": "WSGI servers such as gunicorn run synchronous Python web applications and Flask apps.",
        "ASGI": "ASGI servers like uvicorn run asynchronous Python web applications with websockets.",
        "Flask": "Flask routes decorators map URLs to Python view functions with blueprints.",
        "Sessions": "Python web sessions store signed cookies and server side user state.",
        "Jinja": "Jinja2 templates render HTML pages for Python web frameworks.",
    }, "python", "languages", "mixed"),
    "py_data": doc("python_data_notes.md", {
        "NumPy arrays": "NumPy ndarray stores homogeneous numeric data and supports vectorized broadcasting.",
        "Pandas frames": "Pandas DataFrame offers groupby, merge and pivot operations on tabular data.",
        "Matplotlib": "Matplotlib plots figures and axes for line charts, scatter plots and histograms.",
        "Missing values": "Pandas fillna and dropna handle missing values in numeric columns.",
        "Scikit-learn": "Scikit-learn trains classifiers and regressors with fit and predict, KMeans clusters data.",
    }, "python", "languages", "mixed"),
    "py_tools": doc("python_tooling_notes.md", {
        "Virtualenv": "A Python venv isolates project dependencies; pip installs packages into it.",
        "Pytest": "Pytest discovers tests, uses fixtures and parametrize to run test functions.",
        "Packaging": "Python packaging builds wheels with pyproject.toml and publishes to PyPI.",
        "Linting": "Ruff and mypy lint and type check Python source code before commits.",
        "Debugging": "The pdb debugger sets breakpoints and steps through Python code.",
    }, "python", "languages", "mixed"),
}

# (query, evidence substring that must be in the relevant chunk, doc key, split)
QUERIES = [
    ("how does django map url patterns to views", "path() and re_path()", "django", "dev"),
    ("django class based views as_view", "class based views use as_view()", "django", "dev"),
    ("django middleware csrf sessions", "Django middleware wraps", "django", "heldout"),
    ("fastapi pydantic request body validation", "validates request bodies with Pydantic", "fastapi", "dev"),
    ("fastapi dependency injection Depends database sessions", "dependency injection uses Depends", "fastapi", "dev"),
    ("fastapi lifespan startup uvicorn clients", "lifespan handlers", "fastapi", "heldout"),
    ("postgresql btree gin indexes explain analyze", "supports btree, hash and gin", "postgres", "dev"),
    ("postgresql vacuum dead tuples default port", "reclaims dead tuples and autovacuum", "postgres", "dev"),
    ("postgresql streaming replication standby write ahead log", "streaming replication ships", "postgres", "heldout"),
    ("redis expire ttl eviction policy lru", "Redis keys expire", "redis", "dev"),
    ("redis rdb snapshots append only file", "RDB snapshots and the append only", "redis", "dev"),
    ("redis pub sub channels subscribers", "publish subscribe channels", "redis", "heldout"),
    ("docker image dockerfile cached layers", "built from a Dockerfile", "docker", "dev"),
    ("docker compose multi container yaml", "Docker compose describes", "docker", "dev"),
    ("docker volumes persist container data", "Docker volumes persist", "docker", "heldout"),
    ("git rebase versus merge commit history", "Git rebase replays", "git", "dev"),
    ("git stash reflog recover commits", "Git stash saves", "git", "dev"),
    ("git cherry-pick single commit", "Git cherry-pick applies", "git", "heldout"),
]
MIXED_QUERIES = [
    ("wsgi gunicorn flask synchronous applications", "WSGI servers such as gunicorn", "py_web", "dev"),
    ("pandas dataframe groupby merge pivot", "Pandas DataFrame offers", "py_data", "dev"),
    ("pytest fixtures parametrize discovery", "Pytest discovers tests", "py_tools", "dev"),
    ("numpy ndarray broadcasting vectorized", "NumPy ndarray stores", "py_data", "heldout"),
]


def build_eval_fixture(ctx, include_mixed: bool = False) -> dict[str, Any]:
    """Resolve evidence snippets to memory IDs from the currently stored chunks (labels are verified, not guessed)."""
    texts = {mid: p.get("text", "") for mid, p, _ in ctx.qdrant.scroll_memories()}
    out = []
    for i, (q, ev, key, split) in enumerate(QUERIES + (MIXED_QUERIES if include_mixed else [])):
        ids = sorted(m for m, t in texts.items() if ev in t)
        if not ids:
            continue
        out.append({"query_id": f"q{i:03d}", "query": q, "relevant_memory_ids": ids, "split": split, "tags": [key],
                    "evidence": [ev]})
    return {"name": "test fixture", "queries": out}
