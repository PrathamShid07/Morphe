"""Labeled retrieval evaluation dataset: fixture file <-> MongoDB, with strict validation."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..common import canonical_json, iso, sha256_hex
from ..storage.mongo_store import MongoStore


@dataclass
class EvalQuery:
    query_id: str
    query: str
    relevant_memory_ids: list[str]
    split: str = "dev"
    tags: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    grades: dict[str, int] = field(default_factory=dict)  # optional graded relevance per memory id (default: 1)


@dataclass
class EvalDataset:
    queries: list[EvalQuery]
    dataset_hash: str
    issues: list[str]
    source: str
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return not self.issues and bool(self.queries)

    def summary(self) -> dict[str, Any]:
        splits: dict[str, int] = {}
        for q in self.queries:
            splits[q.split] = splits.get(q.split, 0) + 1
        return {"query_count": len(self.queries), "splits": splits, "dataset_hash": self.dataset_hash,
                "valid": self.valid, "issues": self.issues[:20], "source": self.source, "meta": self.meta}


def parse_dataset(raw: Any, source: str) -> EvalDataset:
    issues: list[str] = []
    queries: list[EvalQuery] = []
    meta: dict[str, Any] = {}
    items = raw
    if isinstance(raw, dict):
        meta = {k: v for k, v in raw.items() if k != "queries"}
        items = raw.get("queries")
    if not isinstance(items, list):
        return EvalDataset([], "", ["dataset must be a list of queries or an object with a 'queries' list"], source, meta)
    seen: set[str] = set()
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            issues.append(f"entry {i} is not an object")
            continue
        qid, text, rel = it.get("query_id"), it.get("query"), it.get("relevant_memory_ids")
        if not isinstance(qid, str) or not qid:
            issues.append(f"entry {i} has no query_id")
            continue
        if qid in seen:
            issues.append(f"duplicate query_id {qid}")
            continue
        seen.add(qid)
        if not isinstance(text, str) or not text.strip():
            issues.append(f"{qid}: empty query text")
            continue
        if not isinstance(rel, list) or not rel or not all(isinstance(r, str) and r for r in rel):
            issues.append(f"{qid}: relevant_memory_ids must be a non-empty list of strings")
            continue
        split = it.get("split", "dev")
        if split not in ("dev", "heldout"):
            issues.append(f"{qid}: split must be 'dev' or 'heldout'")
            continue
        grades = it.get("grades") or {}
        if not isinstance(grades, dict) or any(not isinstance(v, int) or v < 0 for v in grades.values()):
            issues.append(f"{qid}: grades must map ids to non-negative integers")
            continue
        queries.append(EvalQuery(qid, text.strip(), list(dict.fromkeys(rel)), split, list(it.get("tags", [])),
                                 list(it.get("evidence", [])), dict(grades)))
    digest = sha256_hex(canonical_json([[q.query_id, q.query, sorted(q.relevant_memory_ids), q.split]
                                        for q in sorted(queries, key=lambda q: q.query_id)]))[:16]
    return EvalDataset(queries, digest, issues, source, meta)


def load_dataset_file(path: str) -> EvalDataset:
    p = Path(path)
    if not p.is_file():
        return EvalDataset([], "", [f"evaluation dataset file not found: {path}"], str(p))
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return EvalDataset([], "", [f"evaluation dataset could not be read: {exc}"], str(p))
    return parse_dataset(raw, str(p))


class DatasetStore:
    """Evaluation queries live in MongoDB (`evaluation_queries`); the JSON fixture is the reproducible source."""

    def __init__(self, mongo: MongoStore, path: str):
        self.mongo = mongo
        self.path = path

    def load_fixture(self, replace: bool = True) -> EvalDataset:
        ds = load_dataset_file(self.path)
        if not ds.valid:
            return ds  # never replace a good dataset with a broken fixture
        col = self.mongo.col("evaluation_queries")
        if replace:
            col.delete_many({})
        col.insert_many([{**q.__dict__, "dataset_hash": ds.dataset_hash, "loaded_at": iso()} for q in ds.queries])
        self.mongo.set_state("evaluation_dataset_meta", {**ds.meta, "dataset_hash": ds.dataset_hash,
                                                         "loaded_at": iso(), "source": ds.source})
        return ds

    def replace_queries(self, queries: list[EvalQuery], meta: dict[str, Any]) -> EvalDataset:
        """Install a programmatically built labeled set (used by the BEIR benchmark for its validation group)."""
        ds = parse_dataset({"queries": [q.__dict__ for q in queries], **meta}, "programmatic")
        if not ds.valid:
            return ds
        col = self.mongo.col("evaluation_queries")
        col.delete_many({})
        col.insert_many([{**q.__dict__, "dataset_hash": ds.dataset_hash, "loaded_at": iso()} for q in ds.queries])
        self.mongo.set_state("evaluation_dataset_meta", {**meta, "dataset_hash": ds.dataset_hash, "loaded_at": iso(),
                                                         "source": "programmatic"})
        return ds

    def current(self) -> EvalDataset:
        docs = list(self.mongo.col("evaluation_queries").find({}).sort("query_id", 1))
        if not docs:
            return EvalDataset([], "", ["no evaluation dataset has been loaded"], "mongodb")
        raw = [{k: d.get(k) for k in ("query_id", "query", "relevant_memory_ids", "split", "tags", "evidence", "grades")}
               for d in docs]
        ds = parse_dataset(raw, "mongodb")
        ds.meta = self.mongo.get_state("evaluation_dataset_meta", {}) or {}
        return ds
