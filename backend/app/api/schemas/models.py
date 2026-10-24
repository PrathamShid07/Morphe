"""Pydantic request/response contracts for the REST API."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Importance = Literal["low", "normal", "high", "critical"]
Operation = Literal["split_module", "merge_modules", "add_shortcut_edge"]


class Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


# ------------------------------------------------------------------ errors
class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: Any = None


class ErrorResponse(BaseModel):
    error: ErrorBody


# --------------------------------------------------------------- ingestion
class IngestionRequest(BaseModel):
    source_name: str = Field(..., min_length=1, max_length=200, examples=["django_notes.txt"])
    text: str = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict,
                                     description="Optional: topic, parent_topic, module_name and any provenance fields")
    tags: list[str] = Field(default_factory=list, max_length=20)
    importance: Importance = "normal"
    pinned: bool = False
    operation_id: str | None = Field(None, max_length=64, description="Idempotency key")


class IngestionResponse(Loose):
    source_id: str
    status: Literal["completed", "duplicate"]
    chunks_created: int
    module_ids: list[str]
    new_module_ids: list[str] = []
    tokens_added: int
    active_tokens: int
    usage_percent: float
    operation_id: str | None = None
    message: str | None = None
    compression: dict[str, Any] | None = None


# ------------------------------------------------------------------- query
class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, examples=["How does Django URL routing work?"])
    top_k: int = Field(5, ge=1, le=50)
    debug_trace: bool = False
    mode: Literal["graph", "global"] = "graph"


class QueryResult(Loose):
    memory_id: str
    text: str
    score: float
    module_id: str
    module_name: str | None = None
    source_name: str | None = None


class QueryResponse(Loose):
    query_id: str
    architecture_version: str | None
    results: list[QueryResult]
    latency_ms: float
    mode: str
    trace: dict[str, Any] | None = None


# ---------------------------------------------------------------- memories
class MemoryPatch(BaseModel):
    pinned: bool | None = None
    importance: Importance | None = None


# -------------------------------------------------------------- compression
class CompressionRunRequest(BaseModel):
    mode: Literal["auto", "normal", "emergency"] = "auto"
    dry_run: bool = False
    operation_id: str | None = Field(None, max_length=64)


class BudgetRequest(BaseModel):
    budget_tokens: int | None = Field(None, ge=1, description="null restores the configured budget")


# -------------------------------------------------------------- adaptation
class AdaptationRequest(BaseModel):
    operation: Operation
    module_ids: list[str] = Field(..., min_length=1, max_length=2)
    validation_override: bool = False
    operation_id: str | None = Field(None, max_length=64)


# ---------------------------------------------------------------- evaluation
class EvaluationRunRequest(BaseModel):
    mode: Literal["graph", "global"] = "graph"
    version_id: str | None = None
    splits: list[Literal["dev", "heldout"]] | None = None


class VersionCompareRequest(BaseModel):
    from_version: str
    to_version: str
    evaluate: bool = True


# ----------------------------------------------------------------- versions
class RestoreRequest(BaseModel):
    operation_id: str | None = Field(None, max_length=64)
    reason: str | None = Field(None, max_length=300)
    evaluate: bool = True


class ResetRequest(BaseModel):
    confirm: str = Field(..., description='must equal "RESET"')
    reload_dataset: bool = True
