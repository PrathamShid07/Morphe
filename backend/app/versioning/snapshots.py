"""Architecture versions: lightweight manifests + separately stored, checksummed full snapshots."""
from __future__ import annotations

from typing import Any

from ..common import IntegrityError, NotFoundError, get_logger, iso, log_event
from ..storage.mongo_store import MongoStore, clean
from .state import ArchitectureState

log = get_logger("versioning")
ACTIVE_KEY = "active_architecture_version"


class VersionStore:
    def __init__(self, mongo: MongoStore, dimension: int):
        self.mongo = mongo
        self.dimension = dimension

    # ---------------------------------------------------------------- pointer
    def active_id(self) -> str | None:
        return self.mongo.get_state(ACTIVE_KEY)

    def set_active(self, version_id: str) -> None:
        if not self.mongo.col("architecture_versions").find_one({"version_id": version_id}):
            raise NotFoundError(f"version {version_id} does not exist", code="version_not_found")
        self.mongo.set_state(ACTIVE_KEY, version_id)

    # ----------------------------------------------------------------- create
    def create(self, state: ArchitectureState, *, reason: str, operation_type: str, operation_id: str | None = None,
               parent_version_id: str | None = None, evaluation: dict[str, Any] | None = None,
               validation: dict[str, Any] | None = None, summary: dict[str, Any] | None = None,
               restored_from: str | None = None, activate: bool = True) -> dict[str, Any]:
        problems = state.validate(self.dimension)
        if problems:
            raise IntegrityError("refusing to snapshot an invalid architecture: " + "; ".join(problems[:5]),
                                 details=problems)
        n = self.mongo.next_counter("architecture_version")
        vid = f"arch_{n:04d}"
        checksum = state.checksum()
        now = iso()
        manifest = {
            "version_id": vid, "created_at": now, "parent_version_id": parent_version_id, "reason": reason,
            "operation_type": operation_type, "operation_id": operation_id,
            "node_count": len(state.modules), "content_module_count": len(state.content_modules()),
            "edge_count": len(state.edges), "co_access_edge_count": len(state.co_access_edges()),
            "memory_count": state.total_assigned(), "checksum": checksum, "summary": summary or {},
            "evaluation": evaluation, "validation": validation or {"integrity_check": "passed"},
            "restored_from": restored_from, "status": "available"}
        self.mongo.col("architecture_snapshots").insert_one(
            {"version_id": vid, "created_at": now, "checksum": checksum, "state": state.to_dict()})
        self.mongo.col("architecture_versions").insert_one(dict(manifest))
        if activate:
            self.mongo.set_state(ACTIVE_KEY, vid)
        log_event(log, "version_created", version_id=vid, operation_type=operation_type, nodes=len(state.modules),
                  edges=len(state.edges))
        return manifest

    # ------------------------------------------------------------------- read
    def manifest(self, version_id: str) -> dict[str, Any]:
        m = clean(self.mongo.col("architecture_versions").find_one({"version_id": version_id}))
        if not m:
            raise NotFoundError(f"version {version_id} does not exist", code="version_not_found")
        return m

    def verify(self, version_id: str) -> dict[str, Any]:
        snap = self.mongo.col("architecture_snapshots").find_one({"version_id": version_id})
        if not snap:
            return {"valid": False, "problems": ["snapshot is missing"]}
        try:
            state = ArchitectureState.from_dict(snap["state"])
        except Exception as exc:
            return {"valid": False, "problems": [f"snapshot is unreadable: {type(exc).__name__}"]}
        problems = []
        if state.checksum() != snap.get("checksum"):
            problems.append("snapshot checksum mismatch (corrupt snapshot)")
        problems += state.validate(self.dimension)
        return {"valid": not problems, "problems": problems}

    def snapshot_state(self, version_id: str) -> ArchitectureState:
        check = self.verify(version_id)
        if not check["valid"]:
            raise IntegrityError(f"version {version_id} cannot be used: " + "; ".join(check["problems"][:3]),
                                 code="snapshot_invalid", details=check["problems"])
        snap = self.mongo.col("architecture_snapshots").find_one({"version_id": version_id})
        return ArchitectureState.from_dict(snap["state"])  # type: ignore[index]

    def describe(self, manifest: dict[str, Any], active: str | None = None, check: bool = True) -> dict[str, Any]:
        active = active if active is not None else self.active_id()
        v = self.verify(manifest["version_id"]) if check else {"valid": True, "problems": []}
        return {**manifest, "is_active": manifest["version_id"] == active, "restorable": v["valid"],
                "snapshot_problems": v["problems"]}

    def list_versions(self, limit: int = 20, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        col = self.mongo.col("architecture_versions")
        total = col.count_documents({})
        docs = [clean(d) for d in col.find({}).sort("created_at", -1).skip(offset).limit(limit)]
        active = self.active_id()
        return [self.describe(d, active) for d in docs if d], total  # type: ignore[arg-type]

    def update_manifest(self, version_id: str, fields: dict[str, Any]) -> None:
        self.mongo.col("architecture_versions").update_one({"version_id": version_id}, {"$set": fields})

    def lineage(self, version_id: str) -> list[str]:
        """version_id and its ancestors, newest first."""
        out: list[str] = []
        cur: str | None = version_id
        while cur and cur not in out:
            out.append(cur)
            cur = self.manifest(cur).get("parent_version_id")
        return out

    def genesis_id(self, from_version: str | None = None) -> str | None:
        """Oldest version in the lineage that actually contains memories (the 'fixed' pre-adaptation architecture)."""
        start = from_version or self.active_id()
        if not start:
            return None
        chain = self.lineage(start)  # newest first
        for vid in reversed(chain):
            if self.manifest(vid).get("memory_count", 0) > 0:
                return vid
        return chain[-1]
