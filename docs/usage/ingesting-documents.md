# Ingesting Documents

## PDF Ingestion

Use the `ingest` tool with a path to a PDF file. PDFs are processed through pymupdf4llm, which produces structured markdown preserving headings, tables, and image references. The output is chunked using header-aware splitting: sections stay together when possible, oversized sections are split at prose/table boundaries, and tiny sections are merged with their children.

```json
{ "name": "ingest", "arguments": { "path": "/home/user/papers/attention.pdf" } }
```

Page numbers are tracked per chunk and stored in chunk metadata. Extracted images are saved to `~/.local/share/knowledge-base/figures/<stem>_<hash>/extracted/`.

If pymupdf4llm is not installed, ingestion falls back to flat text extraction via PyMuPDF (`fitz`), which loses structural information.

## Markdown and Text Files

Markdown (`.md`), plain text (`.txt`), Typst (`.typ`), and reStructuredText (`.rst`) files are all read as plain text and chunked using fixed-size splitting: 1000 characters per chunk with 200-character overlap. No header-aware chunking is applied to these formats -- header-aware splitting is only used for PDFs (via pymupdf4llm's markdown output).

```json
{
  "name": "ingest",
  "arguments": { "path": "/home/user/notes/research-log.md" }
}
```

## Code Files

Python files (`.py`) use AST-aware chunking: the source is parsed with `ast.parse`, and each top-level function or class becomes its own chunk. Module-level code (imports, constants, etc.) is collected into a separate chunk. If a function or class exceeds the chunk size limit, it is split using fixed-size chunking. If `ast.parse` fails (syntax error), the file falls back to fixed-size chunking.

Other code files (`.rs`, `.cpp`, `.c`, `.h`, `.hpp`, `.toml`, `.yaml`, `.yml`, `.json`) use fixed-size chunking (1000 chars, 200 overlap).

```json
{ "name": "ingest", "arguments": { "path": "/home/user/src/model.py" } }
```

You can force a file to be treated as code (or any other type) with the `source_type` parameter:

```json
{
  "name": "ingest",
  "arguments": { "path": "/home/user/Makefile", "source_type": "code" }
}
```

## Web Pages

Use the `ingest_url` tool. Content is extracted with trafilatura (strips boilerplate, extracts main text and tables).

```json
{
  "name": "ingest_url",
  "arguments": { "url": "https://arxiv.org/abs/2301.00001" }
}
```

If trafilatura extracts fewer than 200 characters (likely a JS-heavy page), and browser rendering is configured, the page is rendered via Playwright and re-extracted. Browser rendering also captures a screenshot that can be processed by the vision pipeline for figure extraction. See [Figure Extraction](figure-extraction.md) for vision configuration.

Browser rendering requires explicit configuration via `configure_browser_tool`. See the tool reference for setup instructions.

### Inline Image Extraction

When a vision model is configured (see [Figure Extraction](figure-extraction.md)), `ingest_url` also extracts inline `<img>` tags from the HTML. Each qualifying image is downloaded, sent to the vision model for description, and stored as a figure chunk (`source_type='figure'`, `figure_type='web_image'`).

Filtering heuristics skip non-content images:

- **Decorative patterns** -- URLs or alt text containing logo, icon, avatar, favicon, banner, sprite, spacer, tracking pixel, or badge
- **Small images** -- Width or height below 100px (checked first from HTML attributes to avoid unnecessary downloads, then verified from actual pixel dimensions after download)
- **Non-raster formats** -- SVG and data URI images are excluded
- **SSRF protection** -- Image URLs are validated against private/loopback IP ranges, including post-redirect targets

Up to 10 images per page are processed, with a 10 MB cap per image download.

Only `http` and `https` URLs are accepted.

### Rendered DOM Image Extraction

When browser fallback fires, the rendered DOM is also parsed for `<img>` tags that may not appear in the static HTML (e.g., images injected by JavaScript). These are deduplicated against images found in the static HTML to avoid duplicate figure descriptions.

### Canvas and SVG Element Capture

Some pages render meaningful visualizations via `<canvas>` (D3.js charts, WebGL diagrams) or complex `<svg>` elements that cannot be extracted as `<img>` tags. During browser fallback, Playwright captures per-element screenshots for qualifying `<canvas>` and `<svg>` elements:

- **Size filter** -- Elements smaller than 80×80 pixels are skipped (likely icons or decorations)
- **Visibility filter** -- Elements outside the viewport or with zero area are skipped
- **Cap** -- Up to 10 element captures per page

Each captured element is sent individually to the vision model for description, producing focused descriptions rather than asking the model to parse a dense full-page screenshot. Captures are stored as figure chunks with `source_type='figure'` and metadata including `element_tag` (`"canvas"` or `"svg"`), element dimensions, and the vision model's `figure_type` classification.

Element capture requires both browser rendering and a vision model to be configured.

## Directory Ingestion

Pass a directory path to `ingest`. It recursively walks the directory (`rglob("*")`) and ingests files with extensions: `.pdf`, `.md`, `.txt`, `.typ`, `.rst`. Code files like `.py` are not included in directory walks -- ingest them individually.

```json
{ "name": "ingest", "arguments": { "path": "/home/user/papers/" } }
```

The response includes per-file results:

```json
{
  "files_processed": 3,
  "chunks_added": 47,
  "chunks_skipped": 0,
  "details": [
    {
      "file": "/home/user/papers/paper1.pdf",
      "chunks_added": 22,
      "chunks_skipped": 0
    },
    {
      "file": "/home/user/papers/paper2.pdf",
      "chunks_added": 15,
      "chunks_skipped": 0
    },
    {
      "file": "/home/user/papers/notes.md",
      "chunks_added": 10,
      "chunks_skipped": 0
    }
  ]
}
```

## Re-Ingestion

Use the `reingest` tool to force a full re-ingest. This deletes all existing chunks for the file and reinserts from scratch -- it does not compare content hashes. Use this after a file has been updated.

```json
{
  "name": "reingest",
  "arguments": { "path": "/home/user/papers/attention.pdf" }
}
```

`reingest` also cleans up foreign key references: it nullifies `papers.abstract_chunk_id`, `relationships.evidence_chunk_id`, and `methods/datasets/metrics.chunk_id` for affected chunks, removes stale `entity_mentions`, and re-links papers and entities to the new chunks after insertion.

## Chunking Strategy

Two chunking strategies are available for PDFs, selectable via the `configure_chunking` tool:

| Strategy       | Target models | PDF behavior                                              | Typical chunks/paper |
| -------------- | ------------- | --------------------------------------------------------- | -------------------- |
| **mechanical** | 8K context    | Fixed-size (1000 chars, 200 overlap), heading-aware split | 15+                  |
| **semantic**   | 32K context   | Section-level split at H1/H2 boundaries, no overlap       | 3--5                 |

**Why two strategies?** Models like Qwen3-Embedding-0.6B have 32K context windows, making 1000-char chunks unnecessarily granular. Semantic chunking produces complete sections (Abstract, Methods, Results, etc.) as individual chunks, eliminating boundary artifacts and overlap waste. See [#100](https://github.com/dutiona/knowledge-base/issues/100) for the design rationale.

**How it works:** When `chunk_strategy` is set to `'semantic'`, PDF ingestion splits at `#`/`##` heading boundaries. Oversized sections fall back to `###` sub-headings, then paragraph boundaries. Abstract always gets its own chunk. References always get their own chunk. Tables are never split. Non-PDF content (markdown, code, web) always uses mechanical chunking regardless of this setting.

```json
{ "name": "configure_chunking", "arguments": { "strategy": "semantic" } }
```

**Default:** `'mechanical'` -- all existing behavior is preserved. Switch to `'semantic'` after configuring a 32K embedding model. After switching, use `reingest` to re-chunk existing documents.

**Per-source-type summary:**

| Source type    | Chunking method        | Details                                                                                        |
| -------------- | ---------------------- | ---------------------------------------------------------------------------------------------- |
| PDF            | Mechanical or semantic | Mechanical: heading-aware 1000-char split. Semantic: section-level H1/H2 split (configurable). |
| Python (`.py`) | AST-aware              | Each function/class = one chunk; module-level code separate; oversized nodes sub-split         |
| All other text | Fixed-size             | 1000 characters, 200-character overlap                                                         |
| Web            | Fixed-size             | Same as other text, applied to trafilatura output                                              |

## Deduplication

On normal `ingest`, each chunk is SHA-256 hashed (truncated to 16 hex chars). If a chunk with the same hash already exists in the database, it is skipped. This makes ingestion idempotent: ingesting the same file twice adds zero new chunks.

On `reingest`, all existing chunks for the source URI are deleted unconditionally, then new chunks are inserted. No hash comparison occurs -- it is a force-replace operation.

## Session Tracking (Co-occurrence)

When documents are ingested together, they share a **session ID** that captures their co-occurrence -- a behavioral signal about research context at ingestion time. This complements embedding similarity (#105): similarity finds documents that _say similar things_, while co-ingestion finds documents that _entered the system together_.

**Why:** Inspired by [DiffMem](https://github.com/Growth-Kinetics/DiffMem) (MIT). DiffMem's insight is that entity files modified in the same git commit encode contextual relationships invisible to keyword or vector search. Session tracking applies this principle to ingestion: documents researched in the same sitting share implicit context that no query on their content would surface.

**How it works:**

- **Directory ingestion** automatically generates a shared `session_id` for all files in the directory. No parameter needed.
- **File and URL ingestion** accept an optional `session_id` parameter. Pass the same ID across multiple calls in one research session to mark co-occurrence.
- **Single-file ingestion** without a `session_id` generates a unique ID per call. Single-document sessions produce no co-occurrence signal, which is correct -- no false positives.

```json
{
  "name": "ingest",
  "arguments": {
    "path": "/home/user/papers/paper1.pdf",
    "session_id": "my-session-1"
  }
}
```

```json
{
  "name": "ingest",
  "arguments": {
    "path": "/home/user/papers/paper2.pdf",
    "session_id": "my-session-1"
  }
}
```

These two papers now share a session. Query co-occurrence with the `co_occurrence` tool:

```json
{ "name": "co_occurrence", "arguments": { "min_sessions": 1 } }
```

Returns document pairs ordered by number of shared sessions. Pairs that co-occur across multiple sessions get stronger signals.

**Deduplication handling:** Session associations are stored in a `chunk_sessions` join table with N:M cardinality (#139). When content-hash deduplication skips an existing chunk, the new session is still recorded via `INSERT OR IGNORE` into `chunk_sessions`. This means re-ingesting the same file in a different session correctly produces co-occurrence signals across both sessions. The `reingest` operation also preserves all historical session associations when replacing chunks with updated content.

## Embedding

Chunks are embedded automatically during ingestion using the configured embedding provider and model. Embeddings are stored in the **active embedding space's** vec table (typically `chunks_vec` for the default space, or `chunks_vec_<name>` for named spaces). The model, dimension, and provider can be checked with the `embed_config` tool. To switch models, use `re_embed_tool` (convenience wrapper) or the granular space lifecycle tools -- see [Embedding Spaces](embedding-spaces.md) for the full workflow.

### Embedding Providers

Pluggable embedding providers decouple the knowledge base from any single inference backend. This design was motivated by competitive analysis (NornicDB supports Ollama, OpenAI, and local ONNX inference) and aligns with the [four-layer cognitive architecture](../insights/four-layer-cognitive-architecture.md) — embeddings sit in the Knowledge (infrastructure) layer where swappable backends enable cost/quality/latency trade-offs without touching higher layers.

By default, knowledge-base uses **Ollama** (BGE-M3, 1024 dimensions). Providers are organized
by **API family**, not vendor (per [ADR-0018](../design/adr/0018-provider-abstraction.md)):

| Family            | Config value       | Endpoint                   | Covers (non-exhaustive)                                |
| ----------------- | ------------------ | -------------------------- | ------------------------------------------------------ |
| OpenAI-compatible | `openai_compat`    | `{base_url}/v1/embeddings` | OpenAI, vLLM, LM Studio, OpenRouter, Ollama-Cloud, TEI |
| Ollama            | `ollama` (default) | `{base_url}/api/embed`     | native Ollama (auto-detected URL / `OLLAMA_HOST`)      |
| ONNX Runtime      | `onnx`             | local `InferenceSession`   | local ONNX models (`uv sync --group onnx`)             |

`openai_compat` is the workhorse: it reaches **any** OpenAI-compatible embedding endpoint by
`base_url` — no per-vendor code. (`anthropic_compat` is chat-only; Anthropic has no embeddings
API, so it is rejected for embeddings.) `openai` remains a deprecated alias for the OpenAI
literal endpoint.

### Configuring the embedding provider

Use the **`configure_embeddings`** tool (mirrors `configure_llm`) — it validates the `base_url`,
runs a connectivity probe, and stores the config in the DB (selection now lives in `config`,
not env vars):

```json
// A hosted OpenAI-compatible embedder (e.g. vLLM / TEI / OpenRouter):
{ "name": "configure_embeddings_tool",
  "arguments": {
    "provider": "openai_compat",
    "base_url": "https://my-vllm.example.com",
    "model": "Qwen/Qwen3-Embedding-0.6B",
    "api_key": "env:VLLM_API_KEY"          // env:VARNAME — resolved at call time, never stored
  } }

// A LOCAL OpenAI-compatible server (vLLM/LM Studio/TEI on localhost):
{ "name": "configure_embeddings_tool",
  "arguments": {
    "provider": "openai_compat",
    "base_url": "http://localhost:11434/v1",
    "model": "bge-m3",
    "allow_loopback_base_url": true        // REQUIRED to permit a loopback/localhost base_url
  } }
```

After switching providers or models, re-embed existing chunks with `re_embed_tool`. A provider
swap that changes the **family** or **model** without creating a new space is hard-rejected at
ingest (the active space's recorded identity is authoritative) — a `base_url` change within the
same family (e.g. TEI → vLLM, both `openai_compat`, same model) is allowed.

**API keys.** Prefer the `env:VARNAME` indirection (the secret is read from the environment at
call time and never written to the DB). An inline key is stored plaintext-at-rest in the
`config` table — acceptable for a local single-user setup; keyring hardening is deferred.

**SSRF safety.** Every `openai_compat` `base_url` is validated (scheme, private/link-local/
metadata/DNS-rebinding rejected) before it is persisted and on each request (cross-host
redirects are refused). A loopback IP literal or the exact name `localhost` is permitted **only**
with `allow_loopback_base_url=true`; a hostname that merely _resolves_ to loopback
(`127.0.0.1.nip.io`) stays rejected.

### Environment Variable Override (deprecated)

The legacy `EMBED_PROVIDER` / `OPENAI_API_KEY` env vars still apply **while `embed_provider` is
at its seeded default** (an explicit `configure_embeddings` choice wins), with a one-time
deprecation warning. Prefer `configure_embeddings`; env back-compat will be removed in a future
release.
