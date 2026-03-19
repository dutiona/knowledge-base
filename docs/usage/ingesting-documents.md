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

Only `http` and `https` URLs are accepted.

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

| Source type    | Chunking method       | Details                                                                                              |
| -------------- | --------------------- | ---------------------------------------------------------------------------------------------------- |
| PDF            | Header-aware markdown | pymupdf4llm output split at heading boundaries, tables preserved whole, oversized sections sub-split |
| Python (`.py`) | AST-aware             | Each function/class = one chunk; module-level code separate; oversized nodes sub-split               |
| All other text | Fixed-size            | 1000 characters, 200-character overlap                                                               |
| Web            | Fixed-size            | Same as other text, applied to trafilatura output                                                    |

## Deduplication

On normal `ingest`, each chunk is SHA-256 hashed (truncated to 16 hex chars). If a chunk with the same hash already exists in the database, it is skipped. This makes ingestion idempotent: ingesting the same file twice adds zero new chunks.

On `reingest`, all existing chunks for the source URI are deleted unconditionally, then new chunks are inserted. No hash comparison occurs -- it is a force-replace operation.

## Embedding

Chunks are embedded automatically during ingestion using the configured embedding provider and model. Embeddings are stored in the `chunks_vec` virtual table. The model, dimension, and provider can be checked with the `embed_config` tool and changed with `re_embed_tool` (which re-embeds all existing chunks).

### Embedding Providers

By default, knowledge-base uses **Ollama** (BGE-M3, 1024 dimensions). Three providers are supported:

| Provider     | Config value       | Requirements                                            |
| ------------ | ------------------ | ------------------------------------------------------- |
| Ollama       | `ollama` (default) | Ollama running locally or via `OLLAMA_HOST`             |
| OpenAI       | `openai`           | `OPENAI_API_KEY` env var                                |
| ONNX Runtime | `onnx`             | `ONNX_EMBED_MODEL_PATH` env var, `uv sync --group onnx` |

To switch providers, update the config table:

```sql
-- Switch to OpenAI
UPDATE config SET value = 'openai' WHERE key = 'embed_provider';
UPDATE config SET value = 'text-embedding-3-large' WHERE key = 'embed_model';
UPDATE config SET value = '3072' WHERE key = 'embed_dim';
```

After switching providers or models, re-embed existing chunks with `re_embed_tool`.

### Environment Variable Override

Set `EMBED_PROVIDER=openai` (or `onnx`) to override the database config without modifying it. Useful for CI/dev environments.
