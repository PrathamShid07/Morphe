"""Active-text token budget (NOT a RAM/disk budget; those are measured separately)."""
from __future__ import annotations

from typing import Any

from ..common import ValidationFailure
from ..storage.mongo_store import MongoStore


class BudgetTracker:
    def __init__(self, mongo: MongoStore, settings):
        self.mongo = mongo
        self.s = settings

    @property
    def budget_tokens(self) -> int:
        override = self.mongo.get_state("budget_tokens_override")
        return int(override) if override else self.s.memory_budget_tokens

    def set_override(self, tokens: int | None) -> int:
        if tokens is not None and tokens <= 0:
            raise ValidationFailure("budget_tokens must be positive")
        self.mongo.set_state("budget_tokens_override", tokens)
        return self.budget_tokens

    def usage(self) -> dict[str, Any]:
        tokens, count = self.mongo.active_token_total()
        budget = self.budget_tokens
        pct = round(tokens / budget * 100, 2) if budget > 0 else 0.0
        return {"active_tokens": tokens, "active_memories": count, "budget_tokens": budget, "usage_percent": pct,
                "budget_overridden": bool(self.mongo.get_state("budget_tokens_override")),
                "start_threshold_percent": self.s.compression_start_pct,
                "emergency_threshold_percent": self.s.emergency_cleanup_pct,
                "cleanup_target_percent": self.s.cleanup_target_pct,
                "normal_target_percent": self.s.compression_target_pct,
                "tokenizer": self.mongo.get_state("tokenizer_name")}

    def band(self, pct: float | None = None) -> str:
        pct = self.usage()["usage_percent"] if pct is None else pct
        if pct >= self.s.emergency_cleanup_pct:
            return "emergency"
        if pct >= self.s.compression_start_pct:
            return "pressure"
        return "normal"
