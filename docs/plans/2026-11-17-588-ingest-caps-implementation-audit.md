# Implementation audit (pre-review): 2026-11-17-588-ingest-caps

- Approved plan: `docs/plans/2026-11-17-588-ingest-caps.md`, sha256
  `a404bd10683837ab045c0198184ec0aae20073185dc31657fcd5fcdbb7dbf205` (marker on #601, comment 6951466594).
- Audited head: `b2746f8` (scope: pre-review). The plan bytes are unchanged.

| # | Item | Status | Commit | Note |
|---|---|---|---|---|
| 1 | The fetch limits in `settings.py` | Implemented | `bddad51` | The suite's autouse environment guard also unsets the three variables, so a developer's shell cannot change a test's limits |
| 2 | The deadline in the floored transport | Implemented | `1cfa0bd` | Adds a test that a nested `egress_deadline` restores the outer one (D2's stated rule) |
| 3 | `ingest_url` fetches its page through `_fetch` | Implemented | `dd9d544` | The per-hop deadline check of D3 step 2 is the transport's (item 2), which runs before every hop; `_fetch` does not repeat it. The test pages stream their bodies, since httpx decodes a bytes body when a response is built |
| 4 | Inline images through `_fetch` | Implemented | `1b3fbf8` | |
| 5 | Documentation | Implemented | `b2746f8` | Also `docs/usage/figure-extraction.md`, whose "capped at 10 MB" line was the same stale cap |

Counts: Implemented 5, Modified 0, Dropped 0, Incomplete 0, Added 0. Divergence: 0% (threshold 30%). The notes are
intent-preserving variations; no approved intent changed, so no successor plan.

Verification as planned: the AGENTS.md gate at `b2746f8` (all green, quoted in the PR), and the after-measurement
through `_fetch` (the 1 GB file refused at the cap in 0.91 s with a 160 MiB process peak; the pages ingest).
