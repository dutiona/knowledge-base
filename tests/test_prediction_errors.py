"""Tests for prediction-error detection (search observability)."""

from __future__ import annotations

import hashlib
import sqlite3

import pytest

from knowledge_base.db import get_connection, init_schema
from knowledge_base.search import SearchResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_db(tmp_path) -> sqlite3.Connection:
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    return conn


def _make_result(
    chunk_id: int = 1,
    score: float = 0.03,
    match_type: str = "hybrid",
    content: str = "test content",
) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        content=content,
        source_type="pdf",
        source_uri="/test.pdf",
        chunk_index=0,
        score=score,
        match_type=match_type,
    )


def _query_hash(query: str) -> str:
    return hashlib.sha256(query.strip().lower().encode()).hexdigest()


# ---------------------------------------------------------------------------
# Detection logic
# ---------------------------------------------------------------------------


def _insert_chunk(conn, chunk_id: int = 1) -> int:
    """Insert a dummy chunk and return its ID."""
    conn.execute(
        """INSERT INTO chunks (id, content_hash, content, source_type, source_uri, chunk_index)
           VALUES (?, ?, ?, 'pdf', '/test.pdf', 0)""",
        (chunk_id, f"hash_{chunk_id}", f"content_{chunk_id}"),
    )
    conn.commit()
    return chunk_id


def test_low_confidence_single_leg(tmp_path):
    """A result from only one retrieval leg (score < threshold) is logged."""
    from knowledge_base.prediction_errors import detect_and_log

    conn = _setup_db(tmp_path)
    cid = _insert_chunk(conn)
    # Single-leg result: score ~0.016, below default threshold 0.025
    results = [_make_result(chunk_id=cid, score=0.0164, match_type="fts")]
    detect_and_log(conn, "test query", results)

    row = conn.execute("SELECT * FROM prediction_errors").fetchone()
    assert row is not None
    assert row["error_type"] == "low_confidence"
    assert row["top_score"] == pytest.approx(0.0164)
    assert row["query_hash"] == _query_hash("test query")


def test_high_confidence_hybrid_not_logged(tmp_path):
    """A hybrid result with score above threshold is NOT logged."""
    from knowledge_base.prediction_errors import detect_and_log

    conn = _setup_db(tmp_path)
    # Hybrid result: score ~0.033, above default threshold 0.025
    results = [_make_result(score=0.0328, match_type="hybrid")]
    detect_and_log(conn, "good query", results)

    count = conn.execute("SELECT COUNT(*) FROM prediction_errors").fetchone()[0]
    assert count == 0


def test_single_mode_not_logged(tmp_path):
    """FTS-only or vec-only searches are never logged as low_confidence."""
    from knowledge_base.prediction_errors import detect_and_log

    conn = _setup_db(tmp_path)
    cid = _insert_chunk(conn, chunk_id=10)
    # Score 0.016 in fts-only mode — should NOT be logged
    results = [_make_result(chunk_id=cid, score=0.0164, match_type="fts")]
    detect_and_log(conn, "fts query", results, mode="fts")
    detect_and_log(conn, "vec query", results, mode="vec")

    count = conn.execute("SELECT COUNT(*) FROM prediction_errors").fetchone()[0]
    assert count == 0


def test_no_results_logged(tmp_path):
    """Empty results are logged as no_results."""
    from knowledge_base.prediction_errors import detect_and_log

    conn = _setup_db(tmp_path)
    detect_and_log(conn, "obscure query", [])

    row = conn.execute("SELECT * FROM prediction_errors").fetchone()
    assert row is not None
    assert row["error_type"] == "no_results"
    assert row["top_score"] is None
    assert row["top_chunk_id"] is None


def test_filtered_empty_records_filter(tmp_path):
    """When source_type_filter is set and results are empty, the filter is recorded."""
    from knowledge_base.prediction_errors import detect_and_log

    conn = _setup_db(tmp_path)
    detect_and_log(conn, "filtered query", [], source_type_filter="pdf")

    row = conn.execute("SELECT * FROM prediction_errors").fetchone()
    assert row is not None
    assert row["error_type"] == "no_results"
    assert row["source_type_filter"] == "pdf"


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_rate_limiting(tmp_path):
    """Same (query, error_type, filter) within 1 hour produces only 1 row."""
    from knowledge_base.prediction_errors import detect_and_log

    conn = _setup_db(tmp_path)
    detect_and_log(conn, "repeated query", [])
    detect_and_log(conn, "repeated query", [])

    count = conn.execute("SELECT COUNT(*) FROM prediction_errors").fetchone()[0]
    assert count == 1


def test_rate_limiting_different_queries(tmp_path):
    """Different queries are both logged."""
    from knowledge_base.prediction_errors import detect_and_log

    conn = _setup_db(tmp_path)
    detect_and_log(conn, "query one", [])
    detect_and_log(conn, "query two", [])

    count = conn.execute("SELECT COUNT(*) FROM prediction_errors").fetchone()[0]
    assert count == 2


def test_rate_limiting_different_filters(tmp_path):
    """Same query with different source_type_filter are both logged."""
    from knowledge_base.prediction_errors import detect_and_log

    conn = _setup_db(tmp_path)
    detect_and_log(conn, "query", [], source_type_filter="pdf")
    detect_and_log(conn, "query", [], source_type_filter="code")

    count = conn.execute("SELECT COUNT(*) FROM prediction_errors").fetchone()[0]
    assert count == 2


# ---------------------------------------------------------------------------
# Resolution & listing
# ---------------------------------------------------------------------------


def test_resolve_nonexistent(tmp_path):
    """Resolving a non-existent error raises NotFoundError."""
    from knowledge_base.prediction_errors import resolve_prediction_error
    from knowledge_base.exceptions import NotFoundError
    import pytest

    conn = _setup_db(tmp_path)
    with pytest.raises(NotFoundError, match="9999"):
        resolve_prediction_error(conn, 9999)


def test_resolve(tmp_path):
    """Resolving an error sets resolved_at."""
    from knowledge_base.prediction_errors import (
        detect_and_log,
        resolve_prediction_error,
    )

    conn = _setup_db(tmp_path)
    detect_and_log(conn, "resolve me", [])

    row = conn.execute("SELECT id, resolved_at FROM prediction_errors").fetchone()
    assert row["resolved_at"] is None

    resolve_prediction_error(conn, row["id"])

    row = conn.execute("SELECT resolved_at FROM prediction_errors WHERE id = ?", (row["id"],)).fetchone()
    assert row["resolved_at"] is not None


def test_list_unresolved_only(tmp_path):
    """list_prediction_errors filters by resolved status."""
    from knowledge_base.prediction_errors import (
        detect_and_log,
        list_prediction_errors,
        resolve_prediction_error,
    )

    conn = _setup_db(tmp_path)
    detect_and_log(conn, "error one", [])
    detect_and_log(conn, "error two", [])

    row = conn.execute("SELECT id FROM prediction_errors LIMIT 1").fetchone()
    resolve_prediction_error(conn, row["id"])

    unresolved = list_prediction_errors(conn, unresolved_only=True)
    assert len(unresolved) == 1

    all_errors = list_prediction_errors(conn, unresolved_only=False)
    assert len(all_errors) == 2


def test_list_since_filter(tmp_path):
    """list_prediction_errors filters by since timestamp."""
    from knowledge_base.prediction_errors import detect_and_log, list_prediction_errors

    conn = _setup_db(tmp_path)
    detect_and_log(conn, "old error", [])

    # All errors should be recent
    errors = list_prediction_errors(conn, since="2000-01-01")
    assert len(errors) == 1

    # Future date should return nothing
    errors = list_prediction_errors(conn, since="2099-01-01")
    assert len(errors) == 0


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def test_statistics_includes_count(tmp_path):
    """get_prediction_error_count returns unresolved count."""
    from knowledge_base.prediction_errors import (
        detect_and_log,
        get_prediction_error_count,
        resolve_prediction_error,
    )

    conn = _setup_db(tmp_path)
    detect_and_log(conn, "err1", [])
    detect_and_log(conn, "err2", [])

    assert get_prediction_error_count(conn) == 2

    row = conn.execute("SELECT id FROM prediction_errors LIMIT 1").fetchone()
    resolve_prediction_error(conn, row["id"])
    assert get_prediction_error_count(conn, unresolved_only=True) == 1
    assert get_prediction_error_count(conn, unresolved_only=False) == 2


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


def test_detect_never_raises(tmp_path):
    """detect_and_log swallows all exceptions."""
    from knowledge_base.prediction_errors import detect_and_log

    conn = _setup_db(tmp_path)
    # Drop the table to force a DB error
    conn.execute("DROP TABLE prediction_errors")

    # Should not raise
    detect_and_log(conn, "will fail", [])


def test_threshold_from_config(tmp_path):
    """Threshold is read from config table, with fallback."""
    from knowledge_base.prediction_errors import get_threshold

    conn = _setup_db(tmp_path)

    # Default should be 0.025
    assert get_threshold(conn) == pytest.approx(0.025)

    # Override
    conn.execute("UPDATE config SET value = '0.05' WHERE key = 'prediction_error_threshold'")
    conn.commit()
    assert get_threshold(conn) == pytest.approx(0.05)


def test_threshold_fallback_when_missing(tmp_path):
    """If config key is missing, get_threshold returns default."""
    from knowledge_base.prediction_errors import get_threshold

    conn = _setup_db(tmp_path)
    conn.execute("DELETE FROM config WHERE key = 'prediction_error_threshold'")
    conn.commit()

    assert get_threshold(conn) == pytest.approx(0.025)


def test_query_normalization(tmp_path):
    """Queries differing only in case/whitespace produce the same hash."""
    from knowledge_base.prediction_errors import detect_and_log

    conn = _setup_db(tmp_path)
    detect_and_log(conn, "  Query  ", [])
    detect_and_log(conn, "query", [])

    count = conn.execute("SELECT COUNT(*) FROM prediction_errors").fetchone()[0]
    assert count == 1  # rate-limited because same normalized hash
