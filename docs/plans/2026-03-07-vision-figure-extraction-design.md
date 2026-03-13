# Vision-Augmented Figure Extraction

**Date**: 2026-03-07
**Issue**: https://github.com/dutiona/knowledge-base/issues/20
**Status**: Draft (R2 — post Codex+Gemini review)

## Problem

Figures, diagrams, and image-based tables are lost during text-only PDF extraction.
For research papers, this means losing 30-50% of critical information (architecture
diagrams, comparison charts, flow visualizations). These are not searchable in the
current system.

## Design Decisions

| Decision         | Choice                                 | Rationale                                                                                                                           |
| ---------------- | -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| Figure detection | Hybrid heuristic (C)                   | Full-page render for candidates; avoids wasting vision calls on text-only pages while catching vector diagrams PyMuPDF can't detect |
| Tool integration | Separate `extract_figures_tool` (A)    | Mirrors `extract_structure_tool` pattern; keeps ingest fast; degrades gracefully without vision model                               |
| Storage          | Chunks with `source_type='figure'` (A) | Figures auto-participate in hybrid search with zero search pipeline changes                                                         |
| Vision API       | Separate `_vision_call` function (A)   | Vision models differ from text LLMs; separate config keys (`vision_model`, `vision_base_url`); keeps `_llm_call` untouched          |
| Prompt output    | Structured JSON array (B)              | One object per figure per page; enables metadata tagging and entity cross-referencing                                               |
| PNG storage      | On disk                                | Cheap insurance for debugging and re-prompting with different models                                                                |
| Page hints       | Optional `pages` parameter             | User can override heuristic to target specific pages                                                                                |

## Architecture

```
extract_figures_tool(paper_id, pages?, confirmed?)
    |
    v
pages hint provided? ──yes──> render specified pages
    |no
    v
heuristic filter:
  has_images OR has_drawings OR
  low_text_density OR caption_cues
    |
    v
render candidate pages (PyMuPDF, 2x matrix)
    |
    v
for each page PNG (concurrent, bounded thread pool):
    base64 encode
    _vision_call(structured prompt)
    validate JSON schema
    parse figure objects
    |
    v
── single SQLite transaction ──
delete old figure chunks for this paper
for each figure:
    create chunk (source_type='figure')
    embed + store in chunks + chunks_vec
── commit ──
    |
    v
save page PNGs to ~/.local/share/knowledge-base/figures/{paper_id}/
```

## New Module: `vision.py`

All vision-specific logic isolated from existing code.

### Functions

**`_heuristic_filter(pdf_path: str) -> list[int]`**

Returns 0-indexed page numbers likely containing figures. Four signals (any triggers inclusion):

- `page.get_images()` count > 0 (catches raster images)
- `page.get_drawings()` count above threshold (catches vector diagrams from TikZ/pgf)
- Text density below page average (catches large figures displacing text)
- Caption cues: text contains "Figure", "Fig.", or "Table" patterns via regex

If the heuristic yields zero pages, fall back to all pages (recall > precision).

**`_render_page(pdf_path: str, page_num: int) -> bytes`**

Renders a single page to PNG bytes via PyMuPDF.
Uses `fitz.Matrix(2, 2)` for readability (~150 DPI equivalent).
`page_num` is 0-indexed (PyMuPDF convention).

**`_vision_call(image_b64: str, prompt: str, *, conn) -> list[dict]`**

Sends multimodal request to vision model. Always uses OpenAI-compatible
`/v1/chat/completions` with `image_url` content blocks. Reads config from
`vision_model`, `vision_base_url` in config table.

Includes response validation:

- Strip markdown code fences before `json.loads`
- Validate each object has required fields (`figure_type`, `description`)
- Coerce missing optional fields (`title` → `null`, `entities_mentioned` → `[]`)
- Reject and log objects with empty `description`

**`_validate_figure(obj: dict) -> dict | None`**

Schema validation/coercion for a single figure object from the vision model.
Returns cleaned dict or `None` (logged and skipped).

**`extract_figures(conn, paper_id: int, pages: list[int] | None = None, confirmed: bool = False) -> dict`**

Orchestrator:

1. Finds paper's source PDF via `papers.source_uri` (canonical, not from chunks)
2. Validates `pages` hint: converts 1-based user input to 0-based internally, bounds-checks against PDF page count
3. Runs heuristic or uses `pages` hint
4. ETA gate (if not confirmed and estimated time > threshold)
5. Renders pages concurrently (bounded thread pool, max 4 workers)
6. Calls vision model for each page (concurrent, respects Ollama parallelism)
7. Validates and collects all figure objects
8. In a single SQLite transaction: deletes old figure chunks, inserts new chunks + embeddings
9. Saves PNGs to disk (outside transaction — best effort)
10. Returns summary stats

### Idempotency

All chunk operations (delete old + insert new) happen in a single SQLite
transaction. If any step fails, the transaction rolls back and existing
figure data is preserved. This avoids the "delete then crash" data loss
scenario.

Delete scope: `source_type='figure'` AND `source_uri` matching the paper's PDF.

### Page Numbering Convention

- **User-facing** (MCP tool `pages` parameter): **1-based** (matches what users see in PDF viewers)
- **Internal** (PyMuPDF, stored metadata): **0-based**
- Conversion happens once at the tool boundary in `extract_figures_tool`

## Vision Prompt

System prompt instructs the model to return a JSON array, one object per
figure detected on the page. Empty array `[]` if no figures.

```json
[
  {
    "figure_type": "diagram|chart|table|photo|equation",
    "title": "Figure 3: CoALA Framework",
    "description": "Detailed natural language description...",
    "entities_mentioned": ["CoALA", "working memory", "retrieval"]
  }
]
```

Prompt instructions include:

- One object per distinct figure; treat sub-figures (a), (b), (c) as separate objects if they represent different concepts
- `title`: transcribe exactly as shown, or `null` if no visible caption
- `entities_mentioned`: only names explicitly visible in the figure, do not infer
- `description`: describe visual relationships and structure; if text in the figure is illegible, describe the layout rather than guessing text content
- Do not fabricate information not visible in the image
- Return `[]` if no figures, diagrams, charts, or tables are present

## Chunk Storage

| Field         | Value                                                                                                   |
| ------------- | ------------------------------------------------------------------------------------------------------- |
| `content`     | `description` from vision model                                                                         |
| `source_type` | `'figure'`                                                                                              |
| `source_uri`  | Original PDF path                                                                                       |
| `chunk_index` | `1_000_000 + page_num * 100 + figure_index` (disjoint from text chunk namespace)                        |
| `metadata`    | `{"page": N, "figure_type": "...", "title": "...", "entities_mentioned": [...], "vision_model": "..."}` |

The `1_000_000` offset guarantees figure chunk indices never collide with text
chunk indices (which are sequential starting from 0). A page with >99 figures
is unrealistic for research papers.

Content-hash deduplication applies. Embedded via same pipeline as text chunks.

## PNG Storage

- Path: `~/.local/share/knowledge-base/figures/{paper_id}/page_{N}.png`
- One PNG per rendered page (not per figure)
- Created after successful vision call (best effort, outside transaction)

## Config Keys

| Key               | Default        | Description                             |
| ----------------- | -------------- | --------------------------------------- |
| `vision_model`    | `"gemma3:27b"` | Vision-capable model on Ollama          |
| `vision_base_url` | auto-detected  | Same Ollama URL detection as embeddings |

Removed `vision_provider` — always OpenAI-compatible. No dead config.

## MCP Tools (2 new)

### `extract_figures_tool(paper_id, pages?, confirmed?)`

Main tool. Parameters:

- `paper_id: int` — required, paper must exist and have a `source_uri` pointing to a PDF
- `pages: list[int] | None` — optional 1-based page numbers, override heuristic
- `confirmed: bool` — ETA gate bypass (same pattern as `extract_structure_tool`)

Returns: `{"pages_processed": N, "pages_failed": M, "figures_found": K, "chunks_created": K, "errors": [...]}`

### `configure_vision_tool(model?, base_url?)`

Set vision model config. Only two keys: `vision_model` and `vision_base_url`.

## Error Handling

- Per-page errors caught and logged (don't abort pipeline)
- Vision model timeout: 30s per call
- JSON parse failures: log raw response, skip page, continue
- Schema validation failures: log invalid objects, skip them, continue
- Returns error summary in result dict
- Transaction rollback on storage failure preserves existing data
- Pattern matches `extract_structure_tool` error resilience

## Performance

- Vision calls run concurrently via `ThreadPoolExecutor(max_workers=4)`
- Ollama handles concurrent requests if `OLLAMA_NUM_PARALLEL` is configured
- Rendering is CPU-bound but fast (~50ms/page) — not worth parallelizing
- ETA estimate: `len(candidate_pages) * 4` seconds (conservative)

## What This Does NOT Change

- Existing `ingest()` pipeline
- Existing `_llm_call()` function
- Search pipeline (figures are just chunks)
- Database schema (no new tables)
- Any existing MCP tools

## Test Plan

1. Unit test `_heuristic_filter` with a known PDF (CoALA paper) — verify pages 4, 7 are detected
2. Unit test `_heuristic_filter` caption cue detection
3. Unit test `_heuristic_filter` fallback when zero candidates
4. Unit test `_render_page` produces valid PNG bytes
5. Unit test `_validate_figure` with valid, partial, and invalid inputs
6. Unit test page number conversion (1-based → 0-based)
7. Integration test `_vision_call` with mocked HTTP response
8. Integration test `_vision_call` with malformed JSON (markdown fences, extra prose)
9. Integration test `extract_figures` end-to-end with CoALA paper pages 4, 7
10. Test idempotency: run twice, verify no duplicate chunks
11. Test transaction rollback: simulate mid-insert failure, verify old data preserved
12. Test `pages` hint override (1-based input)
13. Test ETA gate behavior
14. Test chunk_index values are in the 1_000_000+ range

## Changelog

### R1 → R2 (Codex + Gemini review)

**Blockers addressed:**

- **Atomic idempotency**: Changed from delete-then-insert to single SQLite transaction wrapping both delete and all inserts. Rollback preserves existing data on failure.
- **chunk_index collision**: Added `1_000_000` offset to figure chunk indices, creating a disjoint namespace from text chunks.
- **Heuristic recall**: Added `page.get_drawings()` threshold check and caption cue regex ("Figure", "Fig.", "Table"). Added zero-candidate fallback to all pages.
- **Response validation**: Added `_validate_figure()` function with markdown fence stripping, required field validation, optional field coercion, and empty-description rejection.

**Medium findings addressed:**

- **Page numbering**: Defined convention — user-facing is 1-based, internal is 0-based, conversion at tool boundary.
- **Source PDF lookup**: Changed to use `papers.source_uri` (canonical) instead of querying chunks.
- **Prompt hallucination**: Added explicit instructions to not fabricate unseen text, transcribe titles exactly, only list visible entities.
- **Multi-pane figures**: Added prompt instruction to treat sub-figures as separate objects.
- **Performance**: Added `ThreadPoolExecutor(max_workers=4)` for concurrent vision calls.

**Low findings noted (not addressed — intentional):**

- Bounding box storage: Deferred — vision models are unreliable for precise coordinates. Can add later.
- PNG retention policy: Acceptable disk cost for research papers. Can add cleanup later.
- Reproducibility metadata: `vision_model` is stored in chunk metadata. `prompt_version` deferred to when prompt actually changes.
- Removed dead `vision_provider` config key.
