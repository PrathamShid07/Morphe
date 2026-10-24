"""Connect a BEIR corpus to MORPHE: cached embeddings, a deterministic initial module graph, and ingestion into an
isolated MORPHE context where every memory keeps its original BEIR document id (`beir_doc_id` in the Qdrant payload)."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..common import iso, sha256_hex, slugify
from ..container import AppContext, build_context
from ..memory.models import ACTIVE, MemoryRecord
from ..memory.modules import top_keywords
from ..storage.mongo_store import MongoStore
from ..storage.qdrant_store import QdrantStore
from ..versioning.state import (CO_ACCESS, HIERARCHY, ArchitectureState, EdgeSpec, ModuleSpec, centroid, edge_id_for,
                                hierarchy_edges_from_parents)
from .datasets import doc_text

log = logging.getLogger("morphe.benchmark")
PREPROCESS_ID = "beir_title_text_v1"  # text = "<title> <text>", model-side truncation at the model's max sequence length


def memory_id_for(dataset_id: str, doc_id: str) -> str:
    return f"mem_beir_{slugify(dataset_id, 20)}_{hashlib.sha1(doc_id.encode('utf-8')).hexdigest()[:14]}"


@dataclass
class DocMap:
    """Explicit, verified mapping between original BEIR document ids and MORPHE memory ids."""
    doc_to_mem: dict[str, str]
    mem_to_doc: dict[str, str]

    @classmethod
    def build(cls, dataset_id: str, doc_ids: list[str]) -> "DocMap":
        d2m = {d: memory_id_for(dataset_id, d) for d in doc_ids}
        if len(set(d2m.values())) != len(d2m):
            raise ValueError("memory id collision while mapping BEIR document ids")
        return cls(d2m, {m: d for d, m in d2m.items()})

    def docs(self, memory_ids: list[str]) -> list[str]:
        return [self.mem_to_doc[m] for m in memory_ids]


# ------------------------------------------------------------------------------------------- embeddings
def _cache_key(model: str, dim: int, corpus_sha: str) -> str:
    return sha256_hex(f"{model}|{dim}|{PREPROCESS_ID}|{corpus_sha}")[:16]


def embed_corpus(embedder, dataset_id: str, corpus: dict[str, dict[str, str]], corpus_sha: str, cache_dir: str,
                 *, force: bool = False, batch_size: int = 128,
                 progress: Callable[[str], None] | None = None) -> tuple[list[str], np.ndarray, dict[str, Any]]:
    """Embed `title + text` for every document (sorted by id for determinism); reuse the cache when model, preprocessing and
    corpus content are unchanged. Validates dimension and finiteness. Returns (ids, matrix, info)."""
    ids = sorted(corpus)
    key = _cache_key(embedder.model_name, embedder.dimension, corpus_sha)
    d = Path(cache_dir) / dataset_id / key
    npy, meta = d / "corpus.npy", d / "meta.json"
    log_ = progress or (lambda m: log.info(m))
    if npy.exists() and meta.exists() and not force:
        info = json.loads(meta.read_text())
        mat = np.load(npy)
        if info.get("ids_sha") == sha256_hex("\n".join(ids)) and mat.shape == (len(ids), embedder.dimension):
            log_(f"embeddings cache hit: {npy} ({mat.shape[0]} docs)")
            return ids, mat, {**info, "cache_hit": True, "path": str(npy)}
        log_("embeddings cache is stale for this corpus; recomputing")
    texts = [doc_text(corpus[i]) for i in ids]
    t0 = time.perf_counter()
    parts = []
    for s in range(0, len(texts), batch_size):
        parts.append(embedder.encode(texts[s:s + batch_size]))
        if (s // batch_size) % 5 == 0:
            log_(f"embedding {dataset_id}: {min(s + batch_size, len(texts))}/{len(texts)} "
                 f"({time.perf_counter() - t0:.0f}s elapsed)")
    mat = np.concatenate(parts).astype(np.float32)
    if mat.shape != (len(ids), embedder.dimension) or not np.isfinite(mat).all():
        raise ValueError("corpus embeddings have the wrong shape or contain non-finite values")
    d.mkdir(parents=True, exist_ok=True)
    np.save(npy, mat)
    info = {"model": embedder.model_name, "dimension": embedder.dimension, "preprocess": PREPROCESS_ID,
            "corpus_sha256": corpus_sha, "documents": len(ids), "ids_sha": sha256_hex("\n".join(ids)),
            "seconds": round(time.perf_counter() - t0, 1), "created_at": iso()}
    meta.write_text(json.dumps(info, indent=1))
    log_(f"embedded {len(ids)} documents in {info['seconds']}s -> {npy}")
    return ids, mat, {**info, "cache_hit": False, "path": str(npy)}


# --------------------------------------------------------------------------------------------- graph init
@dataclass
class GraphInitConfig:
    method: str = "kmeans"
    target_module_size: int = 100
    min_modules: int = 8
    min_module_size: int = 10
    group_policy: str = "sqrt"  # number of hierarchy group containers = round(sqrt(#modules))
    edge_policy: str = "hierarchy_only"  # no co_access edges at initialisation
    seed: int = 13
    n_init: int = 10


def build_initial_architecture(dataset_id: str, doc_map: DocMap, ids: list[str], mat: np.ndarray,
                               corpus: dict[str, dict[str, str]], cfg: GraphInitConfig) -> tuple[ArchitectureState, dict[str, Any]]:
    """Deterministic modules from content embeddings only — no queries and no relevance judgments are involved."""
    from sklearn.cluster import KMeans
    n = len(ids)
    k = max(cfg.min_modules, round(n / cfg.target_module_size))
    km = KMeans(n_clusters=k, n_init=cfg.n_init, random_state=cfg.seed).fit(mat)
    labels = km.labels_.copy()
    # enforce the minimum module size: tiny clusters are folded into the nearest larger cluster (by centroid cosine)
    cents = np.stack([mat[labels == c].mean(axis=0) for c in range(k)])
    cents /= np.maximum(np.linalg.norm(cents, axis=1, keepdims=True), 1e-9)
    sizes = np.bincount(labels, minlength=k)
    for c in np.argsort(sizes):
        if 0 < sizes[c] < cfg.min_module_size:
            sims = cents @ cents[c]
            sims[c] = -2
            sims[sizes < cfg.min_module_size] = -2
            target = int(np.argmax(sims))
            labels[labels == c] = target
            sizes = np.bincount(labels, minlength=k)
    live = sorted(int(c) for c in np.unique(labels))
    state = ArchitectureState()
    now = iso()
    prefix = f"mod_{slugify(dataset_id, 12)}"
    members: dict[int, list[int]] = {c: [int(i) for i in np.flatnonzero(labels == c)] for c in live}
    order = sorted(live, key=lambda c: (-len(members[c]), min(members[c])))  # stable numbering by size then first doc
    for num, c in enumerate(order):
        idx = members[c]
        mid = f"{prefix}_m{num:03d}"
        titles = [(corpus[ids[i]].get("title") or corpus[ids[i]].get("text") or "")[:200] for i in idx[:60]]
        kws = top_keywords(titles, 3)
        state.modules[mid] = ModuleSpec(mid, f"M{num:03d} · {' '.join(kws) or 'misc'}", None, ", ".join(kws), kws,
                                        centroid(mat[idx]), now, "benchmark_init", "created", now)
        state.assignments[mid] = sorted(doc_map.doc_to_mem[ids[i]] for i in idx)
    # hierarchy: group containers over module centroids
    mods = list(state.modules)
    g = max(2, round(len(mods) ** 0.5)) if cfg.group_policy == "sqrt" else max(2, int(cfg.group_policy))
    g = min(g, len(mods))
    gm = KMeans(n_clusters=g, n_init=cfg.n_init, random_state=cfg.seed).fit(np.stack([state.modules[m].profile_vector for m in mods]))
    for gi in range(g):
        children = [m for m, lab in zip(mods, gm.labels_) if lab == gi]
        if not children:
            continue
        gid = f"{prefix}_g{gi:02d}"
        all_kws = sorted({kw for m in children for kw in state.modules[m].keywords})[:4]
        state.modules[gid] = ModuleSpec(gid, f"Group {gi:02d} · {' '.join(all_kws)}", None, "group container", all_kws, None,
                                        now, "benchmark_init", "created", now)
        for m in children:
            state.modules[m].parent_id = gid
    hierarchy_edges_from_parents(state, now, "benchmark_init")
    sizes_final = [len(v) for m, v in state.assignments.items() if v]
    info = {"config": asdict(cfg), "modules": len(sizes_final), "groups": g, "module_size": {
        "min": int(min(sizes_final)), "median": float(np.median(sizes_final)), "max": int(max(sizes_final)),
        "mean": float(np.mean(sizes_final))}, "inertia": float(km.inertia_), "initial_edges": len(state.edges),
        "uses_queries_or_qrels": False}
    problems = state.validate(mat.shape[1])
    if problems:
        raise ValueError("initial architecture is invalid: " + "; ".join(problems[:3]))
    return state, info


# ------------------------------------------------------------------------------------------- ingestion
def benchmark_settings(base, **over):
    """MORPHE settings for an isolated benchmark context. Every value is recorded in the run manifest."""
    d = dict(app_env="development", compression_mode="off", auto_adaptation=False, adaptation_inline=True,
             adaptation_auto_apply=True, mongo_backend=base.mongo_backend, qdrant_mode="memory",
             eval_dataset_path="", memory_budget_tokens=10**12, fallback_enabled=True, log_level="WARNING",
             acceptance_profile="beir", require_benefit=True, eval_depth=10, post_commit_verification=True,
             max_query_chars=100_000)
    d.update(over)
    return base.model_copy(update=d)


def make_context(base_settings, embedder, dataset_id: str, run_id: str, *, mongo: MongoStore | None = None,
                 **settings_over) -> AppContext:
    settings = benchmark_settings(base_settings, mongodb_database=f"morphe_beir_{slugify(dataset_id, 16)}_{run_id[-8:]}",
                                  **settings_over)
    mongo = mongo or MongoStore.from_settings(settings)
    qdrant = QdrantStore.in_memory(settings.embedding_dimension, "bench_memories", "bench_profiles")
    return build_context(settings, embedder=embedder, qdrant=qdrant, mongo=mongo)


def ingest_corpus(ctx: AppContext, dataset_id: str, corpus: dict[str, dict[str, str]], ids: list[str], mat: np.ndarray,
                  doc_map: DocMap, state: ArchitectureState, *, batch: int = 500) -> dict[str, Any]:
    """Write every document as one active memory (id preserved), then converge the stores to the initial architecture
    through the production persistence layer and take the baseline checkpoint."""
    t0 = time.perf_counter()
    src = f"src_beir_{slugify(dataset_id, 20)}"
    mem_module = state.module_of()
    now = iso()
    with ctx.coordinator.mutate("benchmark_ingest", meta={"dataset": dataset_id}):
        records: list[MemoryRecord] = []
        for i, doc_id in enumerate(ids):
            text = doc_text(corpus[doc_id])
            mid = doc_map.doc_to_mem[doc_id]
            records.append(MemoryRecord(
                memory_id=mid, module_id=mem_module[mid], text=text, source_id=src, source_name=dataset_id, created_at=now,
                token_count=max(1, len(text.split())), content_hash=sha256_hex(text), status=ACTIVE, kind="document",
                external_id=doc_id, dataset_id=dataset_id, title=(corpus[doc_id].get("title") or "") or None))
        for s in range(0, len(records), batch):
            chunk = records[s:s + batch]
            ctx.qdrant.upsert_memories([(r.memory_id, mat[s + j], r.qdrant_payload()) for j, r in enumerate(chunk)])
            ctx.mongo.upsert_memory_metadata([{**r.mongo_doc(), "access_count": 0, "last_accessed_at": None} for r in chunk])
        ctx.mongo.col("sources").update_one({"source_id": src}, {"$set": {
            "source_id": src, "source_name": dataset_id, "content_hash": sha256_hex("\n".join(ids)), "created_at": now,
            "metadata": {"benchmark": "beir", "preprocess": PREPROCESS_ID}, "chunk_count": len(records),
            "token_count": sum(r.token_count for r in records), "status": "active", "module_ids": list(state.modules)}},
            upsert=True)
        ctx.persistence.converge_to(state, operation_id="benchmark_init")
    with ctx.coordinator.mutate("checkpoint"):
        v = ctx.lifecycle.ensure_checkpoint("benchmark initial architecture")
    return {"documents": len(records), "seconds": round(time.perf_counter() - t0, 2), "initial_version": v["version_id"]}


def verify_mapping(ctx: AppContext, doc_map: DocMap) -> dict[str, Any]:
    """The Qdrant payload must carry exactly the original BEIR id for every memory, with no loss or duplication."""
    seen: dict[str, str] = {}
    for mid, payload, _ in ctx.qdrant.scroll_memories():
        seen[mid] = payload.get("beir_doc_id")
    mism = [m for m, d in seen.items() if doc_map.mem_to_doc.get(m) != d]
    ok = not mism and set(seen) == set(doc_map.mem_to_doc)
    if not ok:
        raise ValueError(f"BEIR id mapping is inconsistent for {len(mism)} memories (e.g. {mism[:3]})")
    return {"verified": True, "documents": len(seen)}
