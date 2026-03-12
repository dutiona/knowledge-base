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

## Status

Use the `status` tool to get index statistics including total chunks, counts by type, paper/conclusion/relationship counts, embedding config, and recent ingestions:

```json
{ "name": "status" }
```
