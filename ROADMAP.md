# Roadmap

> Last updated: 2026-03-19

34 open issues across 6 workstreams. This document establishes priority, ordering,
dependency chains, and parallelism opportunities.

## Issue Index

| #   | Title                                                 | Workstream  | Phase |
| --- | ----------------------------------------------------- | ----------- | ----- |
| 88  | pymupdf4llm production integration (Phase 2)          | Foundation  | 0     |
| 89  | **PR**: pymupdf4llm structured markdown extraction    | Foundation  | 0     |
| 85  | fix(vision): chunk_index encoding overflow            | Foundation  | 0     |
| 78  | refactor: executemany for config init                 | Foundation  | 0     |
| 46  | refactor: move SQL batching helpers to db.py          | Foundation  | 0     |
| 45  | refactor: .replace() instead of .format() in SQL      | Foundation  | 0     |
| 16  | feat: connectivity test for configure_llm             | Foundation  | 0     |
| 71  | docs: comprehensive documentation + typing + API ref  | Foundation  | 1     |
| 101 | chore: rename to knowledge-base                       | Foundation  | 1     |
| 99  | multi-space embedding architecture                    | Embedding   | 2     |
| 100 | dual chunking strategy (8K + 32K)                     | Embedding   | 2     |
| 95  | pluggable embedding providers                         | Embedding   | 2     |
| 94  | compressed vector indices (int8/bit)                  | Embedding   | 3     |
| 106 | stage-2 reranking in hybrid search                    | Search      | 2     |
| 105 | auto-relationship discovery via similarity            | Search      | 2     |
| 15  | parallelize map phase LLM calls                       | Search      | 2     |
| 110 | pymupdf4llm Phase 3: narrow vision pipeline scope     | Ingest      | 2     |
| 82  | extract inline images from web pages                  | Ingest      | 2     |
| 126 | folder-level semantic embeddings for context boosting | Search      | 2     |
| 127 | prediction-error detection for stale search results   | Search      | 2     |
| 128 | ingestion session tracking for co-occurrence signals  | Search      | 2     |
| 130 | keyword intent extraction pre-filter for search       | Search      | 2     |
| 111 | pymupdf4llm Phase 4: hybrid enrichment                | Ingest      | 3     |
| 129 | retrieval plan intermediate representation            | Search      | 3     |
| 63  | document watch/sync for auto-re-ingestion             | Ingest      | 3     |
| 64  | workspace/project tagging for chunk isolation         | Ingest      | 3     |
| 108 | OCR preprocessing for scanned documents               | Ingest      | 4     |
| 109 | evaluate IBM Tableformer for tables                   | Ingest      | 4     |
| 107 | epic: semantic code indexing                          | Ingest      | 4+    |
| 102 | hook-based memory-engine integration                  | Integration | 3     |
| 103 | wisdom → knowledge pipeline                           | Integration | 3     |
| 104 | memory → wisdom consolidation                         | Integration | 3     |
| 12  | migrate entity graph to neo4j                         | Scale       | 4     |
| 65  | evaluate LanceDB as sqlite-vec alternative            | Scale       | 4     |
| 80  | web UI (Svelte + Rust WASM graph)                     | Scale       | 4     |

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

## Phase 1 — Documentation & Rename

**Goal:** Stabilize the project identity and make it approachable.

```
#71 (documentation) ────────────┐
                                ├──▶ Phase 1 complete
#101 (rename to knowledge-base) ┘
```

**Sequential dependency:** Documentation should be written first, then the rename
applied. Writing docs for `knowledge_base` and immediately renaming to
`knowledge_base` doubles the work. But the rename touches every import, test,
and config — it's a natural point to also update all docs.

**Recommended order:**

1. ~~**#71 first**~~ — Write comprehensive docs against the current codebase. This
   forces a full audit of the API surface, which will surface inconsistencies
   worth fixing before the rename. **(done — PR #120, partial: docs workstream only)**
2. ~~**#101 second**~~ — Rename everything in one atomic PR. The docs written in step
   1 get updated as part of the rename. **(done — PR #123, squash merged)**

**Why documentation before features:** Every subsequent phase adds complexity.
Documenting the current state creates a baseline that makes future changes
reviewable. Without docs, new contributors (or future-you) can't assess whether
a change is correct.

**Exit criteria:** README rewritten, API reference exists, typing coverage
measured, project renamed.

---

## Phase 2 — Embedding Architecture + Search Quality

**Goal:** Upgrade the embedding and search infrastructure.

```
                    ┌── #99 (multi-space) ───┐
                    │                        │
#95 (providers) ────┤                        ├──▶ #106 (reranking)
                    │                        │
                    └── #100 (dual chunking) ┘

#110 (vision Phase 3)        ─── depends on PR #89 (Phase 2)
#105 (auto-relationships)    ─── independent
#128 (session co-occurrence) ─── independent, feeds #105
#15 (parallel LLM calls)    ─── independent
#82 (web page images)        ─── independent
#126 (folder embeddings)     ─── independent
#127 (prediction errors)     ─── independent
#130 (keyword pre-filter)    ─── independent, research: APC (arXiv:2506.14852)
```

**Dependency chain:**

- **#95 (providers) must come before or with #99 (multi-space).** Multi-space
  tables need the provider abstraction to know which provider populates which
  space.
- **#99 (multi-space) and #100 (dual chunking) are independent** but naturally
  pair — different chunk sizes map to different embedding spaces.
- **#106 (reranking) depends on #99** — reranking across multiple embedding
  spaces requires the multi-space query layer to exist first.

**pymupdf4llm chain:**

- **#110 (Phase 3)** depends on PR #89 / #88 (Phase 2) from Phase 0. Once Phase
  2 lands and extracted images are on disk, Phase 3 narrows the vision pipeline
  to process only those images instead of full-page renders.

**Parallelism:**

- #105 (auto-relationships), #128 (session co-occurrence), #15 (parallel LLM),
  #82 (web images), #110 (vision Phase 3), #126 (folder embeddings), #127
  (prediction errors), and #130 (keyword pre-filter) are fully independent of
  the embedding work and of each other. #128 feeds into #105 as a second
  relationship signal source.
- #99 and #100 can be developed in parallel.

**Exit criteria:** Multiple embedding models coexist, Matryoshka truncation
works, search quality measurably improves via reranking.

---

## Phase 3 — Intelligence & Integration

**Goal:** Connect the four-layer architecture.

```
#102 (hook-based memory integration) ──▶ #103 (wisdom→knowledge)
                                    ──▶ #104 (memory→wisdom)

#111 (vision Phase 4)       ─── depends on #110 (Phase 3)
#94 (int8/bit quantization) ─── independent (builds on #99)
#63 (document watch/sync)   ─── independent
#64 (workspace tagging)     ─── independent
#129 (retrieval plan IR)    ─── independent, benefits from #106
```

**Dependency chain:**

- **#102 is the prerequisite** for #103 and #104. The memory-engine MCP
  integration establishes the communication channel. Without it, the
  consolidation and curation pipelines have nothing to consolidate from.
- **#103 and #104 are independent of each other** — wisdom→knowledge and
  memory→wisdom are separate pipelines that happen to share the #102 transport.

**Parallelism:**

- #94 builds on Phase 2's #99 but is otherwise independent.
- #63 (watch/sync) and #64 (workspace tagging) are ingest improvements
  independent of the integration work.
- #129 (retrieval plan IR) is independent but benefits from #106 (reranking)
  being in place — the plan can specify reranking directives per sub-query.

**Vision pipeline chain:**

- **#111 (Phase 4)** depends on #110 (Phase 3). Hybrid enrichment combines
  pymupdf4llm structure + vision LLM semantic descriptions + OmniParser OCR.

**External dependency:** #102, #103, #104 all require `memory-engine` to expose
an MCP server. If memory-engine isn't ready, these issues are blocked.

**Exit criteria:** Hooks log to memory-engine, consolidation proposes wisdom
candidates, auto-curation suggests relationships.

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
Phase 0                Phase 1              Phase 2              Phase 3              Phase 4
────────               ────────             ────────             ────────             ────────

PR #89 ──────┐
#85 ─────────┤
#78 ─────────┼──▶ #71 (docs) ──▶ ┌─ #95 ──▶ #99 ──▶ #106      #102 ──┬──▶ #103     #108
#46 ─────────┤                   │          │                   │      └──▶ #104     #109
#45 ─────────┤    #101 (rename)──┤  #100 ───┘                   │                    #107
#16 ─────────┘                   │                              #94 (needs #99)      #12
                                 ├─ #105 ◀─ #128                #63                  #65
                                 ├─ #128 (sessions)             #64                  #80
                                 ├─ #15                         #129 (plan IR)
                                 ├─ #82
                                 ├─ #126
                                 ├─ #127                         #111 (needs #110)
                                 ├─ #130
                                 └─ #110 (needs PR #89)
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

All previous quick wins have been completed in Phase 0.
