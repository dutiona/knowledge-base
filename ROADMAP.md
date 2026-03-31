# Roadmap

> Last updated: 2026-03-31

126 open issues (118 actionable + 5 reference/stale) across 8 workstreams. This document establishes priority, ordering,
dependency chains, and parallelism opportunities.

> **Gap Analysis Integration (2026-03-31):** Seven new Phase 3 issues and five
> existing-issue updates derive from the notes 23-28 gap analysis in
> `autonomous-agent-project` (`docs/summaries/gap-analysis-notes-23-25.md`).
> These cover multi-agent write-gating, provenance tracking, decontamination,
> staleness detection, and Level 2 injection research.

## Issue Index

| #   | Title                                                    | Workstream  | Phase | Status                                            |
| --- | -------------------------------------------------------- | ----------- | ----- | ------------------------------------------------- |
| 88  | pymupdf4llm production integration (Phase 2)             | Foundation  | 0     | ✔                                                 |
| 89  | **PR**: pymupdf4llm structured markdown extraction       | Foundation  | 0     | ✔                                                 |
| 85  | fix(vision): chunk_index encoding overflow               | Foundation  | 0     | ✔                                                 |
| 78  | refactor: executemany for config init                    | Foundation  | 0     | ✔                                                 |
| 46  | refactor: move SQL batching helpers to db.py             | Foundation  | 0     | ✔                                                 |
| 45  | refactor: .replace() instead of .format() in SQL         | Foundation  | 0     | ✔                                                 |
| 16  | feat: connectivity test for configure_llm                | Foundation  | 0     | ✔                                                 |
| 71  | docs: comprehensive documentation + typing + API ref     | Foundation  | 1     | partial (docs done, typing/cov/API ref remaining) |
| 101 | chore: rename to knowledge-base                          | Foundation  | 1     | ✔                                                 |
| 95  | pluggable embedding providers                            | Embedding   | 2     | ✔                                                 |
| 15  | parallelize map phase LLM calls                          | Extraction  | 2     | ✔                                                 |
| 110 | pymupdf4llm Phase 3: narrow vision pipeline scope        | Ingest      | 2     | ✔                                                 |
| 82  | extract inline images from web pages (Phase 1)           | Ingest      | 2     | ✔                                                 |
| 126 | folder-level semantic embeddings for context boosting    | Search      | 2     | ✔                                                 |
| 127 | prediction-error detection for stale search results      | Search      | 2     | ✔                                                 |
| 128 | ingestion session tracking for co-occurrence signals     | Search      | 2     | ✔                                                 |
| 130 | keyword intent extraction pre-filter for search          | Search      | 2     | ✔                                                 |
| 99  | multi-space embedding architecture                       | Embedding   | 2     | ✔                                                 |
| 100 | dual chunking strategy (8K + 32K)                        | Embedding   | 2     | ✔                                                 |
| 105 | auto-relationship discovery via similarity               | Search      | 2     | ✔                                                 |
| 139 | chunk_sessions join table for N:M session tracking       | Search      | 2     | ✔                                                 |
| 106 | stage-2 reranking in hybrid search                       | Search      | 2     | ✔                                                 |
| 163 | bug: qwen3.5 thinking-mode empty extraction              | Extraction  | 2.5a  | ✔ done                                            |
| 160 | fix: zombie conclusions after FK cleanup                 | Extraction  | 2.5a  | ✔                                                 |
| 152 | fix: stale inline image chunks on re-ingest              | Ingest      | 2.5a  |                                                   |
| 151 | fix: getaddrinfo for SSRF IP check                       | Ingest      | 2.5a  | ✔ PR #270                                         |
| 150 | improve zero-norm embedding vector handling              | Embedding   | 2.5a  | ✔ PR #274                                         |
| 165 | auto_relate: fallback to abstract_chunk_id               | Search      | 2.5a  |                                                   |
| 166 | scan_relationships: avoid redundant pairwise comparisons | Search      | 2.5a  |                                                   |
| 180 | no rollback on embedding failure in ingest_file          | Ingest      | 2.5a  |                                                   |
| 182 | relocate_paper lacks transaction safety                  | Papers      | 2.5a  |                                                   |
| 195 | path_conflict referenced before assignment               | Ingest      | 2.5a  |                                                   |
| 197 | LIKE wildcard injection in title search                  | Papers      | 2.5a  |                                                   |
| 198 | cursor.lastrowid falsy check by accident                 | Papers      | 2.5a  |                                                   |
| 201 | folder boost bug when best_distance==0                   | Search      | 2.5a  |                                                   |
| 202 | offset drift in \_chunk_markdown                         | Ingest      | 2.5a  |                                                   |
| 203 | \_validate_bib_path return value discarded               | Papers      | 2.5a  |                                                   |
| 204 | supersede_conclusion no rollback                         | Extraction  | 2.5a  |                                                   |
| 212 | PIL Image not closed in \_crop_regions                   | Vision      | 2.5a  |                                                   |
| 276 | fix(vision): extract_figures missing conclusions cleanup | Vision      | 2.5a  | done                                              |
| 277 | perf: optimize full table scan in conclusion FK cleanup  | Extraction  | 2.5b  |                                                   |
| 278 | refactor: consolidate conclusion FK cleanup into utility | Ingest      | 2.5b  |                                                   |
| 236 | unified \_insert_chunks helper (5 call sites)            | Ingest      | 2.5b  |                                                   |
| 238 | extract bibtex module (papers.py → bibtex.py)            | Papers      | 2.5b  |                                                   |
| 239 | extract auto_relate module (papers.py → auto_relate.py)  | Papers      | 2.5b  |                                                   |
| 240 | extract LLM module (extraction.py → llm.py)              | Extraction  | 2.5b  |                                                   |
| 243 | light improvements (7 items)                             | Foundation  | 2.5b  |                                                   |
| 234 | extract chunking module (ingest.py → chunking.py)        | Ingest      | 2.5b  |                                                   |
| 235 | extract web ingestion module (ingest.py → web.py)        | Ingest      | 2.5b  |                                                   |
| 237 | shared reingest/ingest logic                             | Ingest      | 2.5b  |                                                   |
| 241 | decompose extract_figures into pipeline stages           | Vision      | 2.5b  |                                                   |
| 242 | decompose server.py into router sub-modules              | Foundation  | 2.5b  |                                                   |
| 141 | refactor: unify multi-line SQL INSERT strings            | Foundation  | 2.5b  |                                                   |
| 140 | refactor: extraction module cleanup from #15             | Extraction  | 2.5b  |                                                   |
| 154 | refactor: consolidate transaction management             | Ingest      | 2.5b  |                                                   |
| 158 | chore: full Windows path support in folder summaries     | Search      | 2.5b  |                                                   |
| 181 | auto_relate O(P\*C²) with per-paper DB round-trips       | Search      | 2.5c  |                                                   |
| 199 | unbounded IN clause in compare_papers                    | Papers      | 2.5c  |                                                   |
| 200 | chunk_strategy unbatched IN clause (999 limit)           | Search      | 2.5c  |                                                   |
| 205 | get_paper N+1 queries                                    | Papers      | 2.5c  |                                                   |
| 206 | suggest_relationships fetches ALL papers                 | Papers      | 2.5c  |                                                   |
| 207 | O(n\*m) entity mention dedup                             | Extraction  | 2.5c  |                                                   |
| 208 | N+1 query in get_entities                                | Extraction  | 2.5c  |                                                   |
| 209 | O(E\*C) entity re-linking with uncompiled regex          | Extraction  | 2.5c  |                                                   |
| 210 | N+1 content_hash lookups per chunk                       | Ingest      | 2.5c  |                                                   |
| 211 | N+1 query for conclusion source chunks                   | Extraction  | 2.5c  |                                                   |
| 213 | single-pass duplicates entity storage                    | Extraction  | 2.5c  |                                                   |
| 187 | bare except swallows all FTS errors                      | Search      | 2.5c  |                                                   |
| 188 | no input validation on search params                     | Search      | 2.5c  |                                                   |
| 189 | subprocess with user-configurable omniparser_path        | Vision      | 2.5c  |                                                   |
| 190 | LLM base_url SSRF — only scheme validated                | Extraction  | 2.5c  |                                                   |
| 191 | LLM JSON without structural validation                   | Extraction  | 2.5c  |                                                   |
| 192 | indirect prompt injection in LLM extraction              | Extraction  | 2.5c  |                                                   |
| 193 | FTS5 operator sanitization incomplete                    | Search      | 2.5c  |                                                   |
| 194 | f-string SQL assembly fragile pattern                    | Foundation  | 2.5c  |                                                   |
| 196 | add_relationship error ignored by auto_relate            | Papers      | 2.5c  |                                                   |
| 214 | bidirectional coupling vision.py ↔ ingest.py             | Vision      | 2.5c  |                                                   |
| 215 | papers.py god module — 5 responsibilities                | Papers      | 2.5c  |                                                   |
| 216 | server.py god module — 38 tools in 1083 LOC              | Foundation  | 2.5c  |                                                   |
| 217 | status() has 9 inline SQL queries                        | Foundation  | 2.5c  |                                                   |
| 218 | reingest owns relationship invalidation                  | Ingest      | 2.5c  |                                                   |
| 219 | primitive obsession — dicts in ingest.py                 | Ingest      | 2.5c  |                                                   |
| 220 | duplicated FK cleanup logic                              | Papers      | 2.5c  |                                                   |
| 221 | record_method/dataset WET code                           | Papers      | 2.5c  |                                                   |
| 222 | ingest_file — no docstring                               | Ingest      | 2.5c  |                                                   |
| 223 | ingest_directory — no docstring                          | Ingest      | 2.5c  |                                                   |
| 224 | keyword_prefilter missing from mcp-tools.md              | Search      | 2.5c  |                                                   |
| 225 | max_workers missing from mcp-tools.md                    | Extraction  | 2.5c  |                                                   |
| 226 | phantom auto_relate_accept_threshold in docs             | Search      | 2.5c  |                                                   |
| 227 | get_methods/datasets/metrics — no docstrings             | Papers      | 2.5c  |                                                   |
| 228 | server.py 38/41 functions untested                       | Foundation  | 2.5c  |                                                   |
| 229 | \_validate_bib_path — zero tests (security)              | Papers      | 2.5c  |                                                   |
| 230 | \_cluster_bboxes — zero tests                            | Vision      | 2.5c  |                                                   |
| 231 | \_folder_boost — zero tests                              | Search      | 2.5c  |                                                   |
| 232 | low findings (55)                                        | Mixed       | 2.5c  |                                                   |
| 233 | info findings (30)                                       | Mixed       | 2.5c  |                                                   |
| 162 | folder summary integration for ingest_url                | Search      | 3     |                                                   |
| 164 | `<picture>` and srcset in inline image extraction        | Ingest      | 3     |                                                   |
| 161 | keyword stopwords strips compound technical terms        | Search      | 3     |                                                   |
| 147 | configurable stopword list for keyword extraction        | Search      | 3     |                                                   |
| 179 | multilingual stopword support (lingua-py)                | Search      | 3     |                                                   |
| 155 | mixed raster+vector pages in vision pipeline             | Ingest      | 3     |                                                   |
| 149 | ONNXProvider: pre-tokenized model support                | Embedding   | 3     |                                                   |
| 146 | workspace-scoped prediction error filtering              | Search      | 3     |                                                   |
| 145 | scope_miss prediction error type                         | Search      | 3     |                                                   |
| 125 | BM25-seeded HNSW insertion for graph quality             | Search      | 3     |                                                   |
| 124 | decouple index construction from query serving           | Search      | 3     |                                                   |
| 122 | docs: four-layer cognitive architecture                  | Foundation  | 3     |                                                   |
| 94  | compressed vector indices (int8/bit)                     | Embedding   | 3     |                                                   |
| 111 | pymupdf4llm Phase 4: hybrid enrichment                   | Ingest      | 3     |                                                   |
| 131 | web image extraction Phase 2: rendered DOM images        | Ingest      | 3     |                                                   |
| 132 | web image extraction Phase 3: canvas/SVG screenshots     | Ingest      | 3     |                                                   |
| 129 | retrieval plan intermediate representation               | Search      | 3     |                                                   |
| 63  | document watch/sync for auto-re-ingestion                | Ingest      | 3     |                                                   |
| 64  | workspace/project tagging for chunk isolation            | Ingest      | 3     |                                                   |
| 102 | hook-based memory-engine integration                     | Integration | 3     |                                                   |
| 103 | wisdom → knowledge pipeline                              | Integration | 3     |                                                   |
| 104 | memory → wisdom consolidation                            | Integration | 3     |                                                   |
| 173 | N:M chunk-to-source mapping for cross-source dedup       | Ingest      | 3     |                                                   |
| 246 | retrieval budget annotations to MCP tool descriptions    | Search      | 3     |                                                   |
| 250 | build retrieval coverage golden set (100+ questions)     | Search      | 2.5   |                                                   |
| 251 | graph-expand search mode (relationship edges)            | Search      | 3     |                                                   |
| 252 | entity-overlap signal in SearchResult                    | Search      | 3     |                                                   |
| 253 | query-type classifier in keywords.py                     | Search      | 3     |                                                   |
| 254 | reasoning hints in search MCP tool response              | Search      | 3     |                                                   |
| 255 | cross-reference resolution at ingestion time             | Ingest      | 3+    |                                                   |
| 256 | evaluate olmOCR-bench for vision pipeline                | Vision      | 3     |                                                   |
| 257 | reasoning-aware context preparation (Table 7)            | Search      | 3+    |                                                   |
| 258 | abstention diagnostic MCP tool (diagnose_abstention)     | Search      | 3     |                                                   |
| 259 | chunk strategy exposure by reasoning intent              | Search      | 3     |                                                   |
| 260 | figure-in-context chunking (caption+figure fused)        | Ingest      | 3+    |                                                   |
| 261 | staged Bayesian pipeline optimization (Optuna/NSGA-II)   | Search      | 3+    |                                                   |
| 263 | MCP server ACL layer (capability-token auth)             | Integration | 3     |                                                   |
| 264 | `contributing_agent` field on all write operations       | Integration | 3     |                                                   |
| 265 | evidence-basis metadata on conclusions                   | Extraction  | 3     |                                                   |
| 266 | staleness/drift detection pass                           | Search      | 3     |                                                   |
| 267 | contrastive decontamination for multi-agent ingestion    | Integration | 3     |                                                   |
| 268 | Level 2 context-triggered injection interface (research) | Search      | 3     |                                                   |
| 269 | injection dosing framework for KB retrieval (research)   | Search      | 3     |                                                   |
| 262 | multimodal embedding as secondary retrieval signal       | Embedding   | 4+    |                                                   |
| 108 | OCR preprocessing for scanned documents                  | Ingest      | 4     |                                                   |
| 109 | evaluate IBM docling Tableformer for tables              | Ingest      | 4     |                                                   |
| 247 | research: evaluate Kreuzberg v4.50                       | Ingest      | 4     |                                                   |
| 107 | epic: semantic code indexing                             | Ingest      | 4+    |                                                   |
| 12  | migrate entity graph to neo4j                            | Scale       | 4     |                                                   |
| 65  | evaluate LanceDB as sqlite-vec alternative               | Scale       | 4     |                                                   |
| 80  | web UI (Svelte + Rust WASM graph)                        | Scale       | 4     |                                                   |

**Umbrella issues** (closed when child decomposition issues land):

| #   | Title                                           | Children               |
| --- | ----------------------------------------------- | ---------------------- |
| 183 | god module — ingest.py 1950 LOC, 5 concerns     | #234, #235, #236, #237 |
| 184 | 250-line duplication ingest_file/reingest_file  | #237                   |
| 185 | chunk insert+dedup boilerplate repeated 5 times | #236                   |
| 186 | extract_figures() 500-LOC god function          | #241                   |
| 215 | papers.py god module — 5 responsibilities       | #238, #239             |
| 216 | server.py god module — 38 tools in 1083 LOC     | #242                   |

**Plan issues** (reference-only, not actionable work items):

| #   | Title                                                  |
| --- | ------------------------------------------------------ |
| 171 | plan: Phase 1 — Multi-space embedding registry         |
| 174 | plan: Phase 2 — Matryoshka truncation support          |
| 177 | plan: Phase 3 — A/B embedding space comparison tooling |
| 178 | perf: search query optimization opportunities (#172)   |
| 248 | plan: stage-2 cross-encoder reranking (#106)           |

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

## Phase 2 — Embedding Architecture + Search Quality ✔

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
- ~~#99~~ multi-space embedding architecture (PR #172)
- ~~#100~~ dual chunking strategy (PR #170)
- ~~#139~~ chunk_sessions N:M (PR #169)
- ~~#106~~ stage-2 cross-encoder reranking (PR #249)

**All items complete.** Multiple embedding models coexist, Matryoshka truncation
works, search quality improves via opt-in cross-encoder reranking.

---

## Phase 2.5a — Bugs & Safety Fixes

**Goal:** Fix all known bugs and safety issues from Phase 2 and super-qa audit
before adding features or refactoring.

```
✔ #163 (qwen3.5 thinking-mode)      ─── bug, independent (done)
#160 (zombie conclusions)            ─── bug, independent
✔ #152 (stale inline image chunks)   ─── bug, depends on #82 (done) ✔ PR #271
#151 (getaddrinfo SSRF)              ─── bug, depends on #82 (done) ✔ PR #270
✔ #150 (zero-norm embeddings)          ─── fix, independent ✔ PR #274
✔ #165 (auto_relate fallback)          ─── fix, depends on #105 (done) ✔ PR #280
#166 (scan_relationships 2x)         ─── perf, depends on #105 (done)
#180 (no rollback on embed failure)  ─── bug, independent
#182 (relocate_paper no transaction) ─── bug, independent
#195 (path_conflict unbound)         ─── bug, independent
#197 (LIKE wildcard injection)       ─── bug, independent
#198 (lastrowid falsy check)         ─── bug, independent
#201 (folder boost div-by-zero)      ─── bug, depends on #126 (done)
#202 (offset drift in chunking)      ─── bug, independent
#203 (_validate_bib_path discarded)  ─── bug, independent
#204 (supersede_conclusion rollback) ─── bug, independent
#212 (PIL Image not closed)          ─── bug, independent
```

**Parallelism:** All 17 items are independent of each other. Any can be picked up
in any order. All are small scope (< 1 session each).

**Exit criteria:** No known bugs from Phase 2 work or super-qa audit. Clean
ruff/pyright. Test suite green without workarounds.

---

## Phase 2.5b — Module Decomposition & Refactoring

**Goal:** Break god modules into focused, single-responsibility modules. This is
the structural prerequisite that makes Phase 3 features easier to implement and
review.

**Step 1 — Leaf extractions (all parallel):**

```
#236 (unified _insert_chunks)         ─── touches ingest.py
#238 (bibtex.py from papers.py)       ─── touches papers.py
#239 (auto_relate.py from papers.py)  ─── touches papers.py (coordinate with #238)
#240 (llm.py from extraction.py)      ─── touches extraction.py
#243 (light improvements, 7 items)    ─── scattered, low conflict risk
```

**Step 2 — Ingest decomposition (depends on Step 1, specifically #236):**

```
#234 (chunking.py from ingest.py)     ─── needs #236 (shared helper) first
#235 (web.py from ingest.py)          ─── needs #236 first
#237 (shared reingest/ingest logic)   ─── needs #234, #235
```

**Step 3 — Final decompositions (depends on Step 2):**

```
#241 (decompose extract_figures)      ─── after ingest.py is smaller
#242 (decompose server.py)            ─── independent, can go at any step
```

**Parallel with any step:**

```
#141 (unify SQL INSERTs)              ─── refactor, independent
#140 (extraction cleanup from #15)    ─── refactor, depends on #15 (done)
#154 (transaction consolidation)      ─── refactor, independent
#158 (Windows path support)           ─── chore, depends on #126 (done)
```

**Umbrella closure:** When children land, close #183, #184, #185, #186, #215, #216.

**Exit criteria:** No module exceeds ~500 LOC. Each module has a single
responsibility. God module warnings resolved.

---

## Phase 2.5c — Super-QA Medium Findings

**Goal:** Address remaining medium-severity findings from the super-qa audit.
These are lower urgency than 2.5a/2.5b but should land before Phase 3 to prevent
compounding tech debt.

### Performance (N+1, O(n²)) — 11 items

```
#181 (auto_relate O(P*C²))            ─── depends on #105 (done), #239 helps
#199 (unbounded IN in compare_papers)  ─── independent
#200 (chunk_strategy unbatched IN)     ─── independent
#205 (get_paper N+1)                   ─── independent
#206 (suggest_relationships all papers)─── independent
#207 (O(n*m) entity mention dedup)     ─── independent
#208 (N+1 in get_entities)             ─── independent
#209 (O(E*C) entity re-linking)        ─── independent
#210 (N+1 content_hash lookups)        ─── independent
#211 (N+1 conclusion source chunks)    ─── independent
#213 (single-pass entity duplication)  ─── independent
```

### Security & Validation — 7 items

```
#187 (bare except swallows FTS errors) ─── independent
#188 (no input validation on search)   ─── independent
#189 (subprocess omniparser_path)      ─── independent
#190 (LLM base_url SSRF)              ─── independent
#191 (LLM JSON no structural valid.)   ─── independent
#192 (indirect prompt injection)       ─── independent
#193 (FTS5 sanitization incomplete)    ─── independent
```

### Code Quality & Coupling — 7 items

```
#194 (f-string SQL assembly)           ─── independent
#196 (add_relationship error ignored)  ─── independent
#214 (vision ↔ ingest coupling)        ─── easier after #241 (Phase 2.5b)
#218 (reingest owns relationship inv.) ─── independent
#219 (primitive obsession — dicts)     ─── easier after #234/#235 (Phase 2.5b)
#220 (duplicated FK cleanup)           ─── independent
#221 (record_method/dataset WET)       ─── independent
```

### Documentation & Tests — 12 items

```
#217 (status() 9 inline SQL queries)   ─── independent
#222 (ingest_file no docstring)        ─── independent
#223 (ingest_directory no docstring)   ─── independent
#224 (keyword_prefilter docs gap)      ─── independent
#225 (max_workers docs gap)            ─── independent
#226 (phantom threshold in docs)       ─── independent
#227 (no docstrings on get_methods)    ─── independent
#228 (server.py 38/41 untested)        ─── large, consider after #242
#229 (_validate_bib_path zero tests)   ─── independent
#230 (_cluster_bboxes zero tests)      ─── independent
#231 (_folder_boost zero tests)        ─── independent
```

### Aggregate / Deferred — 2 items

```
#232 (low findings, 55 items)          ─── triage individually when convenient
#233 (info findings, 30 items)         ─── reference only, no action needed
```

**Parallelism:** All items within each sub-category are independent. Items in
different sub-categories are also independent. The only soft dependency is that
#214 and #219 are easier after Phase 2.5b decompositions land, and #228 is easier
after #242 (server.py decomposition).

**Exit criteria:** All medium super-qa findings addressed or explicitly deferred
with rationale.

---

## Phase 3 — Intelligence, Integration & Search Refinement

**Goal:** Connect the four-layer architecture, refine search quality, and
polish ingest pipelines with follow-up enhancements.

```
#102 (hook-based ME integration)   ──▶ #103 (wisdom→knowledge)
  Bidirectional linking (NYX12).   ──▶ #104 (memory→wisdom)
  KnowledgeBaseConnector: intent-aware retrieval,
  Knowledge-URI-to-Memory-fact linking.

#111 (vision Phase 4)       ─── depends on #110 (done)
#155 (mixed raster+vector)  ─── depends on #110 (done)
#131 (web images, Phase 2)  ─── depends on #82 (done)
#132 (web images, Phase 3)  ─── depends on #131
#164 (<picture>/srcset)     ─── depends on #82 (done)

#94 (int8/bit quantization) ─── depends on #99 (done)
#125 (BM25-seeded HNSW)     ─── depends on #99 (done)
#124 (build/serve pattern)  ─── independent

#129 (retrieval plan IR)    ─── benefits from #106 (done)

#253 (query-type classifier)       ─── depends on #130 (done)
  Prerequisite for Level 2 context-triggered injection (#268).
  Must support reasoning-context-as-query, not just user queries.
#254 (reasoning hints in metadata) ─── depends on #253
  Include dosing recommendations. IBM -5.6pp over-injection means
  metadata must help consumers LIMIT injection volume.
#257 (reasoning-aware context)     ─── depends on #253, #254
  Must work proactively (harness-driven, between thinking blocks),
  not just reactively (user queries).

#162 (folder summary + ingest_url) ─── depends on #126 (done)
#161 (keyword compound terms)      ─── depends on #130 (done)
#147 (configurable stopwords)      ─── depends on #130 (done)
#179 (multilingual stopwords)      ─── extends #147, coordinate together
#146 (workspace prediction errors) ─── depends on #127 (done)
#145 (scope_miss error type)       ─── depends on #127 (done)
#149 (ONNX pre-tokenized)          ─── depends on #95 (done)
#122 (cognitive arch docs)         ─── independent

#63 (document watch/sync)   ─── independent
#64 (workspace tagging)     ─── independent
  Must support agent-identity-scoped isolation, not just
  project-scoped. Companion to #264 (contributing_agent).
#173 (N:M chunk-to-source)  ─── independent schema evolution
#246 (retrieval budget MCP) ─── independent, zero-code-change quick win

# Search quality improvements (from research notes 18-21, March 2026)
#250 (golden set)           ─── prerequisite for all below
#251 (graph-expand search)  ─── uses existing papers.py relationships
#252 (entity-overlap)       ─── independent, low effort
#253 (query-type classifier)─── independent, low effort
#254 (reasoning hints)      ─── depends on #252, #253
#258 (abstention diagnostic)─── depends on #252
#259 (chunk strategy intent)─── depends on #253
#257 (reasoning-aware ctx)  ─── depends on #253, #259
#261 (Bayesian optimization)─── depends on #250 (golden set)

# Ingest improvements (from notes 20-21)
#255 (cross-ref resolution) ─── independent
#256 (olmOCR-bench eval)    ─── independent
#260 (figure-in-context)    ─── independent

# --- Gap analysis additions (notes 23-28) ---
#263 (MCP server ACL layer)      ─── independent
  Capability-token auth. Write-gate ingest_file,
  record_conclusion, add_relationship. Agent identity
  verification before write access.
#264 (contributing_agent field)   ─── independent
  Add column to papers, conclusions, relationships, entities.
  Prerequisite for audit trails and decontamination (#267).
#265 (evidence-basis metadata)    ─── independent
  evidence_basis enum (Observed/Inferred/Synthesized).
  MAGELLAN uses GROUNDED/PARAMETRIC/SPECULATIVE.
#266 (staleness/drift detection)  ─── independent
  Periodic validation that stored knowledge reflects reality.
  MEX has 8 heuristic checkers. KB relies on manual supersession.
#267 (contrastive decontamination)─── depends on #264
  MemCollab: -5.4pp from naive cross-agent transfer.
  Decontamination step identifies agent-specific biases before
  incorporating into shared KB.
#268 (Level 2 injection, research)─── depends on #253
  Harness silently queries KB based on reasoning context.
  IBM: +28.5pp hard, -5.6pp over-injection.
  Paired with ME dosing framework.
#269 (injection dosing, research) ─── depends on #268
  Principled volume/relevance calibration by task difficulty
  and reasoning budget.
```

**Dependency chains:**

- **#102 → #103, #104**: Memory-engine integration is the prerequisite.
  External dependency: `memory-engine` must expose an MCP server.
  NYX12 bidirectional linking: KnowledgeBaseConnector needs intent-aware
  retrieval and Knowledge-URI-to-Memory-fact linking.
- **#94, #125** both build on #99 (multi-space), now done.
- **#129 (retrieval plan IR)** benefits from #106 (reranking) — plan can
  specify reranking directives per sub-query.
- **#147 + #179** should be implemented together — #147 adds configurability,
  #179 adds language detection. Same module, same tests.
- **#253 → #254 → #257**: Query-type classifier feeds reasoning hints, which
  feed reasoning-aware context preparation. All three form the Level 2
  injection prerequisite chain.
- **#264 → #267**: contributing_agent field is required before contrastive
  decontamination can identify per-agent biases.
- **#253 → #268 → #269**: Query classifier enables Level 2 injection
  interface, which enables the dosing framework.

**Parallelism:**

- Follow-up enhancements (#162, #161, #147+#179, #146, #145, #149, #164, #246)
  are all independent of each other and of the integration work. Good quick-win
  candidates between larger features.
- **#246** is the lowest-effort item in the entire roadmap — modifying MCP tool
  description strings only. Can be done at any time.
- #111 and #155 (vision pipeline) are independent of integration work.
- #63 (watch/sync) and #64 (workspace tagging) are standalone ingest
  improvements. #64 now also covers agent-identity-scoped isolation (companion
  to #264).
- #173 (N:M chunk-to-source) is independent schema work.
- #122 (docs) can be done anytime.
- **#263** (ACL layer), **#264** (contributing_agent), **#265** (evidence
  basis), **#266** (staleness detection) are all independent of each other
  and can start as soon as Phase 3 begins.
- **#267** (decontamination) depends on #264. **#268** and **#269** are
  research items that depend on #253 and on each other sequentially.

**Exit criteria:** Hooks log to memory-engine, consolidation proposes wisdom
candidates, search pipeline has reranking + retrieval plans, follow-up
enhancements from Phase 2 are landed. Multi-agent write operations are
ACL-gated with contributing_agent provenance. Evidence-basis metadata on
conclusions. Staleness detection operational.

---

## Phase 4 — New Frontends & Scale

**Goal:** Extend the knowledge base beyond research papers.

```
#108 (OCR preprocessing) ─┐
                          ├── can be parallel
#109 (Tableformer eval)  ─┤
                          │
#247 (Kreuzberg eval)    ─┘

#107 (code indexing epic) ─── multi-phase, starts here

#12 (neo4j migration)  ─── independent
#65 (LanceDB eval)     ─── independent
#80 (web UI)           ─── independent
#262 (multimodal embed)─── depends on Ollama #5304 or ONNX image path
```

These are **exploratory and high-effort** items. Each is independent and can be
prioritized based on which use case is most pressing at the time.

**#247 (Kreuzberg)** and **#109 (Tableformer)** are both document extraction
evaluations — natural to run them together as a comparative study. #247 may
subsume parts of #108 (OCR) since Kreuzberg includes multi-backend OCR.

**#107 (code indexing)** is the largest single feature — an epic with 6
sub-phases. It should be broken into sub-issues when work begins.

**#12 and #65** are mutually exclusive alternatives for scale — evaluate before
committing to either. neo4j for graph queries, LanceDB for vector queries. Both
solve problems that don't exist yet at current corpus size.

**#80 (web UI)** is a nice-to-have that becomes valuable once the knowledge base
has enough content to warrant visual exploration.

**Exit criteria:** At least one new ingest frontend working (OCR or code).

---

## Dependency Graph

```
Phase 0 ✔       Phase 1 ✔       Phase 2 (12/13)     Phase 2.5a          Phase 2.5b              Phase 2.5c
────────        ────────        ────────             ────────            ────────                ────────
                                ✔ #95                ✔ #163, #160        Step 1:                 Perf:
PR #89 ──┐                      ✔ #99               ✔ #152, ✔ #151       #236, #238, #239         #181, #199, #200
#85 ─────┤                      ✔ #100               ✔ #150, ✔ #165         #240, #243               #205–#211, #213
#78 ─────┼──▶ ✔ #71            ✔ #15                #166, #180          Step 2 (needs #236):    Security:
#46 ─────┤                      ✔ #110               #182, #195           #234, #235, #237         #187–#193
#45 ─────┤    ✔ #101           ✔ #82                #197, #198          Step 3 (needs Step 2):  Quality:
#16 ─────┘                      ✔ #126               #201, #202           #241, #242               #194, #196, #214
                                ✔ #127               #203, #204          Parallel:                #218–#221
                                ✔ #128               #212                 #141, #140, #154, #158  Docs/Tests:
                                ✔ #130                                                            #217, #222–#231
                                ✔ #105
                                ✔ #99
                                ✔ #100
                                ✔ #139
                                ✔ #106

Phase 3                         Phase 4
────────                        ────────
#102 ──┬──▶ #103                #108, #109, #247
       └──▶ #104                #107 (epic)
#94, #125 (need ✔#99)          #12, #65, #80
#124, #129 (after ✔#106)
#253 ──▶ #254 ──▶ #257
#253 ──▶ #268 ──▶ #269
#162, #161, #147+#179
#146, #145, #149, #164
#131 ──▶ #132
#111, #155
#63, #64, #122
#173, #246
#263, #264 ──▶ #267
#265, #266 (independent)
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
- **#232** (low findings) — 55 items to triage individually. Pick off as
  convenient between features.
- **#233** (info findings) — 30 items. Reference only, no action needed unless
  a specific item becomes relevant.

---

## Quick Wins (< 1 session each)

**Phase 2.5a items** (all independent, all small scope):
✔ #163, ✔ #160, ✔ #152, ✔ #151, ✔ #150, ✔ #165, #166, #180, #182, #195, #197, #198, #201,
#202, #203, #204, #212, #276

**Phase 2.5b parallel items** (independent of decomposition ordering):
#141, #140, #154, #158, #243, #277, #278

**Dependency:** #278 (consolidate cleanup) subsumes #276 (vision.py fix) and #277 (perf optimization).
Do #276 first (quick fix), then #278 absorbs the shared utility + perf work.

**Phase 2.5c doc/test gaps** (independent, low risk):
#222, #223, #224, #225, #226, #227, #229, #230, #231

**Phase 3 follow-up enhancements** (depend on Phase 2 features already done):
#162, #164, #161, #147, #149, #146, #145, #122, #246, #253

**Phase 3 gap-analysis additions** (independent, can start when Phase 3 begins):
#263, #264, #265, #266
