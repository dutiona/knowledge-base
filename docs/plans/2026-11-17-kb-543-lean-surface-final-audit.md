# Implementation audit (post-review): 2026-11-17-kb-543-lean-surface

- Approved plan: `docs/plans/2026-11-17-kb-543-lean-surface.md`, sha256
  `720899d4e3e03eca36d5883675a7c4d9690247ffa33a9d632af3acc475e17da3`; the plan bytes are unchanged.
- Audited head: `f26efe1` (scope: post-review, after /super-review Full: Codex, agy and a clean-slate Claude lens, 3
  rounds, all LGTM at that head).
- Pre-review audit: `2026-11-17-kb-543-lean-surface-implementation-audit.md` (head `b32cc16`).

Review fixes, each within an existing item:

| Item | Commit | Change |
|---|---|---|
| 8 | `96ee898`, `f26efe1` | The no-delete test now matches the plan's wording: every table outside chunks, chunk_sessions and prediction_errors is unchanged, and only the active space's chunk_count may grow (per space). The pre-review audit had not listed the weaker check |
| 6 | `96ee898` | The embedder-down test also counts the active vector table; test names use escapes for U+202E and U+2028 |
| 5 | `96ee898`, `f26efe1` | `search_index` refuses an unknown `space_name` with its reason (only that lookup's ValueError is mapped) and keeps the fts hint for a non-retryable embedder failure |
| 10 | `96ee898` | The reference states that search results name stdio-ingested files by path; its `rerank` row matches the fallback |
| — | `79d6486` | A typed helper for the embedder-name assertions (basedpyright) |

Counts: Implemented 10, Modified 0, Dropped 0, Incomplete 0, Added 0. Divergence 0% (threshold 30%). No approved
intent changed; no successor plan.
