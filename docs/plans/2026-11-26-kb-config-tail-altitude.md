Plan SHA-256: 068fb954f2337555fe26437a8a2ac65831233637f0db5533a631c48979252fcc

# Altitude checkpoint — `docs/plans/2026-11-26-kb-config-tail.md`

Revision: r10 (owner ruling after the Codex-seat review: the exact-key removal layer is dropped). r9 was
`805bcda6…1814`; r1 `40c3522b…0df4`, …, r8 `1d7f6801…66f2`. This revision **removes** about 250 lines from the plan and
about 130 production lines from the design: `without_secret`, `without_secret_in_data`, `_mask`, `_free_key`,
`llm._without_sent_key`, `llm.configured_key`, their sinks and their twelve tests are gone; the connectivity-warning
rewrite is gone. Two items were added: the MCP tool result as a sink (`routes/extraction.py`) with a test, and two
accepted residuals (older rows; an endpoint's echo).

## Candidate measurements

- Path: `docs/plans/2026-11-26-kb-config-tail.md`; 389 physical lines; 30 342 raw bytes (r9: 591 / 46 908).
- Top-level implementation tasks: **8** (`- [ ]` at column 0 outside any fence; a line scan that skips fenced regions,
  no new parser). Steps 0 and 6–7 are operational; steps 1–5 are the two commits.
- Fenced content: 100 lines / 5 790 bytes in 7 blocks — `require_key` (124–135), `_key_to_send` (161–172),
  `_check_llm_key_spec` (173–183), `error_text` (221–235), the step-5 red test (258–263), the step-5 replacement of the
  linearity test (268–315, the largest at 47 lines), and the verification command list (371–380). Every block is an
  excerpt of a signature, a rule or a test written verbatim; **none is a complete canonical file, validator or generated
  payload**.

## Target files: measured existing surface

| File | Lines | Planned change |
| --- | --- | --- |
| `src/knowledge_base/settings.py` | 398 | +15 est. (`require_key`, `_key_source` refactor, `__all__`) |
| `src/knowledge_base/llm.py` | 411 | +20 / −6 est. (`_key_to_send`, `_check_llm_key_spec` + call sites, 3 header sites, 2 docstrings) |
| `src/knowledge_base/utils.py` | 640 | +18 est. (`error_text` only) |
| `src/knowledge_base/jobs.py` | 291 | +6 / −4 est. (both failure branches) |
| `src/knowledge_base/indexer.py` | 479 | +2 / −2 est. |
| `src/knowledge_base/extraction.py` | 1 097 | +8 / −6 est. (four render sites) |
| `src/knowledge_base/routes/extraction.py` | — | +2 / −1 est. (`error_text(e)` at the handler, import) |
| `tests/test_llm.py` | 831 | +60 est. (4 tests, one parametrized × 12) |
| `tests/test_jobs.py` | 357 | +35 est. (1 parametrized test) |
| `tests/test_extraction.py` | 2 019 | +40 est. (2 tests) |
| `tests/test_indexer.py` | 991 | +12 est. (1 test) |
| `tests/test_routes_extraction.py` | — | +30 est. (1 tool-path test) |
| `tests/test_redact.py` | 270 | −19 / +45 est. (the linearity test is replaced) |
| Docs (5 files) | 2 451 | +10 lines est. |

Estimated production change: about **70 lines** across seven modules (r9's design: ~200). Test surface: about +220 lines
(r9: ~395). No new module, no new dependency, no schema change, no deployment or runtime-topology change.

## Duplication and ownership

- The key rule gains exactly one home (`settings.require_key`), `_key_source` refactored onto it — the plan removes a
  duplicate rather than creating one.
- `error_text` gains one home (`utils.py`); `jobs.py`, `indexer.py`, `extraction.py` and `routes/extraction.py` call it.
  `routes/__init__.py` keeps `embedder_failure`, a different contract (it also returns a retry flag); this change does
  not merge them.
- `redact.scrub` is deliberately not part of the rule (measured: it cannot see a 32-hex key; and it rewrites links and
  placeholder-key output).
- No validator, generator, manifest, transcript or mutant catalogue is copied into the plan; the pre-fix module is run in
  a throwaway probe and quoted as a measurement, never vendored.

## Regeneration / provenance inputs

N/A: a fresh plan, not a successor or amendment. No predecessor plan or design document is an input. (The revision
responds to a review artifact and an owner ruling; neither is a plan input the reviewers must hash — the ruling's text is
quoted in Constraints, and the review artifact is referenced by its findings.)

Required fallback/provenance inputs beyond the plan itself: **explicitly none.** The issues' acceptance is quoted
verbatim in Constraints; the code facts are the tree at HEAD; the K1 precedent (#661, merged as 82af52f) is cited by
file and line and its code is in the tree; the #664 evidence uses `git show a87f6c8^:src/knowledge_base/redact.py`, in
the repository's own history. The review copy carries only `.review/plan.md` and `.review/altitude.md`.

## Calibration and disposition

Disposition: **proceed**.

- Role: an implementation plan assigning work, invariants and acceptance. At 389 lines for two bug fixes it is at the
  upper edge of "plan, not transcript"; the reduction from r9 (591) is the point — the layer that consumed nine review
  rounds is gone, and what remains is the refusal rule, one renderer, its sinks, one MCP tool-path test, and the #664
  rewrite.
- Detail budget: every item names its files, its failing test and red command, the change as a signature/rule/exact
  text, its green command with the expected result, and its commit subject.
- Scope discipline: two commits, matching the two member issues. The three items beyond the issues' literal wording are
  each argued: the MCP tool result (owner instruction, F4), the `extraction.py` composition sites (without them #662's
  third criterion cannot hold), and the #664 retry removal (simplification with measured evidence).
- Structural signals: no second validator, no frozen catalogue, no planning maintenance exceeding implementation.
- Measurement quality: #664's numbers are measured on this desktop (linear 1.97–2.13 by me; 1.72–2.30 over 42 pairs idle
  and under 40-way load by the reviewer; the #592 pre-fix module run at N = 2**19 gives 3.52 against 2.03; a quadratic
  double 3.85–3.99). The cost is the sum of measured pair times (~4.9 s against today's ~6.5 s).
- Residuals: two accepted, one out-of-scope, all stated in Constraints and in the path table (rows 9–10).

No override is requested; the reviewers are asked to acknowledge this report's digest with the plan's own review.
