Plan SHA-256: 720899d4e3e03eca36d5883675a7c4d9690247ffa33a9d632af3acc475e17da3

# Altitude report: 2026-11-17-kb-543-lean-surface

- Plan: `docs/plans/2026-11-17-kb-543-lean-surface.md`, 605 physical lines, 52,595 bytes (`wc -l -c`).
- Top-level tasks: 10 implementation items (`- [ ]` lines between `## Implementation sequence` and the next `## `,
  counted with awk) plus 5 operational steps (worktree, plan issue, PR, review, finish) in `## Operational steps`.
- Fenced literals: 3 fences, 22 body lines, 1,213 bytes (awk over the fence markers): a 6-line mermaid diagram
  (lines 64-71), the 3-signature tool map (lines 151-161) and the 7-line merge-gate command list (lines 577-585). All
  are commands or interface signatures; none is a complete product file, validator or generated payload.
- Other data volume: the tool-fate table, 48 rows (lines 201-248). It is the issue's required deliverable ("the fate
  of each of the 48 tools on the HTTP surface"), not a frozen manifest of the test apparatus.
- Instruction strings (D7) are quoted verbatim, about 1,100 bytes: they are model-facing product text the issue
  requires to be fixed ("The `server.py` instructions name the real tools"), and an exact text is what makes the
  instructions test decidable.

## Target surface

Existing files the items change (measured, `wc -l`): `src/knowledge_base/server.py` 145,
`src/knowledge_base/auth.py` 191, `src/knowledge_base/routes/ingestion.py` 192, `src/knowledge_base/routes/search.py`
129, `src/knowledge_base/routes/__init__.py` 1, `src/knowledge_base/ingest.py` 735 (one appended function),
`src/knowledge_base/web.py` 1,298 (one tail replaced), `tests/conftest.py` 368, `tests/test_http_auth.py` 200,
`tests/test_server.py` 448, `tests/test_ingest.py` 4,310 (tests appended), `tests/test_routes_ingestion.py` 337,
`tests/test_search.py` 278, `docs/reference/mcp-tools.md` 952, `README.md` 383, plus `AGENTS.md`, `GEMINI.md`,
`CLAUDE.md`, `docs/reference/glossary.md`, `docs/usage/ingesting-documents.md`, `docs/design/architecture-overview.md`.

Estimates (not measured; uncertainty about ±50%): source about +175/-60 lines (`ingest_text` ~60 moved from `web.py`, the
tool ~30, the network hub and instructions ~25, the refusal ~10, `calling_machine` ~8, `embedder_failure` ~15, the body cap ~25); tests
+450 lines (`tests/test_lean_surface.py` new ~300, the rest spread); docs ~+150/-40 lines. No generated renders.

## Inputs

Required fallback/provenance inputs: none. The plan is a fresh candidate (no predecessor, no regeneration). The
research context reviewers may consult is advisory, not a required input:
`~/.local/state/agent-work/2026-11-02-me-kb-claude-code/fleet/artifacts/knowledge-base-543/scratch/research-context.md`.

## Calibration and disposition

The plan has two roles by design: it is #543's approved design (the issue's first acceptance item: every tool's fate,
the ingest contract, the admin answer) and its implementation plan. The Design section (~255 lines, lines 57-310, with the 48-row
table) carries the first role and is the reason the plan is longer than a usual implementation plan; it defines the
contract memory-engine#1028 builds against, which has no other home in this ticket. The implementation sequence
(~196 lines, lines 311-506) assigns ten reviewable commits with their red/green commands and does not restate code. No duplicated
validator, generator, transcript or mutant catalogue. The plan is shorter than the code and tests it describes
(~720 estimated changed lines plus docs).

Disposition: **proceed**. The design-plus-plan combination is required by the brief ("put the whole design ... in
the plan"), so a design split would separate what Michaël approves with one marker.

## Revision 2

Round 1 changed four passages (an error message made truthful for stdio, the content-name rule, the
`CallingMachine` note, the no-delete assertion); no section grew in role. Disposition unchanged: **proceed**.

## Revision 3

The clean-slate review (1 high, 4 medium, 11 low) added: G5 to G7 and the approval deviation note (decisions for
Michaël, ~25 lines), item 9 (a 4 MiB request-body cap, G3's recommendation), sharper D4/D6 wording, and test details in
items 4 to 8. Growth is ~72 lines, all assigning work or stating a decision; no product file, validator or transcript
entered the plan. Disposition unchanged: **proceed**.

## Revision 4

Round 3 (Codex two mediums, the clean-slate reviewer one medium and six lows) changed wording and test details only:
G3's stated scope, the race mapping for `ingest_url`, the subprocess test's rejected-argument call, item 9's 413
handling, the name categories, and four stale references. +12 lines. The estimates above are corrected to their
component sums. Disposition unchanged: **proceed**.

## Revision 5

Three LOW wording fixes from the clean-slate reviewer's round 4 (item 6's refusal text and test names for the D3
categories, the grep's expected self-matches, where the opaque `source_uri` is documented). +2 lines. Disposition
unchanged: **proceed**.
