# Auto-Relationship Discovery

Automatic discovery of `"similar"` relationships between papers via chunk embedding similarity.

## How It Works

For a given paper, the system:

1. Fetches all chunk embeddings for that paper
2. Fetches chunk embeddings for every other paper in the corpus
3. Computes pairwise cosine similarities between all chunk pairs
4. Takes the **top-3 highest similarities** and averages them (top-k average)
5. If the average exceeds the **propose threshold** (default 0.82), creates a `"similar"` relationship with the score as confidence

The top-3 average is robust against single-chunk outliers (boilerplate, bibliography) while still capturing strong local similarity signals.

## Automatic Trigger

When you register a paper with `register_paper_tool` and provide a `source_uri`, an `auto_relate` background job is automatically scheduled. This compares the new paper against all existing papers.

```json
{
  "name": "register_paper_tool",
  "arguments": {
    "title": "Attention Is All You Need",
    "source_uri": "/path/to/attention.pdf"
  }
}
```

To skip the automatic scan (useful for bulk imports), set `skip_auto_relate: true`:

```json
{
  "name": "register_paper_tool",
  "arguments": {
    "title": "Paper Title",
    "source_uri": "/path/to/paper.pdf",
    "skip_auto_relate": true
  }
}
```

## Manual Scanning

### Single paper (1×M)

```json
{
  "name": "scan_relationships",
  "arguments": { "paper_id": 42 }
}
```

### Full corpus (N×M)

```json
{
  "name": "scan_relationships",
  "arguments": {}
}
```

Full scan submits one background job per paper. Direction normalization (source < target for `"similar"`) prevents duplicate edges. Job deduplication prevents re-queuing papers that already have a pending scan.

## Configuration

Thresholds are stored in the `config` table and tuned for the default `bge-m3` embedding model:

| Key                             | Default | Description                                                      |
| ------------------------------- | ------- | ---------------------------------------------------------------- |
| `auto_relate_propose_threshold` | `0.82`  | Minimum top-3 average cosine similarity to create a relationship |
| `auto_relate_accept_threshold`  | `0.95`  | Reserved for future `status` column (proposed vs auto-accepted)  |

Different embedding models have different cosine similarity distributions. If you switch models via `re_embed_tool`, adjust these thresholds accordingly.

## Stale Relationship Cleanup

- **On reingest:** All `"similar"` relationships for papers linked to the reingested file are deleted, and new `auto_relate` jobs are scheduled automatically.
- **On re-embed:** All `"similar"` relationships corpus-wide are deleted (the embedding space changed). Run `scan_relationships()` to recompute.
- Other relationship types (`cites`, `extends`, etc.) are never affected.

## Relationship to `suggest_relationships`

These are complementary tools:

| Tool                                | Signal                                            | Strength                 |
| ----------------------------------- | ------------------------------------------------- | ------------------------ |
| `suggest_relationships`             | DOI matching, title words, author+year heuristics | Precise for citations    |
| `scan_relationships` / auto-trigger | Chunk embedding cosine similarity                 | Broad content similarity |

A paper can have both a `"cites"` relationship (from `suggest_relationships`) and a `"similar"` relationship (from auto-discovery) with the same target.

## Direction Semantics

`"similar"` relationships are stored with the lower paper ID as source. Since similarity is symmetric, always query with `direction="both"` (the default) to see all similar edges for a paper.
