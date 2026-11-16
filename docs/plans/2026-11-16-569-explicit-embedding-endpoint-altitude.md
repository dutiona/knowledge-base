Plan SHA-256: 12997b016ebded1a3eac4956fb6028af1a0687494543097f776c1a5db7c14528

# Altitude report: 2026-11-16-569-explicit-embedding-endpoint (candidate 8)

- Plan path: `docs/plans/2026-11-16-569-explicit-embedding-endpoint.md`. Predecessors (retained under
  `~/.local/state/super-plan/2026-11-16-569-explicit-embedding-endpoint/evidence-r1/`): candidate 1
  `27d24ea58d51f1378b9d9b9b8f349d7eff9dba481167a1bfea1f39c325eb17e8` (`plan-r1.md`), candidate 2
  `df27ab6e8226292c39afdea1b5ef0648c588ee48bf901a65633b9bef629edbbf` (`plan-r2.md`), candidate 3
  `48cdb7b4133ac7378c7156289ebd80ac5e28e5f89f382e275c48558e69972232` (`plan-r3.md`), candidate 4
  `1190f940271f1eac24ec871f5f5712435e1248e98264f15106310093bbb10359` (`plan-r4.md`), candidate 5
  `ad27adcb1383950e4df4f6204a9d58c6bb44eeaa1b8f6c6254c8d5d78135513a` (`plan-r5.md`), candidate 6
  `ab4b9674b5678696f89108410608f1046619c2bf53e62b66a04315b3e6e4e3ce` (`plan-r6.md`), candidate 7
  `994c30bfe98f9bc077714a149bc840923ec0b3b443989f5b770410a6bc7e68ad` (`plan-r7.md`). The same plan revised in place
  after review; nothing is approved yet.
- Size: 551 physical lines, 52502 bytes (`wc -lc`); candidate 7 was 546 / 51923, candidate 6 539 / 51228, candidate 5 533 / 50454, candidate 4 520 / 49044, candidate 3 506 / 47692, candidate 2
  471 / 43585, candidate 1 375 / 34066. Top-level
  implementation tasks: 20 (`grep -c '^- \[ \] \*\*'`, items 0–19; 0 and 19 operational, 16–18 documentation).
- Literal volume: one fenced block (Verification, lines 540–551), 10 lines / 949 bytes of commands. No complete product
  file, validator, generated payload, manifest or mutant catalogue is inlined.
- Growth, bounded delta: round 1 found two real gaps, both added as work rather than prose. (1) Tool-created or
  deprecated spaces could rewrite the identity at runtime or embed with another family's wire format (Codex BLOCKER,
  subagent HIGH-1): D3 gains one provider-config builder and a promote guard (item 7 widened, new item 14). (2) The
  re-embed and endpoint-move procedures were unworkable under the gate (Codex HIGH, subagent HIGH-2): D3 gains two
  maintenance-command transitions (item 14). The rest is precision: the URL normalization rule, `onnx`'s explicit model
  file, the derived key row, configure_* refusals before writing, the `env:KNOWLEDGE_BASE_*` LLM key refusal, per-commit
  full-suite greens, labelled guards, observed reds, and two owner calls ((d) widened, (e) new). The Outcome paragraph
  shrank to a pointer at D1–D6 to remove the duplicate restatement. Round 2 (+35 lines): the active-space check in the
  bind, the re-embed reuse rule (resume and rollback), the transition URL-row rule, the LLM key allowlist (also on
  stored rows), owner calls (f) comparison spaces and (g) a pre-existing promote ordering left as collateral, the
  `onnx` slug rationale, full-suite per-commit greens, and test precision. Round 3 (+14 lines) removed more than it
  added in substance: the re-embed reuse/resume/rollback rule is dropped (it needed matching rules over every space
  parameter), `promote_space` becomes all-or-nothing, owner call (g) now states the rollback cost, and the transition's
  checks run before any write. Round 4 (+13 lines): the explicit transaction for `promote_space` and its in-transaction
  failure test, a failed `re-embed` removing its own space, and `rebind` repairing drifted `config` identity rows.
  Round 5 (+6 lines): the whole promotion, summary embed included, under one write lock; `BaseException` cleanup that
  keeps the original error; the `isolation_level` guard; the killed-run residual documented. Round 6 (+7 lines): the
  optimistic promote (embed outside the lock, recheck and re-embed changed rows inside it), the pending-transaction
  refusal, `BaseException` rollback with a `finally` restore, and the lifecycle-race rationale. Round 7 (+5 lines,
  all three reviewers LGTM on candidate 7, only LOWs applied): the summary diff rule for new and vanished folders, the
  unenforced unit-stopped dependency, and the redundant route-level `similar` delete.
- Named target surface (measured at 8fa69bd, `wc -l`): `db.py` 1208, `embed_swap.py` 700, `embeddings.py` 427,
  `utils.py` 385, `vision.py` 2240, `web.py` 1267, `llm.py` 405, `server.py` 41, `indexer.py` 375, plus `ingest.py`,
  `search.py`, `folder_summaries.py`, `routes/embeddings.py` call sites; new `settings.py`. Estimate (uncertain, not
  measured): roughly 250–350 changed or added source lines, roughly 150 deleted, and a larger test delta.
- Required inputs for reviewers:
  - Issue body: source `docs/plans/2026-11-16-569-explicit-embedding-endpoint-drafts/issue-569.md`, review copy
    `.review/issue-569.md`, SHA-256 `253161c07fc16786566fe45d5a4c377d3406d9c321a50e0071e8d100d42ada76` (unchanged).
  - No fallback or provenance inputs otherwise.
- Calibration: still a work assignment (each item: files, red with its observed failure, rule, green, commit), with the
  decisions stated once in D1–D7. Growth is explained by new necessary work, not by inlined product; the plan remains
  shorter than the code and tests it assigns.
- Disposition: **proceed**.
