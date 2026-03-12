# Core Concepts

## Domain model

### Chunks

The atomic unit of content. Every document is split into chunks for indexing and
retrieval. Each chunk has:

- **content** -- the text (typically ~1000 characters for prose, AST-aware boundaries
  for Python code)
- **content_hash** -- SHA-256 prefix for deduplication (if a chunk with the same hash
  exists, it is skipped on ingest)
- **embedding** -- a dense vector (1024-dim BGE-M3 by default) stored in `chunks_vec`
- **FTS5 index** -- full-text search via SQLite FTS5 with Porter stemming and Unicode
  tokenization
- **source_type** -- one of: `pdf`, `markdown`, `code`, `web`, `note`, `figure`
- **source_uri** -- the file path or URL the chunk came from
- **chunk_index** -- position within the source document (figure chunks use a special
  encoding: `1_000_000 + page * 1000 + fig_idx`)

### Papers

Metadata records for research papers. A paper stores:

- **title**, **authors** (JSON array), **year**, **venue**, **doi**
- **bibtex** -- raw BibTeX entry for export
- **abstract_chunk_id** -- optional link to the abstract chunk

Papers link to chunks indirectly through the `paper_paths` table: a paper can have
multiple file paths, and chunks are matched by `source_uri`. This decouples paper
metadata from ingestion.

### Entities

Resolved names for methods and datasets, scoped per-paper. Entity resolution maps
surface forms (aliases, abbreviations) to canonical names using an LLM during
structured extraction.

For example, "our method", "CNN-LSTM", and "the proposed approach" might all resolve
to the canonical entity "CNN-LSTM Encoder".

Entities are stored in the `entities` table. Each entity has:

- **canonical_name** -- the resolved primary name
- **entity_type** -- `method` or `dataset` (the schema also allows `metric`, but
  metrics are stored directly in the `metrics` table without entity resolution)
- **paper_id** -- the paper this entity belongs to

Surface forms are tracked in `entity_mentions`, linking each alias to its entity
and the chunk where it appeared.

### Relationships

Typed directed edges between papers. Each relationship has:

- **source_paper_id** and **target_paper_id**
- **relation_type** -- one of: `extends`, `contradicts`, `replicates`, `cites`,
  `compares`, `applies`, `implements`
- **confidence** -- score from 0.0 to 1.0 (default 1.0)
- **evidence_chunk_id** -- optional link to the chunk containing evidence

Relationships are unique per (source, target, type) triple. Use
`suggest_relationships_tool` to auto-detect citation relationships by matching DOIs,
title words, and author+year patterns.

### Conclusions

Evidence-chained claims with supersession. A conclusion stores:

- **claim** -- the assertion text
- **confidence** -- score from 0.0 to 1.0
- **source_chunk_ids** -- JSON array of chunk IDs serving as evidence
- **session_context** -- why this conclusion was drawn
- **superseded_by** -- pointer to a newer conclusion that replaces this one

Conclusions form chains: when understanding evolves, supersede the old conclusion
with a new one rather than deleting it. The full chain is retrievable via
`get_conclusion_chain_tool`.

### Jobs

Background tasks for expensive operations. Two job types:

- **extract_structure** -- LLM-based extraction of methods, datasets, metrics
- **extract_figures** -- vision model analysis of PDF pages

Jobs go through states: `pending` -> `running` -> `completed` | `failed`.
Poll progress with `get_job_status_tool(job_id)`. Jobs are queued when the estimated
time exceeds 2 minutes.

## Hybrid search

Search combines two retrieval strategies and merges them with Reciprocal Rank Fusion.

**BM25 (FTS5)** -- keyword matching using SQLite's FTS5 module with Porter stemming
and Unicode tokenization. Fast, exact-match-friendly, handles technical terms well.

**Cosine similarity (sqlite-vec)** -- semantic matching using dense embedding vectors.
Finds conceptually related content even without keyword overlap.

**Reciprocal Rank Fusion (RRF)** -- merges both ranked lists. For each result, the
RRF score is:

```
score = sum(1 / (k + rank + 1))  for each list containing the result
```

where `k = 60` (smoothing constant). The search over-fetches 3x the requested
`top_k` from each retrieval leg before merging, then returns the top results.

Three modes are available via the `mode` parameter:

- `hybrid` (default) -- BM25 + cosine, merged via RRF
- `fts` -- BM25 keyword search only
- `vec` -- semantic vector search only

## Entity-relationship diagram

```{mermaid}
erDiagram
    papers ||--o{ paper_paths : "has paths"
    papers ||--o{ relationships : "source/target"
    papers ||--o{ methods : "uses"
    papers ||--o{ datasets : "uses"
    papers ||--o{ metrics : "reports"
    papers ||--o{ entities : "has entities"

    chunks ||--o{ conclusions : "evidences"
    chunks ||--o| papers : "abstract_chunk"

    entities ||--o{ entity_mentions : "mentioned as"
    entity_mentions }o--|| chunks : "found in"

    methods ||--o{ metrics : "achieves"
    datasets ||--o{ metrics : "measured on"
    methods }o--o| chunks : "described in"
    datasets }o--o| chunks : "described in"

    relationships }o--o| chunks : "evidence"

    conclusions }o--o| conclusions : "superseded_by"

    papers {
        int id PK
        text title
        text authors
        int year
        text venue
        text doi UK
        text bibtex
    }

    chunks {
        int id PK
        text content_hash UK
        text content
        text source_type
        text source_uri
        int chunk_index
    }

    entities {
        int id PK
        text canonical_name
        text entity_type
        int paper_id FK
    }

    entity_mentions {
        int id PK
        int entity_id FK
        text surface_form
        int chunk_id FK
    }

    relationships {
        int id PK
        int source_paper_id FK
        int target_paper_id FK
        text relation_type
        real confidence
    }

    conclusions {
        int id PK
        text claim
        real confidence
        text source_chunk_ids
        int superseded_by FK
    }

    methods {
        int id PK
        text name
        int paper_id FK
    }

    datasets {
        int id PK
        text name
        int paper_id FK
    }

    metrics {
        int id PK
        text name
        real value
        text unit
        int method_id FK
        int dataset_id FK
        int paper_id FK
    }

    jobs {
        int id PK
        int paper_id FK
        text job_type
        text status
    }

    paper_paths {
        int id PK
        int paper_id FK
        text path UK
        text content_hash
        bool is_primary
    }
```
