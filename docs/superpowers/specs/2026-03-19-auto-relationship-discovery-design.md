# Auto-Relationship Discovery via Embedding Similarity

**Issue:** [#105](https://github.com/dutiona/knowledge-base/issues/105)
**Date:** 2026-03-19
**Status:** Approved

## Problem

`suggest_relationships` requires explicit user invocation and uses text heuristics
(DOI matching, title words, author+year). Paper connections that the user never
thinks to ask about remain hidden. Embedding similarity — cheap, exhaustive, and
already available from ingestion — is not used at all.

## Solution Overview

A background job (`auto_relate`) that computes pairwise embedding similarity
between papers' chunks. When similarity exceeds a configurable threshold,
a `"similar"` relationship is created automatically.

## Design Decisions

### 1. Chunk-to-Paper Similarity Aggregation: Top-k Average (k=3)

**Context:** Relationships are paper-to-paper, but embeddings are per-chunk.

**Decision:** Average the top-3 highest cosine similarities across all chunk pairs
between two papers.

**Why:**
- Robust against single-chunk outliers (unlike max-pool)
- Captures strong local similarity signals without drowning in noise
- Simple to implement and reason about
- The best-matching chunk pair provides `evidence_chunk_id`
- k=3: selective enough to avoid flukes, large enough for robustness

**Rejected alternatives:**
- Max-pool: fragile — one boilerplate chunk (bibliography, headers) dominates
- Weighted mean of all pairs: O(n×m) per pair, more complex, marginal benefit

### 2. Relationship Type: `"similar"`

**Context:** Existing types (`extends`, `contradicts`, `cites`, etc.) carry semantic
meaning that cosine similarity cannot infer.

**Decision:** Add `"similar"` to `RELATIONSHIP_TYPES`.

**Why:**
- Cosine similarity tells us content is related, not *how*
- Keeps existing types meaningful and trustworthy
- Users can reclassify via `add_relationship`
- LLM-based reclassification can be added as a follow-up

**Rejected alternative:**
- LLM-inferred type per candidate: adds cost/latency, defeats the "cheap &
  exhaustive" goal

### 3. Computation Trigger: Eager + Manual Scan + Opt-Out

**Context:** O(n²) pairwise comparison is expensive. Need control over when it runs.

**Decision:**
- **Eager (default):** After each `ingest_file`, auto-schedule an `auto_relate`
  job comparing the new paper against all existing papers
- **Manual full scan:** `scan_relationships()` — N×M paper comparison for backfill
  or re-threshold tuning
- **Manual single-paper scan:** `scan_relationships(paper_id=X)` — 1×M comparison
- **Opt-out:** `ingest_file(..., skip_auto_relate=True)` — skip post-ingest job,
  useful for bulk imports

**Why:**
- Eager handles the common case cheaply and automatically
- Manual tools give users full control over expensive operations
- Opt-out respects power users doing bulk imports
- No periodic scheduling complexity

**Rejected alternatives:**
- Periodic full scan: unnecessary scheduling overhead, user should decide when
- Lazy-only: delays relationship discovery, loses the "automatic" benefit

### 4. Threshold Storage: Co-Located with Embedding Config

**Context:** Thresholds are properties of the embedding space. Future work will
support multiple embeddings coexisting side-by-side.

**Decision:** Store in the `config` table alongside embedding model settings:
- `auto_relate_propose_threshold` (default `0.82`)
- `auto_relate_accept_threshold` (default `0.95`)

**Why:**
- Thresholds depend on the embedding model — 0.82 for bge-m3 may be wrong for
  another model
- When multiple embeddings coexist, each needs its own thresholds
- Config table already stores embedding settings — natural home
- Adjustable without code changes

## Schema Changes

### `RELATIONSHIP_TYPES` (db.py)

Add `"similar"` to the existing tuple:

```python
RELATIONSHIP_TYPES = (
    "extends",
    "contradicts",
    "replicates",
    "cites",
    "compares",
    "applies",
    "implements",
    "similar",      # new: embedding-similarity-based discovery
)
```

### `config` table — new keys

| Key | Default | Description |
|-----|---------|-------------|
| `auto_relate_propose_threshold` | `0.82` | Minimum top-3 avg cosine similarity to create a proposed relationship |
| `auto_relate_accept_threshold` | `0.95` | Minimum top-3 avg cosine similarity to auto-accept |

No new tables. Candidates go into the existing `relationships` table with
`relation_type='similar'`.

## New Job Type: `auto_relate`

Added to `jobs.py` dispatch. Two modes via `params`:

| Param | Effect |
|-------|--------|
| `{"paper_id": N}` | Compare paper N vs all other papers |
| `{"full_scan": true}` | Compare all paper pairs (backfill) |

### Algorithm (per paper pair)

1. Fetch chunk embeddings for both papers from `chunks_vec` (joined via
   `chunks.source_uri` → `papers.source_path`)
2. Compute pairwise cosine similarities (brute-force on small per-pair sets)
3. Take top-3 similarities, average them → paper-pair score
4. If score >= `accept_threshold`: insert with `confidence=score`
5. Else if score >= `propose_threshold`: insert with `confidence=score`
6. Skip if a relationship already exists between this pair (any type)
7. Store best-matching chunk ID as `evidence_chunk_id`

### Deduplication

- UNIQUE constraint on `(source_paper_id, target_paper_id, relation_type)`
  prevents duplicate `"similar"` edges
- Job skips pairs that already have *any* relationship type to avoid proposing
  similarity for papers already linked as `"cites"`, `"extends"`, etc.

## Post-Ingest Hook

In `ingest_file()` or the server-level tool wrapper, after successful ingestion:

```
if paper_id is not None and not skip_auto_relate:
    submit_job(paper_id, 'auto_relate', {"paper_id": paper_id})
```

This is the first auto-triggered background job in the system.

## New MCP Tools

### `scan_relationships`

Manual trigger for relationship scanning.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `paper_id` | int \| None | None | If set, scan this paper only (1×M). If None, full scan (N×M). |

Submits an `auto_relate` job with the appropriate params. Returns the job ID.

## Performance Considerations

- **Per-paper fetch:** Chunk embeddings fetched per-paper (grouped by paper_id),
  not loaded all at once
- **Early termination:** Skip papers with 0 embedded chunks
- **Progress reporting:** Use existing `on_progress` callback, report per-paper-pair
- **No KNN index:** Brute-force cosine on per-pair chunk sets (small). KNN index
  is for "find nearest across whole corpus" — not what we need here
- **Batching for full scan:** Iterate paper pairs, not all chunks at once

## Scope Boundaries

### Included
- `auto_relate` job type with single-paper and full-scan modes
- `"similar"` relationship type
- Post-ingest auto-trigger with opt-out
- `scan_relationships` MCP tool
- Configurable thresholds in config table

### Excluded (follow-up work)
- LLM-based relationship type inference from `"similar"` edges
- Co-access patterns (#128 session tracking feeds into this)
- Temporal proximity signals
- Transitive inference (A→B, B→C ⇒ A→C)
- `status='proposed'` column on relationships (needs its own schema migration;
  for now, confidence score distinguishes proposed from auto-accepted)
