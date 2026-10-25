"""One-time, idempotent labelling of pre-account data on the shared plane.

Before user accounts existed, nothing recorded who could see a memory. The new rule is fail-closed: a shared-plane record is visible to
ordinary users only if it carries `visibility == "shared"`. This migration decides that label for every record that has none:

* records that come from the repository's own sample corpus (`data/samples/manifest.json`) are the shared demonstration knowledge -> `shared`;
* everything else (ad-hoc administrator ingests, test notes, unknown provenance) -> `restricted` (administrators only), so it never silently
  becomes visible to every registered user. An administrator can publish it later (`POST /sources/{id}/visibility`).

Properties:
* idempotent: only records without a `visibility` field are touched, so a second run changes nothing and an interrupted run simply resumes;
* reversible: before writing, the ids it is about to label are saved to a JSON backup, and `rollback()` removes exactly those labels;
* narrow: it never moves, rewrites or deletes memories, modules, versions or benchmark artifacts, and it does not touch the personal plane.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import get_logger, iso, log_event, new_id

log = get_logger("migration")
MIGRATION_ID = "accounts_v1_shared_visibility"


def sample_source_names(samples_dir: str) -> set[str]:
    """Source names of the repository's sample corpus. A relative SAMPLES_DIR (as in .env.example) is resolved against backend/ like the rest of the
    app, with the repository's own data/samples as a fallback, so the result does not depend on which directory a script was started from."""
    from .config import BACKEND_DIR, REPO_ROOT
    p = Path(samples_dir)
    candidates = [p] if p.is_absolute() else [Path.cwd() / p, BACKEND_DIR / p, REPO_ROOT / "data" / "samples"]
    man = next((c / "manifest.json" for c in candidates if (c / "manifest.json").is_file()), None)
    if man is None:
        log_event(log, "sample_manifest_missing", level=30, samples_dir=samples_dir)  # fail-closed: nothing is treated as shared demonstration content
        return set()
    data = json.loads(man.read_text(encoding="utf-8"))
    names: set[str] = set()
    for items in (data.get("sets") or {}).values():
        names |= {i["source_name"] for i in items if i.get("source_name")}
    return names


def _classify(doc: dict[str, Any], source_names: dict[str, str], samples: set[str]) -> str:
    if doc.get("kind", "chunk") != "chunk" and doc.get("merged_source_ids"):  # a summary is only as public as every source it came from
        origin = [source_names.get(s) for s in doc["merged_source_ids"]]
        return "shared" if origin and all(n in samples for n in origin) else "restricted"
    return "shared" if (doc.get("source_name") in samples) else "restricted"


def plan(ctx) -> dict[str, Any]:
    """What the migration would do, without changing anything."""
    samples = sample_source_names(ctx.settings.samples_dir)
    names = {d["source_id"]: d.get("source_name") for d in ctx.mongo.col("sources").find({}, {"source_id": 1, "source_name": 1})}
    todo = list(ctx.mongo.col("memory_metadata").find({"visibility": {"$exists": False}}, {"memory_id": 1, "source_name": 1, "kind": 1, "merged_source_ids": 1}))
    labels = [_classify(d, names, samples) for d in todo]
    total = ctx.mongo.col("memory_metadata").count_documents({})
    return {"migration": MIGRATION_ID, "records_total": total, "records_unlabelled": len(todo), "would_share": labels.count("shared"),
            "would_restrict": labels.count("restricted"), "sample_sources_known": len(samples),
            "legacy_visitor_history_rows": ctx.mongo.col("user_query_results").count_documents({"session_id": {"$exists": True}})}


def migrate(ctx, *, dry_run: bool = False) -> dict[str, Any]:
    p = plan(ctx)
    if dry_run or p["records_unlabelled"] == 0:
        return {**p, "applied": False, "changed": 0}
    samples = sample_source_names(ctx.settings.samples_dir)
    names = {d["source_id"]: d.get("source_name") for d in ctx.mongo.col("sources").find({}, {"source_id": 1, "source_name": 1})}
    todo = list(ctx.mongo.col("memory_metadata").find({"visibility": {"$exists": False}}, {"memory_id": 1, "source_name": 1, "source_id": 1, "kind": 1, "merged_source_ids": 1}))
    shared = [d["memory_id"] for d in todo if _classify(d, names, samples) == "shared"]
    restricted = [d["memory_id"] for d in todo if d["memory_id"] not in set(shared)]
    unlabelled_sources = [sid for sid in names if not ctx.mongo.col("sources").find_one({"source_id": sid, "visibility": {"$exists": True}})]
    run_id = new_id("mig")
    backup_dir = Path(ctx.settings.migration_backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{MIGRATION_ID}_{run_id}.json"
    backup.write_text(json.dumps({"migration": MIGRATION_ID, "run_id": run_id, "created_at": iso(), "previous_state": "no visibility/owner_user_id fields",
                                  "labelled_shared": shared, "labelled_restricted": restricted, "labelled_sources": unlabelled_sources}, indent=1), encoding="utf-8")
    ctx.mongo.col("migrations").insert_one({"run_id": run_id, "migration": MIGRATION_ID, "status": "running", "started_at": iso(), "backup": str(backup)})
    with ctx.coordinator.mutate("migration", meta={"migration": MIGRATION_ID, "run_id": run_id}):
        for ids, label in ((shared, "shared"), (restricted, "restricted")):
            if not ids:
                continue
            # Qdrant first, then Mongo: if the process stops in between, the unlabelled Mongo docs make the next run redo (and fix) exactly this batch.
            in_qdrant = [mid for mid, _, _ in ctx.qdrant.scroll_memories(status=None)]  # every record, any status
            ctx.qdrant.set_payload([m for m in ids if m in set(in_qdrant)], {"visibility": label, "owner_user_id": None})
            ctx.mongo.update_memory_metadata(ids, {"visibility": label, "owner_user_id": None})
        for sid in unlabelled_sources:
            ctx.mongo.col("sources").update_one({"source_id": sid}, {"$set": {"visibility": "shared" if names.get(sid) in samples else "restricted"}})
    result = {**p, "applied": True, "changed": len(todo), "labelled_shared": len(shared), "labelled_restricted": len(restricted), "backup": str(backup), "run_id": run_id}
    ctx.mongo.col("migrations").update_one({"run_id": run_id}, {"$set": {"status": "completed", "finished_at": iso(), "changed": len(todo),
                                                                          "labelled_shared": len(shared), "labelled_restricted": len(restricted)}})
    log_event(log, "migration_completed", migration=MIGRATION_ID, changed=len(todo), shared=len(shared), restricted=len(restricted))
    return result


def rollback(ctx, backup_path: str) -> dict[str, Any]:
    """Undo a migration run exactly: remove the labels it added (the records themselves were never altered otherwise)."""
    data = json.loads(Path(backup_path).read_text(encoding="utf-8"))
    ids = list(data["labelled_shared"]) + list(data["labelled_restricted"])
    with ctx.coordinator.mutate("migration_rollback", meta={"run_id": data["run_id"]}):
        in_qdrant = {mid for mid, _, _ in ctx.qdrant.scroll_memories(status=None)}
        ctx.qdrant.delete_payload_keys([m for m in ids if m in in_qdrant], ["visibility", "owner_user_id"])
        ctx.mongo.col("memory_metadata").update_many({"memory_id": {"$in": ids}}, {"$unset": {"visibility": "", "owner_user_id": ""}})
        ctx.mongo.col("sources").update_many({"source_id": {"$in": list(data.get("labelled_sources", []))}}, {"$unset": {"visibility": ""}})
        ctx.mongo.col("migrations").update_one({"run_id": data["run_id"]}, {"$set": {"status": "rolled_back", "rolled_back_at": iso()}})
    return {"run_id": data["run_id"], "labels_removed": len(ids)}
