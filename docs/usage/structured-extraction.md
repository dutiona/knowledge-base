# Structured Extraction

## What Gets Extracted

The extraction pipeline identifies three types of structured information from paper text:

- **Methods** -- algorithms, models, and techniques (e.g., "Transformer", "ResNet-50", "Adam optimizer")
- **Datasets** -- evaluation benchmarks and training data (e.g., "ImageNet", "GLUE", "WMT14")
- **Metrics** -- quantitative results with values, units, and links to methods and datasets (e.g., accuracy = 76.3%)

Additionally, the pipeline creates **entities** -- resolved canonical names with their surface forms and chunk mentions. This enables tracking how the same method/dataset is referred to across different sections of a paper.

## Map-Reduce Architecture

Document length determines the extraction strategy:

**Short documents (<8000 total chars):** A single LLM call extracts all methods, datasets, and metrics at once.

**Long documents (>= 8000 total chars):** Three-phase map-reduce:

1. **Map** -- Each chunk is sent to the LLM independently. The prompt asks for methods (with surface_forms/aliases), datasets (with surface_forms), and metrics. Each extraction result is tagged with its source chunk_id for provenance.

2. **Resolve** -- All extracted entity mentions are collected and sent to the LLM in a single call. The resolution prompt asks the LLM to group mentions that refer to the same real-world entity, choosing the most specific/formal name as canonical. Entity resolution applies to **methods and datasets only**; metrics bypass resolution and are attributed to their resolved method/dataset via surface form lookup.

3. **Store** -- Resolved entities are written atomically. Previous extraction data for the paper is cleared first (idempotent). Methods and datasets are inserted into their respective tables, entity mentions are recorded with chunk provenance, and metrics are linked to their resolved method and dataset IDs.

The entire store phase is wrapped in a transaction with rollback on failure.

## ETA and Background Jobs

Before running extraction, the tool estimates processing time based on chunk count (approximately 4 seconds per chunk). If the estimate exceeds 2 minutes, the tool returns a warning:

```json
{
  "warning": "Extraction will take ~5min for 78 chunks",
  "estimated_seconds": 312,
  "chunk_count": 78,
  "confirm_required": true
}
```

Call again with `confirmed: true` to submit a background job:

```json
{
  "name": "extract_structure_tool",
  "arguments": { "paper_id": 1, "confirmed": true }
}
```

The job runs on a background worker thread. Poll progress with:

```json
{ "name": "get_job_status_tool", "arguments": { "job_id": 42 } }
```

Short documents run inline (no background job).

## Manual Recording

You can record methods, datasets, and metrics manually without LLM extraction:

```json
{
  "name": "record_method_tool",
  "arguments": {
    "name": "Vision Transformer",
    "paper_id": 1,
    "description": "Applies transformer architecture to image patches"
  }
}
```

```json
{
  "name": "record_dataset_tool",
  "arguments": {
    "name": "ImageNet-1K",
    "paper_id": 1,
    "description": "1000-class image classification benchmark"
  }
}
```

```json
{
  "name": "record_metric_tool",
  "arguments": {
    "name": "top-1 accuracy",
    "value": 86.4,
    "paper_id": 1,
    "method_id": 3,
    "dataset_id": 5,
    "unit": "%"
  }
}
```

Methods and datasets upsert on `(name, paper_id)` conflict -- re-recording the same name updates the description. Metrics always insert (no upsert).

## Cross-Paper Comparison

Compare metrics across papers that share datasets:

```json
{
  "name": "compare_papers_tool",
  "arguments": { "paper_ids": [1, 2, 3] }
}
```

Returns results grouped by dataset, showing each paper's metrics on that dataset. Only datasets appearing in 2+ of the requested papers are included.

## Entity Inspection

View resolved entities for a paper, including all surface forms and the chunks where each was mentioned:

```json
{
  "name": "get_entities_tool",
  "arguments": { "paper_id": 1 }
}
```

Returns a list of entities with their canonical name, type (method/dataset/metric), description, and mentions (surface_form, chunk_id, confidence).

## LLM Configuration

Configure the LLM used for structured extraction:

```json
{
  "name": "configure_llm_tool",
  "arguments": {
    "provider": "ollama",
    "model": "qwen3.5:27b"
  }
}
```

Two providers are supported:

- **`ollama`** -- Uses the native Ollama `/api/generate` endpoint. Base URL is auto-detected (OLLAMA_HOST env, WSL2 gateway, or localhost:11434).
- **`openai_compat`** -- Uses the OpenAI `/v1/chat/completions` endpoint. Requires `base_url`. Supports optional `api_key` for authenticated endpoints.

```json
{
  "name": "configure_llm_tool",
  "arguments": {
    "provider": "openai_compat",
    "base_url": "http://192.168.1.41:1234",
    "model": "qwen/qwen3.5-35b-a3b"
  }
}
```

Configuration is saved to the database first, then an advisory connectivity probe runs. If the probe fails, a warning is returned but the config is **not** rolled back -- the saved settings remain. This lets you configure an endpoint before it comes online.

The API key, if provided, is stored as plain text in the SQLite config table. This is acceptable for local use but not suitable for network-exposed deployments.
