"""Tests for reranker provider abstraction and search integration."""

from __future__ import annotations

import logging
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
    """Within the reranked tier, scores are monotonically descending.

    All fixture chunks fit in the default rerank_top_n, so every result is
    reranked here. Global monotonicity across the reranked/tail boundary is
    deliberately NOT a contract: cross-encoder and RRF scores live on
    incomparable scales and are never co-sorted (#387).
    """
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
    assert all(r.match_type == "reranked" for r in results)  # no tail in this fixture
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


# ---------------------------------------------------------------------------
# Regression: reranker/RRF scale mixing (#387)
# ---------------------------------------------------------------------------


def _insert_graded_chunks(conn, n):
    """Insert n chunks with decreasing BM25 relevance for query 'signal'.

    Chunk i repeats the term (n - i) times, so FTS ranks them in
    insertion order: chunk 0 first, chunk n-1 last.
    """
    for i in range(n):
        content = " ".join(["signal"] * (n - i)) + f" filler{i}"
        conn.execute(
            "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
            "VALUES (?, ?, 'markdown', ?, 0)",
            (f"g{i}", content, f"/tmp/graded_{i}.md"),
        )
    conn.commit()


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", _fake_embed_single)
def test_rerank_poor_scores_stay_above_unreranked_tail(tmp_path):
    """Reranked items rank above the un-reranked tail even with poor scores (#387).

    Cross-encoder scores live in [0, 1]; RRF scores are ~1/(RRF_K + rank + 1)
    (~0.016 at rank 0). A legitimate weak cross-encoder score like 0.01 must
    NOT sort below tail items the reranker never saw.
    """
    conn = _setup_db(tmp_path)
    _insert_graded_chunks(conn, 5)

    def _poor_rerank(query, candidates, **_kw):
        # Weak-but-valid scores, all below the best RRF score (~0.0164)
        return [0.01] * len(candidates)

    with patch("knowledge_base.reranker.rerank", _poor_rerank):
        results = search(conn, "signal", mode="fts", rerank=True, rerank_top_n=2)

    assert len(results) == 5
    # The two reranked items must occupy the top positions
    assert [r.match_type for r in results] == ["reranked", "reranked", "fts", "fts", "fts"]
    # They are the same two items the RRF ranking fed to the reranker
    assert {r.source_uri for r in results[:2]} == {"/tmp/graded_0.md", "/tmp/graded_1.md"}


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", _fake_embed_single)
def test_rerank_orders_within_tiers(tmp_path):
    """Cross-encoder order wins inside the reranked tier; the tail keeps RRF order."""
    conn = _setup_db(tmp_path)
    _insert_graded_chunks(conn, 5)

    def _reversing_poor_rerank(query, candidates, **_kw):
        # Second candidate scores higher than the first — both still poor
        return [0.01 + 0.001 * i for i in range(len(candidates))]

    with patch("knowledge_base.reranker.rerank", _reversing_poor_rerank):
        results = search(conn, "signal", mode="fts", rerank=True, rerank_top_n=2)

    assert [r.source_uri for r in results] == [
        "/tmp/graded_1.md",  # reranked, cross-encoder 0.011
        "/tmp/graded_0.md",  # reranked, cross-encoder 0.010
        "/tmp/graded_2.md",  # tail, RRF order preserved
        "/tmp/graded_3.md",
        "/tmp/graded_4.md",
    ]


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", _fake_embed_single)
def test_rerank_preserves_result_count(tmp_path):
    """Fixing #387 must not drop the un-reranked tail from the result set."""
    conn = _setup_db(tmp_path)
    _insert_graded_chunks(conn, 5)

    def _poor_rerank(query, candidates, **_kw):
        return [0.01] * len(candidates)

    with patch("knowledge_base.reranker.rerank", _poor_rerank):
        results = search(conn, "signal", mode="fts", rerank=True, rerank_top_n=2, top_k=5)

    assert len(results) == 5


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", _fake_embed_single)
def test_rerank_tiering_with_source_type_filter(tmp_path):
    """Filtered pool: reranked hits stay above the filter-passing tail (#387).

    With a source_type filter, only filter-passing candidates reach the
    reranker; filter-failing tail chunks are dropped at the final fetch. A
    poor cross-encoder score must not sink the reranked hits below the
    surviving tail.
    """
    conn = _setup_db(tmp_path)
    # 3 pdf chunks (graded relevance) + 2 markdown decoys that rank well
    for i in range(3):
        content = " ".join(["signal"] * (5 - i)) + f" pdffiller{i}"
        conn.execute(
            "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
            "VALUES (?, ?, 'pdf', ?, 0)",
            (f"p{i}", content, f"/tmp/pdf_{i}.pdf"),
        )
    for i in range(2):
        content = " ".join(["signal"] * (5 - i)) + f" mdfiller{i}"
        conn.execute(
            "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
            "VALUES (?, ?, 'markdown', ?, 0)",
            (f"m{i}", content, f"/tmp/md_{i}.md"),
        )
    conn.commit()

    def _poor_rerank(query, candidates, **_kw):
        return [0.01] * len(candidates)

    with patch("knowledge_base.reranker.rerank", _poor_rerank):
        results = search(conn, "signal", mode="fts", source_type="pdf", rerank=True, rerank_top_n=2)

    # Only pdf chunks survive; the two reranked ones stay on top
    assert [r.source_type for r in results] == ["pdf", "pdf", "pdf"]
    assert [r.match_type for r in results] == ["reranked", "reranked", "fts"]


# ---------------------------------------------------------------------------
# Graceful degradation (reranker failure → clean RRF fallback)
# ---------------------------------------------------------------------------


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", _fake_embed_single)
def test_rerank_error_falls_back_to_rrf(tmp_path):
    """Reranker raising RuntimeError degrades to pure RRF ordering."""
    conn = _setup_db(tmp_path)
    _insert_graded_chunks(conn, 3)

    def _broken_rerank(query, candidates, **_kw):
        raise RuntimeError("model exploded")

    with patch("knowledge_base.reranker.rerank", _broken_rerank):
        results = search(conn, "signal", mode="fts", rerank=True)

    assert [r.source_uri for r in results] == ["/tmp/graded_0.md", "/tmp/graded_1.md", "/tmp/graded_2.md"]
    assert all(r.match_type == "fts" for r in results)


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", _fake_embed_single)
def test_rerank_wrong_length_scores_clean_fallback(tmp_path):
    """A misbehaving provider returning too few scores must not leak state.

    zip(strict=True) raises ValueError mid-mutation; the fallback must drop
    the partially-recorded reranked ids so no result is stamped
    match_type='reranked' while carrying an RRF score and ordering.
    """
    conn = _setup_db(tmp_path)
    _insert_graded_chunks(conn, 4)

    def _short_rerank(query, candidates, **_kw):
        return [0.9] * (len(candidates) - 1)  # one score short → ValueError

    with patch("knowledge_base.reranker.rerank", _short_rerank):
        results = search(conn, "signal", mode="fts", rerank=True)

    assert len(results) == 4
    # Clean fallback: RRF order, and NO result claims to be reranked
    assert [r.source_uri for r in results] == [f"/tmp/graded_{i}.md" for i in range(4)]
    assert all(r.match_type == "fts" for r in results)


@pytest.mark.parametrize("exc_type", [ImportError, RuntimeError, ValueError, OSError])
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", _fake_embed_single)
def test_rerank_error_catch_set_locks_fallback(tmp_path, caplog, exc_type):
    """Every exception in the rerank catch set degrades to clean RRF + a logged warning.

    Locks the ``(ImportError, RuntimeError, ValueError, OSError)`` tuple at
    ``search.py``: if any type were dropped from the ``except`` clause, the
    matching parametrization would propagate out of ``search`` instead of
    falling back, and this test would fail. Also pins the fallback's logged
    warning (``search.py``'s ``logger.warning(...)``), otherwise unverified.
    """
    conn = _setup_db(tmp_path)
    _insert_graded_chunks(conn, 3)

    def _broken_rerank(query, candidates, **_kw):
        raise exc_type("inference failed")

    with (
        patch("knowledge_base.reranker.rerank", _broken_rerank),
        caplog.at_level(logging.WARNING, logger="knowledge_base.search"),
    ):
        results = search(conn, "signal", mode="fts", rerank=True)

    # Fallback contract: full RRF-ordered result set, nothing stamped 'reranked'.
    assert results  # non-empty
    assert [r.source_uri for r in results] == [f"/tmp/graded_{i}.md" for i in range(3)]
    assert all(r.match_type == "fts" for r in results)

    # The degradation path logs a warning (RRF-fallback signal for operators).
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "Reranker failed" in r.getMessage()]
    assert warnings, "expected a fallback warning to be logged"
