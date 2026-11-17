# Implementation audit (pre-review): 2026-11-17-kb-543-lean-surface

- Approved plan: `docs/plans/2026-11-17-kb-543-lean-surface.md`, sha256
  `720899d4e3e03eca36d5883675a7c4d9690247ffa33a9d632af3acc475e17da3` (marker on #594, comment 6948914409).
- Audited head: `b32cc16` (scope: pre-review). The plan bytes are unchanged.

| # | Item | Status | Commit | Note |
|---|---|---|---|---|
| 1 | Reference documents every stdio tool | Implemented | `acd21ba` | |
| 2 | `ingest.ingest_text`, URL text through it | Implemented | `e5f2398` | |
| 3 | Repeated chunks stored once, failed write rolled back | Implemented | `9499fa6` | |
| 4 | Network-facing docstrings made true | Implemented | `07dd423` | |
| 5 | Tool errors that never name the embedder (`search_index`, `ingest_url`) | Implemented | `0ecec8c` | |
| 6 | `ingest_text` tool | Implemented | `78f8c3e` | Refusal text per the arbitrated Codex r5 LOW: "no control, format or surrogate characters and no line or paragraph separators" |
| 7 | Serve only search and ingest over HTTP | Implemented | `9ae5038` | The gate-less test runs both hubs with `ingest_text`, which both serve, instead of `register_paper_tool` |
| 8 | Refuse `--http` with vision or browser on; no HTTP tool deletes | Implemented | `fdafd28` | The snapshot leaves out every virtual table and its shadow tables (FTS5 and sqlite-vec rewrite them in place) |
| 9 | 4 MiB request-body cap | Implemented | `c4d33fe` | |
| 10 | Docs: both surfaces, counts | Implemented | `b32cc16` | Also `docs/getting-started/installation.md`. The README's `ingest_text` sample shows the JSON-RPC body and points to the existing stdin-header check rather than repeating the token-file command |

Counts: Implemented 10, Modified 0, Dropped 0, Incomplete 0, Added 0. Divergence: 0% (threshold 30%). The notes are
intent-preserving variations; no approved intent changed, so no successor plan.
