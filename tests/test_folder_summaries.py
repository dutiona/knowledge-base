"""Tests for folder-level semantic embeddings."""

from knowledge_base.db import get_connection, init_schema


def test_folder_summaries_table_exists(tmp_path):
    """init_schema creates folder_summaries and folder_summaries_vec tables."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table')"
        ).fetchall()
    }
    assert "folder_summaries" in tables
    assert "folder_summaries_vec" in tables


def test_folder_summaries_columns(tmp_path):
    """folder_summaries has the expected columns."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    cols = {
        row[1] for row in conn.execute("PRAGMA table_info(folder_summaries)").fetchall()
    }
    assert cols == {"folder_path", "summary", "content_hash", "updated_at"}
