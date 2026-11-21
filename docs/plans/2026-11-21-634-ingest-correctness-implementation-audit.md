# Implementation audit (pre-review): plan #634, ingest correctness

- Scope: pre-review.
- Approved plan: `docs/plans/2026-11-21-634-ingest-correctness.md`, sha256
  `b39c9ff5cfed28699e2d1f18d41091f1238e568b7bc092bbd8e8b837f2eccaac` (approved on #637).
- Implementation head audited: `abef910547f318fff96b90c2c35f327be38d4862` (rebased on origin/master `000348c`, #641);
  memory-engine half at `c74856b` on its branch `test/kb626-json-key-hex-fixture`.

## Items

| Item | Status | Commit | Notes |
| --- | --- | --- | --- |
| 0. The plan on the branch | Implemented | `026482e` | |
| 1. Docstrings promising an error dict | Implemented | `a65f8ee` | |
| 2. Stale references | Implemented | `529dd27` | |
| 3. #633 schema read first | Implemented | `81f1070` | Green-first as O5 states: the 5 tests passed before the change. The comment cites "SQLite < 3.54.1": the orchestrator measured the fix upstream in 3.54.1 (the KB image links 3.46.1). |
| 4. #597 one storage owner, rollbacks | Implemented | `4331bbb` | Variations below. |
| 5. Call boundary (O2) | Implemented | `bb42a28` | Published schemas byte-identical before and after (both hubs). |
| 6. #631 gone-file removal | Implemented | `eea2f55` | |
| 7. #598 tool errors | Implemented | `8a935e3` | Variations below. |
| 8. memory-engine fixture PR | Implemented | ME `c74856b` | Nine cases, the in-crate test, the pin, one doc sentence; ME's full Verify block passed at `c74856b`. |
| 9. #626 JSON-key rule | Implemented | `80a7770` | Fixture bytes `cmp`-equal to ME's, sha256 `0c142e4c…f4db`. |
| Added: schema 4 in the config table | Added | `abef910` | #641 bumped the schema to 4 while item 2 corrected the row to 3. |

Counts: Implemented 10, Modified 0, Dropped 0, Incomplete 0, Added 1. Divergence (Modified + Dropped + Added) / 10 =
**10%**, under the 30% boundary.

## Intent-preserving variations

- Item 4: the four web figure writers route their write phase through one helper, `web._replace_figures`, instead of
  four copies of the rule; `INSERT_SITES` therefore names `web._replace_figures` once.
  `test_ingest_url_keeps_its_figures_when_their_write_fails` lives in `tests/test_redact_paths.py`, beside the drivers
  it shares, not in `tests/test_ingest.py`.
- Item 5: the "returns with a transaction open" test patches `get_job_status_tool`'s library call.
- Item 7: the three HTTP tools' own `except KnowledgeBaseError: raise ToolError(str(e))` blocks go, since the boundary
  now does exactly that; their httpx and `IntegrityError` mappings are unchanged. One test was added for
  `relocate_paper_tool`'s hash mismatch, which now names both hashes in its text. The 40 route tests were rewritten to
  assert the tool error and its text.
- Verification: #641 extended AGENTS.md's Verify list (the Sphinx build with `-W -E`, and the image step when
  `pyproject.toml` or `uv.lock` change, which this branch does). The final gate runs that list.
