# Roadmap

> Last updated: 2026-03-19

42 open issues across 7 workstreams. This document establishes priority, ordering,
dependency chains, and parallelism opportunities.

## Issue Index

| #   | Title                                                    | Workstream  | Phase | Status |
| --- | -------------------------------------------------------- | ----------- | ----- | ------ |
| 88  | pymupdf4llm production integration (Phase 2)             | Foundation  | 0     | ✔      |
| 89  | **PR**: pymupdf4llm structured markdown extraction       | Foundation  | 0     | ✔      |
| 85  | fix(vision): chunk_index encoding overflow               | Foundation  | 0     | ✔      |
| 78  | refactor: executemany for config init                    | Foundation  | 0     | ✔      |
| 46  | refactor: move SQL batching helpers to db.py             | Foundation  | 0     | ✔      |
| 45  | refactor: .replace() instead of .format() in SQL         | Foundation  | 0     | ✔      |
| 16  | feat: connectivity test for configure_llm                | Foundation  | 0     | ✔      |
| 71  | docs: comprehensive documentation + typing + API ref     | Foundation  | 1     | ✔      |
| 101 | chore: rename to knowledge-base                          | Foundation  | 1     | ✔      |
| 95  | pluggable embedding providers                            | Embedding   | 2     | ✔      |
| 15  | parallelize map phase LLM calls                          | Extraction  | 2     | ✔      |
| 110 | pymupdf4llm Phase 3: narrow vision pipeline scope        | Ingest      | 2     | ✔      |
| 82  | extract inline images from web pages (Phase 1)           | Ingest      | 2     | ✔      |
| 126 | folder-level semantic embeddings for context boosting    | Search      | 2     | ✔      |
| 127 | prediction-error detection for stale search results      | Search      | 2     | ✔      |
| 128 | ingestion session tracking for co-occurrence signals     | Search      | 2     | ✔      |
| 130 | keyword intent extraction pre-filter for search          | Search      | 2     | ✔      |
| 99  | multi-space embedding architecture                       | Embedding   | 2     |        |
| 100 | dual chunking strategy (8K + 32K)                        | Embedding   | 2     |        |
| 106 | stage-2 reranking in hybrid search                       | Search      | 2     |        |
| 105 | auto-relationship discovery via similarity               | Search      | 2     | ✔      |
| 139 | chunk_sessions join table for N:M session tracking       | Search      | 2     |        |
| 163 | bug: qwen3.5 thinking-mode empty extraction              | Extraction  | 2.5   |        |
| 160 | fix: zombie conclusions after FK cleanup                 | Extraction  | 2.5   |        |
| 152 | fix: stale inline image chunks on re-ingest              | Ingest      | 2.5   |        |
| 151 | fix: getaddrinfo for SSRF IP check                       | Ingest      | 2.5   |        |
| 150 | improve zero-norm embedding vector handling              | Embedding   | 2.5   |        |
| 154 | refactor: consolidate transaction management             | Ingest      | 2.5   |        |
| 141 | refactor: unify multi-line SQL INSERT strings            | Foundation  | 2.5   |        |
| 140 | refactor: extraction module cleanup from #15             | Extraction  | 2.5   |        |
| 158 | chore: full Windows path support in folder summaries     | Search      | 2.5   |        |
| 162 | folder summary integration for ingest_url                | Search      | 3     |        |
| 164 | `<picture>` and srcset in inline image extraction        | Ingest      | 3     |        |
| 161 | keyword stopwords strips compound technical terms        | Search      | 3     |        |
| 147 | configurable stopword list for keyword extraction        | Search      | 3     |        |
| 155 | mixed raster+vector pages in vision pipeline             | Ingest      | 3     |        |
| 149 | ONNXProvider: pre-tokenized model support                | Embedding   | 3     |        |
| 146 | workspace-scoped prediction error filtering              | Search      | 3     |        |
| 145 | scope_miss prediction error type                         | Search      | 3     |        |
| 166 | scan_relationships: avoid redundant pairwise comparisons | Search      | 2.5   |        |
| 165 | auto_relate: fallback to abstract_chunk_id               | Search      | 2.5   |        |
| 125 | BM25-seeded HNSW insertion for graph quality             | Search      | 3     |        |
| 124 | decouple index construction from query serving           | Search      | 3     |        |
| 122 | docs: four-layer cognitive architecture                  | Foundation  | 3     |        |
| 94  | compressed vector indices (int8/bit)                     | Embedding   | 3     |        |
| 111 | pymupdf4llm Phase 4: hybrid enrichment                   | Ingest      | 3     |        |
| 131 | web image extraction Phase 2: rendered DOM images        | Ingest      | 3     |        |
| 132 | web image extraction Phase 3: canvas/SVG screenshots     | Ingest      | 3     |        |
| 129 | retrieval plan intermediate representation               | Search      | 3     |        |
| 63  | document watch/sync for auto-re-ingestion                | Ingest      | 3     |        |
| 64  | workspace/project tagging for chunk isolation            | Ingest      | 3     |        |
| 102 | hook-based memory-engine integration                     | Integration | 3     |        |
| 103 | wisdom → knowledge pipeline                              | Integration | 3     |        |
| 104 | memory → wisdom consolidation                            | Integration | 3     |        |
| 108 | OCR preprocessing for scanned documents                  | Ingest      | 4     |        |
| 109 | evaluate IBM Tableformer for tables                      | Ingest      | 4     |        |
| 107 | epic: semantic code indexing                             | Ingest      | 4+    |        |
| 12  | migrate entity graph to neo4j                            | Scale       | 4     |        |
| 65  | evaluate LanceDB as sqlite-vec alternative               | Scale       | 4     |        |
| 80  | web UI (Svelte + Rust WASM graph)                        | Scale       | 4     |        |

---

## Phase 0 — Finish Pending Work ✔

**Goal:** Land in-flight PRs, fix known bugs, clean up tech debt.

**All items complete:**

- ~~PR #89~~ merged → closed #88
- ~~#85~~ fixed (PR #112)
- ~~#78~~ done (PR #118)
- ~~#46~~ done (PR #114)
- ~~#45~~ done (PR #116)
- ~~#16~~ done (PR #115)

---

## Phase 1 — Documentation & Rename ✔

**Goal:** Stabilize the project identity and make it approachable.

**All items complete:**

1. ~~**#71**~~ — Comprehensive docs (PR #120)
2. ~~**#101**~~ — Rename to knowledge-base (PR #123)

---

## Phase 2 — Embedding Architecture + Search Quality (9/13 done)

**Goal:** Upgrade the embedding and search infrastructure.

**Completed:**

- ~~#95~~ pluggable embedding providers (PR #135)
- ~~#15~~ parallel map phase LLM calls (PR #133)
- ~~#110~~ vision Phase 3: narrow pipeline (PR #137)
- ~~#82~~ web image extraction Phase 1 (PR #142)
- ~~#126~~ folder-level semantic embeddings (PR #138)
- ~~#127~~ prediction-error detection (PR #143)
- ~~#128~~ ingestion session tracking (PR #134)
- ~~#130~~ keyword intent extraction (PR #136)
- ~~#105~~ auto-relationship discovery (PR #144)

**Remaining:**

```
                    ┌── #99 (multi-space) ───┐
                    │                        │
#95 (done) ─────────┤                        ├──▶ #106 (reranking)
                    │                        │
                    └── #100 (dual chunking) ┘

#139 (chunk_sessions N:M) ─── independent (#128 done, #105 done)
```

**Dependency chain:**

- **#99 (multi-space)** can now start — #95 (providers) is done.
- **#100 (dual chunking)** is independent of #99, can be parallel.
- **#106 (reranking)** depends on #99 — reranking across multiple embedding
  spaces requires the multi-space query layer.
- **#139 (chunk_sessions N:M)** depends on #128 (done). Originally gated by
  #105 but can now land independently as a schema improvement.

**Exit criteria:** Multiple embedding models coexist, Matryoshka truncation
works, search quality measurably improves via reranking.

---

## Phase 2.5 — Stabilize

**Goal:** Fix bugs and clean up tech debt from Phase 2 before adding more features.

```
#163 (qwen3.5 thinking-mode)    ─── bug, independent
#160 (zombie conclusions)        ─── bug, independent
#152 (stale inline image chunks) ─── bug, depends on #82 (done)
#151 (getaddrinfo SSRF)          ─── bug, depends on #82 (done)
#150 (zero-norm embeddings)      ─── fix, independent
#154 (transaction consolidation) ─── refactor, independent
#141 (unify SQL INSERTs)         ─── refactor, independent
#140 (extraction cleanup)        ─── refactor, depends on #15 (done)
#158 (Windows path support)      ─── chore, depends on #126 (done)
#165 (auto_relate fallback)      ─── fix, depends on #105 (done)
#166 (scan_relationships 2x)     ─── perf, depends on #105 (done)
```

**Parallelism:** All items are independent of each other. Any can be picked up
in any order. All are small-to-medium scope (< 1 session each).

**Why a separate phase:** These are follow-up fixes and cleanups from Phase 2
PRs. Landing them before Phase 3 prevents compounding tech debt and avoids
merge conflicts with the larger Phase 3 features.

**Exit criteria:** No known bugs from Phase 2 work. Clean ruff/pyright. Test
suite green without workarounds.

---

## Phase 3 — Intelligence, Integration & Search Refinement

**Goal:** Connect the four-layer architecture, refine search quality, and
polish ingest pipelines with follow-up enhancements.

```
#102 (hook-based memory integration) ──▶ #103 (wisdom→knowledge)
                                     ──▶ #104 (memory→wisdom)

#111 (vision Phase 4)       ─── depends on #110 (done)
#155 (mixed raster+vector)  ─── depends on #110 (done)
#131 (web images, Phase 2)  ─── depends on #82 (done)
#132 (web images, Phase 3)  ─── depends on #131
#164 (<picture>/srcset)     ─── depends on #82 (done)

#94 (int8/bit quantization) ─── depends on #99 (Phase 2)
#125 (BM25-seeded HNSW)     ─── depends on #99 (Phase 2)
#124 (build/serve pattern)  ─── independent

#106 lands here if Phase 2 ──▶ #129 (retrieval plan IR)

#162 (folder summary + ingest_url) ─── depends on #126 (done)
#161 (keyword compound terms)      ─── depends on #130 (done)
#147 (configurable stopwords)      ─── depends on #130 (done)
#146 (workspace prediction errors) ─── depends on #127 (done)
#145 (scope_miss error type)       ─── depends on #127 (done)
#149 (ONNX pre-tokenized)          ─── depends on #95 (done)
#122 (cognitive arch docs)         ─── independent

#63 (document watch/sync)   ─── independent
#64 (workspace tagging)     ─── independent
```

**Dependency chains:**

- **#102 → #103, #104**: Memory-engine integration is the prerequisite.
  External dependency: `memory-engine` must expose an MCP server.
- **#94, #125** both build on #99 (multi-space) from Phase 2.
- **#129 (retrieval plan IR)** benefits from #106 (reranking) — plan can
  specify reranking directives per sub-query.
- **#166, #165** are #105 follow-ups — moved to Phase 2.5 now that #105 has
  landed.

**Parallelism:**

- Follow-up enhancements (#162, #161, #147, #146, #145, #149, #164) are all
  independent of each other and of the integration work. Good quick-win
  candidates between larger features.
- #111 and #155 (vision pipeline) are independent of integration work.
- #63 (watch/sync) and #64 (workspace tagging) are standalone ingest
  improvements.
- #122 (docs) can be done anytime.

**Exit criteria:** Hooks log to memory-engine, consolidation proposes wisdom
candidates, search pipeline has reranking + retrieval plans, follow-up
enhancements from Phase 2 are landed.

---

## Phase 4 — New Frontends & Scale

**Goal:** Extend the knowledge base beyond research papers.

```
#108 (OCR preprocessing) ─┐
                          ├── can be parallel
#109 (Tableformer eval)  ─┘

#107 (code indexing epic) ─── multi-phase, starts here

#12 (neo4j migration)  ─── independent
#65 (LanceDB eval)     ─── independent
#80 (web UI)           ─── independent
```

These are **exploratory and high-effort** items. Each is independent and can be
prioritized based on which use case is most pressing at the time.

**#107 (code indexing)** is the largest item — an epic with 6 sub-phases. It
should be broken into sub-issues when work begins.

**#12 and #65** are mutually exclusive alternatives for scale — evaluate before
committing to either. neo4j for graph queries, LanceDB for vector queries. Both
solve problems that don't exist yet at current corpus size.

**#80 (web UI)** is a nice-to-have that becomes valuable once the knowledge base
has enough content to warrant visual exploration.

**Exit criteria:** At least one new ingest frontend working (OCR or code).

---

## Dependency Graph

```
Phase 0 ✔          Phase 1 ✔          Phase 2              Phase 2.5           Phase 3              Phase 4
────────           ────────           ────────             ────────            ────────             ────────

PR #89 ──────┐                        ✔ #95 ──▶ #99 ──▶ #106                  #102 ──┬──▶ #103     #108
#85 ─────────┤                        │         │                   #163       │      └──▶ #104     #109
#78 ─────────┼──▶ ✔ #71 (docs)       ✔ #15     ├──▶ #94           #160       │                    #107
#46 ─────────┤                        │  #100 ──┘                   #152       #125 (needs #99)     #12
#45 ─────────┤    ✔ #101 (rename)    ✔ #110                       #151       #124                 #65
#16 ─────────┘                        ✔ #82                        #150       #129 (after #106)    #80
                                      ✔ #126                       #154       #162, #161, #147
                                      ✔ #127                       #141       #146, #145, #149
                                      ✔ #128 ──▶ #139              #140       #164, #131 ──▶ #132
                                      ✔ #130                       #158       #111, #155
                                      ✔ #105                       #165       #63, #64, #122
                                                                   #166
```

---

## Parking Lot

Issues that are valid but have no immediate timeline. Re-evaluate quarterly.

- **#12** (neo4j) — Not needed until entity graph exceeds sqlite's comfort zone
  (~100K+ entities). Current corpus is far below this.
- **#65** (LanceDB) — Not needed until sqlite-vec shows performance problems.
  Benchmark first, migrate only if proven necessary.
- **#80** (web UI) — High effort, high reward, but not blocking any other work.
  Good candidate for a focused sprint once the core is stable.
- **#107** (code indexing) — The largest single feature. Requires its own
  planning session when ready. See issue for 6-phase breakdown.

---

## Quick Wins (< 1 session each)

**Phase 2.5 items** (all independent, all small scope):
#163, #160, #152, #151, #150, #154, #141, #140, #158, #165, #166

**Phase 3 follow-up enhancements** (depend on Phase 2 features already done):
#162, #164, #161, #147, #149, #146, #145, #122
