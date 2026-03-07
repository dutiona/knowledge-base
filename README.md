# research-index

Hybrid semantic search MCP server for research papers, code, and notes. Ingests documents into a local SQLite database with FTS5 full-text search and sqlite-vec vector similarity, then exposes them as MCP tools for AI assistants.

## What it solves

- **Persistent knowledge base** — ingest PDFs, markdown, code, and web pages into a single searchable index that persists across sessions
- **Hybrid search** — combines BM25 keyword search (FTS5) with cosine vector similarity (sqlite-vec), merged via Reciprocal Rank Fusion
- **Paper management** — register papers with metadata, track relationships (extends, contradicts, replicates), export BibTeX
- **Structured extraction** — LLM-powered extraction of methods, datasets, and metrics from paper text; cross-paper comparison on shared datasets
- **Map-reduce for long documents** — structured extraction handles documents of any size by splitting into chunks, extracting per-chunk, then merging with entity resolution
- **Configurable LLM backend** — use Ollama natively or any OpenAI-compatible endpoint (e.g. LM Studio, vLLM)
- **Embedding model flexibility** — swap embedding models and re-embed all chunks without data loss

## What it does not solve

- No cloud sync — the index is a local SQLite database
- No PDF layout analysis — uses PyMuPDF text extraction (works well for text-heavy papers, not for tables/figures)
- No automatic paper discovery — you ingest documents manually or via URL
- Structured extraction depends on an LLM (Ollama or OpenAI-compatible) — quality varies with model capability

## Architecture

```
┌─────────────────────────────────────────────┐
│              MCP Client (Claude, etc.)       │
└──────────────────┬──────────────────────────┘
                   │ MCP protocol
┌──────────────────▼──────────────────────────┐
│              FastMCP Server (24 tools)        │
├──────────────────────────────────────────────┤
│  ingest.py     │ search.py    │ papers.py    │
│  embed_swap.py │ extraction.py│ conclusions.py│
├──────────────────────────────────────────────┤
│  embeddings.py (Ollama nomic-embed-text)     │
├──────────────────────────────────────────────┤
│  SQLite + FTS5 + sqlite-vec                  │
└──────────────────────────────────────────────┘
```

## Getting started

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- [Ollama](https://ollama.ai/) running locally (or on a Windows host for WSL2)
  - `ollama pull nomic-embed-text` for embeddings
  - `ollama pull qwen3.5:27b` for structured extraction (optional, or use any OpenAI-compatible endpoint)

### Install

```bash
git clone https://github.com/dutiona/research-index.git
cd research-index
uv sync
```

### Run the server

```bash
uv run research-index
```

Or register as an MCP server in your client's config (e.g. Claude Code `settings.json`):

```json
{
  "mcpServers": {
    "research-index": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/path/to/research-index",
        "research-index"
      ]
    }
  }
}
```

### Run tests

```bash
uv run pytest tests/ -q
```

## MCP Tools

### Ingest

| Tool         | Description                                                                       |
| ------------ | --------------------------------------------------------------------------------- |
| `ingest`     | Ingest a local file (PDF, markdown, code)                                         |
| `reingest`   | Re-ingest a previously ingested file (deletes old chunks, preserves FK integrity) |
| `ingest_url` | Fetch and ingest a web page via trafilatura                                       |

### Search

| Tool           | Description                                                                    |
| -------------- | ------------------------------------------------------------------------------ |
| `search_index` | Hybrid search (FTS5 + vector), returns ranked chunks with context              |
| `status`       | Database statistics (chunks, papers, methods, datasets, metrics, embed config) |

### Papers

| Tool                         | Description                                                      |
| ---------------------------- | ---------------------------------------------------------------- |
| `register_paper_tool`        | Register a paper with title, authors, year, venue, DOI           |
| `get_paper_tool`             | Look up papers by ID, title, or DOI                              |
| `add_relationship_tool`      | Record relationships between papers (extends, contradicts, etc.) |
| `get_relationships_tool`     | Get all relationships for a paper                                |
| `export_bibtex_tool`         | Export BibTeX for one or all papers                              |
| `suggest_relationships_tool` | Suggest relationships based on vector similarity                 |

### Conclusions

| Tool                        | Description                                                    |
| --------------------------- | -------------------------------------------------------------- |
| `record_conclusion_tool`    | Record a research conclusion with confidence and source chunks |
| `get_conclusions_tool`      | List conclusions, optionally filtered by active-only           |
| `supersede_conclusion_tool` | Mark a conclusion as superseded by a new one                   |
| `get_conclusion_chain_tool` | Trace a conclusion's supersession chain                        |

### Structured Extraction

| Tool                     | Description                                                                                                                                                    |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `record_method_tool`     | Record a method for a paper                                                                                                                                    |
| `record_dataset_tool`    | Record a dataset for a paper                                                                                                                                   |
| `record_metric_tool`     | Record a metric value (links to method + dataset)                                                                                                              |
| `compare_papers_tool`    | Compare metrics across papers on shared datasets                                                                                                               |
| `extract_structure_tool` | LLM-powered extraction of methods, datasets, and metrics via map-reduce (handles any document size; pass `confirmed=True` to skip ETA warning for long papers) |
| `get_entities_tool`      | List resolved entities for a paper with their surface forms and chunk mentions                                                                                 |

### Embedding Management

| Tool            | Description                                                         |
| --------------- | ------------------------------------------------------------------- |
| `embed_config`  | Show current embedding model and dimension                          |
| `re_embed_tool` | Swap embedding model and re-embed all chunks (atomic, no data loss) |

### LLM Configuration

| Tool                 | Description                                                                 |
| -------------------- | --------------------------------------------------------------------------- |
| `configure_llm_tool` | Set the LLM backend for structured extraction (`ollama` or `openai_compat`) |

## Database

The index is stored at `~/.local/share/research-index/research.db` by default. Tables:

- `chunks` — document content with content-hash deduplication
- `chunks_fts` — FTS5 full-text index (auto-synced via triggers)
- `chunks_vec` — sqlite-vec vector index
- `papers` — paper metadata
- `relationships` — inter-paper relationships
- `conclusions` — research conclusions with supersession chains
- `methods` / `datasets` / `metrics` — structured extraction results
- `config` — key-value store (current embedding model + dimension, LLM settings)

### Config keys

| Key            | Default            | Description                                                                |
| -------------- | ------------------ | -------------------------------------------------------------------------- |
| `embed_model`  | `nomic-embed-text` | Ollama embedding model name                                                |
| `embed_dim`    | `768`              | Embedding vector dimension                                                 |
| `llm_provider` | `ollama`           | LLM provider: `ollama` (native API) or `openai_compat` (OpenAI-compatible) |
| `llm_model`    | `qwen3.5:27b`      | Model name passed to the provider                                          |
| `llm_base_url` | _(unset)_          | Base URL for `openai_compat` provider (e.g. `http://192.168.1.41:1234`)    |

## Limitations

- Single-user, single-process (SQLite WAL mode, no concurrent writers)
- AST-aware chunking only for Python files; other code uses fixed-size chunks
- Web ingest validates URL scheme (http/https) but does not block private IP ranges
- Embedding model swap re-embeds all chunks sequentially (can be slow for large indexes)
