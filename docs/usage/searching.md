# Searching

## Hybrid Search (Default)

The default search mode combines two retrieval strategies and merges results using Reciprocal Rank Fusion:

1. **FTS5 BM25** -- keyword matching via SQLite's full-text search with Porter stemming and Unicode tokenization.
2. **sqlite-vec cosine** -- semantic similarity using the query's embedding against stored chunk embeddings.

Both legs over-fetch 3x the requested `top_k` results, then RRF merges them into a single ranked list.

```json
{
  "name": "search_index",
  "arguments": { "query": "attention mechanism transformer", "top_k": 10 }
}
```

## FTS-Only Mode

Keyword search only. Uses FTS5 BM25 scoring. Useful when you want exact term matching without semantic expansion.

```json
{
  "name": "search_index",
  "arguments": { "query": "ResNet-50 accuracy", "mode": "fts" }
}
```

If the FTS query contains syntax errors (e.g., unbalanced quotes), the FTS leg silently returns zero results rather than raising an error.

## Vec-Only Mode

Semantic search only. Embeds the query and finds the nearest neighbors by cosine distance. Useful when the exact terminology is unknown.

```json
{
  "name": "search_index",
  "arguments": {
    "query": "methods for reducing model size at inference time",
    "mode": "vec"
  }
}
```

## Reciprocal Rank Fusion (RRF)

RRF merges ranked lists without requiring score normalization. For each result at 1-based rank `r`, its RRF contribution is `1 / (k + r)` where `k = 60`. Results appearing in both FTS and vec lists accumulate scores from both, pushing them higher in the merged ranking.

If only one leg returns results (e.g., FTS query syntax error causes FTS to return nothing), the other leg's results are used directly with RRF-style scoring.

## Keyword Pre-filter

When `keyword_prefilter` is set to `true`, the search engine extracts high-level intent keywords from the query before running the FTS5 leg. This strips stopwords, context-specific filler, and query phrasing artifacts, producing a focused OR query of content terms.

This is based on research from **Agentic Plan Caching** (Zhang et al., Stanford, NeurIPS 2025, arXiv:2506.14852, §3.2): keyword-based matching on extracted intent outperforms full-query semantic similarity for identifying relevant documents. Full queries overemphasize entity names and context-specific details rather than broader intent — their Figure 3 shows lower false positive AND false negative rates with keyword extraction at all similarity thresholds.

```json
{
  "name": "search_index",
  "arguments": {
    "query": "What are the best practices for Rust error handling in async code?",
    "keyword_prefilter": true
  }
}
```

The above query extracts keywords like `rust`, `error`, `async`, `practices` and uses those for BM25 matching, while the full query is still used for semantic vector search.

**When to use:**

- Verbose natural language questions ("What methods exist for...") — keyword extraction removes question structure
- Queries mixing intent with specific entities — extraction focuses on the intent terms

**When NOT to use:**

- Short, precise queries ("ResNet-50 ImageNet accuracy") — these are already mostly keywords
- FTS-only mode with exact term requirements — the raw query gives more control

## Source Type Filtering

Restrict results to a specific content type:

```json
{
  "name": "search_index",
  "arguments": {
    "query": "batch normalization",
    "source_type": "pdf"
  }
}
```

Valid source types: `pdf`, `markdown`, `code`, `web`, `note`, `figure`.

Filtering is applied after RRF merging, which means the final result count may be less than `top_k` if many top results are of other types.

## Result Format

Each result contains:

| Field         | Description                                                           |
| ------------- | --------------------------------------------------------------------- |
| `chunk_id`    | Unique chunk identifier                                               |
| `content`     | Full chunk text                                                       |
| `source_type` | One of: pdf, markdown, code, web, note, figure                        |
| `source_uri`  | File path or URL the chunk was ingested from                          |
| `chunk_index` | Position within the source (0-based for text; 1,000,000+ for figures) |
| `score`       | RRF score (rounded to 6 decimal places)                               |
| `match_type`  | `hybrid`, `fts`, or `vec` depending on which legs contributed         |

## Query Tips

- **Hybrid mode** works best for most queries -- it catches both exact term matches and semantic paraphrases.
- **FTS mode** is better for specific identifiers, method names, or dataset names that must match exactly.
- **Vec mode** is better for conceptual queries ("methods that improve training stability") where exact terms vary across papers.
- Increase `top_k` if you need broader recall. The default is 10.
- Use `source_type` filtering to scope results (e.g., only `pdf` for published papers, only `code` for implementations).

## Embedding Provider

Vector search uses the embedding provider configured in the database. By default, this is Ollama (BGE-M3), but OpenAI and ONNX Runtime are also supported. The query embedding is generated using the same provider and model as the indexed chunks -- mismatched providers will produce poor results. See [Ingesting Documents: Embedding Providers](ingesting-documents.md#embedding-providers) for configuration details.

## Status

Use the `status` tool to get index statistics including total chunks, counts by type, paper/conclusion/relationship counts, embedding config, and recent ingestions:

```json
{ "name": "status" }
```
