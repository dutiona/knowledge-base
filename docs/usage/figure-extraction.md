# Figure Extraction

## How It Works

Figure extraction uses a dual-path pipeline that prioritizes pymupdf4llm-extracted images over full-page rendering:

### Primary path: extracted images

During PDF ingestion, pymupdf4llm extracts raster images and records them in chunk metadata (`images` field). The vision pipeline reads these image basenames from the database, resolves them to files on disk (in the PDF's image directory), and sends each directly to the vision model.

**Why this is better than full-page rendering:** each extracted image contains exactly one figure — no sub-figure conflation, no surrounding text noise, and smaller vision model inputs produce more focused descriptions.

### Fallback path: full-page rendering

Some figures are vector-drawn (plots, diagrams composed of PDF drawing primitives). pymupdf4llm cannot extract these as images. The pipeline detects such pages by counting fitz drawing objects (>10 threshold) on pages that have no extracted images, then renders those pages at 2x resolution for the vision model.

### Full pipeline

1. **Extracted image collection** -- Queries chunk metadata for image basenames, resolves to disk paths, deduplicates by filename (earliest page wins).

2. **Vector page detection** -- Identifies pages with many vector drawings (>10) that lack extracted images. When pages are explicitly requested, all pages without extracted images are rendered (regardless of drawing count).

3. **ETA gate** -- If estimated time for (extracted images + vector pages) exceeds 2 minutes, returns a confirmation prompt.

4. **OmniParser (optional)** -- If configured, each extracted image and each rendered vector page is analyzed by OmniParser. For extracted images, no multi-region splitting (each image IS a single figure). For vector pages, spatial clustering may split a page into subregions.

5. **Vision model** -- Extracted images use a figure-specific prompt (tailored for single-figure analysis). Vector pages use the original full-page prompt. Vision calls run in a thread pool (max 4 workers).

6. **Enrichment** -- OmniParser text/icons are merged into figure descriptions (keyed by image name for extracted images, by page number for vector pages).

7. **Storage** -- Figure descriptions are embedded and stored as chunks with `source_type='figure'`. Old figure chunks are deleted first (unscoped for full refresh, page-scoped for explicit pages). Vector-rendered PNGs are saved to `~/.local/share/knowledge-base/figures/<paper_id>/`.

### Known limitations

- **Mixed raster+vector pages**: If a page has an extracted raster image, the full-page render is skipped even if the page also contains vector figures. The extracted image is processed; the vector figure is missed. This is by design — the primary path prioritizes extracted images.
- **Multi-page chunk images**: A chunk may span multiple PDF pages. All images in the chunk are assigned to the chunk's first page since pymupdf4llm doesn't provide per-image page mapping.

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
  "extracted_images": 4,
  "vector_pages": 2
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
