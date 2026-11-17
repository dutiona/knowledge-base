Plan SHA-256: 581603829a68b79a47867b0066e7b8cd58c287439fdffd7e176b20a91c787732

# Altitude report: 2026-11-17-kb-592-content-redaction-v2 (successor)

## Regeneration provenance

- Predecessor: `docs/plans/2026-11-17-kb-592-content-redaction.md`, SHA-256
  `19cdd0181904645659f3249d688e660ba59ee65313d4b1d3dd64e9db02da7565`, approved by Michaël's marker on
  dutiona/knowledge-base#608 (comment 6955369443). Its bytes are unchanged.
- Cause: Michaël's ruling 1 (no email rule, no email fixture case) changes approved intent, so a complete successor is
  required (not an amendment). Ruling 2 confirms existing intent (URL query strings not redacted).
- Bounded delta (`diff -u` predecessor successor): 69 changed lines (added plus removed, excluding headers), all in
  place: a provenance preamble; the two rulings quoted in Constraints; the Outcome's rule-set sentence; decisions 4 and
  5; the fixture's header line, case exclusions, removed email cases and the oracle build; item 0's second build; item
  1's detector list, one new test (`test_email_is_not_redacted`), one hostile-input shape replaced, its commit subject;
  the folder-summary test's examples; the model-facing docstring; V1; follow-ups F1-F6 now cite their filed homes
  (memory-engine#1024 comments, #609, #610, #611); two line references refreshed after KB#588 and KB#573 merged.
- Retained unchanged: every other item, the maintenance command, the purge, the merge, the residuals.

## Measurements

- Successor: 502 physical lines, 45,864 bytes (`wc -l -c`); 13 top-level implementation tasks
  (`grep -c '^- \[ \] \*\*'`), none inside a fence.
- Fenced literal volume: one fence (Verification), 8 body lines, 293 bytes: the AGENTS.md merge-gate command list.
- Target files and estimates: as in the predecessor's report (existing surface measured there; KB#573/#588 changed
  `ingest.py`, `web.py` and `tests/conftest.py`, and the six `_insert_chunk` call sites are unchanged, re-counted on
  a518ab9). Removing the email detector shrinks the port slightly.

## Required inputs (provenance)

- memory-engine `crates/memory-engine/src/bootstrap/redact.rs` at 97c8e77, private scratch copy; review copy
  `.review/inputs/me-redact-97c8e77.rs`; SHA-256 `945069ac543bf97d92cefa0cac7374ac69ac0ec0c420c62e3f6cd425fab98c13`.
- memory-engine `docs/design/adr/0020-harness-neutral-lifecycle-api.md` at 97c8e77, private scratch copy; review copy
  `.review/inputs/me-adr-0020.md`; SHA-256 `f977cadfe414b4ff8b1436d88d3c3dfea2a821f05eb94cc8b7f4813cc8f946f1`.
- The predecessor plan, review copy `.review/last-reviewed-plan.md`, SHA-256 as above.

## Calibration and disposition

The successor keeps the predecessor's role (an implementation plan assigning one cross-cutting security change); the
delta is the ruling's consequences placed where they bind, not a second copy of the product. No validator, generator or
fixture body was added to the plan.

Successor revision 2 (after Codex's successor round 1): ruling 2 applied to URL-valued metadata as well as
`source_uri` (they keep their query and lose only their userinfo), `without_userinfo` introduced in item 6 and reused by
item 7, the fixture header limited to ruling 1, one new metadata test. Still in place, no new role.

Successor revision 3 (after the subagent's successor round): provenance of both rulings, the stale in-flight
constraint, one more email-bearing test excluded, the header's handling by every consumer, one line reference, F2's
unposted part. In place.

Successor revision 4 (after Codex's successor round 2): one `redact_metadata` shared by insert, report and rewrite;
case-insensitive URL schemes matched with `re.fullmatch`; two test extensions. In place.

Successor revision 5 (after round 3): the clean-report test split from a report-and-rewrite metadata-URL test;
the rewrite's step 1 names its three functions; revision 6 puts that test's report half in item 10 and its
rewrite half in item 11. In place.

Disposition: **proceed**. No fallback needed.
