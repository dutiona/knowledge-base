Plan SHA-256: 17a10967c5e2d406729432a25bcfe190a240ce1c6b311e4eb969b194182a8e49

# Altitude report: docs/plans/2026-11-23-657-q-redaction-parity.md

- **Plan:** 326 physical lines, 29,524 bytes (revision 3; revision 2: 326 lines, 29,519 bytes, SHA-256 8fd0fed9…; revision 1: 306 lines, 27,314 bytes, SHA-256 d2d254c3…); 11 top-level implementation tasks (`grep -c '^- \[ \]'`, none inside a
  fence).
- **Literal volume:** two fenced blocks, 21 lines / 859 bytes, both in Verification (lines 282–291, 296–310): the KB
  and ME merge-gate command lists copied from each repository's `AGENTS.md`. Commands, not product files. One table
  in D5 (three fixture cases, about 0.6 KB) names inputs and outputs by reference to existing fixture tokens; it is
  the case specification the ME commit encodes, not a canonical file (the committed bytes come from ME's own
  re-encoder). No validator, generator, manifest or transcript is embedded.
- **Target surface (measured):** KB `src/knowledge_base/` redact.py 464 lines, embeddings.py 601, embed_swap.py 571,
  ingest.py 815, folder_summaries.py ~140, migrate.py 377, redact_store.py 323; tests: 235 `ingest.embed` and 180
  `folder_summaries.embed` patch sites across 18 files (grep). ME: the fixture (136 lines), `tests/redaction_conformance.rs`
  (one constant), `docs/reference/redaction.md` (two lines).
- **Estimated change (unmeasured, labelled estimate):** production about 60–100 lines changed across six KB modules
  (most of it the deleted `embed()` and the two folder writers), one new migration (~15 lines); tests: ~415 decorator
  or context-manager lines rewritten mechanically, ~15 new tests (~250 lines); docs ~40 lines across 8 files. ME: three
  fixture lines, one constant, two doc lines.
- **Regeneration:** none. Revision 2 is an in-place edit answering round 1's findings (about 20 added lines in D2, D3, D4, items 2, 5, 6, 10 and the last section); no section copied or restructured. Revision 3 rewords two sentences (Outcome, Testing) that still said keys take the caller's leaf, so they agree with revision 2's D4.
- **Fallback/provenance inputs:** none required. The plan's facts come from the code at the KB branch base (1e1bd20)
  and ME main (bfa0601), which reviewers read directly.
- **Calibration:** a coordinated three-issue plan across two repositories, one schema migration and a ~415-site test
  refactor. Its detail budget goes to decisions (D1–D7 with rejected alternatives), the order, and per-item red/green
  commands; the mechanical conversion is specified as rules for bundles, not enumerated. The plan is shorter than the
  code and tests it describes and assigns work rather than defining the product twice.
- **Disposition:** proceed.
