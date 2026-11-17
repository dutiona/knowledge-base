Plan SHA-256: 97f36fce176318c058ff373724b1ac16c9656fe41c94c97ec99b9aafe553d21a

# Altitude report: docs/plans/2026-11-17-offline-image-build.md (revision 9)

## Measurements

- Plan: 318 physical lines, 26 891 bytes, 3 top-level implementation tasks (`grep -c '^- \[ \]'`; no task-like line
  sits inside a fence). Revision 1: 261 lines, `2f07a7d6…0950`; revision 2: 298, `28e7f15d…b2df`; revision 3: 308,
  `5a166abb…bc7d`; revision 4: 311, `db26e81e…d666`; revision 5: 314, `824dc1a3…9b48`; revision 6: 318, `f4d3f8b2…0b5b`;
  revision 7: 313, `bcfa4852…6ee9`; revision 8: 318, `c741dbf6…4bf7`.
- Fenced literals: 3 blocks, 20 body lines, 1 969 bytes, unchanged since revision 2: the 3-line `pyproject.toml` group
  (item 1), the 3-line `Containerfile` header and the 14-line replacement build-stage block (item 2), the issue's own
  specified change with the plan's three departures applied. The guide paragraph (item 3) is an 8-line quote. No
  validator, generator, manifest, transcript or mutant catalogue.
- Target files and measured surface: `Containerfile` (35 lines, about 15 changed), `pyproject.toml` (100 lines, 3
  added), `uv.lock` (generated, about 47 lines added, excluded from product estimates), `tests/test_container_build.py`
  (61 lines; estimate 30 to 45 added: two tests, one asserting the exact build-stage prefix, one tightened assertion,
  a moved skip marker), `docs/getting-started/installation.md` (256 lines; 8 added). Hand-written product change:
  estimate 60 to 75 lines.

## Delta from revision 8

Both reviewers gave LGTM on revision 8. One correction since: recording the Super evidence requires Codex's verdicts as
`docs/plans/2026-11-17-offline-image-build-external-codex.md`, so Operations 2 names five committed files, not four,
and is rewrapped. This report is rewrapped at 120 (round-8 LOW). No other change.

## Required inputs

- The issue body, source `issue-600-body.md` (fetched from dutiona/knowledge-base#600, no comments), review copy
  `.review/issue-600-body.md`, SHA-256 `1e5e87207f0c2e889b9c93fab7bbe8d5bf45dd3fd2dd372d94b10e95cabe197d`. Unchanged.
- No fallback/design/predecessor inputs: a revision under review, not a successor or split.

## Calibration and disposition

Unchanged: the plan is longer than the hand-written code, the excess being verbatim acceptance, measured facts
justifying D1 to D3 and a verification protocol for a property only real image builds can observe. All growth since
revision 1 traces to review findings. No product file restated, no duplicated ownership.

Disposition: **proceed**.
