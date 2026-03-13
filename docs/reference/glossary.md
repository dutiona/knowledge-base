# Glossary

```{glossary}
BM25
    A probabilistic ranking function used by {term}`FTS5` for full-text search scoring. Measures term frequency, inverse document frequency, and document length normalization to rank chunks by keyword relevance.

chunk
    A segment of ingested text stored in the `chunks` table. Each chunk has a {term}`content hash` for deduplication, a source type, and both a full-text index entry ({term}`FTS5`) and a vector embedding ({term}`sqlite-vec`).

content hash
    SHA-256 hash of a {term}`chunk`'s text content, used for deduplication. If a chunk with the same hash already exists, it is skipped during ingestion.

cosine similarity
    A metric measuring the angle between two embedding vectors, ranging from -1 (opposite) to 1 (identical). Used by {term}`sqlite-vec` for semantic search over {term}`chunk` embeddings.

entity
    A resolved concept (method, dataset, or metric) extracted from a paper by the LLM extraction pipeline. Each entity has a canonical name and is linked to one or more {term}`entity mention`s.

entity mention
    An occurrence of an {term}`entity` in a specific {term}`chunk`, recorded with its {term}`surface form` and a confidence score. Multiple mentions with different surface forms can resolve to the same entity.

entity resolution
    The process of mapping different {term}`surface form`s to the same canonical {term}`entity`. For example, "ResNet-50", "ResNet50", and "residual network (50 layers)" may all resolve to the same entity.

evidence chain
    A sequence of {term}`chunk` references that support an analytical conclusion. Stored as a JSON array of chunk IDs in the `conclusions.source_chunk_ids` column, linking claims to their source material.

FTS5
    SQLite's full-text search extension (version 5). Provides {term}`BM25`-ranked keyword search over chunk content and paper titles. Uses Porter stemming and Unicode61 tokenization.

hybrid search
    The default search mode combining {term}`FTS5` keyword results with {term}`sqlite-vec` semantic results via {term}`Reciprocal Rank Fusion (RRF)`. Balances exact keyword matching with conceptual similarity.

map-reduce extraction
    The LLM extraction strategy used by `extract_structure_tool`. Each {term}`chunk` is independently analyzed by the LLM (map phase), then results are deduplicated and merged across chunks (reduce phase) to produce consolidated methods, datasets, and metrics.

MCP
    Model Context Protocol. A standard for exposing tools and resources to AI assistants. knowledge-base runs as an MCP server, making its 33 tools available to any MCP-compatible client.

OmniParser
    A local tool for detecting UI elements, OCR text, and icons in images. When configured, enriches figure descriptions produced by the vision model with detected text and element labels. Requires a separate installation with its own Python venv.

Reciprocal Rank Fusion (RRF)
    A rank aggregation algorithm that merges results from multiple ranked lists. For each result, its RRF score is the sum of `1/(k + rank)` across all lists where it appears. Used in {term}`hybrid search` to combine {term}`FTS5` and {term}`sqlite-vec` results.

sqlite-vec
    A SQLite extension providing vector similarity search. Stores float embeddings in a virtual table (`chunks_vec`) and supports nearest-neighbor queries via {term}`cosine similarity`. Powers the semantic component of {term}`hybrid search`.

supersession
    The mechanism by which a newer conclusion replaces an older one while preserving history. The old conclusion's `superseded_by` column points to the new one, forming a chain that can be traversed with `get_conclusion_chain_tool`.

surface form
    The exact text of an {term}`entity` as it appears in a {term}`chunk`. Different surface forms (e.g. "BERT", "bert-base-uncased", "Bidirectional Encoder Representations") can be resolved to the same canonical entity via {term}`entity resolution`.

WAL mode
    Write-Ahead Logging mode for SQLite. Enables concurrent readers with a single writer, improving performance for the MCP server's mixed read/write workload. Set via `PRAGMA journal_mode=WAL` at connection time.
```
