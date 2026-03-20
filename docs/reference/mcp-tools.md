# MCP Tools Reference

Complete reference for all 43 MCP tools exposed by the knowledge-base server.

Return values are JSON strings unless noted otherwise.

## Ingest

Tools for adding content to the index.

### ingest

Ingest a file or directory into the research index.

| Parameter     | Type          | Required | Default              | Description                                                                                                                         |
| ------------- | ------------- | -------- | -------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `path`        | `str`         | yes      | --                   | Absolute path to a file or directory                                                                                                |
| `source_type` | `str \| None` | no       | `None` (auto-detect) | Override auto-detection. One of: `pdf`, `markdown`, `code`, `web`, `note`, `figure`                                                 |
| `session_id`  | `str \| None` | no       | `None`               | Session ID for co-occurrence tracking. Directories auto-generate one; for files, pass the same ID across calls to mark co-ingestion |

**Returns (file):**

```json
{ "file": "/path/to/file.pdf", "chunks_added": 12, "chunks_skipped": 0 }
```

**Returns (directory):**

```json
{
  "files_processed": 3,
  "chunks_added": 42,
  "chunks_skipped": 0,
  "details": [
    { "file": "/path/to/file.pdf", "chunks_added": 12, "chunks_skipped": 0 }
  ]
}
```

Uses content-hash deduplication -- unchanged chunks are skipped.

### reingest

Force re-ingest of a previously ingested file. Deletes old chunks and inserts new ones. Cleans up FK references in papers, relationships, and conclusions.

| Parameter     | Type          | Required | Default              | Description                                                                         |
| ------------- | ------------- | -------- | -------------------- | ----------------------------------------------------------------------------------- |
| `path`        | `str`         | yes      | --                   | Absolute path to the file to re-ingest                                              |
| `source_type` | `str \| None` | no       | `None` (auto-detect) | Override auto-detection. One of: `pdf`, `markdown`, `code`, `web`, `note`, `figure` |
| `session_id`  | `str \| None` | no       | `None`               | Optional session ID for co-occurrence tracking                                      |

**Returns:**

```json
{ "file": "/path/to/file.pdf", "chunks_deleted": 8, "chunks_added": 12 }
```

### ingest_url

Ingest a web page by URL. Fetches the page, extracts main content, and indexes it.

Uses content-hash dedup -- unchanged pages are skipped on re-ingest.

| Parameter    | Type          | Required | Default | Description                                    |
| ------------ | ------------- | -------- | ------- | ---------------------------------------------- |
| `url`        | `str`         | yes      | --      | The URL to fetch and ingest                    |
| `session_id` | `str \| None` | no       | `None`  | Optional session ID for co-occurrence tracking |

**Returns:**

```json
{
  "url": "https://example.com/page",
  "source_uri": "https://example.com/page",
  "source_type": "web",
  "browser_rendered": false,
  "figures_extracted": 0,
  "chunks_added": 5,
  "chunks_skipped": 0,
  "title": "Example Page Title"
}
```

Falls back to browser rendering (if configured) when trafilatura extracts insufficient content (< 200 chars). When browser rendering is active, figures may also be extracted from a viewport screenshot.

### co_occurrence

Find document pairs that were ingested together in the same session. Co-ingestion is a behavioral signal: documents ingested together share research context at ingestion time, complementing embedding similarity.

| Parameter      | Type  | Required | Default | Description                                         |
| -------------- | ----- | -------- | ------- | --------------------------------------------------- |
| `min_sessions` | `int` | no       | `1`     | Minimum number of shared sessions to include a pair |

**Returns:**

```json
[
  {
    "source_uri_a": "/path/to/paper1.pdf",
    "source_uri_b": "/path/to/paper2.pdf",
    "co_sessions": 3
  }
]
```

Pairs are ordered by `co_sessions` descending. URIs are alphabetically ordered within each pair.

---

## Search

Tools for querying the index.

### search_index

Search the research index using hybrid semantic + keyword search.

| Parameter        | Type          | Required | Default    | Description                                                                 |
| ---------------- | ------------- | -------- | ---------- | --------------------------------------------------------------------------- |
| `query`          | `str`         | yes      | --         | Natural language search query                                               |
| `top_k`          | `int`         | no       | `10`       | Number of results to return                                                 |
| `source_type`    | `str \| None` | no       | `None`     | Filter results by type (`pdf`, `markdown`, `code`, `web`, `note`, `figure`) |
| `mode`           | `str`         | no       | `"hybrid"` | Search mode: `hybrid`, `fts` (keyword only), `vec` (semantic only)          |
| `chunk_strategy` | `str \| None` | no       | `None`     | Filter by chunking strategy (`mechanical` or `semantic`). None returns all. |
| `space_name`     | `str \| None` | no       | `None`     | Search a specific embedding space instead of the active one.                |

**Returns:** Array of result objects:

```json
[
  {
    "chunk_id": 42,
    "content": "The transformer architecture...",
    "source_type": "pdf",
    "source_uri": "/path/to/paper.pdf",
    "chunk_index": 3,
    "score": 0.891234,
    "match_type": "hybrid"
  }
]
```

`match_type` is one of `fts`, `vec`, or `hybrid`. Hybrid mode combines BM25 and cosine similarity via {term}`Reciprocal Rank Fusion (RRF)`.

### status

Get index statistics: chunk counts by type, recent ingestions, DB size.

| Parameter | Type | Required | Default | Description |
| --------- | ---- | -------- | ------- | ----------- |
| _(none)_  | --   | --       | --      | --          |

**Returns:**

```json
{
  "total_chunks": 1500,
  "by_type": { "pdf": 800, "markdown": 400, "code": 300 },
  "papers": 25,
  "conclusions": 12,
  "relationships": 30,
  "methods": 40,
  "datasets": 15,
  "metrics": 60,
  "prediction_errors": 3,
  "jobs": { "completed": 5, "pending": 1 },
  "embed_config": { "model": "bge-m3", "dim": 1024 },
  "recent_ingestions": [],
  "db_size_mb": 42.5,
  "db_path": "/home/user/.local/share/knowledge-base/knowledge.db"
}
```

---

## Papers

Tools for managing paper metadata and relationships.

### register_paper_tool

Register a research paper. Optionally link to already-ingested chunks via `source_uri`.

| Parameter          | Type                | Required | Default | Description                                                      |
| ------------------ | ------------------- | -------- | ------- | ---------------------------------------------------------------- |
| `title`            | `str`               | yes      | --      | Paper title                                                      |
| `authors`          | `list[str] \| None` | no       | `None`  | List of author names                                             |
| `year`             | `int \| None`       | no       | `None`  | Publication year                                                 |
| `venue`            | `str \| None`       | no       | `None`  | Conference or journal name                                       |
| `doi`              | `str \| None`       | no       | `None`  | Digital Object Identifier                                        |
| `bibtex`           | `str \| None`       | no       | `None`  | Raw BibTeX entry (stored as-is for export)                       |
| `source_uri`       | `str \| None`       | no       | `None`  | Path of an already-ingested file to link chunks to this paper    |
| `skip_auto_relate` | `bool`              | no       | `False` | If true, skip auto-scheduling similarity scan (for bulk imports) |

**Returns:**

```json
{ "paper_id": 1 }
```

When `source_uri` is provided and `skip_auto_relate` is false (default), an `auto_relate` background job is automatically scheduled.

### get_paper_tool

Retrieve paper metadata, related chunks, and relationships. At least one lookup parameter must be provided.

| Parameter       | Type          | Required | Default | Description                     |
| --------------- | ------------- | -------- | ------- | ------------------------------- |
| `paper_id`      | `int \| None` | no       | `None`  | Lookup by paper ID              |
| `title_pattern` | `str \| None` | no       | `None`  | Lookup by title substring match |
| `doi`           | `str \| None` | no       | `None`  | Lookup by DOI                   |

**Returns:** Paper metadata dict with linked chunks and relationships.

### add_relationship_tool

Add a typed relationship between two papers. Upserts on conflict (same source, target, and type).

| Parameter           | Type          | Required | Default | Description                                                                                  |
| ------------------- | ------------- | -------- | ------- | -------------------------------------------------------------------------------------------- |
| `source_paper_id`   | `int`         | yes      | --      | ID of the source paper                                                                       |
| `target_paper_id`   | `int`         | yes      | --      | ID of the target paper                                                                       |
| `relation_type`     | `str`         | yes      | --      | One of: `extends`, `contradicts`, `replicates`, `cites`, `compares`, `applies`, `implements` |
| `confidence`        | `float`       | no       | `1.0`   | Confidence score 0.0--1.0                                                                    |
| `evidence_chunk_id` | `int \| None` | no       | `None`  | Chunk ID containing evidence for this relationship                                           |

**Returns:**

```json
{ "relationship_id": 1 }
```

### get_relationships_tool

Get relationships for a paper.

| Parameter       | Type          | Required | Default  | Description                                                                                           |
| --------------- | ------------- | -------- | -------- | ----------------------------------------------------------------------------------------------------- |
| `paper_id`      | `int`         | yes      | --       | Paper ID to query relationships for                                                                   |
| `relation_type` | `str \| None` | no       | `None`   | Filter by type (`extends`, `contradicts`, `replicates`, `cites`, `compares`, `applies`, `implements`) |
| `direction`     | `str`         | no       | `"both"` | `outgoing`, `incoming`, or `both`                                                                     |

**Returns:** Array of relationship objects.

### export_bibtex_tool

Export papers as BibTeX for Typst citation workflow.

| Parameter       | Type                | Required | Default | Description                                                 |
| --------------- | ------------------- | -------- | ------- | ----------------------------------------------------------- |
| `paper_ids`     | `list[int] \| None` | no       | `None`  | Export specific papers by ID                                |
| `title_pattern` | `str \| None`       | no       | `None`  | Export papers matching title substring                      |
| `output_path`   | `str \| None`       | no       | `None`  | File path to write `.bib` file (returns content if omitted) |

`output_path` must have a `.bib` or `.bibtex` extension and resolve under the user's home directory or current working directory.

**Returns (no output_path):**

```json
{ "bibtex": "@article{...}\n", "entries": 3 }
```

**Returns (with output_path):**

```json
{ "written_to": "/home/user/refs.bib", "entries": 3 }
```

### sync_bibtex_tool

Append only new papers to an existing `.bib` file, skipping duplicates.

| Parameter       | Type                | Required | Default | Description                                           |
| --------------- | ------------------- | -------- | ------- | ----------------------------------------------------- |
| `output_path`   | `str`               | yes      | --      | Path to the `.bib` file (created if it doesn't exist) |
| `paper_ids`     | `list[int] \| None` | no       | `None`  | Sync specific papers by ID                            |
| `title_pattern` | `str \| None`       | no       | `None`  | Sync papers matching title substring                  |

**Returns:**

```json
{ "synced": 2, "skipped": 5, "output_path": "/home/user/refs.bib" }
```

### suggest_relationships_tool

Suggest citation relationships by matching DOIs, title words, and author+year in paper text.

| Parameter  | Type  | Required | Default | Description                                 |
| ---------- | ----- | -------- | ------- | ------------------------------------------- |
| `paper_id` | `int` | yes      | --      | Paper ID to analyze for citation references |

**Returns:** Object with `suggestions` (candidate relationships with confidence scores) and `unmatched_dois` (DOIs found in text that don't match any registered paper).

### scan_relationships

Scan for embedding-similarity relationships between papers. Submits `auto_relate` background jobs.

| Parameter  | Type          | Required | Default | Description                                             |
| ---------- | ------------- | -------- | ------- | ------------------------------------------------------- |
| `paper_id` | `int \| None` | no       | `None`  | Scan this paper only (1×M). If omitted, scan all (N×M). |

**Returns:**

```json
// Single paper
{ "job_id": 42 }
// Full scan
{ "jobs_submitted": 15 }
```

See [Auto-Relationship Discovery](../usage/auto-relationships.md) for algorithm details.

### relocate_paper_tool

Update a paper's filesystem path after moving/renaming the file. Updates all internal references so lookups continue to work.

| Parameter  | Type  | Required | Default | Description                       |
| ---------- | ----- | -------- | ------- | --------------------------------- |
| `paper_id` | `int` | yes      | --      | The paper to update               |
| `new_path` | `str` | yes      | --      | The new absolute path to the file |

**Returns:** Confirmation dict with updated path info.

### get_paper_paths_tool

List all registered filesystem paths for a paper.

| Parameter  | Type  | Required | Default | Description          |
| ---------- | ----- | -------- | ------- | -------------------- |
| `paper_id` | `int` | yes      | --      | The paper to look up |

**Returns:** Array of path records with `path`, `is_primary`, and `content_hash`.

---

## Conclusions

Tools for recording and querying evidence-chained analytical conclusions.

### record_conclusion_tool

Record an analytical conclusion with evidence links to source chunks.

| Parameter          | Type                | Required | Default | Description                                 |
| ------------------ | ------------------- | -------- | ------- | ------------------------------------------- |
| `claim`            | `str`               | yes      | --      | The conclusion claim text                   |
| `confidence`       | `float`             | no       | `1.0`   | Confidence score 0.0--1.0                   |
| `source_chunk_ids` | `list[int] \| None` | no       | `None`  | List of chunk IDs serving as evidence       |
| `session_context`  | `str \| None`       | no       | `None`  | Context about why this conclusion was drawn |

**Returns:**

```json
{ "conclusion_id": 1 }
```

### get_conclusions_tool

Search conclusions by keyword and confidence threshold.

| Parameter            | Type          | Required | Default | Description                                   |
| -------------------- | ------------- | -------- | ------- | --------------------------------------------- |
| `keyword`            | `str \| None` | no       | `None`  | Search term for claim text                    |
| `min_confidence`     | `float`       | no       | `0.0`   | Minimum confidence threshold                  |
| `include_superseded` | `bool`        | no       | `False` | Include conclusions that have been superseded |

**Returns:** Array of conclusion objects with `id`, `claim`, `confidence`, `source_chunk_ids`, `session_context`, `created_at`, and `superseded_by`.

### supersede_conclusion_tool

Supersede an old conclusion with a new one, maintaining the {term}`supersession` chain.

| Parameter           | Type                | Required | Default | Description                             |
| ------------------- | ------------------- | -------- | ------- | --------------------------------------- |
| `old_conclusion_id` | `int`               | yes      | --      | ID of the conclusion to supersede       |
| `new_claim`         | `str`               | yes      | --      | The updated conclusion claim            |
| `confidence`        | `float`             | no       | `1.0`   | Confidence score for the new conclusion |
| `source_chunk_ids`  | `list[int] \| None` | no       | `None`  | Updated evidence chunk IDs              |
| `session_context`   | `str \| None`       | no       | `None`  | Context for why the conclusion changed  |

**Returns:**

```json
{ "conclusion_id": 2, "supersedes": 1 }
```

### get_conclusion_chain_tool

Follow the {term}`supersession` chain for a conclusion (oldest to newest).

| Parameter       | Type  | Required | Default | Description                    |
| --------------- | ----- | -------- | ------- | ------------------------------ |
| `conclusion_id` | `int` | yes      | --      | Any conclusion ID in the chain |

**Returns:** Ordered array of conclusion objects from oldest to newest in the chain.

---

## Extraction

Tools for structured extraction of methods, datasets, metrics, and entities from papers.

### record_method_tool

Record a method used in a paper. Upserts on conflict (same name + paper).

| Parameter     | Type          | Required | Default | Description                                   |
| ------------- | ------------- | -------- | ------- | --------------------------------------------- |
| `name`        | `str`         | yes      | --      | Method name (e.g. `Transformer`, `ResNet-50`) |
| `paper_id`    | `int`         | yes      | --      | Paper that uses this method                   |
| `description` | `str \| None` | no       | `None`  | Brief description of the method               |

**Returns:**

```json
{ "method_id": 1 }
```

### record_dataset_tool

Record a dataset used in a paper. Upserts on conflict (same name + paper).

| Parameter     | Type          | Required | Default | Description                            |
| ------------- | ------------- | -------- | ------- | -------------------------------------- |
| `name`        | `str`         | yes      | --      | Dataset name (e.g. `ImageNet`, `GLUE`) |
| `paper_id`    | `int`         | yes      | --      | Paper that uses this dataset           |
| `description` | `str \| None` | no       | `None`  | Brief description of the dataset       |

**Returns:**

```json
{ "dataset_id": 1 }
```

### record_metric_tool

Record a metric value from a paper.

| Parameter    | Type          | Required | Default | Description                                 |
| ------------ | ------------- | -------- | ------- | ------------------------------------------- |
| `name`       | `str`         | yes      | --      | Metric name (e.g. `accuracy`, `F1`, `BLEU`) |
| `value`      | `float`       | yes      | --      | Numeric value of the metric                 |
| `paper_id`   | `int`         | yes      | --      | Paper reporting this metric                 |
| `method_id`  | `int \| None` | no       | `None`  | Method that achieved this metric            |
| `dataset_id` | `int \| None` | no       | `None`  | Dataset the metric was measured on          |
| `unit`       | `str \| None` | no       | `None`  | Unit of measurement (e.g. `%`, `ms`)        |

**Returns:**

```json
{ "metric_id": 1 }
```

### compare_papers_tool

Compare metrics across papers on shared datasets. Shows side-by-side results for papers that report metrics on the same datasets.

| Parameter   | Type        | Required | Default | Description                     |
| ----------- | ----------- | -------- | ------- | ------------------------------- |
| `paper_ids` | `list[int]` | yes      | --      | List of 2+ paper IDs to compare |

**Returns:** Object keyed by dataset name, with metric comparisons across papers.

### extract_structure_tool

Extract methods, datasets, and metrics from a paper using LLM ({term}`map-reduce extraction`).

For short papers, runs inline. For long papers (>2 min estimated), returns a warning with ETA -- call again with `confirmed=True` to queue a background job.

| Parameter   | Type   | Required | Default | Description                                           |
| ----------- | ------ | -------- | ------- | ----------------------------------------------------- |
| `paper_id`  | `int`  | yes      | --      | Paper ID to extract structure from                    |
| `confirmed` | `bool` | no       | `False` | Set `True` to skip the ETA warning for long documents |

**Returns (short document):** Extraction results inline.

**Returns (long document, not confirmed):**

```json
{
  "warning": "Extraction will take ~5min for 30 chunks",
  "estimated_seconds": 300,
  "chunk_count": 30,
  "confirm_required": true
}
```

**Returns (long document, confirmed):**

```json
{
  "deferred": true,
  "job_id": 1,
  "status": "pending",
  "message": "Use get_job_status(job_id) to poll progress."
}
```

### get_entities_tool

List resolved entities for a paper with their {term}`surface form`s and chunk mentions.

| Parameter  | Type  | Required | Default | Description                  |
| ---------- | ----- | -------- | ------- | ---------------------------- |
| `paper_id` | `int` | yes      | --      | Paper ID to get entities for |

**Returns:** Array of entity objects with `canonical_name`, `type`, `description`, and `mentions` (each with `surface_form`, `chunk_id`, `confidence`).

---

## Figures

Tools for vision-based figure extraction from PDFs.

### extract_figures_tool

Extract figures from a paper's PDF using a vision model. Renders candidate pages as images, sends them to the configured vision model, and stores figure descriptions as searchable `figure` chunks.

Always queues a background job (figure extraction involves PDF rendering + vision API calls). Returns an ETA warning first; call with `confirmed=True` to submit the job.

| Parameter   | Type                | Required | Default | Description                                               |
| ----------- | ------------------- | -------- | ------- | --------------------------------------------------------- |
| `paper_id`  | `int`               | yes      | --      | Paper ID                                                  |
| `pages`     | `list[int] \| None` | no       | `None`  | 1-based page numbers to process (auto-detects if omitted) |
| `confirmed` | `bool`              | no       | `False` | Skip ETA warning for long documents                       |

Pages are **1-based** in the API (converted to 0-based internally). Auto-detection uses a heuristic filter that checks for embedded images, vector drawings, low text density, and caption cues.

**Returns (not confirmed, >2 min):**

```json
{
  "confirm_required": true,
  "estimated_seconds": 180,
  "candidate_pages": 12
}
```

**Returns (confirmed):**

```json
{
  "deferred": true,
  "job_id": 1,
  "status": "pending",
  "message": "Use get_job_status(job_id) to poll progress."
}
```

---

## Configuration

Tools for configuring embedding models, LLM providers, vision models, and browser rendering.

### embed_config

Get current embedding model configuration (model name and dimension).

| Parameter | Type | Required | Default | Description |
| --------- | ---- | -------- | ------- | ----------- |
| _(none)_  | --   | --       | --      | --          |

**Returns:**

```json
{ "model": "bge-m3", "dim": 1024 }
```

### re_embed_tool

Re-embed all chunks with a new embedding model. Drops and recreates the vector table with new dimensions, then re-embeds all existing chunks. This is expensive -- use only when switching models.

| Parameter             | Type          | Required | Default | Description                                                                                                                                   |
| --------------------- | ------------- | -------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `model`               | `str`         | yes      | --      | Ollama model name (e.g. `mxbai-embed-large`, `nomic-embed-text`)                                                                              |
| `dim`                 | `int`         | yes      | --      | Embedding dimension for the new model                                                                                                         |
| `matryoshka_base_dim` | `int \| None` | no       | `None`  | Native model dimension for Matryoshka truncation. Embeds at this dim, truncates to `dim`, L2 re-normalizes. Must be > `dim`. MRL models only. |

**Returns:** Summary with chunk count and timing.

### configure_chunking

Configure the chunking strategy for PDF ingestion.

| Parameter  | Type          | Required | Default | Description                                                                      |
| ---------- | ------------- | -------- | ------- | -------------------------------------------------------------------------------- |
| `strategy` | `str \| None` | no       | `None`  | `mechanical` (8K, fixed-size) or `semantic` (32K, section-level). Omit to query. |

With no arguments, returns the current strategy. Non-PDF content always uses mechanical chunking regardless of this setting.

**Returns:**

```json
{ "chunk_strategy": "mechanical" }
```

### configure_llm_tool

Configure the LLM used for structured extraction. Runs a connectivity test after saving.

| Parameter  | Type          | Required | Default         | Description                                                              |
| ---------- | ------------- | -------- | --------------- | ------------------------------------------------------------------------ |
| `provider` | `str`         | no       | `"ollama"`      | `ollama` (native API) or `openai_compat` (OpenAI-compatible API)         |
| `base_url` | `str \| None` | no       | `None`          | Base URL (e.g. `http://192.168.1.41:1234`). Required for `openai_compat` |
| `model`    | `str`         | no       | `"qwen3.5:27b"` | Model name                                                               |
| `api_key`  | `str \| None` | no       | `None`          | Optional API key for authenticated endpoints                             |

**Returns:** Saved config (api_key redacted) plus connectivity test result.

### configure_vision_tool

Configure the vision model used for figure extraction.

| Parameter  | Type          | Required | Default | Description                                                 |
| ---------- | ------------- | -------- | ------- | ----------------------------------------------------------- |
| `model`    | `str \| None` | no       | `None`  | Vision model name (e.g. `gemma3:27b`, `llava:13b`)          |
| `base_url` | `str \| None` | no       | `None`  | Base URL for the vision API (e.g. `http://localhost:11434`) |

With no arguments, returns current configuration. Default vision model is `gemma3:27b`.

**Returns:**

```json
{ "model": "gemma3:27b", "base_url": "http://localhost:11434" }
```

### configure_omniparser_tool

Configure {term}`OmniParser` for figure enrichment. OmniParser adds OCR text and icon detection to figure descriptions.

| Parameter | Type          | Required | Default | Description                                                                     |
| --------- | ------------- | -------- | ------- | ------------------------------------------------------------------------------- |
| `path`    | `str \| None` | no       | `None`  | Absolute path to OmniParser directory. `None` to query, empty string to disable |

Requires a local OmniParser installation with `parse.py` and a `.venv/bin/python` in the specified directory.

**Returns:**

```json
{ "omniparser_path": "/path/to/OmniParser" }
```

### configure_browser_tool

Configure browser rendering for JS-heavy web pages. Enables fallback rendering when trafilatura extracts insufficient content from a URL (< 200 chars).

| Parameter      | Type          | Required | Default | Description                                                                                  |
| -------------- | ------------- | -------- | ------- | -------------------------------------------------------------------------------------------- |
| `cdp_endpoint` | `str \| None` | no       | `None`  | WebSocket CDP endpoint (`ws://` or `wss://`). Requires `venv_path` too                       |
| `venv_path`    | `str \| None` | no       | `None`  | Absolute path to Python venv with playwright installed. Pass both as empty string to disable |

Two modes (both require a venv with `playwright` installed):

- **CDP** (recommended for Docker): Connect to a running Playwright container. Provide both `cdp_endpoint` and `venv_path`.
- **Local**: Launch headless Chromium from the venv. Provide `venv_path` only.

With no arguments, returns current configuration.

**Returns:**

```json
{
  "browser": {
    "mode": "cdp",
    "endpoint": "ws://localhost:3000",
    "venv": "/path/to/venv"
  }
}
```

---

## Embedding Spaces

Tools for managing embedding spaces -- multiple embedding models coexisting with independent vec tables. See [Embedding Spaces](../usage/embedding-spaces.md) for the full workflow.

### list_embed_spaces_tool

List all embedding spaces with status, progress, and chunk strategy.

| Parameter | Type | Required | Default | Description |
| --------- | ---- | -------- | ------- | ----------- |
| _(none)_  | --   | --       | --      | --          |

**Returns:** Array of space objects with `name`, `model`, `provider`, `dim`, `chunk_strategy`, `status`, `table_name`, `created_at`, `chunk_count`, `total_chunks`.

### create_embed_space_tool

Create a new embedding space in `populating` status.

| Parameter             | Type          | Required | Default        | Description                                                                                                                                   |
| --------------------- | ------------- | -------- | -------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`                | `str`         | yes      | --             | Unique space name (alphanumeric + underscores only)                                                                                           |
| `model`               | `str`         | yes      | --             | Embedding model name (e.g. `bge-m3`, `qwen3-embedding`)                                                                                       |
| `dim`                 | `int`         | yes      | --             | Embedding dimension (e.g. 768, 1024)                                                                                                          |
| `provider`            | `str`         | yes      | --             | Embedding provider (`ollama`, `openai`, `onnx`)                                                                                               |
| `chunk_strategy`      | `str`         | no       | `"mechanical"` | Which chunks to embed: `mechanical` or `semantic` (from #100)                                                                                 |
| `matryoshka_base_dim` | `int \| None` | no       | `None`         | Native model dimension for Matryoshka truncation. Embeds at this dim, truncates to `dim`, L2 re-normalizes. Must be > `dim`. MRL models only. |

**Returns:**

```json
{
  "space": "bge_m3_1024",
  "table_name": "chunks_vec_bge_m3_1024",
  "status": "populating",
  "total_chunks": 1500
}
```

### backfill_embed_space_tool

Backfill an embedding space with chunk embeddings. Resumable -- interrupted backfills pick up where they left off.

| Parameter    | Type  | Required | Default | Description                                             |
| ------------ | ----- | -------- | ------- | ------------------------------------------------------- |
| `name`       | `str` | yes      | --      | Name of the space to backfill (must be in `populating`) |
| `batch_size` | `int` | no       | `32`    | Number of chunks per embedding batch                    |

**Returns:**

```json
{ "space": "bge_m3_1024", "chunks_processed": 1500, "total_chunks": 1500 }
```

### promote_embed_space_tool

Promote an embedding space to active. Deprecates the current active space and syncs config.

| Parameter | Type  | Required | Default | Description                  |
| --------- | ----- | -------- | ------- | ---------------------------- |
| `name`    | `str` | yes      | --      | Name of the space to promote |

**Returns:**

```json
{
  "promoted": "bge_m3_1024",
  "deprecated": "default",
  "note": "All 'similar' relationships removed (embedding space changed). Run scan_relationships() to recompute."
}
```

### deprecate_embed_space_tool

Mark an embedding space as deprecated.

| Parameter | Type  | Required | Default | Description                                             |
| --------- | ----- | -------- | ------- | ------------------------------------------------------- |
| `name`    | `str` | yes      | --      | Name of the space to deprecate (cannot be active space) |

**Returns:**

```json
{ "deprecated": "experimental_model" }
```

### cleanup_embed_space_tool

Drop a deprecated space's vec table and remove its registry entry.

| Parameter | Type  | Required | Default | Description                              |
| --------- | ----- | -------- | ------- | ---------------------------------------- |
| `name`    | `str` | yes      | --      | Name of the deprecated space to clean up |

**Returns:**

```json
{ "cleaned": "old_model_768" }
```

---

## Comparison

Tools for A/B comparison of embedding spaces. See [Embedding Spaces: Comparing spaces](../usage/embedding-spaces.md#comparing-spaces) for usage guidance.

### compare_spaces_tool

Compare search results for a query across two embedding spaces. Returns side-by-side results with overlap metrics and rank correlation.

| Parameter | Type  | Required | Default | Description                                                     |
| --------- | ----- | -------- | ------- | --------------------------------------------------------------- |
| `query`   | `str` | yes      | --      | Search query to compare                                         |
| `space_a` | `str` | yes      | --      | Name of the first embedding space                               |
| `space_b` | `str` | yes      | --      | Name of the second embedding space                              |
| `top_k`   | `int` | no       | `10`    | Number of results per space                                     |
| `mode`    | `str` | no       | `"vec"` | Search mode: `vec` (default for comparison), `hybrid`, or `fts` |

**Returns:**

```json
{
  "query": "attention mechanism",
  "space_a": {
    "name": "bge_m3_1024",
    "model": "bge-m3",
    "dim": 1024,
    "result_count": 10,
    "results": []
  },
  "space_b": {
    "name": "qwen3_512",
    "model": "qwen3-embedding",
    "dim": 512,
    "result_count": 10,
    "results": []
  },
  "metrics": {
    "overlap_count": 7,
    "overlap_at_k": 0.7,
    "jaccard": 0.5385,
    "rank_correlation": 0.8214
  },
  "warnings": []
}
```

Emits a cross-strategy warning when spaces use different `chunk_strategy` values.

### batch_compare_spaces_tool

Batch-compare two embedding spaces with multiple queries. Returns aggregated statistics.

| Parameter | Type        | Required | Default | Description                           |
| --------- | ----------- | -------- | ------- | ------------------------------------- |
| `space_a` | `str`       | yes      | --      | Name of the first embedding space     |
| `space_b` | `str`       | yes      | --      | Name of the second embedding space    |
| `queries` | `list[str]` | yes      | --      | List of search queries to compare     |
| `top_k`   | `int`       | no       | `10`    | Number of results per space per query |
| `mode`    | `str`       | no       | `"vec"` | Search mode (default `vec`)           |

**Returns:**

```json
{
  "space_a": "bge_m3_1024",
  "space_b": "qwen3_512",
  "queries_analyzed": 5,
  "overlap_at_k": { "mean": 0.72, "std": 0.15, "min": 0.5, "max": 0.9 },
  "jaccard": { "mean": 0.55, "std": 0.12, "min": 0.38, "max": 0.7 },
  "rank_correlation": {
    "mean": 0.81,
    "std": 0.1,
    "min": 0.65,
    "max": 0.95,
    "valid_count": 4
  },
  "warnings": []
}
```

---

## Jobs

Tools for monitoring background extraction jobs.

### get_job_status_tool

Get the status and progress of a background extraction job.

| Parameter | Type  | Required | Default | Description                                                           |
| --------- | ----- | -------- | ------- | --------------------------------------------------------------------- |
| `job_id`  | `int` | yes      | --      | Job ID returned by `extract_structure_tool` or `extract_figures_tool` |

**Returns:**

```json
{
  "id": 1,
  "paper_id": 5,
  "job_type": "extract_figures",
  "status": "running",
  "progress": "vision 12 pages...",
  "created_at": "2025-01-15 10:30:00",
  "started_at": "2025-01-15 10:30:01"
}
```

### list_jobs_tool

List background extraction jobs.

| Parameter  | Type          | Required | Default | Description                                                   |
| ---------- | ------------- | -------- | ------- | ------------------------------------------------------------- |
| `status`   | `str \| None` | no       | `None`  | Filter by status: `pending`, `running`, `completed`, `failed` |
| `paper_id` | `int \| None` | no       | `None`  | Filter by paper ID                                            |

**Returns:** Array of job objects.

---

## Prediction Errors

Tools for monitoring and resolving search quality issues. Prediction errors are logged automatically when `search_index` returns poor or empty results.

### list_prediction_errors_tool

List prediction errors (queries with low-confidence or missing results).

| Parameter         | Type          | Required | Default | Description                               |
| ----------------- | ------------- | -------- | ------- | ----------------------------------------- |
| `since`           | `str \| None` | no       | `None`  | ISO 8601 timestamp to filter errors after |
| `unresolved_only` | `bool`        | no       | `True`  | Only show unresolved errors               |

**Returns:** Array of prediction error objects:

```json
[
  {
    "id": 1,
    "query": "quantum error correction",
    "query_hash": "a1b2c3...",
    "top_score": null,
    "top_chunk_id": null,
    "error_type": "no_results",
    "source_type_filter": null,
    "detected_at": "2025-01-15 10:30:00",
    "resolved_at": null
  }
]
```

Rate-limited: at most 1 error per (query, error_type, source_type_filter) per hour. Configurable threshold via `prediction_error_threshold` config key (default `0.025`).

### resolve_prediction_error_tool

Mark a prediction error as resolved (e.g. after ingesting content that fills the gap).

| Parameter  | Type  | Required | Default | Description                           |
| ---------- | ----- | -------- | ------- | ------------------------------------- |
| `error_id` | `int` | yes      | --      | ID of the prediction error to resolve |

**Returns:**

```json
{ "resolved": 1 }
```
