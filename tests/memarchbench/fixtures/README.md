# MemArch-Bench fixtures

Golden-set fixtures live in-repo here (#360 open question 1: resolved — no
submodule). The golden set itself is `golden_queries.jsonl`, curated under
#250 (owner judges; format locked in the #250 comment of 2026-07-06).

One JSON object per line:

```json
{
  "schema": 1,
  "id": "q0001",
  "query": "how does expression templates eliminate temporaries",
  "source": "query_log",
  "query_log_id": 42,
  "type": "single_hop",
  "judgments": [
    {
      "doc": "bibliography/veldhuizen.1995.expression.pdf",
      "quote": "verbatim span from the answering chunk",
      "content_hash": "sha256-of-the-chunk-at-curation-time",
      "relevance": 2
    }
  ],
  "answer_note": "optional free text",
  "curated_at": "2026-07-06"
}
```

- `source`: `query_log` (real demand, #533) or `seeded` (thesis questions).
- `type`: `single_hop` | `multi_hop` | `comparison` | `temporal`.
- `relevance`: graded — 2 fully answers, 1 partial/supporting, 0 judged
  irrelevant (kept for nDCG and hard-negative tracking).
- `quote` is the durable anchor (survives re-ingestion/re-chunking);
  `content_hash` is the fast path. Never store raw chunk ids — they are
  DB-instance-specific.
- `missed` judgments from #533 map to a query with an empty `judgments`
  array (hard case: it still counts against Coverage@k).
