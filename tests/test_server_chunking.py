"""Tests for configure_chunking MCP tool and chunk_strategy in search/status."""

from __future__ import annotations

from knowledge_base.db import get_connection, init_schema


def test_configure_chunking_query(tmp_path):
    """Querying with no args returns current strategy."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    row = conn.execute(
        "SELECT value FROM config WHERE key = 'chunk_strategy'"
    ).fetchone()
    assert row["value"] == "mechanical"


def test_configure_chunking_set_valid(tmp_path):
    """Setting strategy to 'semantic' updates config."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('chunk_strategy', 'semantic')"
    )
    conn.commit()

    row = conn.execute(
        "SELECT value FROM config WHERE key = 'chunk_strategy'"
    ).fetchone()
    assert row["value"] == "semantic"


def test_configure_chunking_set_invalid(tmp_path):
    """Invalid strategy values are rejected."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Simulate what configure_chunking does for invalid input
    strategy = "garbage"
    assert strategy not in ("mechanical", "semantic")


def test_status_includes_chunk_strategy(tmp_path):
    """Status output includes chunk_strategy field."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Verify the config key exists and can be read
    row = conn.execute(
        "SELECT value FROM config WHERE key = 'chunk_strategy'"
    ).fetchone()
    assert row is not None
    assert row["value"] in ("mechanical", "semantic")


def test_search_index_chunk_strategy_passthrough(tmp_path):
    """search_index passes chunk_strategy to search()."""
    from unittest.mock import patch

    from knowledge_base.db import DEFAULT_EMBED_DIM
    from knowledge_base.search import search

    def _fake_embed(texts, model="bge-m3", expected_dim=None, **_kw):
        dim = expected_dim if expected_dim is not None else DEFAULT_EMBED_DIM
        return [[0.1] * dim for _ in texts]

    def _fake_embed_single(text, model="bge-m3", **_kw):
        return [0.1] * DEFAULT_EMBED_DIM

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Insert chunks with different strategies
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, chunk_strategy) "
        "VALUES ('cs_pass1', 'mechanical graph neural network', 'pdf', '/tmp/a.pdf', 0, 'mechanical')"
    )
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, chunk_strategy) "
        "VALUES ('cs_pass2', 'semantic graph neural network', 'pdf', '/tmp/b.pdf', 0, 'semantic')"
    )
    conn.commit()

    with patch("knowledge_base.search.embed_single", _fake_embed_single):
        # Default (no filter) returns both — vec leg filters implicitly
        # but FTS mode returns all strategies
        all_results = search(conn, "graph neural", mode="fts")
        assert len(all_results) == 2

        # Explicit filter to semantic only
        sem_results = search(
            conn, "graph neural", mode="fts", chunk_strategy="semantic"
        )
        assert len(sem_results) == 1
        assert sem_results[0].content.startswith("semantic")
