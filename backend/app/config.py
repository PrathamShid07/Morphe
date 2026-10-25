"""Application settings. Every threshold in MORPHE is configuration, never a constant in an algorithm."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"), env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- application -------------------------------------------------------
    app_env: Literal["development", "test", "production"] = "development"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"
    log_json: bool = False
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    max_request_bytes: int = 2_000_000
    debug_trace_enabled: bool = True

    # --- embeddings --------------------------------------------------------
    embedding_backend: Literal["sentence_transformers", "hashing"] = "sentence_transformers"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dimension: int = 384
    embedding_batch_size: int = 64

    # --- Qdrant ------------------------------------------------------------
    qdrant_mode: Literal["local", "memory", "server"] = "local"
    qdrant_local_path: str = str(REPO_ROOT / "data" / "qdrant")
    qdrant_url: str = "http://localhost:6333"
    qdrant_memory_collection: str = "morphe_memories"
    qdrant_module_collection: str = "morphe_module_profiles"

    # --- MongoDB -----------------------------------------------------------
    mongo_backend: Literal["pymongo", "mongomock"] = "pymongo"
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "morphe"
    mongodb_timeout_ms: int = 3000

    # --- ingestion ---------------------------------------------------------
    max_ingest_chars: int = 1_000_000
    chunk_max_tokens: int = 200
    chunk_overlap_tokens: int = 25
    module_assign_threshold: float = 0.45

    # --- retrieval ---------------------------------------------------------
    top_k_default: int = 5
    top_k_max: int = 50
    max_query_chars: int = 2000  # raised by the BEIR benchmark: ArguAna queries are full arguments
    seed_module_count: int = 3
    max_candidate_modules: int = 8
    per_module_candidates: int = 15
    fallback_enabled: bool = True
    fallback_min_score: float = 0.30
    # Phase-2 determinism: complete exact-score tie groups that straddle the vector-search limit (see storage/qdrant_store.py)
    tie_safe_search: bool = True

    # --- experimental strategy: progressive graph expansion (retrieval/strategies.py) ----------------------------
    progressive_seed_modules: int = 3          # stage 1: top-N modules by profile similarity
    progressive_step_modules: int = 3          # modules added per expansion step
    progressive_max_modules: int = 12          # search budget (modules); 0 = no module cap
    progressive_max_steps: int = 4             # maximum expansion steps after stage 1
    progressive_policy: Literal["profile", "graph", "blend"] = "profile"  # which unsearched modules come next
    progressive_graph_bonus: float = 0.05      # blend policy: cosine-units bonus for modules linked to an already searched module
    progressive_patience: int = 1              # stop after this many consecutive expansion steps leave the top-k membership unchanged
    progressive_fallback_on_budget: bool = True  # budget exhausted while the top-k was still changing -> global fallback

    # --- experimental strategy: soft graph routing (rerank of a global dense candidate pool) ---------------------
    soft_candidate_pool: int = 100             # size of the global dense candidate set that is re-ranked
    soft_weight: float = 0.02                  # lambda: maximum score shift (cosine units) the routing prior can cause; 0 = pure dense ranking
    soft_graph_share: float = 0.3              # gamma: share of the prior that comes from graph distance (rest: profile similarity)
    soft_seed_modules: int = 3                 # seeds from which graph distance is measured
    soft_graph_depth: int = 2                  # edges beyond this distance contribute no graph prior

    # --- compression -------------------------------------------------------
    memory_budget_tokens: int = 100_000
    compression_start_pct: float = 75
    emergency_cleanup_pct: float = 90
    cleanup_target_pct: float = 82
    compression_target_pct: float = 70
    min_summary_saving_pct: float = 15
    compression_cooldown_seconds: float = 30
    compression_mode: Literal["inline", "background", "off"] = "background"
    near_duplicate_similarity: float = 0.97
    summary_group_min_size: int = 3
    summary_group_similarity: float = 0.55

    # --- structural adaptation --------------------------------------------
    adaptation_query_batch: int = 50
    auto_adaptation: bool = True
    adaptation_inline: bool = False
    adaptation_auto_apply: bool = True
    adaptation_max_ops_per_window: int = 1
    adaptation_max_proposals_per_window: int = 5
    adaptation_round_robin: bool = False  # interleave operation types when only some proposals can be evaluated per window
    adaptation_rejected_cooldown_windows: int = 3
    split_capacity_threshold: int = 1000
    split_mixed_min_records: int = 16
    split_min_group_size: int = 4
    split_max_groups: int = 4
    split_min_silhouette: float = 0.08
    split_mixed_min_silhouette: float = 0.10
    merge_profile_similarity: float = 0.85
    merge_overlap_min: float = 0.30
    merge_pair_similarity: float = 0.85
    merge_max_conflicts: int = 0
    coaccess_min_count: int = 10
    coaccess_min_window_queries: int = 20
    max_edges_total: int = 60
    max_coaccess_per_node: int = 4

    # --- evaluation & validation ------------------------------------------
    eval_dataset_path: str = str(REPO_ROOT / "data" / "evaluation_queries.json")
    eval_top_k: int = 5
    eval_repetitions: int = 3
    min_eval_queries: int = 10
    max_recall_regression: float = 0.02
    max_mrr_regression: float = 0.02
    max_p95_latency_regression_pct: float = 10.0
    latency_noise_floor_ms: float = 1.0
    eval_depth: int = 10  # ranked-list depth retrieved during candidate validation (>= 10 so nDCG@10 / MRR@10 are well defined)
    acceptance_profile: Literal["legacy", "beir"] = "legacy"  # beir: guard nDCG@10, Recall@5, MRR@10 (+ p95)
    max_ndcg_regression: float = 0.02
    require_benefit: bool = False  # a change must also show a meaningful benefit, not merely avoid harm
    min_quality_gain: float = 0.005  # absolute nDCG@10 or Recall@5 gain that counts as a benefit
    min_candidate_reduction_pct: float = 5.0  # relative cut of the mean eligible candidate pool that counts as a benefit
    min_latency_gain_pct: float = 5.0  # relative p95 improvement (above the noise floor) that counts as a benefit
    eval_interleaved: bool = False  # measure baseline and candidate alternately per query/repetition (paired latency)
    benefit_requires_ci: bool = False  # a quality gain only counts if its paired-bootstrap 95% interval is above zero
    cumulative_guard: bool = False  # candidate must also stay within the guardrails relative to the INITIAL architecture
    benefit_bootstrap_resamples: int = 500
    allow_validation_override: bool = False
    post_commit_verification: bool = True
    regression_monitor_min_samples: int = 20
    mutation_lock_timeout_seconds: float = 60

    # --- BEIR benchmark ----------------------------------------------------
    beir_data_dir: str = str(REPO_ROOT / "data" / "beir")
    beir_artifacts_dir: str = str(BACKEND_DIR / "artifacts" / "beir")
    beir_strategies_dir: str = str(BACKEND_DIR / "artifacts" / "beir_strategies")  # retrieval-strategy comparison runs (evaluation/run_strategies.py)

    # --- demo --------------------------------------------------------------
    samples_dir: str = str(REPO_ROOT / "data" / "samples")
    replay_queries_path: str = str(REPO_ROOT / "data" / "demo_replay_queries.json")
    allow_admin_endpoints: bool = True

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        if not 0 < self.compression_start_pct < self.emergency_cleanup_pct <= 100:
            raise ValueError("require 0 < COMPRESSION_START_PCT < EMERGENCY_CLEANUP_PCT <= 100")
        if not 0 < self.cleanup_target_pct < self.emergency_cleanup_pct:
            raise ValueError("CLEANUP_TARGET_PCT must be below EMERGENCY_CLEANUP_PCT")
        if not 0 < self.compression_target_pct < self.compression_start_pct:
            raise ValueError("COMPRESSION_TARGET_PCT must be below COMPRESSION_START_PCT")
        if self.memory_budget_tokens <= 0:
            raise ValueError("MEMORY_BUDGET_TOKENS must be positive")
        if self.adaptation_query_batch <= 0:
            raise ValueError("ADAPTATION_QUERY_BATCH must be positive")
        if self.embedding_backend == "hashing" and self.app_env == "production":
            raise ValueError("EMBEDDING_BACKEND=hashing is a test-only fake and is not allowed in production")
        if self.mongo_backend == "mongomock" and self.app_env == "production":
            raise ValueError("MONGO_BACKEND=mongomock is test-only")
        if self.chunk_overlap_tokens >= self.chunk_max_tokens:
            raise ValueError("CHUNK_OVERLAP_TOKENS must be smaller than CHUNK_MAX_TOKENS")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
