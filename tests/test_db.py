"""Smoke tests for schema and basic operations."""

import sqlite3
import json
import struct

from knowledge_base.db import get_connection, init_schema, DEFAULT_EMBED_DIM


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
