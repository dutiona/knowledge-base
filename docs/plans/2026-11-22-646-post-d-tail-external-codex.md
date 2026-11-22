Reviewer: Codex gpt-6.1-terra (medium effort) via codex-headless-round.sh, persistent thread, 3 rounds

~~~
Michaël, **LGTM**. The refreshed altitude report’s proceed disposition remains justified, and the wording changes introduce no findings.

Altitude report SHA-256: f8b499704b226433e9a844744a95d9cdf5c3b6d39c156df1b3282b335732acee

REVIEW COMPLETE
~~~

## Resolution

Round 1 (plan SHA-256 00def7fa…, altitude report 45ace0a1…):

- [HIGH] D4 and item 5 narrowed #643's quoted acceptance ("leaves none of them stored") to uncommitted writes without
  resolving the difference; `auto_relate` commits each edge through `add_relationship`. Resolved in revision 2: the
  Constraints quote the issue's defect sentence ("rows the failing job wrote before it raised are committed along
  with the failure status"); D4 states that "none of them" covers what a failed job leaves uncommitted, explains why a
  unit a dispatcher commits itself (idempotent per-edge upserts; single-transaction stores) correctly stays, rejects
  whole-job atomicity (savepoint, or compute-then-write in `auto_relate`) with its cost, and leaves the
  compute-then-write form open to Michaël at approval. Item 5 adds
  `test_a_failed_job_keeps_the_unit_it_committed_itself`, pinning the boundary (red count 5).
- [MEDIUM] D7's non-blank check could pass on a heading-only section. Resolved in revision 2: D7 checks the body after
  the heading line; item 7 adds the red case "body deleted in all three, heading kept" (`3 failed, 2 passed`).

Round 2 (plan SHA-256 535e1ba6…, altitude report 4122c5d4…): LGTM, both round-1 findings confirmed resolved. No new
finding.

Round 3 (plan SHA-256 581dba17…, altitude report f8b49970…): a wording-only delta (the plan's own file path
replaced by portable text, which the plan-issue archiver requires). LGTM, no new finding.

Degradations: none. The Codex model is `gpt-6.1-terra`, the model Michaël's 2026-11-21 ruling names for Codex; it is
not a degradation.
