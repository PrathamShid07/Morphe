# Implementation decisions and spec conflicts

Priority used: (1) the problem statement's mandatory requirements, (2) `MORPHE_Technical_Project_Specification.docx`, (3) repository conventions and the screenshots, (4) engineering defaults.

## Where the problem statement overrides the specification
| Spec says | Done instead | Why |
|---|---|---|
| Post-change evaluation, accept/reject and rollback are deferred (§1.3, §8.6, §12.3, M7) | Implemented as core: sandbox evaluation, thresholds, post-commit verification, versions, cross-store restore, monitoring rollback | explicit mandatory requirement |
| `GET /versions` is read-only history with no restore | full version API incl. compare, preview and `POST …/restore` | mandatory |
| Version history "labelled as history only" | versions are restorable (checksum-verified) | mandatory |

## Genuine ambiguities and the resolution chosen
1. **Deterministic IDs.** Source id = `src_<sha256(normalised text)[:12]>`; memory id = `mem_<source-hash>_<chunk-index>` (the spec shows ULIDs). Re-ingesting identical content is therefore detectable and the evaluation labels stay valid across resets. Event/query/operation ids are time-sortable ULID-style ids.
2. **Where access statistics live.** The spec lists `access_count`/`last_accessed_at` in the Qdrant payload *and* MongoDB. They are authoritative in MongoDB only (one bulk write per query); mirroring them into Qdrant would double write cost and add a consistency hazard.
3. **Superseded records stay in Qdrant** with `status: superseded` instead of being deleted (spec §7.3 says remove the point). This keeps summary provenance readable ("summarised from…"), keeps replacement recoverable, and makes structural rollback unable to revive them (restore only places currently-active memories). Cost: superseded text still occupies disk; the dashboard reports tokens (active only), RSS and Qdrant disk separately. User deletion and emergency deletion hard-delete the vector and keep a tombstone (no text) in MongoDB.
4. **Container modules.** A module that has been split (or created as a `parent_topic` group) holds no records, has no profile vector, and exists to carry hierarchy; routing expands through it to siblings. Empty leaf modules are retired automatically (with their edges).
5. **Version policy.** The spec says "every accepted mutation creates a snapshot"; ingestion also changes structure, so versions are *not* created per ingestion. Instead a checkpoint is taken before every mutation that could be rolled back, which captures any drift. Restores create a new version rather than moving the pointer, so history stays linear and honest about what the live state really is.
6. **Latency measurement for validation.** Production latency includes query embedding and is shown live; evaluation latency excludes embedding and uses isolated in-memory stores, median of repeats, plus a 1 ms absolute noise floor. At ~1 ms, a pure 10 % rule would reject on jitter.
7. **Graph path is not faster at this scale.** With 60–90 chunks, brute-force global search (0.5–0.7 ms) beats module-search + filtered search (1.2–1.6 ms). The benefit shown is search-space narrowing (candidates 38 vs 83) with equal-or-better quality; speed benefit needs a larger corpus and a real Qdrant server with payload indexes (indexes have no effect in local mode). This is reported, not hidden.
8. **`per_module_candidates`.** A union search filtered by module ids returns the same top results as per-module searches merged, so the setting is used as the guided search *pool size* (`max(top_k, per_module_candidates)`) before ranking.
9. **Fallback masks routing misses by design**, so reports include guided-only Recall/MRR, and acceptance uses the user-visible (post-fallback) metrics.
10. **Mixed-module trigger.** The spec's "1,000 records" is kept as the capacity trigger. For the demo-sized data the mixed-topic trigger is ≥ 16 records with silhouette ≥ 0.10 (real MiniLM embeddings of "web / data / tooling" Python notes score ≈ 0.14; random groupings ≈ 0). Both are settings.
11. **One operation per window.** Spec §8 says execute *one* structural operation per window; implemented, with the rejected-proposal cooldown, so demonstrating split, merge and shortcut takes three windows (`demo.py full`).
12. **Compression targets.** Spec only defines the 82 % emergency target. A separate `COMPRESSION_TARGET_PCT` (70 %) lets normal jobs stop; skipped operations are logged with reasons but capped at 5 per job to avoid event spam.
13. **Summarisation.** Extractive and fact-preserving: sentences with numbers, dates, identifiers or code-like tokens are mandatory; a summary that cannot keep every such fact within the size target is **skipped**, not truncated. Records with code fences, key/secret/connection-string patterns are never rewritten. Embeddings are never used to generate text.
14. **Protected records.** Pinned or `importance=critical` records are excluded from deduplication retirement, summarisation, shortening and emergency deletion; manual delete requires `force=true`.
15. **Qdrant local mode** is opened by exactly one process; scripts talk to the API and never open the folder. Payload indexes are created only for server mode.

## Environment / tooling decisions
* **Python 3.12** venv (system default is 3.14; PyTorch wheels target 3.12). Exact tested versions in `backend/requirements.lock.txt`.
* **MongoDB** was not installed and Docker is unavailable, so a project-local MongoDB 8.0.12 binary is unpacked into the git-ignored `.tools/` and started by `scripts/dev_mongo.sh` with data in `data/mongo`. No system service, tap or package was installed. Any MongoDB 7/8 at `MONGODB_URI` works.
* **Tests** use `mongomock` and in-memory Qdrant plus a deterministic hashing embedder (`EMBEDDING_BACKEND=hashing`, refused when `APP_ENV=production`). `mongomock` is incompatible with PyMongo's `UpdateOne` in `bulk_write`, so `MongoStore._bulk` uses real bulk writes on MongoDB and a loop on the double. Real-model behaviour is covered by the scripted demo and the e2e tests, not by unit tests.
* **shadcn/ui**: the CLI generated `import { cn } from "cn"` and installed an unrelated npm package named `cn`. I removed the package and pointed those imports at `@/lib/utils`; `next-themes` (pulled in by the sonner wrapper) was replaced by the app's own theme provider. `@hookform/resolvers` is pinned to v5 (v6 has an unsatisfiable optional peer).
* Frontend charts use recharts through shadcn's `chart` wrapper; React Flow handles touch pan/pinch; nodes are not draggable below 1024 px so panning wins.
* No external service, no LLM, no API key is used anywhere; `.env` is git-ignored and `.env.example` contains only safe defaults.

## Not done / limits worth knowing
* Quality validation covers retrieval on a 59-query benchmark; it cannot prove improvement on other data. Replace the dataset for real corpora.
* Live p95 under production traffic is displayed but not an automatic rollback trigger.
* No undo endpoint for individual compression operations (originals remain recoverable in Qdrant as `superseded`, but there is no UI/API to revive them).
* No authentication, multi-tenancy or queue; adaptation/compression run in one in-process worker thread.
* PDF/DOCX extraction is not implemented (plain text, markdown and code files only).
