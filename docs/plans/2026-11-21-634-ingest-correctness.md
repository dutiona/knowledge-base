# #634: ingest correctness, group D1 (#597, #598, #626, #631, #633)

One PR, one commit per member plus separate boy-scout commits. Synthesized from three lens drafts: the single storage
owner and the order of commits from risk-first; the gone-file `reingest` message, the dropped folder summary, the
schema-first deletion owner and one owner of a tool call's failure from architecture-first; the per-site conversion of
returned errors, the fixture case shape that leaves both text runners untouched, and the green-first #633 position from
mvp-first. Round 1 of the plan review (Codex) moved the call boundary from connection hand-out to the tool's own thread.

## Outcome

Every new document KB stores, by content, by URL or from a file of any type, is stored by one function in one
transaction: any failure stores nothing of it and leaves no transaction open, and each web figure writer replaces its
own figures atomically (a writer that fails keeps its previous figures). Every tool call, stdio or HTTP, ends with no
transaction open on its connection, whether it failed or not. Every stdio tool reports a failure as an MCP tool error
carrying the text it returned before, and a test that walks all 47 registered tools through an in-process MCP client
fails on any `{"error": ...}` success. KB's JSON walk redacts a value of 32 or more hex digits whose
key's last word is a context word, exactly as memory-engine's does, pinned by JSON cases in the shared fixture
(memory-engine's PR first, same sha256). `knowledge-base-ingest remove <path>` removes a file document whose file is
gone, under the store lock, through the one deletion path; `reingest` of such a file names `remove`; a file that exists
is still refused toward `reingest`. A chunk deletion through a connection opened before another connection's DDL
succeeds because the deletion owner reads the schema before it writes, and every chunk or paper row deletion stays in
that owner.

## Constraints

- #634: "One PR that closes these five issues, each in its own commit." "Each member's own acceptance, unchanged. The PR
  carries one `Closes #N` line per member." "Refs dutiona/infra#37"
- #597: "A failed insert midway through a file ingest leaves no rows behind; a test injects the failure."
- #597: "The non-PDF file path converges onto `ingest_text`, so one function owns the transaction."
- #598: "Every stdio tool reports failure as a tool error, the way #543's three HTTP tools do."
- #598: "A test walks the registered tools and fails on any `{"error": ...}` success result."
- #626: "KB's JSON walk treats the key name as the leaf's context, with the same rule ME adopts in #1024 (same
  placeholder)."
- #626: "A shared fixture case covers it; the fixture change lands as a linked ME-then-KB pair quoting the same
  `sha256sum` (the pairing rule from dutiona/knowledge-base#592), unless #1024's KB half already carries it." It does
  not: both copies are sha256 `87d16a42195fdc108b32f07916fd343f7cec185d5304fa266e56442d6eb0f9b7`, text cases only.
- memory-engine#1103 decision 11: "a string leaf that is entirely a run of 32+ hex characters is redacted as
  `hex-secret` when its key reads as a context word the way the text rule reads one: the key's last word (alphanumerics
  and `_`), split on `_`, holds a context key (`api_key`, `access-token`, `X-Api-Key`, …)."
- #631: "A file document whose path no longer exists can be removed by its stored `source_uri` (through `remove` or
  `reingest`), with the same lock, every-space vector cleanup and reference cleanup as `remove`."
- #631: "Tests cover a deleted file, a moved file, and an existing file (which still goes through `reingest`)."
- #633: "A failing test first: connection A opened, connection B creates a space, a delete through A succeeds."
- #633: "The fix covers every path that resolves a space's tables through a long-lived connection (reingest, remove,
  re-embed)."
- Route (Michaël, confirmed): Bug fix / Standard / Super / Full, Default severity.
- AGENTS.md rule 2 (TDD), rule 5 (thread-local connections), rule 8 (batched IN clauses), rule 9 ("a new insert site
  needs a leak case in `tests/test_redact_paths.py`"; the fixture changes "only through a linked pair of PRs,
  memory-engine first"). #629 D5: `remove` "embeds nothing, so it opens without a declaration". #629 D6:
  `delete_chunks_cascade` is the only deletion path. #543 D6: a `KnowledgeBaseError` raises `ToolError(str(e))`.
- D2 (#635) runs in parallel and changes `embed_swap.py` (#620) and `embeddings.py` (#605). This plan edits no function
  in either file; it calls `embed_swap.create_space`, `re_embed` and `declared_provider_config` unchanged. Files both
  PRs may edit: `src/knowledge_base/routes/embeddings.py` (items 5 and 7), `tests/test_embed_spaces.py`,
  `tests/test_embed_swap.py`, `tests/test_routes_embeddings.py`, `docs/reference/mcp-tools.md`, `README.md`. The second
  to land rebases and reruns the whole gate.
- Public repository: no tailnet names, home paths or tokens committed. No `git stash`; nothing written to the member
  issues; branch commits carry no co-author line; the worker never merges.

## #633: what was measured, and the position

Pure SQLite (3.53.1 in the worktree venv, 3.46.1 system), no KB code: a table with an FTS5 external-content index whose
AFTER DELETE trigger writes the `'delete'` command, a child table with a foreign key to it, `foreign_keys=ON`.
Connection A reads; connection B runs any DDL (`CREATE TABLE zz(x)` suffices) and commits; if A's next statement is a
`DELETE` on that table, it fails `no such table: chunks`. Dropping the trigger or the foreign keys makes it pass; a
retry passes; `BEGIN IMMEDIATE` before it does not help; any prior statement that reads the schema (`SELECT` on a
table, `SELECT 1 FROM sqlite_master`) does. INSERT and UPDATE on `chunks` pass. KB's `chunks` and `papers` both match.
On 528d170 every production path already passes through an old connection (`remove_document` and `reingest_file`
start with a SELECT, `delete_chunks_cascade` with `UPDATE papers`; `redact_store._merge_into`'s bare DELETE runs under
`held_exclusively`, where no other connection exists): by statement order, not by design. So the issue's red cannot come
through reingest, remove or re-embed. Position: make the order a property of the owner (it reads the schema first),
keep every chunk and paper deletion in it (a tripwire), pin the three paths with tests that assert success (they hold
whether or not SQLite is fixed), never a test asserting SQLite's failure.

## Owner decisions (the sequence assumes each recommendation)

- **O1 (#597) What "converges onto `ingest_text`" covers. Recommended: `ingest_text`'s own storage step,
  `_store_document`, becomes the one owner of every new document: text by content, a page's text, and files of every
  type (PDF and `.py` AST chunks included); a plain-text file is also chunked by `ingest_text`'s own `_text_chunks`.**
  This keeps the criterion's purpose ("so one function owns the transaction") and changes its letter: a file does not
  call `ingest_text` itself, it reaches the two functions `ingest_text` is made of. Approving O1 is that amendment.
  What you see: no stored chunk changes (pinned by guard tests for a markdown and a non-Python code file); one function
  embeds, inserts and commits every new document, and the two near-identical copies of dedup, embed and insert become
  one (the copy that lacked #553's in-batch collapse was a live defect #543 fixed). `.py` files keep their AST chunks
  and per-chunk metadata, which `ingest_text`'s one metadata dict per document cannot carry. *Alternative (the letter):*
  `ingest_file` calls `ingest_text` for plain-text files only; PDF and `.py` keep a second owner with its own rollback.
- **O2 (#597's class) A tool call that ends with a transaction open. Recommended: one call boundary on every tool,
  `routes.tool(mcp)` in place of `@mcp.tool()`, running on the tool's own thread: a call that raises rolls back its
  connection's open transaction; a call that returns with one open logs a WARNING and rolls it back.** Today any write
  that fails inside a tool (not only #597's writers: `papers.register_paper`'s two inserts under one commit, the
  `reingest` tool's edge invalidation before `submit_job`, any single failed statement, which still opens the implicit
  transaction) leaves the thread's connection holding its transaction and SQLite's write lock: other threads' writes
  wait out the 30 s busy timeout and fail, and that thread's next commit stores the partial rows. What you see: every
  call ends clean the moment it ends; a write someone forgot to commit is logged and lost instead of stored by accident.
  Item 7 gives the same boundary the `KnowledgeBaseError` to tool-error mapping (#543 D6), so a tool's failure has one
  owner. *Alternatives:* roll back at hand-out in `_conn._get_conn()` (cheaper, but the lock stays until that thread's
  next call); or fix only #597's writers and send the others to Collateral findings.
- **O3 (#631) The removed file's folder summary. Recommended: dropped in the removal's transaction** (its folder's
  `folder_summaries` and `folder_summaries_vec` rows), rebuilt by the folder's next ingest, reingest or
  `redact --rewrite`. What you see: `remove` still embeds nothing and needs no declaration (#629 D5 holds); the removed
  file's first passage leaves the summary at once; the folder's other documents lose their folder boost until the
  rebuild. *Alternative:* rebuild it in the transaction: boost kept, but every file removal then needs the declared
  embedder, and an embedder failure removes nothing.
- **O4 (#631) What a removed file document leaves. Recommended: its `paper_paths` row and a PDF's extracted images on
  disk stay.** What you see: a paper registered at the gone path keeps its record and path. The workflows differ and the
  docs say so: a registered paper's file that moved is moved with `relocate_paper_tool`, which rewrites its chunks'
  `source_uri` and its path together and keeps everything; `remove` is for a file that is gone for good, or a moved file
  that is no paper. After `remove`, the paper's chunks are gone, its abstract link is cleared, and a later relocation
  moves only the record, so re-ingesting the file at its new place is what brings its text back. Deleting the path row
  would make even that relocation impossible. The images are unsearchable PNGs under the data directory, found only by
  the file's content hash, which a gone file cannot give. *Alternative:* record the image directory at ingest (a schema
  change) and delete it on remove.
- **O5 (#633) Close it without a red step. Recommended: close it with item 3** (the schema-first owner, the tripwire,
  the green-first path tests, the reproduction quoted in the commit body). This amends #633's first criterion ("A
  failing test first"), which the measurement shows no KB path can satisfy on 528d170; approving O5 is that amendment.
  What you see: nothing changes at runtime; a future bare DELETE on `chunks` or `papers` fails the tripwire, which says
  why. *Alternatives:* keep #633 open until SQLite fixes it: item 3 lands with `Refs #633`, and since #634's acceptance
  needs all five members closed, the PR also says `Refs #634` instead of `Closes #634` and states that it closes four
  of #634's five members, #634 staying open on #633; or refresh the schema at connection hand-out, rejected: another
  connection's DDL can still land between that read and the DELETE.
- **O6 (#633) Upstream. Recommended: Michaël posts the pure-SQLite reproduction (six statements, no project data) to
  the SQLite forum, or OKs the orchestrator posting it.** Nothing in KB depends on it.
- **O7 (#626) The fixture's JSON cases. Recommended: cases named `json-…`, each a JSON document whose JSON-walk output
  equals its one-pass text output**, so both repositories' text runners and canonical encoders pass every line
  unchanged and each side adds only a JSON runner over the `json-` cases; the header's `about` gains one sentence. What
  you see: the smallest change to the shared contract. Its limit: a case where the walk and the text pass disagree (a
  hex value followed by other text under `api_key`, a key ending in `:`) cannot be shared; each repository pins those
  in its own unit tests (memory-engine's `a_hex_leaf_is_redacted_when_its_key_is_a_context_word`, KB's item 9 tests).
  *Alternative:* a `"walk":"json"` field; both encoders, both runners and the canonical-form checks change, and such
  cases become shareable.
- **O8 (#626) If memory-engine's PR is not final when items 0 to 7 are reviewed. Recommended: this PR waits** and goes
  READY only with memory-engine's final bytes; Michaël merges memory-engine first. *Alternative:* Michaël changes the
  confirmed route and moves #626 to its own KB PR (the worker does not make that change).

## Implementation sequence

`H` = `flock /run/user/1000/memlane-heavy.lock`; every pytest command runs as `H uv run pytest …`.

- [ ] **0. The plan on the branch.** This plan in the branch's `docs/plans/`, with its altitude report and review
  files beside it (the lens drafts are never committed). No executable behaviour. Commit:
  `docs(plans): plan #634, ingest correctness`.

- [ ] **1. Boy-scout: docstrings that promise an `{"error": ...}` return.** Files: `src/knowledge_base/extraction.py`
  (`estimate_extraction_time`), `src/knowledge_base/vision.py` (`estimate_figures_time`): each names the
  `NotFoundError`/`ValidationError` it raises. No executable behaviour. Green: `uv run ruff check src/`. Commit:
  `docs: say what the time estimates raise instead of an error dict`.

- [ ] **2. Boy-scout: stale references.** Files: `README.md` (config table: `schema_version` default `2` → `3`,
  `migrate.CURRENT_SCHEMA_VERSION` since #579), `docs/usage/ingesting-documents.md` (Redaction's
  `crates/memory-engine/src/bootstrap/redact.rs` → `crates/memory-engine/src/redact/mod.rs`, moved by
  memory-engine#1024; `redact.py`'s pointer "at 97c8e77" is history and stays), `src/knowledge_base/indexer.py`
  (`cmd_reingest`'s "mirrors routes/ingestion.py:83-99" names the `reingest` tool, not lines). No executable behaviour.
  Commit: `docs: correct the schema default, memory-engine's redaction path and a line reference`.

- [ ] **3. #633: the deletion owner reads the schema before it writes.**
  - Files: `src/knowledge_base/db.py` (`delete_chunks_cascade`), `tests/test_db.py`, `tests/test_embed_spaces.py` (the
    comment at the second connection of `test_backfill_writes_nothing_for_a_chunk_changed_while_it_embeds`).
  - Tests: `test_a_connection_older_than_a_new_space_still_deletes[cascade|remove|reingest|re-embed]`: a store with a
    URL document (`ingest_text`) and a markdown file (`ingest_file`), embedders stubbed as the suite does; A opened and
    read; B runs `create_space(B, "s", TEST_EMBED_MODEL, TEST_EMBED_DIM)` and commits; then, as A's first statement
    after B's DDL: `delete_chunks_cascade` of the URL document's ids and commit; `remove_document(url)`;
    `reingest_file(file)` with changed text; `re_embed` (provider patched at `knowledge_base.embed_swap.get_provider`).
    Each returns; a fresh connection sees the chunks gone and their vectors gone from every space, the file's new
    text in place of its old one, and for `re_embed` the space it returns active and holding a vector per chunk
    (`re_embed` creates its own space; B's `s` only changes A's schema).
    `test_chunk_and_paper_rows_are_deleted_only_by_their_owner`: an AST walk of `src/knowledge_base` (like
    `test_every_chunk_insert_site_is_covered`) finds `DELETE FROM chunks` or `DELETE FROM papers` only in
    `db.delete_chunks_cascade` and `redact_store._merge_into`; its message names #633 and the skipped reference cleanup.
  - Red: none reachable on 528d170 (O5): `H uv run pytest tests/test_db.py -q -k "older_than_a_new_space or
    only_by_their_owner"` shows 5 passed before the change; the commit body quotes the pure-SQLite reproduction and
    both versions instead.
  - Change: `delete_chunks_cascade` reads its spaces' tables (the existing `embed_spaces JOIN sqlite_master` query)
    first, before any write, and its docstring says why in one sentence (#633). The test comment states the real
    condition: its raw writer's first statement is a bare DELETE, which any other connection's DDL breaks.
  - Green: the same command, 5 passed; `H uv run pytest tests/test_db.py tests/test_embed_spaces.py tests/test_indexer.py
    -q` passes.
  - Commit: `fix(db): read the schema before the first chunk deletion`, body ending `Closes #633`.

- [ ] **4. #597: one function stores every new document; file ingest and figure writes roll back.**
  - Files: `src/knowledge_base/ingest.py`, `src/knowledge_base/web.py`, `tests/test_ingest.py`,
    `tests/test_redact_paths.py`, `docs/design/architecture-overview.md`, `docs/reference/mcp-tools.md`.
  - Guard, green before and after (`tests/test_ingest.py`): `test_a_text_file_is_stored_as_its_text_would_be[markdown|code]`:
    a markdown file, and a non-Python code file (`.rs`, the plain-text branch), through `ingest_file`, then its text
    through `ingest_text` under another URI: 0 added, every chunk skipped.
  - Red, the failure injected as `sqlite3.OperationalError("disk I/O error")`:
    - `tests/test_ingest.py::test_a_failed_file_ingest_leaves_no_rows[markdown|python|pdf]`:
      `knowledge_base.ingest._insert_chunk` fails on
      its second call (a multi-chunk markdown file; a `.py` with two functions; a two-page PDF through
      `tests/test_redact_paths.py`'s `pymupdf4llm` stub); `ingest_file(conn, f, session_id="s")` raises; `not
      conn.in_transaction`; after `conn.commit()` no `chunks`, `chunk_sessions` or vector row of the file exists.
    - `tests/test_redact_paths.py::test_a_failed_figure_write_changes_nothing[html-images|web-figures|element-captures|stale-cleanup]`
      (beside the `_run_*` drivers it uses): a stale
      figure with a vector in the writer's index range; `knowledge_base.web._insert_chunk` fails on its first call
      (`stale-cleanup`: `knowledge_base.db.delete_chunk_vecs` fails in `_cleanup_stale_inline_images`); the writer
      raises, `not conn.in_transaction`, the stale figure and its vector are intact.
    - `tests/test_redact_paths.py::test_a_figure_set_replaced_by_known_figures_is_committed[web-figures|element-captures]`:
      the vision stub
      describes only figures whose text another chunk holds; the stale figure is deleted, nothing added, and a fresh
      connection sees the deletion (today it stays open: those writers commit only `if figures_added`).
    - `tests/test_ingest.py::test_ingest_url_keeps_its_figures_when_their_write_fails`: a page with one `<img>` and a
      stored inline figure;
      `web._insert_chunk` fails; `ingest_url` returns the text stored and `figures_extracted == 0`, and a fresh
      connection still sees the old figure (today `ingest_text`'s commit stores the half-replaced set).
    - `H uv run pytest tests/test_ingest.py tests/test_redact_paths.py -q -k "failed_file_ingest or
      failed_figure_write or known_figures or keeps_its_figures"` fails, each test for its own reason (checked one by
      one): an open transaction and the rows stored by the next commit; a lone delete left open.
  - Change:
    - Rule (#554's pattern): the function that commits a write catches `BaseException`, rolls back and re-raises.
    - `ingest.py`: `_text_chunks(conn, clean: str) -> list[str]` (already-redacted text: `_chunk_text`, each window
      scrubbed, `_bounded_chunks`, each piece scrubbed; `[]` before the limit lookup when nothing chunks);
      `_file_chunks(conn, path, source_type) -> tuple[list[tuple[int, str, str, str]], str]` (the file's
      `(chunk_index, text, content_hash, metadata_json)` items and its chunk strategy: the PDF and `.py` AST branches
      lifted unchanged, every other file through `_text_chunks`, each item a scrub fixpoint);
      `_insert_chunks(conn, items, *, source_uri, source_type, chunk_strategy, session_id=None, session_ids=None) ->
      tuple[int, int, list[tuple[int, int]]]` (in-batch collapse, stored-hash probe, every new chunk embedded before the
      first write, inserts, one `bump_chunk_count`; commits nothing); `_store_document(conn, items, *, source_uri,
      source_type, chunk_strategy, session_id=None) -> tuple[int, int]` (`_insert_chunks`, the already-stored chunks
      linked to `session_id`, one commit, rollback on any failure). `ingest_text` = `_store_document` over
      `_text_chunks(conn, scrub(text))` with its one metadata JSON on every item, strategy `mechanical`; `ingest_file` =
      `_store_document` over `_file_chunks`, then `duplicate_of_paper_id` and the folder summary as today;
      `reingest_file` keeps its transaction: cascade, `_file_chunks`, `_insert_chunks(session_ids=…)`, relinks.
      `_produce_and_insert_chunks`, `_store_text_chunks` and the always-true `deduplicate` flag go. Signatures of
      `ingest_text`, `ingest_file` and `reingest_file` unchanged.
    - `web.py`: the write phase (stale delete, inserts, bump) of `_extract_html_images`, `_extract_web_figures`,
      `_extract_element_captures` and `_cleanup_stale_inline_images` runs under the rule and commits unconditionally;
      the `if figures_added:` guards go. Downloads, vision and embedding stay before it. `ingest_url` still logs a
      failed figure phase and continues.
    - `INSERT_SITES`: the two old `ingest.` keys become `ingest._insert_chunks`, mapped to the existing file-ingest leak
      case; `test_reingest_rolls_back_on_midway_failure` patches `_insert_chunks` with its assertions unchanged.
  - Green: the red command passes; `H uv run pytest tests/test_ingest.py tests/test_redact_paths.py tests/test_indexer.py
    tests/test_lean_surface.py tests/test_routes_ingestion.py tests/test_web_redirects.py tests/test_web_limits.py
    tests/test_multi_figure.py tests/test_vision.py tests/test_folder_summaries.py tests/test_embed_limits.py -q`
    passes with no other existing assertion edited (an edit there means the
    convergence changed stored rows: stop and report).
  - Commit: `fix(ingest): roll back a failed file ingest instead of leaving rows in the open transaction`, body ending
    `Closes #597`.

- [ ] **5. Every tool call ends with no transaction open (#597's class, O2).**
  - Files: `src/knowledge_base/routes/__init__.py` (`tool`), the six `src/knowledge_base/routes/*.py` modules (each
    `@mcp.tool()` becomes `@tool(mcp)`), `tests/test_server.py`, `docs/design/architecture-overview.md`.
  - Red, `tests/test_server.py`, through `fastmcp.Client(server.mcp)` on a declared store:
    `test_a_failed_tool_call_leaves_no_transaction_open`: `register_paper_tool` with `papers.register_paper`'s second
    insert made to fail (`sqlite3.OperationalError`); the call is an error; then the test's own connection, with
    `PRAGMA busy_timeout = 0`, writes at once and sees no paper row. `test_a_tool_that_returns_with_a_transaction_open_is_rolled_back`:
    a tool body patched to insert a row without committing; the call succeeds, the row is absent from another
    connection, one WARNING names the tool. `test_every_tool_is_registered_through_the_boundary`: an AST walk of
    `src/knowledge_base/routes/` finds no function decorated with `mcp.tool` directly.
    `H uv run pytest tests/test_server.py -q -k "transaction_open or through_the_boundary"` fails: `database is
    locked` on the immediate write, the row present, direct decorators found.
  - Change: `routes.tool(mcp: FastMCP) -> Callable[[F], F]`: registers `fn` through `mcp.tool()` wrapped by
    `functools.wraps` (same name, signature and annotations, so the published schema is unchanged; pinned by the
    existing schema and surface tests). The wrapper runs on the tool's own thread: if the call raises and the thread's
    connection (`_conn._local.conn`, when one exists) is in a transaction, it rolls back and re-raises; if the call
    returns with one open, it logs a WARNING naming the tool and rolls back. `NETWORK_TOOLS` are the same functions, so
    HTTP calls get it too. No library function changes.
  - Green: the red command passes; `H uv run pytest tests/test_server.py tests/test_lean_surface.py tests/test_http_auth.py
    tests/test_routes_ingestion.py tests/test_routes_papers.py tests/test_thread_safety.py -q` passes, the tool counts
    staying 47 and 3.
  - Commit: `fix(mcp): end every tool call with no transaction open`, body naming the instances (O2) and `Refs #597`.

- [ ] **6. #631: remove a file document whose file is gone.**
  - Files: `src/knowledge_base/ingest.py` (`remove_document`, `reingest_file`), `src/knowledge_base/folder_summaries.py`
    (`drop_summary`), `src/knowledge_base/indexer.py` (`cmd_reingest`, the `remove` parser),
    `src/knowledge_base/routes/ingestion.py` (`reingest`), `tests/test_indexer.py`, `README.md`,
    `docs/usage/ingesting-documents.md`.
  - Red, through `indexer.main`, embedders stubbed:
    - `test_remove_a_deleted_files_document`: a markdown file with vectors in the active space and a backfilled second
      space, a paper whose abstract is its chunk, a conclusion citing it and another document's chunk; file deleted;
      `remove <its source_uri>` prints `{"source_uri": …, "chunks_deleted": N}`; no chunk, FTS match or vector of it in
      either space, each space's `chunk_count` equals its rows, the abstract is NULL, the conclusion cites the other
      chunk only, its folder's summary rows are gone, the paper and its `paper_paths` row stay.
    - `test_remove_a_moved_files_document`: `a/doc.md` ingested, moved to `b/doc.md`; `remove <a's uri>` exits 0;
      `ingest b/doc.md` adds every chunk. A registered paper's moved file is `relocate_paper_tool`'s case (its tests in
      `tests/test_routes_papers.py` stand); the deleted-file test pins what `remove` leaves of a paper (O4).
    - `test_reingest_of_a_gone_file_names_remove`: exit 1 naming `knowledge-base-ingest remove`, chunks intact.
    - `test_remove_refuses_a_file_that_exists` (#629's `test_remove_refuses_a_file_document`, renamed, assertions kept:
      exit 1 naming `reingest`, `iterdump()` unchanged); `reingest` of it then exits 0. Guard, green before.
    - `H uv run pytest tests/test_indexer.py -q -k "files_document or gone_file or file_that_exists"`: the deleted and
      moved files are refused as file documents; the reingest refusal does not name `remove`.
  - Change: `remove_document(conn, source_uri) -> dict`, signature unchanged. A URI (after `without_userinfo`) that is
    none of `http://`, `https://`, `content://` is a file document, refused only while `Path(uri).exists()`:
    `ValidationError("<uri> exists: reingest replaces a file document from its file; remove takes a URL, a content://
    document, or a file document whose file is gone")`. Otherwise as today (`NotFoundError` when nothing is stored),
    one transaction: `delete_chunks_cascade`, then for a file document `folder_summaries.drop_summary(conn,
    <parent as ingest stored it>)` (both rows, commits nothing; `update_folder_summary`'s empty-folder branch calls it
    too), commit, rollback on any `BaseException` (O3, O4). `reingest_file` owns the missing-file check: a stored
    document whose file is gone raises `NotFoundError("<uri> no longer exists: remove its document with
    knowledge-base-ingest remove <uri> (server stopped)")`, a path neither stored nor present `NotFoundError("Path does
    not exist: <p>")`; the CLI's and the tool's own `exists()` checks go. The lock is unchanged: `remove` is in #629's
    writing table. Help: subcommand `Remove one document and its chunks: a URL, a content:// document, or a file that
    no longer exists (server stopped).`; the description adds `A file document is removed only once its file is gone
    (deleted or moved); reingest replaces one that exists. Its folder's summary is dropped until the folder's next
    ingest or redact --rewrite; a registered paper keeps its record and path (move a registered paper's file with
    relocate_paper_tool instead: it keeps its chunks), and images extracted from the file stay on disk.`; the argument
    help ends `, or the absolute path of a file that no longer exists`.
  - Green: `H uv run pytest tests/test_indexer.py tests/test_ingest.py tests/test_folder_summaries.py
    tests/test_routes_ingestion.py tests/test_db.py -q` (item 3's tripwire included: no new deletion site).
  - Commit: `fix(ingest): remove a file document whose file is gone`, body ending `Closes #631`.

- [ ] **7. #598: every stdio tool reports failure as a tool error.**
  - Files: `pyproject.toml` and `uv.lock` (`jsonschema` in the dev group), `src/knowledge_base/routes/__init__.py`
    (the boundary's mapping),
    `src/knowledge_base/routes/{ingestion,embeddings,extraction,papers,operations}.py`,
    `src/knowledge_base/exceptions.py`, `src/knowledge_base/papers.py` (`relocate_paper`'s mismatch message),
    `tests/test_server.py`, `tests/test_routes_{ingestion,embeddings,extraction,papers,operations}.py`,
    `tests/test_embed_identity.py`, `tests/test_embed_swap.py`, `tests/test_vision.py`, `tests/test_lean_surface.py`,
    `docs/reference/mcp-tools.md`, `docs/usage/prediction-errors.md`.
  - Red:
    - `test_no_stdio_tool_reports_a_failure_as_a_result` (`tests/test_server.py`) on a declared empty store
      (`declared_db`, `stub_embedder`, `stub_document_embedder`, `http_requests`, `fake_dns`; the LLM and vision callers
      patched so nothing leaves the process). Through `fastmcp.Client(server.mcp)` in process, `raise_on_error=False`,
      it calls every tool `list_tools()` returns once, with arguments built from its published `inputSchema`: an enum
      its first value, an `anyOf` its first non-null branch, a string a path under `tmp_path` that does not exist,
      integers and numbers their `minimum` or 0, booleans false, arrays empty, optional ones omitted. Before each call
      the test asserts its arguments validate against that `inputSchema` (`jsonschema.validate`; `jsonschema` is
      declared in the dev group, already locked at 4.26.0 as `mcp`'s dependency), so FastMCP accepts them and the call
      reaches the tool's body: the walk cannot pass on arguments refused before it, and it needs no assumption about
      what a body does first (`ingest_text` checks its name and `sync_bibtex_tool` its path before opening a
      connection; both are their bodies refusing, which is a tool error). A result that is not `isError` and parses to
      a JSON object holding `error` fails the test, naming the tool; it asserts 47 tools were walked. Calls run on FastMCP's worker threads;
      item 5's boundary leaves no transaction or lock behind between them.
    - Per branch: each of the 32 converted sites keeps a test asserting its tool error and text, the existing route
      tests rewritten from `["error"]` to `pytest.raises(ToolError, match=<the same text>)` and one new test for any
      site that has none. `test_a_domain_error_reads_as_its_message[stdio|network]` (`tests/test_lean_surface.py`): a
      library call patched to raise `NotFoundError("marker refusal")` reads exactly `marker refusal`, through
      `Client(server.mcp)` for a stdio-only tool that catches nothing today (`get_paper_tool`'s `get_paper`; red: stdio
      prefixes an uncaught exception with `Error calling tool`), and through `Client(server.network)` for
      `search_index` (a guard, green today: the three HTTP tools convert explicitly).
    - `H uv run pytest tests/test_server.py tests/test_lean_surface.py tests/test_routes_ingestion.py
      tests/test_routes_embeddings.py tests/test_routes_extraction.py tests/test_routes_papers.py
      tests/test_routes_operations.py -q -k "reports_a_failure or domain_error or error"` fails: the walk names at
      least `ingest`, `reingest`, `get_job_status_tool` and `create_embed_space_tool`; the stdio domain-error case reads
      the prefix; the rewritten branch tests see a returned result instead of a raise.
  - Change: the boundary (item 5) re-raises a `KnowledgeBaseError` as `ToolError(str(e))` (#543 D6, now in one place);
    each route's `except KnowledgeBaseError: return json.dumps({"error": …})` block goes, and each explicit
    `return json.dumps({"error": …})` raises with the same text: `NotFoundError` or `ValidationError` for a KB refusal
    (`Path does not exist: …`, `Job … not found`, `Invalid strategy …`), `ToolError(…) from e` where it rephrases a
    foreign exception (`ValueError` from `embed_swap`, `OSError` from the bib tools). `extract_structure_tool`'s
    `ExtractionError` raises the JSON a failed extraction job records (`{"error"[, "errors"][, "raw"]}`) as its text.
    `KnowledgeBaseError.details` goes: its only producer is `relocate_paper`'s hash mismatch, whose two hashes move into
    its message, and its only reader the `{"error": …, **e.details}` merge, so the one client-visible loss is
    `relocate_paper_tool`'s two separate hash keys, now in the message text; test constructors passing `details=`
    (`tests/test_routes_ingestion.py`) drop it; the `exceptions.py` docstring says tools raise these as tool errors.
    Unchanged: `benchmark_spaces_tool`'s per-space `{"space", "error"}` entries inside a successful result, a job's
    stored `error` column, the three HTTP tools' own mappings (httpx, `IntegrityError`).
  - Green: the red command passes; `H uv run pytest tests/test_server.py tests/test_routes_ingestion.py
    tests/test_routes_embeddings.py tests/test_routes_extraction.py tests/test_routes_papers.py
    tests/test_routes_operations.py tests/test_routes_search.py tests/test_embed_identity.py tests/test_embed_swap.py
    tests/test_vision.py tests/test_papers.py tests/test_lean_surface.py tests/test_http_auth.py -q`;
    `git grep -n 'json.dumps({"error"' -- src/knowledge_base/routes` prints nothing.
  - Commit: `fix(mcp): report stdio tool failures as tool errors, not as successful results`, body ending `Closes #598`.

- [ ] **8. memory-engine: the shared JSON-key cases (its own PR, first).** In the memory-engine worktree the
  orchestrator creates after approval, under memory-engine's own route and Verify list; not a commit here.
  - Files: `tests/fixtures/redaction-cases.jsonl` (cases appended through the `#[ignore]`d canonical rewriter; the
    header's `about` gains: `A case named json-... holds a JSON document: each repository's JSON walk must also turn it
    into its output, a string member of 32 or more hex digits whose key's last word, split on underscores, holds a
    context word becoming [REDACTED:hex-secret].`), `tests/redaction_conformance.rs` (`FIXTURE_SHA256`),
    `crates/memory-engine/src/redact/tests.rs` (`fixture_json_cases_pass_the_json_walk`: reads the fixture under
    `CARGO_MANIFEST_DIR/../../tests/fixtures/`; for every `json-` case, `scrub_json` of the parsed input equals the parsed
    output; fails when no such case exists). No production change: `key_has_context` and `scrub_member` already hold
    the rule.
  - Cases, `<hex>` = `0123456789abcdef0123456789abcdef` (4.0 bits per character, under the high-entropy threshold):
    redacted to `[REDACTED:hex-secret]`: `json-key-api-key-redacts-hex` `{"api_key":"<hex>"}`,
    `json-key-last-word-redacts-hex` `{"X-Api-Key":"<hex>"}`, `json-key-access-token-redacts-hex`
    `{"access-token":"<hex>"}`, `json-key-nested-member-redacts-hex` `{"auth":{"token":"<hex>"}}`; kept:
    `json-key-sha-keeps-hex` `{"sha":"<hex>"}`, `json-key-ending-in-key-keeps-hex` `{"monkey":"<hex>"}`,
    `json-key-starting-with-key-keeps-hex` `{"keyboard":"<hex>"}`, `json-array-element-keeps-hex`
    `{"api_key":["<hex>"]}`, `json-short-hex-keeps-hex` `{"api_key":"<31 hex>"}`. Each is also its own one-pass text
    output (the text rule skips `"` and `:` back to the same word and stops at `[`); the existing text runners confirm
    it (O7).
  - Red: the new bytes fail `fixture_is_the_shared_copy` until `FIXTURE_SHA256` moves; the JSON test is the conformance
    half and passes at once. `CARGO_BUILD_JOBS=16 H cargo test -p memory-engine --all-features --test
    redaction_conformance`. Green: that and `… --lib redact::` report 0 failed; memory-engine's merge gate passes.
  - PR body: the new `sha256sum`, the KB PR link, "merges before knowledge-base", `Refs dutiona/knowledge-base#626`,
    `Refs dutiona/infra#37`; no closing keyword. Commit: `test(redact): share JSON-key hex cases with knowledge-base`.

- [ ] **9. #626: the JSON walk reads a member's key as its value's context.** Made once item 8's bytes are final; a
  change to them in item 8's review is redone here.
  - Files: `tests/fixtures/redaction-cases.jsonl` (item 8's bytes, `cmp` clean), `tests/test_redact.py`,
    `src/knowledge_base/redact.py`, `tests/test_redact_paths.py`, `docs/usage/ingesting-documents.md`.
  - Red: the bytes copied and `FIXTURE_SHA256` set to item 8's digest (the module docstring names the memory-engine PR
    as the `json-` cases' source); `test_the_json_walk_matches_the_shared_json_cases[<each json- case>]`:
    `redact_json(json.loads(input)) == json.loads(output)`; `test_metadata_hex_under_a_context_key_is_redacted`
    (through `ingest._insert_chunk`, where `redact_metadata` runs): `{"api_key": <32 hex>, "commit": <40 hex>}` is
    stored as `{"api_key": "[REDACTED:hex-secret]", "commit": <the same 40 hex>}`; local parity tests in
    `tests/test_redact.py`, beside memory-engine's own: `test_a_hex_value_is_redacted_when_its_key_is_a_context_word`
    (keys `api_key`, `access-token`, `X-Api-Key`, `secret`, `client_secret_v2`, `clé_token` redact; `sha`, `commit`,
    `monkey`, `keyboard`, `tokené`, `api_key:` keep), `test_the_key_rule_reads_no_array_element_and_only_its_own_member`
    (`{"api_key": [<hex>]}` and `{"api_key": {"v": <hex>}}` keep; `{"auth": {"token": <hex>}}` redacts),
    `test_the_key_rule_needs_a_whole_hex_value` (31 hex digits, and 32 then ` rotated`, keep), and
    `test_the_key_rule_runs_before_a_custom_leaf` (`redact_json(…, leaf)` never hands the redacted value to `leaf`; a
    whole-URL value under `url` keeps its URL through `_metadata_leaf`). `H uv run pytest tests/test_redact.py
    tests/test_redact_paths.py -q -k "json_walk or context_key or key_rule or context_word"`: the redacting cases and
    the metadata test fail; the keeping cases and every text case pass.
  - Change: `redact_json(value, leaf=scrub)`, signature unchanged: an object member whose value is a string of at least
    `HEX_SECRET_MIN_LEN` characters, all ASCII hex digits, under a key `_key_has_context(key) -> bool` accepts, becomes
    exactly `[REDACTED:hex-secret]`; every other value is walked as today; array elements read no key.
    `_key_has_context` ports memory-engine's `key_has_context`: the key's trailing run of `_ALNUM` characters and `_`,
    read backwards (linear), split on `_`, has a part `_is_key` accepts (`_ALNUM` is Alphabetic, Nd, Nl, No: Rust's
    `char::is_alphanumeric`).
  - Green: the red command passes; `H uv run pytest tests/test_redact.py tests/test_redact_paths.py
    tests/test_redact_store.py tests/test_ingest.py -q`; `sha256sum tests/fixtures/redaction-cases.jsonl` prints item
    8's digest.
  - Commit: `fix(redact): redact a hex secret whose only context word is its JSON key`, body quoting the digest and
    linking the memory-engine PR, ending `Closes #626`.

## Documentation

- Item 2: `README.md` config table; `docs/usage/ingesting-documents.md` Redaction's source path.
- Item 4: `docs/design/architecture-overview.md`, the `ingest.py` paragraph: one function stores every new document in
  one transaction that rolls back on failure, and each figure writer replaces its figures the same way.
  `docs/reference/mcp-tools.md` `ingest`: a file that fails stores nothing; a directory stops at the first such file and
  keeps the files before it.
- Item 5: `docs/design/architecture-overview.md`, the connection section: every tool call ends with no transaction open
  on its thread's connection (rolled back on failure; a call that returns with one open is logged and rolled back).
- Item 6: `README.md` maintenance bullet "Removing a document" (URL, content, or a file that is gone) and Limitations;
  `docs/usage/ingesting-documents.md` Re-Ingestion (`reingest` of a gone file names `remove`) and Removing a Document
  (the gone-file rule; a moved file: remove the old path, then ingest the new one, since ingested first it stores
  nothing new; a registered paper's moved file goes through `relocate_paper_tool` instead; the dropped folder summary;
  what stays, O4). Runbook delta for infra#81 in the PR body: a gone file's
  document is removed with `knowledge-base-ingest remove <its path>`, unit stopped, no declaration needed.
- Item 7: `docs/reference/mcp-tools.md` Surfaces: "The other tools return failures as `{"error": ...}` results."
  becomes "Every tool reports a failure as a tool error whose text says why; a successful result is never an
  `{"error": ...}` object (a benchmark's per-space `error` entry belongs to a successful result)."
  `docs/usage/prediction-errors.md`: an unknown ID fails with a tool error naming it.
- Item 9: `docs/usage/ingesting-documents.md` Redaction, the `hex-secret` row: "or, in a chunk's metadata, a whole value
  of 32 or more hex digits under a key whose last word is such a word (`api_key`, `X-Api-Key`)".
- No ADR and no schema change.

## Testing

Behaviour tests at each issue's seam on per-test databases with the suite's embedder stubs: library functions for
transactions and redaction, `indexer.main` for `remove` and `reingest`, the in-process MCP client
(`fastmcp.Client(server.mcp)`, and `server.network` for the shared mapping) for the call boundary and tool errors, two
connections on one file for #633. Failures are injected where the code calls by module name
(`ingest._insert_chunk`, `web._insert_chunk`, `db.delete_chunk_vecs`); every failure test also asserts `not
conn.in_transaction` and inspects the store through a fresh connection or after `conn.commit()`. Guards green before
and after are named as such (text-file equivalence, existing-file refusal, #633's paths). Tripwires: `INSERT_SITES`,
the deletion-owner walk, the boundary walk, the 47-tool walk (its arguments schema-checked before each call, so no
call is refused before its body), both fixture pins. Not tested on purpose: SQLite's failing first-statement
DELETE (it would break when SQLite is fixed; the commit body carries it), FastMCP's error wording, a removed PDF's
images (O4). Integration and end-to-end levels add nothing here: every defect lives in a library function or a tool
wrapper reachable in-process.

Acceptance to proof: #597 → `test_a_failed_file_ingest_leaves_no_rows`, the figure tests; its class at the call
boundary → item 5's tests; its convergence (O1) → `_store_document` as the one owner, `INSERT_SITES`, the
`[markdown|code]` guard. #598 → the 32 conversions with their per-branch tests,
`test_no_stdio_tool_reports_a_failure_as_a_result`, the both-hubs test. #626 → the JSON-walk cases, the local parity
tests, the metadata test, the same digest in both repositories. #631 → the deleted, moved and existing-file tests, the reingest message;
the lock is `remove`'s own (#629's `test_a_writing_command_refuses_while_the_store_is_held[remove]`). #633 →
`test_a_connection_older_than_a_new_space_still_deletes` (green first, O5) and the owner tripwire.

The red commands select by keyword; each new test's failure is checked for its own reason, not for the selection's
overall result.

## Accepted, not changed (surfaced in the PR)

- `redact`'s report counts a metadata row's rules from its leaves alone, so a value only the key rule redacts shows in
  `rows_to_rewrite` and `chunk_ids` but not in `spans`; no key KB writes is a context word (metadata keys are KB's own
  constants), and `--rewrite` still fixes such a row.
- `reingest_file` embeds inside its transaction, as today.
- The PDF image directory is cleared before extraction, outside the transaction; the same bytes regenerate it.
- The call boundary is our own wrapper, registered through `mcp.tool()`, not a FastMCP middleware: a middleware runs
  on the event loop, not the tool's thread (it cannot reach the thread's connection), and would rely on FastMCP
  chaining the cause of its own `ToolError`, an incidental behaviour of the dependency.
- A page's three figure writers each replace their own index range atomically; a later writer that fails leaves the
  earlier writers' new figures in place, which are complete sets of their own.

## Verification

At the final head, clean tree, in AGENTS.md's order, each result quoted in the PR:

```bash
uv lock --check
uv sync
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
flock /run/user/1000/memlane-heavy.lock uv run pytest tests/ -q -m "not slow"
uv sync --all-groups
uv run basedpyright src/ tests/
```

Expected: every command exits 0; ruff `All checks passed!` and `… files already formatted`; pytest no failure or error;
basedpyright `0 errors, 0 warnings, 0 notes`. Then:

- `git -C <memory-engine worktree> show HEAD:tests/fixtures/redaction-cases.jsonl | cmp - tests/fixtures/redaction-cases.jsonl`
  prints nothing, and `sha256sum tests/fixtures/redaction-cases.jsonl` equals both repositories' `FIXTURE_SHA256`.
- `git grep -n 'json.dumps({"error"' -- src/knowledge_base/routes` prints nothing; `git diff --stat origin/master --
  src/knowledge_base/embed_swap.py src/knowledge_base/embeddings.py` prints nothing.
- Added lines carry no home path or tailnet name: `git diff origin/master | grep '^+' | grep -nE '/h[o]me/|\.ts\.n[e]t'`
  prints nothing.

## Operational steps

- [ ] **Worktree:** exists and is locked, branch `fix/634-ingest-correctness` from origin/master at 528d170; reuse it.
  The memory-engine worktree for item 8 is the orchestrator's to create.
- [ ] **Plan issue:** one, published by `~/.claude/skills/finish-pr/scripts/archive-plan-issue.sh` before Michaël's
  approval is asked; his marker goes there and `/finish-pr` reuses it. No code before the marker.
- [ ] **PR:** title `fix: ingest correctness, group D1 (#634)`. Body: the route lines, O1 to O8 as approved, the #633
  measurement, the memory-engine PR link and digest, the runbook delta, the gate transcript, `Collateral findings`,
  then `Closes #634`, `Closes #597`, `Closes #598`, `Closes #626`, `Closes #631`, `Closes #633`, one per line, and
  `Refs dutiona/infra#37` (with O5 declined: `Refs #633` and `Refs #634` instead of their `Closes`, as O5 says). No
  negated closing phrase anywhere.
  If item 8 is not final when items 0 to 7 are done, the PR opens as a draft and item 9 joins it later.
- [ ] **Review:** `/super-review` at the confirmed Full gate through the persisted route, once item 9 is in; fixes land
  as new commits; rebind after each.
- [ ] **Finish:** `/finish-pr` to READY with a fresh unresolved-thread poll of 0, the memory-engine PR final with a
  `cmp`-equal fixture; its Item 5 archives the plan to the plan issue and removes `docs/plans/`.
- [ ] **Merge:** Michaël's, through the orchestrator, memory-engine first. The worker never merges.
