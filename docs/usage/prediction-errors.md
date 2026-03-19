# Prediction Errors

## What

Prediction errors are automatically logged when `search_index` returns results that fall below a confidence threshold, or returns no results at all. They surface retrieval failures as actionable maintenance signals: **what questions is the agent asking that the knowledge base can't answer well?**

Two error types are tracked:

| Error type       | Trigger                                                                     |
| ---------------- | --------------------------------------------------------------------------- |
| `low_confidence` | Top result's RRF score is below the configured threshold (hybrid mode only) |
| `no_results`     | Query returned zero candidates (any mode)                                   |

Detection is non-blocking -- it never slows down or disrupts the search response.

## Why

**Research rationale:** This feature is inspired by prediction-error signaling in memory systems (see NeuroStack analysis, 2026-03-14). When a retrieval system returns a poor match, it indicates a gap between what the user expects to find and what the index contains. Rather than silently returning weak results, logging these failures turns them into a gap analysis tool -- they tell you what to ingest next.

**RRF score interpretation:** Reciprocal Rank Fusion scores are rank-based, not confidence-based. With k=60:

- A result appearing at rank 1 in **both** FTS and vec legs scores `2 / (60 + 1) = 0.0328`
- A result appearing at rank 1 in **one** leg only scores `1 / (60 + 1) = 0.0164`

The default threshold (`0.025`) catches queries where the best result appeared in only one retrieval leg -- it matched on keywords OR semantics, but not both. This is a weak retrieval signal.

Low-confidence detection only applies to **hybrid mode** searches. FTS-only and vec-only searches inherently produce single-leg scores, so they would trigger false positives.

## Using Prediction Errors

### Reviewing Errors

List unresolved prediction errors to identify knowledge base gaps:

```json
{
  "name": "list_prediction_errors_tool",
  "arguments": {}
}
```

Filter by time range (ISO 8601 timestamps are normalized automatically):

```json
{
  "name": "list_prediction_errors_tool",
  "arguments": { "since": "2026-03-01T00:00:00Z" }
}
```

Include resolved errors:

```json
{
  "name": "list_prediction_errors_tool",
  "arguments": { "unresolved_only": false }
}
```

### Resolving Errors

After ingesting content that fills a gap, mark the error as resolved:

```json
{
  "name": "resolve_prediction_error_tool",
  "arguments": { "error_id": 42 }
}
```

Returns `{"error": "prediction error 42 not found"}` if the ID does not exist.

### Monitoring

The `status` tool includes the count of unresolved prediction errors:

```json
{
  "prediction_errors": 3
}
```

A rising count indicates the knowledge base is falling behind on coverage for topics being queried.

## Configuration

The threshold is stored in the `config` table and can be adjusted:

| Config key                   | Default | Description                               |
| ---------------------------- | ------- | ----------------------------------------- |
| `prediction_error_threshold` | `0.025` | RRF score below which a result is flagged |

Lower values reduce sensitivity (fewer false positives, may miss weak results). Higher values increase sensitivity (catches more marginal results, but may flag acceptable matches).

## Rate Limiting

To avoid flooding the table from repeated queries, at most **one error per (query, error_type, source_type_filter) per hour** is logged. Queries are normalized (lowercase, stripped whitespace) before hashing for consistent deduplication.

The dedup key includes `source_type_filter` so that the same query failing with different filters produces distinct error records -- these represent different failure surfaces.

## Deferred Features

- **`scope_miss` error type** (#145): Detecting when a filtered search returns empty but an unfiltered search would have results. Deferred because it requires a second search call.
- **Workspace-scoped filtering** (#146): Adding a workspace dimension to prediction errors for multi-workspace deployments. Deferred pending workspace feature.
