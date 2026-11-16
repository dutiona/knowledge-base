# Advisor capability unavailable

Altitude report SHA-256: 26ed18e6eb4b66b711db7bc58a90861d3a6ce7ff990bfc9fb5dc976b031d1235

- The altitude line above is controller metadata naming the final report the fallback reviewers assessed (round 1
  assessed `aca6edb9…`; each round's report digest is recorded in the reviewers' own artifacts).
- Reason: `advisor()` is not exposed in this fleet worker's harness (no advisor tool in its tool list).
- Gate: Full. Its advisor-less form runs instead: Codex (headless) + agy (resume) + one clean-slate subagent.
- No advisor findings exist; the findings below the Resolution are attributed to the reviewer that produced them in
  their own artifacts (`*-subagent-review.md`, the Codex shared file, the agy round's `review.md`).

## Resolution

Fallback roster that actually ran (gate Full, advisor-less form): Codex headless (8 rounds, one resumed thread), agy
resume (8 rounds, one conversation; round 1 transport `degraded`: completed review, unclean CLI exit), and one
clean-slate Claude subagent (8 rounds, resumed). Final verdicts on candidate 8 (plan SHA-256
`12997b016ebded1a3eac4956fb6028af1a0687494543097f776c1a5db7c14528`, altitude report
`26ed18e6eb4b66b711db7bc58a90861d3a6ce7ff990bfc9fb5dc976b031d1235`): Codex LGTM, agy LGTM, subagent LGTM.

Codex findings by round, and their disposition (full text in `*-external-codex.md`):

- R1 [BLOCKER] tool-created spaces defeat the endpoint boundary → D3 builder + promote guard. [HIGH] tool URLs not
  floored (redirects, ollama LLM, vision/omniparser/browser) → owner call (d), KB-6 (#572). [HIGH] re-embed procedure
  unworkable → `re-embed`/`rebind` transitions. [HIGH] legacy `/v1` normalization → D1 rule. [HIGH] onnx endpoint →
  `ONNX_EMBED_MODEL_PATH` required. [HIGH] full suite → per-item full suite. [MEDIUM] red specifics → fixed.
- R2 [HIGH] stored LLM key bypass → allowlist on stored rows. [HIGH] failed re-embed appears bound → active-space
  check. [HIGH] promote summary rebuild → all-or-nothing promote. [HIGH] comparison spaces → owner call (f).
  [HIGH] onnx path unbound → dismissed (ADR-0015 slug-level identity). [MEDIUM] slow tests → per-item full suite.
- R3 [HIGH] refused re-embed adopts URL → checks before writes. [HIGH]×2 resume mixes endpoints / reuse ignores
  parameters → reuse rule dropped, owner call (g). [HIGH] no recovery after summary failure → all-or-nothing promote.
  [MEDIUM] rollback test not selected → test removed with the rule.
- R4 [HIGH] all-or-nothing test fails only before writes → explicit transaction, in-transaction failure test.
  [MEDIUM] populating cleanup unworkable → failed `re_embed` removes its own space.
- R5 [HIGH] embed before the lock → whole promotion under one lock (then refined in R6). [MEDIUM] killed process
  orphan → `BaseException` cleanup, residual documented.
- R6 [HIGH] late writer after promotion → pre-existing lifecycle race, bounded by D3, collateral finding.
  [HIGH] `isolation_level` commits a caller's transaction → refuse `conn.in_transaction`.
- R7, R8: LGTM.

agy findings (full text in `*-external-agy.md`): R1 three LOWs (URL wording, bare-name `EMBED_PROVIDER`,
`configure_vision` write-then-raise), all applied; R2–R8 LGTM.
