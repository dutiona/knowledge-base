"""Smoke tests for schema and basic operations."""

import sqlite3
import struct

from knowledge_base.db import (
    _migrate_normalize_source_uri,
    co_occurrence_pairs,
    get_connection,
    init_schema,
    DEFAULT_EMBED_DIM,
)


def _serialize_f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def test_schema_creates_all_tables(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    table_names = {row["name"] for row in tables}

    assert "chunks" in table_names
    assert "papers" in table_names
    assert "relationships" in table_names
    assert "conclusions" in table_names
    assert "executions" in table_names


def test_fts_trigger(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('abc123', 'transformers attention mechanism', 'pdf', '/tmp/paper.pdf', 0)"
    )
    conn.commit()

    rows = conn.execute(
        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'attention'"
    ).fetchall()
    assert len(rows) == 1


def test_vec_insert_and_query(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Insert a chunk
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('vec_test', 'test vector content', 'note', '/tmp/test.md', 0)"
    )
    chunk_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Insert embedding
    fake_emb = [0.1] * DEFAULT_EMBED_DIM
    conn.execute(
        "INSERT INTO chunks_vec (rowid, embedding, chunk_id) VALUES (?, ?, ?)",
        (chunk_id, _serialize_f32(fake_emb), chunk_id),
    )
    conn.commit()

    # Query
    results = conn.execute(
        "SELECT chunk_id, distance FROM chunks_vec WHERE embedding MATCH ? ORDER BY distance LIMIT 1",
        (_serialize_f32(fake_emb),),
    ).fetchall()
    assert len(results) == 1
    assert results[0]["chunk_id"] == chunk_id


def test_content_hash_dedup(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('dedup_hash', 'same content', 'note', '/tmp/a.md', 0)"
    )
    conn.commit()

    # Duplicate should fail
    try:
        conn.execute(
            "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
            "VALUES ('dedup_hash', 'same content', 'note', '/tmp/b.md', 0)"
        )
        assert False, "Should have raised IntegrityError"
    except sqlite3.IntegrityError:
        pass


def test_session_id_column_exists(tmp_path):
    """session_id column is nullable and present on chunks table."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Insert without session_id (NULL) — backwards compatible
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('no_sess', 'no session content', 'note', '/tmp/a.md', 0)"
    )
    # Insert with session_id
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, session_id) "
        "VALUES ('with_sess', 'session content', 'note', '/tmp/b.md', 0, 'sess-001')"
    )
    conn.commit()

    rows = conn.execute(
        "SELECT content_hash, session_id FROM chunks ORDER BY content_hash"
    ).fetchall()
    assert rows[0]["session_id"] is None
    assert rows[1]["session_id"] == "sess-001"


def test_session_id_index_exists(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    indexes = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_chunks_session_id'"
    ).fetchall()
    assert len(indexes) == 1


def test_co_occurrence_pairs_basic(tmp_path):
    """co_occurrence_pairs returns document pairs sharing a session."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Two docs in session-A
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, session_id) "
        "VALUES ('h1', 'c1', 'note', '/tmp/a.md', 0, 'session-A')"
    )
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO chunk_sessions (chunk_id, session_id) VALUES (?, 'session-A')",
        (cid,),
    )
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, session_id) "
        "VALUES ('h2', 'c2', 'note', '/tmp/b.md', 0, 'session-A')"
    )
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO chunk_sessions (chunk_id, session_id) VALUES (?, 'session-A')",
        (cid,),
    )
    # A third doc in a different session — no co-occurrence with a.md/b.md
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, session_id) "
        "VALUES ('h3', 'c3', 'note', '/tmp/c.md', 0, 'session-B')"
    )
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO chunk_sessions (chunk_id, session_id) VALUES (?, 'session-B')",
        (cid,),
    )
    conn.commit()

    pairs = co_occurrence_pairs(conn)
    assert len(pairs) == 1
    assert pairs[0]["source_uri_a"] == "/tmp/a.md"
    assert pairs[0]["source_uri_b"] == "/tmp/b.md"
    assert pairs[0]["co_sessions"] == 1


def test_co_occurrence_pairs_min_sessions(tmp_path):
    """min_sessions filters out pairs below the threshold."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # a.md and b.md share TWO sessions
    for sess in ("s1", "s2"):
        conn.execute(
            "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, session_id) "
            f"VALUES ('a_{sess}', 'ca', 'note', '/tmp/a.md', 0, ?)",
            (sess,),
        )
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO chunk_sessions (chunk_id, session_id) VALUES (?, ?)",
            (cid, sess),
        )
        conn.execute(
            "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, session_id) "
            f"VALUES ('b_{sess}', 'cb', 'note', '/tmp/b.md', 0, ?)",
            (sess,),
        )
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO chunk_sessions (chunk_id, session_id) VALUES (?, ?)",
            (cid, sess),
        )
    # c.md shares only ONE session with a.md
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, session_id) "
        "VALUES ('c_s1', 'cc', 'note', '/tmp/c.md', 0, 's1')"
    )
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO chunk_sessions (chunk_id, session_id) VALUES (?, ?)", (cid, "s1")
    )
    conn.commit()

    # min_sessions=2 should only return a.md/b.md
    pairs = co_occurrence_pairs(conn, min_sessions=2)
    assert len(pairs) == 1
    assert pairs[0]["co_sessions"] == 2

    # min_sessions=1 returns all pairs
    all_pairs = co_occurrence_pairs(conn, min_sessions=1)
    assert len(all_pairs) == 3  # a-b, a-c, b-c


def test_co_occurrence_ignores_null_sessions(tmp_path):
    """Chunks without session_id should not form co-occurrence pairs."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('n1', 'c1', 'note', '/tmp/a.md', 0)"
    )
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('n2', 'c2', 'note', '/tmp/b.md', 0)"
    )
    conn.commit()

    pairs = co_occurrence_pairs(conn)
    assert pairs == []


def test_chunk_strategy_column_exists(tmp_path):
    """Fresh DB has chunk_strategy column with default 'mechanical'."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('cs_test', 'strategy test', 'pdf', '/tmp/paper.pdf', 0)"
    )
    conn.commit()

    row = conn.execute(
        "SELECT chunk_strategy FROM chunks WHERE content_hash = 'cs_test'"
    ).fetchone()
    assert row["chunk_strategy"] == "mechanical"


def test_chunk_strategy_config_default(tmp_path):
    """Config table has chunk_strategy = 'mechanical' after init."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    row = conn.execute(
        "SELECT value FROM config WHERE key = 'chunk_strategy'"
    ).fetchone()
    assert row is not None
    assert row["value"] == "mechanical"


def test_chunk_strategy_migration(tmp_path):
    """Existing DB without chunk_strategy column gets it via migration."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)

    # Create old schema WITHOUT chunk_strategy
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    INSERT OR IGNORE INTO config (key, value) VALUES ('embed_model', 'bge-m3');
    INSERT OR IGNORE INTO config (key, value) VALUES ('embed_dim', '1024');
    INSERT OR IGNORE INTO config (key, value) VALUES ('embed_provider', 'ollama');

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
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('old', 'old content', 'note', '/tmp/old.md', 0)"
    )
    conn.commit()

    # Run init_schema which triggers migration
    init_schema(conn)

    # Verify chunk_strategy column exists with default
    row = conn.execute(
        "SELECT chunk_strategy FROM chunks WHERE content_hash = 'old'"
    ).fetchone()
    assert row["chunk_strategy"] == "mechanical"

    # Config key also set
    cfg = conn.execute(
        "SELECT value FROM config WHERE key = 'chunk_strategy'"
    ).fetchone()
    assert cfg["value"] == "mechanical"


def test_migrate_session_id_on_existing_db(tmp_path):
    """Migration adds session_id to a pre-existing chunks table."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)

    # Create old schema WITHOUT session_id
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
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        metadata TEXT DEFAULT '{}'
    );
    """)
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('old', 'old content', 'note', '/tmp/old.md', 0)"
    )
    conn.commit()

    # Run init_schema which triggers migration
    init_schema(conn)

    # Verify session_id column exists and old data preserved
    row = conn.execute(
        "SELECT session_id FROM chunks WHERE content_hash = 'old'"
    ).fetchone()
    assert row["session_id"] is None

    # Can now insert with session_id
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, session_id) "
        "VALUES ('new', 'new content', 'note', '/tmp/new.md', 0, 'sess-1')"
    )
    conn.commit()


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
    conn.execute(
        "INSERT INTO chunk_sessions (chunk_id, session_id) VALUES (1, 'sess-X')"
    )
    conn.execute(
        "INSERT INTO chunk_sessions (chunk_id, session_id) VALUES (2, 'sess-X')"
    )
    conn.commit()

    pairs = co_occurrence_pairs(conn)
    assert len(pairs) == 1
    assert pairs[0]["source_uri_a"] == "/tmp/a.md"
    assert pairs[0]["source_uri_b"] == "/tmp/b.md"
    assert pairs[0]["co_sessions"] == 1


def test_migrate_normalize_source_uri(tmp_path):
    """Migration normalizes backslash source_uris to forward slashes (#158)."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Insert rows with Windows-style backslash paths
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index)"
        r" VALUES ('h1', 'c1', 'note', 'C:\Users\foo\papers\a.md', 0)"
    )
    conn.execute("INSERT INTO papers (title, authors) VALUES ('Test', '[]')")
    conn.execute(
        "INSERT INTO paper_paths (paper_id, path, is_primary)"
        r" VALUES (1, 'C:\Users\foo\papers\a.md', TRUE)"
    )
    conn.commit()

    # Run the migration directly (#450: init_schema no longer re-runs the
    # idempotent builds on an already-versioned DB — it early-returns).
    _migrate_normalize_source_uri(conn)
    conn.commit()

    chunk_uri = conn.execute(
        "SELECT source_uri FROM chunks WHERE content_hash = 'h1'"
    ).fetchone()
    assert chunk_uri["source_uri"] == "C:/Users/foo/papers/a.md"

    pp = conn.execute("SELECT path FROM paper_paths WHERE paper_id = 1").fetchone()
    assert pp["path"] == "C:/Users/foo/papers/a.md"
