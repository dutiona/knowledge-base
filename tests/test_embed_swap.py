"""Tests for embedding model swap and re-embed."""

from unittest.mock import patch

from research_index.db import DEFAULT_EMBED_DIM, get_connection, init_schema
from research_index.embed_swap import get_embed_config, re_embed
from research_index.ingest import ingest_file


NEW_DIM = 384


def _fake_embed(texts, model="bge-m3", expected_dim=None):
    dim = expected_dim if expected_dim is not None else DEFAULT_EMBED_DIM
    return [[0.1] * dim for _ in texts]


def _fake_embed_new(texts, model="mxbai-embed-large", expected_dim=None):
    dim = expected_dim if expected_dim is not None else NEW_DIM
    return [[0.2] * dim for _ in texts]


def _setup(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    return conn


def test_get_embed_config(tmp_path):
    conn = _setup(tmp_path)
    config = get_embed_config(conn)
    assert config["model"] == "bge-m3"
    assert config["dim"] == DEFAULT_EMBED_DIM


@patch("research_index.ingest.embed", _fake_embed)
def test_re_embed_changes_model(tmp_path):
    conn = _setup(tmp_path)

    # Ingest a file with old model
    md = tmp_path / "doc.md"
    md.write_text("Test content for re-embedding.\n")
    ingest_file(conn, md)

    old_count = conn.execute("SELECT COUNT(*) as c FROM chunks_vec").fetchone()["c"]
    assert old_count >= 1

    # Re-embed with new model
    with patch("research_index.embed_swap.embed", _fake_embed_new):
        result = re_embed(conn, "mxbai-embed-large", NEW_DIM)

    assert result["chunks_processed"] == old_count
    assert result["model"] == "mxbai-embed-large"
    assert result["dim"] == NEW_DIM

    # Config should be updated
    config = get_embed_config(conn)
    assert config["model"] == "mxbai-embed-large"
    assert config["dim"] == NEW_DIM

    # Vec table should still have same number of rows
    new_count = conn.execute("SELECT COUNT(*) as c FROM chunks_vec").fetchone()["c"]
    assert new_count == old_count


@patch("research_index.ingest.embed", _fake_embed)
def test_re_embed_preserves_chunk_ids(tmp_path):
    conn = _setup(tmp_path)

    md = tmp_path / "doc.md"
    md.write_text("Content to re-embed.\n")
    ingest_file(conn, md)

    chunk_ids_before = [
        r["id"] for r in conn.execute("SELECT id FROM chunks").fetchall()
    ]

    with patch("research_index.embed_swap.embed", _fake_embed_new):
        re_embed(conn, "mxbai-embed-large", NEW_DIM)

    chunk_ids_after = [
        r["id"] for r in conn.execute("SELECT id FROM chunks").fetchall()
    ]
    assert chunk_ids_before == chunk_ids_after


@patch("research_index.ingest.embed", _fake_embed)
def test_re_embed_empty_db(tmp_path):
    conn = _setup(tmp_path)

    with patch("research_index.embed_swap.embed", _fake_embed_new):
        result = re_embed(conn, "mxbai-embed-large", NEW_DIM)

    assert result["chunks_processed"] == 0
    config = get_embed_config(conn)
    assert config["model"] == "mxbai-embed-large"
