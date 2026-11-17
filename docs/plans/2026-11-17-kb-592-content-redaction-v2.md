# #592 — fix(ingest): redact token-shaped secrets before KB stores or embeds content (successor)

Successor of the plan approved on dutiona/knowledge-base#608 (`docs/plans/2026-11-17-kb-592-content-redaction.md`,
SHA-256 19cdd0181904645659f3249d688e660ba59ee65313d4b1d3dd64e9db02da7565). The one change of intent is Michaël's
ruling 1 below: no email rule. Ruling 2 confirms what the predecessor already planned. The rest is the predecessor
carried over, with line references refreshed after KB#588 (db27b24) and KB#573 (a518ab9) merged.

## Outcome

Every text KB stores as a chunk, or sends to the embedder for one, has passed `redact.scrub()` first: a document as a
whole before it is chunked, hashed or embedded (a PDF page by page); a figure's description before it is hashed or
embedded; every string value of chunk metadata at the insert. A fetched URL is stored without its userinfo. The rules
are memory-engine's at 97c8e77 (`crates/memory-engine/src/bootstrap/redact.rs`) without its email detector (ruling 1),
plus ADR-0020 §4's machine-token shape, and a shared fixture, whose outputs memory-engine's own code produced (with
that detector removed), holds KB's port to them byte for byte; memory-engine#1024 drops its email rule too and adopts
the same file. `knowledge-base-ingest redact` reports the stored rows redaction would change
and exits 1 when there are any; `redact --rewrite`, with the server stopped, rewrites them, merges rows that become
identical, re-embeds them, rebuilds the folder summaries they feed, and purges the old bytes from free pages, FTS
segments and the WAL. Neither prints a match.

## Constraints

- Owner decision, 2026-11-17, option a: conform now to memory-engine's current rule set
  (`crates/memory-engine/src/bootstrap/redact.rs`) plus the `me_` + 32 base62 token shape (ADR-0020), with a shared
  fixture file that memory-engine#1024 adopts later. Put the fixtures where both repos can carry byte-identical copies,
  and say how they stay in step.
- Michaël's ruling 1, 2026-11-17, on plan #608 (given in his session, relayed verbatim by the orchestrator; its first
  two sentences are posted on memory-engine#1024, comment 6955524923): "I'd drop email from the shared secret set. e-mail are not secrets
  in my book, nor for KB, nor for ME. I don't want MY mail to leak so there's a harness rule about you not sending my
  e-mail when it's requested in an API header for instance. For ME & KB, that's something else entirely." So KB has no
  email rule and the shared fixture has no email case; the ruling is also posted on memory-engine#1024, which drops
  ME's email rule. The fixture's rule set therefore deliberately diverges from memory-engine at 97c8e77 (no email
  detector); no case carries an email, so no expected output differs on one.
- Michaël's ruling 2, 2026-11-17, on plan #608 (same provenance as ruling 1), verbatim: "I accept the gap. It's too difficult today to differenciate
  secrets in URL from legitimate token as ID allowing to access data we want to store/reference. So no redaction
  either." Read as applying to every URL KB stores as a reference: `source_uri` and URL-valued metadata
  (`source_url`, `image_url`) keep their query strings and lose only their userinfo (decision 4, residual R4). A URL
  inside a document's text is text, and passes the rules like the rest of it (Michaël may narrow this at approval).
- ADR-0020 §4: "Redaction matches exactly that shape: those 35 characters with no `[0-9A-Za-z_]` on either side."
- Fixtures are example tokens only: generated shapes, never a real key. KB is public: no home path, tailnet name or
  address in any committed file, this plan included (it is archived to a public issue).
- memory-engine's worktrees are never touched; memory-engine is read through `gh api` and a scratch copy.
- Out of scope by the decision: memory-engine#1024's new prefixes (`hf_`, `tskey-`, `glpat-`, ...). They reach KB later
  through the fixture (see "Keeping the copies in step").
- Not ported: memory-engine's denylist (detector 0). Its `redact_text` runs it empty; it serves memory-engine's backfill
  of an author-known corpus, which KB does not have.
- AGENTS.md rules: TDD; Conventional, atomic commits; no real embedder, LLM or network in tests (rule 6); content-hash
  dedup kept, now over redacted text (rule 7); batched IN clauses (rule 8).
- KB#588 (db27b24) and KB#573 (a518ab9) have merged; the branch starts from a518ab9. The tripwire of item 9 names any
  insert site a later change adds.
- This ticket blocks infra#81 (KB on the tailnet). `redact` exiting 0 on the served database is KB's side of that gate
  for chunks and folder summaries; the other stores are follow-up F4.

## Decisions taken in this plan (Michaël may override at approval)

1. **The machine-token rule** is named `machine-token` (ADR-0020's "machine tokens") and runs before every other rule,
   so a leaked machine token is reported under its own name. memory-engine#1024 inherits both through the fixture.
2. **Redaction runs on the whole document before chunking.** `chunk_text` cuts fixed 1000-character windows with 200 of
   overlap, not at whitespace, so per-chunk redaction would store the head or tail of a cut token. A PDF is redacted
   page by page before `page_map` offsets are counted; that equals redacting the joined text, because the pages are
   joined by `\n\n` and no span or context check crosses a `\n` (`-----BEGIN` stops at the end of its line, the
   hex-context word is read on its own line, the other rules stop at whitespace).
3. **`scrub()` repeats one pass until the text stops changing.** One pass of memory-engine's rules is not idempotent:
   a lower-priority candidate that overlaps an earlier span is dropped whole and its remainder can match on the next
   pass, and the URL rule relabels an existing placeholder. The fixture's contract is one pass (memory-engine's
   semantics); storage uses the fixpoint. It terminates (each changing pass shrinks the text outside placeholders, apart
   from at most one relabel per placeholder); a cap of 8 passes guards the invariant and raises `KnowledgeBaseError`,
   which every ingest path already turns into "nothing was stored".
4. **Identity strings are not redacted**: a file path `source_uri` (the key of `reingest_file`, `paper_paths` and folder
   summaries), a `content://<machine>/<name>` URI, and a fetched URL beyond its userinfo. The full rules on a URL would
   collapse distinct pages (a Drive or signed-URL id meets the entropy rule) and figure cleanup, keyed on `source_uri`,
   would then delete another page's figures. A fetched URL is stored as `without_userinfo(url)` (the authority's text up
   to its last `@` removed, `http`/`https` only); a token in its query string stays (residual R4, ruling 2). The same
   holds for a metadata value that is a whole `http`/`https` URL (`source_url`, `image_url`): it is a stored reference,
   so it gets `without_userinfo` only, never the rules; every other metadata string gets `scrub()`.
5. **Conforming has user-facing consequences**, stated in the docs: base64 blobs and integrity hashes become
   `[REDACTED:high-entropy]`; a `-----BEGIN` line is redacted whatever follows; documents that differ only by a secret
   now share chunks. Email addresses are left as written (ruling 1).
6. **The rewrite takes no backup**: a backup would keep what it removes. The report lists the existing
   `<db dir>/backups/*.db` files, which still hold the old text; deleting them is Michaël's call.
7. **Rust's character classes are ported exactly with `regex`**, made a direct dependency (`regex>=2026.2.28`, the floor
   being the release V2 compares with Rust's tables, written next to the pin; it is already locked transitively). `str.isalnum()` differs from Rust's `is_alphanumeric` on Other_Alphabetic marks,
   and that difference changes whether a token is caught, not only where it ends: `sk-` plus 16 ASCII characters plus
   U+093E is 22 UTF-8 bytes in Rust's capture and 19 in a `str.isalnum` port, so only Rust redacts it. The standard
   library cannot name Other_Alphabetic; `regex` can (`\p{Alphabetic}`, `\p{White_Space}`).
8. **A reingest links a chunk another document holds** instead of failing (item 5). It changes `reingest`'s documented
   "no hash comparison" behaviour; without it, a reingest after a rewrite that merged rows fails on the UNIQUE hash.

## Shared fixture and how the two copies stay in step

- **Location.** KB: `tests/fixtures/redaction-cases.jsonl`. memory-engine#1024, proposed:
  `crates/memory-engine/tests/fixtures/redaction-cases.jsonl` (its directory is that ticket's choice; the file name and
  bytes are the contract). Nothing in it is specific to either repository.
- **Format.** UTF-8 JSON Lines, LF endings. The first line is a header object `{"about": "..."}` naming the rule set
  the outputs encode: "memory-engine redact.rs at 97c8e77 without its email detector, plus the ADR-0020 machine-token
  detector run first; owner ruling 2026-11-17: email is not a secret". It changes
  with every regeneration, so memory-engine#1024's byte-identical copy carries the divergence with it. Then one
  `{"name", "input", "output", "rules"}` object per line in that key order. Every consumer treats line 1 as the header:
  the case tests skip it, item 0's harness copies it through unchanged, and `test_fixture_is_canonical` re-encodes it; `rules` lists the rule of each replaced span in text order. Contract: one pass,
  `redact(input) == (output, rules)`. Names are kebab-case; a case taken from a `redact.rs` unit test keeps its name;
  `known-gap-*` pins memory-engine behaviour that memory-engine#1024 should change.
- **No literal token in the committed bytes.** In `input` and `output`, every `@` and the first character of every
  maximal ASCII-alphanumeric run is written as a lowercase `\u00xx` escape; beyond that, JSON's mandatory escapes only,
  in the form `serde_json` and `json.dumps(..., ensure_ascii=False)` both write (`\" \\ \b \f \n \r \t`, other C0
  controls as lowercase `\u00xx`), compact separators (`,` and `:`, no spaces), and non-ASCII stays raw UTF-8.
  That breaks every
  prefix (including one after `=`, `/` or `-`), turns every context word into `u00xx…`, and puts a `\` into every
  entropy token. Both JSON parsers restore the text; GitHub push protection and KB's own rules see nothing.
  `test_fixture_holds_no_secret` asserts every raw line is a fixpoint of `scrub()`, and
  `test_fixture_is_canonical` that re-encoding the parsed cases by this rule reproduces the file byte for byte.
- **Cases** (about 100): every input memory-engine's unit tests at 97c8e77 pass to `redact_text`, except the email
  tests (`redacts_email`, `email_with_digit_in_local_part`, `angle_bracket_email_redacted`, `localhost_not_email`,
  `bare_version_not_email`) and the email parts of mixed ones (`redacts_secrets_embedded_in_json`,
  `multibyte_tokens_no_panic`, `report_tallies_by_rule`, `multiple_findings_one_line`,
  `idempotent_placeholders_not_re_redacted`, `empty_denylist_is_backward_compatible` keep their token and lose their
  address; the multibyte case keeps its
  non-ASCII words around a provider token); each port hazard on
  both sides of its edge (U+001C..U+001F, U+00A0, U+2028, U+3000 before a prefix and in the entropy split; U+212A in the
  hex-context word; `sk-` bodies of 19 and 20 UTF-8 bytes in two-byte letters; 19 and 20 entropy characters at 4.22 and
  4.32 bits, never within 0.02 of 4.3; hex runs of 31 and 32; `-----BEGIN` before CRLF and at end of text; prefix order
  and overlaps; placeholders run again; the empty input); machine-token with 31, 32 and 33 body characters, `_`,
  alphanumeric and other characters on each side, `me_types`, the all-zero ADR-0020 test vector, inside a URL password,
  a JSON value and a `Bearer` line; Other_Alphabetic edges (`sk-` + 15 and + 16 ASCII + U+093E); `known-gap-overlap-remainder`, `known-gap-placeholder-relabel`; `url-authority-ends-at-multibyte-space`, the one case
  memory-engine@97c8e77 cannot run (it panics; collateral).
- **Who writes the outputs.** memory-engine's code, never a hand derivation: item 0's harness runs the 97c8e77 file,
  and a second build of it with `detect_email` removed from `collect_spans` and a machine-token detector first. Each
  output is read and accepted case by case.
- **Keeping the copies in step.** memory-engine owns the rules. Until memory-engine#1024 lands, KB's copy is the only
  one and the harness ties it to 97c8e77. memory-engine#1024 copies the file byte for byte and runs every case in its
  suite; from then on a rule change is a pair of PRs, memory-engine first with the regenerated file, then KB copying it
  byte for byte and porting the rule, each PR linking the other and quoting the same `sha256sum` line; neither repo edits
  its copy alone (the lockstep rule ADR-0017 already applies to its own two copies). `tests/test_redact.py`'s docstring
  names the generating memory-engine commit and changes with every copy. A mechanical cross-repository byte check would
  be stronger; neither repo's tests can reach the other, so it is follow-up F5.

## Maintenance command

`knowledge-base-ingest [--db PATH] redact` reports. It opens the database as `status` does (`get_connection` +
`init_schema`), needs no declared embedder and is safe beside a running server. A row needs rewriting when
`scrub(content) != content`, when `redact_metadata(metadata)` (item 6, the same function the insert uses) differs
from it, or when `without_userinfo(source_uri)`
differs; a folder summary when `scrub(summary) != summary`. It prints JSON: rows scanned; rows to rewrite per field;
spans per rule (one pass, a span already equal to its placeholder not counted); the ids of the chunks to rewrite; folder
summaries to rebuild; `rewrite_incomplete` (the `config` row `redact_rewrite_pending` exists: an earlier `--rewrite`
committed but did not finish its rebuild and purge); the backup files that still hold old text. It exits 1 when
anything needs rewriting or `rewrite_incomplete` is true, 0 when clean. It never prints content, metadata, a URI, a
summary or any part of a match.

`knowledge-base-ingest [--db PATH] redact --rewrite` runs with the server stopped (documented, as for `re-embed`; #596
covers enforcement) and opens through `open_declared_db`, because it re-embeds:

0. Before any write of its own, refuse when `PRAGMA wal_checkpoint(TRUNCATE)` reports busy: a reader that holds the
   database is caught early, before step 3, and the refusal changes nothing. This is an early refusal, not an
   exclusion: an idle connection, or one that starts after the check, is not caught, and `open_declared_db` may already
   have rebound config rows (as for `re-embed`). Stopping the server stays the operator's step.
1. Plan in memory, in id order: new content (`scrub`), hash, metadata (`redact_metadata`) and `source_uri`
   (`without_userinfo`) per row; a row whose new hash another row
   holds (already, or earlier in the plan) merges into that row.
2. Embed every content that changed (a row whose only change is metadata or `source_uri` keeps its vector) through
   `_embed_with_config` before any write: an embedder failure changes nothing.
3. One `BEGIN IMMEDIATE` transaction: update rewritten rows (the `chunks_au` trigger rewrites `chunks_fts`); merge the
   others with `_merge_into(conn, dup_id, keep_id)`; delete the vectors of every content-changed or merged row (never a
   metadata-only or `source_uri`-only one) from every
   `embed_spaces.table_name` through `delete_chunk_vecs(..., table_name=t)`, which decrements that space's
   `chunk_count`; insert the new vectors into the active space through `insert_chunk_vec`, which increments it (a
   populating space refills through `backfill_space`, which counts them again; a deprecated one keeps the gap); delete
   the folder summaries (and their vectors) that hold a match or cover a rewritten chunk; insert the `config` row
   `redact_rewrite_pending`. Commit.
4. Rebuild the folder summary of every folder that holds a file chunk, with `update_folder_summary` (it skips a folder
   whose hash is unchanged and commits per folder), from redacted chunks.
5. `purge_deleted_content(conn)` (item 3), then delete `redact_rewrite_pending`, commit, and checkpoint again.
6. Print the report with `rewritten`, `merged`, vectors removed per space and `folders_rebuilt`; exit 0.

Interruption: everything a run must still do after step 3's commit is derivable from the database (the pending row,
folders without a current summary, the purge), so the report keeps exiting 1 until a later `--rewrite` (which, with
nothing left to rewrite, runs steps 4 and 5 only) clears the row. A second `redact` after a complete run exits 0.

`_merge_into` moves every reference to `chunks.id` that `pragma_foreign_key_list` names (papers, relationships,
methods, datasets, metrics, entity_mentions, prediction_errors, chunk_sessions) with `UPDATE OR IGNORE`, deletes what a
UNIQUE constraint held back (duplicate mentions and sessions), rewrites `conclusions.source_chunk_ids` (JSON, not a
foreign key) without duplicates, then deletes the row. Ingest-time dedup would have produced the same state.

## Implementation sequence

- [ ] **0. Pin "current" and build the oracle** (no commit; in the ticket's private scratch directory, outside both
  repositories).
  - `gh api 'repos/dutiona/memory-engine/contents/crates/memory-engine/src/bootstrap/redact.rs?ref=main' --jq .sha`
    equals the same call at `ref=97c8e77`; if not, stop and ask: the decision named the current rule set.
  - A Cargo binary compiles the unmodified 97c8e77 file with `serde_json`, maps `{"name","input"}` lines to
    `{"name","output","rules"}` through `redact_text` under `catch_unwind`, and runs memory-engine's own unit tests on
    that copy (`cargo test`, count quoted in the PR). A second build removes `detect_email` from `collect_spans` (ruling
    1) and adds the machine-token detector first; it writes the fixture's outputs. Both run under the machine-wide heavy-build lock.
- [ ] **1. Port memory-engine's rules, held to the shared fixture.**
  - Files: `src/knowledge_base/redact.py` (new), `tests/fixtures/redaction-cases.jsonl` (new),
    `tests/test_redact.py` (new), `pyproject.toml` and `uv.lock` (`regex` made direct; `uv lock` must leave the
    locked `regex` version unchanged).
  - Red: `uv run pytest tests/test_redact.py -q` fails at collection (`No module named 'knowledge_base.redact'`).
    Tests: `test_redact_matches_the_shared_case[<name>]`; `test_fixture_holds_no_secret`;
    `test_url_authority_ending_at_a_multibyte_space_does_not_crash`; `test_redact_json_keeps_keys_and_non_strings`
    (memory-engine's two JSON tests); `test_email_is_not_redacted` (ruling 1: an address alone, in a JSON value and
    beside a token, unchanged); `test_redaction_is_linear_on_hostile_input` (1 MiB each of `=eyJ`, `://u:p@`,
    `token=<32 hex> `, `-----BEGIN ` on one line and `_<32 hex>` in one word repeated, each well under 10 s; a literal
    port of the overlap scans, the to-end-of-line `-----BEGIN` scan and the backward context-word walk is quadratic,
    and `ingest_text` takes 4 MiB over HTTP).
  - Change: a pure module (standard library and `regex`, no KB import). `redact(text: str) -> tuple[str, list[str]]` is one
    pass: detectors in memory-engine's order (provider-prefix, url-credential, hex-in-context, high-entropy; no email,
    ruling 1) with its constant names and values, non-overlapping spans, earlier
    detector wins, `[REDACTED:<rule>]`. `scrub(text: str) -> str` is the bounded fixpoint.
    `redact_json(value: Any, leaf: Callable[[str], str] = scrub) -> Any` applies `leaf` to every string leaf, keys
    kept (memory-engine's `redact_json_strings`; item 6 passes the URL-aware leaf). Port rules, each pinned by a case:
    whitespace is `regex`'s `\p{White_Space}` (never `str.isspace`, `\s`, bare `split()`/`strip()`, which add
    U+001C..U+001F); alphanumeric is `[\p{Alphabetic}\p{Nd}\p{Nl}\p{No}]`, Rust's `is_alphabetic() || is_numeric()`
    (decision 7; case `sk-` + 16 ASCII + U+093E is redacted); hex, digit and ASCII tests are ASCII sets;
    the hex-context word is lowercased ASCII-only (U+212A stays itself); the 20-byte prefix minimum measures
    `len(s.encode())`, the 20-character entropy minimum `len(s)`; offsets are code points, so the URL scan resumes one
    character past a multibyte terminator where memory-engine panics; `split("\n")`, never `splitlines()`; overlap
    checks bisect the sorted spans (the same answer as memory-engine's linear scan).
  - Green: the same command, plus the harness agreeing with every case (V1).
  - Commit: `feat(redact): port memory-engine's redaction rules, without email, held to a shared fixture`
- [ ] **2. Redact the machine-token shape.**
  - Files: `src/knowledge_base/redact.py`, `src/knowledge_base/auth.py`, the fixture, `tests/test_redact.py`.
  - Red: the machine-token cases of the fixture section; `uv run pytest tests/test_redact.py -q -k machine-token`
    fails. Two item-1 cases change output (the random token was `high-entropy`; the all-zero vector was unchanged).
  - Change: `TOKEN_SHAPE` moves from `auth.py` to `redact.py`, which `auth.py` imports and still exports; the first
    detector is `(?<![0-9A-Za-z_])me_[0-9A-Za-z]{32}(?![0-9A-Za-z_])` (ASCII), rule `machine-token`.
  - Green: `uv run pytest tests/test_redact.py tests/test_auth.py tests/test_http_auth.py -q`; the unpatched harness
    differs from the fixture on exactly the cases whose `rules` contain `machine-token` (so this commit's fixture diff is
    ADR-0020 §4's rule and nothing else).
  - Commit: `feat(redact): redact the ADR-0020 machine-token shape`
- [ ] **3. Prove the purge before anything depends on it.**
  - Files: `src/knowledge_base/db.py`, `tests/test_redact_store.py` (new).
  - Red: `test_purge_leaves_no_deleted_text_in_the_file`: a file database with `wal_autocheckpoint=0` holds chunks in
    two spaces and folder summaries with vectors; rows are deleted in each vec table and in `folder_summaries_vec` to
    leave gaps; one chunk's content carries a 40-character example secret and is updated, its vector deleted,
    committed, purged. The bytes of `<db>` and `<db>-wal` hold no 12-character window of the secret and no 16-byte
    window of the old vector; `PRAGMA integrity_check` is `ok`; a KNN query for every remaining vector, in every vec
    table, returns its own row (VACUUM may renumber a vec0 shadow table's rowids).
    `uv run pytest tests/test_redact_store.py -q -k purge` fails at the import, then, against a helper that only
    commits, on the byte scan.
  - Change: `purge_deleted_content(conn: sqlite3.Connection) -> None`: refuses an open transaction; runs
    `INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')`, commits, `VACUUM`, `PRAGMA wal_checkpoint(TRUNCATE)`
    (in WAL mode `VACUUM` writes the clean pages to the WAL, and the main file keeps the old ones until this
    checkpoint); raises `KnowledgeBaseError("another connection holds the database; stop the server and rerun")`
    unless the checkpoint reports busy 0. If the old vector's bytes survive (sqlite-vec keeping a deleted slot) or a
    KNN lookup breaks after `VACUUM`, it also rebuilds each space's vec0 table and `folder_summaries_vec` before
    `VACUUM`; the PR says which branch the test required.
  - Green: the same command.
  - Commit: `feat(db): purge deleted text from free pages, FTS segments and the WAL`
- [ ] **4. Redact a document before it is chunked, hashed or embedded.**
  - Files: `src/knowledge_base/ingest.py` (`ingest_text`, `_extract_markdown_text`, `_extract_pdf_text`,
    `_extract_pdf_markdown`), `tests/test_redact_paths.py` (new).
  - Red: `uv run pytest tests/test_redact_paths.py -q`, secrets from fixture cases, `stub_document_embedder` recording
    every text: `test_ingest_text_neither_stores_nor_embeds_a_secret` (through the MCP `ingest_text` tool in process,
    once over HTTP with the `kb_http` fixture of `tests/conftest.py`, and through `ingest_url` with
    `http_requests`, `fake_dns`, `public_net`: no stored content, FTS term or embedded text holds a
    12-character window of the secret; each `content_hash` is `content_hash(content)`);
    `test_a_token_across_a_chunk_window_never_survives` (a token starting 15 characters before offset 1000; per-chunk
    redaction fails it too); `test_documents_identical_after_redaction_store_one_chunk`;
    `test_file_ingest_redacts_markdown_code_and_pdf` (`.md`; a `.py` with `API_KEY = "<token>"`, AST chunks kept; a PDF
    through a monkeypatched `pymupdf4llm.to_markdown`; the flat `fitz` fallback); `test_pdf_page_map_counts_redacted_text`
    (page 2 starts at `len(scrub(page1)) + 2`).
  - Change: `ingest_text` chunks `scrub(text)`; each `_extract_*` returns scrubbed text, the PDF markdown path page by
    page before its offsets.
  - Green: the same command, then `uv run pytest tests/ -q -m "not slow"`; an existing expectation that a rule now
    matches takes the placeholder, the rule is never weakened.
  - Commit: `fix(ingest): redact a document before it is chunked, embedded or stored`
- [ ] **5. Let a reingest link a chunk another document holds** (boy-scout, own commit; redaction and the merge make
  this sharing common).
  - Files: `src/knowledge_base/ingest.py` (`reingest_file`), `tests/test_ingest.py`.
  - Red: `test_reingest_links_a_chunk_another_document_holds`: two files sharing their first chunk, the second with a
    paper, a method and two historical sessions on its rows; reingesting it raises `sqlite3.IntegrityError` today.
    After the fix it succeeds, the shared chunk carries both historical sessions, and the paper's abstract and the
    method are re-linked (to the shared chunk when it is the one that matches), with `session_id=None` and with a new
    session. `uv run pytest tests/test_ingest.py -q -k reingest_links`.
  - Change: `_produce_and_insert_chunks` returns the deduplicated chunks as `(chunk_index, id)` pairs in document order,
    whatever `session_id` is (today it collects ids only when `session_id` is set, `ingest.py:405`); its callers do the
    session linking. `reingest_file` calls it with `deduplicate=True` (its own rows are already gone, so the probe
    finds only other documents' chunks), links every deduplicated chunk to all of its sessions (`all_sessions`, not
    only `session_id`), and re-links papers and entities over the document's chunks in document order, its new rows and
    the deduplicated ones together, instead of only the rows found by `source_uri`; `ingest_file` keeps linking
    `session_id` as before. The comment at
    `ingest.py:388-389` that says reingest passes `deduplicate=False` is updated with it.
  - Green: `uv run pytest tests/test_ingest.py -q -k reingest`.
  - Commit: `fix(ingest): link a chunk another document holds instead of failing the reingest`
- [ ] **6. Redact chunk metadata at the insert.**
  - Files: `src/knowledge_base/ingest.py` (`_insert_chunk`), `src/knowledge_base/utils.py` (`without_userinfo`, which
    `_sanitize_url` now calls for its userinfo step), `tests/test_redact_paths.py`, `tests/test_utils.py`.
  - Red: `test_chunk_metadata_is_redacted_keys_kept` (a page title and an AST name carrying secrets; keys unchanged;
    a clean metadata string stored byte for byte); `test_metadata_urls_keep_their_query`: `source_url` and `image_url`
    values `https://reader:<password>@docs.example.test/a?sig=<token-shaped value>` are stored as
    `https://docs.example.test/a?sig=<the same value>` (ruling 2), and so are `HTTPS://<bare token>@docs.example.test/a`
    and `Http://reader:<password>@…` (schemes are case-insensitive). `uv run pytest tests/test_redact_paths.py -q -k
    "metadata"`.
  - Change: `redact_metadata(metadata: str) -> str` in `ingest.py` returns
    `json.dumps(redact_json(json.loads(metadata), _metadata_leaf))` when that differs, else `metadata` unchanged;
    `_metadata_leaf(s)` is `without_userinfo(s)` when `s` is a whole `http`/`https` URL (`re.fullmatch(r"(?i)https?://\S+", s)`, so a trailing `\n` does not
    qualify), else
    `scrub(s)`. `_insert_chunk` stores `redact_metadata(metadata)`; the report and the rewrite (items 10-11) call the
    same function, so a URL query stored after this change is never reported or altered. Metadata is neither hashed
    nor embedded, so the insert is its one complete point. `without_userinfo(url: str) -> str` drops the authority's
    text up to its last `@` for a case-insensitive `http`/`https` scheme and returns anything else unchanged; item 7
    reuses it.
  - Green: the same command.
  - Commit: `fix(ingest): redact chunk metadata before it is stored`
- [ ] **7. Store a fetched URL without its userinfo.**
  - Files: `src/knowledge_base/web.py` (`ingest_url`), `tests/test_redact_paths.py`.
  - Red: `uv run pytest tests/test_redact_paths.py -q -k "url or identity"`:
    `test_ingest_url_stores_the_url_without_userinfo` (`https://reader:<password>@docs.example.test/page`: every stored
    `source_uri`, the result's `source_uri` and every stored `image_url` read without userinfo; the result's `url`
    echoes the caller's input; a bare-token userinfo too; a re-ingest dedupes under that identity); `test_source_uris_keep_their_identity` (a URL with a dotless high-entropy query value, a file
    path with an `sk-` directory, and a `content://` name are stored byte for byte; `reingest` still finds the file).
  - Change: `ingest_url` computes
    `source_uri = without_userinfo(url)` once and passes it to every storage and stale-cleanup key (`ingest_text`, the
    three figure functions, the result's `source_uri`); `image_url` and `source_url` metadata lose their userinfo at
    the insert (item 6); fetching and rendering still use `url` and the response's URLs.
  - Green: the same command.
  - Commit: `fix(web): store a fetched URL without its userinfo`
- [ ] **8. Redact figure text before it is hashed or embedded.**
  - Files: `src/knowledge_base/web.py` (`_extract_html_images`, `_extract_web_figures` after the OmniParser merge,
    `_extract_element_captures`), `src/knowledge_base/vision.py` (`_persist_figures`, after
    `_assemble_figure_content`), `tests/test_redact_paths.py`.
  - Red: `test_figure_text_is_neither_stored_nor_embedded[<site>]` for the four sites, with the existing vision patches
    (`knowledge_base.vision._vision_call`, `_get_vision_config`, `knowledge_base.ingest.embed`): description, title,
    caption and OCR text carry secrets; neither stored content and metadata nor embedded texts do.
    `uv run pytest tests/test_redact_paths.py -q -k figure`.
  - Change: each site scrubs its final text where it collects the texts to embed, so the embedded text, the hash and the
    stored content are one string.
  - Green: the same command.
  - Commit: `fix(ingest): redact figure text before it is embedded or stored`
  - Then, as its own commit, folder summaries: `_build_folder_summary` prefixes each snippet with the file name of a
    `source_uri` that stays unredacted (decision 4), cuts content at 200 characters and joins lines, each of which can
    create a match the document did not have, and `update_folder_summary` embeds and stores the result.
    Red: `test_folder_summary_is_a_fixpoint` (a file name carrying a machine token; a chunk whose `api_key:` and 32 hex
    digits sit on two lines; a snippet whose 200-character cut leaves a dotless high-entropy head of a dotted token; `knowledge_base.folder_summaries.embed` patched to
    record: the stored and embedded summary satisfy `scrub(s) == s`). Change: `_build_folder_summary` returns
    `scrub(...)`. Files: `src/knowledge_base/folder_summaries.py`, `tests/test_redact_paths.py`. Commit:
    `fix(ingest): redact a folder summary before it is embedded or stored`
- [ ] **9. Fail on any chunk insert site without a redaction case.**
  - Files: `tests/test_redact_paths.py`.
  - Red: none needed beyond the assertion itself: `test_every_chunk_insert_site_is_covered` walks
    `src/knowledge_base` with `ast`, collects the functions that call `_insert_chunk`, and requires them to equal the
    keys of a map from caller to its leak test; no other source may match `INSERT INTO chunks\b` outside `db.py`'s
    migrations and `_insert_chunk`'s own statement. Shown red by deleting one map entry locally (not committed): the message names the site and says to
    scrub before hashing and add a leak case.
  - Green: `uv run pytest tests/test_redact_paths.py -q -k insert_site`.
  - Commit: `test(ingest): fail when a chunk insert site has no redaction case`
- [ ] **10. Report the stored rows redaction would change.**
  - Files: `src/knowledge_base/redact_store.py` (new; `report`), `src/knowledge_base/indexer.py` (`cmd_redact`, the
    `redact` sub-parser, dispatch), `tests/test_redact_store.py`, `tests/test_indexer.py`.
  - Red: `uv run pytest tests/test_redact_store.py tests/test_indexer.py -q -k "report or subcommands"`:
    `test_report_counts_without_printing_matches` (rows seeded by raw SQL as before the upgrade: content, metadata, a
    URL with userinfo, a folder summary; `main(["--db", db, "redact"])` exits 1 with the right counts and ids; stdout
    and stderr hold no 8-character window of any secret; the database bytes are unchanged);
    `test_report_on_a_clean_database_exits_zero` (it holds a row stored through the new insert path whose metadata URL
    keeps a token-shaped query value: exit 0, nothing reported); `test_report_flags_a_metadata_url_with_userinfo` (a
    pre-existing raw row whose metadata URL carries userinfo and a token-shaped query: `redact` exits 1 and reports it);
    `redact` joins
    `test_build_parser_subcommands`.
  - Change: `report(conn: sqlite3.Connection) -> dict` as under "Maintenance command"; `cmd_redact` prints it and exits
    1 or 0.
  - Green: the same command.
  - Commit: `feat(indexer): report stored rows that redaction would change`
- [ ] **11. Rewrite them in place, server stopped.**
  - Files: `src/knowledge_base/redact_store.py` (`rewrite`, `_merge_into`), `src/knowledge_base/indexer.py`
    (`--rewrite`), `tests/test_redact_store.py`.
  - Red: `uv run pytest tests/test_redact_store.py -q -k rewrite`, a vector stub derived from each text's hash patched
    at `knowledge_base.ingest.embed` and `knowledge_base.folder_summaries.embed`:
    `test_rewrite_merges_rows_identical_after_redaction` (a raw row and its redacted twin, plus two raw rows that redact
    alike, referenced from every table `_merge_into` names and from `conclusions.source_chunk_ids`; afterwards one row
    per text, every reference on it, conclusion ids deduplicated, sessions united, no `IntegrityError`);
    `test_rewrite_reembeds_from_redacted_text` (the active vector replaced from redacted text only; a populating space
    lost the vector and `backfill_space` refills it; a deprecated space lost it and the output counts it; after the
    rewrite and again after the backfill, every space's `chunk_count` equals its vec table's row count, and
    `_check_promotable` refuses the populating space before the backfill and accepts it after; a metadata-only and a
    `source_uri`-only rewrite keep their vectors in every space and are not sent to the embedder);
    `test_rewrite_resumes_after_an_interruption[<stage>]` (a failure injected after step 3's commit, during the summary
    rebuild, before and after `VACUUM`: `redact` then exits 1 with `rewrite_incomplete`, a second `--rewrite` completes,
    and `redact` exits 0);
    `test_rewrite_leaves_no_secret_bytes` (no window of any seeded secret in the database or its WAL; an FTS `MATCH` on
    a secret's term finds nothing; the folder summary rebuilt; `redact` exits 0; a second `--rewrite` changes nothing);
    `test_rewrite_strips_metadata_userinfo_and_keeps_query` (the same raw row: `redact --rewrite` exits 0, strips the
    userinfo, keeps the query and keeps the row's vectors; `redact` then exits 0);
    `test_rewrite_embedder_failure_changes_nothing` (`stub_document_embedder` down: exit 1, the report unchanged);
    `test_rewrite_refuses_while_another_connection_reads` (exit 1, every chunk, vector and folder-summary row unchanged by
    `iterdump()`; file bytes may move, since a busy checkpoint can still copy WAL frames).
  - Change: `rewrite(conn: sqlite3.Connection) -> dict` and `_merge_into(conn, dup_id: int, keep_id: int) -> None` as
    under "Maintenance command"; `--rewrite` (`store_true`, help "server stopped") opens through `open_declared_db`.
  - Green: the same command.
  - Commit: `feat(indexer): rewrite stored secrets in place with the server stopped`
- [ ] **12. Document it** (files under Documentation). Green: `uv run pytest tests/test_lean_surface.py -q` (it compares
  the stdio and HTTP tool descriptions), then the merge gate. Commit:
  `docs(ingest): describe redaction, the shared fixture and the redact command`

Operational steps: the worktree is the orchestrator's locked `.worktrees/fix-592-content-redaction`; the plan issue is
published once by `archive-plan-issue.sh` before approval; the PR closes #592 and references `dutiona/infra#37`;
`/super-review` at the Full gate; `/finish-pr` to READY. The merge is Michaël's, through the orchestrator.

## Documentation

- `docs/usage/ingesting-documents.md`: a "Redaction" section after "Deduplication": the rules in priority order with
  their placeholders; where redaction runs (whole document, figure text, metadata, URL userinfo) and what is not
  transformed (file paths, `content://` names, URL query strings); the promise (known formats caught, an unprefixed
  password not guaranteed, ADR-0020 §6); the consequences of decision 5; the maintenance run (stop the unit, `redact`,
  read the counts, `redact --rewrite`, `redact` exits 0, decide about the listed backups, rotate any real credential
  the counts imply, start the unit); the shared fixture and the lockstep rule. "Deduplication" says hashes are of
  redacted text; "Re-Ingestion" says a chunk another document holds is linked.
- `docs/reference/mcp-tools.md`, and the `ingest_text` and `ingest_url` docstrings in `src/knowledge_base/routes/ingestion.py`
  (model-facing): "Token-shaped secrets and URL passwords are stored as `[REDACTED:<rule>]`; a URL is
  stored without its userinfo."
- `README.md`: `redact` beside `re-embed` and `rebind` among the server-stopped maintenance commands, with a residue
  note like the API-key one (backups made earlier keep the text).
- `docs/design/architecture-overview.md` and `AGENTS.md` Project Layout: `redact.py`, `redact_store.py`. `AGENTS.md`
  Rules gain: "9. **Redaction** — text stored as chunk content passes `redact.scrub()` before it is hashed or embedded;
  a new insert site needs a leak case in `tests/test_redact_paths.py`. `tests/fixtures/redaction-cases.jsonl` is shared
  byte for byte with memory-engine: change it only through a linked pair of PRs."
- Hand-off outside any commit: the orchestrator relays to memory-engine#1024 the fixture path, its `sha256sum`, the
  machine-token name and position, and the collateral findings for memory-engine.

## Testing

- Unit: `tests/test_redact.py` (every shared case on exact output and rules, fixture hygiene, the multibyte-terminator
  case, JSON leaves, linear time). The fixture is the port's unit-test corpus, so any case added for memory-engine is
  one KB runs.
- Integration: `tests/test_redact_paths.py` (each write path, the chunk-window straddle, identity, the insert-site
  tripwire), `tests/test_redact_store.py` (purge, report, rewrite, merge, byte scans), and the extended
  `tests/test_ingest.py` and `tests/test_indexer.py`. A leak assertion checks stored columns, FTS, every text the
  embedder received and, in the store tests, the raw bytes of the database and its WAL, by fixed-size windows so a
  partial secret fails too.
- End-to-end over HTTP: N/A as a separate level: the MCP `ingest_text` tool is called in-process in item 4, and the HTTP
  transport adds no content path.
- No real embedder, LLM or network: `stub_document_embedder`, a hash-derived vector stub for byte scans,
  `http_requests`, `fake_dns`, `public_net`, the existing vision patches. Secrets come from fixture cases or are built at
  run time; no test source holds a token literal.
- Not tested, by design: Unicode-version differences between Rust and `regex` (V2) and storage below SQLite (R1).

## Verification

- **V1, the fixture equals memory-engine.** Item 0's checks, then: the patched build (no email, machine-token first)
  agrees with every case; the unpatched build agrees with every case without `machine-token` except the panicking one,
  which also proves no case depends on the email detector (ruling 1). Quoted in the PR: both blob SHAs,
  the `cargo test` count, the two summaries, `sha256sum tests/fixtures/redaction-cases.jsonl`.
- **V2, differential run.** 200,000 generated inputs (fixed seed; fragments: each prefix and `me_`, `://`, `@`, `:`,
  `=`, `.`, `/`, quotes, brackets, `_`, `-`, `+`, hex and base62 runs, the hex-context keys, `\n`, `\r`, space, tab,
  U+001C, U+00A0, U+3000, `é`, U+212A, U+093E, U+0345, U+24B6) through the patched build and `redact()`. Zero
  differences outside one counted class, memory-engine's panic (an entropy within 1e-9 of 4.3 is memory-engine's own
  `HashMap`-order noise and is set aside, counted). A residual the sample cannot rule out, stated in
  `redact.py`: a character whose Unicode properties differ between the Unicode versions of Rust's standard library and
  of `regex` (a character assigned in one version and not the other). Any other difference is a port bug: fix it and
  add its case. Not committed; counts in the PR.
- **V3, real data, with Michaël's OK only.** `knowledge-base-ingest --db <copy> redact` on a copy of the served
  database, never the live one and never with `--rewrite`, so decision 5's consequences are measured before the hub
  rewrites anything. Counts go to Michaël, not into the public PR.
- **Merge gate** (AGENTS.md "Verify"), every command in order at the PR's final head with a clean tree, `pytest` under
  the machine-wide heavy-build lock, results quoted in the PR:

```bash
sudo apt-get update && sudo apt-get install -y tesseract-ocr  # once per host: without it the OCR tests skip
uv lock --check
uv sync
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest tests/ -q -m "not slow"
uv sync --all-groups
uv run basedpyright src/ tests/
```

## Residuals and follow-ups

Accepted residuals (documented):

- R1. Below SQLite: filesystem or SSD remanence, `VACUUM`'s temporary file, page cache, swap; earlier backups and any
  operator copy.
- R2. What left before the upgrade: texts already sent to the embedder or a vision model; journal lines.
- R3. A row stored before #592 can keep a fragment of a token a chunk window cut, shorter than every rule's minimum;
  the rewrite sees one chunk at a time. Re-ingesting a file or URL source removes it; a `content://` document has no
  source, and a document whose every chunk was merged into another document's has no rows left under its
  `source_uri`, so `reingest` answers NotFound and the file must be ingested again instead.
- R4. A token in a fetched URL's query string stays in `source_uri` (decision 4).
- R5. memory-engine's gaps inherited by conformance, pinned as `known-gap-*` cases.
- R6. Content false positives (decision 5); a PDF image reference whose file name starts with a provider prefix loses
  its link; a Python file whose redaction breaks `ast.parse` falls back to fixed chunks.
- R7. A deprecated embedding space loses the rewritten rows' vectors; promoting it back needs `re-embed`.
- R8. A chunk shared between documents belongs to one `source_uri`, so reingesting that owner deletes it, and if the
  owner's edit dropped it the other document's text is no longer searchable (a paper abstract linked to it is re-linked
  to the owner's new first chunk). The mechanism predates this ticket (ingest-time dedup); decision 5, `_merge_into`
  and item 5 make it more likely. The fix is a chunk-to-source membership table (F6).

Follow-ups for the orchestrator to file (not built here):

- F1 (memory-engine, commented on memory-engine#1024): `detect_url_credential` panics when a URL authority ends at a multibyte whitespace (U+00A0,
  U+3000); reproduced on 97c8e77. Once memory-engine#1024 redacts every write path, that is a crash on ordinary input.
- F2 (memory-engine, commented on memory-engine#1024): one pass is not idempotent (overlap remainder,
  placeholder relabel) although its doc comment says it is. Not yet on that comment: the overlap scan, the
  to-end-of-line `-----BEGIN` scan and the backward context-word walk are quadratic on hostile input.
- F3 (knowledge-base, Refs #609): `delete_chunks_cascade` and the web figure cleanups delete vectors from the active space only,
  so other spaces keep orphan vectors and overcount `chunk_count` (read in code; to reproduce).
- F4 (knowledge-base, Refs #610): redaction of the other write paths, for parity with memory-engine#1024's every-write-path
  rule: the logged search query (`prediction_errors`), HTTP-mode log lines beyond `me_` shapes, and the stdio-written
  stores (papers, conclusions, executions, extraction descriptions).
- F5 (commented on memory-engine#1024): a mechanical byte check that the two fixture copies are equal.
- F6 (knowledge-base, Refs #611): a chunk-to-source membership table, so a reingest deletes only chunks no other source holds
  (R8); a schema change for its own PR. Whether it must land before infra#81 is Michaël's call.
