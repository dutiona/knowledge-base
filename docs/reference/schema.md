# Database Schema Reference

SQLite database with {term}`FTS5` full-text search, {term}`sqlite-vec` vector similarity, and {term}`WAL mode`.

Default location: `~/.local/share/knowledge-base/knowledge.db`

## ER Diagram

```{mermaid}
erDiagram
    config {
        TEXT key PK
        TEXT value
    }

    chunks {
        INTEGER id PK
        TEXT content_hash UK
        TEXT content
        TEXT source_type
        TEXT source_uri
        INTEGER chunk_index
        TEXT session_id
        TEXT chunk_strategy
        TEXT created_at
        TEXT metadata
    }

    chunks_fts {
        TEXT content
    }

    chunks_vec {
        FLOAT embedding
        INTEGER chunk_id
    }

    papers {
        INTEGER id PK
        TEXT title
        TEXT authors
        INTEGER year
        TEXT venue
        TEXT doi UK
        TEXT bibtex
        INTEGER abstract_chunk_id FK
        TEXT added_at
    }

    paper_paths {
        INTEGER id PK
        INTEGER paper_id FK
        TEXT path UK
        TEXT content_hash
        BOOLEAN is_primary
        TEXT added_at
    }

    relationships {
        INTEGER id PK
        INTEGER source_paper_id FK
        INTEGER target_paper_id FK
        TEXT relation_type
        REAL confidence
        INTEGER evidence_chunk_id FK
        TEXT created_at
    }

    conclusions {
        INTEGER id PK
        TEXT claim
        REAL confidence
        TEXT source_chunk_ids
        TEXT session_context
        TEXT created_at
        INTEGER superseded_by FK
    }

    executions {
        INTEGER id PK
        TEXT task
        TEXT result_summary
        TEXT conclusion_ids
        TEXT created_at
    }

    methods {
        INTEGER id PK
        TEXT name
        INTEGER paper_id FK
        TEXT description
        INTEGER chunk_id FK
    }

    datasets {
        INTEGER id PK
        TEXT name
        INTEGER paper_id FK
        TEXT description
        INTEGER chunk_id FK
    }

    metrics {
        INTEGER id PK
        TEXT name
        REAL value
        TEXT unit
        INTEGER dataset_id FK
        INTEGER method_id FK
        INTEGER paper_id FK
        INTEGER chunk_id FK
    }

    entities {
        INTEGER id PK
        TEXT canonical_name
        TEXT entity_type
        INTEGER paper_id FK
        TEXT description
    }

    entity_mentions {
        INTEGER id PK
        INTEGER entity_id FK
        TEXT surface_form
        INTEGER chunk_id FK
        REAL confidence
    }

    chunk_sessions {
        INTEGER chunk_id FK
        TEXT session_id
    }

    jobs {
        INTEGER id PK
        INTEGER paper_id FK
        TEXT job_type
        TEXT params
        TEXT status
        TEXT progress
        TEXT result
        TEXT error
        TEXT created_at
        TEXT started_at
        TEXT completed_at
    }

    chunks ||--o{ chunk_sessions : "session membership"
    papers ||--o{ paper_paths : "has paths"
    papers ||--o{ relationships : "source"
    papers ||--o{ relationships : "target"
    papers ||--o{ methods : "uses"
    papers ||--o{ datasets : "uses"
    papers ||--o{ metrics : "reports"
    papers ||--o{ entities : "mentions"
    prediction_errors {
        INTEGER id PK
        TEXT query
        TEXT query_hash
        REAL top_score
        INTEGER top_chunk_id FK
        TEXT error_type
        TEXT source_type_filter
        TEXT detected_at
        TEXT resolved_at
    }

    papers ||--o{ jobs : "has jobs"
    papers }o--o| chunks : "abstract_chunk"
    chunks }o--o{ prediction_errors : "top_chunk"
    chunks ||--o{ embed_refusals : "refused"
    embed_spaces ||--o{ embed_refusals : "refused for"
    embed_refusals {
        INTEGER chunk_id FK
        TEXT space_name FK
    }
    chunks ||--o{ embed_truncations : "stored cut"
    embed_spaces ||--o{ embed_truncations : "stored cut in"
    embed_truncations {
        INTEGER chunk_id FK
        TEXT space_name FK
        INTEGER tokens_before
        INTEGER tokens_after
    }
    embed_spaces {
        TEXT name PK
        TEXT model
        TEXT provider
        INTEGER dim
        TEXT chunk_strategy
        TEXT status
        TEXT table_name UK
        TEXT created_at
        INTEGER chunk_count
        INTEGER total_chunks
        INTEGER matryoshka_base_dim
        TEXT element_type
        TEXT artifact_sha256
        TEXT pooling
        TEXT normalization
        INTEGER max_input_tokens
        TEXT query_instruction
    }

    chunks ||--o| chunks_vec : "embedding"
    chunks ||--o{ entity_mentions : "mentioned_in"
    chunks }o--o{ methods : "chunk_id"
    chunks }o--o{ datasets : "chunk_id"
    chunks }o--o{ metrics : "chunk_id"
    chunks }o--o{ relationships : "evidence"
    entities ||--o{ entity_mentions : "has mentions"
    methods }o--o{ metrics : "achieved"
    datasets }o--o{ metrics : "measured_on"
    conclusions }o--o| conclusions : "superseded_by"
```

## Tables

### config

Key-value store for server configuration (embedding model, LLM provider, vision model, browser settings).

| Column  | Type | Constraints | Default | Description         |
| ------- | ---- | ----------- | ------- | ------------------- |
| `key`   | TEXT | PRIMARY KEY | --      | Configuration key   |
| `value` | TEXT | NOT NULL    | --      | Configuration value |

Populated on first run with the `KNOWLEDGE_BASE_EMBED_*` declaration (`embed_provider`, `embed_model`, `embed_dim`,
`embed_base_url`, and the key source in `embed_api_key`), never with defaults, and checked against it at every start; and
with these defaults:

| Key                             | Default Value |
| ------------------------------- | ------------- |
| `llm_provider`                  | `ollama`      |
| `llm_model`                     | `qwen3.5:27b` |
| `auto_relate_propose_threshold` | `0.82`        |
| `auto_relate_accept_threshold`  | `0.95`        |

Additional keys set via configure tools: `llm_base_url`, `llm_api_key`, `vision_model`, `vision_base_url`, `omniparser_path`, `omniparser_server_url`, `browser_mode`, `browser_endpoint`, `browser_venv`.

**Provider keys** (`embed_*` recorded from the declaration, `llm_*` set via `configure_llm`, per
[ADR-0018](../design/adr/0018-provider-abstraction.md) and its #569 amendment):

| Key                                          | Meaning                                                                                  |
| -------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `embed_provider`                             | Embedding API family: `openai_compat` \| `openai` \| `ollama` \| `onnx`                  |
| `embed_base_url`                             | The declared `KNOWLEDGE_BASE_EMBED_URL`, normalized                                      |
| `embed_api_key`                              | Always `env:KNOWLEDGE_BASE_EMBED_API_KEY` when a key is declared; never the secret       |
| `embed_model`                                | Embedding model slug — the `model` field of the embedding-identity tuple                 |
| `llm_provider`                               | Chat API family: `openai_compat` \| `anthropic_compat` \| `ollama`                       |
| `llm_base_url` / `llm_api_key` / `llm_model` | Chat counterparts (same semantics as the `embed_*` keys)                                 |
| `allow_loopback_base_url`                    | `true`/`false` — **shared** opt-in permitting a loopback/`localhost` URL for every stored service URL (LLM, vision, OmniParser, CDP); never for ingest or image URLs |

The `embed_spaces` columns `model`, `provider`, `dim`, `matryoshka_base_dim`, `element_type`,
`artifact_sha256`, `pooling`, `normalization` and `max_input_tokens` are the ADR-0015 identity
tuple. The served artifact (the last four) is recorded whole or not at all: NULL means the space
was recorded before the served artifact existed (schema v1). The active space must equal the
`KNOWLEDGE_BASE_EMBED_*` declaration at startup, and every embedding call checks the space it
embeds into or queries. `config` mirrors only `embed_provider`, `embed_model` and `embed_dim`. An
`env:` key spec is resolved at call time and is **never** stored as the resolved secret. An inline
`llm_api_key` is stored plaintext-at-rest (local-only posture); an LLM key may name only
`env:KNOWLEDGE_BASE_LLM_API_KEY`.

---

### chunks

Primary content storage. Each row is one chunk of ingested text.

| Column           | Type    | Constraints               | Default           | Description                                                                      |
| ---------------- | ------- | ------------------------- | ----------------- | -------------------------------------------------------------------------------- |
| `id`             | INTEGER | PRIMARY KEY AUTOINCREMENT | --                | Chunk ID                                                                         |
| `content_hash`   | TEXT    | NOT NULL, UNIQUE          | --                | SHA-256 {term}`content hash` for deduplication                                   |
| `content`        | TEXT    | NOT NULL                  | --                | Raw text content                                                                 |
| `source_type`    | TEXT    | NOT NULL, CHECK           | --                | One of: `pdf`, `markdown`, `code`, `web`, `note`, `figure`                       |
| `source_uri`     | TEXT    | NOT NULL                  | --                | File path or URL of the source                                                   |
| `chunk_index`    | INTEGER | NOT NULL                  | --                | Position within the source (0-based; figures use 1,000,000+ encoding)            |
| `session_id`     | TEXT    | --                        | --                | **Deprecated.** Ingestion session ID (superseded by `chunk_sessions` join table) |
| `chunk_strategy` | TEXT    | NOT NULL                  | `'mechanical'`    | Chunking strategy: `mechanical` (8K) or `semantic` (32K)                         |
| `created_at`     | TEXT    | NOT NULL                  | `datetime('now')` | ISO 8601 timestamp                                                               |
| `metadata`       | TEXT    | --                        | `'{}'`            | JSON metadata blob                                                               |

**CHECK constraint:** `source_type IN ('pdf', 'markdown', 'code', 'web', 'note', 'figure')`

**Index:** `idx_chunks_session_id` on `(session_id)` -- accelerates co-occurrence joins.

**Session tracking:** Session associations are stored in the `chunk_sessions` join table (see below), enabling N:M relationships between chunks and sessions. The `chunks.session_id` column is retained for backward compatibility but is no longer read by `co_occurrence_pairs()`. See [Ingesting Documents > Session Tracking](../usage/ingesting-documents.md#session-tracking-co-occurrence) for usage details.

---

### chunk_sessions

N:M join table mapping chunks to ingestion sessions. Introduced in #139 to replace the 1:1 `chunks.session_id` column.

| Column       | Type    | Constraints                                   | Description          |
| ------------ | ------- | --------------------------------------------- | -------------------- |
| `chunk_id`   | INTEGER | NOT NULL, FK → `chunks(id)` ON DELETE CASCADE | Chunk ID             |
| `session_id` | TEXT    | NOT NULL                                      | Ingestion session ID |

**UNIQUE constraint:** `(chunk_id, session_id)` — prevents duplicate associations. `INSERT OR IGNORE` is idempotent.

**Index:** `idx_chunk_sessions_session` on `(session_id)` — accelerates co-occurrence CTE joins.

**Why N:M:** Content-hash deduplication skips existing chunks on re-ingest. With the old 1:1 column, the new session was silently lost. The join table allows `INSERT OR IGNORE` on the dedup path, preserving all session associations regardless of whether the chunk itself was skipped.

**Co-occurrence query:** The `co_occurrence_pairs()` CTE joins `chunk_sessions` → `chunks` to compute `DISTINCT (source_uri, session_id)` pairs, then self-joins to find documents sharing sessions. Complexity is proportional to documents, not chunks.

**Migration:** `_migrate_chunk_sessions()` creates the table and backfills from existing `chunks.session_id` values. Runs idempotently via `init_schema()`.

---

### chunks_fts

{term}`FTS5` virtual table for full-text keyword search over chunk content. Content-synced with `chunks` via triggers.

| Column    | Type | Description                                    |
| --------- | ---- | ---------------------------------------------- |
| `content` | TEXT | Indexed content (synced from `chunks.content`) |

**Configuration:** `content='chunks'`, `content_rowid='id'`, `tokenize='porter unicode61'`

**Triggers:**

- `chunks_ai` -- AFTER INSERT on chunks: inserts into FTS index
- `chunks_ad` -- AFTER DELETE on chunks: removes from FTS index
- `chunks_au` -- AFTER UPDATE on chunks: removes old, inserts new

---

### chunks_vec

{term}`sqlite-vec` virtual table for vector similarity search. This is the **default** space table (registered as the `default` entry in `embed_spaces`). Additional spaces create their own tables named `chunks_vec_<name>`, all managed through the embedding space registry.

| Column      | Type     | Description                                                 |
| ----------- | -------- | ----------------------------------------------------------- |
| `embedding` | float[N] | Vector embedding (N = configured `embed_dim`, default 1024) |
| `chunk_id`  | INTEGER  | Auxiliary column linking to `chunks.id`                     |

Dimension is read from `config.embed_dim` at schema creation time. A re-embed creates a new space table rather than recreating this one.

---

### embed_spaces

Registry for embedding spaces. Each space owns a `sqlite-vec` virtual table and tracks its model, provider, dimension, and lifecycle status. Exactly one space can be `active` at any time (enforced by a partial unique index on `status`). See [Embedding Spaces](../usage/embedding-spaces.md) for the full lifecycle workflow.

| Column                | Type    | Constraints      | Default           | Description                                                             |
| --------------------- | ------- | ---------------- | ----------------- | ----------------------------------------------------------------------- |
| `name`                | TEXT    | PRIMARY KEY      | --                | Unique space identifier (alphanumeric/underscore)                       |
| `model`               | TEXT    | NOT NULL         | --                | Embedding model name                                                    |
| `provider`            | TEXT    | NOT NULL         | --                | Embedding provider (`openai_compat`, `openai`, `llama.cpp`, `ollama`, `onnx`) |
| `dim`                 | INTEGER | NOT NULL         | --                | Embedding dimension                                                     |
| `chunk_strategy`      | TEXT    | NOT NULL, CHECK  | `'mechanical'`    | One of: `mechanical`, `semantic`                                        |
| `status`              | TEXT    | NOT NULL, CHECK  | --                | One of: `active`, `populating`, `deprecated`                            |
| `table_name`          | TEXT    | NOT NULL, UNIQUE | --                | Backing vec0 virtual table name                                         |
| `created_at`          | TEXT    | NOT NULL         | `datetime('now')` | ISO 8601 timestamp                                                      |
| `chunk_count`         | INTEGER | --               | `0`               | Vectors stored in the space's table                                     |
| `total_chunks`        | INTEGER | --               | --                | Total chunks targeted for backfill                                      |
| `matryoshka_base_dim` | INTEGER | --               | `NULL`            | Native model dimension for Matryoshka truncation (NULL = no truncation) |
| `element_type`        | TEXT    | NOT NULL, CHECK  | `'float32'`       | Vector element storage type; every space KB creates is `float32` (`int8` is reserved by ADR-0015 and may only appear in old rows) |
| `artifact_sha256`     | TEXT    | --               | `NULL`            | Schema v2. SHA-256 of the served model file (ADR-0015 served artifact)  |
| `pooling`             | TEXT    | --               | `NULL`            | Schema v2. Pooling the server applies (e.g. `last`)                     |
| `normalization`       | TEXT    | --               | `NULL`            | Schema v2. Normalization of each output vector (e.g. `l2`)              |
| `max_input_tokens`    | INTEGER | --               | `NULL`            | Schema v2. Maximum input length in tokens the server embeds             |
| `query_instruction`   | TEXT    | --               | `NULL`            | Schema v2. Text put before each query into this space: NULL = the model's default (KB's ADR-0015 §6 instruction for Qwen3-Embedding, none otherwise), `''` = none. Not identity |

**CHECK constraints:**

- `chunk_strategy IN ('mechanical', 'semantic')`
- `status IN ('active', 'populating', 'deprecated')`
- `element_type IN ('float32', 'int8')`
- `matryoshka_base_dim IS NULL OR matryoshka_base_dim > dim`

**Index:** `idx_embed_spaces_one_active` -- UNIQUE on `(status)` WHERE `status = 'active'` (enforces at most one active space)

### embed_refusals

Schema v3 (#579). The chunks a space's embedder answered with no usable vector (zero norm), so a backfill neither counts
nor resends them and promotion counts them as done.

| Column       | Type    | Constraints                                                     | Description              |
| ------------ | ------- | --------------------------------------------------------------- | ------------------------ |
| `chunk_id`   | INTEGER | NOT NULL, REFERENCES `chunks(id)` ON DELETE CASCADE             | The refused chunk        |
| `space_name` | TEXT    | NOT NULL, REFERENCES `embed_spaces(name)` ON DELETE CASCADE     | The space it was refused for |

**Primary key:** `(chunk_id, space_name)`. **Trigger:** `chunks_au_refusals` deletes a chunk's refusals when its
`content` changes, so new text is tried again.

### embed_truncations

Schema v4 (#620). The chunks a space stored cut to fit `KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS`, written with the vector,
so a backfill reports them even after it was interrupted and resumed. Ingest splits long chunks and never cuts one, so
only a chunk stored before that (#604) or under a larger limit can appear here.

| Column          | Type    | Constraints                                                 | Description                                  |
| --------------- | ------- | ----------------------------------------------------------- | -------------------------------------------- |
| `chunk_id`      | INTEGER | NOT NULL, REFERENCES `chunks(id)` ON DELETE CASCADE         | The chunk stored cut                         |
| `space_name`    | TEXT    | NOT NULL, REFERENCES `embed_spaces(name)` ON DELETE CASCADE | The space whose vector embeds only its start |
| `tokens_before` | INTEGER | NOT NULL                                                    | The chunk's tokens, as the embedder counts   |
| `tokens_after`  | INTEGER | NOT NULL                                                    | The tokens of the part it embedded           |

**Primary key:** `(chunk_id, space_name)`. **Trigger:** `chunks_au_truncations` deletes a chunk's records when its
`content` really changes (`WHEN old.content IS NOT new.content`): a metadata-only `redact --rewrite` keeps the vector
and its record.

---

### papers

Research paper metadata.

| Column              | Type    | Constraints               | Default           | Description                |
| ------------------- | ------- | ------------------------- | ----------------- | -------------------------- |
| `id`                | INTEGER | PRIMARY KEY AUTOINCREMENT | --                | Paper ID                   |
| `title`             | TEXT    | NOT NULL                  | --                | Paper title                |
| `authors`           | TEXT    | --                        | `'[]'`            | JSON array of author names |
| `year`              | INTEGER | --                        | --                | Publication year           |
| `venue`             | TEXT    | --                        | --                | Conference or journal      |
| `doi`               | TEXT    | UNIQUE                    | --                | Digital Object Identifier  |
| `bibtex`            | TEXT    | --                        | --                | Raw BibTeX entry           |
| `abstract_chunk_id` | INTEGER | FK -> chunks(id)          | --                | Link to abstract chunk     |
| `added_at`          | TEXT    | NOT NULL                  | `datetime('now')` | ISO 8601 timestamp         |

---

### paper_paths

Filesystem paths associated with papers. Supports multiple paths per paper (e.g., after relocation).

| Column         | Type    | Constraints                                  | Default           | Description                      |
| -------------- | ------- | -------------------------------------------- | ----------------- | -------------------------------- |
| `id`           | INTEGER | PRIMARY KEY AUTOINCREMENT                    | --                | Path record ID                   |
| `paper_id`     | INTEGER | NOT NULL, FK -> papers(id) ON DELETE CASCADE | --                | Owning paper                     |
| `path`         | TEXT    | UNIQUE                                       | --                | Absolute filesystem path         |
| `content_hash` | TEXT    | --                                           | --                | File content hash                |
| `is_primary`   | BOOLEAN | CHECK(is_primary IN (0, 1))                  | `TRUE`            | Whether this is the primary path |
| `added_at`     | TEXT    | NOT NULL                                     | `datetime('now')` | ISO 8601 timestamp               |

**Indexes:**

- `idx_paper_paths_paper_id` on `(paper_id, is_primary)`
- `idx_paper_paths_hash` on `(content_hash)`
- `idx_paper_paths_one_primary` -- UNIQUE on `(paper_id)` WHERE `is_primary = TRUE` (enforces at most one primary path per paper)

---

### relationships

Typed directed edges between papers.

| Column              | Type    | Constraints                | Default           | Description               |
| ------------------- | ------- | -------------------------- | ----------------- | ------------------------- |
| `id`                | INTEGER | PRIMARY KEY AUTOINCREMENT  | --                | Relationship ID           |
| `source_paper_id`   | INTEGER | NOT NULL, FK -> papers(id) | --                | Source paper              |
| `target_paper_id`   | INTEGER | NOT NULL, FK -> papers(id) | --                | Target paper              |
| `relation_type`     | TEXT    | NOT NULL, CHECK            | --                | Relationship type         |
| `confidence`        | REAL    | --                         | `1.0`             | Confidence score 0.0--1.0 |
| `evidence_chunk_id` | INTEGER | FK -> chunks(id)           | --                | Supporting evidence chunk |
| `created_at`        | TEXT    | NOT NULL                   | `datetime('now')` | ISO 8601 timestamp        |

**CHECK constraint:** `relation_type IN ('extends', 'contradicts', 'replicates', 'cites', 'compares', 'applies', 'implements', 'similar')`

**UNIQUE constraint:** `(source_paper_id, target_paper_id, relation_type)`

---

### papers_fts

{term}`FTS5` virtual table for full-text search over paper titles. Content-synced with `papers` via triggers.

| Column  | Type | Description                                |
| ------- | ---- | ------------------------------------------ |
| `title` | TEXT | Indexed title (synced from `papers.title`) |

**Configuration:** `content='papers'`, `content_rowid='id'`, `tokenize='porter unicode61'`

**Triggers:**

- `papers_ai` -- AFTER INSERT on papers: inserts into FTS index
- `papers_ad` -- AFTER DELETE on papers: removes from FTS index
- `papers_au` -- AFTER UPDATE OF title on papers: removes old, inserts new

---

### conclusions

Evidence-chained analytical conclusions. Supports {term}`supersession` chains where newer conclusions replace older ones.

| Column             | Type    | Constraints               | Default           | Description                                      |
| ------------------ | ------- | ------------------------- | ----------------- | ------------------------------------------------ |
| `id`               | INTEGER | PRIMARY KEY AUTOINCREMENT | --                | Conclusion ID                                    |
| `claim`            | TEXT    | NOT NULL                  | --                | The conclusion claim text                        |
| `confidence`       | REAL    | --                        | `1.0`             | Confidence score 0.0--1.0                        |
| `source_chunk_ids` | TEXT    | NOT NULL                  | `'[]'`            | JSON array of evidence chunk IDs                 |
| `session_context`  | TEXT    | --                        | --                | Context for why this conclusion was drawn        |
| `created_at`       | TEXT    | NOT NULL                  | `datetime('now')` | ISO 8601 timestamp                               |
| `superseded_by`    | INTEGER | FK -> conclusions(id)     | --                | ID of the replacing conclusion (NULL if current) |

---

### executions

Task execution records linked to conclusions.

| Column           | Type    | Constraints               | Default           | Description                  |
| ---------------- | ------- | ------------------------- | ----------------- | ---------------------------- |
| `id`             | INTEGER | PRIMARY KEY AUTOINCREMENT | --                | Execution ID                 |
| `task`           | TEXT    | NOT NULL                  | --                | Task description             |
| `result_summary` | TEXT    | --                        | --                | Summary of execution result  |
| `conclusion_ids` | TEXT    | NOT NULL                  | `'[]'`            | JSON array of conclusion IDs |
| `created_at`     | TEXT    | NOT NULL                  | `datetime('now')` | ISO 8601 timestamp           |

---

### methods

Research methods recorded from papers (manual or LLM-extracted).

| Column        | Type    | Constraints                | Default | Description             |
| ------------- | ------- | -------------------------- | ------- | ----------------------- |
| `id`          | INTEGER | PRIMARY KEY AUTOINCREMENT  | --      | Method ID               |
| `name`        | TEXT    | NOT NULL                   | --      | Method name             |
| `paper_id`    | INTEGER | NOT NULL, FK -> papers(id) | --      | Paper using this method |
| `description` | TEXT    | --                         | --      | Brief description       |
| `chunk_id`    | INTEGER | FK -> chunks(id)           | --      | Source chunk reference  |

**UNIQUE constraint:** `(name, paper_id)`

---

### datasets

Datasets recorded from papers (manual or LLM-extracted).

| Column        | Type    | Constraints                | Default | Description              |
| ------------- | ------- | -------------------------- | ------- | ------------------------ |
| `id`          | INTEGER | PRIMARY KEY AUTOINCREMENT  | --      | Dataset ID               |
| `name`        | TEXT    | NOT NULL                   | --      | Dataset name             |
| `paper_id`    | INTEGER | NOT NULL, FK -> papers(id) | --      | Paper using this dataset |
| `description` | TEXT    | --                         | --      | Brief description        |
| `chunk_id`    | INTEGER | FK -> chunks(id)           | --      | Source chunk reference   |

**UNIQUE constraint:** `(name, paper_id)`

---

### metrics

Quantitative results from papers, linking methods to datasets.

| Column       | Type    | Constraints                | Default | Description                      |
| ------------ | ------- | -------------------------- | ------- | -------------------------------- |
| `id`         | INTEGER | PRIMARY KEY AUTOINCREMENT  | --      | Metric ID                        |
| `name`       | TEXT    | NOT NULL                   | --      | Metric name (e.g. accuracy, F1)  |
| `value`      | REAL    | NOT NULL                   | --      | Numeric value                    |
| `unit`       | TEXT    | --                         | --      | Unit of measurement              |
| `dataset_id` | INTEGER | FK -> datasets(id)         | --      | Dataset measured on              |
| `method_id`  | INTEGER | FK -> methods(id)          | --      | Method that achieved this result |
| `paper_id`   | INTEGER | NOT NULL, FK -> papers(id) | --      | Reporting paper                  |
| `chunk_id`   | INTEGER | FK -> chunks(id)           | --      | Source chunk reference           |

---

### entities

Resolved entities from LLM extraction. Each entity has a canonical name and type, linked to a paper.

| Column           | Type    | Constraints                | Default | Description                           |
| ---------------- | ------- | -------------------------- | ------- | ------------------------------------- |
| `id`             | INTEGER | PRIMARY KEY AUTOINCREMENT  | --      | Entity ID                             |
| `canonical_name` | TEXT    | NOT NULL                   | --      | Resolved canonical name               |
| `entity_type`    | TEXT    | NOT NULL, CHECK            | --      | One of: `method`, `dataset`, `metric` |
| `paper_id`       | INTEGER | NOT NULL, FK -> papers(id) | --      | Paper this entity belongs to          |
| `description`    | TEXT    | --                         | --      | Entity description                    |

**CHECK constraint:** `entity_type IN ('method', 'dataset', 'metric')`

**UNIQUE constraint:** `(canonical_name, entity_type, paper_id)`

---

### entity_mentions

{term}`Surface form`s of entities found in specific chunks. Multiple surface forms can map to the same entity ({term}`entity resolution`).

| Column         | Type    | Constraints                  | Default | Description                     |
| -------------- | ------- | ---------------------------- | ------- | ------------------------------- |
| `id`           | INTEGER | PRIMARY KEY AUTOINCREMENT    | --      | Mention ID                      |
| `entity_id`    | INTEGER | NOT NULL, FK -> entities(id) | --      | Resolved entity                 |
| `surface_form` | TEXT    | NOT NULL                     | --      | Text as it appears in the chunk |
| `chunk_id`     | INTEGER | NOT NULL, FK -> chunks(id)   | --      | Chunk containing this mention   |
| `confidence`   | REAL    | --                           | `1.0`   | Confidence of the resolution    |

**UNIQUE constraint:** `(entity_id, surface_form, chunk_id)`

---

### jobs

Background extraction job tracking.

| Column         | Type    | Constraints                                  | Default           | Description                                                   |
| -------------- | ------- | -------------------------------------------- | ----------------- | ------------------------------------------------------------- |
| `id`           | INTEGER | PRIMARY KEY AUTOINCREMENT                    | --                | Job ID                                                        |
| `paper_id`     | INTEGER | NOT NULL, FK -> papers(id) ON DELETE CASCADE | --                | Target paper                                                  |
| `job_type`     | TEXT    | NOT NULL, CHECK                              | --                | One of: `extract_structure`, `extract_figures`, `auto_relate` |
| `params`       | TEXT    | NOT NULL                                     | `'{}'`            | JSON parameters for the job                                   |
| `status`       | TEXT    | NOT NULL, CHECK                              | `'pending'`       | One of: `pending`, `running`, `completed`, `failed`           |
| `progress`     | TEXT    | --                                           | --                | Current progress message                                      |
| `result`       | TEXT    | --                                           | --                | JSON result on completion                                     |
| `error`        | TEXT    | --                                           | --                | Error message on failure                                      |
| `created_at`   | TEXT    | NOT NULL                                     | `datetime('now')` | Job creation time                                             |
| `started_at`   | TEXT    | --                                           | --                | Job start time                                                |
| `completed_at` | TEXT    | --                                           | --                | Job completion time                                           |

**CHECK constraints:**

- `job_type IN ('extract_structure', 'extract_figures', 'auto_relate')`
- `status IN ('pending', 'running', 'completed', 'failed')`

**Index:** `idx_jobs_status_created` on `(status, created_at)`

---

### prediction_errors

Search observability: queries where the best result fell below a confidence threshold or returned no results.

| Column               | Type    | Constraints                         | Default           | Description                                            |
| -------------------- | ------- | ----------------------------------- | ----------------- | ------------------------------------------------------ |
| `id`                 | INTEGER | PRIMARY KEY AUTOINCREMENT           | --                | Error ID                                               |
| `query`              | TEXT    | NOT NULL                            | --                | Normalized query text                                  |
| `query_hash`         | TEXT    | NOT NULL                            | --                | SHA-256 of normalized query (for dedup/rate-limit)     |
| `top_score`          | REAL    | --                                  | --                | RRF score of the top result (NULL if no results)       |
| `top_chunk_id`       | INTEGER | FK -> chunks(id) ON DELETE SET NULL | --                | Top result chunk (NULL if no results or chunk deleted) |
| `error_type`         | TEXT    | NOT NULL, CHECK                     | --                | One of: `low_confidence`, `no_results`                 |
| `source_type_filter` | TEXT    | --                                  | --                | Source type filter applied (NULL if unfiltered)        |
| `detected_at`        | TEXT    | NOT NULL                            | `datetime('now')` | ISO 8601 timestamp                                     |
| `resolved_at`        | TEXT    | --                                  | --                | ISO 8601 timestamp when resolved (NULL if open)        |

**CHECK constraint:** `error_type IN ('low_confidence', 'no_results')`

**Indexes:**

- `idx_prediction_errors_hash_type` on `(query_hash, error_type, detected_at)` -- rate-limit lookups
- `idx_prediction_errors_unresolved` on `(resolved_at)` WHERE `resolved_at IS NULL` -- unresolved error queries
