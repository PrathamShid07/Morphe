"""Compression execution: ordered steps with a safe write-verify-retire replacement sequence.

Order: exact duplicates -> near duplicates -> related-memory summaries -> single-record shortening -> (emergency only)
deletion of eligible low-value records. Originals stay active until a replacement is written AND read back verified.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from ..common import get_logger, iso, log_event, sha256_hex
from ..ingestion.embedder import Embedder, validate_vectors
from ..memory.models import ACTIVE, DELETED, PENDING, SUPERSEDED, MemoryRecord, is_protected, preview
from ..memory.modules import ModuleService
from ..monitoring.events import EventLog
from ..storage.mongo_store import MongoStore
from ..storage.qdrant_store import QdrantStore
from .duplicate_detector import exact_duplicate_groups, near_duplicate_pairs
from .selector import can_rewrite, emergency_order, summary_groups
from .summarizer import ExtractiveSummarizer, SummaryProvider, shorten_text

log = get_logger("compression")


class VerificationError(Exception):
    pass


@dataclass
class Job:
    job_id: str
    mode: str  # normal | emergency
    trigger: str
    target_pct: float
    budget: int
    dry_run: bool
    docs: dict[str, dict[str, Any]]
    vectors: dict[str, np.ndarray]
    tokens: int
    events: list[dict[str, Any]] = field(default_factory=list)
    plan: list[dict[str, Any]] = field(default_factory=list)
    touched: set[str] = field(default_factory=set)
    skipped_logged: int = 0
    duplicates_removed: int = 0
    summaries_created: int = 0
    deleted: int = 0

    @property
    def pct(self) -> float:
        return self.tokens / self.budget * 100 if self.budget else 0.0

    def reached_target(self) -> bool:
        return self.pct <= self.target_pct


class CompressionExecutor:
    MAX_SKIP_EVENTS = 5

    def __init__(self, settings, embedder: Embedder, qdrant: QdrantStore, mongo: MongoStore, modules: ModuleService,
                 events: EventLog, provider: SummaryProvider,
                 fault_hook: Callable[[str], None] | None = None):
        self.s = settings
        self.embedder, self.qdrant, self.mongo = embedder, qdrant, mongo
        self.modules, self.events, self.provider = modules, events, provider
        self.fault_hook = fault_hook or (lambda point: None)  # test seam for failure injection

    # ------------------------------------------------------------------ load
    def load(self) -> tuple[dict[str, dict[str, Any]], dict[str, np.ndarray]]:
        meta = {d["memory_id"]: d for d in self.mongo.active_memory_docs()}
        docs, vectors = {}, {}
        for mid, payload, vec in self.qdrant.scroll_memories(with_vectors=True):
            m = meta.get(mid, {})
            docs[mid] = {**payload, "access_count": m.get("access_count", 0),
                         "last_accessed_at": m.get("last_accessed_at")}
            vectors[mid] = vec
        return docs, vectors

    # ---------------------------------------------------------------- events
    def _begin(self, job: Job, operation: str, reason: str, source_ids: list[str], output_id: str | None = None,
               tokens_before: int | None = None) -> dict[str, Any]:
        ev = self.events.start_compression(
            operation=operation, trigger=job.trigger, reason=reason, scope="operation", job_id=job.job_id,
            source_memory_ids=source_ids[:200], source_count=len(source_ids), output_memory_id=output_id,
            usage_before_pct=round(job.pct, 2), provider=self.provider.name if "summar" in operation else None,
            status="simulated" if job.dry_run else "running")
        return ev

    def _end(self, job: Job, ev: dict[str, Any], status: str, before: int, after: int, **fields: Any) -> None:
        reclaimed = before - after
        self.events.update_compression(ev["event_id"], status=status if not job.dry_run else "simulated",
                                       finished_at=iso(), tokens_before=before, tokens_after=after,
                                       tokens_reclaimed=reclaimed, usage_after_pct=round(job.pct, 2), **fields)
        job.events.append({"event_id": ev["event_id"], "operation": ev["operation"], "status": status,
                           "tokens_reclaimed": reclaimed})

    def _skip(self, job: Job, operation: str, reason: str, source_ids: list[str]) -> None:
        job.skipped_logged += 1
        if job.skipped_logged > self.MAX_SKIP_EVENTS:
            return
        ev = self._begin(job, operation, reason, source_ids)
        self.events.update_compression(ev["event_id"], status="skipped", finished_at=iso(), tokens_before=0,
                                       tokens_after=0, tokens_reclaimed=0)
        job.events.append({"event_id": ev["event_id"], "operation": operation, "status": "skipped",
                           "tokens_reclaimed": 0, "reason": reason})

    # ----------------------------------------------------------- primitives
    def _retire(self, ids: list[str], by: str | None) -> None:
        self.qdrant.set_status(ids, SUPERSEDED, {"superseded_by": by})
        try:
            self.mongo.update_memory_metadata(ids, {"status": SUPERSEDED, "superseded_by": by, "retired_at": iso()})
        except Exception:
            self.qdrant.set_status(ids, ACTIVE, {"superseded_by": None})  # compensate: keep originals active
            raise

    def _drop_local(self, job: Job, ids: list[str]) -> int:
        reclaimed = 0
        for i in ids:
            d = job.docs.pop(i, None)
            job.vectors.pop(i, None)
            if d:
                reclaimed += int(d["token_count"])
                job.touched.add(d["module_id"])
        job.tokens -= reclaimed
        return reclaimed

    def safe_replace(self, job: Job, source_ids: list[str], text: str, kind: str) -> MemoryRecord:
        """Write replacement (pending) -> read back & verify -> activate -> retire originals. Any failure before the
        originals are retired leaves them active and removes the replacement."""
        first = job.docs[source_ids[0]]
        new_id = "mem_" + kind[:3] + "_" + sha256_hex("|".join(sorted(source_ids)) + text)[:14]
        src_ids = sorted({job.docs[i]["source_id"] for i in source_ids})
        src_names = sorted({job.docs[i]["source_name"] for i in source_ids})
        vec = validate_vectors(self.embedder.encode([text]), self.embedder.dimension, 1)[0]
        rec = MemoryRecord(
            memory_id=new_id, module_id=first["module_id"], text=text, source_id=first["source_id"],
            source_name=src_names[0] if len(src_names) == 1 else "Summary of " + ", ".join(src_names)[:120],
            created_at=iso(), token_count=self.embedder.count_tokens(text), content_hash=sha256_hex(text),
            section=first.get("section"), chunk_index=0, status=PENDING,
            importance=max((job.docs[i].get("importance", "normal") for i in source_ids),
                           key=lambda x: ["low", "normal", "high", "critical"].index(x)),
            pinned=False, summary_of=list(source_ids), tags=sorted({t for i in source_ids for t in job.docs[i].get("tags", [])}),
            merged_source_ids=src_ids, kind=kind)
        wrote = False
        try:
            self.qdrant.upsert_memories([(new_id, vec, rec.qdrant_payload())])
            self.mongo.upsert_memory_metadata([{**rec.mongo_doc(), "access_count": 0, "last_accessed_at": None}])
            wrote = True
            self.fault_hook("after_replacement_write")
            got = self.qdrant.get_memories([new_id], with_vectors=True).get(new_id)
            meta = self.mongo.get_memory_metadata(new_id)
            if (not got or got[1] is None or len(got[1]) != self.embedder.dimension or got[0].get("text") != text
                    or not got[0].get("summary_of") or not meta or meta.get("memory_id") != new_id):
                raise VerificationError("replacement record failed read-back verification")
            self.qdrant.set_status([new_id], ACTIVE)
            self.mongo.update_memory_metadata([new_id], {"status": ACTIVE})
            self.fault_hook("before_retire")
        except Exception:
            if wrote:
                self._cleanup_replacement(new_id)
            raise
        try:
            self._retire(source_ids, new_id)
        except Exception:
            # partial retirement: re-activate originals and remove the replacement so nothing is double-counted
            try:
                self.qdrant.set_status(source_ids, ACTIVE, {"superseded_by": None})
                self.mongo.update_memory_metadata(source_ids, {"status": ACTIVE, "superseded_by": None})
            finally:
                self._cleanup_replacement(new_id)
            raise
        rec.status = ACTIVE
        return rec

    def _cleanup_replacement(self, new_id: str) -> None:
        for step in (lambda: self.qdrant.delete_memories([new_id]),
                     lambda: self.mongo.col("memory_metadata").delete_one({"memory_id": new_id})):
            try:
                step()
            except Exception as exc:
                log_event(log, "replacement_cleanup_failed", level=40, error_type=type(exc).__name__)

    # ------------------------------------------------------------- step 1
    def step_exact_duplicates(self, job: Job) -> None:
        for group in exact_duplicate_groups(job.docs):
            if job.reached_target():
                return
            keeper, drops = group[0], [m for m in group[1:] if not is_protected(job.docs[m])]
            if not drops:
                continue
            before = job.tokens
            reclaimed = sum(job.docs[m]["token_count"] for m in drops)
            ev = self._begin(job, "exact_duplicate_removal", f"{len(drops)} exact copies of {keeper}", drops, keeper)
            if job.dry_run:
                self._drop_local(job, drops)
                job.duplicates_removed += len(drops)
                self._end(job, ev, "simulated", before, job.tokens)
                continue
            try:
                merged = sorted(set(job.docs[keeper].get("merged_source_ids", []) or [job.docs[keeper]["source_id"]])
                                | {job.docs[m]["source_id"] for m in drops})
                self.qdrant.set_payload([keeper], {"merged_source_ids": merged})
                self.mongo.update_memory_metadata([keeper], {"merged_source_ids": merged})
                self._retire(drops, keeper)
                self._drop_local(job, drops)
                job.duplicates_removed += len(drops)
                self.modules.refresh_profiles([job.docs[keeper]["module_id"]])
                self._end(job, ev, "completed", before, job.tokens, reclaimed_expected=reclaimed)
            except Exception as exc:
                self._end(job, ev, "failed", before, before, error=f"{type(exc).__name__}: {exc}"[:300])

    # ------------------------------------------------------------- step 2
    def step_near_duplicates(self, job: Job) -> None:
        by_mod: dict[str, list[str]] = {}
        for mid, d in job.docs.items():
            by_mod.setdefault(d["module_id"], []).append(mid)
        for mod, ids in sorted(by_mod.items()):
            for keep, drop, sim in near_duplicate_pairs(sorted(ids), job.vectors, job.docs,
                                                        self.s.near_duplicate_similarity):
                if job.reached_target():
                    return
                if keep not in job.docs or drop not in job.docs:
                    continue
                before = job.tokens
                ev = self._begin(job, "near_duplicate_consolidation",
                                 f"{drop} is a compatible near-duplicate of {keep} (cosine {sim:.3f}); "
                                 "no conflicting facts", [drop], keep)
                if job.dry_run:
                    self._drop_local(job, [drop])
                    job.duplicates_removed += 1
                    self._end(job, ev, "simulated", before, job.tokens)
                    continue
                try:
                    merged = sorted(set(job.docs[keep].get("merged_source_ids", []) or [job.docs[keep]["source_id"]])
                                    | {job.docs[drop]["source_id"]})
                    self.qdrant.set_payload([keep], {"merged_source_ids": merged})
                    self.mongo.update_memory_metadata([keep], {"merged_source_ids": merged})
                    self._retire([drop], keep)
                    self._drop_local(job, [drop])
                    job.duplicates_removed += 1
                    self.modules.refresh_profiles([mod])
                    self._end(job, ev, "completed", before, job.tokens, similarity=round(sim, 4))
                except Exception as exc:
                    self._end(job, ev, "failed", before, before, error=f"{type(exc).__name__}: {exc}"[:300])

    # ------------------------------------------------------------- step 3
    def step_summaries(self, job: Job) -> None:
        for group in summary_groups(job.docs, job.vectors, self.s.summary_group_min_size,
                                    self.s.summary_group_similarity):
            if job.reached_target():
                return
            group = [m for m in group if m in job.docs]
            if len(group) < self.s.summary_group_min_size:
                continue
            records = [job.docs[m] for m in group]
            total = sum(r["token_count"] for r in records)
            result = self.provider.summarize(records, max(20, int(total * (1 - self.s.min_summary_saving_pct / 100))))
            saving = (1 - result.output_token_count / total) * 100 if total else 0
            if not result.text or not result.preserved_facts:
                self._skip(job, "summarize_group",
                           "summary would drop exact facts: " + ", ".join(result.missing_facts[:4]), group)
                continue
            if saving < self.s.min_summary_saving_pct:
                self._skip(job, "summarize_group", f"saving {saving:.0f}% is below the "
                           f"{self.s.min_summary_saving_pct:.0f}% minimum", group)
                continue
            before = job.tokens
            ev = self._begin(job, "summarize_group",
                             f"{len(group)} related records in module {job.docs[group[0]]['module_id']}", group)
            if job.dry_run:
                self._drop_local(job, group)
                job.tokens += result.output_token_count
                job.summaries_created += 1
                self._end(job, ev, "simulated", before, job.tokens, provider_version=result.provider_version)
                continue
            try:
                rec = self.safe_replace(job, group, result.text, "summary")
                self.events.update_compression(ev["event_id"], output_memory_id=rec.memory_id,
                                               provider=result.provider_name, provider_version=result.provider_version,
                                               verification="passed")
                mod = job.docs[group[0]]["module_id"]
                self._drop_local(job, group)
                job.docs[rec.memory_id] = {**rec.qdrant_payload(), "access_count": 0, "last_accessed_at": None}
                job.vectors[rec.memory_id] = self.embedder.encode([rec.text])[0]
                job.tokens += rec.token_count
                job.summaries_created += 1
                self.modules.refresh_profiles([mod])
                self._end(job, ev, "completed", before, job.tokens)
            except Exception as exc:
                self._end(job, ev, "failed", before, before, error=f"{type(exc).__name__}: {exc}"[:300],
                          note="originals remain active")

    # ------------------------------------------------------------- step 4
    def step_shorten(self, job: Job) -> None:
        for mid in sorted(job.docs, key=lambda m: -job.docs[m]["token_count"]):
            if job.reached_target():
                return
            d = job.docs.get(mid)
            if not d or not can_rewrite(d):
                continue
            new = shorten_text(d["text"])
            old_t = d["token_count"]
            new_t = self.embedder.count_tokens(new) if new else 0
            if not new or (1 - new_t / old_t) * 100 < self.s.min_summary_saving_pct:
                continue
            from .summarizer import critical_facts
            if critical_facts(d["text"]) - critical_facts(new):
                continue
            before = job.tokens
            ev = self._begin(job, "shorten_record", "redundant phrasing removed; facts preserved", [mid])
            if job.dry_run:
                self._drop_local(job, [mid])
                job.tokens += new_t
                self._end(job, ev, "simulated", before, job.tokens)
                continue
            try:
                rec = self.safe_replace(job, [mid], new, "shortened")
                mod = d["module_id"]
                self._drop_local(job, [mid])
                job.docs[rec.memory_id] = {**rec.qdrant_payload(), "access_count": 0, "last_accessed_at": None}
                job.vectors[rec.memory_id] = self.embedder.encode([rec.text])[0]
                job.tokens += rec.token_count
                self.modules.refresh_profiles([mod])
                self.events.update_compression(ev["event_id"], output_memory_id=rec.memory_id, verification="passed")
                self._end(job, ev, "completed", before, job.tokens)
            except Exception as exc:
                self._end(job, ev, "failed", before, before, error=f"{type(exc).__name__}: {exc}"[:300],
                          note="original remains active")

    # ------------------------------------------------------------- step 5
    def step_emergency_delete(self, job: Job) -> None:
        order = emergency_order(job.docs)
        protected = sum(1 for d in job.docs.values() if is_protected(d))
        if job.reached_target():
            return
        victims: list[str] = []
        projected = job.tokens
        for mid in order:
            if projected / job.budget * 100 <= job.target_pct:
                break
            victims.append(mid)
            projected -= job.docs[mid]["token_count"]
        before = job.tokens
        if not victims:
            self._skip(job, "emergency_delete", f"no eligible records remain ({protected} protected)", [])
            return
        ev = self._begin(job, "emergency_delete",
                         f"usage {job.pct:.1f}% reached the emergency threshold; deleting {len(victims)} eligible "
                         f"low-value records (oldest, least-used, lowest importance first); {protected} protected "
                         "records were excluded", victims)
        if job.dry_run:
            self._drop_local(job, victims)
            job.deleted += len(victims)
            self._end(job, ev, "simulated", before, job.tokens)
            return
        try:
            mods = {job.docs[m]["module_id"] for m in victims}
            self.qdrant.delete_memories(victims)
            self.mongo.update_memory_metadata(victims, {"status": DELETED, "deleted_at": iso(), "text_preview": "",
                                                        "deleted_by": ev["event_id"]})
            self._drop_local(job, victims)
            job.deleted += len(victims)
            self.modules.refresh_profiles(sorted(mods))
            self._end(job, ev, "completed", before, job.tokens)
        except Exception as exc:
            self._end(job, ev, "failed", before, before, error=f"{type(exc).__name__}: {exc}"[:300])
