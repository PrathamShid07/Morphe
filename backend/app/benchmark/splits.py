"""Leakage-free, deterministic query-group protocol.

Groups: **workload** (queries only — drives access/co-access events), **validation** (qrels used only to validate candidate
architectures) and **test** (frozen; evaluated once after the architecture is frozen). The groups are pairwise disjoint by
query id and by query text. Nothing in the architecture is ever derived from `test` judgments.
"""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from typing import Any

from .datasets import BeirData

WORKLOAD_CAP = 500


@dataclass
class SplitProtocol:
    dataset_id: str
    seed: int
    workload: list[str]
    validation: list[str]
    test: list[str]
    provenance: dict[str, str]
    official_test: bool  # True only if `test` is the complete official test split
    truncated: bool
    notes: str = ""
    dropped_for_text_overlap: int = 0
    pools: dict[str, str] = field(default_factory=dict)  # group -> which official split its qrels/queries come from

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["sizes"] = {"workload": len(self.workload), "validation": len(self.validation), "test": len(self.test)}
        return d


def _shuffled(ids: list[str], seed: int, salt: str) -> list[str]:
    rng = random.Random(f"{seed}:{salt}")
    out = sorted(ids)
    rng.shuffle(out)
    return out


def assert_disjoint(p: SplitProtocol, data: BeirData | None = None) -> None:
    g = {"workload": set(p.workload), "validation": set(p.validation), "test": set(p.test)}
    for a in g:
        for b in g:
            if a < b and g[a] & g[b]:
                raise AssertionError(f"leakage: {a} and {b} query groups overlap on {len(g[a] & g[b])} queries")
    if data is not None:
        texts = {name: {_q(data, qid) for qid in ids} for name, ids in (("workload", p.workload), ("validation", p.validation), ("test", p.test))}
        for a in texts:
            for b in texts:
                if a < b and texts[a] & texts[b]:
                    raise AssertionError(f"leakage: {a} and {b} share identical query text")


def _q(data: BeirData, qid: str) -> str:
    for sp in data.queries.values():
        if qid in sp:
            return sp[qid].strip().lower()
    raise KeyError(qid)


def build_split_protocol(data: BeirData, seed: int = 13, query_limit: int | None = None) -> SplitProtocol:
    q, avail = data.queries, set(data.queries)
    test_pool = sorted(q["test"]) if "test" in q else []
    prov: dict[str, str] = {}
    pools: dict[str, str] = {}
    official = "test" in avail
    notes = ""
    if {"train", "dev", "test"} <= avail:
        workload = _shuffled(list(q["train"]), seed, "workload")[:WORKLOAD_CAP]
        validation = sorted(q["dev"])
        test = test_pool
        prov = {"workload": f"seeded sample (<= {WORKLOAD_CAP}) of the official train split (queries only)",
                "validation": "official dev split", "test": "official test split"}
        pools = {"workload": "train", "validation": "dev", "test": "test"}
    elif {"train", "test"} <= avail:
        tr = _shuffled(list(q["train"]), seed, "train-partition")
        half = len(tr) // 2
        workload, validation = tr[:half], tr[half:]
        test = test_pool
        prov = {"workload": "seeded 50 % of the official train split (queries only)",
                "validation": "other seeded 50 % of the official train split", "test": "official test split"}
        pools = {"workload": "train", "validation": "train", "test": "test"}
        notes = "No official dev split exists; the train split is partitioned deterministically into workload and validation."
    elif {"dev", "test"} <= avail:
        dv = _shuffled(list(q["dev"]), seed, "dev-partition")
        half = len(dv) // 2
        workload, validation, test = dv[:half], dv[half:], test_pool
        prov = {"workload": "seeded half of the official dev split (queries only)", "validation": "other half of dev",
                "test": "official test split"}
        pools = {"workload": "dev", "validation": "dev", "test": "test"}
    else:
        only = sorted(avail)[0]
        ids = _shuffled(list(q[only]), seed, f"{only}-internal")
        n = len(ids)
        a, b = int(n * 0.30), int(n * 0.50)
        workload, validation, test = ids[:a], ids[a:b], sorted(ids[b:])
        official = False
        prov = {"workload": f"seeded 30 % of the official {only} queries (queries only)",
                "validation": f"seeded 20 % of the official {only} queries", "test": f"seeded remaining 50 % of the official {only} queries"}
        pools = {k: only for k in ("workload", "validation", "test")}
        notes = (f"Dataset has no train/dev qrels: this is an INTERNAL query-level partition of the official {only} split "
                 "(seeded). Its test group is not the full official test set, so scores are not comparable to published BEIR numbers.")

    # an exact-text duplicate of a test query must never be used to shape the architecture
    test_texts = {data.queries[pools["test"]][qid].strip().lower() for qid in test}
    dropped = 0
    def clean(ids: list[str], pool: str) -> list[str]:
        nonlocal dropped
        keep = [i for i in ids if data.queries[pool][i].strip().lower() not in test_texts]
        dropped += len(ids) - len(keep)
        return keep
    workload, validation = clean(workload, pools["workload"]), clean(validation, pools["validation"])
    # workload queries must not repeat a validation query either (the validation group's judgments steer acceptance)
    val_texts = {data.queries[pools["validation"]][qid].strip().lower() for qid in validation}
    before = len(workload)
    workload = [i for i in workload if data.queries[pools["workload"]][i].strip().lower() not in val_texts]
    dropped += before - len(workload)

    truncated = False
    if query_limit is not None and query_limit > 0:
        if len(test) > query_limit or len(validation) > query_limit:
            truncated = True
        test = _shuffled(test, seed, "test-limit")[:query_limit]
        validation = _shuffled(validation, seed, "val-limit")[:query_limit]
        workload = _shuffled(workload, seed, "wl-limit")[: max(100, query_limit * 2)]
        notes = (notes + " " if notes else "") + f"SMOKE run: query limit {query_limit}; not an official benchmark result."
        official = False
    p = SplitProtocol(data.dataset_id, seed, sorted(workload), sorted(validation), sorted(test), prov, official, truncated,
                      notes, dropped, pools)
    _check_cross_pool(p)
    assert_disjoint(p, data)
    return p


def _check_cross_pool(p: SplitProtocol) -> None:
    """Query ids are unique within a split but different official splits could in principle reuse an id."""
    keys = [(p.pools["workload"], i) for i in p.workload] + [(p.pools["validation"], i) for i in p.validation] + \
           [(p.pools["test"], i) for i in p.test]
    if len(keys) != len(set(keys)):
        raise AssertionError("leakage: a query appears in more than one group")
    ids = p.workload + p.validation + p.test
    if len(ids) != len(set(ids)):
        raise AssertionError("leakage: query ids are shared between groups")


def group_queries(data: BeirData, p: SplitProtocol, group: str) -> dict[str, str]:
    pool = p.pools[group]
    return {qid: data.queries[pool][qid] for qid in getattr(p, group)}


def group_qrels(data: BeirData, p: SplitProtocol, group: str) -> dict[str, dict[str, int]]:
    if group == "workload":
        raise PermissionError("workload queries are used without relevance judgments by design")
    pool = p.pools[group]
    return {qid: dict(data.qrels[pool][qid]) for qid in getattr(p, group)}
