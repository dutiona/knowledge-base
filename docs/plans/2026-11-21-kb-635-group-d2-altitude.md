Plan SHA-256: 141d6db124319ad71d00fc5b1632cec08be87f6e6efd5d233c49b2b81bf74a60

# Altitude report: docs/plans/2026-11-21-kb-635-group-d2.md

## Measurements (method: `wc -l -c`, `grep -c '^- \[ \]'`, and an awk fence counter over lines opening with three backticks)

- Physical lines 487, raw bytes 37802 (revision 3; revision 2 was 481 lines, 37246 bytes, SHA-256
  7e8a004045a733eda995d8fa5e551757346f59a6e72f701b2bd01c2d579e2b19; revision 1 was 415 lines, 31285 bytes, SHA-256
  9ae2208be59feda159d819fdda8287030fa1c52e1e338eaa1f25c38fffe41265).
- Top-level implementation tasks: 8 (`- [ ]` at column 0): items 0 and 7 are operational (worktree/plan issue/sibling
  check; PR/review/finish), items 1 to 6 are one commit each.
- Fenced code: 3 fences, 13 body lines, 1372 bytes, at plan lines 214-222 (the Verify image step, the exact text item 3
  adds), 262-268 (item 5's signature one-liner, a red/green check) and 466-468 (Verification 2's per-commit rebase
  command). All three are commands or a short instruction-file addition, not complete canonical files, validators or
  generated payloads. Inline backticked exact texts (toctree entries, a docs line, two URLs, three docs sentences) are
  the replacement strings the items assign.

## Target surface

- Existing files named, measured lines: `src/knowledge_base/embeddings.py` 639, `src/knowledge_base/embed_swap.py` 531,
  `tests/test_embeddings.py` 1153, `tests/test_embed_limits.py` 317, `docs/index.md` 45, `docs/conf.py` 34,
  `AGENTS.md` 93, `CLAUDE.md` 166, `GEMINI.md` 94, `.claude/skills/release-checklist/SKILL.md` 214, plus six docs
  pages touched by a sentence or link each (`ingesting-documents.md`, `installation.md`, `architecture-overview.md`,
  `embedding-spaces.md`, `mcp-tools.md`, ADR-0018).
- Revision 2 adds schema v4 to #620 (Codex round 1's HIGH on interrupted backfills): `src/knowledge_base/migrate.py`
  (350 lines), `src/knowledge_base/db.py` (one tuple entry), `tests/test_embed_identity.py` (727 lines),
  `docs/reference/schema.md` (622 lines) and `README.md` (457 lines) join the named files.
- Estimated changed surface (estimate, not measured): about 150 lines deleted from `embeddings.py` and
  `tests/test_embeddings.py` (#605), about 80 added in source and 120 in tests (#620 with its migration), about 40 lines of docs and
  toctree, 3 x 8 lines in the Verify blocks, about 15 lines in the release checklist: roughly 480 changed lines.
  Uncertainty: the test deletions in #605 could be 50 lines either way.
- No generated renders are involved.

## Fallback/provenance inputs

None. The plan's measured facts were taken from the repository at 528d170 and the worker's own probe runs; no external
file is a required review input. (Reviewers may read the member issues #576, #586, #605, #617, #620 and #635 on GitHub
if they have access; the plan quotes each acceptance verbatim in Constraints.)

## Calibration and disposition

- Purpose: one implementation plan for a five-member PR whose members interact in shared files (the three Verify
  copies edited by items 1-3 and referred to by item 4; `embeddings.py` edited by items 5 and 6; `embed_swap.py` shared
  with a parallel sibling PR). The owner asked that the release/verify commands be shown exactly and that the
  shared-file functions be named, which accounts for most of the exact text.
- Signals checked: no complete product file, no duplicated validator or generator, no frozen manifest, transcript or
  mutation catalogue, no near-copy successor. The plan (487 lines) is close to the estimated diff (about 480 lines),
  because it carries six decisions with rationale and a Verification section a docs-and-procedure change needs; the
  per-item detail is assignment (files, red, change, green, commit), not a transcript of the code.
- Disposition: **proceed**. Reason: the length comes from coordinating six commits across five issues with stated
  decisions, not from a change of role into a design or product specification.
