# knowledge-base

Hybrid semantic search MCP server for research papers, code, and notes. Ingests documents into a local SQLite database with FTS5 full-text search and sqlite-vec vector similarity, then exposes them as MCP tools for AI assistants.

Part of a [four-layer cognitive architecture](https://github.com/dutiona/autonomous-agent-project) research project. Companion to [memory-engine](https://github.com/dutiona/memory-engine) (Rust crate, Memory layer).

## What it solves

- **Persistent knowledge base** — ingest PDFs, markdown, code, and web pages into a single searchable index that persists across sessions
- **Hybrid search** — combines BM25 keyword search (FTS5) with cosine vector similarity (sqlite-vec), merged via Reciprocal Rank Fusion, with stage-2 reranking
- **Paper management** — register papers with metadata, track relationships (extends, contradicts, replicates), export/sync BibTeX
- **Structured extraction** — LLM-powered extraction of methods, datasets, metrics, and entities from paper text; cross-paper comparison on shared datasets
- **Map-reduce for long documents** — structured extraction handles documents of any size by splitting into chunks, extracting per-chunk, then merging with entity resolution
- **Multi-space embeddings** — maintain multiple embedding spaces simultaneously, compare retrieval quality, promote/deprecate spaces without data loss
- **Dual chunking** — 8K + 32K chunk strategies for fine-grained and broad-context retrieval
- **Auto-relationship discovery** — similarity-based detection of cross-paper connections
- **Folder-level semantic embeddings** — context boosting from folder summaries
- **Keyword intent extraction** — pre-filter search queries for better precision
- **Ingestion session tracking** — co-occurrence signals for retrieval ranking
- **Configurable LLM backend** — use Ollama natively or any OpenAI-compatible endpoint (e.g. LM Studio, vLLM)
- **Embedding model flexibility** — swap embedding models and re-embed all chunks without data loss

## What it does not solve

- No cloud sync — the index is a local SQLite database
- No automatic paper discovery — you ingest documents manually or via URL
- Structured extraction depends on an LLM (Ollama or OpenAI-compatible) — quality varies with model capability

## Architecture

```
┌─────────────────────────────────────────────┐
│              MCP Client (Claude, etc.)       │
└──────────────────┬──────────────────────────┘
                   │ MCP protocol
┌──────────────────▼──────────────────────────┐
│              FastMCP Server (46 tools)        │
├──────────────────────────────────────────────┤
│  ingestion.py  │ search.py    │ papers.py    │
│  embeddings.py │ extraction.py│ operations.py│
├──────────────────────────────────────────────┤
│  embeddings.py (pluggable: Ollama, OpenAI)   │
├──────────────────────────────────────────────┤
│  SQLite + FTS5 + sqlite-vec                  │
└──────────────────────────────────────────────┘
```

## Getting started

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- [Ollama](https://ollama.ai/) running locally (or on a Windows host for WSL2)
  - `ollama pull bge-m3` for embeddings
  - `ollama pull qwen3.5:27b` for structured extraction (optional, or use any OpenAI-compatible endpoint)

### Install

```bash
git clone https://github.com/dutiona/knowledge-base.git
cd knowledge-base
uv sync
```

### Run the server

```bash
uv run knowledge-base
```

Or register as an MCP server in your client's config (e.g. Claude Code `settings.json`):

```json
{
  "mcpServers": {
    "knowledge-base": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/path/to/knowledge-base",
        "knowledge-base"
      ]
    }
  }
}
```

### Run tests

```bash
uv run pytest tests/ -q
```

## MCP Tools (46)

### Ingest (5)

| Tool                     | Description                                                                       |
| ------------------------ | --------------------------------------------------------------------------------- |
| `ingest`                 | Ingest a local file (PDF, markdown, code)                                         |
| `reingest`               | Re-ingest a previously ingested file (deletes old chunks, preserves FK integrity) |
| `ingest_url`             | Fetch and ingest a web page via trafilatura (with SSRF protection)                |
| `configure_chunking`     | Configure chunking strategy parameters                                            |
| `configure_browser_tool` | Configure browser-based web ingestion                                             |

### Search (4)

| Tool                 | Description                                                                    |
| -------------------- | ------------------------------------------------------------------------------ |
| `search_index`       | Hybrid search (FTS5 + vector + stage-2 reranking), returns ranked chunks       |
| `status`             | Database statistics (chunks, papers, methods, datasets, metrics, embed config) |
| `scan_relationships` | Discover relationships across papers via embedding similarity                  |
| `co_occurrence`      | Find papers that co-occur in ingestion sessions                                |

### Papers (9)

| Tool                         | Description                                                      |
| ---------------------------- | ---------------------------------------------------------------- |
| `register_paper_tool`        | Register a paper with title, authors, year, venue, DOI           |
| `get_paper_tool`             | Look up papers by ID, title, or DOI                              |
| `add_relationship_tool`      | Record relationships between papers (extends, contradicts, etc.) |
| `get_relationships_tool`     | Get all relationships for a paper                                |
| `export_bibtex_tool`         | Export BibTeX for one or all papers                              |
| `sync_bibtex_tool`           | Sync paper metadata from a .bib file                             |
| `suggest_relationships_tool` | Suggest relationships based on vector similarity                 |
| `relocate_paper_tool`        | Move a paper's associated file to a new path                     |
| `get_paper_paths_tool`       | List file paths associated with papers                           |

### Structured Extraction (10)

| Tool                        | Description                                                                                                                                                    |
| --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `extract_structure_tool`    | LLM-powered extraction of methods, datasets, and metrics via map-reduce (handles any document size; pass `confirmed=True` to skip ETA warning for long papers) |
| `record_method_tool`        | Record a method for a paper                                                                                                                                    |
| `record_dataset_tool`       | Record a dataset for a paper                                                                                                                                   |
| `record_metric_tool`        | Record a metric value (links to method + dataset)                                                                                                              |
| `compare_papers_tool`       | Compare metrics across papers on shared datasets                                                                                                               |
| `get_entities_tool`         | List resolved entities for a paper with their surface forms and chunk mentions                                                                                 |
| `record_conclusion_tool`    | Record a research conclusion with confidence and source chunks                                                                                                 |
| `get_conclusions_tool`      | List conclusions, optionally filtered by active-only                                                                                                           |
| `supersede_conclusion_tool` | Mark a conclusion as superseded by a new one                                                                                                                   |
| `get_conclusion_chain_tool` | Trace a conclusion's supersession chain                                                                                                                        |

### Embedding Management (10)

| Tool                         | Description                                                         |
| ---------------------------- | ------------------------------------------------------------------- |
| `embed_config`               | Show current embedding model and dimension                          |
| `re_embed_tool`              | Swap embedding model and re-embed all chunks (atomic, no data loss) |
| `create_embed_space_tool`    | Create a new embedding space with a different model                 |
| `backfill_embed_space_tool`  | Backfill an embedding space for existing chunks                     |
| `list_embed_spaces_tool`     | List all embedding spaces with statistics                           |
| `cleanup_embed_space_tool`   | Remove an embedding space                                           |
| `deprecate_embed_space_tool` | Mark an embedding space as deprecated                               |
| `promote_embed_space_tool`   | Promote an embedding space to primary                               |
| `batch_compare_spaces_tool`  | Compare retrieval quality across embedding spaces                   |
| `compare_spaces_tool`        | Compare two embedding spaces on specific queries                    |

### Operations (4)

| Tool                        | Description                                                                 |
| --------------------------- | --------------------------------------------------------------------------- |
| `configure_llm_tool`        | Set the LLM backend for structured extraction (`ollama` or `openai_compat`) |
| `configure_vision_tool`     | Configure the vision model for figure extraction                            |
| `configure_omniparser_tool` | Configure OmniParser for UI element detection                               |
| `extract_figures_tool`      | Extract figures from PDF pages using the vision pipeline                    |

### Background Jobs (2)

| Tool             | Description                            |
| ---------------- | -------------------------------------- |
| `get_job_status` | Check status of a background operation |
| `list_jobs`      | List all background jobs               |

### Prediction Errors (2)

| Tool                            | Description                                     |
| ------------------------------- | ----------------------------------------------- |
| `list_prediction_errors_tool`   | List detected prediction errors (stale results) |
| `resolve_prediction_error_tool` | Mark a prediction error as resolved             |

## Current Status

**Phase 2 complete** (338+ tests, ~11K lines Python, 34 source files). Phase 2.5 stabilization (bug fixes + module decomposition) done.

| Phase   | Focus                                   | Status |
| ------- | --------------------------------------- | ------ |
| **0**   | Finish pending work                     | Done   |
| **1**   | Documentation & rename                  | Done   |
| **2**   | Embedding architecture + search (13/13) | Done   |
| **2.5** | Stabilization (bugs + refactors)        | Done   |
| **3**   | Integration (ME hooks, wisdom pipeline) | Next   |

## Database

The index is stored at `~/.local/share/knowledge-base/knowledge.db` by default. Override
the location with (highest precedence first) the `--db-path` CLI flag, the
`KNOWLEDGE_BASE_DB` environment variable, or fall back to the default — the MCP server
reads `KNOWLEDGE_BASE_DB`. Key tables:

- `chunks` — document content with content-hash deduplication
- `chunks_fts` — FTS5 full-text index (auto-synced via triggers)
- `chunks_vec` — sqlite-vec vector index
- `papers` / `papers_fts` — paper metadata with full-text search
- `relationships` — inter-paper relationships
- `conclusions` — research conclusions with supersession chains
- `methods` / `datasets` / `metrics` — structured extraction results
- `entities` / `entity_mentions` — resolved entities and surface forms
- `embed_spaces` — multiple embedding space configurations
- `chunk_sessions` — N:M ingestion session tracking
- `folder_summaries` / `folder_summaries_vec` — folder-level semantic embeddings
- `prediction_errors` — stale result detection
- `jobs` — background job tracking
- `config` — key-value store (embedding model, LLM settings, **schema version**)

### Upgrading / migrating the database

The database carries a `schema_version` (a `config` row). On open, the code
**validates** the version and refuses to operate on a DB that is newer than the
running build or behind it (telling you to migrate). Two operator commands —
both offline, embedding-free, and safe to use in a CI/release gate:

```bash
# Report live vs current schema version. Exit code 0 = match, non-zero = mismatch.
knowledge-base-ingest --db <path> schema

# Dry run: list pending migrations without touching the DB (non-zero unless current).
knowledge-base-ingest --db <path> migrate --check

# Apply pending migrations. A fresh DB is initialized to the current version;
# an existing DB is BACKED UP first (see below), then migrated.
knowledge-base-ingest --db <path> migrate
```

- **Stop the MCP server before `migrate`.** The server caches its connection and
  would keep serving the old schema (and may contend for the write lock) until
  restarted. `schema` / `migrate --check` are read-only and safe against a
  running server.
- **Backups.** `migrate` writes a timestamped copy to `<db dir>/backups/` (or
  `--backup-dir`) via `VACUUM INTO` before mutating. If a migration fails, the
  backup is **restored automatically** and the command aborts non-zero. Backups
  are not auto-pruned — clean them up yourself.

### Config keys

| Key              | Default       | Description                                                                |
| ---------------- | ------------- | -------------------------------------------------------------------------- |
| `embed_model`    | `bge-m3`      | Ollama embedding model name                                                |
| `embed_dim`      | `1024`        | Embedding vector dimension                                                 |
| `llm_provider`   | `ollama`      | LLM provider: `ollama` (native API) or `openai_compat` (OpenAI-compatible) |
| `llm_model`      | `qwen3.5:27b` | Model name passed to the provider                                          |
| `llm_base_url`   | _(unset)_     | Base URL for `openai_compat` provider (e.g. `http://192.168.1.41:1234`)    |
| `schema_version` | `1`           | Applied DB schema version (managed by `migrate`; see Upgrading above)      |

## Limitations

- Single-user, single-process (SQLite WAL mode, no concurrent writers)
- AST-aware chunking only for Python files; other code uses fixed-size chunks
- Embedding model swap re-embeds all chunks sequentially (can be slow for large indexes)
