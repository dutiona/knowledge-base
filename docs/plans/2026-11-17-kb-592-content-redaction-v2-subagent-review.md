# Clean-slate subagent review (Opus): successor plan v2

Successor of the approved plan `docs/plans/2026-11-17-kb-592-content-redaction.md` (#608). Rounds below are verbatim, each followed by its resolution.

## Round 1 (verbatim)

Altitude report SHA-256: e7f292aa25dd985078764f8ae30bbcd990762943dd1f943e9d71e684cbf29d43

# Successor review: v1 → v2 (plan 7d54c58b, tree a518ab9)

**Verdict: LGTM once M1 is fixed. The email removal is complete and consistent in every place that binds behaviour. One MEDIUM: the shared-fixture header, and the ruling as posted on memory-engine#1024, state ruling 2 as a rule of the redaction engine when it only covers KB's URL identity. Four LOWs. Confidence is high.**

## Digests and altitude (assessed first)
- The successor's SHA-256 is `7d54c58b…884a8a` and matches the altitude header.
- The predecessor's SHA-256 is still `19cdd018…a7565`, and it matches the `ADDRESS-ISSUE-PLAN-APPROVAL … APPROVED` marker on #608.
- The report's measurements are correct: 476 lines, 43,437 bytes, 13 items.
- Re-counted on a518ab9, there are still six `_insert_chunk` call sites: `ingest.py:427` and `:567`, `web.py:516`, `:894` and `:1033`, `vision.py:2047`.
- I accept the disposition, **proceed**. A full successor rather than an amendment is the right form, because ruling 1 changes approved intent. The delta sits where the ruling binds and copies no product code.

## Email removal: checked
- **Detector order and outcome:** consistent. Item 1 names the order "provider-prefix, url-credential, hex-in-context, high-entropy". The Outcome says "without its email detector".
- **Oracle and V1:** the second build removes `detect_email` and adds machine-token first. V1 now has the unpatched build agree on every case without machine-token except the panicking one. That is a sound proof that no case depends on email, because any case containing an email the unpatched build would redact fails that check.
- **Tests and hostile input:**
  - `test_email_is_not_redacted` is right. An address can never meet the entropy rule, since `@` fails the charset check, and no other rule touches it.
  - The `://u:p@` shape replacing `a@b.co ` is linear by its own scan.
- **Folder-summary examples:** both replacements are valid.
  - A file name carrying a machine token matches, because `.md` is not in `[0-9A-Za-z_]`.
  - A 200-character cut that leaves the dotless head of a dotted token works, because `.` fails the entropy charset.
- **Decision 5, docs and model-facing docstring:** all updated. No email mention is left except the rulings and the exclusions.
- **Merged KB#573 and KB#588 (both merged into the tree now):** no carried-over assumption breaks.
  - `_embed_with_config` and `update_folder_summary` now go through `require_active_space`, and the names the plan patches still exist: `knowledge_base.ingest.embed` and `knowledge_base.folder_summaries.embed`.
  - `_check_promotable`, `backfill_space`, `delete_chunk_vecs`, `insert_chunk_vec`, `kb_http` (`conftest.py:410`) and `stub_document_embedder` (`conftest.py:299`) are all present.
  - `cmd_status` still opens with `get_connection` plus `init_schema`.
  - `ingest_url` keeps the shape item 7 relies on:
    - `source_uri=url` (`web.py:1302`) and `source_url=url` (`:1280`);
    - the two figure calls (`:1244`, `:1257`);
    - `base_url=str(fetched.url)` (`:1281`, now from `_fetch`);
    - `_base_result` (`:1288`).
- **Refreshed line references:**
  - `ingest.py:405` is correct: `if session_id is not None:` inside the dedup probe.

## Findings

### [MEDIUM] M1. The fixture header and the ME#1024 comment state ruling 2 as an engine rule
The header line reads "owner rulings 2026-11-17: email is not a secret, URL query strings are not redacted". The ME#1024 ruling comment says "secrets in URL query strings are not redacted". But ruling 2, as the plan itself applies it (decision 4, R4), covers only KB's identity string: a fetched URL's `source_uri`.

The engine does redact query values in content, and the fixture's own outputs will show it:
- `?api_key=<32 hex>` is caught by hex-in-context.
- `?key=AIza…` is caught as a prefix after the `=` boundary.
- A dotless high-entropy `?id=…` at the end of a URL is caught by the entropy rule.

A header that contradicts its own cases is a trap for memory-engine#1024, which treats this file as the contract. It could exempt query strings in its engine and then fail the fixture.

Fix: keep ruling 2 out of the engine header. It is a KB storage rule, not part of the rule set the outputs encode. Then post a one-line clarification on ME#1024.

If Michaël meant the ruling to cover URLs inside content too ("too difficult today to differenciate secrets in URL from legitimate token as ID"), that would be a new engine exemption for both repos, with new fixture cases. Ask him rather than guess.

### [LOW] L1. One more unit-test input carries an email (Shared fixture, Cases)
`empty_denylist_is_backward_compatible` (`redact.rs:1343`) passes `"… and craig@example.com"` to `redact_text`. It is missing from the exclusion list. V1 would catch it, but the list should be complete: drop its address as for the mixed tests. The other `@`-bearing tests are fine to keep, because the URL span already wins there in both builds: `url_credential_basic`, `url_credential_password_with_at_sign` and `url_embedded_github_pat_redacted`.

### [LOW] L2. The header line is unhandled in the tests and the harness
Three consumers need an explicit rule for the header:
- `test_redact_matches_the_shared_case[<name>]` must skip it.
- Item 0's harness maps `{"name","input"}` lines and must pass the header through.
- `test_fixture_is_canonical` must re-encode it.

Also, "the fixture's expected outputs deliberately diverge from memory-engine at 97c8e77 on email input" is inaccurate, since the fixture holds no email input. The divergence is in the rule set, not in any case.

### [LOW] L3. Stale reference and stale constraint
- `ingest.py:388-392` should be `388-389`. Lines 390-392 are code; the comment ends at 389.
- The constraint "In flight on the same files: KB#573 and KB#588 / PR #602 … The second to land rebases" is stale now that both are in the tree.

### [LOW] L4. Ruling provenance and the follow-up homes
- **Ruling 1's source:** the plan says both rulings were given "on #608", but #608's only comment is the approval marker. ME#1024's comment quotes only ruling 1's first two sentences. Per Michaël's rule on keeping quotes verbatim, name where each quote is from (comment URL or session) so it can be checked. The text is plausible; only where it came from is unclear.
- **F2:** it says "commented on memory-engine#1024", but that comment does not mention the quadratic overlap and prefix scans. Add them there, or correct F2.
- **F3, F4 and F6:** these correctly point to open issues #609, #610 and #611.

## Resolution (round 1)

- [MEDIUM] M1 fixture header states ruling 2 as an engine rule → header names only ruling 1; Constraints scope ruling 2 to stored URLs (source_uri and URL-valued metadata); the content-URL question is flagged for Michaël; the ME#1024 comment wording goes to the orchestrator.
- [LOW] L1 `empty_denylist_is_backward_compatible` → excluded with the other mixed tests.
- [LOW] L2 header handling → stated for every consumer; divergence sentence corrected.
- [LOW] L3 → `ingest.py:388-389`; in-flight constraint replaced by the merged state.
- [LOW] L4 → provenance of both rulings stated; F2's unposted quadratic part named.

## Round 2 (verbatim)

Altitude report SHA-256: daa15c01a3bd483f8427c62dd37e8a19197ceb5864cdae613b536fe6970fd427

# Successor round 2: delta 7d54c58b → ffb30b2c

**Verdict: not LGTM yet. M1 and L1-L4 are resolved correctly. The delta's new metadata rule (Codex's HIGH) is applied at the insert but not in the report or the rewrite. That brings back the "flagged forever" defect from round 1's H2 (folder summaries), this time for metadata. The fix is two lines of plan text. Confidence is high.**

## Digests and altitude
- The plan's SHA-256 is `ffb30b2c…c1c3`. It matches the altitude header.
- The report's measurements are correct: 489 lines, 44,793 bytes, 13 items.
- I accept the disposition, **proceed**. Revisions 2 and 3 are in-place changes with no new role.

## Round-1 findings, checked
- **M1: resolved.**
  - The fixture header now names only ruling 1.
  - The Constraints state where ruling 2 applies: stored URLs (`source_uri` and URL-valued metadata) keep their query, and URLs inside document text still pass the rules.
  - The open question, whether Michaël meant URLs in content too, is flagged for his approval.
  - Still open, and owned by the orchestrator: ME#1024 comment 6955524923 still says "secrets in URL query strings are not redacted". Its wording needs correcting before memory-engine#1024 reads it as an engine rule.
- **L1: resolved.** `empty_denylist_is_backward_compatible` is now excluded.
- **L2: resolved.** Every consumer's handling of the header is stated, and the divergence sentence is now accurate: the divergence is in the rule set, not in any case.
- **L3: resolved.** The reference is now `388-389`, and the in-flight constraint is replaced by the merged state.
- **L4: resolved.**
  - Both rulings' provenance is stated. I checked that comment 6955524923 on ME#1024 holds ruling 1's first two sentences verbatim.
  - F2 now names the part not yet posted there.

## New findings

### [HIGH] H1. The report and the rewrite judge metadata by a different rule than the insert
Item 6 stores metadata with `redact_json(json.loads(metadata), _metadata_leaf)`. A whole `http`/`https` URL gets `without_userinfo` only. But the report still flags a row "when `redact_json(metadata)` differs from it" (plan line 140), and `redact_json`'s default leaf is `scrub`. The rewrite's step 1 ("new … metadata") does not say which leaf it uses.

A signed CDN image URL such as `https://cdn.example.test/i.png?sig=<base64url>` is stored with its query, as ruling 2 intends. The entropy rule then fires on it, because `=` and `:` are split characters and the value is a dotless token. The outcome depends on which leaf the rewrite uses:
- If the rewrite uses `scrub`, it redacts exactly what ruling 2 says must be kept.
- If it uses `_metadata_leaf`, it changes nothing, so `redact` exits 1 forever and KB's side of the infra#81 gate cannot pass.

Fix:
- Define `_metadata_leaf` once, and use it in all three places: the insert, the report's metadata check, and the rewrite's planned metadata.
- Correct line 140 to `redact_json(metadata, _metadata_leaf)`.
- Extend `test_report_on_a_clean_database_exits_zero` with a stored `image_url` that carries a token-shaped `sig=` value.

### [LOW] L1. Whole-URL test for `_metadata_leaf`
- Item 6 matches a whole URL with `^https?://\S+$`. With `re.match` (or `re.search` with `^`), `$` also matches before a trailing `\n`, so `"https://x\n"` counts as a whole URL and skips the rules. Use `re.fullmatch(r"https?://\S+", s)`, or `\Z`.
- An uppercase scheme (`HTTP://…`) falls through to `scrub()`. That is a harmless over-redaction, but match the scheme case-insensitively, since `without_userinfo` already treats http/https as the only schemes.

## Resolution (round 2)

- [HIGH] H1 report/rewrite use a different metadata rule → one `redact_metadata` used by insert, report and rewrite (also Codex round 2).
- [LOW] L1 → `re.fullmatch(r"(?i)https?://\S+", s)`; case-insensitive scheme in `without_userinfo`; uppercase-scheme tests.

## Round 3 (verbatim)

Altitude report SHA-256: 3f8baa1e1267cfb5863f98423921bd37d72822023ffa0d5e3e1f97b49d8ddf7c

# Successor round 3: delta ffb30b2c → 2fae22fd

**Verdict: LGTM. H1 and L1 are resolved correctly and the delta adds no new gap. Two optional wording fixes are below. Confidence is high.**

## Digests and altitude
- The plan's SHA-256 is `2fae22fd…76895`. It matches the altitude header.
- The report's measurements are correct: 498 lines, 45,563 bytes, 13 items.
- I accept revision 4 and the disposition, **proceed**. The change is in place and adds no new role.

## Round-2 findings, checked
- **H1: resolved.** There is now one `redact_metadata` in `ingest.py`.
  - `_insert_chunk` stores its output.
  - The report's metadata check at line 140 names it.
  - Item 6 binds the rewrite to it ("the report and the rewrite (items 10-11) call the same function").
  - A metadata URL that keeps its query by ruling 2 can now be neither reported forever nor rewritten against the ruling.
  - Importing it from `redact_store.py` creates no cycle, since `ingest.py` does not import `redact_store`.
  - The test extension covers both cases: a new row whose `sig=` query is kept, and an old row whose userinfo is stripped while its query stays.
- **L1: resolved.**
  - `re.fullmatch(r"(?i)https?://\S+", s)` closes the trailing-`\n` hole.
  - `without_userinfo` now treats the scheme case-insensitively.
  - `HTTPS://` and `Http://` are tested.

## Optional fixes [LOW]
- **Rewrite step 1 (line 156):** it says "new … metadata" without naming the function. Writing "metadata through `redact_metadata`" there would make the binding visible at the point of use, not only from item 6.
- **`test_report_on_a_clean_database_exits_zero`:** it now seeds a row the report flags, so as written it first exits 1. Either say it ends with `redact` exiting 0 after `--rewrite`, or split it from the truly clean case so the name stays accurate.

## Still with the orchestrator (not a plan finding)
ME#1024 comment 6955524923 still says "secrets in URL query strings are not redacted". It needs correcting so memory-engine#1024 does not read ruling 2 as an engine rule.

## Resolution (round 3)

- [LOW] rewrite step 1 naming → step 1 names `scrub`, `redact_metadata`, `without_userinfo`.
- [LOW] clean-report test → split (also Codex round 3 MEDIUM).

## Round 4 (verbatim)

Altitude report SHA-256: 500735001394280dde563eb1e292d01a0ca5e3f16c3ee8680acbf8262e943c00

# Successor round 4: delta 2fae22fd → 5bdab381

**Verdict: LGTM once L1 is fixed. Both optional LOWs from round 3 are applied correctly. The test split introduces one ordering slip. Confidence is high.**

## Digests and altitude
- The plan's SHA-256 is `5bdab381…dbb14`. It matches the refreshed altitude header.
- The report's measurements are correct: 500 lines, 45,747 bytes, 13 items.
- I accept revision 5 and the disposition, **proceed**. The change is in place and adds no new role.

## Round-3 LOWs, checked
- **Rewrite step 1:** resolved. It now names `scrub`, `redact_metadata` and `without_userinfo` where they are used.
- **The clean-database test:** resolved.
  - `test_report_on_a_clean_database_exits_zero` now really is clean: a new row whose query is kept, and the report exits 0.
  - The flagged row moved to its own test, `test_report_and_rewrite_a_metadata_url`.

## Finding

### [LOW] L1. The new test lands in item 10 but needs item 11's `--rewrite`
`test_report_and_rewrite_a_metadata_url` is listed under item 10 (plan line 355). It runs `redact --rewrite`, which item 11 adds (line 363). That has two effects:
- Item 10's Green command, `-k "report or subcommands"` (line 350), selects it by "report" and fails at item 10's commit.
- That contradicts TDD's green-per-commit order.

Fix: move the test to item 11's Red list. Its name already matches item 11's `-k rewrite`. Alternatively, keep only the report half in item 10 and add the rewrite half in item 11.

## Resolution (round 4)

- [LOW] L1 test ordering → report half in item 10 (`test_report_flags_a_metadata_url_with_userinfo`), rewrite half in item 11 (`test_rewrite_strips_metadata_userinfo_and_keeps_query`).

## Round 5 (verbatim)

Altitude report SHA-256: d4e46e9aedfe4d15ec8cf8ca82d6bba76e9f4427e675b6b17909a0d1da694711

# Successor round 5: delta 5bdab381 → 58160382

**Verdict: LGTM. My last finding (L1, the test ordering) is resolved and the delta adds no new gap. No findings this round. Confidence is high.**

## Digests and altitude
- The plan's SHA-256 is `58160382…87732`. It matches the refreshed altitude header.
- The report's measurements are correct: 502 lines, 45,864 bytes, 13 items.
- I accept revision 6 and the disposition, **proceed**. The change is in place and adds no new role.

## L1 (test ordering), checked
- **Item 10** (line 355) now holds only the report half: `test_report_flags_a_metadata_url_with_userinfo`. Item 10's filter `-k "report or subcommands"` selects it, and it needs nothing item 11 adds.
- **Item 11** (line 381) holds the rewrite half: `test_rewrite_strips_metadata_userinfo_and_keeps_query`. Item 11's filter `-k rewrite` selects it.
- Neither filter picks up the other item's tests, so each item can go green at its own commit.
- The new check that the row keeps its vectors matches steps 2 and 3. A row whose only change is metadata keeps its vector in every space and is not re-embedded.

## Still with the orchestrator (not a plan finding)
ME#1024 comment 6955524923 still says "secrets in URL query strings are not redacted". It needs correcting so memory-engine#1024 does not read ruling 2 as a rule of the redaction engine.

## Resolution (round 5)

No changes required — LGTM, no new findings.
