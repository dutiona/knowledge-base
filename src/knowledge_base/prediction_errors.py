"""Prediction-error detection for search observability.

Logs queries where the best result falls below a confidence threshold,
surfacing retrieval failures as actionable maintenance signals.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3

from .search import SearchResult

logger = logging.getLogger(__name__)

_DEFAULT_THRESHOLD = 0.025


def _normalize_query(query: str) -> str:
    return query.strip().lower()


def _query_hash(query: str) -> str:
    return hashlib.sha256(_normalize_query(query).encode()).hexdigest()


def get_threshold(conn: sqlite3.Connection) -> float:
    """Read prediction_error_threshold from config, with fallback."""
    row = conn.execute(
        "SELECT value FROM config WHERE key = 'prediction_error_threshold'"
    ).fetchone()
    if row is None:
        return _DEFAULT_THRESHOLD
    try:
        return float(row["value"])
    except (ValueError, TypeError):
        return _DEFAULT_THRESHOLD


def detect_and_log(
    conn: sqlite3.Connection,
    query: str,
    results: list[SearchResult],
    source_type_filter: str | None = None,
    mode: str = "hybrid",
) -> None:
    """Detect and log prediction errors. Never raises."""
    try:
        _detect_and_log_inner(conn, query, results, source_type_filter, mode)
    except Exception:
        logger.debug("prediction error detection failed", exc_info=True)


def _detect_and_log_inner(
    conn: sqlite3.Connection,
    query: str,
    results: list[SearchResult],
    source_type_filter: str | None,
    mode: str,
) -> None:
    if not results:
        error_type = "no_results"
        top_score = None
        top_chunk_id = None
    else:
        # Low-confidence detection only meaningful for hybrid mode.
        # Single-mode searches (fts/vec) inherently score ~0.016 (one leg),
        # which would cause false positives against the 0.025 threshold.
        if mode != "hybrid":
            return
        threshold = get_threshold(conn)
        top = results[0]
        if top.score >= threshold:
            return  # good result, nothing to log
        error_type = "low_confidence"
        top_score = top.score
        top_chunk_id = top.chunk_id

    qhash = _query_hash(query)

    # Rate limit: at most 1 per (query_hash, error_type, source_type_filter) per hour
    existing = conn.execute(
        """
        SELECT 1 FROM prediction_errors
        WHERE query_hash = ?
          AND error_type = ?
          AND source_type_filter IS ?
          AND detected_at > datetime('now', '-1 hour')
        LIMIT 1
        """,
        (qhash, error_type, source_type_filter),
    ).fetchone()
    if existing:
        return

    conn.execute(
        """
        INSERT INTO prediction_errors
            (query, query_hash, top_score, top_chunk_id, error_type, source_type_filter)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            _normalize_query(query),
            qhash,
            top_score,
            top_chunk_id,
            error_type,
            source_type_filter,
        ),
    )
    conn.commit()


def list_prediction_errors(
    conn: sqlite3.Connection,
    since: str | None = None,
    unresolved_only: bool = True,
) -> list[dict]:
    """List prediction errors with optional filters."""
    clauses = []
    params: list = []

    if unresolved_only:
        clauses.append("resolved_at IS NULL")
    if since:
        clauses.append("detected_at >= datetime(?)")
        params.append(since)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM prediction_errors {where} ORDER BY detected_at DESC",
        params,
    ).fetchall()

    return [dict(row) for row in rows]


def resolve_prediction_error(conn: sqlite3.Connection, error_id: int) -> dict:
    """Mark a prediction error as resolved."""
    cursor = conn.execute(
        "UPDATE prediction_errors SET resolved_at = datetime('now') WHERE id = ?",
        (error_id,),
    )
    conn.commit()
    if cursor.rowcount == 0:
        return {"error": f"prediction error {error_id} not found"}
    return {"resolved": error_id}


def get_prediction_error_count(
    conn: sqlite3.Connection, unresolved_only: bool = True
) -> int:
    """Count prediction errors."""
    if unresolved_only:
        row = conn.execute(
            "SELECT COUNT(*) FROM prediction_errors WHERE resolved_at IS NULL"
        ).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) FROM prediction_errors").fetchone()
    return row[0]
