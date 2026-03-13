# Database Schema Reference

SQLite database with {term}`FTS5` full-text search, {term}`sqlite-vec` vector similarity, and {term}`WAL mode`.

Default location: `~/.local/share/research-index/research.db`

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

    papers ||--o{ paper_paths : "has paths"
    papers ||--o{ relationships : "source"
    papers ||--o{ relationships : "target"
    papers ||--o{ methods : "uses"
    papers ||--o{ datasets : "uses"
    papers ||--o{ metrics : "reports"
    papers ||--o{ entities : "mentions"
    papers ||--o{ jobs : "has jobs"
    papers }o--o| chunks : "abstract_chunk"
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

Populated on first run with:

| Key            | Default Value |
| -------------- | ------------- |
| `embed_model`  | `bge-m3`      |
| `embed_dim`    | `1024`        |
| `llm_provider` | `ollama`      |
| `llm_model`    | `qwen3.5:27b` |

Additional keys set via configure tools: `llm_base_url`, `llm_api_key`, `vision_model`, `vision_base_url`, `omniparser_path`, `browser_mode`, `browser_endpoint`, `browser_venv`.

---

### chunks

Primary content storage. Each row is one chunk of ingested text.

| Column         | Type    | Constraints               | Default           | Description                                                           |
| -------------- | ------- | ------------------------- | ----------------- | --------------------------------------------------------------------- |
| `id`           | INTEGER | PRIMARY KEY AUTOINCREMENT | --                | Chunk ID                                                              |
| `content_hash` | TEXT    | NOT NULL, UNIQUE          | --                | SHA-256 {term}`content hash` for deduplication                        |
| `content`      | TEXT    | NOT NULL                  | --                | Raw text content                                                      |
| `source_type`  | TEXT    | NOT NULL, CHECK           | --                | One of: `pdf`, `markdown`, `code`, `web`, `note`, `figure`            |
| `source_uri`   | TEXT    | NOT NULL                  | --                | File path or URL of the source                                        |
| `chunk_index`  | INTEGER | NOT NULL                  | --                | Position within the source (0-based; figures use 1,000,000+ encoding) |
| `created_at`   | TEXT    | NOT NULL                  | `datetime('now')` | ISO 8601 timestamp                                                    |
| `metadata`     | TEXT    | --                        | `'{}'`            | JSON metadata blob                                                    |

**CHECK constraint:** `source_type IN ('pdf', 'markdown', 'code', 'web', 'note', 'figure')`

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

{term}`sqlite-vec` virtual table for vector similarity search.

| Column      | Type     | Description                                                 |
| ----------- | -------- | ----------------------------------------------------------- |
| `embedding` | float[N] | Vector embedding (N = configured `embed_dim`, default 1024) |
| `chunk_id`  | INTEGER  | Auxiliary column linking to `chunks.id`                     |

Dimension is read from `config.embed_dim` at schema creation time. Dropped and recreated by `re_embed_tool`.

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

**CHECK constraint:** `relation_type IN ('extends', 'contradicts', 'replicates', 'cites', 'compares', 'applies', 'implements')`

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

| Column         | Type    | Constraints                                  | Default           | Description                                         |
| -------------- | ------- | -------------------------------------------- | ----------------- | --------------------------------------------------- |
| `id`           | INTEGER | PRIMARY KEY AUTOINCREMENT                    | --                | Job ID                                              |
| `paper_id`     | INTEGER | NOT NULL, FK -> papers(id) ON DELETE CASCADE | --                | Target paper                                        |
| `job_type`     | TEXT    | NOT NULL, CHECK                              | --                | One of: `extract_structure`, `extract_figures`      |
| `params`       | TEXT    | NOT NULL                                     | `'{}'`            | JSON parameters for the job                         |
| `status`       | TEXT    | NOT NULL, CHECK                              | `'pending'`       | One of: `pending`, `running`, `completed`, `failed` |
| `progress`     | TEXT    | --                                           | --                | Current progress message                            |
| `result`       | TEXT    | --                                           | --                | JSON result on completion                           |
| `error`        | TEXT    | --                                           | --                | Error message on failure                            |
| `created_at`   | TEXT    | NOT NULL                                     | `datetime('now')` | Job creation time                                   |
| `started_at`   | TEXT    | --                                           | --                | Job start time                                      |
| `completed_at` | TEXT    | --                                           | --                | Job completion time                                 |

**CHECK constraints:**

- `job_type IN ('extract_structure', 'extract_figures')`
- `status IN ('pending', 'running', 'completed', 'failed')`

**Index:** `idx_jobs_status_created` on `(status, created_at)`
