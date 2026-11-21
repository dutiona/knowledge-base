Plan SHA-256: b39c9ff5cfed28699e2d1f18d41091f1238e568b7bc092bbd8e8b837f2eccaac

# Altitude report: docs/plans/2026-11-21-634-ingest-correctness.md

## Measurements (method: `wc -l -c`; top-level tasks counted with `grep -c '^- \[ \] \*\*[0-9]'` outside fences;
fenced bytes with an awk toggle on lines starting with three backticks)

- Plan: 509 physical lines, 47493 bytes (round 4: item 0 names the plan by its place, not its file name, which the
  plan-issue archiver refuses; round 3 was 509 lines, 47507 bytes, digest `03ba2ea3…2c46`; round 2 was 502 lines, 46672 bytes, digest `d8b12410…a91c`; round 1
  420 lines, 38231 bytes, digest `6cabafc7…b883`).
- Top-level implementation items: 10 (items 0 to 9); 6 further `- [ ]` operational steps.
- Fenced code: one block, 7 lines / 224 bytes (the merge-gate command list, a command example, not a canonical file).
  No complete product file, validator, generated payload, manifest, transcript or mutant catalogue is embedded. Inline
  literals are exact error/help strings and test names the implementer must reproduce (about 2 KB in total), and the
  nine fixture cases of item 8 as one-line JSON documents (about 0.6 KB), which are the shared contract's new data and
  belong to item 8's memory-engine PR, not a copy of it.
- Target surface (existing, measured with `wc -l`): `ingest.py` 836, `web.py` 1270, `routes/*.py` 1457, `redact.py`
  434, `db.py` 1473, `_conn.py` 24, `folder_summaries.py` 148, `indexer.py` 477 (6120 with those files); plus tests
  (`test_ingest.py`, `test_redact_paths.py`, `test_indexer.py`, `test_server.py`, `test_redact.py`, route tests).
  Estimate (uncertain, not measured): about 250 to 350 changed source lines (ingest.py's storage split is the largest,
  net negative or flat; 32 one-line route conversions; the call boundary about 20 lines and 47 one-line decorator
  swaps), about 500 to 800 test lines, about 60 doc lines; on the
  memory-engine side about 40 test lines and 9 fixture lines.
- Not a regeneration: a round-2 revision of the same candidate. Growth (+82 lines) is the round-1 findings' content:
  item 5 (the call boundary, which replaces O2's hand-out rollback), the walk's body-ran rule and per-branch tests, the
  key rule's local parity tests, and the explicit amendments in O1 and O5. Nothing was copied from elsewhere.
  Round 3 replaces the walk's connection recorder with a schema check of its arguments and spells out O5's declined
  branch (a few lines).

## Required fallback/provenance inputs (copied into the review tree's `.review/` and hashed)

- `.review/members.md` (the five member issues with every comment), sha256
  `16c8c2f8dc72b37eee28c43a2824b964c1fbe9656844eb6436a8ad7e9844c842`.
- `.review/probes-633.md` (the #633 probe scripts and their recorded outputs at 528d170), sha256
  `6a342c4d664bbd60c4f803cee765dc64b73b2934948762c824579269efb5ab3e`.
- `.review/me-redact-excerpt.txt` (memory-engine origin/main: `key_has_context`, `scrub_member`, `scrub_json`, its
  JSON-key test, the conformance runner's head), sha256
  `00626981d85e90182c7bf99838d430a85ecae8c5fde3a8fc443ac82985cc0463`.

## Calibration and disposition

Purpose: assign five independent bug fixes plus one cross-repository fixture change to one PR, with each member's
acceptance mapped to a test. Detail budget: the plan names functions, signatures, rules, exact user-facing strings and
test names because the implementer (and the reviewers) must be able to check each item without this conversation; it
does not transcribe code. The length comes from breadth (five members, two repositories, eight owner decisions), not
from a second copy of the product: the largest item (4) states a storage split as five signatures and one rule, about
the size of a docstring per function. The plan is shorter than the code and tests it describes (estimated 800 to 1200
lines of change against 509 plan lines, most of them wrapped prose).

Structural signals checked: no duplicated validator or generator, no frozen output, no planning fixture; the one
literal data set (item 8's cases) is the real contract input and is owned by its memory-engine PR. The #633 section
records a measurement once and references it; it is not repeated per item.

Disposition: **proceed**. No fallback needed.
