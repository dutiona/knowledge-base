# Figure Extraction

## How It Works

Figure extraction renders PDF pages as images and sends them to a vision model for description. The pipeline:

1. **Page selection** -- Either user-specified pages or heuristic auto-detection. The heuristic looks for four signals (any one triggers inclusion): embedded images, >10 vector drawings, text density below 50% of page average, or caption cues (Figure/Fig./Table patterns). Falls back to all pages if no candidates are found.

2. **Rendering** -- Candidate pages are rendered as PNG at 2x resolution via PyMuPDF.

3. **OmniParser (optional)** -- If configured, each rendered page is analyzed by OmniParser to detect text regions and icons. When multiple spatial clusters are detected (using y-axis then x-axis gap analysis), the page is cropped into sub-regions and each crop is sent to the vision model separately.

4. **Vision model** -- Each page (or crop) is sent to the vision API as a base64-encoded PNG. The model returns a JSON array of figure descriptions, each with `figure_type`, `title`, `description`, and `entities_mentioned`. Vision calls run in a thread pool (max 4 workers) for parallelism.

5. **Enrichment** -- For single-figure pages, OmniParser detected text and icons are appended to the figure description (capped at 500 chars). For multi-figure pages, elements are filtered to each figure's spatial region.

6. **Storage** -- Figure descriptions are embedded and stored as chunks with `source_type='figure'`. Old figure chunks for the same pages are deleted first (idempotent). Rendered PNGs are saved to `~/.local/share/knowledge-base/figures/<paper_id>/`.

## Vision Model Configuration

Configure the vision model before running figure extraction:

```json
{
  "name": "configure_vision_tool",
  "arguments": {
    "model": "gemma3:27b",
    "base_url": "http://localhost:11434"
  }
}
```

The vision API must support the OpenAI-compatible `/v1/chat/completions` endpoint with image inputs. Default model is `gemma3:27b`; default base URL is auto-detected like the embedding client (OLLAMA_HOST, WSL2 gateway, localhost).

Call with no arguments to query the current configuration.

## OmniParser Enrichment

OmniParser adds OCR text and icon detection to figure descriptions. It requires a local installation with its own Python venv.

```json
{
  "name": "configure_omniparser_tool",
  "arguments": { "path": "/home/user/OmniParser" }
}
```

Requirements:

- `parse.py` must exist at the configured path
- `.venv/bin/python` must exist at the configured path

To disable OmniParser:

```json
{
  "name": "configure_omniparser_tool",
  "arguments": { "path": "" }
}
```

To query the current configuration:

```json
{ "name": "configure_omniparser_tool" }
```

When OmniParser is configured, ETA estimates increase from ~4 seconds/page to ~44 seconds/page.

## Page Selection

Pages are specified as **1-based** numbers in the tool API (converted to 0-based internally):

```json
{
  "name": "extract_figures_tool",
  "arguments": { "paper_id": 1, "pages": [3, 5, 7] }
}
```

Omit `pages` for auto-detection via heuristic filtering.

When specific pages are provided, only those pages' figure chunks are deleted and replaced -- existing figure chunks from other pages are preserved.

## Background Jobs

Figure extraction always runs as a background job (it involves PDF rendering + vision API calls). The tool returns a job ID:

```json
{
  "name": "extract_figures_tool",
  "arguments": { "paper_id": 1, "confirmed": true }
}
```

Response:

```json
{
  "deferred": true,
  "job_id": 5,
  "status": "pending",
  "message": "Use get_job_status(job_id) to poll progress."
}
```

## ETA Gate

If estimated extraction time exceeds 2 minutes, the tool returns a confirmation prompt first:

```json
{
  "confirm_required": true,
  "estimated_seconds": 264,
  "candidate_pages": 6
}
```

Call again with `confirmed: true` to proceed.

## Chunk Encoding

Figure chunks use a page-slot encoding for `chunk_index`:

```
chunk_index = 1,000,000 + (page_num * 1,000) + figure_index
```

This separates figure chunks from text chunks (which use indices starting at 0) and allows per-page figure management. Each page has 1,000 available slots; if a page has more than 1,000 figures (effectively impossible), the figure index is capped at 999 with a warning.

## Web Page Inline Images

When ingesting web pages via `ingest_url`, inline `<img>` tags are extracted from the HTML and sent to the vision model for description. This complements the browser screenshot approach (below) by extracting individual images at their native resolution rather than relying on a single full-page screenshot.

**Why this exists:** Text-rich pages (>= 200 chars from trafilatura) never trigger the browser fallback, so their images were completely lost — even pages with dozens of meaningful diagrams and charts. Even when the browser fallback fires, the single 1280x8000px screenshot approach misses images below the fold and asks one vision call to enumerate all figures from a dense composite image. Inline extraction solves both gaps by parsing `<img>` tags directly from the fetched HTML (see [#82](https://github.com/dutiona/knowledge-base/issues/82) for the full analysis).

**When it runs:** Inline image extraction runs automatically when a vision model is configured and the browser-based screenshot extraction did not already fire. If the browser fallback produced figures from a full-page screenshot, inline extraction is skipped to avoid duplicate descriptions of the same visual content.

**Filtering:** Not all images on a page are meaningful. The following are skipped:

- Decorative images (URL or alt text matching patterns like logo, icon, avatar, banner, sprite, tracking pixel, or badge)
- Small images (width or height below 100px — checked first from HTML attributes to avoid unnecessary downloads, then from actual pixel dimensions after download)
- SVG and data URI images (cannot be processed by the vision pipeline without rasterization)
- Images from private/loopback IP ranges (SSRF protection)
- Duplicate URLs on the same page

Up to 10 images per page are processed. Each image download is capped at 10 MB (streamed). Non-PNG images (JPEG, WebP, GIF) are converted to PNG before being sent to the vision model.

**Chunk encoding:**

```
chunk_index = 2,000,000 + image_index
```

This uses a separate range from PDF figure chunks (1,000,000+) and browser screenshot chunks (also 1,000,000+, scoped by source_uri). The `image_index` is a zero-based sequential counter over the qualifying images on that page (after filtering and deduplication). Unlike PDF figures which encode page number into the index, web images use a flat counter since HTML has no page concept.

**Metadata:** Each inline image figure chunk stores:

- `figure_type`: `"web_image"`
- `image_url`: The resolved absolute URL the image was downloaded from
- `alt_text`: The `alt` attribute from the `<img>` tag (if present)
- `original_source_type`: `"web"`
- `source_url`: The page URL (post-redirect)
- `vision_model`: The model used for description

**Re-ingestion:** When the same URL is ingested again, old inline image chunks (chunk_index >= 2,000,000) are deleted and replaced. Foreign key references (entity mentions, methods, datasets, metrics, papers, relationships, conclusions) are cleaned up before deletion.

## Browser Configuration

For web page figure extraction (via `ingest_url`), browser rendering must be configured:

```json
{
  "name": "configure_browser_tool",
  "arguments": {
    "venv_path": "/home/user/.venvs/playwright"
  }
}
```

Two modes:

- **Local** -- Provide `venv_path` only. Launches headless Chromium from the venv.
- **CDP** -- Provide both `cdp_endpoint` (ws:// URL) and `venv_path`. Connects to a running Playwright container.

The venv must have the `playwright` Python package installed. For local mode, Chromium must also be installed (`playwright install --with-deps chromium`).

To disable:

```json
{
  "name": "configure_browser_tool",
  "arguments": { "cdp_endpoint": "", "venv_path": "" }
}
```
