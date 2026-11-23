Plan SHA-256: f44e73583bdcc497d3a83dc0d0d7dfb5d67eaa836b96b7b59e0cc23292162e8f

# Altitude report: docs/plans/2026-11-23-embed-key-file.md

## Measurements
- Physical lines 289, raw bytes 23466 (`wc -l -c`).
- Top-level implementation tasks: 8, counted with `grep -c '^- \[ \]'` (items 0-7: 4 change items, 4 operational).
- Fenced code: 0 fences (`grep -c '^```'`), 0 lines. Literals are inline only: one regex (`[!-~]+`), the
  error-message texts, the spec string and test parameter lists. No complete canonical file, validator, generated
  payload, manifest or transcript is embedded.
- Named target files (existing size, `wc -l`): src/knowledge_base/settings.py 348, utils.py 637 (one branch +
  docstring), llm.py 411 (one condition + docstring); docstrings only in embed_swap.py and embeddings.py; tests
  conftest.py 451, test_settings.py 472, test_utils.py 470, test_llm.py 827, test_server.py 773, test_db.py 2019,
  test_embeddings.py 1018; docs README.md 468, installation.md 278, schema.md 713, ingesting-documents.md 423,
  ADR-0018 438.
- Estimate (labelled, uncertain): about 40-60 changed source lines, about 150-220 new test lines, about 15 doc lines.

## Regeneration
N/A: a first plan, no predecessor.

## Required fallback/provenance inputs
None. The plan cites repository files (read from the review tree) and memory-engine's
`crates/memory-engine-embed/src/key_file.rs` / `crates/memory-engine-mcp/src/config.rs` (another repository) for parity
context only; the plan restates the parity rule it relies on, so no copied input is required for review.

## Calibration and disposition
Purpose: assign a small, settled, security-boundary change (Super forced by the security-or-auth-boundary floor, one
security-first lens). The plan is about the size of the code and tests it assigns, which is near the structure's
"shorter than the code" line; the excess is the trust-boundary table and the decision list, which are the lens's
deliverable (each row names a reachable sink and the guard that stops it), not a second copy of the product. No
duplicated validator, no product file, no catalogue. Disposition: **proceed**.

## Revision 2 (after review round 1)
Delta: decision 2 scoped to the providers that send the key, decision 3 states rule order, decision 8 restated, an
unset-variable fail-closed branch for the file spec, three test cases (ollama padded env key, UTF-8 BOM, ollama empty
file), a stale-claim `rg` check in item 4, and a memory-engine deviation note for the owner. Still 0 fences, no product
file, no duplicated validator; the growth is review resolution, not a change of role. Disposition: **proceed**.
