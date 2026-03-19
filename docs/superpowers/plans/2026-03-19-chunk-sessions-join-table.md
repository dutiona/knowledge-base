# chunk_sessions Join Table (N:M Session Tracking) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 1:1 `chunks.session_id` column with an N:M `chunk_sessions` join table so content-hash-deduplicated chunks correctly participate in multiple ingestion sessions.

**Architecture:** New `chunk_sessions(chunk_id, session_id)` join table with UNIQUE constraint. All dedup paths INSERT OR IGNORE into the join table even when the chunk is skipped. `co_occurrence_pairs()` CTE rewired to query the join table. `reingest_file()` preserves historical session associations across delete-and-reinsert cycles. The `chunks.session_id` column is kept but deprecated (still written for backward compat; no longer read by co-occurrence logic).

**Tech Stack:** Python 3.12+, SQLite (FTS5, sqlite-vec), pytest, ruff

---

## File Map

| File                           | Action | Responsibility                                                                       |
| ------------------------------ | ------ | ------------------------------------------------------------------------------------ |
| `src/knowledge_base/db.py`     | Modify | Add `chunk_sessions` table DDL, migration, update `co_occurrence_pairs()` CTE        |
| `src/knowledge_base/ingest.py` | Modify | Dedup paths write to `chunk_sessions`; `reingest_file` preserves historical sessions |
| `tests/test_db.py`             | Modify | Update co-occurrence tests to use join table, add migration test                     |
| `tests/test_ingest.py`         | Modify | Add multi-session dedup test, reingest session preservation test                     |

---

## Task 1: Create `chunk_sessions` table and migration

**Files:**

- Modify: `src/knowledge_base/db.py:295-315` (schema section)
- Modify: `src/knowledge_base/db.py:210-216` (migration section)
- Modify: `src/knowledge_base/db.py:520-535` (init_schema index/migration calls)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing test for `chunk_sessions` table existence**

```python
def test_chunk_sessions_table_exists(tmp_path):
    """chunk_sessions join table is created by init_schema."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Table exists
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='chunk_sessions'"
    ).fetchone()
    assert row is not None

    # Has correct columns
    cols = {r[1] for r in conn.execute("PRAGMA table_info(chunk_sessions)").fetchall()}
    assert cols == {"chunk_id", "session_id"}

    # UNIQUE constraint works
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('h1', 'c1', 'note', '/tmp/a.md', 0)"
    )
    chunk_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO chunk_sessions (chunk_id, session_id) VALUES (?, 'sess-1')",
        (chunk_id,),
    )
    # Duplicate should be silently ignored
    conn.execute(
        "INSERT OR IGNORE INTO chunk_sessions (chunk_id, session_id) VALUES (?, 'sess-1')",
        (chunk_id,),
    )
    count = conn.execute(
        "SELECT COUNT(*) FROM chunk_sessions WHERE chunk_id = ?", (chunk_id,)
    ).fetchone()[0]
    assert count == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions && uv run pytest tests/test_db.py::test_chunk_sessions_table_exists -v`
Expected: FAIL — table does not exist

- [ ] **Step 3: Add `chunk_sessions` CREATE TABLE to schema**

In `db.py`, after the `chunks` table (around line 312), add:

```python
    # -- chunk_sessions: N:M join for chunk-to-session tracking --
    CREATE TABLE IF NOT EXISTS chunk_sessions (
        chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
        session_id TEXT NOT NULL,
        UNIQUE(chunk_id, session_id)
    );
```

Add index in the index creation section (around line 530):

```python
    CREATE INDEX IF NOT EXISTS idx_chunk_sessions_session
        ON chunk_sessions(session_id);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions && uv run pytest tests/test_db.py::test_chunk_sessions_table_exists -v`
Expected: PASS

- [ ] **Step 5: Write failing test for migration backfill**

```python
def test_migrate_chunk_sessions_backfill(tmp_path):
    """Migration backfills chunk_sessions from existing chunks.session_id."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)

    # Create schema WITHOUT chunk_sessions table but WITH session_id column
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    INSERT OR IGNORE INTO config (key, value) VALUES ('embed_model', 'bge-m3');
    INSERT OR IGNORE INTO config (key, value) VALUES ('embed_dim', '1024');

    CREATE TABLE chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content_hash TEXT NOT NULL UNIQUE,
        content TEXT NOT NULL,
        source_type TEXT NOT NULL CHECK(source_type IN ('pdf','markdown','code','web','note','figure')),
        source_uri TEXT NOT NULL,
        chunk_index INTEGER NOT NULL,
        session_id TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        metadata TEXT DEFAULT '{}'
    );
    """)
    # Insert chunks with session_id values
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, session_id) "
        "VALUES ('h1', 'c1', 'note', '/tmp/a.md', 0, 'sess-A')"
    )
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, session_id) "
        "VALUES ('h2', 'c2', 'note', '/tmp/b.md', 0, 'sess-A')"
    )
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('h3', 'c3', 'note', '/tmp/c.md', 0)"  # NULL session_id
    )
    conn.commit()

    # Run init_schema (triggers migration)
    init_schema(conn)

    # Verify backfill: chunks with session_id should have entries
    rows = conn.execute(
        "SELECT chunk_id, session_id FROM chunk_sessions ORDER BY chunk_id"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["session_id"] == "sess-A"
    assert rows[1]["session_id"] == "sess-A"

    # Verify NULL session_id chunk was NOT backfilled
    null_rows = conn.execute(
        "SELECT * FROM chunk_sessions WHERE chunk_id = 3"
    ).fetchall()
    assert len(null_rows) == 0
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions && uv run pytest tests/test_db.py::test_migrate_chunk_sessions_backfill -v`
Expected: FAIL — no backfill logic yet

- [ ] **Step 7: Implement migration function**

In `db.py`, add a new migration function after `_migrate_jobs_types`:

```python
def _migrate_chunk_sessions(conn: sqlite3.Connection) -> None:
    """Create chunk_sessions join table and backfill from chunks.session_id."""
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='chunk_sessions'"
    ).fetchone()
    if exists:
        return
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunk_sessions (
            chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
            session_id TEXT NOT NULL,
            UNIQUE(chunk_id, session_id)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunk_sessions_session "
        "ON chunk_sessions(session_id)"
    )
    # Backfill from existing chunks.session_id
    conn.execute("""
        INSERT OR IGNORE INTO chunk_sessions (chunk_id, session_id)
        SELECT id, session_id FROM chunks WHERE session_id IS NOT NULL
    """)
    conn.commit()
```

Add the migration call in `init_schema()` after the existing migration calls (around line 533):

```python
    _migrate_chunk_sessions(conn)
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions && uv run pytest tests/test_db.py::test_migrate_chunk_sessions_backfill tests/test_db.py::test_chunk_sessions_table_exists -v`
Expected: PASS

- [ ] **Step 9: Run full test suite to check for regressions**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions && uv run pytest tests/ -q`
Expected: All 518 tests pass

- [ ] **Step 10: Commit**

```bash
cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions
git add src/knowledge_base/db.py tests/test_db.py
git commit -m "feat(db): add chunk_sessions join table with migration backfill

Introduce N:M chunk_sessions(chunk_id, session_id) table to replace the
1:1 chunks.session_id column for session tracking. Migration backfills
existing session associations.

Refs #139"
```

---

## Task 2: Rewire `co_occurrence_pairs()` to use join table

**Files:**

- Modify: `src/knowledge_base/db.py:74-107` (`co_occurrence_pairs` function)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing test that uses join table data**

```python
def test_co_occurrence_pairs_uses_join_table(tmp_path):
    """co_occurrence_pairs reads from chunk_sessions, not chunks.session_id."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Insert chunks WITHOUT session_id on the chunk itself
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('h1', 'c1', 'note', '/tmp/a.md', 0)"
    )
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('h2', 'c2', 'note', '/tmp/b.md', 0)"
    )
    conn.commit()

    # Manually populate chunk_sessions (simulating the new write path)
    conn.execute("INSERT INTO chunk_sessions (chunk_id, session_id) VALUES (1, 'sess-X')")
    conn.execute("INSERT INTO chunk_sessions (chunk_id, session_id) VALUES (2, 'sess-X')")
    conn.commit()

    pairs = co_occurrence_pairs(conn)
    assert len(pairs) == 1
    assert pairs[0]["source_uri_a"] == "/tmp/a.md"
    assert pairs[0]["source_uri_b"] == "/tmp/b.md"
    assert pairs[0]["co_sessions"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions && uv run pytest tests/test_db.py::test_co_occurrence_pairs_uses_join_table -v`
Expected: FAIL — current CTE reads `chunks.session_id` which is NULL for these chunks

- [ ] **Step 3: Update `co_occurrence_pairs()` CTE**

Replace the SQL in `co_occurrence_pairs()` (db.py:80-97):

```python
    rows = conn.execute(
        """
        WITH doc_sessions AS (
            SELECT DISTINCT c.source_uri, cs.session_id
            FROM chunk_sessions cs
            JOIN chunks c ON c.id = cs.chunk_id
        )
        SELECT a.source_uri AS source_uri_a,
               b.source_uri AS source_uri_b,
               COUNT(*) AS co_sessions
        FROM doc_sessions a
        JOIN doc_sessions b
          ON a.session_id = b.session_id
         AND a.source_uri < b.source_uri
        GROUP BY a.source_uri, b.source_uri
        HAVING co_sessions >= ?
        ORDER BY co_sessions DESC
        """,
        (min_sessions,),
    ).fetchall()
```

- [ ] **Step 4: Run new test + existing co-occurrence tests**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions && uv run pytest tests/test_db.py -k "co_occurrence" -v`
Expected: The new test passes. Existing tests (`test_co_occurrence_pairs_basic`, `test_co_occurrence_pairs_min_sessions`, `test_co_occurrence_ignores_null_sessions`) will FAIL because they write to `chunks.session_id` but not to `chunk_sessions`.

- [ ] **Step 5: Update existing co-occurrence tests to use join table**

Update `test_co_occurrence_pairs_basic`, `test_co_occurrence_pairs_min_sessions`, and `test_co_occurrence_ignores_null_sessions` to also INSERT into `chunk_sessions` alongside `chunks.session_id`. This simulates the backfill state.

For `test_co_occurrence_pairs_basic` — after each INSERT into chunks, add:

```python
    chunk_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO chunk_sessions (chunk_id, session_id) VALUES (?, ?)",
        (chunk_id, session_id_value),
    )
```

Apply the same pattern to `test_co_occurrence_pairs_min_sessions`.

For `test_co_occurrence_ignores_null_sessions` — no change needed (chunks without session_id won't have `chunk_sessions` entries, so the test should still pass as-is).

- [ ] **Step 6: Run all co-occurrence tests**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions && uv run pytest tests/test_db.py -k "co_occurrence" -v`
Expected: All PASS

- [ ] **Step 7: Run full test suite**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions && uv run pytest tests/ -q`
Expected: All tests pass

- [ ] **Step 8: Commit**

```bash
cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions
git add src/knowledge_base/db.py tests/test_db.py
git commit -m "feat(db): rewire co_occurrence_pairs to use chunk_sessions join table

The CTE now joins through chunk_sessions instead of reading
chunks.session_id directly, enabling proper N:M session tracking.

Refs #139"
```

---

## Task 3: Update `ingest_file` dedup paths to write `chunk_sessions`

**Files:**

- Modify: `src/knowledge_base/ingest.py:497-618` (ingest_file dedup + insert paths)
- Test: `tests/test_ingest.py`

**Important — test patterns:** This project does NOT use a `db` fixture. Tests create their own connection inline:

```python
db_path = tmp_path / "test.db"
conn = get_connection(db_path)
init_schema(conn)
```

Embeddings are mocked via `_fake_embed` (defined at top of test_ingest.py) patching `knowledge_base.ingest.embed` and `knowledge_base.folder_summaries.embed`. Use `side_effect=lambda conn, texts: [[0.1] * 1024] * len(texts)` for `_embed_with_config` to handle variable chunk counts, OR use the existing `_fake_embed` + `@patch` decorator pattern.

- [ ] **Step 1: Write failing test for multi-session dedup**

This is the core scenario from the issue: file A ingested in session 1 creates chunks. File A re-ingested (unchanged) in session 2 — dedup skips chunks but session 2 must still be recorded.

```python
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_ingest_file_dedup_records_session(tmp_path):
    """When chunks are deduped, the new session is still recorded in chunk_sessions."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    test_file = tmp_path / "a.md"
    test_file.write_text("# Hello\n\nSome content here for testing.")

    # First ingest — creates chunks with session-1
    r1 = ingest_file(conn, test_file, session_id="session-1")
    assert r1["chunks_added"] > 0

    # Second ingest — same content, different session
    r2 = ingest_file(conn, test_file, session_id="session-2")
    assert r2["chunks_added"] == 0  # All deduped
    assert r2["chunks_skipped"] > 0

    # Both sessions should be recorded in chunk_sessions
    chunk_id = conn.execute(
        "SELECT id FROM chunks WHERE source_uri = ?", (str(test_file.resolve()),)
    ).fetchone()["id"]
    sessions = conn.execute(
        "SELECT session_id FROM chunk_sessions WHERE chunk_id = ? ORDER BY session_id",
        (chunk_id,),
    ).fetchall()
    assert len(sessions) == 2
    assert sessions[0]["session_id"] == "session-1"
    assert sessions[1]["session_id"] == "session-2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions && uv run pytest tests/test_ingest.py::test_ingest_file_dedup_records_session -v`
Expected: FAIL — no chunk_sessions entries for session-2

- [ ] **Step 3: Implement chunk_sessions writes in `ingest_file`**

There are three dedup loops in `ingest_file` (AST path ~line 501, PDF path ~line 534, fixed path ~line 555). In each loop, when a chunk is skipped (the `if existing:` branch), add:

```python
            if existing:
                if session_id:
                    conn.execute(
                        "INSERT OR IGNORE INTO chunk_sessions (chunk_id, session_id) VALUES (?, ?)",
                        (existing["id"], session_id),
                    )
                skipped += 1
                continue
```

Also, after each new chunk INSERT (line 592-603), add the `chunk_sessions` write:

```python
        chunk_id = cursor.lastrowid
        if session_id:
            conn.execute(
                "INSERT OR IGNORE INTO chunk_sessions (chunk_id, session_id) VALUES (?, ?)",
                (chunk_id, session_id),
            )
```

The existing dedup SELECT queries need to change from `SELECT id FROM chunks` to capture the `id`:

```python
    existing = conn.execute(
        "SELECT id FROM chunks WHERE content_hash = ?", (h,)
    ).fetchone()
```

These already return `id` — the existing code is correct. Since connections use `Row` factory (set in `get_connection`), `existing["id"]` works.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions && uv run pytest tests/test_ingest.py::test_ingest_file_dedup_records_session -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions && uv run pytest tests/ -q`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions
git add src/knowledge_base/ingest.py tests/test_ingest.py
git commit -m "feat(ingest): record chunk_sessions on dedup in ingest_file

All three chunking paths (AST, markdown, fixed) now INSERT OR IGNORE
into chunk_sessions both for new chunks and deduped chunks, ensuring
session associations are never lost during content-hash dedup.

Refs #139"
```

---

## Task 4: Update `ingest_url` dedup path

**Files:**

- Modify: `src/knowledge_base/ingest.py:1636-1663` (ingest_url dedup + insert)
- Test: `tests/test_ingest.py`

- [ ] **Step 1: Write failing test**

Follow the existing `test_ingest_url_dedup` pattern (test_ingest.py:801): mock `httpx.get` with `_mock_httpx_get` and embed with `_fake_embed`.

```python
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.ingest.httpx.get", _mock_httpx_get)
def test_ingest_url_dedup_records_session(tmp_path):
    """When URL chunks are deduped, the new session is still recorded."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    url = "https://example.com/test"
    r1 = ingest_url(conn, url, session_id="ws-1")
    assert r1["chunks_added"] > 0

    r2 = ingest_url(conn, url, session_id="ws-2")
    assert r2["chunks_added"] == 0
    assert r2["chunks_skipped"] > 0

    chunk_id = conn.execute(
        "SELECT id FROM chunks WHERE source_uri = ?", (url,)
    ).fetchone()["id"]
    sessions = conn.execute(
        "SELECT session_id FROM chunk_sessions WHERE chunk_id = ? ORDER BY session_id",
        (chunk_id,),
    ).fetchall()
    assert len(sessions) == 2
    assert sessions[0]["session_id"] == "ws-1"
    assert sessions[1]["session_id"] == "ws-2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions && uv run pytest tests/test_ingest.py::test_ingest_url_dedup_records_session -v`
Expected: FAIL

- [ ] **Step 3: Apply same pattern to `ingest_url`**

In the dedup loop (line ~1641):

```python
        if existing:
            if session_id:
                conn.execute(
                    "INSERT OR IGNORE INTO chunk_sessions (chunk_id, session_id) VALUES (?, ?)",
                    (existing["id"], session_id),
                )
            skipped += 1
            continue
```

After new chunk INSERT (line ~1656):

```python
        chunk_id = cursor.lastrowid
        if session_id:
            conn.execute(
                "INSERT OR IGNORE INTO chunk_sessions (chunk_id, session_id) VALUES (?, ?)",
                (chunk_id, session_id),
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions && uv run pytest tests/test_ingest.py::test_ingest_url_dedup_records_session -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions && uv run pytest tests/ -q`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions
git add src/knowledge_base/ingest.py tests/test_ingest.py
git commit -m "feat(ingest): record chunk_sessions on dedup in ingest_url

Same INSERT OR IGNORE pattern as ingest_file, ensuring web content
re-ingestion preserves session associations.

Refs #139"
```

---

## Task 5: Preserve historical sessions in `reingest_file`

**Files:**

- Modify: `src/knowledge_base/ingest.py:621-862` (reingest_file)
- Test: `tests/test_ingest.py`

This is the trickiest task. `reingest_file` deletes all old chunks and re-inserts new ones. The ON DELETE CASCADE on `chunk_sessions` wipes historical session associations. We must save and restore them.

- [ ] **Step 1: Write failing test**

```python
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_reingest_preserves_historical_sessions(tmp_path):
    """reingest_file preserves session associations from prior ingestions."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    test_file = tmp_path / "a.md"
    test_file.write_text("# Original\n\nOriginal content here.")
    ingest_file(conn, test_file, session_id="sess-1")

    # Simulate a second session via direct chunk_sessions insert
    chunk_id = conn.execute(
        "SELECT id FROM chunks WHERE source_uri = ?", (str(test_file.resolve()),)
    ).fetchone()["id"]
    conn.execute(
        "INSERT OR IGNORE INTO chunk_sessions (chunk_id, session_id) VALUES (?, 'sess-2')",
        (chunk_id,),
    )
    conn.commit()

    # Reingest with modified content
    test_file.write_text("# Updated\n\nUpdated content here.")
    result = reingest_file(conn, test_file, session_id="sess-3")
    assert result["chunks_added"] > 0

    # New chunks should have ALL three sessions: sess-1, sess-2, sess-3
    new_chunk_id = conn.execute(
        "SELECT id FROM chunks WHERE source_uri = ?", (str(test_file.resolve()),)
    ).fetchone()["id"]
    sessions = conn.execute(
        "SELECT session_id FROM chunk_sessions WHERE chunk_id = ? ORDER BY session_id",
        (new_chunk_id,),
    ).fetchall()
    session_ids = {r["session_id"] for r in sessions}
    assert session_ids == {"sess-1", "sess-2", "sess-3"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions && uv run pytest tests/test_ingest.py::test_reingest_preserves_historical_sessions -v`
Expected: FAIL — historical sessions lost on chunk deletion

- [ ] **Step 3: Implement session preservation in `reingest_file`**

In `reingest_file`, before the chunk deletion block (around line 700), collect historical sessions:

```python
    # --- Preserve historical session associations ---
    historical_sessions = {
        r["session_id"]
        for r in conn.execute(
            "SELECT DISTINCT session_id FROM chunk_sessions "
            "WHERE chunk_id IN (SELECT id FROM chunks WHERE source_uri = ?)",
            (source_uri,),
        ).fetchall()
    }
```

After the new chunk INSERT loop (after line 810), restore historical sessions + add current:

```python
    # --- Restore historical session associations on new chunks ---
    all_sessions = historical_sessions
    if session_id:
        all_sessions = historical_sessions | {session_id}
    if all_sessions:
        new_chunk_ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM chunks WHERE source_uri = ?", (source_uri,)
            ).fetchall()
        ]
        for cid in new_chunk_ids:
            for sid in all_sessions:
                conn.execute(
                    "INSERT OR IGNORE INTO chunk_sessions (chunk_id, session_id) VALUES (?, ?)",
                    (cid, sid),
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions && uv run pytest tests/test_ingest.py::test_reingest_preserves_historical_sessions -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions && uv run pytest tests/ -q`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions
git add src/knowledge_base/ingest.py tests/test_ingest.py
git commit -m "feat(ingest): preserve historical sessions across reingest

Before deleting old chunks, collect all session_ids from chunk_sessions.
After re-inserting new chunks, restore all historical sessions plus the
current session_id on the new chunks.

Refs #139"
```

---

## Task 6: Integration test — full multi-session co-occurrence scenario

**Files:**

- Test: `tests/test_ingest.py`

- [ ] **Step 1: Write end-to-end integration test**

This test validates the full scenario from the issue: two files ingested in session 1, same files re-ingested (unchanged, deduped) in session 2, co-occurrence count should reflect both sessions.

```python
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_multi_session_dedup_co_occurrence(tmp_path):
    """End-to-end: deduped chunks still produce correct co-occurrence counts."""
    from knowledge_base.db import co_occurrence_pairs

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("# Paper A\n\nContent of paper A.")
    b.write_text("# Paper B\n\nContent of paper B.")

    # Session 1: ingest both files
    ingest_file(conn, a, session_id="s1")
    ingest_file(conn, b, session_id="s1")

    # Session 2: re-ingest same files (all deduped)
    r_a = ingest_file(conn, a, session_id="s2")
    r_b = ingest_file(conn, b, session_id="s2")
    assert r_a["chunks_skipped"] > 0
    assert r_b["chunks_skipped"] > 0

    # co_occurrence should see 2 shared sessions for (a, b)
    pairs = co_occurrence_pairs(conn, min_sessions=1)
    assert len(pairs) == 1
    assert pairs[0]["co_sessions"] == 2

    # min_sessions=2 should still include them
    pairs_2 = co_occurrence_pairs(conn, min_sessions=2)
    assert len(pairs_2) == 1

    # min_sessions=3 should exclude them
    pairs_3 = co_occurrence_pairs(conn, min_sessions=3)
    assert len(pairs_3) == 0
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions && uv run pytest tests/test_ingest.py::test_multi_session_dedup_co_occurrence -v`
Expected: PASS (all prior tasks should make this work)

- [ ] **Step 3: Run full test suite + lint**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions && uv run pytest tests/ -q && ruff check src/ tests/ && ruff format --check src/ tests/`
Expected: All tests pass, no lint errors

- [ ] **Step 4: Commit**

```bash
cd /home/mroynard/dev/knowledge-base/.worktrees/feat-139-chunk-sessions
git add tests/test_ingest.py
git commit -m "test: add end-to-end multi-session dedup co-occurrence test

Validates the full scenario from #139: deduped chunks correctly
participate in multiple sessions, producing accurate co-occurrence counts.

Refs #139"
```

---

## Acceptance Criteria Checklist (from #139)

| Criterion                                                      | Task                         |
| -------------------------------------------------------------- | ---------------------------- |
| `chunk_sessions` table with migration from `chunks.session_id` | Task 1                       |
| Dedup path records session even for skipped chunks             | Tasks 3, 4                   |
| `co_occurrence_pairs` uses `chunk_sessions` join table         | Task 2                       |
| `reingest_file` preserves all historical sessions              | Task 5                       |
| Tests cover multi-session dedup scenario                       | Task 6                       |
| Existing tests still pass                                      | Verified at end of each task |
