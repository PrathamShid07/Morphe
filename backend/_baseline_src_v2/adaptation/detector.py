"""Structural weakness detection: turns accumulated signals into explainable, evidence-backed proposals."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..common import iso, sha256_hex
from ..versioning.state import ArchitectureState
from .edge_manager import plan_shortcut
from .module_merger import find_merge_candidates, plan_merge
from .module_splitter import plan_split

SPLIT, MERGE, SHORTCUT = "split_module", "merge_modules", "add_shortcut_edge"
PRIORITY = {SPLIT: 1, MERGE: 2, SHORTCUT: 3}


@dataclass
class PlanningContext:
    state: ArchitectureState
    vectors: dict[str, np.ndarray]
    meta: dict[str, dict[str, Any]]


@dataclass
class Proposal:
    operation: str
    module_ids: list[str]
    trigger: str
    reason: str
    evidence: dict[str, Any]
    thresholds: dict[str, Any]
    plan: Any = field(default=None, repr=False)
    record_ids: int = 0
    rank: int = 0  # tie-break within an operation type: records affected, or co-access count for shortcuts

    @property
    def proposal_id(self) -> str:
        key = f"{self.operation}|{'|'.join(sorted(self.module_ids))}|{self.record_ids}"
        return "prop_" + sha256_hex(key)[:12]

    def to_dict(self) -> dict[str, Any]:
        return {"proposal_id": self.proposal_id, "operation": self.operation, "module_ids": self.module_ids,
                "trigger": self.trigger, "reason": self.reason, "evidence": self.evidence,
                "thresholds": self.thresholds, "affected_record_count": self.record_ids}


class StructureDetector:
    def __init__(self, settings):
        self.s = settings

    # ------------------------------------------------------------- build one
    def build(self, operation: str, module_ids: list[str], ctx: PlanningContext, window: dict[str, Any] | None,
              trigger: str, operation_id: str) -> tuple[Proposal | None, dict[str, Any]]:
        """Plan one specific operation against the given context. Returns (proposal, diagnostics)."""
        now = iso()
        st = ctx.state
        if operation == SPLIT:
            (m,) = module_ids
            if m not in st.modules:
                return None, {"blockers": [f"module {m} does not exist"]}
            plan, diag = plan_split(st, m, ctx.vectors, ctx.meta, self.s, operation_id, now)
            if plan is None:
                return None, {"blockers": [diag.get("reason", "no valid split")], **diag}
            n = st.record_count(m)
            name = st.modules[m].name
            capacity = n >= self.s.split_capacity_threshold
            if capacity:
                why = f"Module '{name}' holds {n} records, above the capacity threshold of {self.s.split_capacity_threshold}"
            else:
                why = (f"Module '{name}' ({n} records) mixes distinct topics: {len(plan.groups)} coherent groups "
                       f"found by {plan.strategy} (silhouette {plan.silhouette:.2f})")
            thresholds = {"split_capacity_threshold": self.s.split_capacity_threshold,
                          "split_mixed_min_records": self.s.split_mixed_min_records,
                          "split_min_group_size": self.s.split_min_group_size,
                          "split_min_silhouette": self.s.split_min_silhouette,
                          "split_mixed_min_silhouette": self.s.split_mixed_min_silhouette}
            if not capacity and (n < self.s.split_mixed_min_records or plan.silhouette < self.s.split_mixed_min_silhouette):
                return None, {"blockers": [f"{n} records is below the capacity threshold and the topic separation "
                                           f"(silhouette {plan.silhouette:.2f}) is not distinct enough"], **diag}
            groups = ", ".join(f"{g['name']} ({g['records']})" for g in plan.groups)
            return Proposal(SPLIT, [m], trigger, f"{why}. Proposed children: {groups}.", plan.evidence, thresholds,
                            plan, n, n), diag
        if operation == MERGE:
            a, b = module_ids
            plan, ev = plan_merge(st, a, b, ctx.vectors, ctx.meta, self.s, operation_id, now, window)
            if plan is None:
                return None, {"blockers": ev.get("blockers", ["not eligible"]), **ev}
            reason = (f"Modules '{st.modules[a].name}' and '{st.modules[b].name}' are redundant: profile similarity "
                      f"{ev['profile_similarity']:.2f}, {ev['overlapping_records']} overlapping records "
                      f"({ev['overlap_ratio']:.0%} of the smaller module), {ev['conflicts']} conflicting facts. "
                      f"'{plan.absorbed}' would be absorbed into '{plan.survivor}'.")
            n = st.record_count(a) + st.record_count(b)
            return Proposal(MERGE, [a, b], trigger, reason, ev, ev["thresholds"], plan, n, n), ev
        if operation == SHORTCUT:
            a, b = module_ids
            wc = (window or {}).get("pair_counts", {})
            key = "|".join(sorted((a, b)))
            count = int(wc.get(key, 0))
            q = int((window or {}).get("query_count", 0))
            plan, ev = plan_shortcut(st, a, b, count, q, self.s, operation_id, now, (window or {}).get("window_id"))
            if plan is None:
                return None, ev
            reason = (f"Modules '{st.modules[a].name}' and '{st.modules[b].name}' were used together by {count} of "
                      f"{q} queries in the current window (threshold {self.s.coaccess_min_count}); a co_access "
                      f"shortcut lets one pull in the other during routing.")
            return Proposal(SHORTCUT, sorted((a, b)), trigger, reason, ev, ev["thresholds"], plan, 0, count), ev
        return None, {"blockers": [f"unknown operation {operation}"]}

    # ----------------------------------------------------------- detect all
    def detect(self, ctx: PlanningContext, window: dict[str, Any] | None, trigger: str
               ) -> tuple[list[Proposal], list[dict[str, Any]]]:
        proposals: list[Proposal] = []
        notes: list[dict[str, Any]] = []
        st = ctx.state
        for m in st.content_modules():
            n = st.record_count(m)
            if n >= self.s.split_capacity_threshold or n >= self.s.split_mixed_min_records:
                p, diag = self.build(SPLIT, [m], ctx, window, trigger, "detect")
                (proposals.append(p) if p else notes.append({"operation": SPLIT, "module_ids": [m], **diag}))
        for ev in find_merge_candidates(st, ctx.vectors, ctx.meta, self.s, window):
            if ev["eligible"]:
                p, diag = self.build(MERGE, ev["modules"], ctx, window, trigger, "detect")
                (proposals.append(p) if p else notes.append({"operation": MERGE, "module_ids": ev["modules"], **diag}))
            else:
                notes.append({"operation": MERGE, "module_ids": ev["modules"], "blockers": ev["blockers"],
                              "profile_similarity": ev["profile_similarity"], "overlap_ratio": ev["overlap_ratio"]})
        if window:
            for key, count in sorted((window.get("pair_counts") or {}).items(), key=lambda kv: (-kv[1], kv[0])):
                if count < self.s.coaccess_min_count:
                    continue
                a, b = key.split("|")
                p, diag = self.build(SHORTCUT, [a, b], ctx, window, trigger, "detect")
                (proposals.append(p) if p else notes.append({"operation": SHORTCUT, "module_ids": [a, b], **diag}))
        proposals.sort(key=lambda p: (PRIORITY[p.operation], -p.rank, p.proposal_id))
        return proposals, notes
