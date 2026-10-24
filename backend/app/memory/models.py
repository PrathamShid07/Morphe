"""Memory record model and the payload/metadata projections stored in Qdrant and MongoDB."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

PENDING, ACTIVE, SUPERSEDED, DELETED = "pending", "active", "superseded", "deleted"
IMPORTANCE_LEVELS = ("low", "normal", "high", "critical")
PREVIEW_CHARS = 240


@dataclass
class MemoryRecord:
    memory_id: str
    module_id: str
    text: str
    source_id: str
    source_name: str
    created_at: str
    token_count: int
    content_hash: str
    section: str | None = None
    chunk_index: int = 0
    status: str = PENDING
    importance: str = "normal"
    pinned: bool = False
    summary_of: list[str] = field(default_factory=list)
    superseded_by: str | None = None
    tags: list[str] = field(default_factory=list)
    merged_source_ids: list[str] = field(default_factory=list)
    kind: str = "chunk"  # chunk | summary | shortened | document
    external_id: str | None = None  # original id in an external collection (e.g. BEIR corpus document id)
    dataset_id: str | None = None
    title: str | None = None

    def qdrant_payload(self) -> dict[str, Any]:
        payload = {
            "memory_id": self.memory_id, "module_id": self.module_id, "text": self.text, "source_id": self.source_id,
            "source_name": self.source_name, "section": self.section, "chunk_index": self.chunk_index,
            "created_at": self.created_at, "token_count": self.token_count, "status": self.status,
            "importance": self.importance, "pinned": self.pinned, "summary_of": self.summary_of,
            "superseded_by": self.superseded_by, "tags": self.tags, "content_hash": self.content_hash,
            "merged_source_ids": self.merged_source_ids, "kind": self.kind,
        }
        if self.external_id is not None:  # keep ordinary chunk payloads unchanged
            payload.update({"beir_doc_id": self.external_id, "dataset_id": self.dataset_id, "title": self.title})
        return payload

    def mongo_doc(self) -> dict[str, Any]:
        d = self.qdrant_payload()
        d.pop("text")
        d["text_preview"] = preview(self.text)
        return d


def preview(text: str, n: int = PREVIEW_CHARS) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    return flat if len(flat) <= n else flat[: n - 1].rstrip() + "…"


def text_fingerprint(text: str) -> str:
    """Whitespace/case-insensitive fingerprint used for exact-duplicate detection."""
    from ..common import sha256_hex
    return sha256_hex(re.sub(r"\s+", " ", text).strip().lower())


def is_protected(doc: dict[str, Any]) -> bool:
    return bool(doc.get("pinned")) or doc.get("importance") == "critical"
