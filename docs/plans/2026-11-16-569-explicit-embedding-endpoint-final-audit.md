# Final audit (post-review): #569 explicit embedding endpoint

- Scope: post-review
- Approved plan: `docs/plans/2026-11-16-569-explicit-embedding-endpoint.md`, SHA-256
  `12997b016ebded1a3eac4956fb6028af1a0687494543097f776c1a5db7c14528` (unchanged since approval)
- Post-review head audited: `9a4e673e4bd499459674ee02f0e7bda8af5da3c2`
- Pre-review audit: `docs/plans/2026-11-16-569-explicit-embedding-endpoint-implementation-audit.md` (head `1cd9eaf`)

`/super-review` (gate Full; advisor unavailable, so Codex headless + agy + one Claude subagent; three rounds; all LGTM at
`9a4e673`) produced these changes, each inside an existing item and preserving its intent:

| Item | Review-fix commit(s) | Variation from the approved text |
| --- | --- | --- |
| 7 / D3 | `6e0f2f9`, `a4369ea`, `9a4e673` | `declared_provider_config` takes the URL and key source from the running process's own declaration instead of the bound `config` rows (D3 said the rows), so a `rebind`/`re-embed` run while a server is up cannot redirect that server. Stricter than planned; same intent |
| 14 / D3 | `f1d2020` | `promote_space` re-reads the target under the write lock and refuses if it changed or vanished while its summaries embedded |
| 4 / D1 | `14192cf` | A refused `KNOWLEDGE_BASE_EMBED_URL` names only its scheme, never the raw value |
| 12 / D6 | `4a4be65` | `extract_structure_tool` refuses before any work while the LLM is off (queues nothing); `configure_vision_tool` returns its refusal as an error object |
| 6, 15 (tests) | `32825e9` | Direct test of the plain-bind refusal of a drifted config row; floor guards match the private-address reason |
| 17, 18 (docs) | `58fd82f`, `3ee76e8` | `embeddings.py` docstrings; README back-out of a failed re-embed; release checklist carries `ONNX_EMBED_MODEL_PATH` |

Adjudicated, not changed (owner decisions approved with the plan on #577): the redirect-hop and `ollama` LLM floor gaps
(owner call (d), folded into #572), and the derived key row and refuse-then-`rebind` endpoint move (D2, owner call (b)).

Counts: Implemented 19, In progress 1 (item 19, operational: `/finish-pr`), Modified 0, Dropped 0, Incomplete 0, Added 0.
Divergence (Modified + Dropped + Added) / 20 = 0%. No successor plan.
