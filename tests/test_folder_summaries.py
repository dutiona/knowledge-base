"""Tests for folder-level semantic embeddings."""

from unittest.mock import patch

from knowledge_base.db import DEFAULT_EMBED_DIM, get_connection, init_schema
from knowledge_base.folder_summaries import compute_folder_hash, update_folder_summary
from knowledge_base.ingest import ingest_file


def _fake_embed(texts, model="bge-m3", expected_dim=None):
    dim = expected_dim if expected_dim is not None else DEFAULT_EMBED_DIM
    return [[0.1] * dim for _ in texts]


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


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_compute_folder_hash_changes_with_content(tmp_path):
    """Hash changes when folder contents change."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    folder = tmp_path / "papers"
    folder.mkdir()
    (folder / "a.md").write_text("Paper about attention mechanisms.\n")
    ingest_file(conn, folder / "a.md")

    hash1 = compute_folder_hash(conn, str(folder))

    (folder / "b.md").write_text("Paper about transformers.\n")
    ingest_file(conn, folder / "b.md")

    hash2 = compute_folder_hash(conn, str(folder))
    assert hash1 != hash2


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_update_folder_summary_creates_entry(tmp_path):
    """update_folder_summary inserts a new folder summary."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    folder = tmp_path / "papers"
    folder.mkdir()
    (folder / "a.md").write_text("Paper about attention mechanisms.\n")
    ingest_file(conn, folder / "a.md")

    updated = update_folder_summary(conn, str(folder))
    assert updated is True

    row = conn.execute(
        "SELECT * FROM folder_summaries WHERE folder_path = ?",
        (str(folder),),
    ).fetchone()
    assert row is not None
    assert "attention" in row["summary"].lower()


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_update_folder_summary_skips_when_unchanged(tmp_path):
    """update_folder_summary returns False when content hash hasn't changed."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    folder = tmp_path / "papers"
    folder.mkdir()
    (folder / "a.md").write_text("Paper about attention.\n")
    ingest_file(conn, folder / "a.md")

    assert update_folder_summary(conn, str(folder)) is True
    assert update_folder_summary(conn, str(folder)) is False


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_update_folder_summary_updates_stale_entry(tmp_path):
    """update_folder_summary updates when content changes."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    folder = tmp_path / "papers"
    folder.mkdir()
    (folder / "a.md").write_text("Paper about attention.\n")
    ingest_file(conn, folder / "a.md")

    update_folder_summary(conn, str(folder))
    old_row = conn.execute(
        "SELECT summary FROM folder_summaries WHERE folder_path = ?",
        (str(folder),),
    ).fetchone()

    (folder / "b.md").write_text("Paper about diffusion models.\n")
    ingest_file(conn, folder / "b.md")

    assert update_folder_summary(conn, str(folder)) is True
    new_row = conn.execute(
        "SELECT summary FROM folder_summaries WHERE folder_path = ?",
        (str(folder),),
    ).fetchone()
    assert new_row["summary"] != old_row["summary"]
