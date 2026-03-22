# Super QA Findings — knowledge-base

Generated: 2026-03-22
Scope: All 9 modules (full codebase scan)
Agents dispatched: 32 (5 core x 4 large modules + combined audits for 5 smaller modules)
Conditional tools: bandit (25 findings), pip-audit (0 vulns), pyright (34 errors)

---

## Executive Summary

| Severity  | Count   | Auto-fixable | Report-only |
| --------- | ------- | ------------ | ----------- |
| Blocker   | 1       | 1            | 0           |
| High      | 12      | 7            | 5           |
| Medium    | 45      | 18           | 27          |
| Low       | 55      | 28           | 27          |
| Info      | 30      | 12           | 18          |
| **Total** | **143** | **66**       | **77**      |

### Supply Chain

- **pip-audit**: 0 known vulnerabilities
- **uv.lock**: present, 8 dependencies with lower-bound pins
- **Bandit**: 25 MEDIUM (all B608 SQL string construction — triaged as false positives; all use parameterized `?` placeholders)

### Pyright

- 34 type errors across 6 files (papers.py:11, extraction.py:8, ingest.py:8, vision.py:5, jobs.py:1, browser/render_page.py:1)

---

## Blocker (1)

### B-001: `_chunk_text` infinite loop when overlap >= size

- **Module**: ingest.py:101
- **Category**: correctness
- **Description**: If `overlap >= size`, `start = end - overlap` never advances. Causes infinite loop hanging the entire process.
- **Auto-fixable**: yes — add guard: `if overlap >= size: overlap = 0`
- **Source**: test-engineer:ingest.py

---

## High (12)

### H-001: Path traversal bypass in `_validate_bib_path`

- **Module**: server.py:88
- **Category**: security (CWE-22)
- **Description**: `str(p).startswith(str(home))` is a string prefix check. `/home/user2/evil.bib` passes validation when home is `/home/user`. Fix: use `p.is_relative_to(home)`.
- **Auto-fixable**: yes
- **Source**: specialist:server.py, code-reviewer:server.py

### H-002: SSRF on primary URL fetch in `ingest_url`

- **Module**: ingest.py:1792
- **Category**: security (CWE-918)
- **Description**: `_is_private_ip()` exists and is applied to image sub-requests, but NOT to the primary `httpx.get(url)` in `ingest_url`. Internal network hosts (169.254.169.254, localhost) are reachable.
- **Auto-fixable**: yes — add `_is_private_ip(parsed.hostname)` before the fetch
- **Source**: security-auditor:ingest.py, security-auditor:server.py

### H-003: No rollback on embedding failure in `ingest_file`

- **Module**: ingest.py:645-827
- **Category**: correctness (CWE-460)
- **Description**: If `_embed_with_config` raises after chunk_sessions writes, the connection is left dirty. No try/except or explicit transaction boundary.
- **Auto-fixable**: no — requires architectural decision on transaction scope
- **Source**: specialist:ingest.py

### H-004: `auto_relate` O(P\*C²) with per-paper DB round-trips

- **Module**: papers.py:373-438
- **Category**: performance
- **Description**: For each of P papers, executes a separate JOIN query. Inner loop is O(source_chunks \* other_chunks) in pure Python. Should batch-fetch and use matrix multiplication.
- **Auto-fixable**: no — requires algorithmic redesign
- **Source**: specialist:papers.py

### H-005: `relocate_paper` lacks transaction safety

- **Module**: papers.py:81-140
- **Category**: correctness (CWE-404)
- **Description**: Two UPDATE statements without explicit BEGIN/COMMIT/ROLLBACK. Partial failure leaves connection dirty.
- **Auto-fixable**: no
- **Source**: specialist:papers.py

### H-006: fitz.open() without context manager (6 sites in vision.py)

- **Module**: vision.py:437,466,661,759,839,901
- **Category**: correctness (CWE-404)
- **Description**: PDF documents opened without context manager. Lines 759-761 and 839-841 have no try/finally — any exception leaks the handle.
- **Auto-fixable**: yes — `with fitz.open(...) as doc:`
- **Source**: specialist:vision.py

### H-007: Unhandled JSONDecodeError in `_resolve_entities`

- **Module**: extraction.py:480
- **Category**: correctness
- **Description**: `json.loads(raw)` without try/except. LLM returning malformed JSON crashes entity resolution. Compare with `_map_extract` which wraps properly.
- **Auto-fixable**: yes
- **Source**: specialist:extraction.py

### H-008: URL sanitizer can leak credentials on parse failure

- **Module**: extraction.py:1061
- **Category**: security (CWE-532)
- **Description**: `_sanitize_url` catches all Exception and returns raw URL. If URL parsing fails, credentials in the URL leak into logs.
- **Auto-fixable**: yes — conservative fallback stripping
- **Source**: specialist:extraction.py

### H-009: God module — ingest.py at 1950 LOC spanning 5 concerns

- **Module**: ingest.py
- **Category**: design
- **Description**: Chunking strategies, PDF extraction, web fetching, HTML image extraction, browser config — all in one file. Root cause of duplication and coupling issues.
- **Auto-fixable**: no — HEAVY refactoring (R1-R4 in code-reviewer:ingest.py)
- **Source**: code-reviewer:ingest.py

### H-010: 250-line duplication between `ingest_file` and `reingest_file`

- **Module**: ingest.py:645-827 vs 830-1104
- **Category**: design
- **Description**: Nearly identical chunking dispatch, embedding, and insertion logic. Divergence risk is high.
- **Auto-fixable**: no — HEAVY refactoring
- **Source**: specialist:ingest.py, code-reviewer:ingest.py

### H-011: Chunk insert+dedup boilerplate repeated 5 times

- **Module**: ingest.py (5 locations)
- **Category**: design
- **Description**: The content-hash → SELECT → skip/INSERT → chunks_vec pattern appears in ingest_file(x2), reingest_file, ingest_url, \_extract_html_images, \_extract_web_figures.
- **Auto-fixable**: no — requires unified helper
- **Source**: code-reviewer:ingest.py

### H-012: `extract_figures()` is a 500-LOC god function

- **Module**: vision.py:803-1300
- **Category**: design
- **Description**: 11 interleaved pipeline stages with dict mutation, inline SQL, misnumbered step comments. Primary maintainability bottleneck.
- **Auto-fixable**: no — HEAVY refactoring
- **Source**: code-reviewer:vision.py

---

## Medium (45) — by category

### Security (8)

| ID       | Module            | Title                                                            | Auto-fix |
| -------- | ----------------- | ---------------------------------------------------------------- | -------- |
| M-SEC-01 | search.py:186     | Bare `except Exception: pass` swallows all FTS errors            | yes      |
| M-SEC-02 | search.py:145     | No input validation on top_k/mode/source_type at trust boundary  | no       |
| M-SEC-03 | vision.py:128     | Subprocess with user-configurable omniparser_path (no allowlist) | no       |
| M-SEC-04 | extraction.py:302 | LLM base_url SSRF — only scheme validated, not IP                | no       |
| M-SEC-05 | extraction.py:480 | LLM JSON responses consumed without structural validation        | no       |
| M-SEC-06 | extraction.py:390 | Indirect prompt injection via document content in LLM prompts    | no       |
| M-SEC-07 | keywords.py:97    | build_fts_query doesn't neutralize all FTS5 operators            | yes      |
| M-SEC-08 | vision.py:1160    | f-string SQL assembly — safe but fragile pattern                 | no       |

### Correctness (10)

| ID       | Module             | Title                                                             | Auto-fix |
| -------- | ------------------ | ----------------------------------------------------------------- | -------- |
| M-COR-01 | papers.py:202      | `path_conflict` referenced before assignment when source_uri None | yes      |
| M-COR-02 | papers.py:272      | add_relationship error-dict return ignored by auto_relate         | no       |
| M-COR-03 | papers.py:219      | LIKE wildcard injection in title_pattern search                   | yes      |
| M-COR-04 | extraction.py:558  | cursor.lastrowid == 0 falsy check works by accident               | yes      |
| M-COR-05 | extraction.py:162  | Unbounded IN clause in compare_papers (999 var limit)             | yes      |
| M-COR-06 | search.py:207      | chunk_strategy filter unbatched IN clause (999 var limit)         | yes      |
| M-COR-07 | search.py:119      | best_distance==0 causes ALL folders boosted                       | yes      |
| M-COR-08 | ingest.py:164      | Offset tracking drift in \_chunk_markdown                         | no       |
| M-COR-09 | server.py:584      | \_validate_bib_path return value discarded                        | yes      |
| M-COR-10 | conclusions.py:118 | supersede_conclusion — no rollback on failure                     | no       |

### Performance (8)

| ID        | Module             | Title                                                | Auto-fix |
| --------- | ------------------ | ---------------------------------------------------- | -------- |
| M-PERF-01 | papers.py:226      | get_paper N+1 queries for chunks/relationships       | no       |
| M-PERF-02 | papers.py:813      | suggest_relationships fetches ALL papers into memory | no       |
| M-PERF-03 | extraction.py:434  | O(n\*m) entity mention dedup (should use dict)       | yes      |
| M-PERF-04 | extraction.py:1183 | N+1 query in get_entities                            | yes      |
| M-PERF-05 | ingest.py:1073     | O(E\*C) entity re-linking with uncompiled regex      | yes      |
| M-PERF-06 | ingest.py:682      | N+1 content_hash lookups per chunk                   | yes      |
| M-PERF-07 | conclusions.py:81  | N+1 query for source chunk resolution                | yes      |
| M-PERF-08 | vision.py:291      | PIL Image opened but never closed in \_crop_regions  | yes      |

### Design (9)

| ID       | Module            | Title                                                                            | Auto-fix |
| -------- | ----------------- | -------------------------------------------------------------------------------- | -------- |
| M-DES-01 | extraction.py:700 | Single-pass duplicates entity storage from \_store_resolved                      | no       |
| M-DES-02 | vision.py:22      | Bidirectional coupling with ingest.py (circular imports)                         | no       |
| M-DES-03 | papers.py         | God module — 5 responsibilities (CRUD, graph, bibtex, auto-relate, cite-suggest) | no       |
| M-DES-04 | server.py         | God module — 38 tools in 1083 LOC flat dispatcher                                | no       |
| M-DES-05 | server.py:279     | status() has 9 inline SQL queries (feature envy on db.py)                        | no       |
| M-DES-06 | server.py:153     | reingest() owns relationship invalidation logic                                  | no       |
| M-DES-07 | ingest.py         | Primitive obsession — dicts everywhere instead of typed structures               | no       |
| M-DES-08 | ingest.py:1186    | Duplicated FK cleanup logic                                                      | no       |
| M-DES-09 | extraction.py:21  | record_method/record_dataset near-identical WET code                             | yes      |

### Documentation (6)

| ID       | Module                | Title                                                      | Auto-fix |
| -------- | --------------------- | ---------------------------------------------------------- | -------- |
| M-DOC-01 | ingest.py:645         | ingest_file — no docstring (primary entry point)           | yes      |
| M-DOC-02 | ingest.py:1924        | ingest_directory — no docstring                            | yes      |
| M-DOC-03 | mcp-tools.md          | keyword_prefilter param missing from search_index docs     | yes      |
| M-DOC-04 | mcp-tools.md          | max_workers param missing from extract_structure_tool docs | yes      |
| M-DOC-05 | auto-relationships.md | auto_relate_accept_threshold documented but unused in code | yes      |
| M-DOC-06 | extraction.py:94      | get_methods/get_datasets/get_metrics — no docstrings       | yes      |

### Testing (4)

| ID        | Module        | Title                                                | Auto-fix |
| --------- | ------------- | ---------------------------------------------------- | -------- |
| M-TEST-01 | server.py     | 38/41 functions with ZERO test coverage              | no       |
| M-TEST-02 | server.py:88  | \_validate_bib_path (security-critical) — zero tests | no       |
| M-TEST-03 | vision.py:183 | \_cluster_bboxes — zero tests (complex spatial algo) | no       |
| M-TEST-04 | search.py:82  | \_folder_boost — zero tests (60 lines of logic)      | no       |

---

## Refactoring Backlog

| ID      | Pattern            | Location                                  | Motivation                                         | Scope                        | Type   |
| ------- | ------------------ | ----------------------------------------- | -------------------------------------------------- | ---------------------------- | ------ |
| REF-001 | Extract Module     | ingest.py → chunking.py                   | Pure functions, no DB deps, ~380 LOC               | 1 new file, ~15 imports      | HEAVY  |
| REF-002 | Extract Module     | ingest.py → web.py                        | Web ingestion + SSRF + browser, ~840 LOC           | 1 new file, ~50 test patches | HEAVY  |
| REF-003 | Extract Method     | ingest.py → \_insert_chunks()             | Unified dedup+insert (5 call sites)                | ~60 LOC helper               | HEAVY  |
| REF-004 | Template Method    | ingest.py → \_produce_and_insert_chunks() | Shared reingest/ingest logic                       | ~100 LOC helper              | HEAVY  |
| REF-005 | Extract Module     | papers.py → bibtex.py                     | BibTeX functions, zero coupling to CRUD, ~150 LOC  | 1 new file                   | HEAVY  |
| REF-006 | Extract Module     | papers.py → auto_relate.py                | Embedding similarity, own test file, ~130 LOC      | 1 new file                   | HEAVY  |
| REF-007 | Extract Module     | extraction.py → llm.py                    | LLM config/calling/connectivity, ~180 LOC          | 1 new file                   | HEAVY  |
| REF-008 | Pipeline Decompose | vision.py extract_figures()               | 500 LOC → 6-8 pipeline functions + dataclasses     | vision.py only               | HEAVY  |
| REF-009 | Router Decompose   | server.py → sub-routers                   | 38 tools → 6 grouped modules                       | ~6 new files                 | HEAVY  |
| REF-010 | Replace Error Code | papers.py, conclusions.py                 | Error dicts → domain exceptions                    | ~4 functions + server.py     | LIGHT  |
| REF-011 | DRY                | extraction.py record_method/dataset       | Generic \_record_entity helper                     | ~20 LOC saved                | LIGHT  |
| REF-012 | Extract Method     | conclusions.py, vision.py, ingest.py      | Unified \_delete_chunks_cascade                    | db.py helper                 | LIGHT  |
| REF-013 | Named Constants    | papers.py, extraction.py, search.py       | Magic numbers → module constants                   | ~20 sites                    | LIGHT  |
| REF-014 | Add **all**        | All 9 modules                             | Define public API surface                          | 9 lines                      | LIGHT  |
| REF-015 | Shared utils       | papers.py → utils.py                      | compute_file_hash, \_content_hash, \_serialize_f32 | 1 new file                   | LIGHT  |
| REF-016 | Stopwords          | keywords.py + papers.py                   | Deduplicate + multilingual support (lingua-py)     | shared module                | MEDIUM |

---

## Cross-cutting Patterns

### Error-as-return-value

Papers, conclusions, extraction, ingest all return `{'error': ...}` dicts. Server.py json.dumps these to MCP. Recommendation: raise domain exceptions, catch at server.py boundary.

### N+1 query pattern

Found in: get_paper, get_entities, get_conclusions (source chunks), auto_relate (embeddings), ingest_file (content_hash). Total: 6 instances.

### Missing **all**

None of the 9 source modules define `__all__`. Cross-module imports reach into `_`-prefixed symbols (vision↔ingest, extraction.\_MAX_WORKERS_LIMIT in server.py).

### No conftest.py

Tests lack shared fixtures. Each test file repeats 3-line DB setup. A shared conftest would cut ~300+ lines of boilerplate.

### Missing parametrize

Zero `@pytest.mark.parametrize` in test_ingest.py (2941 lines), minimal usage elsewhere. Many functions with combinatorial inputs are tested with individual test functions.

---

## Module-level Summary

| Module                 | Findings | High | Med | Low | Key Issue                                  |
| ---------------------- | -------- | ---- | --- | --- | ------------------------------------------ |
| ingest.py              | 30       | 4    | 9   | 12  | God module + duplication                   |
| vision.py              | 22       | 3    | 7   | 8   | 500-LOC god function + resource leaks      |
| extraction.py          | 28       | 2    | 9   | 12  | Single-pass/map-reduce storage duplication |
| papers.py              | 26       | 2    | 7   | 10  | O(P\*C²) auto_relate + transaction safety  |
| server.py              | 24       | 1    | 8   | 8   | Path traversal + god module                |
| search.py              | 14       | 1    | 4   | 5   | Unbatched IN clause + folder boost bug     |
| conclusions.py         | 8        | 0    | 3   | 4   | N+1 + no rollback                          |
| keywords.py            | 5        | 0    | 1   | 3   | FTS5 operator sanitization                 |
| browser/render_page.py | 4        | 0    | 1   | 2   | Zero test coverage                         |
