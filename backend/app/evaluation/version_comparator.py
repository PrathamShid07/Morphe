"""Acceptance policy: decides whether a candidate architecture may replace the current one.

Two layers: (1) *guardrails* — no mandatory quality metric may regress beyond its threshold and p95 latency may not
regress beyond its relative threshold (with a small absolute noise floor); (2) optionally a *benefit requirement* —
the change must also improve something it is supposed to improve by a meaningful, configured amount.
"""
from __future__ import annotations

from typing import Any

from .metrics import paired_bootstrap

# (metric key, label, setting name holding the maximum allowed absolute regression)
LEGACY_GUARDS = [("recall_at_5", "recall_at_5", "max_recall_regression"), ("mrr", "mrr", "max_mrr_regression")]
BEIR_GUARDS = [("ndcg_at_10", "ndcg_at_10", "max_ndcg_regression"), ("recall_at_5", "recall_at_5", "max_recall_regression"),
               ("mrr_at_10", "mrr_at_10", "max_mrr_regression")]


class AcceptancePolicy:
    def __init__(self, settings):
        self.s = settings

    @property
    def guards(self):
        return BEIR_GUARDS if self.s.acceptance_profile == "beir" else LEGACY_GUARDS

    def thresholds(self) -> dict[str, Any]:
        return {"profile": self.s.acceptance_profile, "max_ndcg_regression": self.s.max_ndcg_regression,
                "max_recall_regression": self.s.max_recall_regression, "max_mrr_regression": self.s.max_mrr_regression,
                "max_p95_latency_regression_pct": self.s.max_p95_latency_regression_pct,
                "latency_noise_floor_ms": self.s.latency_noise_floor_ms, "min_eval_queries": self.s.min_eval_queries,
                "require_benefit": self.s.require_benefit, "min_quality_gain": self.s.min_quality_gain,
                "min_candidate_reduction_pct": self.s.min_candidate_reduction_pct,
                "min_latency_gain_pct": self.s.min_latency_gain_pct, "benefit_requires_ci": self.s.benefit_requires_ci,
                "cumulative_guard": self.s.cumulative_guard, "interleaved_latency": self.s.eval_interleaved,
                "validation_override_allowed": self.s.allow_validation_override and not self.s.is_production}

    def _ci_low(self, baseline: dict[str, Any], candidate: dict[str, Any], row_key: str) -> float | None:
        """Lower end of the paired-bootstrap 95% interval of the per-query improvement (None if per-query data is absent)."""
        a = {r["query_id"]: r[row_key] for r in baseline.get("per_query", [])}
        b = {r["query_id"]: r[row_key] for r in candidate.get("per_query", [])}
        ids = sorted(set(a) & set(b))
        if len(ids) < 2:
            return None
        return paired_bootstrap([a[i] for i in ids], [b[i] for i in ids], self.s.benefit_bootstrap_resamples, 13)["ci95_low"]

    def decide(self, baseline: dict[str, Any], candidate: dict[str, Any], override: bool = False,
               latency_factor: float = 1.0, check_benefit: bool = True, reference: dict[str, Any] | None = None
               ) -> dict[str, Any]:
        """Compare two evaluation reports produced under identical conditions."""
        checks: list[dict[str, Any]] = []
        b, c = baseline["metrics"], candidate["metrics"]
        override_allowed = override and self.s.allow_validation_override and not self.s.is_production
        override_used = False

        same_data = baseline["conditions"]["dataset_hash"] == candidate["conditions"]["dataset_hash"] and \
            baseline["conditions"]["queries_evaluated"] == candidate["conditions"]["queries_evaluated"]
        checks.append({"name": "same_evaluation_conditions", "passed": same_data,
                       "detail": "baseline and candidate used the same dataset and queries" if same_data else
                       "baseline and candidate were evaluated on different data"})

        data_ok = baseline["valid"] and candidate["valid"]
        if not data_ok and override_allowed:
            override_used = True
        checks.append({"name": "sufficient_evaluation_data", "passed": data_ok or override_used,
                       "detail": (candidate.get("invalid_reason") or baseline.get("invalid_reason") or
                                  f"{c['sample_size']} labeled queries evaluated") +
                                 (" (development override used)" if override_used else ""),
                       "override_used": override_used})
        if not data_ok and not override_used:
            return self._result(False, checks, "Rejected: evaluation data is missing, invalid or insufficient, so the "
                                "change cannot be validated.", baseline, candidate, override_used)

        for key, name, setting in self.guards:
            limit = getattr(self.s, setting)
            bv, cv = b.get(key), c.get(key)
            if bv is None or cv is None:
                checks.append({"name": name, "passed": False, "detail": "metric unavailable"})
                continue
            delta = cv - bv
            checks.append({"name": name, "baseline": bv, "candidate": cv, "delta": delta, "limit": -limit,
                           "passed": (bv - cv) <= limit + 1e-9,
                           "detail": f"{name} changed by {delta:+.4f} (allowed regression {limit:.3f})"})

        if reference is not None and self.s.cumulative_guard:
            r = reference["metrics"]
            for key, name, setting in self.guards:
                limit = getattr(self.s, setting)
                rv, cv = r.get(key), c.get(key)
                if rv is None or cv is None:
                    continue
                checks.append({"name": f"cumulative_{name}", "baseline": rv, "candidate": cv, "delta": cv - rv, "limit": -limit,
                               "passed": (rv - cv) <= limit + 1e-9,
                               "detail": f"{name} vs the INITIAL architecture changed by {cv - rv:+.4f} (allowed regression {limit:.3f})"})

        bp, cp = b["p95_latency_ms"], c["p95_latency_ms"]
        lat_gain_pct = 0.0
        if bp is None or cp is None:
            checks.append({"name": "p95_latency_ms", "passed": False, "detail": "latency unavailable"})
        else:
            rel = (cp - bp) / bp * 100 if bp > 0 else 0.0
            lat_gain_pct = -rel if (bp - cp) > self.s.latency_noise_floor_ms * latency_factor else 0.0
            limit_pct = self.s.max_p95_latency_regression_pct * latency_factor
            ok = cp <= bp * (1 + limit_pct / 100) or (cp - bp) <= self.s.latency_noise_floor_ms * latency_factor
            checks.append({"name": "p95_latency_ms", "baseline": bp, "candidate": cp, "delta": cp - bp,
                           "relative_change_pct": rel, "limit_pct": limit_pct,
                           "noise_floor_ms": self.s.latency_noise_floor_ms * latency_factor, "passed": ok,
                           "detail": f"p95 latency {bp:.3f}ms → {cp:.3f}ms ({rel:+.1f}%, allowed "
                                     f"+{limit_pct:.0f}% or {self.s.latency_noise_floor_ms * latency_factor:g}ms noise floor)"})
        failed = [x for x in checks if not x["passed"]]
        if failed:
            return self._result(False, checks, "Rejected: " + "; ".join(x["detail"] for x in failed), baseline,
                                candidate, override_used)

        gains: list[str] = []
        weak_gains: list[str] = []
        for key, label, row_key in (("ndcg_at_10", "nDCG@10", "ndcg_at_10"), ("recall_at_5", "Recall@5", "recall")):
            if b.get(key) is not None and c.get(key) is not None and c[key] - b[key] >= self.s.min_quality_gain - 1e-12:
                if self.s.benefit_requires_ci:
                    lo = self._ci_low(baseline, candidate, row_key)
                    if lo is None or lo <= 0:
                        weak_gains.append(f"{label} {b[key]:.3f}→{c[key]:.3f} (not statistically supported: paired 95% interval "
                                          f"{'unavailable' if lo is None else f'starts at {lo:+.4f}'})")
                        continue
                gains.append(f"{label} {b[key]:.3f}→{c[key]:.3f}")
        if c.get("mrr") is not None and b.get("mrr") is not None and c["mrr"] > b["mrr"] + 1e-9:
            weak_gains.append(f"MRR {b['mrr']:.3f}→{c['mrr']:.3f}")
        bc, cc = b.get("mean_candidate_count"), c.get("mean_candidate_count")
        if bc and cc is not None:
            red = (bc - cc) / bc * 100
            if red >= self.s.min_candidate_reduction_pct:
                gains.append(f"mean candidates {bc:.1f}→{cc:.1f} (−{red:.1f}%)")
            elif cc < bc:
                weak_gains.append(f"mean candidates {bc:.1f}→{cc:.1f}")
        if lat_gain_pct >= self.s.min_latency_gain_pct:
            gains.append(f"p95 latency −{lat_gain_pct:.1f}%")
        if check_benefit and self.s.require_benefit:
            checks.append({"name": "meaningful_benefit", "passed": bool(gains),
                           "detail": ("benefit: " + "; ".join(gains)) if gains else
                           f"no meaningful benefit (needs nDCG@10 or Recall@5 ≥ +{self.s.min_quality_gain}, candidate pool "
                           f"≥ −{self.s.min_candidate_reduction_pct:g}%, or p95 ≥ −{self.s.min_latency_gain_pct:g}%)"})
            if not gains:
                return self._result(False, checks, "Rejected: the change passed the quality guardrails but provides no "
                                    "meaningful benefit, so the existing architecture is kept.", baseline, candidate,
                                    override_used)
        shown = gains or weak_gains
        msg = "Accepted: no metric regressed beyond its threshold" + (f" ({'; '.join(shown)})" if shown else "")
        return self._result(True, checks, msg, baseline, candidate, override_used)

    def _result(self, accepted: bool, checks: list[dict[str, Any]], reason: str, baseline: dict[str, Any],
                candidate: dict[str, Any], override_used: bool) -> dict[str, Any]:
        return {"accepted": accepted, "decision": "accept" if accepted else "reject", "reason": reason,
                "checks": checks, "thresholds": self.thresholds(), "override_used": override_used,
                "baseline_metrics": baseline["metrics"], "candidate_metrics": candidate["metrics"],
                "sample_size": candidate["metrics"]["sample_size"], "conditions": candidate["conditions"]}
