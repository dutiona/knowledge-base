"""Smoke tests for schema and basic operations."""

import sqlite3
import struct

from knowledge_base.db import (
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
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, session_id) "
        "VALUES ('h2', 'c2', 'note', '/tmp/b.md', 0, 'session-A')"
    )
    # A third doc in a different session — no co-occurrence with a.md/b.md
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, session_id) "
        "VALUES ('h3', 'c3', 'note', '/tmp/c.md', 0, 'session-B')"
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
        conn.execute(
            "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, session_id) "
            f"VALUES ('b_{sess}', 'cb', 'note', '/tmp/b.md', 0, ?)",
            (sess,),
        )
    # c.md shares only ONE session with a.md
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, session_id) "
        "VALUES ('c_s1', 'cc', 'note', '/tmp/c.md', 0, 's1')"
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
