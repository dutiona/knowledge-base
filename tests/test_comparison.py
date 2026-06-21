"""Tests for A/B embedding space comparison (#99 Phase 3)."""

from __future__ import annotations

import hashlib
from unittest.mock import patch

import pytest

from knowledge_base.comparison import (
    _spearman_rho,
    batch_compare_spaces,
    compare_spaces,
)
from knowledge_base.db import get_connection, init_schema
from knowledge_base.embed_swap import backfill_space, create_space

DIM = 4


def _fake_embed(texts, model="x", expected_dim=None, **_kw):
    dim = expected_dim or DIM
    return [[0.1] * dim for _ in texts]


def _fake_embed_single(text, model="x", expected_dim=None, **_kw):
    dim = expected_dim or DIM
    return [0.1] * dim


def _setup(tmp_path, dim=DIM):
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('embed_dim', ?)",
        (str(dim),),
    )
    conn.commit()
    return conn


def _add_chunk(conn, content, index=0):
    h = hashlib.sha256(content.encode()).hexdigest()[:16]
    cursor = conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, chunk_strategy) "
        "VALUES (?, ?, 'pdf', '/test/paper.pdf', ?, 'mechanical')",
        (h, content, index),
    )
    conn.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# _spearman_rho unit tests
# ---------------------------------------------------------------------------


def test_spearman_rho_perfect():
    assert _spearman_rho([1, 2, 3], [1, 2, 3]) == 1.0


def test_spearman_rho_inverse():
    assert _spearman_rho([1, 2, 3], [3, 2, 1]) == -1.0


# ---------------------------------------------------------------------------
# compare_spaces tests
# ---------------------------------------------------------------------------


def _mock_provider(fake_fn):
    """Wrap a fake embed function in a mock provider."""
    from unittest.mock import MagicMock

    mock = MagicMock()
    mock.embed.side_effect = lambda texts, model=None, expected_dim=None: fake_fn(
        texts, model=model, expected_dim=expected_dim
    )
    return mock


def _create_and_backfill(conn, name, model="test-model", dim=DIM, strategy="mechanical"):
    """Create a space and backfill it with fake embeddings."""
    create_space(conn, name, model, dim, "ollama", chunk_strategy=strategy)
    with patch(
        "knowledge_base.embed_swap.get_provider",
        return_value=_mock_provider(_fake_embed),
    ):
        backfill_space(conn, name)


@patch("knowledge_base.search.embed_single", side_effect=_fake_embed_single)
def test_compare_spaces_identical(mock_es, tmp_path):
    conn = _setup(tmp_path)
    # Add chunks
    for i in range(5):
        _add_chunk(conn, f"chunk content number {i}", index=i)

    _create_and_backfill(conn, "space_a")
    _create_and_backfill(conn, "space_b")

    result = compare_spaces(conn, "test query", "space_a", "space_b", top_k=5, mode="vec")

    assert result["metrics"]["overlap_at_k"] == 1.0
    assert result["metrics"]["jaccard"] == 1.0
    assert result["warnings"] == []


@patch("knowledge_base.search.embed_single", side_effect=_fake_embed_single)
def test_compare_spaces_empty_results(mock_es, tmp_path):
    conn = _setup(tmp_path)
    # Create spaces with no chunks to embed
    create_space(conn, "empty_a", "test-model", DIM, "ollama")
    create_space(conn, "empty_b", "test-model", DIM, "ollama")

    result = compare_spaces(conn, "test query", "empty_a", "empty_b", top_k=10, mode="vec")

    # Both empty → overlap_at_k=1.0, jaccard=0.0
    assert result["metrics"]["overlap_at_k"] == 1.0
    assert result["metrics"]["jaccard"] == 0.0


@patch("knowledge_base.search.embed_single", side_effect=_fake_embed_single)
def test_compare_spaces_one_empty(mock_es, tmp_path):
    conn = _setup(tmp_path)
    for i in range(3):
        _add_chunk(conn, f"chunk {i}", index=i)

    _create_and_backfill(conn, "full_space")
    create_space(conn, "empty_space", "test-model", DIM, "ollama")

    result = compare_spaces(conn, "test query", "full_space", "empty_space", top_k=10, mode="vec")

    assert result["metrics"]["overlap_at_k"] == 0.0


@patch("knowledge_base.search.embed_single", side_effect=_fake_embed_single)
def test_compare_spaces_cross_strategy_warning(mock_es, tmp_path):
    conn = _setup(tmp_path)
    for i in range(3):
        _add_chunk(conn, f"chunk {i}", index=i)

    _create_and_backfill(conn, "mech_space", strategy="mechanical")
    _create_and_backfill(conn, "sem_space", strategy="semantic")

    result = compare_spaces(conn, "test query", "mech_space", "sem_space", top_k=10, mode="vec")

    assert len(result["warnings"]) > 0
    assert "Cross-strategy" in result["warnings"][0]


def test_compare_spaces_invalid_name(tmp_path):
    conn = _setup(tmp_path)

    with pytest.raises(ValueError, match="not found"):
        compare_spaces(conn, "test query", "nonexistent_a", "nonexistent_b")


@patch("knowledge_base.search.embed_single", side_effect=_fake_embed_single)
def test_batch_compare_spaces(mock_es, tmp_path):
    conn = _setup(tmp_path)
    for i in range(5):
        _add_chunk(conn, f"batch chunk {i}", index=i)

    _create_and_backfill(conn, "batch_a")
    _create_and_backfill(conn, "batch_b")

    queries = ["query one", "query two", "query three"]
    result = batch_compare_spaces(conn, "batch_a", "batch_b", queries, top_k=5, mode="vec")

    assert result["queries_analyzed"] == 3
    assert result["space_a"] == "batch_a"
    assert result["space_b"] == "batch_b"
    assert result["overlap_at_k"]["mean"] is not None
    assert result["jaccard"]["mean"] is not None
    assert isinstance(result["warnings"], list)
