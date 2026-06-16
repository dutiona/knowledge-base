# Roadmap

> Last updated: 2026-04-09

249 issues total (104 open, 145 closed) across 8 workstreams. This document
establishes priority, ordering, dependency chains, and parallelism opportunities.

> **Live tracker:** day-to-day status lives in the GitHub Projects (KB — Main,
> Critical Path to Phase 4, Bug & Security Triage, Research & Eval), not here.
> This file is the dependency-graph narrative; the Projects are the live board.
> The roadmap **Phase** column maps 1:1 to the Project **Phase** field
> (2.5c, 3A–3I, 4, 4+, Deferred). Label taxonomy & workflow:
> `docs/design/project-management.md`.

> **Gap Analysis Integration (2026-03-31):** Seven new Phase 3 issues and five
> existing-issue updates derive from the notes 23-28 gap analysis in
> `autonomous-agent-project` (`docs/summaries/gap-analysis-notes-23-25.md`).
> These cover multi-agent write-gating, provenance tracking, decontamination,
> staleness detection, and Level 2 injection research.
>
> **Notes 29-30 Integration (2026-04-04):** 11 new issues (#322-#332) and 3
> existing-issue updates (#94, #108, #109) from notes 29 (Claude Code
> reverse-engineering) and 30 (RAG/memory landscape scan). New items cover
> secret scanning, contextual retrieval (-67% failure rate), chunk enrichment,
> query expansion, embedding versioning, table-aware chunking, late chunking,
> Gemma 4 and EmbeddingGemma evaluation, Docling/Marker extraction, and
> Chandra v2 OCR. See `docs/summaries/steal-list-notes-29-30.md`.
>
> **Notes 31-32 Integration (2026-04-13):** Six new roadmap gaps derived from
> landscape review #32 (April week 2), filed as Phase 3I. Two P0 with proposed
> ADRs (full text archived in their tracking issues): KB-P0-A LongTracer
> Phase 2 ingestion gate (#359) and KB-P0-B benchmark strategy pivot to
> MemArch-Bench-first (#360).
> Four P1: KB-P1-C Embedding Adapters V2 (#361, research), KB-P1-D sem
> structural-hash ingest (#362, research), KB-P1-E compile-upfront positioning
> (#363, docs), KB-P1-F paragraph-level provenance (#364, depends on #325).
> KB-P2-G/H and KB-P3-I are tracking-only — see Parking Lot. See also new
> "Verification" section, the new "Phase 3I" subphase, and
> `autonomous-agent-project/raw/docs/summaries/04-results-and-roadmap.md` §11.2.
>
> **Cross-repo coordination**: The analogous §11.1 memory-engine gaps
> (ME-P0-A through ME-P3-I) are tracked as a single umbrella in
> [dutiona/memory-engine#237](https://github.com/dutiona/memory-engine/issues/237).
> In particular, **KB-P1-D (#362) mirrors ME-P1-D** — both repos want
> Ataraxy-Labs `sem` as a structural-hash backend (KB at ingest/chunk
> level, ME at supersession/fact level). A single Python↔Rust integration
> spike should inform both sides.

---

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
| 163 | bug: qwen3.5 thinking-mode empty extraction              | Extraction  | 2.5a  | ✔                                                 |
| 160 | fix: zombie conclusions after FK cleanup                 | Extraction  | 2.5a  | ✔ PR #272                                         |
| 152 | fix: stale inline image chunks on re-ingest              | Ingest      | 2.5a  | ✔ PR #271                                         |
| 151 | fix: getaddrinfo for SSRF IP check                       | Ingest      | 2.5a  | ✔ PR #270                                         |
| 150 | improve zero-norm embedding vector handling              | Embedding   | 2.5a  | ✔ PR #274                                         |
| 165 | auto_relate: fallback to abstract_chunk_id               | Search      | 2.5a  | ✔ PR #280                                         |
| 166 | scan_relationships: avoid redundant pairwise comparisons | Search      | 2.5a  | ✔ PR #283                                         |
| 180 | no rollback on embedding failure in ingest_file          | Ingest      | 2.5a  | ✔ PR #282                                         |
| 182 | relocate_paper lacks transaction safety                  | Papers      | 2.5a  | ✔ PR #281                                         |
| 195 | path_conflict referenced before assignment               | Ingest      | 2.5a  | ✔ PR #286                                         |
| 197 | LIKE wildcard injection in title search                  | Papers      | 2.5a  | ✔ PR #285                                         |
| 198 | cursor.lastrowid falsy check by accident                 | Papers      | 2.5a  | ✔                                                 |
| 201 | folder boost bug when best_distance==0                   | Search      | 2.5a  | ✔ PR #287                                         |
| 202 | offset drift in \_chunk_markdown                         | Ingest      | 2.5a  | ✔ PR #290                                         |
| 203 | \_validate_bib_path return value discarded               | Papers      | 2.5a  | ✔ PR #291                                         |
| 204 | supersede_conclusion no rollback                         | Extraction  | 2.5a  | ✔ PR #289                                         |
| 212 | PIL Image not closed in \_crop_regions                   | Vision      | 2.5a  | ✔ PR #288                                         |
| 276 | fix(vision): extract_figures missing conclusions cleanup | Vision      | 2.5a  | ✔                                                 |
| 277 | perf: optimize full table scan in conclusion FK cleanup  | Extraction  | 2.5b  | ✔ PR #304                                         |
| 278 | refactor: consolidate conclusion FK cleanup into utility | Ingest      | 2.5b  | ✔ PR #304                                         |
| 236 | unified \_insert_chunks helper (5 call sites)            | Ingest      | 2.5b  | ✔ PR #295                                         |
| 238 | extract bibtex module (papers.py -> bibtex.py)           | Papers      | 2.5b  | ✔ PR #293                                         |
| 239 | extract auto_relate module (papers.py -> auto_relate.py) | Papers      | 2.5b  | ✔ PR #292                                         |
| 240 | extract LLM module (extraction.py -> llm.py)             | Extraction  | 2.5b  | ✔ PR #294                                         |
| 243 | light improvements (7 items)                             | Foundation  | 2.5b  | ✔ PR #296                                         |
| 234 | extract chunking module (ingest.py -> chunking.py)       | Ingest      | 2.5b  | ✔ PR #303                                         |
| 235 | extract web ingestion module (ingest.py -> web.py)       | Ingest      | 2.5b  | ✔ PR #306                                         |
| 237 | shared reingest/ingest logic                             | Ingest      | 2.5b  | ✔ PR #307                                         |
| 241 | decompose extract_figures into pipeline stages           | Vision      | 2.5b  | ✔ PR #308                                         |
| 242 | decompose server.py into router sub-modules              | Foundation  | 2.5b  | ✔ PR #310                                         |
| 141 | refactor: unify multi-line SQL INSERT strings            | Foundation  | 2.5b  | ✔ PR #302                                         |
| 140 | refactor: extraction module cleanup from #15             | Extraction  | 2.5b  | ✔ PR #300                                         |
| 154 | refactor: consolidate transaction management             | Ingest      | 2.5b  | ✔ PR #299                                         |
| 158 | chore: full Windows path support in folder summaries     | Search      | 2.5b  | ✔ PR #301                                         |
| 187 | bare except swallows all FTS errors                      | Search      | 2.5c  | ✔ PR #312                                         |
| 188 | no input validation on search params                     | Search      | 2.5c  | ✔ PR #313                                         |
| 189 | subprocess with user-configurable omniparser_path        | Vision      | 2.5c  | ✔ PR #316                                         |
| 190 | LLM base_url SSRF -- only scheme validated               | Extraction  | 2.5c  | ✔ PR #318                                         |
| 191 | LLM JSON without structural validation                   | Extraction  | 2.5c  | ✔ PR #314                                         |
| 192 | indirect prompt injection in LLM extraction              | Extraction  | 2.5c  | ✔ PR #317                                         |
| 193 | FTS5 operator sanitization incomplete                    | Search      | 2.5c  | ✔ PR #315                                         |
| 181 | auto_relate O(P\*C^2) with per-paper DB round-trips      | Search      | 2.5c  |                                                   |
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
| 194 | f-string SQL assembly fragile pattern                    | Foundation  | 2.5c  |                                                   |
| 196 | add_relationship error ignored by auto_relate            | Papers      | 2.5c  |                                                   |
| 214 | bidirectional coupling vision.py <-> ingest.py           | Vision      | 2.5c  |                                                   |
| 215 | papers.py god module -- 5 responsibilities               | Papers      | 2.5c  | ✔ resolved by #238, #239                          |
| 216 | server.py god module -- 38 tools in 1083 LOC             | Foundation  | 2.5c  | ✔ resolved by #242 (PR #310)                      |
| 217 | status() has 9 inline SQL queries                        | Foundation  | 2.5c  |                                                   |
| 218 | reingest owns relationship invalidation                  | Ingest      | 2.5c  |                                                   |
| 219 | primitive obsession -- dicts in ingest.py                | Ingest      | 2.5c  |                                                   |
| 220 | duplicated FK cleanup logic                              | Papers      | 2.5c  |                                                   |
| 221 | record_method/dataset WET code                           | Papers      | 2.5c  |                                                   |
| 222 | ingest_file -- no docstring                              | Ingest      | 2.5c  |                                                   |
| 223 | ingest_directory -- no docstring                         | Ingest      | 2.5c  |                                                   |
| 224 | keyword_prefilter missing from mcp-tools.md              | Search      | 2.5c  |                                                   |
| 225 | max_workers missing from mcp-tools.md                    | Extraction  | 2.5c  |                                                   |
| 226 | phantom auto_relate_accept_threshold in docs             | Search      | 2.5c  |                                                   |
| 227 | get_methods/datasets/metrics -- no docstrings            | Papers      | 2.5c  |                                                   |
| 228 | routes/ 46 tool functions untested                       | Foundation  | 2.5c  |                                                   |
| 229 | \_validate_bib_path -- zero tests (security)             | Papers      | 2.5c  |                                                   |
| 230 | \_cluster_bboxes -- zero tests                           | Vision      | 2.5c  |                                                   |
| 231 | \_folder_boost -- zero tests                             | Search      | 2.5c  |                                                   |
| 232 | low findings (55)                                        | Mixed       | 2.5c  |                                                   |
| 233 | info findings (30)                                       | Mixed       | 2.5c  |                                                   |
| 297 | perf: pre-extract paper ID markers in sync_bibtex        | Papers      | 2.5c  |                                                   |
| 298 | llm.py: address Gemini Code Assist review suggestions    | Extraction  | 2.5c  |                                                   |
| 309 | fix: flaky test_image_reference_present                  | Vision      | 2.5c  |                                                   |
| 319 | sqlite3.OperationalError catch in FTS too broad          | Search      | 2.5c  |                                                   |
| 125 | BM25-seeded HNSW insertion for graph quality             | Search      | 3     | ✔ closed (N/A: sqlite-vec uses DiskANN, not HNSW) |
| 94  | compressed vector indices (int8/bit)                     | Embedding   | 3     | ✔ PR #355                                         |
| 111 | pymupdf4llm Phase 4: hybrid enrichment                   | Ingest      | 3     | ✔ PR #340                                         |
| 334 | persistent OmniParser HTTP server (model caching)        | Vision      | 3     | ✔ PR #352                                         |
| 155 | mixed raster+vector pages in vision pipeline             | Ingest      | 3     | ✔ PR #338                                         |
| 131 | web image extraction Phase 2: rendered DOM images        | Ingest      | 3     | ✔ PR #339                                         |
| 132 | web image extraction Phase 3: canvas/SVG screenshots     | Ingest      | 3     | ✔ PR #347                                         |
| 164 | `<picture>` and srcset in inline image extraction        | Ingest      | 3     | ✔ PR #356                                         |
| 349 | separate indexer CLI entry point (Phase 1)               | Foundation  | 3     | ✔ PR #358                                         |
| 325 | contextual retrieval -- LLM context prepend before embed | Search      | 3A    | note 30 K5. **Highest-ROI**: -67% failure rate    |
| 326 | chunk enrichment (keywords, questions, context, summary) | Ingest      | 3A    | note 30 K6                                        |
| 327 | query expansion -- rephrase 2-3x before search           | Search      | 3A    | note 30 K7                                        |
| 253 | query-type classifier in keywords.py                     | Search      | 3A    |                                                   |
| 252 | entity-overlap signal in SearchResult                    | Search      | 3A    |                                                   |
| 251 | graph-expand search mode (relationship edges)            | Search      | 3A    |                                                   |
| 129 | retrieval plan intermediate representation               | Search      | 3A    |                                                   |
| 246 | retrieval budget annotations to MCP tool descriptions    | Search      | 3A    | zero-code quick win                               |
| 250 | build retrieval coverage golden set (100+ questions)     | Search      | 3A    | prerequisite for #261                             |
| 258 | abstention diagnostic MCP tool (diagnose_abstention)     | Search      | 3A    | depends on #252                                   |
| 254 | reasoning hints in search MCP tool response              | Search      | 3A    | depends on #253                                   |
| 259 | chunk strategy exposure by reasoning intent              | Search      | 3A    | depends on #253                                   |
| 344 | TurboQuant 4-bit embedding quantization                  | Embedding   | 3B    | depends on ✔ #94                                  |
| 345 | binary quantization + int8 rescoring (two-stage search)  | Search      | 3B    | depends on ✔ #94                                  |
| 328 | embedding versioning -- track model version per vector   | Embedding   | 3B    | note 30 K8. #1 production pain point              |
| 149 | ONNXProvider: pre-tokenized model support                | Embedding   | 3B    |                                                   |
| 332 | benchmark EmbeddingGemma as embedding model              | Embedding   | 3B    | note 30 K15. Operational eval                     |
| 330 | table-aware chunking -- preserve headers across splits   | Ingest      | 3C    | note 30 K13                                       |
| 322 | content provenance gate (secret/PII scanning)            | Ingest      | 3C    | note 29 K1                                        |
| 162 | folder summary integration for ingest_url                | Search      | 3C    |                                                   |
| 63  | document watch/sync for auto-re-ingestion                | Ingest      | 3C    |                                                   |
| 64  | workspace/project tagging for chunk isolation            | Ingest      | 3C    |                                                   |
| 173 | N:M chunk-to-source mapping for cross-source dedup       | Ingest      | 3C    |                                                   |
| 255 | cross-reference resolution at ingestion time             | Ingest      | 3C+   |                                                   |
| 260 | figure-in-context chunking (caption+figure fused)        | Ingest      | 3C+   |                                                   |
| 275 | fix: stale inline image chunks on extraction switch      | Ingest      | 3C    | bug                                               |
| 342 | fix: \_extract_captions grabs full paragraph             | Vision      | 3C    | bug                                               |
| 335 | smart OmniParser skip heuristic from collected data      | Vision      | 3C    | depends on ✔ #334                                 |
| 354 | perf: avoid temp file in OmniParser server path          | Vision      | 3C    | depends on ✔ #334                                 |
| 161 | keyword stopwords strips compound technical terms        | Search      | 3D    |                                                   |
| 147 | configurable stopword list for keyword extraction        | Search      | 3D    |                                                   |
| 179 | multilingual stopword support (lingua-py)                | Search      | 3D    | extends #147                                      |
| 145 | scope_miss prediction error type                         | Search      | 3D    |                                                   |
| 146 | workspace-scoped prediction error filtering              | Search      | 3D    |                                                   |
| 320 | second-hop prompt injection in entity resolution         | Extraction  | 3D    | security                                          |
| 321 | preserve user-sourced data during re-extraction          | Extraction  | 3D    |                                                   |
| 102 | hook-based memory-engine integration                     | Integration | 3E    |                                                   |
| 103 | wisdom -> knowledge pipeline                             | Integration | 3E    | depends on #102                                   |
| 104 | memory -> wisdom consolidation                           | Integration | 3E    | depends on #102                                   |
| 323 | user-facing memory taxonomy overlay for MCP tools        | Integration | 3E    | note 29 K3                                        |
| 333 | incremental indexing endpoint + bidirectional ME linking | Integration | 3E    | pairs with #102, #349 (done)                      |
| 263 | MCP server ACL layer (capability-token auth)             | Integration | 3F    |                                                   |
| 264 | contributing_agent field on all write operations         | Integration | 3F    |                                                   |
| 265 | evidence-basis metadata on conclusions                   | Extraction  | 3F    |                                                   |
| 266 | staleness/drift detection pass                           | Search      | 3F    |                                                   |
| 267 | contrastive decontamination for multi-agent ingestion    | Integration | 3F    | depends on #264                                   |
| 324 | pre-inject existing facts for dedup context              | Extraction  | 3F    | note 29 K4                                        |
| 350 | serve-only MCP server mode (Phase 2)                     | Foundation  | 3G    | depends on ✔ #349                                 |
| 351 | horizontal scaling: replication + blue/green (Phase 3)   | Scale       | 3G    | gated on multi-machine need                       |
| 122 | docs: four-layer cognitive architecture                  | Foundation  | 3G    |                                                   |
| 331 | benchmark Gemma 4 26B-A4B as generative model            | Extraction  | 3H    | note 30 K14. Operational eval                     |
| 256 | evaluate olmOCR-bench for vision pipeline                | Vision      | 3H    |                                                   |
| 329 | late chunking evaluation (8K+ embedding model)           | Search      | 3H    | note 30 K12. Research                             |
| 257 | reasoning-aware context preparation (Table 7)            | Search      | 3H    | depends on #253, #254                             |
| 261 | staged Bayesian pipeline optimization (Optuna/NSGA-II)   | Search      | 3H    | depends on #250                                   |
| 268 | Level 2 context-triggered injection interface (research) | Search      | 3H    | depends on #253                                   |
| 269 | injection dosing framework for KB retrieval (research)   | Search      | 3H    | depends on #268                                   |
| 108 | OCR preprocessing for scanned documents (+Chandra v2)    | Ingest      | 4     | note 30 K11 update                                |
| 109 | evaluate Docling + Marker for layout-aware extraction    | Ingest      | 4     | note 30 K10 update (was: Tableformer only)        |
| 247 | research: evaluate Kreuzberg v4.50                       | Ingest      | 4     |                                                   |
| 107 | epic: semantic code indexing                             | Ingest      | 4     |                                                   |
| 12  | migrate entity graph to neo4j                            | Scale       | 4     |                                                   |
| 65  | evaluate LanceDB as sqlite-vec alternative               | Scale       | 4     |                                                   |
| 80  | web UI (Svelte + Rust WASM graph)                        | Scale       | 4     |                                                   |
| 262 | multimodal embedding as secondary retrieval signal       | Embedding   | 4+    | depends on Ollama #5304 or ONNX image path        |
| 359 | KB-P0-A LongTracer Phase 2 ingestion quality gate        | Ingest      | 3I    | notes 31-32 P0. ADR drafted                       |
| 360 | KB-P0-B pivot verification to MemArch-Bench-first        | Foundation  | 3I    | notes 31-32 P0. ADR drafted                       |
| 361 | KB-P1-C Embedding Adapters V2 (research)                 | Embedding   | 3I    | notes 31-32 P1. needs research                    |
| 362 | KB-P1-D sem structural-hash for code ingest (research)   | Ingest      | 3I    | notes 31-32 P1. needs research                    |
| 363 | KB-P1-E compile-upfront positioning docs                 | Foundation  | 3I    | notes 31-32 P1. docs only                         |
| 364 | KB-P1-F paragraph-level provenance                       | Ingest      | 3I    | notes 31-32 P1. depends on #325                   |

**Umbrella issues** (closed when child decomposition issues land):

| #   | Title                                           | Children                       | Status |
| --- | ----------------------------------------------- | ------------------------------ | ------ |
| 183 | god module -- ingest.py 1950 LOC, 5 concerns    | ✔ #234, ✔ #235, ✔ #236, ✔ #237 | ✔      |
| 184 | 250-line duplication ingest_file/reingest_file  | ✔ #237                         | ✔      |
| 185 | chunk insert+dedup boilerplate repeated 5 times | ✔ #236                         | ✔      |
| 186 | extract_figures() 500-LOC god function          | ✔ #241                         | ✔      |
| 215 | papers.py god module -- 5 responsibilities      | ✔ #238, ✔ #239                 | ✔      |
| 216 | server.py god module -- 38 tools in 1083 LOC    | ✔ #242 (PR #310)               | ✔      |
| 124 | decouple index construction from query serving  | ✔ #349, #350, #351             | open   |

**Plan issues** (reference-only, all closed unless noted):

| #   | Title                                                | Status |
| --- | ---------------------------------------------------- | ------ |
| 171 | plan: Phase 1 -- Multi-space embedding registry      | ✔      |
| 174 | plan: Phase 2 -- Matryoshka truncation support       | ✔      |
| 177 | plan: Phase 3 -- A/B embedding space comparison      | ✔      |
| 178 | perf: search query optimization opportunities (#172) | ✔      |
| 248 | plan: stage-2 cross-encoder reranking (#106)         | ✔      |
| 311 | plan archive: decompose server.py (#242)             | ✔      |
| 337 | plan: mixed raster+vector pages (#155)               | ✔      |
| 341 | plan: hybrid enrichment Phase 4 (#111)               | ✔      |
| 348 | plan: persistent OmniParser HTTP server (#334)       | ✔      |
| 353 | plan archive: per-element canvas/SVG capture         | ✔      |
| 357 | plan: `<picture>` and srcset (#164)                  | ✔      |

> #337 and #341 closed as plan archives (2026-04-09).

---

## Phase 0 -- Finish Pending Work ✔

**Goal:** Land in-flight PRs, fix known bugs, clean up tech debt.

All items complete: ~~#89~~ (PR merged -> closed #88), ~~#85~~ (PR #112),
~~#78~~ (PR #118), ~~#46~~ (PR #114), ~~#45~~ (PR #116), ~~#16~~ (PR #115).

---

## Phase 1 -- Documentation & Rename ✔

**Goal:** Stabilize the project identity and make it approachable.

All items complete: ~~#71~~ (PR #120, partial -- typing/API ref remain),
~~#101~~ (PR #123).

---

## Phase 2 -- Embedding Architecture + Search Quality ✔

**Goal:** Upgrade the embedding and search infrastructure.

All 13 items complete: ~~#95~~ (PR #135), ~~#15~~ (PR #133), ~~#110~~ (PR #137),
~~#82~~ (PR #142), ~~#126~~ (PR #138), ~~#127~~ (PR #143), ~~#128~~ (PR #134),
~~#130~~ (PR #136), ~~#105~~ (PR #144), ~~#99~~ (PR #172), ~~#100~~ (PR #170),
~~#139~~ (PR #169), ~~#106~~ (PR #249).

---

## Phase 2.5a -- Bugs & Safety Fixes ✔

**Goal:** Fix all known bugs and safety issues from Phase 2 and super-qa audit.

All 17 items complete:

```text
✔ #163 (qwen3.5 thinking-mode)        ✔ #160 (zombie conclusions, PR #272)
✔ #152 (stale inline images, PR #271)  ✔ #151 (getaddrinfo SSRF, PR #270)
✔ #150 (zero-norm embeddings, PR #274) ✔ #165 (auto_relate fallback, PR #280)
✔ #166 (scan_relationships 2x, PR#283) ✔ #180 (embed failure rollback, PR #282)
✔ #182 (relocate_paper txn, PR #281)   ✔ #195 (path_conflict unbound, PR #286)
✔ #197 (LIKE injection, PR #285)       ✔ #198 (lastrowid falsy check)
✔ #201 (folder boost div0, PR #287)    ✔ #202 (offset drift, PR #290)
✔ #203 (bib_path discarded, PR #291)   ✔ #204 (supersede rollback, PR #289)
✔ #212 (PIL not closed, PR #288)       ✔ #276 (vision FK cleanup)
```

---

## Phase 2.5b -- Module Decomposition & Refactoring ✔

**Goal:** Break god modules into focused, single-responsibility modules.

All 16 items complete:

**Step 1 -- leaf extractions (parallel):**
✔ #236 (PR #295), ✔ #238 (PR #293), ✔ #239 (PR #292), ✔ #240 (PR #294),
✔ #243 (PR #296).

**Step 2 -- ingest decomposition (needs Step 1):**
✔ #234 (PR #303), ✔ #235 (PR #306), ✔ #237 (PR #307).

**Step 3 -- final decompositions (needs Step 2):**
✔ #241 (PR #308), ✔ #242 (PR #310).

**Parallel with any step:**
✔ #277 + #278 (PR #304), ✔ #141 (PR #302), ✔ #140 (PR #300), ✔ #154 (PR #299),
✔ #158 (PR #301).

All 6 umbrella issues (#183-#186, #215, #216) closed.

---

## Phase 2.5c -- Super-QA Medium Findings

**Goal:** Address remaining medium-severity findings. Lower urgency than 2.5a/2.5b
but should land before Phase 3 features to prevent compounding tech debt.

### Security & Validation -- 7 items ✔

```text
✔ #187 (bare except, PR #312)          ✔ #188 (search validation, PR #313)
✔ #189 (omniparser_path, PR #316)      ✔ #190 (LLM SSRF, PR #318)
✔ #191 (JSON validation, PR #314)      ✔ #192 (prompt injection, PR #317)
✔ #193 (FTS5 sanitization, PR #315)
```

### Performance (N+1, O(n^2)) -- 11 items

```text
#181 (auto_relate O(P*C^2))            #199 (unbounded IN compare_papers)
#200 (chunk_strategy unbatched IN)     #205 (get_paper N+1)
#206 (suggest_relationships all)       #207 (O(n*m) entity dedup)
#208 (N+1 get_entities)                #209 (O(E*C) entity re-linking)
#210 (N+1 content_hash lookups)        #211 (N+1 conclusion sources)
#213 (single-pass entity duplication)
```

### Code Quality & Coupling -- 9 items

```text
#194 (f-string SQL assembly)           #196 (add_relationship error ignored)
#214 (vision <-> ingest coupling)      #218 (reingest owns relationship inv.)
#219 (primitive obsession -- dicts)    #220 (duplicated FK cleanup)
#221 (record_method/dataset WET)       #297 (sync_bibtex O(n) lookup)
#298 (llm.py Gemini review items)
```

### Documentation & Tests -- 13 items

```text
#217 (status() 9 inline SQL)           #222 (ingest_file no docstring)
#223 (ingest_directory no docstring)   #224 (keyword_prefilter docs gap)
#225 (max_workers docs gap)            #226 (phantom threshold in docs)
#227 (no docstrings get_methods)       #228 (routes/ 46 funcs untested)
#229 (_validate_bib_path zero tests)   #230 (_cluster_bboxes zero tests)
#231 (_folder_boost zero tests)        #309 (flaky pymupdf4llm test)
#319 (FTS OperationalError too broad)
```

### Aggregate / Deferred -- 2 items

```text
#232 (low findings, 55 items)          #233 (info findings, 30 items)
```

**Parallelism:** All items within each sub-category are independent. Cross-category
items are also independent. Soft dependencies: #214 and #219 are easier after
Phase 2.5b decompositions (done). #228 is easier now that #242 has landed.

**Exit criteria:** All medium super-qa findings addressed or explicitly deferred.

---

## Phase 3 -- Intelligence, Integration & Search Refinement

**Goal:** Connect the four-layer architecture, refine search quality, and polish
ingest pipelines with follow-up enhancements.

### Completed Phase 3 items

```text
✔ #94  (int8/bit quantization, PR #355)          ✔ #125 (closed N/A: DiskANN)
✔ #111 (hybrid enrichment, PR #340)               ✔ #334 (OmniParser server, PR #352)
✔ #155 (mixed raster+vector, PR #338)             ✔ #131 (web images Phase 2, PR #339)
✔ #132 (web images Phase 3, PR #347)              ✔ #164 (<picture>/srcset, PR #356)
✔ #349 (indexer CLI, PR #358)
```

### Phase 3A -- Search Quality (critical path)

**Goal:** Highest-impact improvements to retrieval accuracy. This is the core
value proposition of the knowledge base.

```text
           independent                    depends on #130 (done)
           ┌──────────────┐               ┌──────────────┐
           │ #325 context │               │ #253 query   │
           │ retrieval    │               │ classifier   │
           │ (-67% fail)  │               └──────┬───────┘
           └──────────────┘                      │
           ┌──────────────┐               ┌──────┴───────┐
           │ #327 query   │               │ #254 reason  │
           │ expansion    │               │ hints        │
           └──────────────┘               └──────┬───────┘
           ┌──────────────┐               ┌──────┴───────┐
           │ #252 entity  │───────────────│ #258 absten  │
           │ overlap      │               │ diagnostic   │
           └──────────────┘               └──────────────┘
           ┌──────────────┐               ┌──────────────┐
           │ #251 graph   │               │ #259 chunk   │
           │ expand       │               │ strategy     │──── depends on #253
           └──────────────┘               └──────────────┘
           ┌──────────────┐               ┌──────────────┐
           │ #129 retriev │               │ #246 budget  │
           │ plan IR      │               │ annotations  │──── zero effort
           └──────────────┘               └──────────────┘
           ┌──────────────┐
           │ #250 golden  │──── prerequisite for #261 (Phase 3H)
           │ set (100+)   │
           └──────────────┘
```

**Priority order:**

1. **#325** (contextual retrieval) -- Anthropic measured -67% retrieval failure.
   Highest single-item ROI in the entire roadmap. Independent.
2. **#326** (chunk enrichment) -- Complements #325. Keywords, questions, summaries
   prepended to chunks before embedding. Independent.
3. **#327** (query expansion) -- Rephrase queries 2-3x before search. Independent.
4. **#253** (query-type classifier) -- Unlocks #254, #258, #259, and the Level 2
   injection chain (#268, #269). Critical dependency node.
5. **#252** (entity-overlap signal) -- Low effort, independent. Unlocks #258.
6. **#251** (graph-expand search) -- Uses existing relationship edges. Independent.
7. **#129** (retrieval plan IR) -- Benefits from ✔ #106 (reranking). Medium effort.
8. **#246** (retrieval budget annotations) -- Zero-code MCP description change.
9. **#250** (golden set) -- Required before Bayesian optimization (#261).
10. **#254, #258, #259** -- Depend on #253 (see above).

**Dependency chains:**

- #253 -> #254 -> #257 (Phase 3H): Query classifier feeds reasoning hints, which
  feed reasoning-aware context preparation.
- #253 -> #268 -> #269 (Phase 3H): Query classifier enables Level 2 injection.
- #252 -> #258: Entity overlap enables abstention diagnostic.

### Phase 3B -- Embedding & Quantization

**Goal:** Compression, versioning, and model evaluation for the embedding layer.

```text
#344 (TurboQuant 4-bit)     ─── depends on ✔ #94
#345 (binary+int8 rescore)  ─── depends on ✔ #94
#328 (embedding versioning) ─── independent, #1 production pain point
#149 (ONNX pre-tokenized)   ─── depends on ✔ #95
#332 (EmbeddingGemma eval)  ─── independent operational eval
```

**Parallelism:** All items are independent of each other. #344 and #345 are
alternative compression strategies -- good candidates for A/B comparison using
the multi-space architecture (✔ #99).

**#328** is the most impactful here: without embedding versioning, model upgrades
require full re-embedding with no way to track which vectors use which model.

### Phase 3C -- Ingest Pipeline Improvements

**Goal:** Harden ingestion with table-awareness, provenance, and follow-up fixes.

```text
Critical / bugs:
  #275 (stale inline images on extraction switch) ─── bug, independent
  #342 (_extract_captions full paragraph)         ─── bug, independent

Follow-ups from completed work:
  #335 (OmniParser skip heuristic)   ─── depends on ✔ #334
  #354 (OmniParser temp file perf)   ─── depends on ✔ #334
  #162 (folder summary + ingest_url) ─── depends on ✔ #126

New capabilities:
  #330 (table-aware chunking)        ─── independent
  #322 (content provenance gate)     ─── independent, security
  #63  (document watch/sync)         ─── independent, pairs with #350
  #64  (workspace tagging)           ─── independent, pairs with #264
  #173 (N:M chunk-to-source)         ─── independent schema evolution

Deferred (3C+):
  #255 (cross-reference resolution)  ─── independent
  #260 (figure-in-context chunking)  ─── independent
```

**Priority:** Fix bugs first (#275, #342), then follow-ups (#335, #354, #162),
then new capabilities based on need.

### Phase 3D -- Search Refinement & Extraction Hardening

**Goal:** Keyword quality, prediction errors, and extraction safety.

```text
Keyword / stopword (implement together):
  #161 (compound technical terms)    ─── depends on ✔ #130
  #147 (configurable stopwords)      ─── depends on ✔ #130
  #179 (multilingual stopwords)      ─── extends #147, coordinate together

Prediction errors:
  #145 (scope_miss error type)       ─── depends on ✔ #127
  #146 (workspace-scoped filtering)  ─── depends on ✔ #127

Extraction hardening:
  #320 (second-hop prompt injection) ─── security, independent
  #321 (preserve user-sourced data)  ─── independent
```

**Parallelism:** All three groups are independent of each other. Within keyword
group, implement #147 first, then #161 and #179 together.

### Phase 3E -- Memory Engine Integration

**Goal:** Connect knowledge-base to memory-engine for the four-layer cognitive
architecture (intelligence -> memory -> wisdom -> knowledge).

```text
#102 (hook-based ME integration)
  ├──▶ #103 (wisdom -> knowledge pipeline)
  └──▶ #104 (memory -> wisdom consolidation)

#333 (incremental indexing + ME linking) ─── pairs with #102, ✔ #349
#323 (memory taxonomy overlay for MCP)   ─── independent
```

**External dependency:** `memory-engine` must expose an MCP server or API.
NYX12 bidirectional linking: KnowledgeBaseConnector needs intent-aware retrieval
and Knowledge-URI-to-Memory-fact linking.

**Dependency chain:** #102 -> #103, #104 (sequential). #333 pairs with #102 and
leverages the indexer CLI (✔ #349).

### Phase 3F -- Multi-Agent Safety & Provenance

**Goal:** ACL, provenance tracking, evidence basis, and decontamination for
multi-agent environments.

```text
Independent (can start immediately):
  #263 (MCP server ACL layer)        ─── capability-token write-gating
  #264 (contributing_agent field)    ─── prerequisite for #267
  #265 (evidence-basis metadata)     ─── Observed/Inferred/Synthesized enum
  #266 (staleness/drift detection)   ─── periodic validation pass
  #324 (pre-inject existing facts)   ─── dedup context for extraction

Depends on #264:
  #267 (contrastive decontamination) ─── MemCollab: -5.4pp naive transfer
```

**Parallelism:** #263, #264, #265, #266, #324 are all independent. #267 requires #264 (contributing_agent field must exist before per-agent bias detection works).

### Phase 3G -- Infrastructure & Documentation

**Goal:** Build/serve separation and architecture documentation.

```text
#350 (serve-only MCP mode)        ─── depends on ✔ #349, pairs with #63
#351 (horizontal scaling)         ─── depends on #350, gated on need
#122 (cognitive architecture docs)─── independent, anytime
```

**#351** is speculative -- do not implement until multi-machine deployment is
actually needed.

### Phase 3H -- Research & Evaluation (deferred)

**Goal:** Research items and evaluations that inform future phases. These can run
in parallel with any other work but are not on the critical path.

```text
#331 (Gemma 4 26B benchmark)            ─── operational eval, independent
#256 (olmOCR-bench eval)                ─── operational eval, independent
#329 (late chunking evaluation)         ─── research, needs 8K+ embed model
#257 (reasoning-aware context, Table 7) ─── depends on #253, #254
#261 (Bayesian pipeline optimization)   ─── depends on #250 (golden set)
#268 (Level 2 injection interface)      ─── depends on #253
#269 (injection dosing framework)       ─── depends on #268
```

**Dependency chains:**

- #253 -> #254 -> #257: Query classifier -> reasoning hints -> reasoning-aware
  context preparation.
- #253 -> #268 -> #269: Query classifier -> Level 2 injection -> dosing framework.
- #250 -> #261: Golden set required for Bayesian optimization.

### Phase 3I -- April 2026 Landscape Gaps (notes 31-32)

**Goal:** Integrate the six new roadmap gaps derived from landscape review #32
(§11.2 of `autonomous-agent-project/raw/docs/summaries/04-results-and-roadmap.md`).
Two P0 items with proposed ADRs, four P1 items (two require research before
an ADR is drafted).

```text
P0 (critical, ADRs archived in tracking issues)
  #359 KB-P0-A LongTracer Phase 2 ingestion quality gate
       → ADR archived in #359
       → independent, CPU-only, default-off
  #360 KB-P0-B pivot verification to MemArch-Bench-first
       → ADR archived in #360
       → gated on #250 for test_retrieval_quality.py

P1 (research / docs)
  #361 KB-P1-C Embedding Adapters V2 (research label)
       → needs license verification, registry coverage, recall
         retention measurement before ADR
       → complements ✔ #99, in-progress #328
  #362 KB-P1-D sem structural-hash for code ingest (research label)
       → needs Python↔Rust integration decision (subprocess / HTTP /
         MCP / PyO3 / reimplement)
       → cross-repo coordination with memory-engine ME-P1-D
  #363 KB-P1-E compile-upfront positioning vs Karpathy/rohitg00/atomicmemory
       → docs-only, README + architecture-overview update
       → paper #3 §Related Work alignment
  #364 KB-P1-F paragraph-level provenance attribution
       → depends on #325 (contextual retrieval) for non-interference
       → benefits #260 (figure-in-context chunking), #162 (folder summary
         for ingest_url)
```

**Priority order:**

1. **#359** (LongTracer gate) — landing first. Closes the "summaries silently
   contradicting chunks" failure mode and establishes the `review_queue`
   surface that other quality-gate work can reuse.
2. **#360** (MemArch-Bench pivot) — docs + test scaffolding, no external
   deps, unblocks all future verification claims.
3. **#363** (positioning docs) — low cost, high value for paper #3. Can
   run in parallel with #359/#360.
4. **#361** and **#362** (research) — can run in parallel with each other
   and with the other Phase 3I items. Each produces an ADR as its
   deliverable, not code.
5. **#364** (paragraph provenance) — last, because it depends on #325
   (contextual retrieval) landing first to avoid chunk-text-mutation
   ordering conflicts.

**Dependency chains:**

- #359 independent of all Phase 3 items
- #360 → gates on #250 for `test_retrieval_quality.py` (other three MemArch-Bench suites are independent)
- #361, #362 independent; produce ADRs as deliverables
- #363 independent; documentation only
- #364 blocked by #325 (chunk-text-mutation ordering)

**Cross-repo coordination:**

- #362 (sem) mirrors memory-engine ME-P1-D (same external dependency, different integration point). Coordinate via the ME umbrella issue [dutiona/memory-engine#237](https://github.com/dutiona/memory-engine/issues/237) to avoid duplicate Python↔Rust integration spikes. One comparative study of integration options (subprocess / HTTP server / MCP client / PyO3 / reimplement) should inform both repos.
- **ME umbrella ([dutiona/memory-engine#237](https://github.com/dutiona/memory-engine/issues/237))** tracks the analogous §11.1 memory-engine gaps (ME-P0-A Wisdom Revision Gate DSL, ME-P0-B Allen Interval Algebra, ME-P0-C prospective-memory docs, ME-P1-D sem code-fact backend, ME-P1-E event-based predicate DSL, ME-P2-F/G/H docs and callout mechanism, ME-P3-I Frona tracking). Knowledge-base is not responsible for those, but KB-P1-D coordination flows through the umbrella.
- #360 (MemArch-Bench) aligns with paper #2 (MemArch-Bench) publication opportunity in autonomous-agent-project. Test cases written under `tests/memarchbench/` are directly reusable as paper #2's KB empirical section.

**Not in Phase 3I (tracking-only, see Parking Lot):**

- KB-P2-G (DocLing + olmOCR ADR) — overlaps existing #108, #109 in Phase 4
- KB-P2-H (RDF-star Bayesian confidence propagation) — exploratory, no owner
- KB-P3-I (Graphify / atomicmemory / HydraDB) — positioning reference only

**Exit criteria for Phase 3I:** All six issues resolved (either implemented,
ADR-drafted-and-rejected, or explicitly deferred with rationale in the issue).
At minimum, #359 and #360 must land for the Phase 3I banner to clear.

### Phase 3 Summary

| Subphase | Items | Theme                        | Blocking? |
| -------- | ----- | ---------------------------- | --------- |
| 3A       | 12    | Search quality               | Critical  |
| 3B       | 5     | Embedding & quantization     | Important |
| 3C       | 12    | Ingest pipeline              | Mixed     |
| 3D       | 7     | Search refinement & security | Optional  |
| 3E       | 5     | Memory engine integration    | External  |
| 3F       | 6     | Multi-agent safety           | Optional  |
| 3G       | 3     | Infrastructure               | Optional  |
| 3H       | 7     | Research & evaluation        | Deferred  |
| 3I       | 6     | April 2026 landscape gaps    | Mixed     |

**Total Phase 3 open items: 63** (plus 9 already completed).

**Phase 3 exit criteria:** Search pipeline has contextual retrieval + query
expansion + reranking + retrieval plans. Chunk enrichment operational. Multi-agent
write operations are ACL-gated with contributing_agent provenance. Evidence-basis
metadata on conclusions. Staleness detection operational. Memory-engine hooks
connected.

---

## Verification

> Added 2026-04-13 per KB-P0-B (notes 31-32 integration).
> Full rationale: ADR archived in #360.

**Primary instrument: MemArch-Bench (KB slice).** Knowledge-base's
verification plan is property-test-first, not benchmark-score-first. The
[MemPalace drama](https://www.reddit.com/r/AIMemory/comments/1sgvsxb/)
(April 2026) established that LoCoMo has a 6.4% ground-truth error rate,
its LLM judge accepts 63% of intentionally-incorrect answers, and
LongMemEval-S's 115K-token per-question contexts permit retrieval bypass.
These defects make public LoCoMo/LongMemEval numbers untrustworthy for
cross-system comparison, so KB pivots to architectural-property testing.

The KB slice of MemArch-Bench lives under `tests/memarchbench/` and
consists of four invariant suites:

| Suite                       | Property                                                   | Status        |
| --------------------------- | ---------------------------------------------------------- | ------------- |
| `test_supersession.py`      | `reingest(newer)` ⇒ search returns newer, not older        | proposed      |
| `test_retrieval_quality.py` | Recall@k / nDCG@k / MRR on KB golden set                   | gated on #250 |
| `test_prediction_errors.py` | Prediction-error fires iff stale; precision + recall ≥ 0.9 | proposed      |
| `test_entity_stability.py`  | Same paper → same entity IDs across chunking strategy swap | proposed      |

**Secondary reference: LoCoMo / LongMemEval with caveats.** Kept only as
reference numbers, always reported alongside: (1) the MemPalace drama
citation, (2) recall@k and end-to-end QA accuracy in separate tables with
explicit column labels, (3) no cross-system claims until the upstream
answer-key and judge issues are resolved.

**Gated on:** issue #250 (retrieval coverage golden set, 100+ questions)
for `test_retrieval_quality.py`. Other three suites are independent.

**Paper #2 alignment:** MemArch-Bench is also the empirical framework for
paper #2 (MemArch-Bench). Test cases under `tests/memarchbench/` are
directly reusable as the KB empirical section of that paper.

**Out of KB scope:** bi-temporal point-in-time accuracy and
type-appropriate decay are memory-engine properties and are tested there,
not here. This is a deliberate scope boundary.

---

## Phase 4 -- New Frontends & Scale

**Goal:** Extend the knowledge base beyond research papers.

```text
#108 (OCR preprocessing)  ─┐
                           ├── document extraction evaluations (parallel)
#109 (Docling + Marker)   ─┤
                           │
#247 (Kreuzberg eval)     ─┘

#107 (code indexing epic)  ─── multi-phase, largest single feature
#12  (neo4j migration)     ─── independent, gated on scale need
#65  (LanceDB eval)        ─── independent, gated on scale need
#80  (web UI)              ─── independent, high effort / high reward
#262 (multimodal embed)    ─── depends on Ollama #5304 or ONNX image path
```

**#108, #109, #247** are document extraction evaluations -- natural to run together
as a comparative study. #247 (Kreuzberg) may subsume parts of #108 (OCR) since
Kreuzberg includes multi-backend OCR.

**#107 (code indexing)** is the largest single feature -- an epic with 6 sub-phases
(tree-sitter parsing, git-aware re-indexing, cross-file references, etc.). Break
into sub-issues when work begins. Benefits from #64 (workspace tagging) and #326
(chunk enrichment) but does not strictly require them.

**#12 and #65** are mutually exclusive alternatives for scale. Evaluate before
committing. Neither solves problems that exist at current corpus size.

**#80 (web UI)** is a standalone project. Valuable once the knowledge base has
enough content to warrant visual exploration. Svelte frontend + Rust WASM for
graph visualization.

**#262 (multimodal embedding)** blocked on upstream support (Ollama multimodal
embedding API or ONNX image embedding path). Benefits from ✔ #94 (quantized
storage) and #328 (embedding versioning).

**Exit criteria:** At least one new ingest frontend working (OCR or code).

---

## Critical Path to Phase 4

The shortest path through Phase 3 to reach Phase 4 items #107, #80, and #262:

```text
                NOW
                 │
    ┌────────────┼────────────────┐
    │            │                │
    ▼            ▼                ▼
 Phase 3A    Phase 3B         Phase 3C
 (search)    (embedding)      (ingest)
    │            │                │
    │     ┌──────┤                │
    │     │      │                │
    │   #328     │              #326
    │  (version) │          (enrichment)
    │     │      │                │
    │     │   #344/#345           │
    │     │  (compress)           │
    │     │      │                │
    ▼     ▼      ▼                ▼
 #325  ─────── PHASE 4 GATE ──────────
 #327            │
 #253            │
    │    ┌───────┼───────┐
    │    │       │       │
    │    ▼       ▼       ▼
    │  #107    #80     #262
    │  (code)  (UI)   (multi-
    │                  modal)
    │
    └── continues (3D-3H)
```

**Minimum viable Phase 3 for Phase 4 entry:**

1. **#325** (contextual retrieval) -- Without this, code indexing (#107) will have
   the same -67% failure rate on retrieval. ~1 session.
2. **#326** (chunk enrichment) -- Code chunks especially benefit from keyword and
   summary enrichment. ~1 session.
3. **#328** (embedding versioning) -- Required before #262 (multimodal embedding)
   can coexist with text embeddings. Also prevents pain when upgrading models for
   code. ~1 session.
4. **#275, #342** (bug fixes) -- Clean up before adding more ingest complexity.

**Items that can run in parallel with Phase 4:**

- All of 3D (keyword/stopword, prediction errors)
- All of 3E (memory engine integration)
- All of 3F (multi-agent safety)
- All of 3G (infrastructure)
- All of 3H (research)
- All of 3I except #364, which requires #325 (see below)
- 3A items after #325/#327/#253 (diminishing returns)
- 3B items #344/#345 (quantization -- nice to have, not blocking)

**Phase 4 items are independent of each other.** #107, #80, and #262 can be
worked on in parallel once the gate items above are done. #107 benefits most
from Phase 3 work; #80 is truly standalone; #262 is blocked on upstream.

**Phase 3I interactions with the critical path:**

- **#359 (LongTracer gate)** is independent — default-off and CPU-only, lands in parallel with any 3A/3B/3C work.
- **#360 (MemArch-Bench pivot)** is independent for the docs/scaffolding slice; the `test_retrieval_quality.py` suite is gated on #250 (golden set) landing first.
- **#361, #362 (research issues)** produce ADRs as deliverables, not code — run in parallel with anything.
- **#363 (positioning docs)** is docs-only, independent.
- **#364 (paragraph provenance)** must land **after #325 (contextual retrieval)** — contextual retrieval mutates chunk text at ingest time, and paragraph offsets must be computed on the **original** chunk text before that mutation to preserve anchor semantics. #364 is therefore a post-#325 item, not a parallel-with-#325 item.
- **#362 (sem structural-hash)** overlaps the #107 (code indexing epic) Phase 4 work and should coordinate with it — sem would be a natural fit inside #107's first phase. Decide once #362's integration-path ADR is drafted.

---

## Dependency Graph

```text
Phase 0 ✔       Phase 1 ✔       Phase 2 ✔            Phase 2.5a ✔
────────        ────────        ────────             ────────
#89 ──┐                         ✔ #95, ✔ #99        All 17 items ✔
#85 ──┤                         ✔ #100, ✔ #15       (bugs & safety)
#78 ──┼──▶ ✔ #71               ✔ #110, ✔ #82
#46 ──┤    ✔ #101              ✔ #126-#130
#45 ──┤                         ✔ #105, ✔ #139
#16 ──┘                         ✔ #106


Phase 2.5b ✔         Phase 2.5c (partial)
────────             ────────
All 16 items ✔       ✔ Security (7/7)
(decomposition)      Perf: #181,#199-#211,#213 (0/11)
                     Quality: #194,#196,#214,#218-#221,#297,#298 (0/9)
                     Docs/Tests: #217,#222-#231,#309,#319 (0/13)
                     Aggregate: #232,#233 (deferred)


Phase 3 (9 done, 57 open)
────────
✔ #94, ✔ #111, ✔ #334, ✔ #155, ✔ #131, ✔ #132, ✔ #164, ✔ #349, ✔ #125 (N/A)

3A Search Quality ──────────────────────────────────────────────────
  #325 (contextual retrieval)    #327 (query expansion)
  #326 (chunk enrichment)        #253 ──▶ #254 ──▶ #259
  #252 ──▶ #258                  #251, #129, #246, #250

3B Embedding ───────────────────────────────────────────────────────
  #344, #345 (need ✔ #94)        #328, #149, #332

3C Ingest ──────────────────────────────────────────────────────────
  #275, #342 (bugs)              #335, #354 (need ✔ #334)
  #162 (need ✔ #126)             #330, #322, #63, #64, #173
  #255, #260 (deferred)

3D Refinement ──────────────────────────────────────────────────────
  #161, #147 ──▶ #179            #145, #146
  #320, #321

3E Integration ─────────────────────────────────────────────────────
  #102 ──▶ #103, #104            #333, #323

3F Multi-Agent ─────────────────────────────────────────────────────
  #263, #264 ──▶ #267            #265, #266, #324

3G Infrastructure ──────────────────────────────────────────────────
  ✔ #349 ──▶ #350 ──▶ #351      #122

3H Research ────────────────────────────────────────────────────────
  #331, #256, #329               #253 ──▶ #268 ──▶ #269
  #253+#254 ──▶ #257             #250 ──▶ #261

3I April 2026 landscape gaps ──────────────────────────────────────
  #359 (LongTracer gate), #360 (MemArch-Bench pivot) ── P0, ADRs drafted
  #361 (Embedding Adapters, research), #362 (sem, research)
  #363 (positioning docs)        #364 (paragraph provenance, needs #325)
  #360 ──▶ depends on #250 (golden set) for retrieval-quality suite
  #364 ──▶ depends on #325 (contextual retrieval)


Phase 4 ────────────────────────────────────────────────────────────
  #108, #109, #247 (extraction evals, parallel)
  #107 (code indexing epic)
  #12, #65 (scale alternatives)
  #80 (web UI)
  #262 (multimodal embedding, blocked on upstream)
```

---

## Parking Lot

Issues that are valid but have no immediate timeline. Re-evaluate quarterly.

- **#12** (neo4j) -- Not needed until entity graph exceeds sqlite's comfort zone
  (~100K+ entities). Current corpus is far below this.
- **#65** (LanceDB) -- Not needed until sqlite-vec shows performance problems.
  Benchmark first, migrate only if proven necessary.
- **#80** (web UI) -- High effort, high reward, but not blocking any other work.
  Good candidate for a focused sprint once the core is stable.
- **#107** (code indexing) -- The largest single feature. Requires its own
  planning session when ready. See issue for 6-phase breakdown.
- **#232** (low findings) -- 55 items to triage individually. Pick off as
  convenient between features.
- **#233** (info findings) -- 30 items. Reference only, no action needed unless
  a specific item becomes relevant.
- **#351** (horizontal scaling) -- Gated on actual multi-machine deployment need.
  Do not implement speculatively.

### April 2026 landscape gaps (notes 31-32 tracking)

P0 and P1 gaps from landscape #32 §11.2 are tracked via GitHub issues (see
Notes 31-32 Integration banner at the top of this file). P2 and P3 gaps are
tracking-only entries, listed here:

- **KB-P2-G (DocLing + olmOCR ADR — PDF ingestion backends).** The KB
  ingestion ADR comparing Marker / Docling / olmOCR / MinerU on OmniDocBench
  remains overdue per landscape #32 §14. Marker is fastest (20-120 pages/s on
  H100), Docling pairs with Markdown-first targets, olmOCR has the best layout
  fidelity for scanned archives, MinerU is strong on Chinese and scientific
  content. Choose based on corpus. Proposed ADR path:
  `docs/adr/phase2-pdf-ingestion-backend.md`. Overlaps existing issues #108
  (OCR preprocessing + Chandra v2) and #109 (Docling + Marker evaluation) in
  the Phase 4 extraction-evals cluster.
- **KB-P2-H (RDF-star Bayesian confidence propagation exploration).**
  arshadansari27/knowledge-service (landscape #32 §17.2.7) implements
  RDF-star with Bayesian confidence scores attached to triples and Noisy-OR
  propagation through forward-chaining inference. Evaluate whether confidence
  propagation is worth the added complexity over a simpler "confidence as a
  fact attribute with decay" model. Exploratory, Phase 3 or 4, no ADR yet.
- **KB-P3-I (Track Graphify, atomicmemory/llm-wiki-compiler, HydraDB).**
  Parallel compile-upfront knowledge-compilation implementations. Track
  feature additions but do not integrate — they are positioning references
  for paper #3 §Related Work, not dependencies.

Source: `autonomous-agent-project/raw/docs/summaries/04-results-and-roadmap.md`
§11.2 and `autonomous-agent-project/raw/landscape/32-memory-knowledge-landscape-april-week2-2026.md`.

---

## Quick Wins (< 1 session each)

**Phase 2.5c doc/test gaps** (independent, low risk):

- #222, #223, #224, #225, #226, #227, #229, #230, #231, #309

**Phase 3A quick wins:**

- #246 (retrieval budget annotations -- zero code change, MCP description only)
- #252 (entity-overlap signal -- low effort)

**Phase 3C bugs** (should land before new ingest work):

- #275 (stale inline images), #342 (caption extraction)

**Phase 3C follow-ups** (depend on completed work):

- #162, #335, #354

**Phase 3D keyword group** (implement together):

- #147 + #161 + #179

**Phase 3F independent items:**

- #263, #264, #265, #266, #324

**Phase 3I quick wins** (notes 31-32 landscape gaps):

- #363 (KB-P1-E compile-upfront positioning -- docs only, README + architecture-overview update)
- #360 (KB-P0-B MemArch-Bench pivot -- ROADMAP/README doc updates + test scaffolding landed here; full retrieval-quality suite is gated on #250)
