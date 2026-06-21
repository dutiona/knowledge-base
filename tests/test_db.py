"""Smoke tests for schema and basic operations."""

import hashlib
import sqlite3
import struct

from knowledge_base.db import (
    DEFAULT_EMBED_DIM,
    DEFAULT_EMBED_MODEL,
    DEFAULT_EMBED_PROVIDER,
    _bootstrap_embed_spaces,
    _migrate_extraction_source,
    _migrate_normalize_source_uri,
    _migrate_papers_fts,
    co_occurrence_pairs,
    delete_chunk_vecs,
    delete_chunks_cascade,
    escape_like,
    get_active_space,
    get_connection,
    init_schema,
    insert_chunk_vec,
)
from knowledge_base.embed_swap import create_space


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


# ---------------------------------------------------------------------------
# escape_like (#377) — characterization
# ---------------------------------------------------------------------------


def test_char_escape_like_identity_no_special_chars():
    """Strings without \\, %, _ pass through unchanged."""
    assert escape_like("hello world") == "hello world"
    assert escape_like("") == ""
    assert escape_like("abc.def-123") == "abc.def-123"


def test_char_escape_like_percent():
    """'%' is escaped to '\\%'."""
    assert escape_like("%") == "\\%"
    assert escape_like("50%") == "50\\%"


def test_char_escape_like_underscore():
    """'_' is escaped to '\\_'."""
    assert escape_like("_") == "\\_"
    assert escape_like("a_b") == "a\\_b"


def test_char_escape_like_backslash():
    """A literal backslash is doubled to '\\\\'."""
    assert escape_like("\\") == "\\\\"
    assert escape_like("a\\b") == "a\\\\b"


def test_char_escape_like_combined_backslash_first_ordering():
    """'a_b%c\\d' locks backslash-first ordering: the escapes added for % and _
    are NOT themselves re-escaped."""
    assert escape_like("a_b%c\\d") == "a\\_b\\%c\\\\d"


def test_char_escape_like_already_escaped_sequence_doubles_backslash():
    """A literal '\\%' becomes '\\\\\\%' (backslash doubled FIRST, then % escaped),
    proving the existing backslash is treated as data, not as an escape char."""
    assert escape_like("\\%") == "\\\\\\%"


def test_char_escape_like_roundtrip_no_wildcard_injection():
    """WHERE col LIKE escape_like(s) ESCAPE '\\' matches s literally; % and _ in s
    are NOT treated as wildcards (no LIKE-injection)."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (col TEXT)")
    rows = [("100% pure",), ("100X pure",), ("a_b",), ("aXb",), ("plain",)]
    conn.executemany("INSERT INTO t (col) VALUES (?)", rows)
    conn.commit()

    # '%' must match only the literal "100% pure", not "100X pure".
    matched = conn.execute(
        "SELECT col FROM t WHERE col LIKE ? ESCAPE '\\'",
        (escape_like("100% pure"),),
    ).fetchall()
    assert matched == [("100% pure",)]

    # '_' must match only the literal "a_b", not "aXb".
    matched = conn.execute(
        "SELECT col FROM t WHERE col LIKE ? ESCAPE '\\'",
        (escape_like("a_b"),),
    ).fetchall()
    assert matched == [("a_b",)]

    # A bare '%' literal must match nothing (no row equals "%"); proves the
    # wildcard was neutralized rather than turning the query into match-all.
    matched = conn.execute(
        "SELECT col FROM t WHERE col LIKE ? ESCAPE '\\'",
        (escape_like("%"),),
    ).fetchall()
    assert matched == []
    conn.close()


# ---------------------------------------------------------------------------
# co_occurrence_pairs — min_sessions=0 boundary + ordering invariant (#433)
# ---------------------------------------------------------------------------


def _char_insert_doc_session(conn, content_hash, source_uri, session_id):
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, session_id) "
        "VALUES (?, 'c', 'note', ?, 0, ?)",
        (content_hash, source_uri, session_id),
    )
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO chunk_sessions (chunk_id, session_id) VALUES (?, ?)",
        (cid, session_id),
    )
    return cid


def test_char_co_occurrence_min_sessions_zero_includes_single_session(tmp_path):
    """min_sessions=0 -> HAVING co_sessions >= 0 includes a pair sharing a single
    session (same set as min_sessions=1, since every co-occurring pair already
    has co_sessions >= 1). The 0 floor does NOT manufacture pairs from
    docs in disjoint sessions."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    _char_insert_doc_session(conn, "h1", "/tmp/a.md", "s1")
    _char_insert_doc_session(conn, "h2", "/tmp/b.md", "s1")
    conn.commit()

    pairs = co_occurrence_pairs(conn, min_sessions=0)
    assert len(pairs) == 1
    assert pairs[0]["co_sessions"] == 1
    assert pairs[0]["source_uri_a"] == "/tmp/a.md"
    assert pairs[0]["source_uri_b"] == "/tmp/b.md"

    # A doc in a disjoint session forms no pair: count stays at 1.
    _char_insert_doc_session(conn, "h3", "/tmp/z.md", "s2")
    conn.commit()
    pairs = co_occurrence_pairs(conn, min_sessions=0)
    assert len(pairs) == 1

    # The HAVING filter is REAL, not a no-op: a threshold ABOVE the pair's
    # shared-session count (co_sessions == 1) excludes it. This distinguishes
    # the filter from a pass-through — min_sessions=0 and =1 only coincide
    # because co_sessions is always >= 1 for any pair the self-join can form.
    assert co_occurrence_pairs(conn, min_sessions=2) == []


def test_char_co_occurrence_pairs_alphabetically_ordered(tmp_path):
    """Each returned pair has source_uri_a < source_uri_b regardless of insertion
    order (the self-join enforces a.source_uri < b.source_uri)."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    # Insert the alphabetically LATER uri FIRST to prove ordering != insertion order.
    _char_insert_doc_session(conn, "hz", "/tmp/zebra.md", "s1")
    _char_insert_doc_session(conn, "ha", "/tmp/alpha.md", "s1")
    conn.commit()

    pairs = co_occurrence_pairs(conn)
    assert len(pairs) == 1
    assert pairs[0]["source_uri_a"] == "/tmp/alpha.md"
    assert pairs[0]["source_uri_b"] == "/tmp/zebra.md"
    assert pairs[0]["source_uri_a"] < pairs[0]["source_uri_b"]


def test_char_co_occurrence_result_ordered_by_co_sessions_desc(tmp_path):
    """Result list is ordered by co_sessions DESC; every pair is internally
    alphabetical. Pair (a,b) shares 2 sessions, (a,c) shares 1 -> (a,b) first."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    _char_insert_doc_session(conn, "a1", "/tmp/a.md", "s1")
    _char_insert_doc_session(conn, "b1", "/tmp/b.md", "s1")
    _char_insert_doc_session(conn, "a2", "/tmp/a.md", "s2")
    _char_insert_doc_session(conn, "b2", "/tmp/b.md", "s2")
    _char_insert_doc_session(conn, "c1", "/tmp/c.md", "s1")
    conn.commit()

    pairs = co_occurrence_pairs(conn, min_sessions=1)
    counts = [p["co_sessions"] for p in pairs]
    assert counts == sorted(counts, reverse=True)
    assert pairs[0]["co_sessions"] == 2
    assert pairs[0]["source_uri_a"] == "/tmp/a.md"
    assert pairs[0]["source_uri_b"] == "/tmp/b.md"
    for p in pairs:
        assert p["source_uri_a"] < p["source_uri_b"]


# ---------------------------------------------------------------------------
# Vec helpers & chunk_count bookkeeping characterization (#378/#379/#394)
#
# Module-level helpers for this bundle. create_space lives in embed_swap, not
# db; imported at module top. The default
# 'chunks_vec' space expects DEFAULT_EMBED_DIM (1024) vectors in this file
# (no embed_dim config pre-seeding, unlike tests/test_embed_spaces.py).
# ---------------------------------------------------------------------------


def _char_add_chunk(conn, content, index=0) -> int:
    h = hashlib.sha256(content.encode()).hexdigest()[:16]
    cursor = conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES (?, ?, 'pdf', '/test/paper.pdf', ?)",
        (h, content, index),
    )
    conn.commit()
    rowid = cursor.lastrowid
    assert rowid is not None  # lastrowid is always set after a successful INSERT
    return rowid


def _char_active_count(conn):
    space = get_active_space(conn)
    assert space is not None  # tests always run with a bootstrapped active space
    return space["chunk_count"]


def _char_space_count(conn, name):
    return conn.execute(
        "SELECT chunk_count FROM embed_spaces WHERE name = ?", (name,)
    ).fetchone()["chunk_count"]


# --- #394 insert_chunk_vec chunk_count side-effect ---


def test_char_insert_chunk_vec_increments_active_chunk_count(tmp_path):
    # WAVE 1 / #392: this pins the CURRENT per-row chunk_count increment inside
    # insert_chunk_vec. PR C batches that bookkeeping out of the per-row path —
    # when it lands, this assertion (and the round-trip test below) must move to
    # the new bulk-bump API. Expected to change, not a regression.
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    start = _char_active_count(conn)

    cid = _char_add_chunk(conn, "vec a", 0)
    insert_chunk_vec(conn, cid, [0.1] * DEFAULT_EMBED_DIM)
    conn.commit()

    assert _char_active_count(conn) == start + 1


def test_char_insert_chunk_vec_nonactive_table_leaves_active_unchanged(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    start = _char_active_count(conn)

    create_space(conn, "other", "model", DEFAULT_EMBED_DIM, "ollama")
    cid = _char_add_chunk(conn, "non-active vec", 0)
    insert_chunk_vec(
        conn, cid, [0.2] * DEFAULT_EMBED_DIM, table_name="chunks_vec_other"
    )
    conn.commit()

    # Active space (default) chunk_count is untouched: status='active' gate.
    assert _char_active_count(conn) == start
    # And the non-active space's own chunk_count also stays 0 — the increment
    # only fires for the active space, never for a 'populating' one.
    assert _char_space_count(conn, "other") == 0


def test_char_insert_then_delete_round_trips_chunk_count(tmp_path):
    # WAVE 1 / #392: also pins the current per-row insert increment (see note on
    # test_char_insert_chunk_vec_increments_active_chunk_count) — update with PR C.
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    start = _char_active_count(conn)

    cid = _char_add_chunk(conn, "round trip", 0)
    insert_chunk_vec(conn, cid, [0.3] * DEFAULT_EMBED_DIM)
    conn.commit()
    assert _char_active_count(conn) == start + 1

    delete_chunk_vecs(conn, [cid])
    conn.commit()
    assert _char_active_count(conn) == start


# --- #378 delete_chunk_vecs ---


def test_char_delete_chunk_vecs_empty_is_noop(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    cid = _char_add_chunk(conn, "present", 0)
    insert_chunk_vec(conn, cid, [0.1] * DEFAULT_EMBED_DIM)
    conn.commit()
    before = _char_active_count(conn)

    delete_chunk_vecs(conn, [])
    conn.commit()

    assert _char_active_count(conn) == before
    # Row still present
    row = conn.execute(
        "SELECT chunk_id FROM chunks_vec WHERE chunk_id = ?", (cid,)
    ).fetchone()
    assert row is not None


def test_char_delete_chunk_vecs_counts_actual_rows_not_input_length(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    # Three chunks WITH embeddings + two chunks WITHOUT embeddings.
    with_vec = []
    for i in range(3):
        cid = _char_add_chunk(conn, f"has vec {i}", i)
        insert_chunk_vec(conn, cid, [0.1 * (i + 1)] * DEFAULT_EMBED_DIM)
        with_vec.append(cid)
    no_vec = [_char_add_chunk(conn, f"no vec {j}", 10 + j) for j in range(2)]
    conn.commit()

    start = _char_active_count(conn)
    assert start == 3  # only the embedded chunks were counted on insert

    # Delete a mix: 2 that have embeddings + 2 that have none. Input length is
    # 4, but only 2 vec rows actually exist for these ids.
    delete_chunk_vecs(conn, with_vec[:2] + no_vec)
    conn.commit()

    # Decremented by 2 (rows that actually existed), NOT by 4 (input length).
    assert _char_active_count(conn) == start - 2
    # The remaining embedded chunk is still present.
    remaining = conn.execute(
        "SELECT chunk_id FROM chunks_vec WHERE chunk_id = ?", (with_vec[2],)
    ).fetchone()
    assert remaining is not None


def test_char_delete_chunk_vecs_clamps_chunk_count_at_zero(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    cids = []
    for i in range(3):
        cid = _char_add_chunk(conn, f"clamp {i}", i)
        insert_chunk_vec(conn, cid, [0.2] * DEFAULT_EMBED_DIM)
        cids.append(cid)
    conn.commit()
    assert _char_active_count(conn) == 3

    # Manually corrupt the registry so chunk_count (1) < actual rows (3).
    conn.execute(
        "UPDATE embed_spaces SET chunk_count = 1 WHERE table_name = 'chunks_vec'"
    )
    conn.commit()

    delete_chunk_vecs(conn, cids)  # actual_deleted == 3, but count was 1
    conn.commit()

    # MAX(0, 1 - 3) floors at 0 rather than going negative.
    assert _char_active_count(conn) == 0


def test_char_delete_chunk_vecs_explicit_nonactive_table(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    active_start = _char_active_count(conn)

    create_space(conn, "expl", "model", DEFAULT_EMBED_DIM, "ollama")
    cid = _char_add_chunk(conn, "explicit del", 0)
    insert_chunk_vec(conn, cid, [0.4] * DEFAULT_EMBED_DIM, table_name="chunks_vec_expl")
    conn.commit()
    # Insert into a non-active table does NOT touch any chunk_count.
    assert _char_space_count(conn, "expl") == 0
    assert _char_active_count(conn) == active_start

    # Seed a POSITIVE count on the populating space so the two candidate
    # behaviors diverge: a table_name-only decrement (current) yields
    # MAX(0, 5-1) == 4, whereas a status='active'-gated decrement would skip
    # this populating space entirely and leave it at 5. With chunk_count==0
    # both behaviors collapse to 0, which is why the bare 0-check below would
    # NOT actually pin the no-status-filter asymmetry.
    conn.execute("UPDATE embed_spaces SET chunk_count = 5 WHERE name = 'expl'")
    conn.commit()

    delete_chunk_vecs(conn, [cid], table_name="chunks_vec_expl")
    conn.commit()

    # Row gone from the explicit table.
    row = conn.execute(
        "SELECT chunk_id FROM [chunks_vec_expl] WHERE chunk_id = ?", (cid,)
    ).fetchone()
    assert row is None
    # expl.chunk_count went 5 -> 4: the decrement matched by table_name with NO
    # status='active' filter (a status-gated delete would have left it at 5).
    # This is the delete side of the insert/delete asymmetry — insert gates on
    # status='active' (pinned above), delete does not.
    assert _char_space_count(conn, "expl") == 4
    # Active default space untouched throughout.
    assert _char_active_count(conn) == active_start


def test_char_delete_chunk_vecs_int8_space(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    # int8 element_type space (non-active); insert + delete round-trips count.
    create_space(conn, "i8", "model", DEFAULT_EMBED_DIM, "ollama", element_type="int8")
    cids = []
    for i in range(2):
        cid = _char_add_chunk(conn, f"int8 {i}", i)
        insert_chunk_vec(
            conn,
            cid,
            [0.1 * (i + 1)] * DEFAULT_EMBED_DIM,
            table_name="chunks_vec_i8",
        )
        cids.append(cid)
    conn.commit()

    # Rows landed in the int8 vec table.
    n = conn.execute("SELECT COUNT(*) FROM [chunks_vec_i8]").fetchone()[0]
    assert n == 2

    # Seed a positive chunk_count so the decrement is observable (insert's
    # status='active' gate left it at 0). #378 asked to parametrize the
    # chunk_count contract across element types: the decrement is element-type
    # agnostic, so deleting the 2 int8 rows takes 5 -> MAX(0, 5-2) == 3.
    conn.execute("UPDATE embed_spaces SET chunk_count = 5 WHERE name = 'i8'")
    conn.commit()

    delete_chunk_vecs(conn, cids, table_name="chunks_vec_i8")
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM [chunks_vec_i8]").fetchone()[0]
    assert n == 0
    assert _char_space_count(conn, "i8") == 3


# --- #379 delete_chunks_cascade ---


def test_char_delete_chunks_cascade_empty_returns_zero(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    cid = _char_add_chunk(conn, "stay put", 0)
    insert_chunk_vec(conn, cid, [0.1] * DEFAULT_EMBED_DIM)
    conn.commit()
    before = _char_active_count(conn)

    assert delete_chunks_cascade(conn, []) == 0

    # Nothing mutated.
    assert _char_active_count(conn) == before
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM chunks_vec").fetchone()[0] == 1


def test_char_delete_chunks_cascade_removes_chunk_vec_and_fts(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    cid = _char_add_chunk(conn, "transformers attention mechanism cascade", 0)
    insert_chunk_vec(conn, cid, [0.1] * DEFAULT_EMBED_DIM)
    conn.commit()

    # FTS sees the content before deletion.
    pre = conn.execute(
        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'attention'"
    ).fetchall()
    assert len(pre) == 1

    n = delete_chunks_cascade(conn, [cid])
    conn.commit()

    assert n == 1
    # chunks row gone.
    assert conn.execute("SELECT 1 FROM chunks WHERE id = ?", (cid,)).fetchone() is None
    # vec row gone.
    assert (
        conn.execute("SELECT 1 FROM chunks_vec WHERE chunk_id = ?", (cid,)).fetchone()
        is None
    )
    # FTS no longer matches — the AFTER DELETE trigger cleaned the index.
    post = conn.execute(
        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'attention'"
    ).fetchall()
    assert len(post) == 0


def test_char_delete_chunks_cascade_return_counts_input_not_actual(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    real = _char_add_chunk(conn, "real chunk to delete", 0)
    insert_chunk_vec(conn, real, [0.1] * DEFAULT_EMBED_DIM)
    conn.commit()

    bogus = real + 9999  # an id that does not exist

    n = delete_chunks_cascade(conn, [real, bogus])
    conn.commit()

    # SURPRISE: return value is len(input) == 2, even though only ONE row
    # actually existed and was removed. The function reports inputs, not
    # rows-affected.
    assert n == 2
    # Only the real chunk was actually present, and it is now gone.
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Legacy migration paths & bootstrap (#395, #396)
#
# init_schema() early-returns on a *stamped* DB; an UNSTAMPED legacy DB (no
# 'schema_version' config key) runs the full migration chain. Each test seeds
# only the OLD form of the table under test; init_schema's idempotent
# CREATE-IF-NOT-EXISTS builds supply the rest. _serialize_f32 is the module
# helper already defined at the top of this file (reused, not redefined).
# ---------------------------------------------------------------------------


_LEGACY_CHUNKS_FTS_DDL = """
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    content, content='chunks', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


def _char_seed_legacy_config(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT OR IGNORE INTO config (key, value) VALUES ('embed_model', 'bge-m3');
        INSERT OR IGNORE INTO config (key, value) VALUES ('embed_dim', '1024');
        INSERT OR IGNORE INTO config (key, value) VALUES ('embed_provider', 'ollama');
        """
    )


# === [#395] _migrate_source_type_figure ====================================


def test_char_migrate_source_type_figure_preserves_rows_and_accepts_figure(tmp_path):
    """OLD chunks (no 'figure' in CHECK, no chunk_strategy col) -> after
    init_schema: every pre-existing row survives with correct column values,
    'figure' rows are accepted, and the re-created FTS triggers still fire."""
    conn = get_connection(tmp_path / "test.db")
    _char_seed_legacy_config(conn)
    # OLD chunks table: source_type CHECK lacks 'figure', NO chunk_strategy col,
    # but HAS session_id (so the migration's conditional session_id branch runs).
    conn.executescript(
        """
        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_hash TEXT NOT NULL UNIQUE,
            content TEXT NOT NULL,
            source_type TEXT NOT NULL CHECK(source_type IN ('pdf','markdown','code','web','note')),
            source_uri TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            session_id TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            metadata TEXT DEFAULT '{}'
        );
        """
    )
    conn.executescript(_LEGACY_CHUNKS_FTS_DDL)
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, session_id, metadata) "
        "VALUES ('old1', 'attention is all you need', 'pdf', '/p/a.pdf', 3, 'sess-X', '{\"k\":1}')"
    )
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('old2', 'second legacy chunk', 'note', '/p/b.md', 7)"
    )
    conn.commit()

    init_schema(conn)

    # 'figure' is now an accepted source_type (CHECK was widened by the rebuild).
    row = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'chunks'").fetchone()
    assert "'figure'" in row[0]

    # Every pre-existing row survived, with column values intact.
    r1 = conn.execute("SELECT * FROM chunks WHERE content_hash = 'old1'").fetchone()
    assert r1["source_type"] == "pdf"
    assert r1["source_uri"] == "/p/a.pdf"
    assert r1["chunk_index"] == 3
    assert r1["session_id"] == "sess-X"
    assert r1["metadata"] == '{"k":1}'
    # chunk_strategy column was added by the rebuild with its default.
    assert r1["chunk_strategy"] == "mechanical"
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 2

    # A 'figure' row inserts cleanly post-migration.
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('fig1', 'figure caption', 'figure', '/p/fig.png', 0)"
    )
    conn.commit()
    assert (
        conn.execute(
            "SELECT source_type FROM chunks WHERE content_hash = 'fig1'"
        ).fetchone()["source_type"]
        == "figure"
    )

    # The re-created INSERT trigger still mirrors content into the FTS index.
    hit = conn.execute(
        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'caption'"
    ).fetchall()
    assert len(hit) == 1
    # And the legacy pre-migration content is also searchable (the rebuild
    # preserved the external-content FTS rows mapped by rowid=id).
    legacy_hit = conn.execute(
        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'attention'"
    ).fetchall()
    assert len(legacy_hit) == 1


def test_char_migrate_source_type_figure_minimal_old_schema_no_optional_cols(tmp_path):
    """Very old chunks: neither session_id NOR chunk_strategy present. The
    migration's INSERT...SELECT must still preserve the base columns and add
    both new columns with their defaults (session_id NULL, strategy mechanical)."""
    conn = get_connection(tmp_path / "test.db")
    _char_seed_legacy_config(conn)
    conn.executescript(
        """
        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_hash TEXT NOT NULL UNIQUE,
            content TEXT NOT NULL,
            source_type TEXT NOT NULL CHECK(source_type IN ('pdf','markdown','code','web','note')),
            source_uri TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            metadata TEXT DEFAULT '{}'
        );
        """
    )
    conn.executescript(_LEGACY_CHUNKS_FTS_DDL)
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('veryold', 'legacy body', 'code', '/p/x.py', 11)"
    )
    conn.commit()

    init_schema(conn)

    r = conn.execute("SELECT * FROM chunks WHERE content_hash = 'veryold'").fetchone()
    assert r["source_type"] == "code"
    assert r["chunk_index"] == 11
    assert r["session_id"] is None
    assert r["chunk_strategy"] == "mechanical"


# === [#395] _migrate_relationship_types ====================================


def test_char_migrate_relationship_types_accepts_similar_and_survives(tmp_path):
    """OLD relationships (CHECK lacks 'similar') -> after init_schema the
    'similar' relation_type is accepted and the pre-existing row survives."""
    conn = get_connection(tmp_path / "test.db")
    _char_seed_legacy_config(conn)
    # papers needs abstract_chunk_id because _migrate_paper_paths runs earlier in
    # the chain and SELECTs it; the idempotent CREATE TABLE IF NOT EXISTS won't
    # add it to an already-present legacy papers table.
    conn.executescript(
        """
        CREATE TABLE papers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            authors TEXT DEFAULT '[]',
            abstract_chunk_id INTEGER REFERENCES chunks(id)
        );
        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_hash TEXT NOT NULL UNIQUE,
            content TEXT NOT NULL,
            source_type TEXT NOT NULL CHECK(source_type IN ('pdf','markdown','code','web','note','figure')),
            source_uri TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            session_id TEXT,
            chunk_strategy TEXT NOT NULL DEFAULT 'mechanical',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            metadata TEXT DEFAULT '{}'
        );
        """
    )
    conn.executescript(_LEGACY_CHUNKS_FTS_DDL)
    conn.executescript(
        """
        CREATE TABLE relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_paper_id INTEGER NOT NULL REFERENCES papers(id),
            target_paper_id INTEGER NOT NULL REFERENCES papers(id),
            relation_type TEXT NOT NULL CHECK(relation_type IN
                ('extends','contradicts','replicates','cites','compares','applies','implements')),
            confidence REAL DEFAULT 1.0,
            evidence_chunk_id INTEGER REFERENCES chunks(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(source_paper_id, target_paper_id, relation_type)
        );
        INSERT INTO papers (title) VALUES ('Paper A');
        INSERT INTO papers (title) VALUES ('Paper B');
        INSERT INTO relationships (source_paper_id, target_paper_id, relation_type, confidence)
            VALUES (1, 2, 'cites', 0.7);
        """
    )
    conn.commit()

    init_schema(conn)

    # CHECK widened to include 'similar'.
    rsql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'relationships'"
    ).fetchone()
    assert "'similar'" in rsql[0]

    # Pre-existing row survived with values intact.
    pre = conn.execute(
        "SELECT * FROM relationships WHERE relation_type = 'cites'"
    ).fetchone()
    assert pre["source_paper_id"] == 1
    assert pre["target_paper_id"] == 2
    assert pre["confidence"] == 0.7

    # 'similar' now accepted.
    conn.execute(
        "INSERT INTO relationships (source_paper_id, target_paper_id, relation_type) "
        "VALUES (2, 1, 'similar')"
    )
    conn.commit()
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM relationships WHERE relation_type = 'similar'"
        ).fetchone()[0]
        == 1
    )


# === [#395] _migrate_jobs_types ============================================


def test_char_migrate_jobs_types_accepts_auto_relate_and_rebuilds_index(tmp_path):
    """OLD jobs (CHECK lacks 'auto_relate') -> after init_schema the value is
    accepted, the pre-existing job survives, and idx_jobs_status_created exists."""
    conn = get_connection(tmp_path / "test.db")
    _char_seed_legacy_config(conn)
    conn.executescript(
        """
        CREATE TABLE papers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            authors TEXT DEFAULT '[]',
            abstract_chunk_id INTEGER
        );
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
            job_type TEXT NOT NULL CHECK(job_type IN ('extract_structure', 'extract_figures')),
            params TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'running', 'completed', 'failed')),
            progress TEXT,
            result TEXT,
            error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            started_at TEXT,
            completed_at TEXT
        );
        CREATE INDEX idx_jobs_status_created ON jobs(status, created_at);
        INSERT INTO papers (title) VALUES ('Paper A');
        INSERT INTO jobs (paper_id, job_type, status) VALUES (1, 'extract_structure', 'completed');
        """
    )
    conn.commit()

    init_schema(conn)

    jsql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'jobs'").fetchone()
    assert "'auto_relate'" in jsql[0]

    # Pre-existing job survived.
    pre = conn.execute(
        "SELECT * FROM jobs WHERE job_type = 'extract_structure'"
    ).fetchone()
    assert pre["paper_id"] == 1
    assert pre["status"] == "completed"

    # Index re-created after the table swap.
    idx = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_jobs_status_created'"
    ).fetchone()
    assert idx is not None

    # 'auto_relate' accepted.
    conn.execute("INSERT INTO jobs (paper_id, job_type) VALUES (1, 'auto_relate')")
    conn.commit()
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE job_type = 'auto_relate'"
        ).fetchone()[0]
        == 1
    )


# === [#395] _migrate_papers_fts (backfill guard contract) ==================


def test_char_migrate_papers_fts_count_guard_reflects_content_table(tmp_path):
    """SURPRISE pinned: papers_fts is an external-content FTS5 table, so
    `SELECT count(*) FROM papers_fts` returns the *papers* (content) table row
    count, NOT the number of indexed FTS entries. Build a legacy schema where
    the FTS index is genuinely UNPOPULATED (no trigger has fired) yet papers
    has a row -> the `count > 0` guard in _migrate_papers_fts short-circuits and
    its backfill INSERT never runs. The title stays unsearchable."""
    conn = get_connection(tmp_path / "test.db")
    # Legacy schema: papers + external-content papers_fts, NO trigger to populate.
    conn.executescript(
        """
        CREATE TABLE papers (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL);
        CREATE VIRTUAL TABLE papers_fts USING fts5(
            title, content='papers', content_rowid='id', tokenize='porter unicode61'
        );
        INSERT INTO papers (title) VALUES ('Reinforcement Learning Survey');
        """
    )
    conn.commit()

    # The FTS index has no entries yet (MATCH finds nothing)...
    assert (
        conn.execute(
            "SELECT rowid FROM papers_fts WHERE papers_fts MATCH 'reinforcement'"
        ).fetchall()
        == []
    )
    # ...but count(*) reflects the content table (papers), so it reads as 1.
    assert conn.execute("SELECT COUNT(*) FROM papers_fts").fetchone()[0] == 1

    _migrate_papers_fts(conn)
    conn.commit()

    # Because count(*) > 0, the guard returned early: the backfill INSERT was
    # NOT executed, so the title remains unsearchable. (Dead-code backfill.)
    assert (
        conn.execute(
            "SELECT rowid FROM papers_fts WHERE papers_fts MATCH 'reinforcement'"
        ).fetchall()
        == []
    )

    # Non-vacuous guard: prove the unsearchable result is the MIGRATION's failure,
    # not an unpopulatable index. A forced FTS rebuild DOES make the title
    # searchable — so a correct backfill would have too. This distinguishes the
    # buggy short-circuit from "the index simply cannot be populated here".
    conn.execute("INSERT INTO papers_fts(papers_fts) VALUES('rebuild')")
    conn.commit()
    assert (
        conn.execute(
            "SELECT rowid FROM papers_fts WHERE papers_fts MATCH 'reinforcement'"
        ).fetchall()
        != []
    )


def test_char_migrate_papers_fts_noop_guards_dont_raise(tmp_path):
    """The early-return guards (missing papers_fts table; empty papers) are
    no-ops that do not raise."""
    # (1) papers_fts table absent -> first guard returns.
    conn = get_connection(tmp_path / "absent.db")
    conn.executescript(
        "CREATE TABLE papers (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL);"
    )
    conn.commit()
    _migrate_papers_fts(conn)  # no raise

    # (2) papers_fts present but papers empty -> count==0, paper_count==0 guard.
    conn2 = get_connection(tmp_path / "empty.db")
    conn2.executescript(
        """
        CREATE TABLE papers (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL);
        CREATE VIRTUAL TABLE papers_fts USING fts5(
            title, content='papers', content_rowid='id', tokenize='porter unicode61'
        );
        """
    )
    conn2.commit()
    assert conn2.execute("SELECT COUNT(*) FROM papers_fts").fetchone()[0] == 0
    _migrate_papers_fts(conn2)  # no raise
    assert conn2.execute("SELECT COUNT(*) FROM papers_fts").fetchone()[0] == 0


# === [#395] _migrate_extraction_source =====================================


def test_char_migrate_extraction_source_adds_source_column(tmp_path):
    """OLD methods/datasets/metrics/entities (no 'source' col) -> the migration
    adds a NOT NULL DEFAULT 'user' source column to each, defaulting existing
    rows to 'user'."""
    conn = get_connection(tmp_path / "test.db")
    _char_seed_legacy_config(conn)
    # init_schema would create these WITH the source col, so build them WITHOUT
    # it and call the migration directly against the legacy form.
    conn.executescript(
        """
        CREATE TABLE papers (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL);
        CREATE TABLE methods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            paper_id INTEGER NOT NULL REFERENCES papers(id),
            UNIQUE(name, paper_id)
        );
        CREATE TABLE datasets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            paper_id INTEGER NOT NULL REFERENCES papers(id),
            UNIQUE(name, paper_id)
        );
        CREATE TABLE metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            value REAL NOT NULL,
            paper_id INTEGER NOT NULL REFERENCES papers(id)
        );
        CREATE TABLE entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_name TEXT NOT NULL,
            entity_type TEXT NOT NULL CHECK(entity_type IN ('method','dataset','metric')),
            paper_id INTEGER NOT NULL REFERENCES papers(id),
            UNIQUE(canonical_name, entity_type, paper_id)
        );
        INSERT INTO papers (title) VALUES ('P');
        INSERT INTO methods (name, paper_id) VALUES ('CNN', 1);
        """
    )
    conn.commit()

    for table in ("methods", "datasets", "metrics", "entities"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        assert "source" not in cols

    _migrate_extraction_source(conn)

    for table in ("methods", "datasets", "metrics", "entities"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        assert "source" in cols

    # Existing row got the default value.
    row = conn.execute("SELECT source FROM methods WHERE name = 'CNN'").fetchone()
    assert row["source"] == "user"


def test_char_migrate_extraction_source_idempotent(tmp_path):
    """Run on a current DB (source col already present) -> no-op, no raise."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    # Should not raise even though all four tables already have 'source'.
    _migrate_extraction_source(conn)
    for table in ("methods", "datasets", "metrics", "entities"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        assert "source" in cols


# === [#396] _bootstrap_embed_spaces ========================================


def test_char_bootstrap_counts_existing_vecs_and_forces_mechanical(tmp_path):
    """(a) chunks_vec pre-populated with K embeddings + config.chunk_strategy set
    to 'semantic' -> default space gets chunk_count == K and chunk_strategy
    FORCED to 'mechanical' regardless of config (per _bootstrap_embed_spaces)."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    # Reset the bootstrapped state to re-exercise the bootstrap on a populated DB.
    conn.execute("DELETE FROM embed_spaces")
    conn.execute("UPDATE config SET value = 'semantic' WHERE key = 'chunk_strategy'")
    conn.commit()

    # Insert K chunks + K embeddings into the legacy default chunks_vec table.
    k = 3
    for i in range(k):
        conn.execute(
            "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
            "VALUES (?, ?, 'note', ?, 0)",
            (f"h{i}", f"content {i}", f"/p/{i}.md"),
        )
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO chunks_vec (rowid, embedding, chunk_id) VALUES (?, ?, ?)",
            (cid, _serialize_f32([0.1] * DEFAULT_EMBED_DIM), cid),
        )
    conn.commit()

    _bootstrap_embed_spaces(conn, DEFAULT_EMBED_DIM)

    space = conn.execute("SELECT * FROM embed_spaces WHERE name = 'default'").fetchone()
    assert space is not None
    assert space["chunk_count"] == k
    # Forced to 'mechanical' even though config.chunk_strategy == 'semantic'.
    assert space["chunk_strategy"] == "mechanical"
    assert space["status"] == "active"
    assert space["table_name"] == "chunks_vec"


def test_char_bootstrap_picks_up_custom_config_model_provider(tmp_path):
    """(b) custom config embed_model/embed_provider -> bootstrapped space adopts
    them (not the DEFAULT_* fallbacks)."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    conn.execute("DELETE FROM embed_spaces")
    conn.execute("UPDATE config SET value = 'custom-model' WHERE key = 'embed_model'")
    conn.execute("UPDATE config SET value = 'openai' WHERE key = 'embed_provider'")
    conn.commit()

    _bootstrap_embed_spaces(conn, DEFAULT_EMBED_DIM)

    space = conn.execute("SELECT * FROM embed_spaces WHERE name = 'default'").fetchone()
    assert space["model"] == "custom-model"
    assert space["provider"] == "openai"
    assert space["model"] != DEFAULT_EMBED_MODEL
    assert space["provider"] != DEFAULT_EMBED_PROVIDER
    assert space["dim"] == DEFAULT_EMBED_DIM


def test_char_bootstrap_falls_back_to_defaults_when_config_absent(tmp_path):
    """(b cont.) embed_model/embed_provider config rows ABSENT -> space falls
    back to DEFAULT_EMBED_MODEL / DEFAULT_EMBED_PROVIDER."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    conn.execute("DELETE FROM embed_spaces")
    conn.execute("DELETE FROM config WHERE key IN ('embed_model', 'embed_provider')")
    conn.commit()

    _bootstrap_embed_spaces(conn, DEFAULT_EMBED_DIM)

    space = conn.execute("SELECT * FROM embed_spaces WHERE name = 'default'").fetchone()
    assert space["model"] == DEFAULT_EMBED_MODEL
    assert space["provider"] == DEFAULT_EMBED_PROVIDER


def test_char_bootstrap_count_falls_back_to_zero_without_chunks_vec(tmp_path):
    """(c) DB lacking chunks_vec -> count falls back to 0 without raising
    (_bootstrap_embed_spaces swallows the OperationalError)."""
    conn = get_connection(tmp_path / "test.db")
    # Minimal hand-built schema: config + embed_spaces but NO chunks_vec table.
    conn.executescript(
        """
        CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO config (key, value) VALUES ('embed_model', 'bge-m3');
        INSERT INTO config (key, value) VALUES ('embed_provider', 'ollama');
        CREATE TABLE embed_spaces (
            name TEXT PRIMARY KEY,
            model TEXT NOT NULL,
            provider TEXT NOT NULL,
            dim INTEGER NOT NULL,
            chunk_strategy TEXT NOT NULL DEFAULT 'mechanical'
                CHECK(chunk_strategy IN ('mechanical', 'semantic')),
            status TEXT NOT NULL CHECK(status IN ('active', 'populating', 'deprecated')),
            table_name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            chunk_count INTEGER DEFAULT 0
        );
        """
    )
    conn.commit()

    # No raise despite chunks_vec being absent.
    _bootstrap_embed_spaces(conn, DEFAULT_EMBED_DIM)

    space = conn.execute("SELECT * FROM embed_spaces WHERE name = 'default'").fetchone()
    assert space is not None
    assert space["chunk_count"] == 0


def test_char_bootstrap_idempotent_second_call_is_noop(tmp_path):
    """(d) a second bootstrap call early-returns (existing-space guard) and does
    not insert a duplicate or mutate the existing default space."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)  # already bootstrapped one 'default' space

    before = conn.execute("SELECT COUNT(*) FROM embed_spaces").fetchone()[0]
    assert before == 1
    orig = dict(
        conn.execute("SELECT * FROM embed_spaces WHERE name = 'default'").fetchone()
    )

    _bootstrap_embed_spaces(conn, DEFAULT_EMBED_DIM)

    after = conn.execute("SELECT COUNT(*) FROM embed_spaces").fetchone()[0]
    assert after == 1
    now = dict(
        conn.execute("SELECT * FROM embed_spaces WHERE name = 'default'").fetchone()
    )
    assert now == orig
