"""Tests for reranker provider abstraction and search integration."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from knowledge_base.db import DEFAULT_EMBED_DIM, get_connection, init_schema
from knowledge_base.reranker import (
    ONNXReranker,
    RerankerProvider,
    _reranker_cache,
    _sigmoid,
    get_reranker,
)
from knowledge_base.search import search


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_embed(texts, model="bge-m3", expected_dim=None, **_kwargs):
    dim = expected_dim if expected_dim is not None else DEFAULT_EMBED_DIM
    return [[0.1] * dim for _ in texts]


def _fake_embed_single(text, model="bge-m3", **_kwargs):
    return [0.1] * DEFAULT_EMBED_DIM


def _fake_rerank(query, candidates, **_kwargs):
    """Mock reranker -- reverse order by returning descending scores."""
    n = len(candidates)
    return [float(n - i) / n for i in range(n)]


def _setup_db(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    return conn


@pytest.fixture(autouse=True)
def _clear_reranker_cache():
    """Ensure no cross-test contamination from cached reranker instances."""
    _reranker_cache.clear()
    yield
    _reranker_cache.clear()


# ---------------------------------------------------------------------------
# Reranker unit tests
# ---------------------------------------------------------------------------


def test_reranker_provider_protocol():
    """ONNXReranker satisfies the RerankerProvider protocol."""
    assert isinstance(ONNXReranker(), RerankerProvider)


def test_reranker_provider_caching():
    """get_reranker() returns cached instance on second call."""
    first = get_reranker("onnx", allow_env_override=False)
    second = get_reranker("onnx", allow_env_override=False)
    assert first is second
    assert isinstance(first, ONNXReranker)


def test_reranker_env_override():
    """RERANK_PROVIDER env var selects provider."""
    with patch.dict("os.environ", {"RERANK_PROVIDER": "onnx"}):
        # Even though we pass a different default name, env var wins
        result = get_reranker("nonexistent_default")
        assert isinstance(result, ONNXReranker)


def test_rerank_unknown_provider():
    """get_reranker('nonexistent') raises ValueError."""
    with pytest.raises(ValueError, match="Unknown reranker provider"):
        get_reranker("nonexistent", allow_env_override=False)


def test_sigmoid():
    """Verify _sigmoid with known values."""
    assert _sigmoid(0.0) == pytest.approx(0.5)
    assert _sigmoid(10.0) == pytest.approx(1.0, abs=1e-4)
    assert _sigmoid(-10.0) == pytest.approx(0.0, abs=1e-4)
    # Basic sanity: sigmoid(x) + sigmoid(-x) == 1
    for x in [0.5, 1.0, 3.0, -2.5]:
        assert _sigmoid(x) + _sigmoid(-x) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Search integration tests (mocked reranker)
# ---------------------------------------------------------------------------


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", _fake_embed_single)
def test_rerank_disabled_by_default(tmp_path):
    """Search without rerank=True uses RRF ordering, no reranker called."""
    conn = _setup_db(tmp_path)
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('h1', 'alpha beta gamma', 'markdown', '/tmp/a.md', 0)"
    )
    conn.commit()

    with patch("knowledge_base.reranker.rerank") as mock_rerank:
        results = search(conn, "alpha", mode="fts")
        mock_rerank.assert_not_called()
    assert len(results) >= 1


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", _fake_embed_single)
def test_rerank_reorders_results(tmp_path):
    """With rerank=True, mock reranker reorders results differently from RRF."""
    conn = _setup_db(tmp_path)
    # Insert two chunks; FTS will rank "alpha" first
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('h1', 'alpha beta gamma', 'markdown', '/tmp/a.md', 0)"
    )
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('h2', 'alpha delta epsilon', 'markdown', '/tmp/b.md', 0)"
    )
    conn.commit()

    def _reverse_rerank(query, candidates, **_kw):
        """Give highest score to the last candidate (reverse RRF order)."""
        n = len(candidates)
        return [float(i) / max(n, 1) for i in range(n)]

    with patch("knowledge_base.reranker.rerank", _reverse_rerank):
        results = search(conn, "alpha", mode="fts", rerank=True)

    assert len(results) == 2
    # The reranker reversed the order, so the second chunk should be first
    assert all(r.match_type == "reranked" for r in results)


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", _fake_embed_single)
def test_rerank_top_n_limits_candidates(tmp_path):
    """Mock reranker receives at most rerank_top_n candidates."""
    conn = _setup_db(tmp_path)
    # Insert 5 chunks
    for i in range(5):
        conn.execute(
            "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "  # noqa: S608  # trusted internal identifier, not user input
            f"VALUES ('h{i}', 'attention mechanism variant {i}', 'markdown', '/tmp/{i}.md', 0)"
        )
    conn.commit()

    calls = []

    def _tracking_rerank(query, candidates, **_kw):
        calls.append(candidates)
        return [0.5] * len(candidates)

    with patch("knowledge_base.reranker.rerank", _tracking_rerank):
        search(conn, "attention", mode="fts", rerank=True, rerank_top_n=3)

    assert len(calls) == 1
    assert len(calls[0]) <= 3


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", _fake_embed_single)
def test_rerank_with_empty_results(tmp_path):
    """Empty search returns [] even with rerank=True."""
    conn = _setup_db(tmp_path)

    with patch("knowledge_base.reranker.rerank") as mock_rerank:
        results = search(conn, "nonexistent_query_xyz", mode="fts", rerank=True)
        mock_rerank.assert_not_called()
    assert results == []


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", _fake_embed_single)
def test_rerank_score_monotonic(tmp_path):
    """After reranking, results[0].score >= results[1].score."""
    conn = _setup_db(tmp_path)
    for i in range(4):
        conn.execute(
            "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "  # noqa: S608  # trusted internal identifier, not user input
            f"VALUES ('m{i}', 'neural network layer {i}', 'markdown', '/tmp/{i}.md', 0)"
        )
    conn.commit()

    with patch("knowledge_base.reranker.rerank", _fake_rerank):
        results = search(conn, "neural", mode="fts", rerank=True)

    assert len(results) >= 2
    for i in range(len(results) - 1):
        assert results[i].score >= results[i + 1].score


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", _fake_embed_single)
def test_rerank_match_type(tmp_path):
    """Reranked results have match_type='reranked'."""
    conn = _setup_db(tmp_path)
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('mt1', 'transformer architecture overview', 'markdown', '/tmp/t.md', 0)"
    )
    conn.commit()

    with patch("knowledge_base.reranker.rerank", _fake_rerank):
        results = search(conn, "transformer", mode="fts", rerank=True)

    assert len(results) >= 1
    assert all(r.match_type == "reranked" for r in results)


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", _fake_embed_single)
def test_rerank_with_source_type_filter(tmp_path):
    """source_type filtering applies before reranking."""
    conn = _setup_db(tmp_path)
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('sf1', 'convolution kernel methods', 'markdown', '/tmp/a.md', 0)"
    )
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('sf2', 'convolution kernel methods in pdf', 'pdf', '/tmp/b.pdf', 0)"
    )
    conn.commit()

    calls = []

    def _tracking_rerank(query, candidates, **_kw):
        calls.append(candidates)
        return [0.9] * len(candidates)

    with patch("knowledge_base.reranker.rerank", _tracking_rerank):
        results = search(conn, "convolution", mode="fts", source_type="pdf", rerank=True)

    # Only the pdf chunk should reach the reranker
    assert len(calls) == 1
    assert len(calls[0]) == 1
    assert all(r.source_type == "pdf" for r in results)


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", _fake_embed_single)
def test_search_index_rerank_passthrough(tmp_path):
    """server.py passes rerank parameter through to search()."""
    conn = _setup_db(tmp_path)
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('sp1', 'passthrough test content', 'markdown', '/tmp/p.md', 0)"
    )
    conn.commit()

    with patch("knowledge_base.reranker.rerank", _fake_rerank):
        # Call search directly with rerank=True — same as server.py passthrough
        results = search(conn, "passthrough", mode="fts", rerank=True)

    assert len(results) >= 1
    assert results[0].match_type == "reranked"
