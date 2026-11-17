Plan SHA-256: 61434ebe86cf44c91eebe73cba3466f666403f615771284218e9bd3de1b5428e

# Altitude checkpoint: docs/plans/2026-11-17-http-bearer.md

Round 6: the round-5 candidate (`4555291c…4893`, LGTM from all three reviewers) with one phrase reworded for publication (GitHub altered the literal `\u` escape in item 8 on read-back, so it now names a NUL character in words), itself the round-4 candidate (`3682a0f3…42a2`) revised for its findings (Codex LGTM; agy 1 MEDIUM, refuted by measurement, its hardening adopted; clean-slate subagent LGTM with 4 LOW), itself the round-3 candidate (`8a2c381e…cfdc`) revised for its findings (Codex 1 MEDIUM; agy 1 LOW, the same; clean-slate subagent 2 MEDIUM, 2 LOW), itself the round-2 candidate (`e3ceb6b4…c1e`) revised for its findings (Codex 2 HIGH; clean-slate subagent 1 HIGH, 1 MEDIUM, 2 LOW; agy LGTM), itself the round-1 candidate (`c110bd30…83be4`, synthesis of the mvp-first, risk-first and architecture-first lens
drafts kept under `docs/plans/2026-11-17-http-bearer-drafts/` as ephemeral working files) revised for the round-1
findings (Codex 1 HIGH, 1 MEDIUM; clean-slate subagent 6 MEDIUM, 7 LOW; agy LGTM). The earlier reports are retained
outside the tree at `~/.local/state/super-plan/2026-11-17-http-bearer/altitude-r1.md`, `altitude-r2.md` and `altitude-r3.md`.

## Measurements

Counting method: `wc -l -c` for size; `awk` counting lines that start `- [ ] **` outside fenced blocks for items;
`awk` toggling on lines that start with three backticks for fenced volume.

- Plan: 499 physical lines, 45,319 bytes (round 5: 499, 45,295; round 4: 493, 44,701; round 3: 480, 43,267; round 2: 454, 40,561; round 1: 417, 36,434); 13 top-level implementation items (0 to 12), of which items 0, 1, 10, 11
  and 12 are operational (baseline, plan issue, PR, review, merge), items 2, 3 and 9 are documentation-only commits,
  and items 4 to 8 carry executable behaviour.
- Fenced literals: one block, lines 476 to 484, 7 body lines, 184 bytes: the AGENTS.md merge-gate command list, a
  command list, not a canonical file. Inline literals: one-line commands (the mint recipe, the curl check, the leak
  guards) and ME's six error reasons; no complete product file, validator, generator, manifest or transcript is
  embedded.
- Named target files and measured existing surface: `src/knowledge_base/server.py` (54 lines),
  `src/knowledge_base/exceptions.py` (49), `tests/test_server.py` (234), `tests/conftest.py` (221), `README.md` (332),
  `docs/getting-started/installation.md` (248), `docs/design/architecture-overview.md` (152), `pyproject.toml`,
  `uv.lock`, `AGENTS.md`.
- Estimated new or changed surface (estimates, not measurements): `src/knowledge_base/auth.py` new, about 130 to 170
  lines; `server.py` about +50 to 70; `tests/test_auth.py` new, about 180 to 230; `tests/test_http_auth.py` new, about
  220 to 300; `tests/conftest.py` about +80 to 120; `tests/test_server.py` about +100 to 140; docs about +60 to 90
  lines. Total roughly 820 to 1,120 lines; uncertainty mostly in the parametrized test tables. `uv.lock` is a generated
  render and is excluded.

## Calibration

The plan's role is an implementation plan: it fixes decisions (a to i), names files, signatures, exact strings and
statuses, and the failing test of each item. It does not restate the product: the token-file grammar is ADR-0020's,
quoted once as a constraint, and the plan points at it rather than shipping a second validator. The parser rules in
item 4 are the minimum an implementer needs to match ME byte for byte (split on LF only, ME's reasons, no quoting); the
test tables mirror ME's `auth.rs` test cases rather than inventing a catalogue. The refusal matrix in item 6 is the
acceptance's "any unauthenticated route … exposes no tool and no data", enumerated so it can be asserted. The plan is
shorter than the code it describes (499 lines against an estimated 820 to 1,120). No regeneration, no predecessor, no
duplicated ownership: the token contract stays in memory-engine's ADR, the gate and parser in `auth.py`.

Structural signals checked: no complete product file or large canonical literal; no duplicated validator or generator;
no frozen manifest, transcript or mutant catalogue (the three mutations of item 6 are temporary red checks of the
tests, reverted, not stored); planning maintenance does not exceed implementation.

The round-2 growth (+37 lines) is decisions and test rows the findings asked for (log redaction, uvicorn logging,
startup exit 1, IP-literal hosts, container bind, smoke isolation), not product restated: no new literal, file or
catalogue. A reviewed-amendment or design split is not warranted; the plan is regenerated in place, pre-approval. Round 3
adds decision d2 (one formatter for every log line) and rewrites item 8's tests around it: again decisions and test
rows, no product literal. The formatter is specified by its rules, not transcribed. Round 4 adds
two formatter rules (control escapes, long-line breaks), test rows and residuals: the same kind of content. Round 5 changes
wording and two test assertions only; round 6 one phrase.

Disposition: **proceed**.

## Required fallback/provenance inputs

Copied into the review tree under `.review/inputs/`; each digest is of the source file named.

| Source | Review copy | SHA-256 |
|---|---|---|
| `~/.local/state/agent-work/2026-11-02-me-kb-claude-code/fleet/artifacts/knowledge-base-571/scratch/ctx/planning-context.md` | `.review/inputs/planning-context.md` | `777fa49d0de02bfee53b12fbdce328771095c23d183a55098b464702d7162918` |
| `…/scratch/ctx/adr-0020.md` (memory-engine `origin/main` ADR-0020) | `.review/inputs/adr-0020.md` | `5118e73d627496d3da88312fd96b8d315a35734ae9388c3def62edb8f62e5d79` |
| `…/scratch/ctx/me1018-auth.rs` (memory-engine#1018 branch) | `.review/inputs/me1018-auth.rs` | `90858d44be69c7f6449f7da60b6fffe65c61dd5d16a7b7762d6e6c6930691e0b` |
| `…/scratch/ctx/me1018-http.rs` | `.review/inputs/me1018-http.rs` | `5c10911a33aced618915f7324d9a32932d7a52c1ed651b8b08f1fc19c3411eca` |
| `…/scratch/ctx/me1018-mcp-server.md` | `.review/inputs/me1018-mcp-server.md` | `e482a99b5510db9f53720cc7dc1564c6fa58871b2e06106e5ad91871a0eccb36` |
| `…/scratch/ctx/spike-gate.py` (spike 1: gate, routes, statuses) | `.review/inputs/spike-gate.py` | `a782ca9e144087daa3506b797e3e478d4951b35b6d3c171ccbada553df93c182` |
| `…/scratch/ctx/spike-restart-log.py` (spike 2: restart, per-call machine) | `.review/inputs/spike-restart-log.py` | `5f4c3014cd9c8d7de386b50758f7da1cae3b1983b5e0876f5af53c56551aa1dc` |
| `…/scratch/ctx/spike-log-leak.py` (spike 3: what the SDK and FastMCP log for a token-named tool and a token argument) | `.review/inputs/spike-log-leak.py` | `7bfcc70efc900deadfdacb2e2a3601c9b0ab1c80fe00a3e81ae22392d4d1befa` |
