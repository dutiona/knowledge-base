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
    "provider": "ollama",
    "chunk_strategy": "mechanical"
  }
}
```

This creates the `embed_spaces` registry entry and the backing `chunks_vec_bge_m3_1024` virtual table. The space starts in `populating` status.

The `chunk_strategy` parameter controls which chunks get embedded. When the `chunk_strategy` column exists on the `chunks` table (from #100), only chunks matching the specified strategy are selected. Otherwise all chunks are embedded.

#### 2. Backfill embeddings

```json
{
  "name": "backfill_embed_space_tool",
  "arguments": { "name": "bge_m3_1024", "batch_size": 64 }
}
```

Embeds all matching chunks into the space's vec table. Resumable -- if interrupted, call again and it picks up where it left off (ID-cursor based, skips already-embedded chunk IDs).

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

Returns all spaces with their status, model, dimension, chunk strategy, and backfill progress (`chunk_count` / `total_chunks`).

### Interaction with chunk_strategy (#100)

When `chunks.chunk_strategy` exists, each embedding space is scoped to a single strategy (`mechanical` or `semantic`). This means:

- A `mechanical` space only embeds chunks produced by fixed-size or AST-aware chunking.
- A `semantic` space only embeds chunks produced by semantic boundary detection.
- You can have two active-quality spaces (one per strategy) by promoting one and keeping the other in `populating` for comparison, though only one can be `active` at a time.

If the `chunk_strategy` column does not exist (pre-#100 databases), all chunks are embedded regardless of the strategy parameter.

### Interaction with re_embed_tool

The existing `re_embed_tool` is a convenience wrapper. Under the hood it:

1. Creates a new space named `<model>_<dim>`
2. Backfills it
3. Promotes it
4. Re-embeds folder summaries

The old space is left as `deprecated` (not auto-cleaned). Use `cleanup_embed_space_tool` to reclaim disk space.

### Deprecating without cleanup

```json
{
  "name": "deprecate_embed_space_tool",
  "arguments": { "name": "experimental_model" }
}
```

Marks a `populating` space as `deprecated` without dropping its table. Useful for abandoned experiments that you might want to inspect later.

### Rollback

To roll back to a previous model:

```json
{
  "name": "promote_embed_space_tool",
  "arguments": { "name": "old_model_768" }
}
```

Promotion works on both `populating` and `deprecated` spaces. The current active space becomes deprecated, and the old space becomes active again.

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
    "provider": "ollama",
    "matryoshka_base_dim": 1024
  }
}
```

This creates a space that embeds at 1024 dimensions (Qwen3-Embedding's native size) but stores 512-dimensional vectors -- halving storage and speeding up cosine similarity searches with minimal quality loss.

Compatible models: Qwen3-Embedding (32--1024), nomic-embed-text-v2-moe (256--768). BGE-M3 does **not** support Matryoshka truncation.
