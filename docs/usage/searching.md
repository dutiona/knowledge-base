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

## Folder Context Boosting

Search results from semantically relevant directories receive an automatic relevance boost. This leverages folder-level summaries to inject structural context into retrieval without requiring explicit tagging.

**What:** After RRF merging but before final ranking, the query embedding is compared against folder-level summary embeddings. Chunks from top-matching folders get a 1.15x score multiplier, which can promote them into the final top-k window.

**Why:** Research knowledge bases often organize papers by topic or project. Folder structure carries implicit semantic signal — a query about "attention mechanisms" should naturally favor results from a `transformers/` or `attention/` directory. This idea is adapted from NeuroStack's vault-level folder summaries (see issue #126).

**How it works:**

1. During ingestion, each file's parent folder gets a summary built from the first chunk of each document in that folder.
2. The summary is embedded and stored in `folder_summaries_vec`.
3. A content hash (from sorted chunk hashes) detects staleness — no recomputation when folder contents haven't changed.
4. At search time, the top 5 matching folders (by cosine distance) are identified, and chunks from those folders receive the boost.

Folder summaries only include **direct children** — files in subdirectories are excluded to keep summaries focused and prevent dilution across nested hierarchies.

The boost is conservative (1.15x) and only applies in `hybrid` and `vec` modes. In `fts` mode, no embedding comparison is available, so no folder boost is applied.

## Cross-Encoder Reranking

After RRF merging, an optional cross-encoder reranking stage scores each (query, candidate) pair jointly, capturing fine-grained query-document interactions that bi-encoder similarity and BM25 cannot express. This is a classic stage-2 precision pass: the bi-encoder + RRF stage retrieves a broad candidate set cheaply, and the cross-encoder re-scores them with full attention over both sequences.

**Why it helps:** Bi-encoders embed query and document independently -- they never see the interaction between the two. A cross-encoder feeds the concatenated pair through a single transformer, so it can detect subtle relevance signals (negation, qualifier scope, argument structure) that independent embeddings miss.

### Setup

1. Install the optional dependency group:

   ```bash
   uv sync --group reranker
   ```

2. Export a cross-encoder model to ONNX:

   ```bash
   optimum-cli export onnx --model cross-encoder/ms-marco-MiniLM-L-6-v2 reranker-model/
   ```

3. Set environment variables:

   ```bash
   export ONNX_RERANK_MODEL_PATH=reranker-model/model.onnx
   export ONNX_RERANK_TOKENIZER_PATH=reranker-model/
   ```

### Usage

Set `rerank: true` in the search tool call:

```json
{
  "name": "search_index",
  "arguments": {
    "query": "how does flash attention reduce memory usage",
    "top_k": 10,
    "rerank": true
  }
}
```

### Latency Budget

| Stage              | Typical Latency |
| ------------------ | --------------- |
| FTS5 BM25          | ~20 ms          |
| sqlite-vec cosine  | ~30 ms          |
| RRF merge          | <5 ms           |
| Cross-encoder      | ~100 ms         |
| **Total (rerank)** | **~155 ms**     |

Without reranking the pipeline completes in ~55 ms. The cross-encoder adds ~100 ms but operates only on the top-k candidates, not the full index.

### When to Use

- Complex natural language queries where word-level interaction matters ("methods that improve latency _without_ sacrificing accuracy")
- High-stakes retrieval where top-3 precision is critical
- Queries where initial RRF results contain near-misses that need re-scoring

### When NOT to Use

- Simple keyword lookups ("ResNet-50 ImageNet top-1") -- RRF is already precise enough
- Latency-sensitive batch pipelines where the extra ~100 ms per query matters
- When `onnxruntime` or the ONNX model is not available -- the flag is silently ignored if the reranker is not configured

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
- Use `chunk_strategy` filtering to target a specific chunking granularity (e.g., `"semantic"` for section-level chunks from 32K models, `"mechanical"` for traditional 1K chunks). By default, no strategy filter is applied -- all chunks are returned regardless of how they were split. See [Ingesting Documents: Chunking Strategy](ingesting-documents.md#chunking-strategy) for configuration.

## Embedding Provider

Vector search uses the embedding provider configured in the database. By default, this is Ollama (BGE-M3), but OpenAI and ONNX Runtime are also supported. The query embedding is generated using the same provider and model as the indexed chunks -- mismatched providers will produce poor results. See [Ingesting Documents: Embedding Providers](ingesting-documents.md#embedding-providers) for configuration details.

## Status

Use the `status` tool to get index statistics including total chunks, counts by type, paper/conclusion/relationship counts, embedding config, and recent ingestions:

```json
{ "name": "status" }
```
