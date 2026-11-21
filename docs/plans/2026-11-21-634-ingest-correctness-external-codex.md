Reviewer: Codex gpt-6.1-terra (medium) via codex-headless-round.sh, thread resumed over 4 rounds

~~~~
Michaël, **LGTM** for the wording-only delta. The altitude report’s digest matches the revised plan.

Altitude report SHA-256: 99e415c886a77ec151a892e828c0e6a22276ea0b1d8f1c9e9bc4cd2f1f304b33

REVIEW COMPLETE
~~~~

## Resolution

Round 1 (plan `6cabafc7…b883`, altitude `e11c82c4…d183`):
- [HIGH] #597 convergence criterion met only in intent: O1 now states it amends the criterion's letter (a file reaches
  `ingest_text`'s own `_text_chunks` and `_store_document`) and that approving O1 is that amendment; the literal
  alternative stays; item 4's guard covers a markdown and a non-Python code file. Revised in round 2.
- [HIGH] #633 closed despite "a failing test first": O5 now states it amends that criterion, approval being the
  amendment; declined, `Refs #633`. Item 3's re-embed assertion checks the space `re_embed` returns. Revised in round 2.
- [HIGH] the walk could pass vacuously and bypassed the client: item 7's walk runs through `fastmcp.Client(server.mcp)`,
  plus per-branch tests for all 32 sites and a stdio/network error-text test. Revised in round 2 (and round 3, below).
- [HIGH] hand-out rollback leaves the lock until the thread's next call: replaced by item 5, a call boundary
  (`routes.tool(mcp)`) on the tool's own thread that rolls back on raise and on a return with a transaction open,
  tested by an immediate write from another connection with `busy_timeout = 0`. Revised in round 2.
- [MEDIUM] figure atomicity overstated: Outcome, docs and Accepted now say per writer. Revised in round 2.
- [MEDIUM] red command missed `tests/test_redact_paths.py`: added; each test names its file; red failures are checked
  one by one. Revised in round 2.
- [MEDIUM] O4's relocation rationale: O4, item 6's help text and docs separate `relocate_paper_tool` (a registered
  paper's moved file) from `remove`, and state what relocation can still do after a remove. Revised in round 2.
- [MEDIUM] O7's format cannot share walk/text disagreements: O7 states the limit; item 9 adds KB-local parity tests
  (memory-engine keeps its own). The `walk` discriminator stays the named alternative. Revised in round 2.
- [LOW] `details` removal broader than stated: item 7 names its one producer, one reader, the one client-visible loss
  and the test constructors to change. Revised in round 2.

Round 2 (plan `d8b12410…a91c`, altitude `465955a2…a85d`):
- [HIGH] the `_get_conn` body-ran recorder misfires on `ingest_text` (and `sync_bibtex_tool`, per agy): removed; the
  walk asserts its schema-built arguments validate against each tool's `inputSchema` (`jsonschema`, declared in the dev
  group) before each call. Revised in round 3.
- [HIGH] O5 declined would still close #634: O5 and the PR step now say `Refs #633` and `Refs #634`, the PR closing
  four of five members. Revised in round 3.

Round 3 (plan `03ba2ea3…2c46`, altitude `22f6bd5f…d61f`): LGTM. O1 and O5 are the two acceptance amendments Michaël
approves or declines with the plan. No degradation: Codex ran on `gpt-6.1-terra` at medium on every round, per the
2026-11-21 review-models ruling.

Round 4 (plan `b39c9ff5…caac`, altitude `99e415c8…4b33`): LGTM on the wording-only delta (item 0 names the plan
by its place: the plan-issue archiver refuses a local source reference). This round's review is the one above.
