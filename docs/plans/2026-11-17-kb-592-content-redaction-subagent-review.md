Altitude report SHA-256: afdd9300c8d2b5f66e764aec05aa12b73aa1bfe1d4ca0ae15af52ce625597f39

# Review: `2026-11-17-kb-592-content-redaction.md`

**Verdict: not LGTM yet. There are 2 HIGH findings and 5 MEDIUM findings. The architecture is sound and the fixes are small. Confidence is high on everything except M5, which I could not check against the sqlite-vec binary.**

## Provenance and digests (checked)
- The plan's SHA-256 is `c2d49f5b…d39533`. It matches the value the altitude report binds, and its size matches too (375 lines, 32,229 bytes).
- The scratch inputs match the altitude report's hashes: `me-redact-97c8e77.rs` is `945069ac…98c13` and `me-adr-0020.md` is `f977cadf…946f1`.
- The worktree HEAD is 252c044, as the report says.
- [LOW] The altitude report names review copies at `.review/inputs/*`. That directory does not exist in the worktree. Only the scratch copies exist, and their hashes match.

## Altitude and duplication (assessed first)
I agree with the altitude disposition: **proceed**.
- The plan holds the decisions an implementer cannot infer, and restates no Rust detector or fixture body.
- The port-hazard list is justified, because each hazard is pinned by a fixture case.
- I accept two overrides as justified:
  - `auth.RedactingFormatter` stays separate and broader. Over-redacting log lines is the safe side.
  - Not porting the denylist follows from the owner's decision.

Duplication the report missed:
- **[LOW] `without_userinfo` duplicates part of `utils._sanitize_url`** (`src/knowledge_base/utils.py:220`). That function already strips userinfo for log lines (ADR-0018 §4). Keeping two functions is defensible, since `_sanitize_url` also drops the query and fragment, which is wrong for an identity. But the plan should either say why both exist or make `_sanitize_url` use `without_userinfo` for its userinfo step.

## Findings

### [HIGH] H1. The fixture escaping rule fails its own test (Shared fixture, "No literal token")
The rule escapes only the first character of each run of `[0-9A-Za-z_.+/=-]`. But `=`, `/`, `-`, `.` and `+` are all inside that run class, and `=` and `/` are also prefix-boundary characters. So a secret that comes after one of them mid-run stays literal in the committed bytes:
- `KEY=sk-…`, `x/ghp_…` and `token=eyJ….…`: only the run's first character gets escaped, so the prefix stays literal after a boundary character.
- `x-me_<32>` and `.me_<32>`: these are the plan's own "other characters on each side" machine-token cases, and `me_…` survives.
- `api_key=<32 hex>`: after escaping it reads `\u0061pi_key=…`. Scanning back from the hex run gives the word `u0061pi_key`, which splits into `u0061pi` and `key`. So `hex-secret` still fires on the raw line.

Consequences:
- `test_fixture_holds_no_secret` goes red in item 1.
- GitHub push protection can match prefix-anchored shapes like `xoxb-…` that sit mid-run.
- This escaping is a byte-level contract that memory-engine#1024 is meant to copy, so it should be right before the first commit.

Fix: escape every `@`, the first `-` of every `-----`, and the first character of **every maximal ASCII-alphanumeric run**. With that rule every prefix is broken, every context word becomes `u00XX…`, and every entropy token contains `\`, which fails the charset check. Keep the self-check test as the guard.

### [HIGH] H2. The report flags folder summaries forever, so `redact` can never exit 0 (Maintenance command; item 11 step 6; Constraint on infra#81)
The report counts a summary when `scrub(summary) != summary`. Summaries are built at ingest time and never scrubbed. `_build_folder_summary` (`folder_summaries.py:53-73`) builds one from three things:
- The raw `basename(source_uri)`. File paths are deliberately not redacted (decision 4), so a file named `notes@team.md` meets the email rule.
- `content[:200]`. Cutting `a@b.com.` (the known gap) to `a@b.com` creates an email. Cutting a low-entropy token can raise its entropy over the threshold.
- `.replace("\n", " ")`. This joins `api_key:\n<32 hex>`, which the hex rule misses because it works line by line, into `api_key: <hex>`, which it catches.

The rewrite rebuilds these summaries the same way, so they stay flagged. Then "a second `redact` exits 0" fails, and KB's side of the infra#81 gate cannot pass.

Fix: return `scrub(...)` from `_build_folder_summary`. That is one line and covers the ingest path too. Add a test where a basename holds an email and a key and its hex sit on two lines.

### [MEDIUM] M1. A failure after the commit leaves the database dirty while `redact` says clean (items 3 and 11, steps 3 to 6)
Two failures can strand the run after step 3 has committed:
- `purge_deleted_content` refuses when the checkpoint is busy. By then the rewritten rows are committed, the old bytes are still in free pages, FTS segments and the WAL, and `redact` exits 0.
- `update_folder_summary` embeds in step 4, after step 3 has already deleted the summaries. If the embedder fails there, a rerun finds nothing to rebuild.

Fix:
- Run the busy check (`wal_checkpoint(TRUNCATE)` reporting busy 0) before step 3, so a refusal changes nothing.
- Embed the rebuilt summaries in step 2, alongside the chunks.
- Make every `--rewrite` run always purge and always rebuild missing summaries.
- State what `test_rewrite_refuses_while_another_connection_reads` asserts: nothing changed.

### [MEDIUM] M2. The linear-time test misses two quadratic shapes in the reference algorithm (item 1)
`ingest_text` accepts 4 MiB over HTTP, so either shape below is a denial of service on a server worker. The current test inputs (`=eyJ`, `a@b.co `, `token=<hex> `) hit neither.
- **`-----BEGIN` repeated on one line.** Each match scans to the end of the line (`redact.rs:310-318`).
- **32-hex runs inside one long word, such as `_<32hex>` repeated.** For each hex run, `has_hex_context_key` slices `line[..start]` and walks back over the whole word (`redact.rs:470-484`).

Add both shapes to `test_redaction_is_linear_on_hostile_input`.

### [MEDIUM] M3. Userinfo survives in web figure `image_url` metadata (item 7)
`_extract_html_images` builds `image_url` with `urljoin(base_url, src)`. Here `base_url = str(response.url)` or the browser's `final_url` (`web.py:299, 1138, 1172, 1217`), and that URL is stored in metadata (`web.py:469`).

I think httpx keeps userinfo in `response.url`, but I did not verify it, because no venv was available. If it does, every relative image keeps `user:pw@`. Item 6 then redacts only the password; the username, or a bare-token userinfo, stays.

Fix: apply `without_userinfo` to both base URLs, and assert on `image_url` in `test_ingest_url_stores_the_url_without_userinfo`.

### [MEDIUM] M4. V2's tolerated class is defined wrongly (Verification V2; item 1 port rules)
V2 tolerates differences from Other_Alphabetic characters on the grounds that they "never [change] whether an ASCII token is caught". That is false, and it would hide real port bugs:
- `a@b.com` followed by U+093E: Rust extends the domain over the mark and the TLD check fails, so it does not redact. Python stops before the mark, so it does.
- `sk-` plus 15 ASCII characters plus U+093E: Rust reaches the 20-byte minimum, so it redacts. Python does not.

Fix: classify differences by direction. Python redacting more than memory-engine is tolerated. Python redacting less is always a bug. Pin both examples as fixture cases.

Related [LOW]: Rust sums `shannon_entropy` over a `HashMap`, so the summation order varies. Treat a difference within about 1e-9 of 4.3 as noise, not a port bug.

### [MEDIUM] M5. VACUUM and sqlite-vec rowids (item 3), likely but unverified
I believe sqlite-vec declares `_vector_chunksNN(rowid PRIMARY KEY, …)`. That column is not an INTEGER PRIMARY KEY alias, so VACUUM may renumber the real rowids that `sqlite3_blob_open` reads. The item 3 test uses a small database with no deleted rows, so it would pass whether or not this happens.

Fix:
- Create gaps first: delete rows in several spaces, and in `folder_summaries_vec`.
- After VACUUM, run a KNN query for every remaining vector.
- Include `folder_summaries_vec` in the branch that rebuilds the vec0 tables.

### [LOW]
- **Decision 2's reasoning.** "No rule matches across whitespace" is wrong: `-----BEGIN` spans spaces to the end of the line. The true invariant is that no span or context check crosses a `\n`. The page-by-page equivalence still holds.
- **Item 9 tripwire.** `INSERT INTO chunks\b` matches `_insert_chunk`'s own f-string (`ingest.py:200`), so the tripwire must exempt it.
- **Decision 3's raise.** Name the exception, for example a `KnowledgeBaseError` that becomes a `ToolError` with "nothing stored". Note also that the fixpoint always terminates: each changing pass strictly reduces the characters outside placeholders, apart from one relabel per placeholder. So the bound of 8 is a choice, not a safety need.
- **Item 5.** Update the stale comment at `ingest.py:399-403`, which says reingest passes `deduplicate=False`.
  - The deferred links attach only `session_id`. A shared chunk that gets deduplicated does not receive the old chunks' historical sessions.
- **Merging loses identity.** A document whose every chunk is merged or deduplicated has no rows left under its `source_uri`, so `reingest` then raises NotFoundError (`ingest.py:661-663`). R3's remedy ("re-ingesting removes it") fails in that case. Document it.
- **Decision 4 vs item 6.** Item 6 also redacts identity-like metadata (`source_url`, `image_url`, `source_image`, `images`). For `images` this is consistent, since `vision.py:1331` reads it and it comes from scrubbed content. But the decision's wording should mention it.
- **Item 11 step 3.** Only rows whose content changed need re-embedding. Rows where only metadata or `source_uri` changed do not.
- **Item 7 test.** `_base_result` returns both `url` and `source_uri` (`web.py:1225-1226`). Say which one must be stripped.
- **Merge gate.** The copied command list leaves out AGENTS.md's tesseract precondition (`AGENTS.md:18`). Without it the OCR tests skip silently.
- **Scope of the infra#81 gate.** `redact` exiting 0 covers chunks only. `prediction_errors` queries and the stdio-written stores are deferred to F4, so the gate's wording should say "chunks".

## Structural completeness
The Documentation, Testing and Verification sections are all present. The HTTP end-to-end test is marked N/A with a reason. That reason is acceptable, though a `kb_http` fixture already exists (`tests/test_lean_surface.py:85`), so a single HTTP leak test would be cheap.

## Feasibility
The order works:
- Item 0 (the harness) feeds item 1.
- Item 3 proves the purge before item 11 depends on it.
- Item 5 comes after item 4, which is what makes shared chunks common.

H1 has to be fixed before item 1 commits the fixture. H2 is a one-line change best done in item 11, or as its own boy-scout commit.

## Checked and correct
- Six `_insert_chunk` callers.
- `content_hash` is UNIQUE.
- `chunks_fts` uses external content with the `chunks_au` trigger.
- The FK and UNIQUE set behind `_merge_into`.
- The Rust semantics the port rules claim: White_Space vs `isspace` on U+001C..U+001F; byte length vs character length; ASCII-only lowercase; `split('\n')`; the URL-scan panic on a multibyte terminator.
- `TOKEN_SHAPE` in `auth.py`.
- The issue's acceptance criteria are covered.

## Resolution

Round 1 (plan c2d49f5b, altitude afdd9300), addressed in plan adc3e87e:

- [HIGH] H1 fixture escaping → rule replaced: every `@` and the first character of every maximal ASCII-alphanumeric run is a lowercase `\u00xx` escape; `test_fixture_is_canonical` added beside `test_fixture_holds_no_secret`.
- [HIGH] H2 folder summaries flagged forever → `_build_folder_summary` returns `scrub(...)`; `test_folder_summary_is_a_fixpoint` covers the file-name email, the two-line hex context and the cut sentence-final email.
- [MEDIUM] M1 failure after commit → busy check moved before any write (step 0); the `redact_rewrite_pending` row makes an unfinished rewrite visible to the report (exit 1) and a later `--rewrite` completes steps 4-5; summaries of every file folder are rebuilt (hash-skipped), so a failed embed is healed on rerun; interruption tests per stage. Embedding summaries in step 2 was not adopted: a summary is built from the rewritten rows, and the pending row already makes the stage recoverable.
- [MEDIUM] M2 quadratic shapes → `-----BEGIN ` on one line and `_<32 hex>` in one word added to the linear-time test.
- [MEDIUM] M3 userinfo in `image_url` → `_extract_html_images` stores `without_userinfo(image_url)`; asserted in the item 7 test.
- [MEDIUM] M4 V2 tolerance → moot by decision 7: the port now uses `regex` `\p{Alphabetic}` / `\p{White_Space}`, so no Other_Alphabetic class is tolerated; both examples are fixture cases; entropy within 1e-9 of 4.3 set aside as memory-engine noise.
- [MEDIUM] M5 VACUUM and vec0 rowids → purge test creates gaps in every vec table and `folder_summaries_vec` and KNN-checks every remaining vector; the rebuild branch includes `folder_summaries_vec`.
- [LOW] decision 2 wording → corrected (no span or context check crosses `\n`).
- [LOW] tripwire self-match → `_insert_chunk`'s own statement exempted.
- [LOW] decision 3 raise → `KnowledgeBaseError`, termination argument stated.
- [LOW] item 5 stale comment and historical sessions → comment update named; all sessions linked (also Codex M).
- [LOW] identity lost after full merge → R3 documents the NotFound and the remedy.
- [LOW] decision 4 vs metadata URLs → decision 4 now says metadata URL values get the full rules.
- [LOW] re-embed only content changes → step 2 says so.
- [LOW] `url` vs `source_uri` in the result → `source_uri` stripped, `url` echoes the input.
- [LOW] tesseract precondition → added to the merge-gate block.
- [LOW] infra#81 wording → "for chunks and folder summaries; the other stores are F4".
- [LOW] `without_userinfo` vs `_sanitize_url` → `without_userinfo` lives in `utils.py` and `_sanitize_url` calls it.
- [LOW] HTTP e2e cheap → one `kb_http` leak test added to item 4.
- [LOW] `.review/inputs` absent in the worktree → by design: they are copies in the disposable review tree; the altitude report names the private sources and digests.

---

# Round 2 (verbatim)

Altitude report SHA-256: b0ed09eb3afca86dd7ba0647732a806cd63fd66bd7339d8f895591d5a5c25d6a

# Round 2 review: delta to plan 42acdbeb

**Verdict: LGTM once N1 is recorded as a residual and a follow-up issue. Every round-1 finding is resolved correctly against the code. The delta adds one MEDIUM gap, which comes from older code but this change makes it more likely, and five small LOW fixes. Confidence is high.**

## Digests and altitude
- The plan's SHA-256 is `42acdbeb…68c84b`. That is the value the refreshed altitude report binds, and the value in the brief.
- The report's own SHA-256 is `b0ed09eb…25d6a`. Its measurements are correct: 442 lines, 39,635 bytes, 13 items.
- I accept the disposition, **proceed**. The +61 and +6 lines are fixes placed in the items they correct, and no new role or copied code was added.
- I accept the resolution on `.review/inputs`: those are disposable review copies, and the digests are bound to the private sources.
- Trivial: the report still says "six write sites". With folder summaries there are now seven text sites that reach the embedder.

## My round-1 findings, checked against the code
- **H1 (fixture escaping): resolved.** I checked the new rule (escape every `@` and the first character of every ASCII-alphanumeric run) against each detector:
  - Every prefix is broken, including one after `=`, `/` or `-`.
  - `-----BEGIN` becomes `-----\u0042EGIN`.
  - A context word becomes something like `\u006bey`; scanning back stops at the `\` and yields `u006bey`, so no key part matches.
  - A hex run is now preceded by the word `u`, which is not a key.
  - `me_` loses its `m`.
  - Email and URL credentials lose their `@`.
  - Every entropy token gains a `\`, which fails the charset check. A token made only of `+/=_-` cannot reach 4.3 bits (the maximum is log2 5).
  - `test_fixture_is_canonical` pins the bytes.
- **H2 (folder summaries flagged forever): resolved.** One subtlety I checked: `update_folder_summary` skips a folder whose hash is unchanged (`folder_summaries.py:99-104`). That would keep an unscrubbed summary written before the upgrade. Step 3 handles it by deleting every summary that holds a match, so step 4 rebuilds it.
- **M1 (failure after commit): resolved.** The `redact_rewrite_pending` row makes every stage after the commit visible to the report and recoverable. I accept the reason for not embedding summaries in step 2.
- **M2 (quadratic shapes): resolved.**
- **M3 (userinfo in `image_url`): resolved.** `image_url` is the only stored URL built from the response's base URL (`web.py:469`).
- **M4 (V2 tolerance): resolved by decision 7.** `[\p{Alphabetic}\p{Nd}\p{Nl}\p{No}]` is exactly Rust's `is_alphabetic() || is_numeric()`, and `\p{White_Space}` is exactly `is_whitespace`.
- **M5 (VACUUM and vec0 rowids): resolved.** The test now creates gaps in every vec table, including `folder_summaries_vec`, and KNN-checks every remaining vector. The rebuild branch covers `folder_summaries_vec`.
- **All LOWs: resolved as stated.** `_sanitize_url` now calls `without_userinfo`, which removes the duplication.

## New findings in the delta

### [MEDIUM] N1. Redaction and merges make a shared chunk more likely to vanish when its owner is reingested
This is new evidence, not a re-raise. Codex's round-1 point was about lost links, not about deletion.

Today, a document whose chunk was deduplicated has no row of its own. The row belongs to the first document's `source_uri`. When that owner document B is reingested:
- `reingest_file` deletes every row under B's `source_uri` (`ingest.py:657-724`), including the shared one.
- If B's edit removed or changed that chunk, document A's text is no longer searchable.
- After item 5, A's paper abstract may point at the shared chunk. Reingesting B then nulls that link and re-links it to B's new first chunk (`ingest.py:745-758`), which is a different document.

This mechanism already exists for dedup at ingest time. The plan makes it much more likely in three ways:
- Decision 5: "documents that differ only by a secret now share chunks".
- `_merge_into` hands a duplicate's text to another document's `source_uri`.
- Item 5 deliberately links reingested documents to other documents' chunks.

The ideal fix is a chunk-to-source membership table, so that a reingest deletes only chunks no other source still holds. That is a schema change and needs its own PR.

Recommendation: add it as residual R8 and file the follow-up issue. Whether it blocks the infra#81 rollout is Michaël's call.

### [LOW] N2. "Nothing else is escaped" conflicts with JSON's mandatory escapes (Shared fixture)
The fixture cases themselves need escapes the sentence rules out:
- U+001C..U+001F
- `\n` and `\r` (the CRLF case)
- `"` and `\`

A raw LF would break JSON Lines. Because `test_fixture_is_canonical` and memory-engine's regenerated file must match byte for byte, the contract should name the forms:
- short escapes `\" \\ \b \f \n \r \t`
- other C0 controls as lowercase `\u00xx`
- non-ASCII left as raw UTF-8

That is what both `serde_json` and Python's `json.dumps(..., ensure_ascii=False)` produce.

### [LOW] N3. Item 1 contradicts decision 7
The plan's line 172 still reads "a pure module (standard library only, no KB import)". It should say "standard library and `regex`, no KB import".

### [LOW] N4. Wrong fixture location in item 4
`kb_http` is defined in `tests/conftest.py:402`, not in `tests/test_lean_surface.py`.

### [LOW] N5. `test_rewrite_refuses_while_another_connection_reads` asserts too strictly
The test asserts the database bytes are unchanged. A `wal_checkpoint(TRUNCATE)` that ends busy can still copy WAL frames into the main file before it waits on the reader, so the file bytes can change while nothing logical does. `open_declared_db` may also rebind config rows; r3 already acknowledges that.

Fix: assert that `iterdump()` (or the rows) are unchanged. Otherwise an implementer could make the test pass by dropping step 0.

### [LOW] N6. The `regex>=2024.11.6` floor has no stated reason
The locked version is 2026.2.28 (`uv.lock:1947-1948`). `regex`'s Unicode version is now part of conformance (the V2 residual). The floor should be the oldest release whose Unicode version was actually checked against Rust's, with that reason written next to the pin, as Michaël's "pin only for a written reason" rule asks.

## Structure and feasibility
- The Documentation, Testing and Verification sections are still present.
- The tesseract precondition has been added to the merge gate.
- The order of items still holds. The folder-summary commit in item 8 comes before items 10 and 11, which depend on it.
- r3's change to item 5 updates both callers of `_produce_and_insert_chunks` (`ingest.py:476` and `ingest.py:734`). Those are the only two.

## Resolution (round 2)

- [MEDIUM] N1 shared chunk deleted on owner reingest → residual R8 and follow-up F6 (chunk-to-source membership table, its own PR); timing against infra#81 left to Michaël.
- [LOW] N2 JSON escapes → named forms and compact separators in the fixture section.
- [LOW] N3 item 1 wording → "standard library and `regex`".
- [LOW] N4 `kb_http` location → `tests/conftest.py`.
- [LOW] N5 busy-refusal assertion → rows unchanged by `iterdump()`, file bytes may move.
- [LOW] N6 `regex` floor → `regex>=2026.2.28`, the release V2 compares with, reason next to the pin.
- trivial "six write sites" → "seven text sites" in the altitude report.

---

# Round 3 (verbatim)

Altitude report SHA-256: 9cd3ca320eb812f9827e175f0521cd7839d357522edbadefa753ed1669e3e1b4

# Round 3 review: delta to plan 19cdd018

**Verdict: LGTM.** All of N1 to N6 are resolved correctly, the "seven text sites" wording is fixed, and the delta adds no new gap. Confidence is high.

## Digests and altitude
- The plan's SHA-256 is `19cdd018…a7565`. It matches the brief and the refreshed altitude report's header.
- The altitude report's SHA-256 is `9cd3ca32…e1b4`.
- Its measurements are correct: 452 lines, 40,723 bytes, 13 items.
- I accept the disposition, **proceed**. Revision 4 adds 9 lines, each in the item it corrects, with no new role and no copied code.

## Round-2 findings, checked
- **N1: resolved.** R8 states how shared chunks get lost, including the abstract being re-linked to another document. It says the problem predates this ticket and names the three things in this plan that make it more likely. F6 names the fix (a chunk-to-source membership table, for its own PR) and leaves the timing against infra#81 to Michaël.
- **N2: resolved.**
  - The named forms match both serializers: `\" \\ \b \f \n \r \t`, and other control characters as lowercase `\u00xx`. `serde_json` takes its hex digits from `0123456789abcdef`, and Python formats them with `\\u{0:04x}`. Neither escapes `/`, U+007F or U+2028 when `ensure_ascii=False`.
  - "Compact separators" is the detail that matters most. `serde_json::to_string` is compact by default, but Python's `json.dumps` writes `", "` and `": "` unless it is passed `separators=(",", ":")`. The canonical encoder has to do that.
- **N3: resolved.** Item 1 now says "standard library and `regex`".
- **N4: resolved.** `kb_http` is now located in `tests/conftest.py`.
- **N5: resolved.** The assertion now compares rows with `iterdump()`, scoped to chunk, vector and folder-summary rows. That is the right scope: the vector rows live in the vec0 shadow tables, which are ordinary tables, so `iterdump` covers them. It also correctly leaves out `config`, which `open_declared_db` may rebind.
- **N6: resolved.** The floor is now the locked release (`uv.lock:1948`, 2026.2.28), and the plan says the reason sits next to the pin. A later `regex` release with newer Unicode tables stays under the V2 residual, which the plan already states.

## New findings
None.

## Resolution (round 3)

No changes required — LGTM, no new findings.
