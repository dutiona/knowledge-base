Altitude report SHA-256: aca6edb96f6be734f2e2323385bf2c096f372a0b5ecb73f6f89dbadf8e8660c9

# Review: #569 plan (explicit embedding endpoint)

Reviewer: clean-slate general-purpose subagent (Claude), round 1, plan SHA-256 27d24ea58d51f1378b9d9b9b8f349d7eff9dba481167a1bfea1f39c325eb17e8.

**Verdict: revise before approval. There are no blockers, two HIGH findings and five MEDIUM findings.** I'm highly confident in the source evidence, which I checked at 8fa69bd. I ran no tests.

## Altitude and duplication (assessed first)

- I agree with the altitude report's "proceed". The report's own hash line matches the plan (`27d24ea5…`), and the issue copy matches its stated hash (`253161c0…`).
- The plan is written as a work assignment: each item gives files, a red test, a rule and a commit. It does not inline any product code.
- Duplication is limited and mostly justified:
  - The Constraints section copies the issue verbatim, which the working rules require.
  - The Testing section's "Acceptance to proof" map is useful traceability.
  - The Outcome paragraph restates D1–D6 in prose. That is tolerable, but it is a second copy that can drift from the Decisions section.
- **Overrides I accept as justified in form:**
  - Owner call (a), the LLM switch, is a declared scope addition with its consequence stated.
  - Owner call (b) departs from the issue's AC "Setting the endpoint therefore never needs an admin write", and the plan says so openly. I challenge the choice itself under HIGH-2, but surfacing it was correct.

## What I checked and found correct

- **Embedding calls:** every production call goes through `_provider_cfg` (`ingest.py:114-144`, `search.py:326-347`, `folder_summaries.py:131-142`, `embed_swap.py:371,531`). The bare-name path has no production caller.
- **Endpoint writer:** `configure_embeddings` is the only runtime writer of `embed_base_url` and `embed_api_key` (`embed_swap.py:236-250`). Item 8 therefore really leaves the gate's endpoint rows as the only source.
- **Vision:** all three `web.py` vision paths catch the `_get_vision_config` raise before any image download (`web.py:364-367, 765-768, 907-910`).
- **LLM:** every extraction path reads its config through `_get_llm_config` (`extraction.py:879`, `llm.py:167`).
- **Item 3:** `DEFAULT_EMBED_*` appears in exactly 14 test files, and `tests/` is a package.
- **Item 17:** there are 48 `@mcp.tool` decorators while the README says 46.
- **Item 2:** `embed_provider_config` has no callers.
- **Item 1:** `cmd_status` really does print `api_key` (`indexer.py:162-180`), so the red is real.

## Findings

### [HIGH-1] Tools the model can call still rewrite the embedding identity, and the new gate turns that into a unit that stays down

- `re_embed_tool`, `promote_embed_space_tool` and create/backfill stay on the model-facing MCP surface (`routes/embeddings.py:75-196`).
- `promote_space` rewrites `embed_model`, `embed_dim` and `embed_provider` (`embed_swap.py:500-524`).
- Under D2/D3 the next start then exits 78. With `RestartPreventExitStatus=78` the unit stays down until an admin write.
- Promoting a deprecated space needs no embedding at all, so a single prompt-injected `promote_embed_space_tool(name="&lt;old&gt;")` is enough.
- Until that restart, the running process embeds with a model and dimension the environment did not declare. D3's claim that "the gate's equality holds for the process lifetime" is true only for the endpoint rows.
- This also contradicts Michaël's settled rule: "Admin writes run as a maintenance CLI with the unit stopped; no admin endpoint."
- **Fix:** refuse the identity-rewriting tools (`re_embed_tool`, `promote_embed_space_tool`) when the process started through `server.main`'s gate, and keep `knowledge-base-ingest re-embed` as the only path. Alternatively, add an owner call (e) that states this consequence and assigns it to KB-5. Either way, add a test.

### [HIGH-2] Owner call (b) leaves out the precedent that a base URL is not part of the identity, and it breaks the epic's own migration path

- **Precedent:** the existing identity guard says "A base_url change within the same family is allowed" (`embed_swap.py:124-131`, `assert_embed_identity_match`). ADR-0018 and ADR-0015 define identity as provider and model (and dimension), not host.
- **No remedy except raw SQL:** item (b) adds no `rebind` command, so the documented fix is raw SQL against the live database.
- **The migration this epic targets cannot be done as documented:** moving the corpus from local Ollama with `bge-m3` to the tailnet `openai_compat` embedder with another model and dimension needs these steps:
  1. A SQL update of `embed_base_url`.
  2. An interim declaration with the old provider, old model and new URL. Its URL must also survive the per-family normalization differences in D1.
  3. `re-embed --provider openai_compat …`.
  4. The final declaration.

  The documented procedure ("re-embed, update the identity variables, start") does not cover this.
- **Recommended option (A):** provider, model and dimension refuse on disagreement. The endpoint rows follow the declaration at start, with a stderr line showing old → new through `_sanitize_url`.
  - Consequence: no admin write ever, as the AC says, and the re-embed procedure stays three steps.
  - Cost: a mistyped URL on an existing corpus is not refused at start; it fails loudly at the first embed call instead.
- **Plan's option (B):** keep the refusal and the SQL remedy.
- **Open question for Michaël:** does the infra#37 deployment carry the existing corpus or start from a blank database? If it starts blank, this drops to MEDIUM.

### [MEDIUM-1] D6 breaks `configure_vision` in a way the plan does not specify

- `configure_vision` commits its rows and then `return _get_vision_config(conn)` (`vision.py:126-146`). The query mode with no arguments takes the same path.
- With D6, the tool writes and then raises. `configure_vision_tool` has no `try/except` (`routes/extraction.py:268-280`), so the model sees a raw tool error after a write.
- `configure_browser` similarly returns `{"browser": None}` right after storing a configuration (`web.py:648`).
- The Documentation section claims both tools "take effect only with their switch on", which is not what would happen.
- **Fix:** either refuse before writing, like D6 does for `configure_llm`, or return the stored rows without going through the gated getter. Add the case to item 10's tests.

### [MEDIUM-2] With the LLM switch on, the key exfiltration that D6 describes is still open

- `_resolve_api_key` resolves any `env:NAME` (`utils.py:125-139`).
- `configure_llm_tool` accepts `api_key` and a public `base_url` from the model, then probes that URL with the resolved key as a Bearer header (`llm.py:331-404`).
- With `KNOWLEDGE_BASE_LLM=on`, which the plan documents as the way to keep structured extraction, `env:KNOWLEDGE_BASE_EMBED_API_KEY`, or any other environment secret, goes to a host the attacker chooses.
- D6 names this vector but closes it only while the switch is off.
- **Recommendation:** refuse an `env:` spec naming any `KNOWLEDGE_BASE_*` variable as `llm_api_key`. That is one guard and one test in item 12.
- If Michaël declines, record it in the PR's Collateral findings next to owner call (d), with KB-5/KB-6 named. Do not leave it unstated.

### [MEDIUM-3] Recording the API-key source gives asymmetric behaviour and forces upgrade admin writes

- Under D2, adding `KNOWLEDGE_BASE_EMBED_API_KEY` later is a silent "first binding", but removing it refuses start.
- An inline key or an `env:OTHER` row left by the old `configure_embeddings` refuses start. The only remedy is raw SQL, and the "Upgrading an existing install" doc does not mention it.
- **Fix:** the source is a constant, so treat `embed_api_key` as derived rather than as identity. `bind_embed_config` rewrites or deletes the row from the declaration and never echoes the old value.
  - This means less code and a symmetric rule.
  - It also removes an inline secret from disk instead of requiring Michaël to do it by hand.

### [MEDIUM-4] "Each commit leaves the suite green" is never checked

- Only item 3 runs the full suite. Items 5, 7, 8, 9 and 13 change behaviour shared across the suite but run only subsets.
  - Item 5 seeds `embed_base_url=http://embedder.example.test:11434` into every blank test database.
  - Item 7 makes `_build_provider` raise for `ollama` with no URL. Legacy-style fixtures that pre-seed `embed_model` get no `embed_base_url` row (for example `tests/test_auto_relate.py:27-37`), so any such test that reaches a real provider would now raise.
- **Fix:** add `uv run pytest tests/ -q -m "not slow"` before every commit, at least for items 5 through 14.

### [MEDIUM-5] Gaps in the upgrade and deployment docs

1. **Config-path `openai` without a URL now raises (D4).** The AC says "the explicit `openai` provider is unchanged". The plan's reading (the bare-name `OpenAIProvider` is unchanged; the config path needs a declared URL) is defensible but should be stated as a call. A database seeded with `EMBED_PROVIDER=openai` must:
   - declare `KNOWLEDGE_BASE_EMBED_URL=https://api.openai.com`;
   - move `OPENAI_API_KEY` to `KNOWLEDGE_BASE_EMBED_API_KEY`.

   Document both.
2. **The live harness registration has no `env` block today.** It lives in `~/.claude.json` (noted in `~/.dotfiles/docs/analysis/2026-09-20-epic-219-reconciliation.md:316`). After a release, KB exits 78 and simply disappears from every session. The release-checklist update in the Documentation section covers only `.release-env` for `migrate`; it must also update the client registration before the promote step.
3. **Inline or foreign `embed_api_key` rows** need upgrade documentation (see MEDIUM-3).

### [LOW]

- **Two sources for the declaration (D2):** `_seed_default_config` must read the environment itself, because `init_schema(conn)` takes no declaration, while `bind_embed_config` compares against the `declared` argument. Either pass `declared` through to seeding or drop the parameter.
- **D1 wording:** "trailing `/` dropped, then `/v1`" is ambiguous; say "a trailing `/v1` removed".
- **Leftover `EMBED_PROVIDER` read:** `get_provider`'s bare-name path still reads `EMBED_PROVIDER` (`embeddings.py:372-374`). It has no production caller. Delete `allow_env_override` or say why it stays; item 16's `rg` covers docs only.
- **Item 11 red may never show:** the fake venv must contain `bin/python`. Otherwise `_render_with_browser` returns before `subprocess.run` (`web.py:663-670`) and the subprocess is never called.
- **Item 13 subprocess test:** give it a `timeout=` so the red-phase stdio server cannot hang.
- **Guard tests listed as reds:** items 5, 6 and 14 include tests that pass before the change (for example `test_existing_database_initializes_without_a_declaration` and the floor guards in item 14). Label them as guards.
- **Matryoshka:** document that `KNOWLEDGE_BASE_EMBED_DIM` is the stored dimension after truncation, not the model's native dimension.
- **Verification:**
  - Use `uv run ruff`, not bare `ruff`.
  - "Opens no DB" is never checked; point `KNOWLEDGE_BASE_DB` at a temp path and run `test ! -e`.
  - Add a positive subprocess start under a declaration.
- **Testing overclaims:** "No network by construction" is too strong, because `http_requests` is opt-in.
- **ADR-0020 is not written yet:** memory-engine branch `docs/1014-adr-0020` has no ADR file. ADR-0015 has no conflicting rule. Record this in Constraints and re-check before merge.
- **Item 8 file list:** it includes `tests/test_routes_embeddings.py`, which never references `configure_embeddings`.
- **Boy-scout:** the `configure_llm_tool` docstring "Shared with the embedding path" for `allow_loopback` (`routes/extraction.py:192-193`) goes stale once D5 lands.

## Structural completeness

Documentation, Testing and Verification sections are all present and filled in. The implementation order is feasible, subject to MEDIUM-4. On over-engineering: the `http_requests` and `fake_dns` fixtures and `EmbedSettings` are justified. Binding the key-source row is the one piece of unneeded machinery (MEDIUM-3).

---

# Round 2 (delta review, candidate 2, plan SHA-256 df27ab6e8226292c39afdea1b5ef0648c588ee48bf901a65633b9bef629edbbf)

Reviewer: the same clean-slate subagent, resumed. Its round-2 altitude line read: `Altitude report SHA-256: 547c687adcf94a0757ab1233eb036fd9a1f70abd261421d933f532fcad84ee80`.

Verdict: nearly ready; no blockers and no HIGH. All round-1 findings resolved (MEDIUM-2 partly, re-raised below with new evidence). Altitude "proceed" agreed; no override to acknowledge.

- [MEDIUM-1] The gate never compares the active space with the declaration: a pre-#569 database whose `config` drifted ahead of its active space (`embed_swap.py:58-62`, `121-151`) passes the gate, then fails every ingest and search. Fix: a plain bind also checks the active space's (provider, model, dim); the re-embed transition skips it.
- [MEDIUM-2] Item 14 depends on item 15: re-embedding to `openai_compat` at `https://embedder.example.test` hits the SSRF floor and a real DNS lookup (`embeddings.py:209`, `utils.py:48-56`) before the exemption lands. Fix: use `fake_dns` and an `is_private_ip` patch, or target the unfloored `ollama` family; keep the exemption last.
- [MEDIUM-3] A transition to or from `onnx` leaves an `embed_base_url` row that the next plain bind refuses. Fix: write the row, or delete it when the declaration has no URL; test `ollama → onnx`.
- [MEDIUM-4] Rolling back now always means a full re-embed (`embed_swap.py:1-5`, `628-638`, `450-485`). Option A (recommended): the re-embed transition promotes an existing deprecated space with the declared identity after a catch-up. Option B: accept and document.
- [MEDIUM-5] The `env:KNOWLEDGE_BASE_*` refusal is a denylist; `_resolve_api_key` resolves any variable (`utils.py:125-139`), so with the LLM on, `env:<any other secret>` can still reach a tool-named host via the probe (`llm.py:398-404`). Fix: an allowlist, inline or `env:KNOWLEDGE_BASE_LLM_API_KEY` only.
- [LOW] `re_embed_tool` is nearly dead; state its remaining use. `test_promote_refuses_an_undeclared_identity` should assert `embed_spaces` unchanged (no orphan). "Purges an inline key" overclaims without `secure_delete`/`VACUUM`. D6 is ambiguous for read-only queries of `configure_*`. A positive real-process start is likely possible with stdin from `/dev/null`.

## Resolution

Round 1 → candidate 2 (see `~/.local/state/super-plan/2026-11-16-569-explicit-embedding-endpoint/changelog-r2.md`):

- [HIGH-1] runtime identity rewrite → `declared_provider_config` at all five embed sites; `promote_space`/`re_embed` identity guard (D3; items 7, 14).
- [HIGH-2] owner call (b) and migration → `re-embed` and `rebind` maintenance transitions replace raw SQL; the rewrite-at-start alternative is stated in (b).
- [MEDIUM-1] configure_vision writes then raises → every `configure_*` refuses before writing while its switch is off.
- [MEDIUM-2] LLM-on key exfiltration → key-spec guard (made an allowlist in round 2).
- [MEDIUM-3] key-row asymmetry → `embed_api_key` derived and rewritten at every bind.
- [MEDIUM-4] per-commit suite → every item's green runs the suite (full suite since round 2).
- [MEDIUM-5] upgrade docs → `openai` notes, owner call (e), release-checklist step.
- [LOW] all applied: single declaration read in `open_declared_db`; D1 wording; bare-name `EMBED_PROVIDER` override deleted; fake venv with `bin/python`; subprocess `timeout=30`; guards labelled; Matryoshka DIM; `uv run ruff`; "opens no DB" checked; testing claim narrowed; ADR-0020 status in Constraints; item 8 file list; `configure_llm_tool` docstring.

Round 2 → candidate 3:

- [MEDIUM-1] → D2: a plain bind checks the active space's (provider, model, dim); `re-embed` skips it; test in item 6.
- [MEDIUM-2] → item 14's move targets the unfloored `ollama` family at `http://embedder-b.example.test:11434`; the exemption stays last.
- [MEDIUM-3] → D2: the re-embed transition deletes the URL row when the declaration has none; `ollama → onnx` case in item 6.
- [MEDIUM-4] → Option A adopted: `re-embed` reuses a `populating` (resume) or `deprecated` (rollback) space with the declared identity; `backfill_space` accepts `deprecated`; rollback test in item 14; documented.
- [MEDIUM-5] → D6 allowlist: inline or `env:KNOWLEDGE_BASE_LLM_API_KEY` only, enforced in `configure_llm` and on stored rows in `_get_llm_config`; D1 row added; test in item 12.
- [LOW] `re_embed_tool`'s remaining use stated in D3; orphan assertion added; upgrade note says to rotate an inline key (free pages, WAL, backups); `configure_*` refuse read-only queries too; positive subprocess start added to item 13 (verified on master: `knowledge-base </dev/null` exits 0).

---

# Round 3 (delta review, candidate 3, plan SHA-256 48cdb7b4133ac7378c7156289ebd80ac5e28e5f89f382e275c48558e69972232)

Reviewer: the same subagent, resumed. Its altitude line read: `Altitude report SHA-256: 3fbb853794ed9d51a3ec7bff5fc9469bc4d2e58cea205649cb987636093b948b` (after first restating the round-2 hash and correcting it).

Verdict: one more small revision; no blockers, no HIGH. All round-2 MEDIUMs resolved; (f) and (g) accepted. Checked against source: `backfill_space` resumes by construction (`embed_swap.py:383-399`); the active-space bind check and the LLM allowlist are sound.

- [MEDIUM-1] Rolling back to a deprecated space keeps orphan vectors and an inflated `chunk_count`: every deletion path removes vectors only from the active table (`db.py:714-759`; callers `ingest.py:640`, `web.py:506,831,981,1053`, `vision.py:1948`), and the catch-up only adds. Fix: prune orphans and reset `chunk_count` before promoting a reused space; test with a reingest between move and rollback.
- [MEDIUM-2] The reuse match key `(provider, model, dim)` is too narrow (`chunk_strategy`, `matryoshka_base_dim`, `element_type`, several candidates; `embed_swap.py:271-336`) and collides with `re_embed_tool`'s remaining use. Fix: match the full spec, refuse ambiguity, keep "use rebind" in `cmd_re_embed` only.
- [LOW] Zero-norm chunks re-embedded on every resume, `chunk_count` bumped by `len(batch)` (`embed_swap.py:412-431`, pre-existing). `configure_llm` writes before raising on a stored bad row (`llm.py:372-398`). An interrupted re-embed exits through a traceback (`indexer.py:362-366`). No-active-space case unstated. Owner calls out of order.

## Resolution (round 3 → candidate 4)

- [MEDIUM-1] and [MEDIUM-2] → the reuse/resume/rollback rule is dropped rather than patched (Codex raised the same class independently: mixed endpoints in a resumed space, unmatched space parameters). `re-embed` always creates a new space, as `re_embed` does today; an interrupted run never serves (the active-space check) and a rerun starts a new space, the abandoned one removed by `cleanup_embed_space_tool`. Owner call (g) now states the consequence: rolling back a model costs a full re-embed. `backfill_space` keeps refusing `deprecated`.
- [LOW] zero-norm/`chunk_count` → pre-existing and no longer on a new resume path; dismissed for this ticket. `configure_llm` → validates the effective key before writing anything; item 12 asserts no row is written. Traceback → the CLI handler catches `httpx.HTTPError` with a rerun message; tested. No active space → the bind refuses. "use rebind" lives only in the `re-embed` transition (CLI), so `re_embed_tool` is unaffected. Owner calls reordered (a)–(g).
- Also from Codex round 3 → the transition's "use rebind" refusal and every bind check run before any write, with one commit at the end; `promote_space` becomes all-or-nothing (summaries embedded first, then one transaction), which replaces the earlier "(g) pre-existing, collateral" call.

---

# Round 4 (delta review, candidate 4, plan SHA-256 1190f940271f1eac24ec871f5f5712435e1248e98264f15106310093bbb10359)

Reviewer: the same subagent, resumed. Its altitude line read: `Altitude report SHA-256: 5f5f969cc2ef21eadc732100939622a8ca04d0974ab60846e4771f306cd7507c`.

Verdict: nearly LGTM; no blockers, no HIGH; all round-3 findings resolved and owner call (g) accepted. Feasibility of the all-or-nothing `promote_space` confirmed by a scratchpad probe on sqlite-vec 0.1.6 (UPDATE, DROP vec0, CREATE vec0, INSERT, ROLLBACK restores the original table and KNN), with the caveat that DDL before DML autocommits under the default isolation level; recommends explicit `BEGIN IMMEDIATE` as in `migrate.py:230-245`, removing the commits at `embed_swap.py:676,697`, and keeping the `similar` DELETE (`embed_swap.py:526`) inside.

- [MEDIUM-1] A drift state with no repair command: `config` drifted (old `configure_embeddings`, `embed_swap.py:236-237`) while the active space kept the real identity, which the operator declares: plain bind, `re-embed` ("use rebind") and `rebind` (both identity checks) all refuse. Fix: `rebind` requires the active space to equal the declaration and rewrites the `config` identity rows and URL.
- [MEDIUM-2] `test_promote_is_all_or_nothing` injects only before any write. Fix: also fail after the `DROP TABLE folder_summaries_vec`.
- [LOW] `cleanup_space` refuses `populating` (`embed_swap.py:563-565`); "a refused bind changes nothing" overclaims because `init_schema` runs first; the `httpx.HTTPError` handler message must name the subcommand.

## Resolution (round 4 → candidate 5)

- [MEDIUM-1] → D2/D3: `rebind` requires the active space's identity to equal the declaration, then records the URL and rewrites the `config` identity rows; tests in items 6 and 14.
- [MEDIUM-2] → already in candidate 5 (raised by Codex too): the test is parametrized over a failure before any write and one while inserting the second summary vector after the drop/recreate, checked on the same and a fresh connection.
- [LOW] cleanup → a failed `re_embed` removes the space it created (`deprecate_space` then `cleanup_space`), so no manual cleanup exists to document; claim scoped to the bind's own writes; handler message names the subcommand. Explicit `BEGIN IMMEDIATE`, no inner commits and the in-transaction `similar` DELETE are written into D3.

---

# Round 5 (delta review, candidate 5, plan SHA-256 ad27adcb1383950e4df4f6204a9d58c6bb44eeaa1b8f6c6254c8d5d78135513a)

Reviewer: the same subagent, resumed. Its altitude line read: `Altitude report SHA-256: a63dc38e7ed6879c0aaf73b00772e8e9de864460d5fc7670a2394ee4a0a348ba`.

Verdict: **LGTM**; all round-4 findings resolved, nothing at BLOCKER/HIGH/MEDIUM. The rebind repair rule is sound (trusts the vectors; `assert_embed_identity_match` holds after it, `embed_swap.py:139-151`; Matryoshka/element-type rows are written only by `promote_space`, `embed_swap.py:517-524`). The failed-`re_embed` cleanup is sound for exceptions (`deprecate_space` refuses only `active`, `embed_swap.py:543-555`; `cleanup_space` requires `deprecated`, `563-565`).

- [LOW] A killed run (Ctrl-C is `BaseException`, SIGKILL, OOM, host loss) still leaves a `populating` orphan, contrary to "no manual cleanup step remains". Fix: catch `BaseException`, re-raise; document the deprecate+cleanup line for killed runs.
- [LOW] The cleanup must not hide the original error: wrap it in its own `try`, log, re-raise the original.
- [LOW] Guard the explicit `BEGIN IMMEDIATE` (`promote_embed_space_tool` on a thread-local connection may already hold an implicit transaction): check `conn.in_transaction` or toggle `isolation_level = None` as `migrate.py:230-271` does.

## Resolution (round 5 → candidate 6)

- [LOW] killed run → cleanup catches `BaseException` and re-raises; the killed-run residual (a harmless `populating` space, removed with the deprecate then cleanup tools once the server runs) is documented in D3 and the README note.
- [LOW] original error → the cleanup runs in its own `try`, its failure is logged, and the original exception is re-raised.
- [LOW] `BEGIN` guard → `isolation_level = None` during the promotion, restored afterwards, as `migrate.py:230-271` does.
- Codex round 5 (same candidate) → the whole promotion (completeness check, summary read and embed, writes) now runs under one `BEGIN IMMEDIATE`, so no concurrent writer can interleave; the killed-run residual is the same as above.

---

# Round 6 (delta review, candidate 6, plan SHA-256 ab4b9674b5678696f89108410608f1046619c2bf53e62b66a04315b3e6e4e3ce)

Reviewer: the same subagent, resumed. Its altitude line read: `Altitude report SHA-256: 7c44bfaf5e95e89f93454875b5fbe7f816b749ae4a2f3ac0e4b033117b23a88a`.

Verdict: one MEDIUM (a decision rather than a defect), then LGTM; no blockers, no HIGH; all round-5 LOWs applied. Holding `BEGIN IMMEDIATE` across the summary embed is correct but, in a served process, can exceed the job worker's 5 s busy timeout (`jobs.py:135`; tool connections 30 s, `db.py:150,380`) given 120 s per embed batch (`embeddings.py:159-165,205-235`).

- [MEDIUM] A slow embed during an in-server promotion fails background jobs (`jobs.py:178-179,201-213`; possibly stuck `running`, `jobs.py:143-146,137`) and, past 30 s, ingests. Option A (recommended): optimistic promote, embed before the lock, recheck and re-embed changed rows inside it. Option B: keep and document, raise the worker timeout.
- [LOW] `promote_space`'s `ROLLBACK` handler must catch `BaseException` and the `isolation_level` restore must be in a `finally`; add a Ctrl-C case.

## Resolution (round 6 → candidate 7)

- [MEDIUM] → Option A adopted: the summary snapshot is embedded outside the lock; inside `BEGIN IMMEDIATE` the completeness check reruns, changed rows are re-embedded (usually none), then all writes; test for a summary changed after the snapshot.
- [LOW] → `ROLLBACK` on `BaseException`, `isolation_level` restored in a `finally`, `KeyboardInterrupt` case added to `test_promote_is_all_or_nothing`.
- Codex round 6 (same candidate) → `promote_space` refuses a connection already in a transaction, so switching `isolation_level` never commits a caller's pending writes (test). The concurrent writer that embedded against the old space before a promotion is the pre-existing lifecycle race; D3 bounds it to a Matryoshka or chunk-strategy variant promoted in a served process (identity-changing promotion runs only in the CLI with the unit stopped) and it is recorded as a collateral finding.

---

# Round 7 (delta review, candidate 7, plan SHA-256 994c30bfe98f9bc077714a149bc840923ec0b3b443989f5b770410a6bc7e68ad)

Reviewer: the same subagent, resumed. Its altitude line read: `Altitude report SHA-256: 3207898e6138026ff5d5994c93a2288290eb196150e4223e2a6789415fb824d0`.

Verdict: **LGTM**. Round-6 MEDIUM and LOW resolved; the `conn.in_transaction` refusal is sound (`re_embed` reaches `promote_space` after per-batch commits, `embed_swap.py:430-431`). The lifecycle-race arbitration holds: in a served process a late writer lands a same-provider, same-model, same-dimension vector; the `element_type` variant is harmless (`db.py:700-703`), the Matryoshka variant is close but not identical, the `chunk_strategy` variant pollutes a filter only (`ingest.py:435,442`).

- [LOW] The arbitration relies on "unit stopped" for the CLI transitions, unenforced (an idle server holds no lock; `migrate.py:190-195`'s probe cannot see it). Note it next to the collateral finding; an advisory lock is optional.
- [LOW] Spell out the summary diff rule for folders added or removed after the snapshot.
- [LOW, boy-scout] Remove the redundant `similar` deletes in the routes (`routes/embeddings.py:94-95,187-188`).

## Resolution (round 7 → candidate 8)

- [LOW] unit-stopped dependency → stated in D3 next to the race bound; the README states the requirement; the advisory lock is part of the same collateral finding.
- [LOW] diff rule → D3: new or changed rows are embedded, vanished rows skipped, snapshot vectors reused only where path and text are unchanged.
- [LOW] route deletes → removed in item 14 (D3).

---

# Round 8 (confirmation, candidate 8, plan SHA-256 12997b016ebded1a3eac4956fb6028af1a0687494543097f776c1a5db7c14528)

Reviewer: the same subagent, resumed. Its altitude line (the final, current report):

Altitude report SHA-256: 26ed18e6eb4b66b711db7bc58a90861d3a6ce7ff990bfc9fb5dc976b031d1235

Verdict: **LGTM.** All three round-7 LOWs applied correctly (diff rule, unenforced unit-stopped dependency stated with the advisory lock filed as collateral, route-level `similar` deletes removed). Altitude "proceed" agreed.

- [LOW] `promote_embed_space_tool` catches only `ValueError` (`routes/embeddings.py:191`) and `re_embed_tool` has no handler (`routes/embeddings.py:76-102`), while the new refusals are `ValidationError`/`KnowledgeBaseError` (`exceptions.py:17,33`); add `KnowledgeBaseError` to both handlers in item 14 so the planned test's "returns an error naming the field" holds.
- [LOW, boy-scout] `cmd_re_embed` has the same redundant `similar` delete and commit (`indexer.py:159-161`); remove it with the route copies.

## Resolution (round 8, final)

Exit condition: all three reviewers (Codex, agy, this subagent) returned LGTM on candidate 8; only two LOWs remain, both implementation details inside item 14's stated intent. Arbitrated by the orchestrator without a further plan revision (a byte change would void every reviewer's acknowledgement of the final report for no change of intent):

- [LOW] route handlers → accepted: item 14's own test already requires both tools to "return an error naming the field", which needs `KnowledgeBaseError` in the promote handler and a handler on `re_embed_tool`; implemented in item 14 and recorded in the plan audit as an intent-preserving variation.
- [LOW] CLI `similar` delete → accepted: removed in item 14 with the route copies (same reason, same commit); recorded in the plan audit.
