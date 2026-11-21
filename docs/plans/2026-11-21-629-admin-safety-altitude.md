Plan SHA-256: 93a3fc1aa8b55a7090a9ae7d754f5a341f3ed64152634c9cfc2cb7a0c0d8488f

# Altitude report: 2026-11-21-629-admin-safety (round 5)

- Plan: `docs/plans/2026-11-21-629-admin-safety.md`, 525 physical lines, 46682 bytes (`wc -l -c`); round 1's
  candidate (`b316c8b2…c20b`) was 473 lines, 41521 bytes, round 2's (`c101049b…f3ee7`) 516 lines, 45908 bytes,
  round 3's (`fef61c81…6b6f`) 524 lines, 46562 bytes, round 4's (`59d8cf92…3328`) 525 lines, 46681 bytes. Round 5
  changes one line: item 0 names "this plan, under `docs/plans/` on the branch" instead of its own path, which the
  plan-issue archiver refuses as a local source reference.
- Top-level implementation tasks: 10 (items 0 to 9), counted with `grep -c '^- \[ \] \*\*'` outside fences (no
  fenced block holds a task line). Round 2 reorders items 3 to 5 (the every-space vector fix now precedes `remove`);
  the count is unchanged.
- Fenced literal volume: 2 blocks, 35 lines, 2479 bytes, at lines 477-485 (the merge gate, seven commands from
  `AGENTS.md`) and 493-522 (the one-off container check: unpredictable resource names, a cleanup that removes only what
  the run created, an
  enforced readiness wait and asserted exit codes and holder messages, after Codex's round-2 findings). Both
  are commands, not canonical files, validators or generated payloads. Inline literals: the exact help, refusal and
  log texts the tests assert by substring.
- Growth since round 1: 52 lines (8 in round 3: the container check and the read-only wording; round 4: one line and three edited lines of that cleanup), all answers to Codex's round-1 findings (the job worker joined inside the lock
  scope, read-only commands that refuse a missing or unstamped store, the backfill's revalidating batch write, the
  deprecated-space remedy, the reorder, the hygiene and container-check fixes) and their tests. No new subject.
- Named target files and measured existing surface (lines, `grep -c ""`): `server.py` 232, `indexer.py` 408,
  `embed_swap.py` 537, `db.py` 1419, `ingest.py` 879, `migrate.py` 326, `web.py` 1320, `vision.py` 2286,
  `embeddings.py` 640, `jobs.py` 273; new `store_lock.py` and `tests/test_store_lock.py`. Estimated change (not
  measured): about 70 lines for `store_lock.py`, 25 in `server.py`, 85 in `indexer.py`, 10 in `jobs.py`, 60 moved
  into `db.delete_chunks_cascade` with about 80 removed from `ingest.py`/`web.py`, about 110 in `embed_swap.py`
  (including the dead branch removed), 30 in `migrate.py`, 5 in `embeddings.py`; tests about 700 to 900 lines; docs
  about 100 lines. The plan is shorter than the change it describes.
- Regeneration: none; the same plan revised in place. Round 1's reviewed bytes are kept outside the repository as the
  delta baseline.
- Required fallback/provenance inputs: none.
- Structural signals inspected: no complete product file, no duplicated validator or generator, no frozen manifest,
  transcript or mutant catalogue, no near-copy successor; planning maintenance does not exceed implementation.
- Calibration: an implementation plan for one PR of eight commits across about eleven source files and their tests,
  at the level of signatures, rules, exact user-facing text and named tests. The growth has a reason beyond growth:
  each added rule closes a reviewer-found defect.
- Disposition: **proceed**.
