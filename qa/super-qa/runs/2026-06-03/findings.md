# Super QA Findings — knowledge-base

**Generated:** 2026-06-03-005835  |  **Run:** full 5-lens dispatch (30 agents) + static tools + multi-model debate

**Scope:** 6 units — web.py + browser/render_page.py, vision.py, ingest.py, extraction.py, db.py, search.py

**Workflow:** runId wf_eafd057e-df0 (30/30 lens-runs, 1.83M subagent tokens)


> NOTE: A prior super-qa ran ~3 months ago (open issues #181–#233). Findings here may overlap; reconciliation/relinking is deferred to post-triage per user. Likely overlaps are tagged inline.


## Executive Summary

- **0 critical, 20 high** (LLM) — high tier dominated by design/refactoring, test gaps, performance (N+1/quadratic).
- **Security lens conservative:** 0 high; flagship is a **known/tracked blind-SSRF** (redirect-following) rated MEDIUM. The 18 bandit SQL hits were adjudicated by the security agents as *necessary identifier interpolation with parameterized values* (LOW/INFO, defense-in-depth).
- **Net-new correctness (debated):** search.py rerank/source_type interaction — silent result truncation on filtered searches (HIGH).
- **Net-new supply-chain:** 6 dependencies with known CVEs (requests/urllib3 on the SSRF fetch path); fixes available.
- **Build hygiene:** no `[tool.ruff]`/`[tool.mypy]`/`[tool.pyright]` config — Phase-1 'clean lint' was against ruff's *default minimal* ruleset; no enforced type checking. 20 mypy errors exist (extraction.py None-guard bugs).

## Summary (LLM findings)

| Severity | Count | Auto-fixable | Report-only |
| --- | --- | --- | --- |
| Critical | 0 | 0 | 0 |
| High | 19 | 0 | 19 |
| Medium | 50 | 9 | 41 |
| Low | 72 | 18 | 54 |
| Info | 20 | 7 | 13 |
| **Total** | **161** | **34** | **127** |

By category: testing=43, design=23, documentation=22, style=21, performance=17, refactoring=16, security=10, correctness=9

## Debated Findings (multi-model verified)

| ID | Location | Orig | Final | Verdict | Notes |
| --- | --- | --- | --- | --- | --- |
| E2-search/scale-mixing | search.py:402-422 | high | **high** | Confirmed | Gemini HIGH / Codex MEDIUM → Opus HIGH; filtered-search truncation |
| E2-search/source_type-underfetch | search.py:430-450 | high | **medium** | Downgraded | Both models MEDIUM; overfetch softens |

**Unified fix (both):** pre-filter `source_type` into the candidate pool at search.py:326-342 exactly as `chunk_strategy` is; and after a successful rerank, drop un-reranked entries from `score_map` before the final sort (or sink them) so reranker[0,1] and RRF(~0.016) scales never co-sort.


## Refactoring Backlog (16 items)

| ID | Location | Severity | Auto-fix? | Title |
| --- | --- | --- | --- | --- |
| web/refactoring-god-functions | web.py:329-543 | high | HEAVY | _extract_html_images is a ~215-LOC function mixing 5 responsibilities |
| web/refactoring-figure-extraction-dup | web.py:494-543,829-881,969-1012 | high | HEAVY | Three figure-extraction functions duplicate the embed/delete-stale/insert-dedup  |
| web/refactoring-ingest-url-orchestration | web.py:1058-1260 | high | HEAVY | ingest_url is a ~200-LOC procedure conflating fetch, browser fallback, figure or |
| extraction/refactoring-store-resolved-split | extraction.py:558-738 | high | HEAVY | _store_resolved is a ~180-LOC multi-responsibility function |
| extraction/refactoring-write-path-duplication | extraction.py:755-871 | high | HEAVY | Single-pass path re-implements the entity/mention/metric write logic |
| vision/refactoring-figure-dataclass | vision.py:1797-1832 | medium | HEAVY | Primitive obsession: figure represented as a dict with underscore-prefixed senti |
| extraction/refactoring-map-reduce-split | extraction.py:874-1024 | medium | HEAVY | _extract_map_reduce mixes orchestration with logging/ETA bookkeeping |
| extraction/refactoring-collect-mentions-quadratic | extraction.py:473-504 | medium | LIGHT | _collect_entity_mentions does an O(n^2) linear rescan on duplicate keys |
| db/refactoring-init-schema-god-fn | db.py:595-896 | medium | HEAVY | init_schema is a ~300 LOC god function mixing DDL, seeding, and migration orches |
| search/refactoring-god-function | search.py:202-473 | medium | HEAVY | search() is a ~270-line god function mixing 8 distinct phases |
| vision/refactoring-dead-figure-prompt-alias | vision.py:961-963 | low | HEAVY | _FIGURE_VISION_PROMPT module constant is unused in production (only pinned by a  |
| ingest/refactoring-magic-numbers-imagedir | ingest.py:208-222 | low | LIGHT | Magic numbers in pdf_image_dir hashing/truncation lack named constants |
| ingest/refactoring-cubic-entity-relink | ingest.py:610-627 | low | LIGHT | Entity re-link is a triple-nested loop recompiling regex per chunk |
| extraction/refactoring-prompt-duplication | extraction.py:405-437 | low | HEAVY | _EXTRACT_PROMPT and _MAP_PROMPT are near-identical templates |
| db/refactoring-migration-boilerplate | db.py:165-363 | low | HEAVY | Eleven _migrate_* functions duplicate the same 'introspect-then-alter/rebuild' p |
| db/refactoring-table-name-fallback-triplication | db.py:421-493 | info | HEAVY | Active-space table_name fallback (`tbl = table_name or get_vec_table_name(conn)` |

## Findings by Module


### A-web — web.py + browser/render_page.py (34 findings)


#### High (5)

| ID | Lens | Category | Location | Title | Auto-fix |
| --- | --- | --- | --- | --- | --- |
| web/refactoring-god-functions | design | refactoring | web.py:329-543 | _extract_html_images is a ~215-LOC function mixing 5 responsibilities | — |
| web/refactoring-figure-extraction-dup | design | refactoring | web.py:494-543,829-881,969-1012 | Three figure-extraction functions duplicate the embed/delete-stale/insert-dedup tail | — |
| web/refactoring-ingest-url-orchestration | design | refactoring | web.py:1058-1260 | ingest_url is a ~200-LOC procedure conflating fetch, browser fallback, figure orchestratio | — |
| render-page/testing-zero-coverage | test | testing | browser/render_page.py:24-120 | browser/render_page.py has zero test coverage | — |
| web/testing-postredirect-ssrf-untested | test | testing | web.py:1090-1095 | ingest_url post-redirect SSRF rejection has no test | — |

- **web/refactoring-god-functions** (refactoring, web.py:329-543)
  - Current state: `_extract_html_images` (lines 329-543, ~215 body LOC) parses candidates, merges extra HTML sources with dedup, downloads+size-guards+SSRF-rechecks each image, decodes via Pillow, calls vision per image, embeds, deletes stale chunks, and inserts new chunks with hash dedup — all in one function body. It far exceeds the 50-LOC threshold and operates at four different abstraction levels (byte streaming, image decoding, DB cleanup, embedding).
Target state: Extract cohesive helpers: `_download_image_bytes(url) -> bytes | None` (the httpx.stream + byte-cap + post-redirect SSRF block, lines 409-447), `_decode_image_to_png_b64(bytes) -> str | None` (lines 450-461), `_describe_image(b64, alt) -> list[(desc, meta)]` (lines 463-489), and a shared `_swap_figure_chunks(conn, source_url, collected, index_start, index_end)` that owns the delete-stale + embed + insert-with-dedup tail (lines 494-543). The top-level function then orchestrates at one altitude.
Pattern name: Extract Method / Compose Method.
Motivation: Testability (each fallible stage independently mockable), readability, and it exposes the duplication shared with `_extract_web_figures`/`_extract_element_captures`.
Estimated scope: 1 file, ~6 new private helpers, plus test updates.
Prerequisites: none.
Risk: Medium — the embed-before-delete ordering (lines 494-516) is a deliberate crash-safety invariant; the extraction must preserve 'delete stale only after embeddings succeed' or it silently corrupts idempotency.
  - **Fix:** Decompose into _download_image_bytes / _decode_image_to_png_b64 / _describe_image / _swap_figure_chunks helpers; keep the embed-then-delete ordering intact in the shared swap helper.
  - refs: https://refactoring.com/catalog/extractFunction.html

- **web/refactoring-figure-extraction-dup** (refactoring, web.py:494-543,829-881,969-1012)
  - Current state: `_extract_html_images` (494-543), `_extract_web_figures` (829-881), and `_extract_element_captures` (969-1012) each repeat the identical pipeline: build `texts` list, call `_embed_with_config`, query stale `chunk_index` range for `source_type='figure'`, call `_cleanup_figure_fk_refs` + delete (via `delete_chunks_cascade` in one, `delete_chunk_vecs`+`_batched_execute` in the other two — an inconsistency, see separate finding), then loop `zip(collected, embeddings)` computing `_content_hash`, skip-if-exists, `_insert_chunk`, increment counter, `conn.commit()` if changed. Each also opens with `import base64` + `from .vision import _get_vision_config` + reads `base_url`/`model`.
Target state: A single `_swap_figure_chunks(conn, source_url, collected, *, index_start, index_end=None)` that takes `collected: list[tuple[str, dict, int]]` (desc, meta, index-offset) and performs embed → scoped-delete → hash-dedup insert → commit. The three callers reduce to per-source collection logic only.
Pattern name: Form Template Method / Extract Superclass (here: extract shared procedure).
Motivation: ~120 lines of near-identical logic; the chunk-index-range scoping is subtle and currently copy-pasted, so a fix to one (e.g. the delete-API inconsistency) must be remembered in three places.
Estimated scope: 1 file, 1 new helper, 3 call-site rewrites, test consolidation.
Prerequisites: reconcile the delete-API inconsistency (delete_chunks_cascade vs delete_chunk_vecs+manual DELETE) first.
Risk: Medium — the index ranges differ per caller and encode an ordering contract between the three figure sources; must be parameterized exactly.
  - **Fix:** Extract a parameterized _swap_figure_chunks(conn, source_url, collected, index_start, index_end) and route all three figure extractors through it.
  - refs: https://refactoring.com/catalog/extractFunction.html

- **web/refactoring-ingest-url-orchestration** (refactoring, web.py:1058-1260)
  - Current state: `ingest_url` (1058-1260, ~200 body LOC) does URL validation+SSRF, httpx fetch, post-redirect SSRF, trafilatura extract, the entire browser-fallback block (1110-1176: render, re-extract, screenshot figures, element captures, tmpdir cleanup in a nested try/finally), inline-image extraction, then text-chunk dedup+embed+insert. The browser-fallback block alone is ~67 lines nested 4 levels deep.
Target state: Extract `_fetch_and_validate(url) -> (html, response)`, `_maybe_browser_fallback(conn, url, response, text, extracted_title) -> BrowserOutcome` (returns figures_extracted, browser_rendered, rendered_html, rendered_base_url, possibly-improved text/title), and `_ingest_text_chunks(conn, text, url, title, session_id) -> dict`. `ingest_url` becomes a ~30-line orchestrator.
Pattern name: Extract Method / Compose Method; introduce a small result dataclass for the browser outcome.
Motivation: The nested try/finally with tmpdir lifecycle (1173-1176) and the 'unconditional rendered_html capture even when text not better' subtlety (1132-1143) are easy to break; isolating them makes the invariant explicit and testable.
Estimated scope: 1 file, 3 helpers + 1 dataclass, test updates.
Prerequisites: dataclass for browser outcome (see primitive-obsession finding).
Risk: Medium — the control flow that mutates `text`/`extracted_title`/`browser_rendered` conditionally must be preserved exactly.
  - **Fix:** Split ingest_url into _fetch_and_validate, _maybe_browser_fallback (returning a BrowserOutcome dataclass), and _ingest_text_chunks; reduce ingest_url to orchestration.
  - refs: https://refactoring.com/catalog/extractFunction.html

- **render-page/testing-zero-coverage** (testing, browser/render_page.py:24-120)
  - The entire render_page.py module is untested. `_capture_elements` (lines 24-62) and `main` (lines 65-116) have no test imports or calls anywhere in tests/. The only `test_render_page_*` tests (tests/test_vision.py:364-379) target `knowledge_base.vision._render_page` (PDF rasterization) — an unrelated function with a colliding name, NOT this browser module. Untested logic includes: the canvas/svg element selection, the `_MIN_ELEMENT_DIMENSION`/`_MAX_ELEMENT_CAPTURES` (80px / 10-cap) filters, the viewport-bounds rejection (`box['x']+w <= 0`), the bounding_box/screenshot try/except continue paths, the manifest write-only-if-nonempty branch (line 61), and `main`'s non-http(s) scheme rejection + sys.exit(1) (lines 76-78). Playwright is the hard dependency, but `_capture_elements` takes a `page` object and can be driven with a Mock exposing `query_selector_all`, and `main`'s scheme guard runs before the playwright import (line 80) so it is unit-testable without playwright.
  - **Fix:** Add tests/test_render_page.py. (1) `_capture_elements`: feed a fake `page` whose `query_selector_all` returns Mock elements with controllable `bounding_box()`, `evaluate()`, and `screenshot()`; assert filtering by min dimension, the 10-element cap, viewport-bounds rejection, the bounding_box-raises-skip path, and that elements.json is only written when at least one capture survives. (2) `main` scheme guard: invoke with `sys.argv` set to a `ftp://` URL via monkeypatch and assert `SystemExit(1)` is raised before the playwright import — this needs no browser.

- **web/testing-postredirect-ssrf-untested** (testing, web.py:1090-1095)
  - ingest_url's post-redirect SSRF guard (lines 1091-1095: raise ValidationError 'URL redirected to a private/internal address') is never asserted. grep across tests/ finds no test that drives `ingest_url` with a `response.url` whose hostname is a private IP. The existing redirect-to-private test (tests/test_ingest.py:2300 `test_extract_html_images_rejects_redirect_to_private`) exercises the image-download path in `_extract_html_images`, not the page-fetch guard in `ingest_url`. This is a security-relevant branch (SSRF defense-in-depth) left uncovered.
  - **Fix:** Add a test that patches `knowledge_base.web.httpx.get` to return a response whose `.url` is e.g. `http://169.254.169.254/` (or `http://127.0.0.1/`) while the requested URL is public, and assert `pytest.raises(ValidationError, match='redirected to a private')`. Patch `is_private_ip` if needed to make the final-host check deterministic.

#### Medium (10)

| ID | Lens | Category | Location | Title | Auto-fix |
| --- | --- | --- | --- | --- | --- |
| web/design-inconsistent-delete-api | design | design | web.py:513-515,841-844,982-985 | Stale-chunk deletion uses two different APIs across the three figure extractors | — |
| web/design-primitive-obsession-dicts | design | design | web.py:560-582,654-738 | Untyped dict returns used as informal structs where dataclasses belong | — |
| web/design-resource-ownership-tmpdir | design | design | web.py:654-738,1173-1176 | tmpdir lifecycle leaks across the function boundary instead of using a context manager | — |
| web/design-circular-import-local-imports | design | design | web.py:357-362,761-770,902-904 | Function-local imports of .vision in hot paths mask a circular dependency | — |
| web/documentation-ingest-url-no-params | docs | documentation | web.py:1058-1067 | Public ingest_url docstring documents no params, return, or raises | — |
| web/documentation-configure-browser-args | docs | documentation | web.py:585-597 | configure_browser Args block omits conn, has a malformed entry, and lacks Raises | — |
| render-browser/documentation-stale-return-keys | docs | documentation | web.py:659-664 | _render_with_browser return docstring is stale (missing final_url and element_captures) | — |
| web/security-ssrf-redirect-chain | security | security | web.py:1080-1095 | SSRF: redirect-following reaches internal hosts before post-redirect validation | — |
| web/performance-n+1-content-hash | specialist | performance | web.py:1218-1228 | N+1 per-chunk content_hash SELECT in ingest_url dedup loop | — |
| web/testing-malformed-elements-json | test | testing | web.py:715-731 | _render_with_browser malformed elements.json branch untested | — |

#### Low (11)

| ID | Lens | Category | Location | Title | Auto-fix |
| --- | --- | --- | --- | --- | --- |
| web/style-string-constants-enum | design | style | web.py:481-487,858-863,954-963 | figure_type / source_type / original_source_type are bare string literals, not an Enum | — |
| render/style-magic-numbers | design | style | browser/render_page.py:94-108 | Browser timeouts and screenshot clip dimensions are unnamed magic numbers | ✅ |
| render/style-untyped-page-param | design | style | browser/render_page.py:24 | _capture_elements page param is untyped with a blanket no-untyped-def suppression | ✅ |
| web/documentation-browser-config-keys | docs | documentation | web.py:560-564 | _get_browser_config docstring overstates the returned dict keys | — |
| web/security-dns-rebinding-toctou | security | security | utils.py:33-55 | SSRF: DNS-rebinding TOCTOU between is_private_ip check and httpx fetch | — |
| web/security-path-traversal-elements-json | security | security | web.py:714-728 | Path traversal: subprocess elements.json filename joined to tmpdir without containment che | — |
| web/performance-n+1-figure-dedup | specialist | performance | web.py:521-523,850-852,990-992 | Repeated per-item content_hash SELECT in figure-insert loops | — |
| render/correctness-viewport-filter | specialist | correctness | browser/render_page.py:41-43 | Off-screen element filter only catches top-left overflow, not right/bottom | — |
| web/correctness-none-embedding-figure | specialist | correctness | web.py:519-538,848-876,988-1007 | Zero-norm (None) embeddings silently inserted as vector-less figure chunks | — |
| web/testing-find-venv-python-direct | test | testing | web.py:551-557 | _find_venv_python has no direct test; Windows branch uncovered | — |
| web/testing-cleanup-figure-fk-refs | test | testing | web.py:220-253 | _cleanup_figure_fk_refs FK-nulling not directly asserted | — |

#### Info (8)

| ID | Lens | Category | Location | Title | Auto-fix |
| --- | --- | --- | --- | --- | --- |
| web/style-noop-else-pass | design | style | web.py:437-439 | for/else with a no-op `pass` body adds noise | ✅ |
| render/style-broad-except | design | style | browser/render_page.py:33-34,48-50 | Bare `except Exception: continue` swallows all errors during element capture | ✅ |
| render-page/documentation-ephemeral-context-claim | docs | documentation | browser/render_page.py:1-10 | Module docstring singles out CDP for ephemeral context, but both modes use it | — |
| web/security-json-type-confusion | security | security | web.py:716-728 | Type confusion: elements.json fields consumed without isinstance validation | — |
| render-page/security-browser-ssrf-tradeoff | security | security | browser/render_page.py:1-10 | SSRF via in-browser page JavaScript (documented accepted trade-off) | — |
| web/performance-repeated-strip | specialist | performance | web.py:1110-1128 | text.strip() recomputed multiple times in browser-fallback comparison | — |
| web/testing-no-class-grouping-fixtures | test | testing | tests/test_ingest.py:1966-2227 | Repeated @patch stacks and inline HTML duplicated across image tests; no shared fixtures | — |
| web/testing-parse-srcset-no-parametrize | test | testing | tests/test_ingest.py:1763-1819 | _parse_srcset cases are 10 separate functions instead of one parametrized table | — |

### B-vision — vision.py (30 findings)


#### High (2)

| ID | Lens | Category | Location | Title | Auto-fix |
| --- | --- | --- | --- | --- | --- |
| vision/design-god-module | design | design | vision.py:1-2279 | God module: 2279 LOC mixing config, server lifecycle, clustering, captions, and orchestrat | — |
| vision/testing-real-fs-write | test | testing | vision.py:2060-2082 | _save_rendered_pngs writes to real ~/.local/share, never isolated in tests | — |

- **vision/design-god-module** (design, vision.py:1-2279)
  - Current state: vision.py is 2279 lines (well past the project's own 1000+ god-module threshold) holding at least six distinct concerns: (a) config read/write (_get_vision_config, configure_vision, _get_omniparser_config, _validate_omniparser_path, configure_omniparser), (b) OmniParser server lifecycle + HTTP (_check_omniparser_server, _run_omniparser_http, _ensure_omniparser_server, _shutdown_omniparser_server, _run_omniparser), (c) geometric clustering/cropping (_cluster_bboxes, _split_cluster_x, _crop_regions, _cluster_drawing_rects, _detect_mixed_page_vector_regions, _elements_in_region), (d) caption extraction (CaptionMap, _extract_captions), (e) prompt building, (f) the multi-stage extraction pipeline (extract_figures and its _resolve/_collect/_render/_dispatch/_enrich/_persist stages). Target state: a vision/ package split into config.py, omniparser_server.py, clustering.py, captions.py, prompts.py, and pipeline.py, with vision/__init__.py re-exporting the four public names. Pattern: Package-by-feature / module extraction. Motivation: a single file of this size is hard to navigate, has high merge-conflict surface, and forces unrelated changes to touch the same file. Estimated scope: HEAVY (~6 new modules, import rewiring across server routes and tests). Prerequisites: stable public API (only 4 names in __all__) makes the boundary clean. Risk: medium — internal imports and test imports (tests reach into private helpers, e.g. _build_figure_vision_prompt) must be repointed; circular-import care around .papers/.ingest/.web.
  - **Fix:** Split vision.py into a vision/ package by concern (config, omniparser_server, clustering, captions, prompts, pipeline); keep the 4 public functions re-exported from vision/__init__.py.
  - refs: CLAUDE.md: 'god modules (1000+ lines)'

- **vision/testing-real-fs-write** (testing, vision.py:2060-2082)
  - _save_rendered_pngs() unconditionally builds its output dir from Path.home() / '.local/share/knowledge-base/figures/<paper_id>' and writes PNG bytes there. It is called at the tail of extract_figures() (vision.py:2245) and is NEVER patched, and Path.home()/$HOME is never redirected via monkeypatch in tests/test_vision.py (grep for '_save_rendered_pngs|home|HOME|figures' returns no isolation). Any end-to-end test that exercises the vector-page render path (e.g. test_extract_figures_falls_back_to_render_for_vector_pages, test_extract_figures_processes_mixed_page_vector_regions) writes real files into the developer's/CI's actual home directory, outside tmp_path. Consequences: cross-run pollution keyed by paper_id, CI artifact leakage, and a latent test failure if $HOME is read-only. This is a test-isolation defect, not merely a coverage gap. Fix requires making the figures base dir injectable (parameter or env lookup) in production code so tests can point it at tmp_path; mark as not auto-fixable because it touches the production signature plus the call site plus existing tests.
  - **Fix:** Refactor _save_rendered_pngs to accept an optional base_dir (or read a KB data-dir resolved once, defaulting to Path.home()/...), then in tests pass tmp_path (or monkeypatch the data-dir resolver). Add an assertion-bearing test: run extract_figures over a vector-only PDF with the figures dir redirected to tmp_path, assert page_<n>.png and page_<n>_vector_<i>.png are written there and that nothing is created under the real home. Also add an OSError-on-mkdir test to cover the except OSError warning branch at vision.py:2081.
  - refs: tests/test_vision.py, src/knowledge_base/vision.py:2244-2245

#### Medium (10)

| ID | Lens | Category | Location | Title | Auto-fix |
| --- | --- | --- | --- | --- | --- |
| vision/design-module-singleton | design | design | vision.py:340-462 | Module-level mutable singleton for the OmniParser server process | — |
| vision/refactoring-figure-dataclass | design | refactoring | vision.py:1797-1832 | Primitive obsession: figure represented as a dict with underscore-prefixed sentinel keys | — |
| vision/design-long-dispatch-vision | design | design | vision.py:1698-1845 | _dispatch_vision_calls is ~148 LOC spanning task-build, result-collect, and region-merge | — |
| vision/design-long-persist-figures | design | design | vision.py:1899-2057 | _persist_figures is ~159 LOC mixing content assembly, scoped-DELETE SQL, and chunk inserti | — |
| vision/style-broad-except-exception | design | style | vision.py:302-303 | except clause lists specific exceptions then a blanket Exception, swallowing all errors si | ✅ |
| vision/documentation-stale-error-return | docs | documentation | vision.py:2090-2135 | estimate_figures_time docstring claims an {"error": ...} return that the function never pr | — |
| vision/correctness-broad-except-in-tuple | specialist | correctness | vision.py:302-303 | Exception in except-tuple makes httpx types redundant and swallows all errors | ✅ |
| vision/testing-geometry-helpers-untested | test | testing | vision.py:555-711 | Pure clustering/crop helpers have zero direct tests (_cluster_bboxes, _split_cluster_x, _c | ✅ |
| vision/testing-omniparser-global-leak | test | testing | tests/test_vision.py:3805-3844 | test_auto_start_success leaks module-global _omniparser_process (MagicMock) with no teardo | ✅ |
| vision/testing-configure-vision-scheme | test | testing | vision.py:133-144 | configure_vision invalid-URL-scheme rejection path is untested | ✅ |

#### Low (15)

| ID | Lens | Category | Location | Title | Auto-fix |
| --- | --- | --- | --- | --- | --- |
| vision/design-long-ensure-server | design | design | vision.py:363-462 | _ensure_omniparser_server is ~100 LOC combining health-check, spawn, and readiness polling | — |
| vision/design-long-extract-figures | design | design | vision.py:2138-2278 | extract_figures orchestrator is ~141 LOC | — |
| vision/style-threading-import | design | style | vision.py:342 | _omniparser_lock uses __import__("threading").Lock() instead of a top-level import | ✅ |
| vision/style-magic-eta-threshold | design | style | vision.py:1556 | Magic numbers: 120s ETA-confirmation threshold, 120s startup deadline, 10s shutdown wait | ✅ |
| vision/style-figure-type-stringly | design | style | vision.py:777-800 | figure_type is a free string with an enumerated domain (diagram|chart|table|photo|equation | — |
| vision/style-local-imports | design | style | vision.py:134 | Function-local imports for urllib, PIL.Image, and atexit | ✅ |
| vision/refactoring-dead-figure-prompt-alias | design | refactoring | vision.py:961-963 | _FIGURE_VISION_PROMPT module constant is unused in production (only pinned by a test) | — |
| vision/documentation-missing-public-api-docs | docs | documentation | vision.py:2138-2153 | extract_figures (public API) docstring omits Args, Returns, and Raises | — |
| vision/documentation-configure-vision-terse | docs | documentation | vision.py:122-146 | configure_vision (public API) has a one-line docstring missing Args/Returns and the query- | — |
| vision/ssrf-omniparser-url-no-scheme-validation | security | security | vision.py:232-243 | OmniParser server_url stored without scheme/host validation, later used in httpx requests | — |
| vision/path-traversal-extracted-image-name | security | security | vision.py:1285-1294 | Image basename from chunk metadata joined to image_dir without path normalization | — |
| vision/correctness-unvalidated-entities-type | specialist | correctness | vision.py:777-800 | _validate_figure passes through entities_mentioned and title without type checking | ✅ |
| vision/performance-quadratic-caption-dedup | specialist | performance | vision.py:1396-1400 | Quadratic caption dedup via list membership inside per-page loop | — |
| vision/performance-serial-omniparser-calls | specialist | performance | vision.py:1652-1690 | OmniParser pipeline issues blocking subprocess/HTTP calls strictly serially | — |
| vision/testing-malformed-metadata | test | testing | vision.py:1278-1281 | Malformed-metadata JSON-decode fallback paths untested in _collect_extracted_images and _e | ✅ |

#### Info (3)

| ID | Lens | Category | Location | Title | Auto-fix |
| --- | --- | --- | --- | --- | --- |
| vision/style-frozen-dataclass-mutated | design | style | vision.py:64-87 | DualPathInputs/OmniParserResults are mutable dataclasses though used as immutable value ca | — |
| vision/performance-list-materialize-dict-unwrap | specialist | performance | vision.py:1017-1024 | Dict-unwrap materializes all values to inspect a single-key case | — |
| vision/performance-repeated-pageresults-lookup | specialist | performance | vision.py:1863-1869 | Repeated page_results[page_num] subscript lookups in enrichment loop | ✅ |

### C-ingest — ingest.py (26 findings)


#### High (2)

| ID | Lens | Category | Location | Title | Auto-fix |
| --- | --- | --- | --- | --- | --- |
| ingest/performance-n1-dedup-query | specialist | performance | ingest.py:360-373 | N+1 query in deduplication loop — one SELECT per chunk | — |
| ingest/testing-matryoshka-embed-path | test | testing | ingest.py:90-108 | _embed_with_config matryoshka base_dim branch never exercised | — |

- **ingest/performance-n1-dedup-query** (performance, ingest.py:360-373)
  - In _produce_and_insert_chunks, deduplication issues a separate `SELECT id FROM chunks WHERE content_hash = ?` for every produced chunk (line 364-366), inside a per-item Python loop. A large PDF can produce hundreds of chunks, yielding hundreds of sequential SQLite round-trips on the hot ingest path. Because `content_hash` carries a `UNIQUE` constraint (verified in db.py:189: `content_hash TEXT NOT NULL UNIQUE`), each hash maps to at most one row, so the whole set can be resolved in a single batched query: collect all `item[2]` hashes, run one `SELECT id, content_hash FROM chunks WHERE content_hash IN ({ph})` via the existing `_batched_select` helper, build a {hash: id} dict, then partition `items` in memory. This collapses N queries into ceil(N/999) and removes per-chunk latency.
  - **Fix:** Replace the per-item loop with a single batched lookup: `existing_rows = _batched_select(conn, 'SELECT id, content_hash FROM chunks WHERE content_hash IN ({ph})', [it[2] for it in items])`, build `hash_to_id = {r['content_hash']: r['id'] for r in existing_rows}`, then iterate items once: if `it[2] in hash_to_id`, append id to deferred_session_links (when session_id is not None) and bump skipped; else append to new_items.
  - refs: src/knowledge_base/db.py:91-114 (_batched_select), src/knowledge_base/db.py:189 (content_hash UNIQUE constraint)

- **ingest/testing-matryoshka-embed-path** (testing, ingest.py:90-108)
  - _embed_with_config has two branches gated on get_active_space(conn) returning a space with a 'matryoshka_base_dim' (lines 92-102). When base_dim is set, it embeds at base_dim then truncates every vector to cfg['dim'] via truncate_embedding, mapping None passthrough for zero-norm vectors. No test in tests/test_ingest.py creates an active space with matryoshka_base_dim, so this branch and the truncate-on-each-vector logic are completely uncovered in the ingest pipeline. truncate_embedding is unit-tested in isolation (tests/test_embeddings.py:280) but never integrated here. The only test that references _embed_with_config (test_embed_failure_does_not_leave_orphan_session_rows) patches it with a RuntimeError side_effect, so the real function body is never run with a configured space. A bug in the base_dim/truncate path (e.g. truncating to the wrong dim, or mishandling the None entry inside the list comprehension at line 101) would ship silently.
  - **Fix:** Add a test that sets an active embedding space with matryoshka_base_dim > cfg['dim'], patches knowledge_base.ingest.embed to return base_dim-length vectors (and one None), then calls _embed_with_config directly and asserts: (a) embed() was invoked with expected_dim == base_dim, (b) returned non-None vectors have len == cfg['dim'], (c) the None entry is preserved as None. Add a companion test for the non-matryoshka branch asserting expected_dim == cfg['dim'] and no truncation.
  - refs: src/knowledge_base/ingest.py:90-108, tests/test_ingest.py:3633-3637, tests/test_embeddings.py:280-284

#### Medium (11)

| ID | Lens | Category | Location | Title | Auto-fix |
| --- | --- | --- | --- | --- | --- |
| ingest/design-long-produce-insert | design | design | ingest.py:269-399 | _produce_and_insert_chunks is a ~130 LOC multi-responsibility function | — |
| ingest/design-long-reingest | design | design | ingest.py:494-646 | reingest_file is a ~150 LOC orchestrator mixing FK-cleanup, session preservation, re-link | — |
| ingest/design-chunk-tuple-obsession | design | design | ingest.py:311-356 | Chunk items modeled as positional tuple[int,str,str,str] (primitive obsession) | — |
| ingest/documentation-missing-ingest-file-docstring | docs | documentation | ingest.py:402-450 | Public ingest_file() has no docstring | — |
| ingest/documentation-missing-ingest-directory-docstring | docs | documentation | ingest.py:649-675 | Public ingest_directory() has no docstring | — |
| ingest/performance-quadratic-entity-relink | specialist | performance | ingest.py:610-627 | Quadratic entity re-link loop with per-iteration regex compilation | ✅ |
| ingest/correctness-zip-no-strict | specialist | correctness | ingest.py:381-397 | zip(items, embeddings) silently truncates on length mismatch | ✅ |
| ingest/testing-detect-source-type | test | testing | ingest.py:60-80 | _detect_source_type extension mapping is untested | — |
| ingest/testing-pdf-runtime-fallback | test | testing | ingest.py:260-262 | _extract_pdf_markdown RuntimeError/OSError fallback untested | — |
| ingest/testing-semantic-pdf-chunking | test | testing | ingest.py:289-336 | Semantic PDF chunking branch (_chunk_by_section) never exercised end-to-end | — |
| ingest/testing-directory-extension-filter | test | testing | ingest.py:649-675 | ingest_directory extension filter, custom extensions, and nesting untested | — |

#### Low (11)

| ID | Lens | Category | Location | Title | Auto-fix |
| --- | --- | --- | --- | --- | --- |
| ingest/design-stringly-typed-source-strategy | design | design | ingest.py:60-80 | source_type and chunk_strategy are stringly-typed (enum candidates) | — |
| ingest/refactoring-magic-numbers-imagedir | design | refactoring | ingest.py:208-222 | Magic numbers in pdf_image_dir hashing/truncation lack named constants | ✅ |
| ingest/refactoring-cubic-entity-relink | design | refactoring | ingest.py:610-627 | Entity re-link is a triple-nested loop recompiling regex per chunk | ✅ |
| ingest/design-silent-source-type-default | design | design | ingest.py:60-80 | _detect_source_type silently defaults unknown extensions to 'markdown' | — |
| ingest/documentation-undocumented-notfounderror | docs | documentation | ingest.py:494-513 | reingest_file() docstring omits NotFoundError it raises | — |
| ingest/documentation-missing-helper-docstring | docs | documentation | ingest.py:265-266 | _extract_markdown_text() has no docstring | ✅ |
| ingest/correctness-chunk-index-gaps | specialist | correctness | ingest.py:358-397 | chunk_index carries pre-dedup positions, leaving gaps after filtering | — |
| ingest/testing-pdf-imagedir-cleanup | test | testing | ingest.py:295-298 | PDF image-dir pre-clean (rmtree) branch on re-ingest is untested | — |
| ingest/testing-empty-pdf-chunks-earlyreturn | test | testing | ingest.py:335-336 | Empty-chunk early-returns for PDF and fixed-size text are partially untested | — |
| ingest/testing-duplicate-paper-oserror | test | testing | ingest.py:432-446 | duplicate_of_paper_id OSError-on-hash and no-matching-paper branches untested | — |
| ingest/testing-no-conftest-fixtures | test | testing | tests/test_ingest.py:75-78 | Repeated DB setup boilerplate; no shared conn fixture (no conftest.py) | — |

#### Info (2)

| ID | Lens | Category | Location | Title | Auto-fix |
| --- | --- | --- | --- | --- | --- |
| ingest/style-logger-defined-after-use | design | style | ingest.py:38-48 | _update_folder_summary_safe references module logger defined 10 lines later | ✅ |
| ingest/documentation-docstring-style-divergence | docs | documentation | ingest.py:1-676 | Module uses freeform-prose docstrings, no structured sections | — |

### D-extraction — extraction.py (27 findings)


#### High (4)

| ID | Lens | Category | Location | Title | Auto-fix |
| --- | --- | --- | --- | --- | --- |
| extraction/refactoring-store-resolved-split | design | refactoring | extraction.py:558-738 | _store_resolved is a ~180-LOC multi-responsibility function | — |
| extraction/refactoring-write-path-duplication | design | refactoring | extraction.py:755-871 | Single-pass path re-implements the entity/mention/metric write logic | — |
| extraction/performance-collect-mentions-quadratic | specialist | performance | extraction.py:473-504 | Quadratic duplicate handling in _collect_entity_mentions | — |
| extraction/performance-get-entities-n-plus-1 | specialist | performance | extraction.py:1088-1118 | N+1 query pattern in get_entities | — |

- **extraction/refactoring-store-resolved-split** (refactoring, extraction.py:558-738)
  - Current state: `_store_resolved` (lines 558-738, ~180 LOC inside one try/except) performs at least five distinct jobs: (1) clear prior extraction, (2) build surface->canonical lookup from resolution groups (568-579), (3) aggregate per-canonical entity_data via a defaultdict-of-dict (581-612), (4) insert into entities + entity_mentions (614-638), (5) write methods/datasets tables with a member-alias fan-out (640-690), (6) write metrics with canonical re-resolution (692-727). Abstraction levels are mixed (raw SQL strings interleaved with dict bookkeeping). Target state: extract `_build_canonical_lookup(resolution) -> dict[tuple[str,str], str]`, `_aggregate_entities(map_results, lookup) -> dict[...]`, `_insert_entities_and_mentions(conn, entity_data, paper_id) -> dict`, `_write_metrics(conn, map_results, lookup, method_map, dataset_map, paper_id) -> int`, leaving `_store_resolved` as a ~25-LOC orchestrator wrapped in the existing transaction. Pattern: Extract Method / Compose Method. Motivation: each helper becomes unit-testable in isolation; the transaction boundary stays in one place. Estimated scope: single module, ~5 new private helpers, plus test additions. Prerequisites: none. Risk: medium — control flow and the metric attribution fan-out (685-690) must be preserved exactly; cover with characterization tests on entity_id_map / method_map ordering before refactoring.
  - **Fix:** Decompose into the named private helpers above; keep `conn.commit()/rollback()` boundary in the orchestrator. Add characterization tests asserting current methods_added/datasets_added/metrics_added/entities_resolved counts before extracting.
  - refs: Fowler, Refactoring: Extract Method, Compose Method

- **extraction/refactoring-write-path-duplication** (refactoring, extraction.py:755-871)
  - Current state: `_extract_single_pass` (755-871, ~116 LOC) inlines its own entities + entity_mentions inserts for methods (778-805) and datasets (807-835) and its own metric writes (837-860). This duplicates the persistence logic already implemented (and resolution-aware) in `_store_resolved` (558-738). The two paths can drift: e.g. single-pass writes entities with `name` as canonical and only the first chunk_id, while the map-reduce path resolves canonicals and records all mention chunks — divergent provenance behavior for the same database. Target state: have the single-pass path build a one-element `map_results` (a single `_validate_extraction` dict tagged with `first_chunk_id`) and an empty resolution `{"groups": []}`, then delegate to `_store_resolved`. `_store_resolved` already handles the no-resolution case (surface_to_canonical empty -> name is its own canonical). Pattern: Substitute Algorithm / Pull Up duplicated logic into one writer. Motivation: single source of truth for persistence; eliminates ~90 LOC and the drift risk; the per-method and per-dataset insert blocks (778-805 vs 807-835) are themselves near-identical and vanish. Estimated scope: rewrite `_extract_single_pass` body to ~20 LOC; verify chunk_id tagging semantics. Prerequisites: ideally do the `_store_resolved` decomposition first so the delegation target is clean. Risk: medium-high — single-pass currently attaches `first_chunk_id` to every entity/metric; the delegated path must reproduce that, and `_store_resolved` prefers description_chunk_id (652) which must remain correct when all chunk_ids equal first_chunk_id.
  - **Fix:** Replace the inline persistence in `_extract_single_pass` with: tag `extracted` items' chunk_id = first_chunk_id, wrap as `[extracted]`, call `_store_resolved(conn, paper_id, [extracted], {"groups": []})`, then add `paper_id`. Keep a regression test comparing single-pass output rows against the prior implementation.
  - refs: Fowler, Refactoring: Substitute Algorithm; DRY

- **extraction/performance-collect-mentions-quadratic** (performance, extraction.py:473-504)
  - When an entity key is already in `seen`, the else-branch (lines 495-503) does a linear scan over the entire `mentions` list to find the matching entry, then for each incoming surface_form does an `if sf not in m["surface_forms"]` membership test (another linear scan). With N distinct entities each appearing in multiple chunks, this is O(N^2 * K) on the number of entity mentions. For a long paper map-reduced into many chunks, the merge step degrades super-linearly. The `seen` set is already maintained for O(1) existence checks, but the actual mention object is re-found via scan rather than looked up.
  - **Fix:** Maintain a `key -> mention` dict (e.g. `by_key`) alongside `seen` so the merge target is fetched in O(1): `m = by_key[key]`. Track surface_forms membership with a per-mention set (or a side dict `key -> set`) instead of `sf not in m["surface_forms"]` list scan, appending to the list only for new forms to preserve order. This collapses the merge to O(total mentions).

- **extraction/performance-get-entities-n-plus-1** (performance, extraction.py:1088-1118)
  - `get_entities` issues one query to fetch all entities, then loops over them and issues a separate `SELECT ... FROM entity_mentions WHERE entity_id = ?` per entity (lines 1097-1100). For a paper with E resolved entities this is E+1 round-trips to SQLite. A paper with many methods/datasets multiplies the per-query overhead linearly.
  - **Fix:** Fetch all mentions in one pass with a single JOIN (`entities LEFT JOIN entity_mentions ON entity_mentions.entity_id = entities.id WHERE entities.paper_id = ?`) and group rows in Python by entity id, or batch the mentions query with `entity_id IN (...)` (respecting the 999-variable batching helper `db._batched_execute` used elsewhere) and bucket results into a dict before assembling the response.
  - refs: docs/reference/schema.md

#### Medium (8)

| ID | Lens | Category | Location | Title | Auto-fix |
| --- | --- | --- | --- | --- | --- |
| extraction/refactoring-map-reduce-split | design | refactoring | extraction.py:874-1024 | _extract_map_reduce mixes orchestration with logging/ETA bookkeeping | — |
| extraction/refactoring-collect-mentions-quadratic | design | refactoring | extraction.py:473-504 | _collect_entity_mentions does an O(n^2) linear rescan on duplicate keys | ✅ |
| extraction/documentation-stale-return-doc | docs | documentation | extraction.py:1027-1049 | estimate_extraction_time docstring contradicts actual error behavior and omits returned ke | — |
| extraction/performance-compare-papers-n-plus-1 | specialist | performance | extraction.py:369-402 | N+1 query pattern in compare_papers per shared dataset | — |
| extraction/performance-upsert-then-select | specialist | performance | extraction.py:195-209 | UPSERT followed by separate SELECT round-trip in _record_entity | — |
| extraction/testing-paper-not-found | test | testing | extraction.py:1069-1071 | extract_structure 'Paper not found' raise path untested | — |
| extraction/testing-estimate-time-branches | test | testing | extraction.py:1027-1049 | estimate_extraction_time short-doc and no-chunks branches untested | — |
| extraction/testing-compare-papers-guard | test | testing | extraction.py:352-353 | compare_papers <2 paper_ids early-return untested | ✅ |

#### Low (13)

| ID | Lens | Category | Location | Title | Auto-fix |
| --- | --- | --- | --- | --- | --- |
| extraction/design-private-in-all | design | design | extraction.py:26-43 | __all__ re-exports underscore-prefixed internal validators | ✅ |
| extraction/design-lazy-import-coupling | design | design | extraction.py:748-752 | Function-local import of papers.get_paper_chunks signals deferred-cycle coupling | — |
| extraction/design-prefetched-chunks-leak | design | design | extraction.py:1052-1085 | Underscore kwarg _prefetched_chunks leaks an internal optimization into a public signature | — |
| extraction/style-source-string-constant | design | style | extraction.py:181-281 | source provenance uses bare string literals instead of an Enum | — |
| extraction/refactoring-prompt-duplication | design | refactoring | extraction.py:405-437 | _EXTRACT_PROMPT and _MAP_PROMPT are near-identical templates | — |
| extraction/documentation-missing-docstrings | docs | documentation | extraction.py:284-340 | Public exported functions get_methods/get_datasets/get_metrics lack docstrings | ✅ |
| extraction/documentation-undocumented-params | docs | documentation | extraction.py:1052-1068 | extract_structure docstring does not document its parameters or unused 'confirmed' arg | — |
| extraction/performance-repeated-dict-lookup | specialist | performance | extraction.py:597-612 | Repeated entity_data dict key lookup in hot loop | ✅ |
| extraction/testing-record-entity-bad-table | test | testing | extraction.py:193-194 | _record_entity invalid-table ValueError guard untested | — |
| extraction/testing-sanitize-str-direct | test | testing | extraction.py:56-61 | _sanitize_str whitespace-retention behavior untested | — |
| extraction/testing-store-resolved-source | test | testing | extraction.py:617-619 | _store_resolved llm_extraction provenance not asserted | — |
| extraction/testing-get-entities-empty-and-confidence | test | testing | extraction.py:1088-1118 | get_entities empty-paper and confidence passthrough untested | — |
| extraction/testing-metric-int-and-stringnum-coercion | test | testing | extraction.py:117-122 | Metric value float-coercion of numeric strings/ints untested | ✅ |

#### Info (2)

| ID | Lens | Category | Location | Title | Auto-fix |
| --- | --- | --- | --- | --- | --- |
| extraction/style-magic-progress-interval | design | style | extraction.py:967-974 | Magic number 5 for ETA logging cadence (and inline /60 seconds-to-minutes) | ✅ |
| extraction/sqli-dynamic-table-name | security | security | extraction.py:181-209 | Dynamic SQL table-name interpolation gated only by allowlist (currently safe) | — |

### E1-db — db.py (22 findings)


#### High (3)

| ID | Lens | Category | Location | Title | Auto-fix |
| --- | --- | --- | --- | --- | --- |
| db/testing-escape-like-untested | test | testing | db.py:54-59 | escape_like has zero tests despite 5+ production LIKE-clause call sites | — |
| db/testing-delete-chunk-vecs-untested | test | testing | db.py:449-473 | delete_chunk_vecs has no direct unit test for empty-input, chunk_count decrement, or expli | — |
| db/testing-delete-chunks-cascade-untested | test | testing | db.py:476-493 | delete_chunks_cascade lacks direct test for return value and empty-list path | — |

- **db/testing-escape-like-untested** (testing, db.py:54-59)
  - escape_like() escapes backslash, percent, and underscore for SQLite LIKE clauses (ESCAPE '\\'). It is imported by conclusions.py:69, folder_summaries.py:27, bibtex.py:106, and papers.py:234 — every wildcard search in the codebase funnels through it. No test file references escape_like (confirmed: grep across tests/ returns 0 hits). The function is pure, order-sensitive (backslash MUST be escaped first or the %/_ escapes get double-escaped), and a regression here silently breaks search correctness or opens a LIKE-injection where a user keyword containing % matches everything. The ordering of the three .replace() calls is exactly the kind of subtle invariant that needs a guard test.
  - **Fix:** Add tests/test_db.py cases covering: no special chars (identity), a lone '%' -> '\\%', a lone '_' -> '\\_', a lone '\\' -> '\\\\', and a combined input like 'a_b%c\\d' to lock the backslash-first ordering. Optionally add a hypothesis property: for any string s, escaping then using it in a LIKE ... ESCAPE '\\' against the literal s matches exactly that row in an in-memory SQLite table (round-trip invariant).
  - refs: src/knowledge_base/conclusions.py:69, src/knowledge_base/folder_summaries.py:27, src/knowledge_base/bibtex.py:106, src/knowledge_base/papers.py:234

- **db/testing-delete-chunk-vecs-untested** (testing, db.py:449-473)
  - delete_chunk_vecs() is exported in __all__ and called from web.py (lines 843, 984, 1046) and indirectly via delete_chunks_cascade. No test references it directly (grep tests/ = 0). Three behaviours are uncovered: (1) the empty-list early return at line 458-459 (`if not chunk_ids: return`); (2) the chunk_count bookkeeping at 468-473 — it counts ACTUAL rows present before deleting (count_rows) and decrements embed_spaces.chunk_count with a MAX(0, ...) clamp, only when actual_deleted>0. Nothing asserts that count stays correct when some chunk_ids have no embedding, nor that the clamp prevents going negative; (3) the table_name override path. The batching boundary is exercised only indirectly through reingest (test_ingest.py:753 patches _SQL_BATCH_SIZE=2) but that test asserts reingest outcomes, not this function's chunk_count contract.
  - **Fix:** Add direct tests: (a) delete_chunk_vecs(conn, []) is a no-op and leaves chunk_count unchanged; (b) insert N vecs into the active space, delete a subset, assert embed_spaces.chunk_count decremented by exactly the number of rows that actually existed (mix in chunk_ids with no embedding to verify count uses actual rows); (c) verify MAX(0,...) clamp by deleting more than chunk_count and asserting count floors at 0; (d) delete from an explicit non-active table_name. Parametrize across element_type float32/int8.
  - refs: src/knowledge_base/web.py:843, src/knowledge_base/web.py:984, tests/test_ingest.py:753-794

- **db/testing-delete-chunks-cascade-untested** (testing, db.py:476-493)
  - delete_chunks_cascade() (exported, called from vision.py:1986, web.py:515, ingest.py:576) has no direct unit test (grep tests/ = 0). Uncovered: (1) the empty-list early return returning 0 (line 489-490); (2) the documented return value `len(chunk_ids)` — note this is the INPUT count, not the number of rows actually deleted, so calling it with non-existent ids returns a non-zero count while deleting nothing; that off-by-semantics is a real trap worth pinning with a test; (3) the two-step cascade ordering (vec rows before chunk rows so the chunks_ad FTS trigger fires correctly) and that the FTS index is cleaned up after cascade.
  - **Fix:** Add tests: (a) delete_chunks_cascade(conn, []) returns 0 and mutates nothing; (b) insert chunks + vecs + an FTS-indexed body, call cascade, assert return == len(input), assert chunks/chunks_vec rows gone AND chunks_fts no longer MATCHes the deleted content; (c) pin the return-count semantics by passing one real id and one bogus id and asserting return==2 while only one row was actually removed (documents the contract or surfaces it as a bug to the reviewer).
  - refs: src/knowledge_base/vision.py:1986, src/knowledge_base/web.py:515, src/knowledge_base/ingest.py:576

#### Medium (6)

| ID | Lens | Category | Location | Title | Auto-fix |
| --- | --- | --- | --- | --- | --- |
| db/refactoring-init-schema-god-fn | design | refactoring | db.py:595-896 | init_schema is a ~300 LOC god function mixing DDL, seeding, and migration orchestration | — |
| db/documentation-missing-element-type-column | docs | documentation | db.py:840-841 | schema.md omits the embed_spaces.element_type column | — |
| db/performance-per-row-chunk-count-update | specialist | performance | db.py:442-446 | Per-row embed_spaces.chunk_count UPDATE in insert_chunk_vec causes N+1 writes during bulk  | — |
| db/testing-insert-chunk-count-increment | test | testing | db.py:441-446 | insert_chunk_vec chunk_count increment side-effect is never asserted | — |
| db/testing-data-migrations-uncovered | test | testing | db.py:165-248 | Table-rebuild legacy migrations have no legacy-path tests (only the no-op fresh-DB path ru | — |
| db/testing-bootstrap-legacy-path | test | testing | db.py:552-592 | _bootstrap_embed_spaces legacy branch (non-empty chunks_vec) and config-driven model/provi | — |

#### Low (11)

| ID | Lens | Category | Location | Title | Auto-fix |
| --- | --- | --- | --- | --- | --- |
| db/refactoring-migration-boilerplate | design | refactoring | db.py:165-363 | Eleven _migrate_* functions duplicate the same 'introspect-then-alter/rebuild' preamble | — |
| db/design-dict-return-primitive-obsession | design | design | db.py:117-150,379-384 | co_occurrence_pairs and get_active_space return untyped dicts where a dataclass/TypedDict  | — |
| db/design-dead-public-fn | design | design | db.py:397-404 | get_active_chunk_strategy is unused dead code (no external callers, absent from __all__) | ✅ |
| db/style-leaky-private-regex | design | style | db.py:370 | _SPACE_NAME_RE is underscore-prefixed but imported cross-module by embed_swap.py | — |
| db/style-duplicated-check-constraint | design | style | db.py:235,317,783 | CHECK constraint definitions are duplicated as literal strings between schema DDL and migr | — |
| db/documentation-get-connection-undocumented | docs | documentation | db.py:153-162 | Exported public function get_connection has no docstring | — |
| db/documentation-init-schema-undocumented | docs | documentation | db.py:595-596 | Exported public function init_schema has no docstring | — |
| db/documentation-incomplete-check-list | docs | documentation | db.py:826-842 | schema.md CHECK-constraint list for embed_spaces is incomplete | — |
| db/sql-injection-vec-table-identifier | security | security | db.py:438-466 | Dynamic SQL table-name interpolation in vec helpers relies on non-local sanitization invar | — |
| db/performance-delete-double-pass-count | specialist | performance | db.py:461-466 | delete_chunk_vecs runs a separate COUNT pass before DELETE instead of using cursor.rowcoun | — |
| db/testing-co-occurrence-boundaries | test | testing | db.py:117-150 | co_occurrence_pairs untested for min_sessions=0 boundary and alphabetical-ordering invaria | — |

#### Info (2)

| ID | Lens | Category | Location | Title | Auto-fix |
| --- | --- | --- | --- | --- | --- |
| db/style-magic-connection-timeout | design | style | db.py:155 | get_connection uses an unnamed magic timeout of 30.0 seconds | ✅ |
| db/refactoring-table-name-fallback-triplication | design | refactoring | db.py:421-493 | Active-space table_name fallback (`tbl = table_name or get_vec_table_name(conn)`) repeated | — |

### E2-search — search.py (22 findings)


#### High (3)

| ID | Lens | Category | Location | Title | Auto-fix |
| --- | --- | --- | --- | --- | --- |
| search/correctness-rerank-scale-mismatch | specialist | correctness | search.py:402-422 | Reranker scores and RRF scores mixed on incomparable scales during final sort 🗣️ | — |
| search/testing-space-name-path | test | testing | search.py:256-271 | space_name (non-active space) search path is entirely untested | — |
| search/testing-matryoshka-truncation-branch | test | testing | search.py:305-313 | Matryoshka query-embedding truncation branch is uncovered | — |

- **search/correctness-rerank-scale-mismatch** (correctness, search.py:402-422)
  - When rerank=True, the cross-encoder is run only on rerank_candidates (capped at rerank_top_n, line 402-406) and only fetchable_cids get score_map[cid] = rs (line 418). Every other entry in score_map keeps its RRF score. reranker.rerank documents scores in [0, 1] (reranker.py:193), while RRF scores are 1/(RRF_K+rank+1) ≈ 0.016 at best and decreasing. The re-sort at line 422 sorts these two incompatible scales together. A reranked candidate that the cross-encoder judged a weak match (e.g. score 0.01, a legitimate value in [0,1]) is then sorted BELOW an un-reranked candidate whose RRF score is ~0.016 — even though that un-reranked candidate was excluded from reranking precisely because it ranked lower. This inverts the intended ordering: items the reranker explicitly scored can be pushed beneath items it never saw. The defect is masked whenever every reranked score happens to exceed the top RRF score (~0.016), so it surfaces intermittently on poor-match pools.
  - **Fix:** Do not mix scales. Either (a) restrict the final ranked list to only the reranked (scored) candidates when rerank succeeds — drop un-reranked entries from score_map before the re-sort — or (b) order primarily by a reranked/not-reranked tier (reranked items always above un-reranked tail), or (c) normalize RRF and reranker scores into a common scale before combining. Option (a)/(b) preserves the contract that reranker order wins.
  - refs: src/knowledge_base/search.py:417-422, src/knowledge_base/reranker.py:193
  - 🗣️ Verified by multi-model debate (Gemini HIGH / Codex MEDIUM; Opus synthesis HIGH): triggers at default top_k when source_type/chunk_strategy filter set; compounds with the under-fetch bug; default unfiltered path unaffected.

- **search/testing-space-name-path** (testing, search.py:256-271)
  - The `space_name` branch of `search()` resolves a specific embedding space via `get_space()`, builds `space_cfg` from the space's model/dim/provider, reads `matryoshka_base_dim`, sets a non-None `vec_table`, and computes `skip_folder_boost = not active or space['name'] != active['name']`. No test in tests/test_search.py, tests/test_reranker.py, tests/test_folder_summaries.py, or tests/test_embed_spaces.py ever calls `search(..., space_name=...)` (grep for `space_name=` in tests/ returns zero matches). This means the folder-boost-skip logic, the per-space provider/model selection, and the explicit `vec_table` plumbing into `_vec_search` are unverified. A regression that, e.g., applied folder boost to a non-active space (folder_summaries_vec is dimensioned for the active space) or queried the wrong vec table would not be caught.
  - **Fix:** Add tests that create a second space (reuse `create_space`/`backfill_space`/`promote_space` helpers from test_embed_spaces.py), then call `search(conn, q, space_name='other')`. Assert (a) results come from the named space's vec table, (b) `_folder_boost` is NOT invoked when the named space != active (patch `knowledge_base.search._folder_boost` and assert_not_called), and (c) when space_name equals the active space name, folder boost IS still eligible.

- **search/testing-matryoshka-truncation-branch** (testing, search.py:305-313)
  - When `space_base_dim` is truthy, `search()` embeds the query at `space_base_dim` then calls `truncate_embedding(query_embedding, space_cfg['dim'])` to shrink it to the space dim before vector search. Grep for `matryoshka` and `truncate_embedding` in tests/test_search.py and tests/test_embed_spaces.py returns no hits for this search code path — matryoshka spaces are tested at ingestion/backfill level (test_create_matryoshka_space, test_backfill_matryoshka_space) but never through `search()`. The truncation-at-query-time logic (and the subsequent None-guard before truncating) is unverified, so a mismatch between query-embedding dim and the stored vec-table dim would surface only at runtime.
  - **Fix:** Create a matryoshka space (base_dim > dim), make it active, ingest a chunk, and call `search()`. Patch `knowledge_base.search.embed_single` to return a base_dim-length vector and assert the vector handed to `_vec_search` has been truncated to `space_cfg['dim']` (e.g. patch `_vec_search` and inspect the embedding length argument).

#### Medium (5)

| ID | Lens | Category | Location | Title | Auto-fix |
| --- | --- | --- | --- | --- | --- |
| search/refactoring-god-function | design | refactoring | search.py:202-473 | search() is a ~270-line god function mixing 8 distinct phases | — |
| search/correctness-source-type-postfilter | specialist | correctness | search.py:430-450 | source_type filtered only at final SQL fetch — can return fewer than top_k results 🗣️ | — |
| search/testing-none-embedding-vec-skip | test | testing | search.py:312-324 | embed_single returning None (vec leg skipped) is untested | — |
| search/testing-rerank-degradation-path | test | testing | search.py:423-428 | Reranker graceful-degradation except clause is untested | — |
| search/testing-fts-operationalerror-skip | test | testing | search.py:294-301 | FTS OperationalError (malformed MATCH query) skip path untested | — |

#### Low (11)

| ID | Lens | Category | Location | Title | Auto-fix |
| --- | --- | --- | --- | --- | --- |
| search/design-string-enums | design | design | search.py:33,62-64 | Closed string vocabularies use frozensets + comments instead of enum.StrEnum | — |
| search/design-spacecfg-dict | design | design | search.py:261-265 | space_cfg is a free-form dict where a dataclass belongs (primitive obsession) | — |
| search/design-param-flag-explosion | design | design | search.py:202-213 | search() has 9 parameters including two boolean control flags | — |
| search/style-magic-threshold-2 | design | style | search.py:166 | Magic number 2 for folder-boost distance threshold lacks a named constant | ✅ |
| search/documentation-searchresult-no-docstring | docs | documentation | search.py:25-33 | Public SearchResult dataclass has no docstring | ✅ |
| search/documentation-search-args-returns | docs | documentation | search.py:214-238 | search() docstring omits conn parameter and lacks a Returns section | ✅ |
| search/sql-injection-vec-table-identifier | security | security | search.py:106-108 | Dynamic table/expression identifier interpolated into SQL via f-string in _vec_search | — |
| search/performance-redundant-chunks-reads | specialist | performance | search.py:389-450 | Up to three separate full-table reads of chunks per search on the rerank path | — |
| search/correctness-folder-boost-zero-threshold | specialist | correctness | search.py:165-170 | Folder-boost threshold collapses to near-zero when best folder distance is exactly 0 | — |
| search/testing-private-helpers-uncovered | test | testing | search.py:192-199 | _fetch_chunk_contents and _vec_search default-table path lack direct tests | — |
| search/testing-chunk-content-mismatch-skip | test | testing | search.py:430-471 | Post-fetch chunk-id mismatch skip (source_type/strategy filter dropping candidates) untest | — |

#### Info (3)

| ID | Lens | Category | Location | Title | Auto-fix |
| --- | --- | --- | --- | --- | --- |
| search/style-lazy-getspace-import | design | style | search.py:258 | Lazy in-function import of get_space despite embed_swap already imported at module top | ✅ |
| search/style-redundant-scoremap-rebuild | design | style | search.py:454 | Redundant score_map rebuild from ranked before result construction | — |
| search/style-manual-placeholders-final-fetch | design | style | search.py:434-450 | Final chunk fetch hand-builds IN-clause placeholders instead of using _batched_select | — |

## Static-Tool Findings (net-new)

### Supply-chain — pip-audit (category: supply-chain)

6 dependencies with known CVEs (28 audited). requests/urllib3/idna are transitive (trafilatura) and sit on web.py's HTTP/SSRF fetch path → priority. Fixes available; low-effort bump.

| package | version | advisory | fix |
| --- | --- | --- | --- |
| idna | 3.11 | CVE-2026-45409 | 3.15 |
| pip | 26.0.1 | CVE-2026-3219 | 26.1 |
| pip | 26.0.1 | CVE-2026-6357 | 26.1 |
| pygments | 2.19.2 | CVE-2026-4539 | 2.20.0 |
| requests | 2.32.5 | CVE-2026-25645 | 2.33.0 |
| urllib3 | 2.6.3 | PYSEC-2026-142 | 2.7.0 |
| urllib3 | 2.6.3 | PYSEC-2026-141 | 2.7.0 |

### Build hygiene (category: infra)

- **MEDIUM** — No `[tool.ruff]` / `[tool.mypy]` / `[tool.pyright]` config in pyproject.toml. Ruff runs its default minimal ruleset (E/F/W only) → Phase-1's '0 warnings' undersells real lint debt. No enforced type checking despite mypy+pyright installed. *auto_fixable: add tool config.*
- **LOW** — Direct deps use `>=` without upper bounds (httpx/numpy/pillow/sqlite-vec/trafilatura/...); mitigated by `uv.lock` (390KB, present). requires-python `>=3.12` ✓; no `import *`; no vendored dirs.

### mypy type-safety (20 errors, default config; category: type-safety)

By file: extraction.py=11, embeddings.py=3, chunking.py=2, vision.py/search.py/papers.py/ingest.py=1. Real bug smells (not just annotations):
- **MEDIUM** extraction.py:607,629,648 `union-attr` — `Item None of list|None has no attribute append/__iter__` → missing None-guard, can raise AttributeError at runtime.
- **LOW** extraction.py:96,598 `assignment` type mismatch; search.py:395 + ingest.py:302 `no-redef` (variable shadowing); embeddings.py:170-172 `list[float]|None` passed to len()/normalize without guard.

### bandit (27 findings, 5459 LOC; category: security — corroboration)

- **3× B603** subprocess-call: vision.py:409,512 (omniparser), web.py:681 (browser launch) — verify input provenance (LOW; LLM security lens reviewed web subprocess).
- **2× B112** try/except/continue (swallowed exceptions): browser/render_page.py:34,49 (LOW).
- **2× B101** assert-as-check (stripped under -O): ingest.py:167, vision.py:429 (LOW).
- **18× B608** SQL-string-construction across db/extraction/ingest/search/vision/web — **adjudicated by security lens as necessary identifier interpolation with parameterized values** (LOW/INFO defense-in-depth; see E1-db/E2-search/D-extraction security findings + existing issue #194).

## Likely overlaps with 3-month-old super-qa (for reconciliation)

- C-ingest N+1 content_hash (ingest.py:360) → likely relates to **#210**
- D-extraction N+1 get_entities → likely relates to **#208**
- D-extraction O(n·m) mention dedup → likely relates to **#207**
- D-extraction single-pass dup storage → likely relates to **#213**
- db.py f-string SQL pattern (security LOW) → likely relates to **#194**
- SSRF redirect comment cites #232 (which is a low-findings bucket, not an SSRF tracker) → likely relates to **#232 stale ref**
