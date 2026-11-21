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
│  FastMCP Server (stdio: all tools; HTTP: 3)   │
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

KB starts only with an explicit embedding declaration (see [Configuration](#configuration)):

```bash
export KNOWLEDGE_BASE_EMBED_PROVIDER=ollama KNOWLEDGE_BASE_EMBED_MODEL=bge-m3 \
       KNOWLEDGE_BASE_EMBED_DIM=1024 KNOWLEDGE_BASE_EMBED_URL=http://localhost:11434
uv run knowledge-base
```

Or register as an MCP server in your client's config (e.g. Claude Code `settings.json`). The
`env` block carries the declaration; this one points at an OpenAI-compatible embedder:

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
      ],
      "env": {
        "KNOWLEDGE_BASE_EMBED_PROVIDER": "openai_compat",
        "KNOWLEDGE_BASE_EMBED_MODEL": "qwen3-embedding",
        "KNOWLEDGE_BASE_EMBED_DIM": "1024",
        "KNOWLEDGE_BASE_EMBED_URL": "https://embedder.example.test",
        "KNOWLEDGE_BASE_EMBED_API_KEY": "<the embedder's key>"
      }
    }
  }
}
```

Or serve stateless MCP over HTTP, with the same declaration exported, behind per-machine tokens (see
[HTTP transport and machine tokens](#http-transport-and-machine-tokens)):

```bash
uv run knowledge-base --http 127.0.0.1:<port> --token-file /path/to/token-hashes
```

Plain `knowledge-base` serves stdio: the local and maintenance surface, with every tool. `--http` serves only the
three network tools, `search_index`, `ingest_text` and `ingest_url`. Either way one `knowledge-base` process serves a
database at a time: it holds the [store lock](#the-store-lock) for its whole life, so a second one, a second Claude
Code session's stdio server included, exits 78 at start. Several clients share one database through one `--http`
server.

### Configuration

The environment is authoritative for the embedding configuration. A missing or invalid
variable, or a database that disagrees with the declaration, stops the server before it
serves: it prints the reason (naming the variable or the `config` field) and exits 78
(`EX_CONFIG`). Empty values count as unset. So do a bad command line, `--http` without `--token-file`, and a
missing, unreadable or malformed token-hash file (named by path and line), and `--http` while
`KNOWLEDGE_BASE_VISION` or `KNOWLEDGE_BASE_BROWSER` is on, all checked before the database is opened, and a
database whose store lock another process holds.

| Variable | Required | Value |
| --- | --- | --- |
| `KNOWLEDGE_BASE_EMBED_PROVIDER` | always | `openai_compat`, `openai`, `llama.cpp` (the `openai_compat` wire), `ollama` or `onnx` |
| `KNOWLEDGE_BASE_EMBED_MODEL` | always | the model slug; it also selects the default query instruction (KB's ADR-0015 one when it contains `qwen3-embedding`) |
| `KNOWLEDGE_BASE_EMBED_DIM` | always | the stored dimension (after any Matryoshka truncation) |
| `KNOWLEDGE_BASE_EMBED_MATRYOSHKA_BASE_DIM` | optional | the native dimension the server embeds at before truncation to `_DIM`; greater than `_DIM` |
| `KNOWLEDGE_BASE_EMBED_ARTIFACT_SHA256`, `_POOLING`, `_NORMALIZATION`, `_MAX_INPUT_TOKENS` | optional, all four or none; only for `llama.cpp` or `openai_compat` | the served artifact (ADR-0015 §1): the model file's sha256 (64 lowercase hex digits), the server's pooling and normalization, and its maximum input in tokens as llama-server counts it, EOS included (`2048` for ADR-0015 §5). Every input is counted at the server's `/tokenize` and kept within it: chunks are split, everything else truncated (see the ingesting guide) |
| `KNOWLEDGE_BASE_EMBED_BATCH_SIZE` | optional; only for `openai_compat`, `openai`, `llama.cpp` | inputs per `/v1/embeddings` request, a whole number of at least 1 (default `1`), so a query from another client sharing the embedder waits for one input, not a batch; each KB process keeps one request in flight per embedder (see the ingesting guide) |
| `KNOWLEDGE_BASE_EMBED_URL` | for `openai_compat`, `openai`, `llama.cpp`, `ollama`; refused for `onnx` | `http(s)://host[:port][/path]`, no userinfo, query or fragment |
| `ONNX_EMBED_MODEL_PATH` | for `onnx` | an existing model file |
| `KNOWLEDGE_BASE_EMBED_API_KEY` | optional | the embedder's key; `config` records only `env:KNOWLEDGE_BASE_EMBED_API_KEY` |
| `KNOWLEDGE_BASE_VISION`, `KNOWLEDGE_BASE_BROWSER`, `KNOWLEDGE_BASE_LLM` | optional | `on` or `off` (default `off`): figure extraction, the Playwright fallback, LLM extraction |
| `KNOWLEDGE_BASE_LLM_API_KEY` | optional | the only `env:` source an LLM key may name |
| `KNOWLEDGE_BASE_HOST_ADDRESSES` | optional | this host's own IP addresses or CIDR networks, comma-separated (e.g. `203.0.113.7,2001:db8:7::/64`); the SSRF floor refuses them though they are public |
| `KNOWLEDGE_BASE_FETCH_MAX_BYTES` | optional | the byte cap on a fetched page or image body (default `52428800`, 50 MiB) |
| `KNOWLEDGE_BASE_FETCH_MAX_REDIRECTS` | optional | redirects a fetch follows (default `5`; `0` follows none) |
| `KNOWLEDGE_BASE_FETCH_DEADLINE` | optional | seconds a whole fetch may take, every hop included (default `60`) |

`EMBED_PROVIDER` is no longer read and is refused when set. No embedding provider has a
default endpoint: nothing falls back to `https://api.openai.com` or to an auto-detected
Ollama. The declared host is the one exception to the SSRF floor, on the embedding request
only; every URL a tool supplies keeps the floor. The floor refuses every non-global address and
the listed host addresses. It is checked on every request, redirect hops included, and each request
connects to the address it checked. Endpoints the operator's environment declares (the embedder,
`OLLAMA_HOST` or the auto-detected Ollama, KB's own OmniParser server) are not floored.

- **A blank database** is seeded from the declaration (its vector tables take the declared
  dimension).
- **An existing database** must agree with it: the URL and the active space's whole ADR-0015
  identity (model, provider, dimension, Matryoshka base dimension, element type and served
  artifact); every differing field is named with its variable. A database from before this
  check records its URL on its first start. The
  key row always follows the declaration (an inline key the old configure tool stored is
  replaced; rotate that key, since its bytes may survive in free pages, the WAL and earlier
  backups).
- **Changing any identity field** (server stopped), adopting a served artifact included: set
  the new `KNOWLEDGE_BASE_EMBED_*` values, run `knowledge-base-ingest re-embed` (`rebind`
  refuses an identity change), start the server. A failed run removes the
  space it created and can be rerun; a killed one leaves a `populating` space to remove with
  `deprecate_embed_space_tool` then `cleanup_embed_space_tool`. Rolling back a model is a
  re-embed too. A failed or killed run has already recorded the new URL: to go back to the
  previous declaration instead of rerunning, restore it and run `knowledge-base-ingest rebind`.
- **Moving the embedder** (server stopped): set the new `KNOWLEDGE_BASE_EMBED_URL`, run
  `knowledge-base-ingest rebind`, start the server.
- **Removing a URL or content document** (server stopped): `knowledge-base-ingest remove <source_uri>` deletes it, its
  chunks and every reference to them. A passage is stored once, under the first document that held it, so a later
  document that repeated it loses it too. See
  [Removing a Document](docs/usage/ingesting-documents.md#removing-a-document).
- **Redacting rows stored before redaction existed** (server stopped): `knowledge-base-ingest redact` reports them
  (exit 1 if any, never printing the text), `knowledge-base-ingest redact --rewrite` redacts them, re-embeds and
  purges the old bytes from the file. Backups taken earlier still hold that text. See
  [Redaction](docs/usage/ingesting-documents.md#redaction).
- "Server stopped" is enforced by the store lock below. `redact --rewrite` also refuses to start while any other
  connection holds the database.

### The store lock

Every KB process that writes a database holds one exclusive advisory lock on `<database>.store-lock`, beside the
database (memory-engine's ADR-0020 §8): `knowledge-base`, stdio or `--http`, for its whole life, and every writing
`knowledge-base-ingest` command (`ingest`, `reingest`, `ingest-url`, `remove`, `re-embed`, `rebind`, `migrate`,
`redact --rewrite`) for its run. A second taker refuses before it reads the database, naming the holder (its role,
PID and start time, which the holder writes into the lock file) and the lock file: a server exits 78, a command exits
1 with its JSON error. A process releases the lock only after its background job worker has
stopped. The read-only commands (`status`, `schema`, `migrate --check`, `redact` without `--rewrite`) take no lock and
run beside a server; they refuse a missing database, or one that is not at this build's schema, rather than create or
migrate it.

The kernel releases the lock when its holder exits, however it exits, so a killed server leaves nothing to clean up.
Never delete the lock file: deleting a held one lets a second writer in. The lock holds between processes of one host,
containers sharing the database's volume included, never across hosts or on a network filesystem.

**Upgrading to schema v3** (#579, the chunks each space's embedder refused): stop the unit and run
`knowledge-base-ingest migrate` once; it backs the database up first, adds `embed_refusals` and sets every space's
`chunk_count` to the vectors it stores. Until then the server exits 78 naming that command. A space holding a chunk an
earlier backfill counted but could not embed is backfilled once more before it can be promoted.

**Upgrading to schema v2** (the served artifact and each space's query instruction): stop the
unit and run `knowledge-base-ingest migrate`, which backs the database up first; until then the
server exits 78 naming that command. The migrated spaces record no served artifact, so they
start under a declaration without one; a store whose active space uses Matryoshka declares
`KNOWLEDGE_BASE_EMBED_MATRYOSHKA_BASE_DIM`, and one whose active space is `int8` is re-embedded.
To roll back, restore the backup `migrate` wrote: an older build refuses the newer schema.

**Queries carry their space's instruction.** A space records its own query instruction, or none
(`""`), or the default for its model: KB's pinned ADR-0015 §6 instruction for Qwen3-Embedding
models, nothing for others, so a bge-m3 quickstart sends exactly the query text. Documents never
carry one. This also applies to an existing Qwen3-Embedding store after the upgrade: its spaces
record no instruction, so its queries start carrying the pinned one and rankings shift.

**Upgrading an existing install.** Declare what the database already holds: for the
historical default, `ollama`, `bge-m3`, `1024` and the Ollama URL (on WSL2, the Windows host
address auto-detection used to find). A database created with `EMBED_PROVIDER=openai`
declares `KNOWLEDGE_BASE_EMBED_PROVIDER=openai`, `KNOWLEDGE_BASE_EMBED_URL=https://api.openai.com`
and moves the key from `OPENAI_API_KEY` to `KNOWLEDGE_BASE_EMBED_API_KEY`. Set
`KNOWLEDGE_BASE_VISION=on` / `KNOWLEDGE_BASE_LLM=on` to keep figure or structured extraction.
An LLM, vision, OmniParser or CDP URL a configure tool stored keeps the floor when it is used: a
LAN or tailnet one is refused. Clear it to use the default (`configure_llm_tool` with no
`base_url`, `configure_vision_tool(base_url="")`, `configure_omniparser_tool(server_url="")`),
declare a LAN or tailnet Ollama with `OLLAMA_HOST`, or set `allow_loopback_base_url` (through
`configure_llm_tool`) for a localhost server.

### HTTP transport and machine tokens

`knowledge-base --http IP:PORT --token-file PATH` serves MCP's streamable HTTP on `POST /mcp`, stateless with JSON
responses, so a restart needs no client session recovery. `IP` is an address (IPv6 in brackets), never a host name.

Every request carries exactly one `Authorization: Bearer <token>` header. Tokens and the token-hash file are
memory-engine's ([ADR-0020 §4](https://github.com/dutiona/memory-engine/blob/main/docs/design/adr/0020-harness-neutral-lifecycle-api.md#4-machine-tokens)):
one token per machine, serving both services; one `<machine> <sha256-hex>` line per machine, for example
`laptop <64 lowercase hex digits>`, read at startup only; a malformed line stops the server. Mint a line without
putting the token on a command line or hashing a line feed:

```bash
printf '%s %s\n' laptop "$(printf '%s' "$token" | sha256sum | cut -d' ' -f1)" >> token-hashes
```

| Status | When |
| --- | --- |
| 401 | Any request, on any method or path, without exactly one valid token |
| 404 | An unknown path, with a token |
| 405 | `GET` or `DELETE` on `/mcp` (stateless mode serves `POST` only) |
| 413 | A request body over 4 MiB, with a valid token (read up to the cap, before any tool runs) |
| other 4xx | A `POST` the MCP SDK refuses (`Accept`, `Content-Type`, body) |

KB never sends 403 and has no unauthenticated route, so a liveness probe expects 401. Every `Host` is served, and the
transport is plain HTTP: keep the port reachable from the host only (a loopback bind, or for a container the
[published-port setup](docs/getting-started/installation.md#container-image)) behind a TLS proxy such as
`tailscale serve`. Each tool call writes one stderr line naming the machine and the tool, and any token shape in any
log line is written `me_[redacted]`. A stop lets calls already started finish, bounded by the unit's stop timeout.
Only `knowledge-base --http` serves HTTP through the token check; any other HTTP server of either hub (the full stdio
hub or the network hub) runs no tool.

Over HTTP the model sees exactly three tools: `search_index`, `ingest_text` (a text or markdown document passed in,
stored as `content://<machine>/<name>`) and `ingest_url` (a web page's text). None takes a server path, configures the
server, or removes or replaces a document; every failure is a tool error that never names the embedder. A document
ingested by content from one machine is found by a search from any other. With the header read from standard input
as in the check below, the call's body is:

```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
 "params": {"name": "ingest_text", "arguments": {"name": "notes/attention.md", "content": "# Attention\n\nNotes."}}}
```

A client sends the header itself (the Claude Code plugin's `headersHelper`, ADR-0020 §12), never in `.mcp.json` or on
a command line. A manual check reads the header from standard input:

```bash
printf 'Authorization: Bearer %s\n' "$(cat ~/.config/memory-engine/secrets/token)" | curl -sS -H @- \
  -H 'Accept: application/json' -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' https://kb.example-tailnet.ts.net/mcp
```

### Run tests

```bash
uv run pytest tests/ -q
```

## MCP Tools

47 tools over stdio, 3 over HTTP (`search_index`, `ingest_text`, `ingest_url`). The tables below list the stdio
tools; [the tool reference](docs/reference/mcp-tools.md) documents both surfaces.

### Ingest (6)

| Tool                     | Description                                                                       |
| ------------------------ | --------------------------------------------------------------------------------- |
| `ingest`                 | Ingest a local file (PDF, markdown, code)                                         |
| `reingest`               | Re-ingest a previously ingested file (deletes old chunks, preserves FK integrity) |
| `ingest_url`             | Fetch and ingest a web page via trafilatura (with SSRF protection)                |
| `ingest_text`            | Ingest a text or markdown document passed in, as `content://<machine>/<name>`      |
| `configure_chunking`     | Configure chunking strategy parameters                                            |
| `configure_browser_tool` | Configure browser-based web ingestion (needs `KNOWLEDGE_BASE_BROWSER=on`)        |

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
| `create_embed_space_tool`    | Create a space the declared endpoint can fill (model, dim, Matryoshka, query instruction) |
| `backfill_embed_space_tool`  | Backfill an embedding space for existing chunks                     |
| `list_embed_spaces_tool`     | List all embedding spaces with statistics                           |
| `cleanup_embed_space_tool`   | Remove an embedding space                                           |
| `deprecate_embed_space_tool` | Mark an embedding space as deprecated                               |
| `promote_embed_space_tool`   | Promote an embedding space of the declared identity to primary       |
| `batch_compare_spaces_tool`  | Compare retrieval quality across embedding spaces                   |
| `compare_spaces_tool`        | Compare two embedding spaces on specific queries                    |
| `benchmark_spaces_tool`      | Benchmark non-deprecated spaces against a baseline on sampled queries |

### Operations (4)

| Tool                        | Description                                                                 |
| --------------------------- | --------------------------------------------------------------------------- |
| `configure_llm_tool`        | Set the LLM backend for structured extraction (needs `KNOWLEDGE_BASE_LLM=on`) |
| `configure_vision_tool`     | Configure the vision model (needs `KNOWLEDGE_BASE_VISION=on`)               |
| `configure_omniparser_tool` | Configure OmniParser for UI element detection                               |
| `extract_figures_tool`      | Extract figures from PDF pages using the vision pipeline                    |

### Background Jobs (2)

| Tool             | Description                            |
| ---------------- | -------------------------------------- |
| `get_job_status_tool` | Check status of a background operation |
| `list_jobs_tool`      | List all background jobs               |

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
- `embed_refusals` — the chunks each space's embedder answered with no usable vector (schema v3)
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

- **Stop the MCP server before `migrate`.** `migrate` refuses while a server holds
  the [store lock](#the-store-lock). `schema` / `migrate --check` are read-only and
  safe against a running server.
- **Backups.** `migrate` writes a timestamped copy to `<db dir>/backups/` (or
  `--backup-dir`) via `VACUUM INTO` before mutating. If a migration fails, the
  backup is **restored automatically** and the command aborts non-zero. Backups
  are not auto-pruned — clean them up yourself.

### Config keys

The `embed_*` rows (`embed_provider`, `embed_model`, `embed_dim`, `embed_base_url`,
`embed_api_key`) record the `KNOWLEDGE_BASE_EMBED_*` declaration: seeded on a blank
database and checked at every start (see [Configuration](#configuration)).

| Key              | Default       | Description                                                                |
| ---------------- | ------------- | -------------------------------------------------------------------------- |
| `llm_provider`   | `ollama`      | LLM provider: `ollama` (native API) or `openai_compat` (OpenAI-compatible) |
| `llm_model`      | `qwen3.5:27b` | Model name passed to the provider                                          |
| `llm_base_url`   | _(unset)_     | Base URL for `openai_compat` provider (e.g. `http://192.168.1.41:1234`)    |
| `schema_version` | `2`           | Applied DB schema version (managed by `migrate`; see Upgrading above)      |

## Limitations

- One process (SQLite WAL mode, no concurrent writers), shared over HTTP by every machine holding a token; each
  reaches only the three network tools
- Only the maintenance command `knowledge-base-ingest remove` removes a document ingested by URL or by content;
  re-ingesting changed text keeps the earlier version searchable
- AST-aware chunking only for Python files; other code uses fixed-size chunks
- Embedding model swap re-embeds all chunks sequentially (can be slow for large indexes)
