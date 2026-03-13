# Architecture Overview

## High-Level Architecture

```{mermaid}
graph LR
    Client["MCP Client<br/>(Claude, IDE)"] -->|stdio| Server["FastMCP Server<br/>server.py"]
    Server --> Ingest["ingest.py"]
    Server --> Search["search.py"]
    Server --> Extract["extraction.py"]
    Server --> Vision["vision.py"]
    Server --> Papers["papers.py"]
    Server --> Conclusions["conclusions.py"]
    Server --> EmbedSwap["embed_swap.py"]
    Server --> Jobs["jobs.py"]

    Ingest --> DB["SQLite<br/>FTS5 + sqlite-vec"]
    Search --> DB
    Extract --> DB
    Vision --> DB
    Papers --> DB
    Conclusions --> DB
    EmbedSwap --> DB
    Jobs --> DB

    Ingest --> Ollama["Ollama<br/>Embedding API"]
    Search --> Ollama
    EmbedSwap --> Ollama
    Vision --> Ollama

    Extract --> LLM["LLM<br/>Ollama / OpenAI-compat"]
    Vision --> LLM
```

## Module Responsibilities

**`server.py`** -- FastMCP entry point. Registers all MCP tools, manages thread-local database connections via `threading.local()`, and protects schema initialization with a `threading.Lock` using double-checked locking. Handles argument validation and JSON serialization for tool responses.

**`db.py`** -- Database schema creation, migrations, and shared SQL helpers. Defines the complete schema (chunks, papers, relationships, conclusions, methods, datasets, metrics, entities, jobs, config tables) along with FTS5 virtual tables and sqlite-vec vector tables. Provides `get_connection()` which loads the sqlite-vec extension and enables WAL mode. Includes `_batched_execute` and `_batched_select` helpers that batch IN-clause queries to stay under SQLite's 999 variable limit.

**`ingest.py`** -- Ingestion pipeline for PDF, markdown, code, and web content. PDFs go through pymupdf4llm for header-aware markdown extraction with page tracking; Python files use AST-aware chunking (functions/classes as units); all other text uses fixed-size chunking (1000 chars, 200 overlap). Web ingestion uses trafilatura with browser fallback for JS-heavy pages. Computes SHA-256 content hashes for deduplication and calls the embedding API for each new chunk.

**`search.py`** -- Hybrid search combining FTS5 BM25 keyword search with sqlite-vec cosine similarity, merged via Reciprocal Rank Fusion (k=60). Supports three modes: `hybrid` (default), `fts` (keyword only), and `vec` (semantic only). Over-fetches 3x the requested results before RRF merging, then fetches chunk details for the top-k.

**`extraction.py`** -- LLM-powered structured extraction of methods, datasets, and metrics from paper chunks. Short documents (<8000 chars) use a single LLM call. Longer documents use map-reduce: per-chunk extraction followed by LLM-based entity resolution (for methods and datasets only; metrics bypass resolution) and atomic storage. Also provides `configure_llm` for switching between Ollama and OpenAI-compatible providers, with an advisory connectivity probe after saving config.

**`vision.py`** -- Figure extraction from PDF pages using vision models. Renders candidate pages as PNG (2x resolution via PyMuPDF), sends to a vision API (OpenAI-compatible chat completions format), validates and stores figure descriptions as searchable `figure` chunks. Optional OmniParser enrichment adds OCR text and icon detection. Uses ThreadPoolExecutor for parallel vision API calls while keeping all SQLite access on the main thread.

**`papers.py`** -- Paper CRUD, relationship management, BibTeX export, and citation suggestion. Manages the `papers`, `relationships`, and `paper_paths` tables. Generates BibTeX keys from first-author surname + year with collision avoidance. Citation suggestion uses three strategies: DOI matching (highest precision), title word-ratio overlap, and author+year heuristic matching.

**`conclusions.py`** -- Evidence-chained conclusions with supersession. Each conclusion links to source chunk IDs as evidence. Supersession creates a new conclusion and atomically marks the old one, forming traceable chains. Chain traversal walks both backward (predecessors) and forward (successors).

**`embed_swap.py`** -- Atomic embedding model swap. Embeds all chunks into a staging table first, then drops and recreates the vector table only after all embeddings succeed -- preventing data loss on failure. Updates the config table with the new model name and dimension.

**`embeddings.py`** -- Ollama embedding client. Auto-detects the Ollama URL: checks `OLLAMA_HOST` env, then WSL2 Windows host gateway, then falls back to localhost:11434. Batches embedding requests in groups of 32 and validates output dimensions.

**`jobs.py`** -- Background job queue for long-running extraction tasks. A singleton daemon thread (`_JobWorker`) processes jobs sequentially from the `jobs` table. Supports `extract_structure` and `extract_figures` job types. Includes crash recovery (resets stale `running` jobs to `pending` on startup) and deduplication (reuses existing pending/running jobs for the same paper+type+params).

**`browser/render_page.py`** -- Playwright-based page renderer for JS-heavy web content. Launched as a subprocess from `ingest.py` with the configured venv's Python. Supports both local Chromium and CDP (Chrome DevTools Protocol) modes. Outputs rendered HTML and a viewport screenshot (1280x8000 max). Uses ephemeral browser contexts for isolation.

## Data Flow

### Ingestion

```{mermaid}
flowchart TD
    Input["File / URL / Directory"] --> Detect{"Detect type"}
    Detect -->|PDF| PyMuPDF4LLM["pymupdf4llm<br/>header-aware markdown"]
    Detect -->|Python| AST["ast.parse<br/>function/class chunks"]
    Detect -->|Other text| Fixed["Fixed-size chunking<br/>1000 chars / 200 overlap"]
    Detect -->|URL| Trafilatura["trafilatura extract"]
    Detect -->|Directory| Walk["rglob *.pdf,*.md,*.txt,*.typ,*.rst"]

    Trafilatura -->|< 200 chars| Browser["Browser fallback<br/>(if configured)"]
    Trafilatura -->|>= 200 chars| Fixed
    Browser --> Fixed

    PyMuPDF4LLM --> Chunks["Chunk list"]
    AST --> Chunks
    Fixed --> Chunks
    Walk -->|per file| Detect

    Chunks --> Hash["SHA-256 content hash"]
    Hash --> Dedup{"Exists?"}
    Dedup -->|Yes| Skip["Skip"]
    Dedup -->|No| Embed["Ollama embed"]
    Embed --> Store["INSERT chunks + chunks_vec"]
```

### Search

```{mermaid}
flowchart TD
    Query["Query string"] --> Mode{"Mode?"}

    Mode -->|hybrid / fts| FTS["FTS5 BM25<br/>chunks_fts MATCH"]
    Mode -->|hybrid / vec| Vec["Embed query<br/>→ chunks_vec MATCH"]

    FTS --> RRF["RRF merge<br/>k=60"]
    Vec --> RRF

    RRF --> TopK["Take top-k"]
    TopK --> Filter{"source_type<br/>filter?"}
    Filter --> Fetch["Fetch chunk details<br/>from chunks table"]
    Fetch --> Results["SearchResult list<br/>(chunk_id, content, score,<br/>source_type, source_uri,<br/>chunk_index, match_type)"]
```

### Structured Extraction

```{mermaid}
flowchart TD
    Paper["paper_id"] --> Chunks["Fetch paper chunks<br/>via paper_paths"]
    Chunks --> Size{"Total chars<br/>> 8000?"}

    Size -->|No| Single["Single LLM call<br/>extract methods/datasets/metrics"]
    Size -->|Yes| Map["Map phase<br/>per-chunk LLM extraction"]

    Map --> Resolve["Entity resolution<br/>(methods + datasets only)<br/>LLM groups aliases → canonical names"]
    Resolve --> Store["Store resolved entities,<br/>methods, datasets, metrics"]

    Single --> Store
    Store --> DB["SQLite tables:<br/>methods, datasets, metrics,<br/>entities, entity_mentions"]
```

## Thread Safety Model

The server uses three mechanisms for thread safety:

**Thread-local connections.** Each thread gets its own `sqlite3.Connection` via `threading.local()`. SQLite connections are not thread-safe, so sharing is avoided entirely.

**Double-checked locking for schema init.** A module-level `threading.Lock` with a `_schema_ready` boolean flag ensures `init_schema()` runs exactly once, even when multiple threads call `_get_conn()` concurrently. The outer check avoids lock acquisition on the hot path.

**WAL mode for read concurrency.** `PRAGMA journal_mode=WAL` is set on every connection, allowing concurrent readers while a single writer holds the lock. This is critical for the background job worker thread, which writes extraction results while the main thread may be serving search queries.

## Design Choices

**Single SQLite file.** The entire index lives in one file (`~/.local/share/research-index/research.db`). Zero configuration, trivially portable, no external database process.

**Content-hash deduplication.** Each chunk is hashed (truncated SHA-256) before insertion. On normal `ingest`, unchanged chunks are skipped. On `reingest`, all old chunks are deleted and replaced regardless of hash -- this is a force operation.

**RRF over learned fusion.** Reciprocal Rank Fusion (k=60) merges FTS5 and vector results without any training data. The fixed k=60 parameter follows the original Cormack et al. recommendation and works well across domains without tuning.

**Map-reduce for unbounded documents.** Papers can have hundreds of chunks. Rather than concatenating (context window limits) or sampling (information loss), map-reduce extracts facts per-chunk and then resolves entities across chunks in a separate LLM call. This scales linearly with document length.
