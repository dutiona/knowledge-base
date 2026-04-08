# Plan: Issue #349 — Separate Indexer CLI Entry Point

## Context

Phase 1 of the build/serve separation (#124). The MCP server currently handles
both ingestion (CPU/LLM-heavy writes) and query serving (lightweight reads) in a
single process. This PR introduces a standalone CLI tool (`knowledge-base-ingest`)
that wraps existing domain functions for batch indexing outside the MCP server.

**Goal:** A second entry point for indexing operations, fully backward compatible
with the existing MCP server.

## Files to Create/Modify

| File | Action | LOC |
|------|--------|-----|
| `src/knowledge_base/indexer.py` | Create | ~150 |
| `pyproject.toml` | Add 1 line (console_script) | 1 |
| `tests/test_indexer.py` | Create | ~200 |

No changes to existing modules. Zero breaking changes.

## Step 1: Create `src/knowledge_base/indexer.py`

Argparse-based CLI (no new dependencies). Structure:

```
knowledge-base-ingest [--db PATH] [--verbose] [--quiet] <subcommand>

Subcommands:
  ingest <path> [--source-type TYPE] [--session-id ID]
  reingest <path> [--source-type TYPE] [--session-id ID]
  ingest-url <url> [--session-id ID]
  re-embed --model MODEL --dim DIM [--batch-size N] [--provider P] [--matryoshka-base-dim D]
  status
```

### Internal helpers

- `_get_conn(db_path: Path) -> sqlite3.Connection` — calls `db.get_connection(db_path)`
  then `db.init_schema(conn)`. Sets `PRAGMA busy_timeout=5000` for WAL contention.
  Does NOT use `_conn._get_conn()` (that's thread-local for the server).

### Subcommand handlers

Each `cmd_*` function: gets conn, calls domain function, prints `json.dumps(result)` to stdout.
Catches `KnowledgeBaseError` → error JSON + `sys.exit(1)`.

- **`cmd_ingest`**: Auto-detects file vs dir (like `routes/ingestion.py:19-53`).
  Calls `ingest.ingest_file()` or `ingest.ingest_directory()`.

- **`cmd_reingest`**: Calls `ingest.reingest_file()`, then replicates the
  post-reingest auto_relate logic from `routes/ingestion.py:83-99`:
  delete stale `similar` relationships + `submit_job(auto_relate)` for affected papers.

- **`cmd_ingest_url`**: Calls `web.ingest_url()`.

- **`cmd_re_embed`**: Calls `embed_swap.re_embed()`, then deletes `similar`
  relationships (mirroring `routes/embeddings.py:59`).

- **`cmd_status`**: Quick DB stats via SQL (chunks count, distinct sources,
  papers count, jobs by status) + `get_embed_config()`.

### Output & logging

- JSON to stdout (machine-parseable, matches MCP route returns)
- `--quiet` suppresses stdout except errors
- `--verbose` sets logging to DEBUG (default: WARNING)
- Logging always goes to stderr

### Job worker (Option D — no decoupling)

`submit_job()` still calls `_ensure_worker_running()` in whichever process it's
called from. Both processes can run workers — SQLite's atomic
`UPDATE...RETURNING` claim in `_tick()` prevents double-processing. Decoupling
is a Phase 2 concern.

## Step 2: Update `pyproject.toml`

Add one line to `[project.scripts]`:
```toml
knowledge-base-ingest = "knowledge_base.indexer:main"
```

## Step 3: Create `tests/test_indexer.py`

Unit tests with `tmp_path` DB (project convention). Call `cmd_*` functions
directly with constructed args — no subprocess overhead.

Reuse existing test patterns:
- `_fake_embed` from `tests/test_ingest.py` style
- `@patch("knowledge_base.ingest.embed", ...)` + `@patch("knowledge_base.folder_summaries.embed", ...)`
- `@patch("knowledge_base.jobs._ensure_worker_running")` to suppress daemon thread

Test cases:
- [ ] `test_cmd_ingest_file` — markdown file → verify chunks in DB
- [ ] `test_cmd_ingest_directory` — dir with 2 files → verify both ingested
- [ ] `test_cmd_ingest_nonexistent` — error JSON + exit code 1
- [ ] `test_cmd_reingest` — ingest then reingest → verify chunks replaced
- [ ] `test_cmd_reingest_auto_relate` — link to paper, reingest → verify auto_relate job submitted
- [ ] `test_cmd_ingest_url` — mock trafilatura → verify chunks
- [ ] `test_cmd_re_embed` — verify space changed + similar rels deleted
- [ ] `test_cmd_status` — verify JSON output keys
- [ ] `test_db_override` — verify `--db` connects to specified path
- [ ] `test_build_parser` — subcommands exist, required args enforced
- [ ] `test_main_integration` — `sys.argv` monkeypatch → call `main()`

## Verification

```bash
# In worktree: .worktrees/feat/349-indexer-cli
uv sync
uv run ruff check src/knowledge_base/indexer.py tests/test_indexer.py
uv run ruff format src/knowledge_base/indexer.py tests/test_indexer.py
uv run pytest tests/test_indexer.py -v          # new tests
uv run pytest tests/ -q                          # full suite regression
```

## Risk: Duplicate Post-Action Logic

The post-reingest auto_relate submission and post-re-embed similar-deletion are
duplicated from routes. Acceptable for Phase 1. Phase 2 should extract these
into the domain functions (e.g., `reingest_file(..., auto_relate=True)`).
