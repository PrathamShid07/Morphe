# Why is filtered vector retrieval slow? (Qdrant local vs server, BEIR corpora)

Phase 4 of the post-benchmark work. The v2 benchmark found that graph routing cut the eligible candidate pool by 77–89 % yet *raised* p95 latency (SciFact 26 → 43 ms end-to-end).
Question: is that due to application overhead, the Qdrant filter configuration, graph traversal, or the local deployment mode?

**Answer: it is the Qdrant deployment. The graph logic costs about 0.2 ms. In Qdrant's local (in-process) mode a filtered search costs more than an unfiltered one and does not depend on how many documents are eligible; payload indexes do not exist in that mode.
A server-mode Qdrant with payload indexes removes that overhead (≈ 1 ms per search), but graph routing is still not faster than global search there. Retrieval semantics are identical in every configuration.**

Artifacts: `backend/artifacts/beir_phase2/perf_01/<dataset>/qdrant_perf.json`. Code: `backend/app/evaluation/qdrant_perf.py`.
Reproduce (needs an isolated server, see below): `cd backend && ../.venv/bin/python -m app.evaluation.qdrant_perf --server --run-id <new id>`.

## Machine and configuration labels (read before comparing numbers)

* **One machine, one session, sequential runs**: Apple-silicon laptop (10 cores, 16 GB), Python 3.12.14, qdrant-client 1.20.0, nothing else CPU-heavy running.
  Every number in this document comes from that session (`perf_01`); none is mixed with the benchmark run's numbers (`diag_03`/v2), although they agree in magnitude.
* **local** = in-process `QdrantClient(":memory:")`, the deployment used in every benchmark so far.
* **server** = a separate Qdrant 1.20.0 process (official release binary, downloaded to `.tools/qdrant/`; SHA-256 of the archive recorded in `.tools/qdrant/SOURCE.txt`, not independently verified against a publisher checksum),
  isolated storage under `data/qdrant_perf_server/`, localhost REST on port 6343, telemetry disabled, default optimizer settings. Server latencies include the HTTP round trip and JSON encoding.
* Three server collection variants: **server_index** (keyword payload indexes on `module_id`, `status`, `source_id`, created *before* ingestion, as `QdrantStore.ensure_collections` does in server mode),
  **server_noindex** (no payload indexes), **server_hnsw** (indexes plus an HNSW graph forced by `indexing_threshold=100`; the default threshold is 20,000 vectors, so by default these 4–9 k-document collections are scanned, not graph-indexed;
  `indexed_vectors_count` confirms HNSW was built only in this variant).
* 100 validation queries per dataset (query vectors only, no labels), one warm-up and the median of 5 repetitions per query, then p50 over queries. Routing (seed and expansion modules per query) comes from the real router and is shared by every variant.
  Mean modules in the filter ≈ 8, mean eligible documents 894 / 828 / 1,044 of 5,183 / 3,633 / 8,674 (17 % / 23 % / 12 % of the corpus).

## What the benchmark's Qdrant configuration actually was

* Local in-process mode. **There were no payload indexes**: `QdrantStore.ensure_collections` creates them only when `not is_local`, and in local mode `create_payload_index` is a no-op in `qdrant-client` (it only records text indexes). So the answer to "is `module_id` indexed?" is *no, and it cannot be in local mode*.
  In server mode the three keyword indexes are created right after the collection, before ingestion, and the server's collection info shows them (`payload_schema` in the artifact).
* **Active-status filtering is applied to every search, including the global baseline**: `search_memories` always sends `status == "active"`. In the benchmark sandbox every point is active, so that condition is semantically redundant, but it is not free (below).
* Read from the `qdrant-client` 1.20.0 source (`local/local_collection.py`): a local-mode search (1) scores **all** stored vectors with one matrix product, (2) builds the payload mask by a **Python loop over every point's payload** that evaluates the filter, and (3) sorts all scores before applying the mask.
  This is why the cost scales with corpus size and the number of filter conditions, and not with the number of eligible points.

## Raw store search time (ms, p50 over 100 queries; the store call only, limit 100)

| | SciFact | NFCorpus | ArguAna |
|---|---|---|---|
| **numpy exact, all rows** (float32 matmul + top-100; diagnostic baseline) | 0.03 | 0.03 | 0.07 |
| numpy exact, candidate rows only (gather + matmul) | 0.05 | 0.05 | 0.06 |
| local: unfiltered | 1.75 | 1.28 | 2.74 |
| local: `status` only (what global retrieval sends today) | 18.63 | 13.29 | 30.98 |
| local: `module_id` only | 24.40 | 17.21 | 40.94 |
| local: `status` + `module_id` (what graph retrieval sends today) | 35.58 | 25.23 | 59.84 |
| server_index: unfiltered | 1.05 | 1.02 | 1.13 |
| server_index: `status` only | 1.05 | 1.03 | 1.13 |
| server_index: `status` + `module_id` | 1.24 | 1.23 | 1.26 |
| server_noindex: `status` only | 22.10 | 14.25 | 36.73 |
| server_noindex: `status` + `module_id` | 44.79 | 31.17 | 76.42 |
| server_hnsw: `status` only | 1.02 | 0.97 | 1.11 |
| server_hnsw: `status` + `module_id` | 1.05 | 1.06 | 1.07 |

Reading the table:

1. **Local mode:** an unfiltered search costs 1.3–2.7 ms; adding the `status` condition multiplies it by about 10 (≈ 3.3 µs per stored point per condition: (18.63 − 1.75) ms / 5,183 points); adding the `module_id` condition doubles it again.
   The extra cost is the Python mask loop over *all* points; selecting only 12–23 % of them saves nothing. So in local mode the benchmark's "global dense" baseline was itself about 10× slower than an unfiltered search (≈ 17 ms instead of ≈ 1.75 ms on SciFact), and graph retrieval
   was about 2× slower than that baseline.
2. **Server without payload indexes (`server_noindex`) is as slow as local mode** (22 / 45 ms on SciFact), i.e. the server does not help by itself. **Payload indexes are what removes the overhead**: `server_index` runs the same filters in ≈ 1.0–1.3 ms.
3. **Server with indexes:** the filter adds ≈ 0.2 ms; global (1.05) and graph-filtered (1.24) are within about 20 % of each other, and both are dominated by the HTTP round trip (an unfiltered search takes 1.05 ms).
   HNSW does not change that at this size (1.05 vs 1.24 → 1.02 vs 1.05, differences of tenths of a millisecond; I did not test whether those differences exceed run-to-run noise).
4. **Exact in-process numpy search over the whole corpus takes 0.03–0.07 ms** at these corpus sizes, 15–30× faster than the server's round trip and several hundred times faster than the local-mode filtered path. Restricting to candidate rows is not faster (gather cost ≈ matmul cost). At ≤ 9 k documents there is no search-time problem for routing to solve;
   whether routing helps at much larger scale (10⁶ documents) was **not tested** and these numbers cannot be extrapolated to it.

## End-to-end through the real `RetrievalEngine` (ms, p50 over 100 queries)

| Store | SciFact global / graph | NFCorpus global / graph | ArguAna global / graph |
|---|---|---|---|
| local | 18.69 / 35.74 | 20.48 / 38.46 | 31.15 / 60.17 |
| server_index | **1.01 / 1.75** | **1.02 / 1.80** | **1.17 / 1.85** |
| server_noindex | 22.70 / 47.52 | 14.27 / 33.04 | 38.95 / 79.32 |
| server_hnsw | 1.00 / 1.61 | 0.95 / 1.62 | 1.11 / 1.69 |

Stage means for graph retrieval on `server_index` (SciFact): seed selection 0.55 ms, expansion 0.04, filtered search 1.14, ranking 0.01, fallback 0.01. **Even with indexes the graph path is about 0.7–0.8 ms slower than global**
because it makes two server calls (a module-profile search, then the filtered memory search) where global makes one; the expansion and ranking logic is negligible (≈ 0.05 ms).
Local-mode stage numbers (seed 0.17, expand 0.04, search 35.5) show the same: the application's graph code is not the bottleneck; the single filtered store call is.
(NFCorpus local global 20.5 ms here vs 12.3 ms in the diagnostics run is within the spread of a different 100-query subset and a laptop that is not isolated; I did not investigate it further.)

## Does server mode change retrieval semantics? No.

* The engine's top-10 on every server variant is identical to the local engine's for **100 / 100 queries, in both modes, on all three datasets** (including the HNSW variant at these sizes).
* All stores return the exact top-10 *set* for global search on 100/100 (SciFact, NFCorpus) and 99/100 (ArguAna, all stores including local, and 100/100 for the candidate-restricted lists); the single ArguAna difference is shared by every store, and is
  most plausibly a score tie at rank 10 that the numpy baseline (which orders ties arbitrarily) resolves differently, but I did not verify that individually.
* So the speed difference between configurations comes from **deployment and indexing, not from a change in what is retrieved**. I did not inspect the server's query planner decisions (for collections this small the server scans; I only observed timings), so I make no claim about planner internals.

## Conclusions and what they do and do not support

* The v2 finding "graph routing raised latency" is **true for the local backend and explained by it**, but it says nothing about routing in general. The original "global dense" baseline was also handicapped by the redundant `status` filter in local mode; an unfiltered local global search would have been about 10× faster (1.75 ms vs 18.6 ms on SciFact).
  The v2 latency tables remain correct descriptions of what was run and stay in place; they must be read with this caveat.
* With a properly configured server (payload indexes before ingestion), search time is ≈ 1 ms for every variant and the 77–89 % candidate-pool reduction produces **no measurable search-time benefit**; graph retrieval remains slightly slower than global (+0.2 ms for the filter, +0.5 ms for the extra profile search).
* Nothing here shows that hard filtering saves time at the corpus sizes tested, in any deployment. A latency benefit of routing would require a much larger corpus or a backend whose cost scales with the eligible set, neither of which was tested.
* Practical fixes this suggests (not yet applied, because Phase 5 design is deferred until these diagnostics are reviewed): skip the redundant `status` condition when every point is active (≈ 10× faster local global search), and prefer an indexed server deployment for any latency claim.

## Limits

* One machine, one session, 100 queries per dataset, localhost REST; absolute numbers are specific to this laptop. p95 values are in the artifact; no confidence intervals for latency were computed, and differences below roughly a millisecond between server variants should not be over-read.
* The server was not CPU-pinned or isolated from other processes; the Qdrant server and the Python client share the machine.
* HNSW was forced on small collections purely as a diagnostic; its recall (here 100 %) at larger sizes was not measured.
* Larger-scale behaviour (≥ 10⁵ documents) was not tested.
