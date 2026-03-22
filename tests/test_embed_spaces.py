"""Tests for embedding space lifecycle (#99)."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from knowledge_base.db import (
    get_active_chunk_strategy,
    get_active_space,
    get_connection,
    get_vec_table_name,
    init_schema,
    insert_chunk_vec,
    space_table_name,
)
from knowledge_base.embed_swap import (
    backfill_space,
    cleanup_space,
    create_space,
    deprecate_space,
    get_embed_config,
    list_spaces,
    promote_space,
)


def _fake_embed(texts, model="x", expected_dim=None, **_kw):
    dim = expected_dim or 4
    return [[0.1] * dim for _ in texts]


class _FakeProvider:
    def embed(self, texts, model="x", expected_dim=None):
        return _fake_embed(texts, model, expected_dim)


def _setup(tmp_path: Path, dim: int = 4) -> sqlite3.Connection:
    conn = get_connection(tmp_path / "test.db")
    # Pre-seed all config keys so init_schema sees embed_model and skips its
    # own INSERT, letting us control embed_dim for small test vectors.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.executemany(
        "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
        [
            ("embed_model", "bge-m3"),
            ("embed_dim", str(dim)),
            ("embed_provider", "ollama"),
        ],
    )
    conn.commit()
    init_schema(conn)
    return conn


def _add_chunk(conn: sqlite3.Connection, content: str, index: int = 0) -> int:
    h = hashlib.sha256(content.encode()).hexdigest()[:16]
    cursor = conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES (?, ?, 'pdf', '/test/paper.pdf', ?)",
        (h, content, index),
    )
    conn.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


def test_bootstrap_creates_default_space(tmp_path):
    conn = _setup(tmp_path)
    space = get_active_space(conn)
    assert space is not None
    assert space["name"] == "default"
    assert space["status"] == "active"
    assert space["table_name"] == "chunks_vec"
    assert space["model"] == "bge-m3"
    assert space["chunk_strategy"] == "mechanical"


def test_bootstrap_idempotent(tmp_path):
    conn = _setup(tmp_path)
    # Call init_schema a second time
    init_schema(conn)
    rows = conn.execute("SELECT * FROM embed_spaces").fetchall()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def test_get_active_space(tmp_path):
    conn = _setup(tmp_path)
    space = get_active_space(conn)
    assert isinstance(space, dict)
    assert space["name"] == "default"
    assert space["dim"] == 4


def test_get_vec_table_name_default(tmp_path):
    conn = _setup(tmp_path)
    assert get_vec_table_name(conn) == "chunks_vec"


def test_get_active_chunk_strategy_default(tmp_path):
    conn = _setup(tmp_path)
    assert get_active_chunk_strategy(conn) == "mechanical"


def test_space_table_name():
    assert space_table_name("default") == "chunks_vec_default"
    assert space_table_name("bge_m3_1024") == "chunks_vec_bge_m3_1024"
    # Special chars get sanitized to underscores
    assert space_table_name("my-model.v2") == "chunks_vec_my_model_v2"
    assert space_table_name("has spaces!") == "chunks_vec_has_spaces_"


# ---------------------------------------------------------------------------
# create_space
# ---------------------------------------------------------------------------


def test_create_space(tmp_path):
    conn = _setup(tmp_path)
    result = create_space(conn, "new_space", "mxbai", 4, "ollama")
    assert result["space"] == "new_space"
    assert result["status"] == "populating"
    assert result["table_name"] == "chunks_vec_new_space"

    # Registry entry exists
    row = conn.execute("SELECT * FROM embed_spaces WHERE name = 'new_space'").fetchone()
    assert row["status"] == "populating"
    assert row["dim"] == 4

    # Vec table was created (should accept inserts)
    from knowledge_base.db import _serialize_f32

    conn.execute(
        "INSERT INTO [chunks_vec_new_space] (rowid, embedding, chunk_id) VALUES (1, ?, 1)",
        (_serialize_f32([0.1] * 4),),
    )


def test_create_space_duplicate_rejected(tmp_path):
    conn = _setup(tmp_path)
    create_space(conn, "dup", "model", 4, "ollama")
    with pytest.raises(ValueError, match="already exists"):
        create_space(conn, "dup", "model", 4, "ollama")


def test_create_space_invalid_name(tmp_path):
    conn = _setup(tmp_path)
    with pytest.raises(ValueError, match="alphanumeric"):
        create_space(conn, "has spaces", "model", 4, "ollama")
    with pytest.raises(ValueError, match="alphanumeric"):
        create_space(conn, "no-dashes", "model", 4, "ollama")


# ---------------------------------------------------------------------------
# backfill_space
# ---------------------------------------------------------------------------


@patch(
    "knowledge_base.embed_swap.get_provider",
    return_value=_FakeProvider(),
)
def test_backfill_space(mock_prov, tmp_path):
    conn = _setup(tmp_path)
    _add_chunk(conn, "chunk one", 0)
    _add_chunk(conn, "chunk two", 1)

    create_space(conn, "test_bf", "mxbai", 4, "ollama")
    result = backfill_space(conn, "test_bf", batch_size=10)

    assert result["chunks_processed"] == 2
    count = conn.execute("SELECT COUNT(*) FROM [chunks_vec_test_bf]").fetchone()[0]
    assert count == 2

    # chunk_count updated in registry
    space = conn.execute(
        "SELECT chunk_count FROM embed_spaces WHERE name = 'test_bf'"
    ).fetchone()
    assert space["chunk_count"] == 2


@patch(
    "knowledge_base.embed_swap.get_provider",
    return_value=_FakeProvider(),
)
def test_backfill_resumable(mock_prov, tmp_path):
    conn = _setup(tmp_path)
    cid1 = _add_chunk(conn, "first chunk", 0)
    _add_chunk(conn, "second chunk", 1)

    create_space(conn, "resume", "mxbai", 4, "ollama")

    # Manually insert first chunk as if a previous partial backfill happened
    from knowledge_base.db import _serialize_f32

    conn.execute(
        "INSERT INTO [chunks_vec_resume] (rowid, embedding, chunk_id) VALUES (?, ?, ?)",
        (cid1, _serialize_f32([0.1] * 4), cid1),
    )
    conn.execute("UPDATE embed_spaces SET chunk_count = 1 WHERE name = 'resume'")
    conn.commit()

    result = backfill_space(conn, "resume", batch_size=10)
    # Should only process the second chunk
    assert result["chunks_processed"] == 1

    count = conn.execute("SELECT COUNT(*) FROM [chunks_vec_resume]").fetchone()[0]
    assert count == 2


# ---------------------------------------------------------------------------
# promote_space
# ---------------------------------------------------------------------------


@patch(
    "knowledge_base.embed_swap.get_provider",
    return_value=_FakeProvider(),
)
def test_promote_space(mock_prov, tmp_path):
    conn = _setup(tmp_path)
    _add_chunk(conn, "data", 0)

    create_space(conn, "promoted", "new_model", 4, "ollama")
    backfill_space(conn, "promoted")
    result = promote_space(conn, "promoted")

    assert result["promoted"] == "promoted"
    assert result["deprecated"] == "default"

    # New space is active
    active = get_active_space(conn)
    assert active["name"] == "promoted"
    assert active["status"] == "active"

    # Old space is deprecated
    old = conn.execute(
        "SELECT status FROM embed_spaces WHERE name = 'default'"
    ).fetchone()
    assert old["status"] == "deprecated"


@patch(
    "knowledge_base.embed_swap.get_provider",
    return_value=_FakeProvider(),
)
def test_promote_updates_config(mock_prov, tmp_path):
    conn = _setup(tmp_path)
    _add_chunk(conn, "data", 0)

    create_space(conn, "cfg_test", "new_model", 4, "ollama")
    backfill_space(conn, "cfg_test")
    promote_space(conn, "cfg_test")

    config = get_embed_config(conn)
    assert config["model"] == "new_model"
    assert config["dim"] == 4
    assert config["provider"] == "ollama"


def test_promote_empty_space_blocked(tmp_path):
    conn = _setup(tmp_path)
    # Add a chunk so total_chunks > 0 in the new space
    _add_chunk(conn, "data", 0)

    create_space(conn, "empty", "model", 4, "ollama")
    # Don't backfill — chunk_count stays 0 but total_chunks > 0
    with pytest.raises(ValueError, match="0 of"):
        promote_space(conn, "empty")


# ---------------------------------------------------------------------------
# deprecate_space
# ---------------------------------------------------------------------------


def test_deprecate_active_blocked(tmp_path):
    conn = _setup(tmp_path)
    with pytest.raises(ValueError, match="Cannot deprecate the active"):
        deprecate_space(conn, "default")


# ---------------------------------------------------------------------------
# cleanup_space
# ---------------------------------------------------------------------------


@patch(
    "knowledge_base.embed_swap.get_provider",
    return_value=_FakeProvider(),
)
def test_cleanup_space(mock_prov, tmp_path):
    conn = _setup(tmp_path)
    _add_chunk(conn, "data", 0)

    create_space(conn, "to_clean", "model", 4, "ollama")
    backfill_space(conn, "to_clean")
    promote_space(conn, "to_clean")

    # Now 'default' is deprecated — clean it up
    result = cleanup_space(conn, "default")
    assert result["cleaned"] == "default"

    # Registry entry gone
    row = conn.execute("SELECT 1 FROM embed_spaces WHERE name = 'default'").fetchone()
    assert row is None

    # Only one space left
    spaces = list_spaces(conn)
    assert len(spaces) == 1
    assert spaces[0]["name"] == "to_clean"


def test_cleanup_active_blocked(tmp_path):
    conn = _setup(tmp_path)
    with pytest.raises(ValueError, match="deprecated"):
        cleanup_space(conn, "default")


# ---------------------------------------------------------------------------
# list_spaces
# ---------------------------------------------------------------------------


def test_list_spaces(tmp_path):
    conn = _setup(tmp_path)
    spaces = list_spaces(conn)
    assert len(spaces) == 1
    assert spaces[0]["name"] == "default"

    create_space(conn, "second", "model", 4, "ollama")
    spaces = list_spaces(conn)
    assert len(spaces) == 2
    names = {s["name"] for s in spaces}
    assert names == {"default", "second"}


# ---------------------------------------------------------------------------
# insert_chunk_vec helper
# ---------------------------------------------------------------------------


def test_insert_chunk_vec_helper(tmp_path):
    conn = _setup(tmp_path)
    cid = _add_chunk(conn, "vec test", 0)

    insert_chunk_vec(conn, cid, [0.1] * 4)
    conn.commit()

    row = conn.execute(
        "SELECT chunk_id FROM chunks_vec WHERE chunk_id = ?", (cid,)
    ).fetchone()
    assert row["chunk_id"] == cid


def test_insert_chunk_vec_explicit_table(tmp_path):
    conn = _setup(tmp_path)
    cid = _add_chunk(conn, "explicit table test", 0)

    create_space(conn, "explicit", "model", 4, "ollama")
    insert_chunk_vec(conn, cid, [0.2] * 4, table_name="chunks_vec_explicit")
    conn.commit()

    row = conn.execute(
        "SELECT chunk_id FROM [chunks_vec_explicit] WHERE chunk_id = ?", (cid,)
    ).fetchone()
    assert row["chunk_id"] == cid


# ---------------------------------------------------------------------------
# Unique index enforcement
# ---------------------------------------------------------------------------


def test_partial_unique_index_active(tmp_path):
    conn = _setup(tmp_path)
    # 'default' is already active — inserting another active space should fail
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO embed_spaces "
            "(name, model, provider, dim, chunk_strategy, status, table_name) "
            "VALUES ('rogue', 'model', 'ollama', 4, 'mechanical', 'active', 'chunks_vec_rogue')"
        )


# ---------------------------------------------------------------------------
# Matryoshka truncation
# ---------------------------------------------------------------------------


@patch("knowledge_base.embed_swap.get_provider", return_value=_FakeProvider())
def test_create_matryoshka_space(mock_prov, tmp_path):
    conn = _setup(tmp_path)
    result = create_space(conn, "mat_test", "qwen3", 4, "ollama", matryoshka_base_dim=8)
    assert result["space"] == "mat_test"
    space = conn.execute(
        "SELECT * FROM embed_spaces WHERE name = 'mat_test'"
    ).fetchone()
    assert space["matryoshka_base_dim"] == 8
    assert space["dim"] == 4


def test_create_matryoshka_invalid_dim(tmp_path):
    conn = _setup(tmp_path)
    with pytest.raises(ValueError, match="matryoshka_base_dim.*must be greater"):
        create_space(conn, "bad", "model", 8, "ollama", matryoshka_base_dim=4)


@patch("knowledge_base.embed_swap.get_provider")
def test_backfill_matryoshka_space(mock_get_prov, tmp_path):
    """Provider returns 8-dim vectors, space stores 4-dim truncated vectors."""
    conn = _setup(tmp_path, dim=4)

    # Mock provider returns 8-dim vectors
    class _MatProvider:
        def embed(self, texts, model="x", expected_dim=None):
            return [[0.1 * (i + 1) for i in range(8)] for _ in texts]

    mock_get_prov.return_value = _MatProvider()

    # Add a chunk
    _add_chunk(conn, "test content", 0)

    create_space(conn, "mat", "qwen3", 4, "ollama", matryoshka_base_dim=8)
    result = backfill_space(conn, "mat")
    assert result["chunks_processed"] == 1

    # Verify stored vector is 4-dim (not 8-dim)
    import struct

    row = conn.execute("SELECT embedding FROM chunks_vec_mat").fetchone()
    vec = struct.unpack(f"{4}f", row["embedding"])
    assert len(vec) == 4
