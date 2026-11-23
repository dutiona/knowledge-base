# Embedding Spaces

## Why

The single-space architecture (one `chunks_vec` table, one model at a time) creates three problems:

1. **Search downtime during model swaps.** `re_embed()` drops the vec table and recreates it — search is unavailable until all chunks are re-embedded. For a 10K-chunk corpus with a decoder-based model, this can take hours.

2. **No safe experimentation.** Evaluating a new embedding model (e.g., switching from BGE-M3 to Qwen3-Embedding-0.6B) requires committing fully — there's no way to A/B compare search quality before switching, and no rollback if the new model underperforms.

3. **No per-model chunking.** Different models benefit from different chunk granularities. BGE-M3 (8K context, encoder-based, fast) works well with mechanical 1000-char chunks. Qwen3-Embedding (32K context, decoder-based, [~5x slower via Ollama](https://github.com/ollama/ollama/issues/12088)) could embed entire paper sections as single chunks — but the single-space architecture forces one chunking strategy for all models.

These limitations were identified during embedding model comparison research (BGE-M3 vs Qwen3-Embedding-0.6B vs nomic-embed-text-v2-moe), analysis of community re-embedding workflows, and the four-layer cognitive architecture review. See [#99](https://github.com/dutiona/knowledge-base/issues/99) for the full research context, including MTEB benchmark comparisons and the Matryoshka dimension trade-off analysis.

### Research context: model comparison

| Model                   | Dims         | Context | Matryoshka | Speed                | MTEB   |
| ----------------------- | ------------ | ------- | ---------- | -------------------- | ------ |
| BGE-M3 (current)        | 1024 (fixed) | 8K      | No         | Fast (encoder)       | 63.0   |
| Qwen3-Embedding-0.6B    | 32–1024      | **32K** | Yes        | ~5x slower (decoder) | 64.33  |
| nomic-embed-text-v2-moe | 256–768      | 8K      | Yes        | Unknown (MoE)        | ~63–64 |

The multi-space architecture makes it possible to benchmark these models on the actual corpus before committing, and to bind each model to its optimal chunking strategy.

## What

The multi-space embedding architecture allows multiple embedding models to coexist in the same database. Each **embedding space** is backed by its own `sqlite-vec` virtual table (`chunks_vec_<name>`) and tracked in the `embed_spaces` registry table. Exactly one space is **active** at any time — ingestion and search use the active space's table.

This enables:

- **Zero-downtime migration** — backfill a new model while the old one serves queries, then promote atomically.
- **A/B comparison** — create two spaces with different models or dimensions, backfill both, and compare search quality before committing.
- **Rollback** — if a new model underperforms, promote the old (deprecated) space back to active.
- **Per-model chunk granularity** — a 32K-context space can use `semantic` chunks (section-level, from #100), while an 8K space uses `mechanical` chunks.

## How

### Lifecycle

Every embedding space moves through three statuses:

```
populating  -->  active  -->  deprecated  -->  (cleaned up)
```

- **populating** -- the space exists and its vec table is being backfilled with embeddings.
- **active** -- the space is used for all ingestion and search. Exactly one space can be active (enforced by a partial unique index).
- **deprecated** -- the space is retained for comparison or rollback but not used by default.

### Step-by-step workflow

#### 1. Create a space

```json
{
  "name": "create_embed_space_tool",
  "arguments": {
    "name": "bge_m3_1024",
    "model": "bge-m3",
    "dim": 1024,
    "chunk_strategy": "mechanical"
  }
}
```

This creates the `embed_spaces` registry entry and the backing `chunks_vec_bge_m3_1024` virtual table. The space starts in `populating` status. It records the shape given here (model, dim, `matryoshka_base_dim`, and an optional `query_instruction`: omitted, the model's default; `""`, none) and the declared endpoint's provider and served artifact, since that endpoint fills it. A space of another model than the declared one is accepted only when the endpoint serves the model a request names: `ollama`, or `openai` at exactly `https://api.openai.com`, with no served artifact declared. Under `llama.cpp`, `onnx` or `openai_compat`, or any declared served artifact, it is refused with the differing field. Vectors are stored as `float32`.

The `chunk_strategy` parameter controls which chunks get embedded: only chunks of that strategy are selected.

#### 2. Backfill embeddings

```json
{
  "name": "backfill_embed_space_tool",
  "arguments": { "name": "bge_m3_1024", "batch_size": 64 }
}
```

Embeds the space's pending chunks into its vec table: chunks of its strategy with no vector there and no refusal. Resumable -- if interrupted, call again and it picks up where it left off. A chunk the embedder answers with no usable vector (zero norm, whole or after Matryoshka truncation) is logged and recorded as refused for the space (`embed_refusals`): it is never counted and never sent again, until its text changes (a `redact --rewrite`, for example). The result reports `chunks_refused`, and the space's `chunk_count` is the number of vectors it stores. Under a declared `KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS`, a chunk stored longer than the limit (before ingest split long chunks) is cut to fit, and the cut is recorded with its vector (`embed_truncations`). `truncated_chunks` lists every chunk of the space stored cut, a resumed backfill's earlier runs included, with its `chunk_id`, `source_uri`, `tokens_before` and `tokens_after` as the embedder counts them, never its text. Reingest those documents to split their chunks and embed them whole. `knowledge-base-ingest re-embed` prints the same list.

#### 3. Promote to active

```json
{
  "name": "promote_embed_space_tool",
  "arguments": { "name": "bge_m3_1024" }
}
```

Atomically:

- Sets the new space to `active`
- Sets the old active space to `deprecated`
- Syncs the `config` table (`embed_model`, `embed_dim`, `embed_provider`, `chunk_strategy`)
- Invalidates all `similar` relationships (they depend on the embedding space)

After promotion, all new ingestion and search queries use the promoted space's vec table.

A space is promoted only when nothing is left to embed: every chunk of its strategy has a vector or a refusal, and it
holds at least one vector. Otherwise the promotion says how many chunks are backfilled (run the backfill again); for a
deprecated space it names `knowledge-base-ingest re-embed`, since a deprecated space cannot be backfilled.

Only a space of the whole declared identity (every ADR-0015 field the `KNOWLEDGE_BASE_EMBED_*` variables declare) can
be promoted: the declaration is the active identity. Every other call into a space (backfill, search, comparison)
checks that space against its own recorded identity and refuses one the declared endpoint cannot produce. Promotion is
all or nothing: the folder summaries are embedded first, then the status swap, the `config` rows and the folder-summary
rebuild, with its record of each summary cut to fit `KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS`
(`folder_summary_truncations`), commit together, so a failure changes nothing. Under a multi-model endpoint (Ollama), spaces of another model
can still be created, backfilled and compared.

#### 4. (Optional) Clean up deprecated spaces

```json
{
  "name": "cleanup_embed_space_tool",
  "arguments": { "name": "old_model_768" }
}
```

Drops the deprecated space's vec table and removes its registry entry. Only works on spaces in `deprecated` status.

### Listing spaces

```json
{ "name": "list_embed_spaces_tool", "arguments": {} }
```

Returns all spaces with their status, model, dimension, chunk strategy, and backfill progress (`chunk_count`, the vectors stored, against `total_chunks`, the chunks targeted when the space was created).

### Interaction with chunk_strategy (#100)

Each embedding space is scoped to a single strategy (`mechanical` or `semantic`). This means:

- A `mechanical` space only embeds chunks produced by fixed-size or AST-aware chunking.
- A `semantic` space only embeds chunks produced by semantic boundary detection.
- You can have two active-quality spaces (one per strategy) by promoting one and keeping the other in `populating` for comparison, though only one can be `active` at a time.

### Re-embedding the active identity

Changing the active identity is a maintenance step with the server stopped: set the new `KNOWLEDGE_BASE_EMBED_*`
values (including `KNOWLEDGE_BASE_EMBED_MATRYOSHKA_BASE_DIM` for a truncated active space), run
`knowledge-base-ingest re-embed`, then start the server. The store lock enforces the order: `re-embed` (and `rebind`)
run beside a server exits 1 naming it, for example `the store is in use by knowledge-base --http (pid 2, since
2026-11-21T19:00:00Z)`, and a server started during a re-embed exits 78 naming the re-embed. Under the hood it:

1. Creates a new space named `<model>_<dim>` with the whole declared identity
2. Backfills it
3. Promotes it
4. Re-embeds folder summaries

The old space is left as `deprecated` (not auto-cleaned). Use `cleanup_embed_space_tool` to reclaim disk space. A run
that fails removes the space it created and can be rerun; a killed run leaves a `populating` space to remove with
`deprecate_embed_space_tool` then `cleanup_embed_space_tool`.

### Deprecating without cleanup

```json
{
  "name": "deprecate_embed_space_tool",
  "arguments": { "name": "experimental_model" }
}
```

Marks a `populating` space as `deprecated` without dropping its table. Useful for abandoned experiments that you might want to inspect later.

### Rollback

Rolling back to a previous model is a re-embed: declare the previous identity and run `knowledge-base-ingest re-embed`
with the server stopped. Promotion works on both `populating` and `deprecated` spaces, but a deprecated space of
another identity cannot be promoted in a running server, whose declaration names the current one.

### Bootstrap behavior

On first schema initialization (or upgrade from a pre-#99 database), the system automatically registers the existing `chunks_vec` table as the `default` space in `active` status. This ensures backward compatibility -- existing databases continue to work without manual space creation.

### Matryoshka truncation

Matryoshka Representation Learning (MRL) models produce embeddings where prefix subsets retain semantic meaning. This lets you embed at the model's native dimension and store only the first N components, trading a small amount of retrieval quality for significant storage savings.

**Why pre-truncate?** sqlite-vec stores fixed-dimension vectors and has no query-time truncation. To use a smaller dimension, vectors must be truncated before insertion. For MRL-capable models, this is nearly free: the [#99 research table](https://github.com/dutiona/knowledge-base/issues/99) shows ~95% retrieval quality retained at 50% dimension reduction (e.g., 1024 -> 512 for Qwen3-Embedding).

**How it works:**

1. The provider embeds text at `matryoshka_base_dim` (the model's native dimension).
2. The system slices the first `dim` components from the full vector.
3. The truncated vector is L2 re-normalized (the prefix is not unit-length).
4. The normalized vector is stored in the space's vec table.

**Example workflow:**

```json
{
  "name": "create_embed_space_tool",
  "arguments": {
    "name": "qwen3_512",
    "model": "qwen3-embedding",
    "dim": 512,
    "matryoshka_base_dim": 1024
  }
}
```

This creates a space that embeds at 1024 dimensions (Qwen3-Embedding's native size) but stores 512-dimensional vectors -- halving storage and speeding up cosine similarity searches with minimal quality loss.

Compatible models: Qwen3-Embedding (32--1024), nomic-embed-text-v2-moe (256--768). BGE-M3 does **not** support Matryoshka truncation.

### Comparing spaces

Before promoting a new embedding space, you can quantitatively compare its search behavior against the current active space (or any other space). The comparison tools compute three metrics:

- **Overlap@K** -- fraction of results in common (relative to `min(K, |results_a|, |results_b|)`). A value of 1.0 means both spaces return the same chunks.
- **Jaccard index** -- `|intersection| / |union|` over the two result sets. More conservative than Overlap@K when result set sizes differ.
- **Spearman's rho** -- rank correlation coefficient over the chunks that appear in both result sets (computed only when >= 5 common results). Measures whether the two spaces agree on ranking, not just membership.

**Single-query comparison:**

```json
{
  "name": "compare_spaces_tool",
  "arguments": {
    "query": "attention mechanism in transformers",
    "space_a": "bge_m3_1024",
    "space_b": "qwen3_512",
    "top_k": 10,
    "mode": "vec"
  }
}
```

Returns side-by-side results with per-space scores, overlap metrics, and any warnings.

**Batch comparison (multiple queries):**

```json
{
  "name": "batch_compare_spaces_tool",
  "arguments": {
    "space_a": "bge_m3_1024",
    "space_b": "qwen3_512",
    "queries": [
      "attention mechanism in transformers",
      "contrastive learning loss functions",
      "graph neural network message passing"
    ]
  }
}
```

Returns aggregated statistics (mean, std, min, max) for overlap, Jaccard, and rank correlation across all queries. This gives a more robust picture than any single query.

**Searching a specific space:**

You can also pass `space_name` to `search_index` to query a non-active space directly:

```json
{
  "name": "search_index",
  "arguments": {
    "query": "attention mechanism",
    "space_name": "qwen3_512"
  }
}
```

**Cross-strategy warnings:** When comparing spaces with different `chunk_strategy` values (e.g., `mechanical` vs `semantic`), the comparison tools emit a warning. In this case, low overlap is expected -- the spaces index different chunks entirely -- so the metrics measure corpus overlap rather than embedding quality. Apples-to-apples model comparison requires spaces with the same chunking strategy.
