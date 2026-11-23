# #657 — group Q: redact JSON object keys (#652), one embedder seam (#654), record cut folder summaries (#656)

## Outcome

knowledge-base (KB) redacts the object keys of chunk metadata as memory-engine (ME) does: a key is scrubbed as a
string, each value is judged under its key as written, and keys that redact to one text keep every member,
the later ones in key order renamed `~1`, `~2`, …. Three new `json-` cases in the shared
`tests/fixtures/redaction-cases.jsonl` pin it; ME's PR lands them first and both repositories pin the same sha256.
Every stored text (chunks at ingest, backfill and rewrite; folder summaries at refresh and at promotion) is embedded
through `embed_swap.space_embedder`, `embeddings.embed()` and its `cuts=` out-parameter are gone, and tests fake the
embedder at the one place a provider is resolved, with the same test ids and outcomes as before. A folder summary
whose vector was cut to `max_input_tokens` leaves a `folder_summary_truncations` row (folder, space, tokens counted,
tokens kept) in schema v5; a refresh that no longer cuts deletes it and a promotion replaces all of them.

## Constraints

Owner decisions (verbatim):

- One KB PR closes #652, #654, #656, each in its own commit (`Closes #N` per member). Route: Bug fix / Standard / Super planning / Full review, Default severity.
- #652 is a cross-repository pair: the shared fixture `tests/fixtures/redaction-cases.jsonl` stays byte-identical in both repos. Its ME half (new key-redaction fixture cases) is ONE small ME PR merged FIRST; the KB PR then quotes the same sha256.
- ME's behaviour is the reference (ME #1128 redacts JSON object keys on every write path). ME code changes beyond the fixture are out of scope; if ME needs one, report it.
- #656: the plan chooses between a folder-summary truncation record (new table, or a nullable folder target on `embed_truncations`) and a summary built to fit the limit; weigh migration cost and what a re-embed after a limit change targets.
- #654: the suite's test count stays unchanged; a provider-level fake replaces the ~207 `ingest.embed` patches. Large mechanical change: local-agent bundles, one at a time, under the worker's review.
- No benchmark. Heavy commands (pytest, cargo) run one at a time under the host lock and memory cap.
- Both repositories are public: no host names, home paths, tokens or private data in anything committed or posted.

Acceptance (verbatim):

- #652: A failing test first: a JSON object whose key is secret-shaped is stored with the key redacted. Shared fixture cases for key redaction, landed as a memory-engine-then-knowledge-base pair quoting the same sha256 (the fixture stays byte-identical).
- #654: One provider-level test seam (a fake embedder registered where the provider is resolved) replaces the `ingest.embed` patches. `_embed_with_config` reuses `space_embedder`; the suite passes unchanged in count.
- #656: A folder summary cut to fit `max_input_tokens` leaves a durable record: tokens counted, tokens kept, folder, space. A refresh that no longer cuts removes the stale record, as the chunk path does. Tests: one cut summary recorded, one uncut summary leaves none, one refresh clears the record.

## Decisions

- **D1, #656: a new table, schema v5.** `folder_summary_truncations (folder_path TEXT PRIMARY KEY REFERENCES
  folder_summaries(folder_path) ON DELETE CASCADE, space_name TEXT NOT NULL REFERENCES embed_spaces(name) ON DELETE
  CASCADE, tokens_before INTEGER NOT NULL, tokens_after INTEGER NOT NULL)`. Keyed on the folder alone:
  `folder_summaries_vec` holds one vector per folder, always the active space's, and `promote_space` rebuilds all of
  them, so one record per folder describes exactly the vectors that exist.
  - Rejected, a nullable folder target on `embed_truncations`: `chunk_id` is NOT NULL and in its primary key, so it
    needs a foreign-keys-off table rebuild, a one-target CHECK and new unique indexes, and every reader
    (`truncated_chunks`, `chunks_au_truncations`, `redact_store`'s exclusion by name) starts seeing rows that are not
    chunks.
  - Rejected, a summary built to fit: a summary grows about 200 characters per document, so a large folder still
    loses documents or snippets, partial text with nothing recording it; the text would depend on the limit while its
    staleness hash (chunk hashes only) does not, so a refresh would skip a summary built under an old limit.
  - Migration cost: one additive `CREATE TABLE`, the one-time `knowledge-base-ingest migrate` (unit stopped) that v3
    and v4 already asked. A re-embed after a limit change builds a space under the new limit, and its promotion
    rewrites every record; a summary cut before v5 is recorded at its folder's next refresh or the next re-embed.
- **D2, #654: the seam is `embeddings._build_provider` with a fresh `embeddings._provider_cache`.** `get_provider`
  reads both at call time and every resolution goes through it (`embed_swap` imports the same function), so one fake
  answers ingest, folder summaries, backfill, promotion, the redact rewrite and unpatched queries; production code
  gains nothing for tests. The fixture is `stub_document_embedder` (kept name, 44 users, `kb_http`): its
  `StubEmbedder` gains the provider method `embed(texts, model, expected_dim=None)` (raises `embedder_error(down)`
  while `down` is set, else records the texts and returns `answer(...)`) and an `answer` attribute with that same
  signature, defaulting to `[[0.1] * (expected_dim or TEST_EMBED_DIM) for _ in texts]`, so the suite's special fakes
  are assigned unchanged. Not autouse: input-limit, identity and provider tests need the real provider against
  `http_requests` (`bounded_embedder`). The fake has no `token_counter`, so `split_inputs` under a declared
  `max_input_tokens` raises AttributeError with it: only the limit tests declare one (conftest's autouse fixture
  unsets it), and they keep `bounded_embedder`; a converted test that meets the error is listed, never patched over.
  Rejected: patching `get_provider` by name in two modules, a test provider
  family in production, and seeding the cache for one `ProviderConfig`.
- **D3, one stored-text path.** `ingest._embed_with_config(conn, texts)` stays as the active-space entry point vision
  and web import, and becomes `space_embedder(conn, require_active_space(conn))(texts)`. `update_folder_summary` and
  `promote_space` (one embedder for both of its calls) use `space_embedder` too. `embeddings.embed()` is deleted;
  `embed_query` calls `get_provider(cfg=...).embed(...)` itself. Left as they are: the `search.embed_query` patches and
  `stub_embedder` (they record the query without its instruction), the `vision._embed_with_config` patches, and the
  `embed_swap.get_provider` patches of tests that ingest nothing. `web.py`'s three `_embed_with_config` calls change
  with it; no test patches `knowledge_base.web._embed_with_config` (grep).
- **D4, #652: one walk.** `redact_json(value, leaf=scrub)` rebuilds an object over `sorted(value)` (ME's serde_json
  map is a BTreeMap, and UTF-8 byte order is code-point order): the value through `_redact_member` with the key as
  written, then the key through `scrub`, then the first free `f"{key}~{i}"`, i = 1, 2, …, if the rebuilt object
  holds it. A key gets `scrub`, never the caller's `leaf`: ME scrubs a key with the function its string leaves get,
  and KB's whole-URL ruling (`_metadata_leaf` keeps a whole URL but its userinfo) is about a stored reference, which a
  key is not; so a whole-URL key keeps its user name and loses its password and any machine token, as in ME. Every
  object is rebuilt in sorted original-key order (ME's map is ordered by the clean key: the stored order can differ,
  the parsed value cannot); `redact_metadata` still returns the stored string when nothing changes, and tests compare
  parsed values.
- **D5, three shared cases, no collision case.** A case's one output must be both the one-pass text result and the
  walk's; a collision cannot be (the text pass leaves two equal keys, the walk renames one), so it stays each
  repository's unit test. Tokens are those of existing cases (GH: the `ghp_` token of `redacts-provider-prefixes-2`;
  AWS: the key of `redacts-provider-prefixes`; HEX: the value of `json-key-api-key-redacts-hex`):

  | name | input | output | rules |
  | --- | --- | --- | --- |
  | `json-key-holding-a-token-is-redacted` | `{"GH":"plain"}` | `{"[REDACTED:github-pat]":"plain"}` | `github-pat` |
  | `json-nested-key-keeps-its-text-around-a-token` | `{"plain":{"deploy AWS now":1}}` | `{"plain":{"deploy [REDACTED:aws-access-key] now":1}}` | `aws-access-key` |
  | `json-hex-value-reads-the-key-as-written` | `{"GH_token":"HEX"}` | `{"[REDACTED:github-pat]":"[REDACTED:hex-secret]"}` | `github-pat`, `hex-secret` |

  KB's one-pass engine gives exactly these outputs and rules (checked while planning). The third fails any walk that
  redacts the key before judging the value. The header's last sentence becomes: "A case named json-... holds a JSON
  document: each repository's JSON walk must also turn it into its output, every object key redacted as a string and
  a string member of 32 or more hex digits whose key's last word, split on underscores, holds a context word becoming
  [REDACTED:hex-secret], the key read as written; keys that redact to one text are each repository's own test."
- **D6, commit order on the KB branch:** plan, #654, `docs(schema)`, #656, `fix(redact-store)`, #652. #654 is the
  largest and least certain, is measured against an untouched base, and gives #656 its cuts at both folder writers;
  #652 alone waits on ME's merge.
- **D7, the KB PR closes `#657` first**, then #652, #654, #656, as group P's PR #655 did; `Refs dutiona/infra#37`. The
  ME PR carries `Refs dutiona/knowledge-base#652` only: a closing keyword there, negated or not, would close the KB
  issue.

## Implementation sequence

Every pytest and cargo command runs in the lane's capped form under the host lock, one at a time, never piped.
Items 5 to 9 proceed while the ME PR (item 4) is in review; only item 10 waits for its merge.

- [ ] **1. ME worktree and baseline.** In the ME checkout: `git check-ignore -q .worktrees`, `git fetch origin`,
  `git worktree add --no-track -b test/kb652-json-key-redaction-fixture .worktrees/test-kb652-json-key-redaction-fixture origin/main`,
  then `git worktree lock --reason "lane-2026-11-02-me-kb-claude-code worker #657 (KB)"` on it. Baseline, green:
  `cargo test -p memory-engine --all-features --test redaction_conformance` and
  `cargo test -p memory-engine --all-features --lib redact::tests`; a red baseline stops here with its output.
  No commit.
- [ ] **2. ME: share the key-redaction cases.** Files: `tests/fixtures/redaction-cases.jsonl`,
  `tests/redaction_conformance.rs` (`FIXTURE_SHA256`), `docs/reference/redaction.md` (the shared-fixture paragraph).
  - Red: D5's cases appended as plain JSON after `json-short-hex-keeps-hex` and the header sentence edited;
    `cargo test -p memory-engine --all-features --test redaction_conformance` fails `fixture_is_the_shared_copy` and
    `fixture_is_canonical_and_holds_no_secret`, and nothing else (ME already behaves so).
  - Change: `cargo test -p memory-engine --all-features --test redaction_conformance rewrite_fixture_canonically -- --ignored --exact`
    re-encodes the file; `FIXTURE_SHA256` takes its `sha256sum`; the doc paragraph's json- sentence adds "and the
    redaction of object keys (knowledge-base#652); keys that redact to one text are each repository's own test, since
    one text pass cannot rename them".
  - Green: the conformance target passes (one ignored) and
    `cargo test -p memory-engine --all-features --lib redact::tests::fixture_json_cases_pass_the_json_walk` passes.
  - KB dry run, nothing committed in KB, in the clean KB worktree after `uv sync`: copy the file over KB's fixture,
    `uv run pytest tests/test_redact.py -q`:
    exactly `test_fixture_is_the_pinned_one` and the three new `test_the_json_walk_matches_the_shared_json_cases`
    cases fail; `git checkout -- tests/fixtures/redaction-cases.jsonl`. Any other failure is fixed in the ME case first.
  - Commit: `test(redact): share JSON object-key redaction cases with knowledge-base` (body: why, the digest, why no
    collision case).
- [ ] **3. ME boy-scout (for Michaël, see the last section):** `docs/reference/redaction.md` lists "JSON keys: only
  string values are scanned." under "Never redacted", which its own JSON paragraph contradicts since ME#1128; delete
  the line. Green: `uv run --locked sphinx-build -b html -W --keep-going -E docs docs/_build`. Commit:
  `docs(redaction): drop the stale claim that JSON keys are never scanned`.
- [ ] **4. ME PR, merged first.** ME's Verification block at the clean head, push, PR titled as item 2's commit with
  the digest, the gate output, `Refs dutiona/knowledge-base#652` and `Refs dutiona/infra#37`; `/super-review` Full
  with the lane's roster; `/finish-pr` to READY; Michaël merges. Record the merged commit and the digest. No commit.
- [ ] **5. KB plan commit and baseline.** The KB worktree already exists and is locked by the orchestrator
  (`.worktrees/fix-657-q-redaction-parity`, branch `fix/657-q-redaction-parity` at 1e1bd20): reuse it. Commit this plan and its review files (`docs/plans/` is Sphinx-excluded;
  the lens drafts are deleted, not committed): `docs(plans): plan #657, redaction parity group Q`. `uv sync`, then
  before any edit, into the lane's private artifact directory: `uv run pytest tests/ --collect-only -q` (ids) and
  `uv run pytest tests/ -q -m "not slow" -rA` (outcomes). A red baseline is reported before any change.
- [ ] **6. #654: one stored-text path, faked at one provider seam.** Files: `tests/conftest.py`,
  `src/knowledge_base/{embeddings,ingest,folder_summaries,embed_swap}.py`, `tests/test_embeddings.py`, every test file
  patching `knowledge_base.{ingest,folder_summaries}.embed` or using `folder_embedder`, and item 6's docs.
  - Red: `git grep -nE 'knowledge_base\.(ingest|folder_summaries)\.embed\b' -- tests/` lists 235 `ingest.embed` sites
    (the issue's ~207 counted the decorators) and 180 `folder_summaries.embed` sites. No `slow`-marked test uses them
    (only `test_pymupdf4llm_integration.py` and `test_vision_omniparser_integration.py` carry `slow`, with no such
    patch), so the `not slow` outcomes cover every converted test.
  - 6.1 Seam (worker): D2's `StubEmbedder.embed` and `answer`; `stub_document_embedder` sets
    `embeddings._provider_cache` to `{}` and `embeddings._build_provider` to `lambda cfg: fake` via monkeypatch.
  - 6.2 Candidate list before converting, kept in the artifact directory, each with its disposition quoted in the
    commit body: tests that faked ingest but not folder summaries and reach a summary (it now succeeds: folder rows
    appear, the ×1.15 folder boost applies, `http_requests` no longer records a failed summary call); the five
    `embed_swap.get_provider` patches that span an ingest (three in `test_embed_swap.py`, `test_cmd_re_embed`, the
    `vectors` fixture of `test_redact_store.py`), which now also answer ingest at the requested `expected_dim`;
    tests asserting recorded texts, `http_requests`, `folder_summaries` rows or absolute scores; tests that search
    under `stub_document_embedder` without `stub_embedder` (the query now reaches the fake); and every module
    `_fake_embed`-style function whose body is not the fixture's default answer (non-constant, `None` or another
    dimension), which keeps its behaviour through `answer` instead of the plain decorator rule. A test keeps its
    meaning; an assertion changes only where it asserted the silent failure, and the body says so.
  - 6.3 Tracer by hand: `tests/test_folder_summaries.py`, green at its base count on unchanged production code.
  - 6.4 By hand: `test_ingest.py`'s `_fake_embed_odd_none` (line ~198), `side_effect=Exception` (~2742, becomes an
    `answer` raising the same exception), `_fake_embed_with_zero` (~3776), `_down` (~4371); `test_embed_identity.py`
    ~710 and ~778; `test_lean_surface.py`'s `_racing_embed`; `test_routes_ingestion.py` ~386; `test_redact_store.py`'s
    `vectors` (its hash answer and `down` through the fake, its by-name patches gone); `test_redact_paths.py`'s
    `folder_embedder`, deleted (the fake records summaries too); `test_embeddings.py`'s two `embed()` tests rewritten in
    place (`space_embedder` through the fake; `get_provider` for the missing base URL, same assertions).
  - 6.5 Bundles, one `local-agents:local-implementer` at a time per address-issue's local-bundles reference, ledger
    kept: B1–B3 `test_ingest.py` in three parts at test boundaries; B4 `test_extraction.py`; B5 `test_papers.py`; B6
    `test_reranker.py`, `test_search.py`; B7 `test_indexer.py`; B8 `test_conclusions.py`, `test_vision.py`,
    `test_embed_swap.py`, `test_web_limits.py`, `test_web_redirects.py`. Rule: in the named files only, each
    `@patch("knowledge_base.ingest.embed", _fake_embed)` and `@patch("knowledge_base.folder_summaries.embed", _fake_embed)`
    on a test or class becomes one `@pytest.mark.usefixtures("stub_document_embedder")` at the top of its decorator
    stack, but only where the patched fake is one 6.2 found to equal the default answer; a `with patch(...)` of those
    names is removed, its body dedented, the test taking the fixture; any other form is listed, not converted; a module
    fake nothing uses is deleted. Done when `uv run pytest <files> -q` matches those
    files' base outcomes and the red grep prints nothing for them. The worker reads every diff and reruns the check.
  - 6.6 Production, last (worker): D3. `ingest` and `folder_summaries` stop importing `embed` and
    `truncate_embedding`; `_embed_folder_summaries(embed, rows)` takes a `space_embedder`; the "Module-level dispatch
    functions" comment goes.
  - Green: the red grep and `grep -n '^def embed(' src/knowledge_base/embeddings.py` print nothing; the collected ids
    and the sorted `PASSED`/`SKIPPED` lines of `-rA` equal item 5's (`diff` empty); ruff check, ruff format --check and
    basedpyright clean.
  - Commit: `refactor(tests): fake the embedder where the provider is resolved` (body: the by-name patches pinned
    `embed`'s shape, which is why #651 kept `cuts=`; the dispositions; the id and outcome evidence; `Closes #654`).
- [ ] **7. Boy-scout: document the folder tables.** `docs/reference/schema.md` documents neither `folder_summaries`
  nor `folder_summaries_vec`: their ER entities and sections from `db.py`'s DDL (the vec table holds the active
  space's vectors only and is rebuilt at promotion). Green: the Sphinx row of the Verify block. Commit:
  `docs(schema): document folder_summaries and folder_summaries_vec`.
- [ ] **8. #656: record a folder summary cut to the input limit.** Files: `src/knowledge_base/migrate.py`
  (`add_v5_folder_summary_truncations`, `_MIGRATIONS[5]`, `CURRENT_SCHEMA_VERSION = 5`, `__all__`),
  `src/knowledge_base/db.py` (the init chain, after v4), `src/knowledge_base/embed_swap.py`,
  `src/knowledge_base/folder_summaries.py`, `tests/test_embed_limits.py`, `tests/test_embed_identity.py`,
  `tests/test_embed_swap.py`, item 8's docs.
  - Red, `uv run pytest tests/test_embed_limits.py tests/test_embed_identity.py tests/test_embed_swap.py -q`:
    under `bounded_embedder`, `test_an_over_length_folder_summary_no_longer_fails_its_batch` also asserts the one row
    (folder, active space, the summary's count, the sent text's count); new
    `test_a_folder_summary_that_fits_leaves_no_truncation_record`,
    `test_a_refresh_that_fits_clears_the_folder_truncation_record` (cut, chunks rewritten short, refreshed: no row,
    one vector), `test_re_embed_records_the_folder_summaries_its_promotion_cuts` (the row names the new space),
    `test_a_dropped_folder_summary_takes_its_truncation_record` (folder emptied, refreshed: no summary, no row);
    `test_a_v4_database_migrates_to_v5` (version and fresh shape; `_drop_v5` runs inside `_drop_v4`), and
    `test_a_v3_database_migrates_to_v4` reads `CURRENT_SCHEMA_VERSION` instead of 4; `test_promote_is_all_or_nothing`
    seeds a record, keeps it on the failed attempt and replaces it on the retry.
  - Change: `embed_swap.record_folder_truncation(conn, folder_path, space_name, cut | None) -> None` (insert or
    replace for a cut, delete otherwise), beside `record_truncation`. `update_folder_summary` upserts the summary, then
    the vector, then calls it with `cuts.get(0)` (None for no vector), before its one commit. `_embed_folder_summaries`
    maps each row to `(vector, cut)`; `_write_folder_summaries_vec(conn, space_name, dim, vectors)` recreates the vec
    table, deletes every record and records each stored vector that was cut, inside promote's transaction.
  - Green: the same command, then `uv run pytest tests/ -q -m "not slow"`.
  - Commit: `fix(folder-summaries): record when a folder summary is cut to the embedder's input limit` (`Closes #656`).
- [ ] **9. Boy-scout: the redact report counts what the rewrite adds.** Pre-existing since #626: `report()` counts a
  metadata row's rules through `_strings`, values only, so a hex value redacted under a context key is never counted.
  Red: new `test_report_counts_a_hex_secret_under_a_metadata_key` in `tests/test_redact_store.py`
  (`rows_to_rewrite["metadata"] == 1`, `spans == {"hex-secret": 1}`). Change: the metadata spans become the
  placeholders `redact_metadata`'s output adds over the stored text (`Counter` of `\[REDACTED:([a-z0-9-]+)\]` in new
  minus old); `_strings` is deleted. Green: `uv run pytest tests/test_redact_store.py -q`. Commit:
  `fix(redact-store): count the metadata findings the rewrite adds`.
- [ ] **10. #652: redact JSON object keys like ME, after item 4's merge.** Files: `tests/fixtures/redaction-cases.jsonl`,
  `src/knowledge_base/redact.py`, `src/knowledge_base/ingest.py` (docstring), `tests/test_redact.py`,
  `tests/test_redact_paths.py`, `tests/test_redact_store.py`, item 10's docs.
  - Fixture: `git fetch origin` in the ME checkout, `git show origin/main:tests/fixtures/redaction-cases.jsonl` becomes
    KB's file; its `sha256sum` equals ME's pin and becomes `FIXTURE_SHA256`.
  - Red, `uv run pytest tests/test_redact.py tests/test_redact_paths.py tests/test_redact_store.py -q`: the three walk
    cases; `test_chunk_metadata_is_redacted_keys_kept` becomes `test_chunk_metadata_keys_are_redacted` (a
    `GITHUB_TOKEN` key stored as `[REDACTED:github-pat]` through `ingest_text(..., metadata=...)`: the acceptance's
    failing test); `test_metadata_urls_keep_their_query` gains a whole-URL key with `user:password@`, stored with the
    password redacted and the user kept (D4's ME rule; the value beside it still loses its whole userinfo); new
    `test_redact_json_redacts_keys_and_keeps_every_member`, ME's test of that name without its denylist, the two
    AWS-shaped keys inserted against key order (`…EXAMPLE` keeps the bare placeholder, `…EXAMPLF` gets `~1`);
    `test_the_key_rule_runs_before_a_custom_leaf` stays as it is (keys never reach a custom leaf), plus one assertion
    that a token key under that leaf is still redacted; `test_redact_json_keeps_keys_and_non_strings` renamed `test_redact_json_keeps_clean_keys_and_non_strings`; new
    `test_report_counts_a_secret_in_a_metadata_key` (`spans == {"github-pat": 1}`).
  - Change: D4.
  - Green: the same command, then `uv run pytest tests/ -q -m "not slow"`.
  - Commit: `fix(redact): redact JSON object keys like memory-engine does` (body: the sha256, the ME PR, `Closes #652`).
- [ ] **11. KB gate, PR, plan archive, review.** The Verify block at the clean head. Push; PR titled
  `fix: group Q — JSON-key redaction parity, one embedder seam, folder-summary truncation record`, body: D7's lines,
  `Classification: Bug fix (user-confirmed)` and the route lines, the ME PR and the shared sha256, #654's id and outcome
  evidence, the deploy note (schema v5: stop the unit, `knowledge-base-ingest migrate` once; until then the server
  exits 78), the gate output, `Collateral findings`. Then `/finish-pr` Item 5 first (it reuses this plan's issue and
  removes `docs/plans/` in one commit, so the review covers that head), then `/super-review` Full through the persisted
  route, each fix its own commit with a delta review, then `/finish-pr` to READY with a fresh Item-4 re-poll at 0.
  Michaël merges; the worker never does.

## Documentation

- #654 (item 6): `AGENTS.md` rule 6's embedder sentence names `stub_document_embedder` (the provider every embedding
  path resolves in that test; `answer` for other vectors, `down` for an endpoint failure; never patch a module's
  `embed` name; input-limit tests use `bounded_embedder`); `CLAUDE.md`'s matching line; `GEMINI.md` if it carries one.
  `docs/design/adr/0018-provider-abstraction.md`: a dated amendment (its "mocks that `@patch("knowledge_base.ingest.embed")`
  keep working" no longer holds; `embed()` is gone; stored texts go through `space_embedder`).
  `docs/design/architecture-overview.md`: the `embeddings.py` and `embed_swap.py` paragraphs.
- Item 7: `docs/reference/schema.md`, the two folder tables.
- #656 (item 8): `docs/reference/schema.md`, the ER relation and a `folder_summary_truncations` section (schema v5,
  written with the vector at a refresh, replaced as a whole at a promotion, keys and cascades);
  `docs/usage/ingesting-documents.md`, the "Folder summaries" row adds "and the cut is recorded with its vector
  (`folder_summary_truncations`)"; `docs/usage/embedding-spaces.md`, what a promotion commits; `README.md`, "Upgrading
  to schema v5" after v4's paragraph, the table in the schema list, `schema_version` `5`;
  `docs/getting-started/installation.md`, "before schema v4" becomes v5; `migrate.py`'s v5 docstring.
- #652 (item 10): `docs/usage/ingesting-documents.md` "Where it runs": "every string and every object key in a chunk's
  metadata", with the `~1`, `~2` rule; `docs/design/architecture-overview.md` `redact.py`; the `redact_json`
  docstring; `tests/test_redact.py`'s module docstring (key cases, why collisions stay local).
- ME (items 2, 3): `docs/reference/redaction.md` as stated there.

## Testing

- #654 adds and removes no test: identical collected ids and `-rA` outcomes before and after, the empty grep, and
  `ingest` no longer having an `embed` attribute (a surviving patch raises AttributeError). No test of the seam itself:
  every converted test exercises it.
- #656: the issue's three tests (cut recorded, fit leaves none, refresh clears) plus promotion, cascade on drop,
  promote rollback and the v4-to-v5 migration, all against a real provider over the transport stub.
- Item 9: one report test. #652: three shared cases in both repositories (one pass and walk), the collision mirror,
  keys never reaching a custom leaf, storage through ingest (the acceptance's failing test), the whole-URL key, the report count.
- No benchmark: nothing here touches a hot path.

## Verification

KB, `AGENTS.md`'s Verify block, every command in order at the final head with a clean tree (no image input changes,
so its image step builds nothing):

```bash
uv lock --check
uv sync
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest tests/ -q -m "not slow"
uv sync --all-groups
uv run basedpyright src/ tests/
uv run --group docs sphinx-build -W -E -q -b html docs docs/_build
```

ME, `AGENTS.md`'s Verify block, every line, in item 1's worktree (no Containerfile, lock or plugin change, so its image
and plugin gates do not apply):

```bash
cargo build --workspace --locked
cargo fmt --all --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo build --workspace --all-features
cargo test  --workspace --all-features
cargo check -p memory-engine
cargo check -p memory-engine --no-default-features --features backend-sqlite
export RUSTDOCFLAGS="-D rustdoc::broken_intra_doc_links -D rustdoc::private_intra_doc_links"
cargo doc --no-deps -p memory-engine
cargo doc --no-deps -p memory-engine --all-features
cargo deny check
uv run --locked sphinx-build -b html -W --keep-going -E docs docs/_build
cargo +nightly fuzz build
```

Across the pair: `sha256sum tests/fixtures/redaction-cases.jsonl` prints one digest in both trees, equal to both pins;
`git show origin/main:tests/fixtures/redaction-cases.jsonl` from the ME checkout, piped to `cmp -` against KB's file,
prints nothing.

## For Michaël

1. Item 3 is a one-line ME doc fix outside "fixture only". Recommended; drop it if the ME PR must hold the fixture
   alone, and it becomes a reported ME follow-up.
2. The ME PR's review gate: Full with the lane's roster (the default here). A fixture-only PR would also fit Light.
3. Deliberately not converted (D3): the `search.embed_query`, `vision._embed_with_config` and ingest-free
   `embed_swap.get_provider` patches. None pins `embed()`'s shape, which is what blocked #651.
4. D4 follows ME for keys: a whole-URL key gets `scrub` (password and machine tokens redacted, user name kept),
   not KB's whole-URL value ruling (whole userinfo removed). Say if keys should take the stricter value rule instead.
5. Not added: a `truncated_folders` field in the re-embed and redact outputs. The table is the durable record and the
   docs name it; say if the outputs should list it as they list `truncated_chunks`.
