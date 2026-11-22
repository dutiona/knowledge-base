Plan SHA-256: 581dba179e63066dcb7c6bae5a7e028691f7a25226db8b52aca2ee0a536cded7

# Altitude checkpoint: docs/plans/2026-11-22-646-post-d-tail.md

## Measurements (wc, grep; no parser)

- Plan: 366 physical lines, 32,708 bytes (revision 3: round-1 findings resolved; the plan's own file path replaced by
  portable text, which the plan-issue archiver requires).
- Top-level implementation tasks (`grep -c '^- \[ \]'`): 10 (item 0 the plan commit, items 1-8 one commit each, item 9
  ship). Five are members, three boy-scout, two operational.
- Fenced code blocks: 0. Literal data: one 10-row Markdown table (the Verify list and its expected results, ~1.1 KB)
  and inline command lines and exact replacement sentences for docs. No complete canonical file, validator, generated
  payload, manifest, transcript or mutant catalogue.
- Named target files and their measured size today: `src/knowledge_base/embed_swap.py` 547 lines,
  `src/knowledge_base/redact_store.py` 290, `src/knowledge_base/jobs.py` 282, `src/knowledge_base/embeddings.py` 591,
  `tests/test_embed_limits.py` 403, `tests/test_jobs.py` 280, `tests/test_pymupdf4llm_integration.py` 212,
  `tests/test_ingest.py` 4,516, `.claude/skills/release-checklist/SKILL.md` 224, `docs/reference/schema.md` 647,
  `docs/usage/ingesting-documents.md` 423, `docs/design/architecture-overview.md` 160,
  `docs/getting-started/core-concepts.md` 230; new `tests/test_agent_instructions.py`.
- Estimated changed surface (estimate, not measured): src about +40/-25 lines (two embed_swap helpers replacing
  backfill's inline lines, the rewrite's cut plumbing, two rollbacks and the progress rule, three default removals);
  tests about +230/-25 (two #638 tests, eight #643 cases, the #639 file, the rewritten slow test, one deleted test);
  docs about +15/-8 lines across five files.

## Calibration

The plan's purpose is to assign five independent bug-class fixes with their red/green evidence and the design choices
each needs. The decisions section (13 items with rejected alternatives) is the larger part of its bytes; it states
rules and signatures, not code. The plan is longer than the estimated diff (about 33 KB of plan against roughly 300
changed lines), which is explained by five members each needing its acceptance quoted, its decision recorded with
alternatives, and an executable red step (two of them, #639 and #644, need constructed reds: a perturbed tree and an
external reproduction environment). No item duplicates a product file, a validator or a generator; no frozen
transcript or catalogue is embedded.

## Inputs

Required fallback/provenance inputs: none. The planning-time measurements the plan cites (D9, D10, D12) are stated in
the plan itself and are re-run in item 1.

## Disposition

proceed — the artifact remains an implementation plan: tasks, invariants and acceptance, with no product
specification or second copy of the product.
