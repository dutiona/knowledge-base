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


def _declare(monkeypatch, model: str, dim: int) -> None:
    """Only the declared identity can be promoted (#569)."""
    monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_MODEL", model)
    monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_DIM", str(dim))


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
    conn.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
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
    assert cursor.lastrowid is not None
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
    result = create_space(conn, "new_space", "mxbai", 4)
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
    create_space(conn, "dup", "model", 4)
    with pytest.raises(ValueError, match="already exists"):
        create_space(conn, "dup", "model", 4)


def test_create_space_invalid_name(tmp_path):
    conn = _setup(tmp_path)
    with pytest.raises(ValueError, match="alphanumeric"):
        create_space(conn, "has spaces", "model", 4)
    with pytest.raises(ValueError, match="alphanumeric"):
        create_space(conn, "no-dashes", "model", 4)


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

    create_space(conn, "test_bf", "mxbai", 4)
    result = backfill_space(conn, "test_bf", batch_size=10)

    assert result["chunks_processed"] == 2
    count = conn.execute("SELECT COUNT(*) FROM [chunks_vec_test_bf]").fetchone()[0]
    assert count == 2

    # chunk_count updated in registry
    space = conn.execute("SELECT chunk_count FROM embed_spaces WHERE name = 'test_bf'").fetchone()
    assert space["chunk_count"] == 2


@patch(
    "knowledge_base.embed_swap.get_provider",
    return_value=_FakeProvider(),
)
def test_backfill_resumable(mock_prov, tmp_path):
    conn = _setup(tmp_path)
    cid1 = _add_chunk(conn, "first chunk", 0)
    _add_chunk(conn, "second chunk", 1)

    create_space(conn, "resume", "mxbai", 4)

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
def test_promote_space(mock_prov, tmp_path, monkeypatch):
    conn = _setup(tmp_path)
    _add_chunk(conn, "data", 0)

    create_space(conn, "promoted", "new_model", 4)
    backfill_space(conn, "promoted")
    _declare(monkeypatch, "new_model", 4)
    result = promote_space(conn, "promoted")

    assert result["promoted"] == "promoted"
    assert result["deprecated"] == "default"

    # New space is active
    active = get_active_space(conn)
    assert active is not None
    assert active["name"] == "promoted"
    assert active["status"] == "active"

    # Old space is deprecated
    old = conn.execute("SELECT status FROM embed_spaces WHERE name = 'default'").fetchone()
    assert old["status"] == "deprecated"


@patch(
    "knowledge_base.embed_swap.get_provider",
    return_value=_FakeProvider(),
)
def test_promote_updates_config(mock_prov, tmp_path, monkeypatch):
    conn = _setup(tmp_path)
    _add_chunk(conn, "data", 0)

    create_space(conn, "cfg_test", "new_model", 4)
    backfill_space(conn, "cfg_test")
    _declare(monkeypatch, "new_model", 4)
    promote_space(conn, "cfg_test")

    config = get_embed_config(conn)
    assert config["model"] == "new_model"
    assert config["dim"] == 4
    assert config["provider"] == "ollama"
    identity_keys = {key for (key,) in conn.execute("SELECT key FROM config WHERE key LIKE 'embed_%'")}
    assert identity_keys - {"embed_base_url", "embed_api_key"} == {"embed_provider", "embed_model", "embed_dim"}


def test_promote_empty_space_blocked(tmp_path, monkeypatch):
    conn = _setup(tmp_path)
    # Add a chunk so total_chunks > 0 in the new space
    _add_chunk(conn, "data", 0)

    create_space(conn, "empty", "model", 4)
    # Don't backfill — chunk_count stays 0 but total_chunks > 0
    _declare(monkeypatch, "model", 4)
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
def test_cleanup_space(mock_prov, tmp_path, monkeypatch):
    conn = _setup(tmp_path)
    _add_chunk(conn, "data", 0)

    create_space(conn, "to_clean", "model", 4)
    backfill_space(conn, "to_clean")
    _declare(monkeypatch, "model", 4)
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

    create_space(conn, "second", "model", 4)
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

    row = conn.execute("SELECT chunk_id FROM chunks_vec WHERE chunk_id = ?", (cid,)).fetchone()
    assert row["chunk_id"] == cid


def test_insert_chunk_vec_explicit_table(tmp_path):
    conn = _setup(tmp_path)
    cid = _add_chunk(conn, "explicit table test", 0)

    create_space(conn, "explicit", "model", 4)
    insert_chunk_vec(conn, cid, [0.2] * 4, table_name="chunks_vec_explicit")
    conn.commit()

    row = conn.execute("SELECT chunk_id FROM [chunks_vec_explicit] WHERE chunk_id = ?", (cid,)).fetchone()
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
    result = create_space(conn, "mat_test", "qwen3", 4, matryoshka_base_dim=8)
    assert result["space"] == "mat_test"
    space = conn.execute("SELECT * FROM embed_spaces WHERE name = 'mat_test'").fetchone()
    assert space["matryoshka_base_dim"] == 8
    assert space["dim"] == 4


def test_create_matryoshka_invalid_dim(tmp_path):
    conn = _setup(tmp_path)
    with pytest.raises(ValueError, match=r"matryoshka_base_dim.*must be greater"):
        create_space(conn, "bad", "model", 8, matryoshka_base_dim=4)


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

    create_space(conn, "mat", "qwen3", 4, matryoshka_base_dim=8)
    result = backfill_space(conn, "mat")
    assert result["chunks_processed"] == 1

    # Verify stored vector is 4-dim (not 8-dim)
    import struct

    row = conn.execute("SELECT embedding FROM chunks_vec_mat").fetchone()
    vec = struct.unpack(f"{4}f", row["embedding"])
    assert len(vec) == 4


# ---------------------------------------------------------------------------
# element_type support
# ---------------------------------------------------------------------------


def test_create_space_default_element_type(tmp_path):
    conn = _setup(tmp_path)
    create_space(conn, "f32_space", "bge-m3", 4)

    row = conn.execute("SELECT element_type FROM embed_spaces WHERE name = 'f32_space'").fetchone()
    assert row["element_type"] == "float32"


def test_migration_adds_element_type(tmp_path):
    conn = _setup(tmp_path)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(embed_spaces)").fetchall()}
    assert "element_type" in columns


# ---------------------------------------------------------------------------
# #579: refused chunks
# ---------------------------------------------------------------------------


class _RefusingProvider:
    """Answers ``None`` (a zero-norm vector) for the text ``zero``; records every text it is sent."""

    def __init__(self, on_embed=None):
        self.sent: list[str] = []
        self.on_embed = on_embed

    def embed(self, texts, model="x", expected_dim=None):
        self.sent.extend(texts)
        if self.on_embed is not None:
            self.on_embed()
        return [None if t == "zero" else [0.1] * (expected_dim or 4) for t in texts]


def _rows(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]  # noqa: S608


def _count(conn, name: str) -> int:
    return conn.execute("SELECT chunk_count FROM embed_spaces WHERE name = ?", (name,)).fetchone()[0]


def test_backfill_counts_only_stored_vectors(tmp_path):
    conn = _setup(tmp_path)
    for i, text in enumerate(["one", "zero", "two"]):
        _add_chunk(conn, text, i)
    create_space(conn, "s", "m", 4)
    with patch("knowledge_base.embed_swap.get_provider", return_value=_RefusingProvider()):
        result = backfill_space(conn, "s")
    assert _count(conn, "s") == 2 == _rows(conn, "chunks_vec_s")
    assert result["chunks_refused"] == 1


def test_backfill_never_resends_a_refused_chunk(tmp_path, caplog):
    conn = _setup(tmp_path)
    for i, text in enumerate(["one", "zero", "two"]):
        _add_chunk(conn, text, i)
    create_space(conn, "s", "m", 4)
    with patch("knowledge_base.embed_swap.get_provider", return_value=_RefusingProvider()):
        backfill_space(conn, "s")
    assert "space 's' records it and will not send it again" in caplog.text
    again = _RefusingProvider()
    with patch("knowledge_base.embed_swap.get_provider", return_value=again):
        backfill_space(conn, "s")
    assert again.sent == []
    assert _count(conn, "s") == 2


def test_a_space_whose_only_gaps_are_refusals_is_promoted(tmp_path, monkeypatch):
    """The trap: today's over-count is what lets such a space be promoted, so the count alone cannot be fixed."""
    conn = _setup(tmp_path)
    _add_chunk(conn, "one", 0)
    _add_chunk(conn, "zero", 1)
    create_space(conn, "s", "m", 4)
    _declare(monkeypatch, "m", 4)
    with patch("knowledge_base.embed_swap.get_provider", return_value=_RefusingProvider()):
        backfill_space(conn, "s")
        assert promote_space(conn, "s")["promoted"] == "s"
    assert _count(conn, "s") == 1


def test_a_zero_matryoshka_prefix_is_a_refusal(tmp_path):
    conn = _setup(tmp_path, dim=4)
    _add_chunk(conn, "fine", 0)
    _add_chunk(conn, "zero prefix", 1)

    class _Provider:
        def embed(self, texts, model="x", expected_dim=None):
            return [[0, 0, 0, 0, 1, 1, 1, 1] if t == "zero prefix" else [0.1] * 8 for t in texts]

    create_space(conn, "mat", "qwen3", 4, matryoshka_base_dim=8)
    with patch("knowledge_base.embed_swap.get_provider", return_value=_Provider()):
        result = backfill_space(conn, "mat")
    assert result["chunks_refused"] == 1
    assert _count(conn, "mat") == 1


def test_a_space_with_no_vector_is_never_promoted(tmp_path, monkeypatch):
    conn = _setup(tmp_path)
    _add_chunk(conn, "zero", 0)
    create_space(conn, "s", "m", 4)
    _declare(monkeypatch, "m", 4)
    with patch("knowledge_base.embed_swap.get_provider", return_value=_RefusingProvider()):
        backfill_space(conn, "s")
        with pytest.raises(ValueError, match="no usable vector for any of its 1 chunks"):
            promote_space(conn, "s")


def test_a_chunk_added_after_the_backfill_blocks_promotion_until_backfilled(tmp_path, monkeypatch):
    conn = _setup(tmp_path)
    _add_chunk(conn, "one", 0)
    _add_chunk(conn, "zero", 1)
    create_space(conn, "s", "m", 4)
    _declare(monkeypatch, "m", 4)
    with patch("knowledge_base.embed_swap.get_provider", return_value=_RefusingProvider()):
        backfill_space(conn, "s")
        _add_chunk(conn, "three", 2)
        with pytest.raises(ValueError, match="only 2 of 3 chunks backfilled"):
            promote_space(conn, "s")
        backfill_space(conn, "s")
        assert promote_space(conn, "s")["promoted"] == "s"


def test_a_deprecated_space_with_pending_chunks_names_re_embed(tmp_path, monkeypatch):
    conn = _setup(tmp_path)
    insert_chunk_vec(conn, _add_chunk(conn, "one", 0), [0.1] * 4)  # the default space is complete
    conn.commit()
    create_space(conn, "s", "m", 4)
    _declare(monkeypatch, "m", 4)
    with patch("knowledge_base.embed_swap.get_provider", return_value=_RefusingProvider()):
        backfill_space(conn, "s")
        promote_space(conn, "s")
        _declare(monkeypatch, "bge-m3", 4)
        promote_space(conn, "default")  # "s" is now deprecated
        _add_chunk(conn, "two", 1)
        _declare(monkeypatch, "m", 4)
        with pytest.raises(ValueError, match="knowledge-base-ingest re-embed"):
            promote_space(conn, "s")


def test_backfill_writes_nothing_for_a_chunk_changed_while_it_embeds(tmp_path):
    conn = _setup(tmp_path)
    kept = _add_chunk(conn, "one", 0)
    gone = _add_chunk(conn, "zero", 1)
    rewritten = _add_chunk(conn, "two", 2)
    create_space(conn, "s", "m", 4)
    # Opened after create_space: the writer's first statement is a bare DELETE on chunks, which SQLite < 3.54.1 fails
    # on a connection older than any other connection's DDL (#633).
    other = get_connection(tmp_path / "test.db")

    def _concurrent_writer():
        other.execute("DELETE FROM chunks WHERE id = ?", (gone,))
        other.execute("UPDATE chunks SET content = 'two, rewritten', content_hash = 'new' WHERE id = ?", (rewritten,))
        other.commit()

    with patch("knowledge_base.embed_swap.get_provider", return_value=_RefusingProvider(_concurrent_writer)):
        backfill_space(conn, "s")  # no foreign-key error for the deleted, refused chunk
    vectors = {r[0] for r in conn.execute("SELECT chunk_id FROM chunks_vec_s")}
    assert vectors == {kept}
    assert conn.execute("SELECT COUNT(*) FROM embed_refusals").fetchone()[0] == 0
    with patch("knowledge_base.embed_swap.get_provider", return_value=_RefusingProvider()) as again:
        backfill_space(conn, "s")
    assert {r[0] for r in conn.execute("SELECT chunk_id FROM chunks_vec_s")} == {kept, rewritten}
    assert again.return_value.sent == ["two, rewritten"]
    other.close()


def test_a_refusal_ends_with_its_chunk_its_content_or_its_space(tmp_path, monkeypatch):
    conn = _setup(tmp_path)
    deleted = _add_chunk(conn, "zero", 0)
    create_space(conn, "s", "m", 4)
    with patch("knowledge_base.embed_swap.get_provider", return_value=_RefusingProvider()):
        backfill_space(conn, "s")
    refusals = lambda: conn.execute("SELECT COUNT(*) FROM embed_refusals").fetchone()[0]  # noqa: E731
    assert refusals() == 1
    conn.execute("DELETE FROM chunks WHERE id = ?", (deleted,))
    conn.commit()
    assert refusals() == 0

    rewritten = conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('h2', 'zero', 'pdf', '/test/paper.pdf', 1)"
    ).lastrowid
    conn.commit()
    with patch("knowledge_base.embed_swap.get_provider", return_value=_RefusingProvider()):
        backfill_space(conn, "s")
    assert refusals() == 1
    conn.execute("UPDATE chunks SET content = 'new text' WHERE id = ?", (rewritten,))
    conn.commit()
    assert refusals() == 0

    with patch("knowledge_base.embed_swap.get_provider", return_value=_RefusingProvider()):
        conn.execute("UPDATE chunks SET content = 'zero' WHERE id = ?", (rewritten,))
        conn.commit()
        backfill_space(conn, "s")
    assert refusals() == 1
    deprecate_space(conn, "s")
    cleanup_space(conn, "s")
    assert refusals() == 0
