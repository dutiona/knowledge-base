# Advisor capability unavailable

Altitude report SHA-256: 7b31829ae1e3ede63fc98507e08424f55c06555750f4d612a2dd22f0ad1a1113

The digest above is controller metadata: the altitude report the fallback reviewers assessed in their final round. No
advisor verdict exists.

- Reason: `advisor()` is not exposed in this worker's harness (no advisor tool among its tools), so step 3a could not
  run.
- Gate: Full. Its advisor-less form is Codex + agy + one clean-slate subagent.
- Fallback reviewers that actually ran:
  - Codex (headless, the config default model; one persistent thread, 9 rounds). Round 1: one MEDIUM (the network
    test checked only `uv` commands, so a networked non-uv `RUN` would pass) and a qualification (`--no-build` can
    reuse a cached source-built wheel). Round 2: one MEDIUM (the scrub grep matched its own pattern) and one LOW
    ("every later build-stage step" includes `COPY src`). Round 3: LGTM. Round 4: one MEDIUM (the allowlist's option
    values could carry shell syntax, `--group reranker;id`). Rounds 5 to 9: LGTM.
  - Clean-slate Claude subagent (Opus; 9 rounds), recorded in full with its resolutions in
    `2026-11-17-offline-image-build-subagent-review.md`.
- Degraded: agy (`gemini-3.8-flash-high`), round 1 returned `RESOURCE_EXHAUSTED (code 429): Individual quota reached`,
  resetting about 78 hours later; the helper's receipt is `incomplete`, cleanup verified. No agy review exists. The
  owner's other agy model is also out of quota until about 2026-11-21, and no other agy model was selected.

## Resolution

- Codex round 1 MEDIUM → item 2's test became an allowlist (networked `RUN`s are only locked, wheels-only `uv sync`
  commands, no other shell metacharacters, no compile, no `--python`/`--active`; offline uv commands carry
  `--offline`, the install `--no-deps`). The subagent raised the same gap (M1).
- Codex round 1 qualification → the fetch comment and the measured facts say the image build starts with an empty uv
  cache, so there `--no-build` means a source-only package fails.
- Codex round 2 MEDIUM → the scrub greps a pattern file of the host's real identifiers built outside the worktree;
  run before revision 3 was dispatched, no match. The subagent raised the same problem (R2-M1).
- Codex round 2 LOW → "Every later build-stage `RUN`".
- Codex round 4 MEDIUM → each allowlisted `<name>` and `<path>` must match `[A-Za-z0-9._/-]+`. The subagent raised the
  same point (R4-L1).
- The clean-slate subagent's later LOWs (rounds 5 to 7) replaced the token parser in item 2's test with an exact
  build-stage prefix; its round-8 and round-9 LOWs were arbitrated (an accepted, stated boundary; a cosmetic wrap,
  fixed). Both reviewers' final verdicts on revision 9 are LGTM.
- agy degradation → reported loudly at plan approval and in the PR; no substitute external voice was added.
