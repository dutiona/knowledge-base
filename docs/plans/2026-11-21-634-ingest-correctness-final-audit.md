# Final audit (post-review): plan #634, ingest correctness

- Scope: post-review.
- Approved plan: `docs/plans/2026-11-21-634-ingest-correctness.md`, sha256
  `b39c9ff5cfed28699e2d1f18d41091f1238e568b7bc092bbd8e8b837f2eccaac` (approved on #637).
- Post-review head audited: `65a2a9cd48fc01472a5cc15a194586860f99e7a9`; pre-review audit at `da327b0` (beside this
  file).

## Review (super-review, Full)

Codex `gpt-6.1-terra` medium (the plan-review thread, resumed) and agy `gemini-4-hydrogen-medium` (the plan-review
conversation, resumed), both PRs (this one and dutiona/memory-engine#1122) in the same sessions. No degradation.

- Round 1 at `da327b0`: agy LGTM, every criterion PASS. Codex: one HIGH (the cascade listed its spaces before the
  write lock, so a space created in that gap kept a deleted chunk's vector), one MEDIUM (the reference overclaimed
  what a failed call rolls back), one MEDIUM (PR closure and pairing not checkable offline; verified on the PR).
- Fixes: `541e0a9` (the schema read stays first, the spaces are listed after the first write; a test creating and
  filling a space just before that write failed before, passes after), `65a2a9c` (the wording), and `f3f592a` (a
  basedpyright error the merge gate found in a new test).
- Round 2 at `65a2a9c`: both LGTM.

## Items since the pre-review audit

The review fixes stay inside items 3, 7 and their documentation: no item Modified, Dropped or Added beyond the
pre-review audit's one addition. Counts: Implemented 10, Added 1, **10% divergence**.
