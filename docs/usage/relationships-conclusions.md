# Relationships and Conclusions

## Relationship Types

Eight typed edges connect papers:

| Type          | Meaning                                             |
| ------------- | --------------------------------------------------- |
| `extends`     | Builds upon the target paper's work                 |
| `contradicts` | Presents conflicting findings                       |
| `replicates`  | Reproduces or validates results                     |
| `cites`       | References the target paper                         |
| `compares`    | Evaluates against the target paper's approach       |
| `applies`     | Uses the target paper's method in a new domain      |
| `implements`  | Provides an implementation of the target paper      |
| `similar`     | Content similarity via embeddings (auto-discovered) |

The first seven types are manually assigned. `similar` is auto-discovered via chunk embedding cosine similarity -- see [Auto-Relationship Discovery](auto-relationships.md) for details.

## Adding Relationships

```json
{
  "name": "add_relationship_tool",
  "arguments": {
    "source_paper_id": 1,
    "target_paper_id": 2,
    "relation_type": "extends",
    "confidence": 0.9,
    "evidence_chunk_id": 42
  }
}
```

Relationships upsert on `(source_paper_id, target_paper_id, relation_type)` -- adding the same relationship again updates the confidence and evidence_chunk_id rather than creating a duplicate.

`confidence` must be between 0.0 and 1.0.

## Querying Relationships

```json
{
  "name": "get_relationships_tool",
  "arguments": {
    "paper_id": 1,
    "direction": "outgoing"
  }
}
```

Three direction modes:

- **`outgoing`** -- Relationships where this paper is the source
- **`incoming`** -- Relationships where this paper is the target
- **`both`** (default) -- Both directions

Filter by type:

```json
{
  "name": "get_relationships_tool",
  "arguments": {
    "paper_id": 1,
    "relation_type": "cites",
    "direction": "outgoing"
  }
}
```

Results include both paper titles and, when an evidence_chunk_id is set, the evidence chunk's content.

## Auto-Suggestion

The `suggest_relationships_tool` analyzes a paper's text to suggest citation relationships with other registered papers:

```json
{
  "name": "suggest_relationships_tool",
  "arguments": { "paper_id": 1 }
}
```

Three matching strategies (applied in order):

1. **DOI matching** (confidence 0.9) -- Extracts DOIs from the paper text and matches against registered papers' DOIs. Highest precision.
2. **Title word-ratio** (confidence 0.3-0.6) -- Compares other papers' title words (excluding stopwords and short words) against the paper text. Requires >= 60% match ratio and at least 2 meaningful words.
3. **Author+year heuristic** (confidence 0.4) -- Extracts parenthetical and narrative citations (e.g., "(Vaswani et al., 2017)", "Vaswani (2017)") and matches surname+year against registered papers' author lists.

The response includes:

- `suggestions` -- Candidate relationships with target paper, confidence, and match method
- `unmatched` -- DOIs found in text that don't match any registered paper (useful for identifying papers to register)

Existing `cites` relationships are excluded from suggestions.

## Recording Conclusions

Conclusions are evidence-chained claims linked to source chunks:

```json
{
  "name": "record_conclusion_tool",
  "arguments": {
    "claim": "ViT outperforms ResNet on ImageNet when pretrained on JFT-300M",
    "confidence": 0.85,
    "source_chunk_ids": [12, 45, 67],
    "session_context": "Comparing Table 2 results across papers"
  }
}
```

All referenced chunk IDs are validated before insertion -- missing IDs return an error.

## Supersession

When a conclusion is revised, supersede it rather than deleting:

```json
{
  "name": "supersede_conclusion_tool",
  "arguments": {
    "old_conclusion_id": 5,
    "new_claim": "ViT outperforms ResNet on ImageNet with sufficient pretraining data (>100M images)",
    "confidence": 0.92,
    "source_chunk_ids": [12, 45, 67, 89]
  }
}
```

This atomically creates a new conclusion and marks the old one's `superseded_by` field. A conclusion that is already superseded cannot be superseded again -- you must supersede the latest in the chain.

## Evidence Chains

Follow the full supersession chain for any conclusion (oldest to newest):

```json
{
  "name": "get_conclusion_chain_tool",
  "arguments": { "conclusion_id": 5 }
}
```

The chain traversal works from any conclusion in the chain -- it walks backward to find the root, then forward to the latest version. Each entry includes the claim, confidence, evidence chunk IDs, session context, and timestamp.

## Filtering Conclusions

```json
{
  "name": "get_conclusions_tool",
  "arguments": {
    "keyword": "transformer",
    "min_confidence": 0.7,
    "include_superseded": false
  }
}
```

- **`keyword`** -- Substring match on the claim text
- **`min_confidence`** -- Minimum confidence threshold (default 0.0)
- **`include_superseded`** -- Whether to include conclusions that have been superseded (default false)

Results are ordered by creation date (newest first) and include resolved source chunk content.
