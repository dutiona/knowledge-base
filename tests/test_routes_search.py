"""Wrapper-level tests for the search route module.

Covers the ``co_occurrence`` MCP tool wrapper, whose server-only logic
(``co_occurrence_pairs`` + ``json.dumps``) is not exercised elsewhere.
``search_index`` and ``status`` already have wrapper coverage in
``test_search.py`` / ``test_db_path_config.py`` and are intentionally not
duplicated here.

Fixture data is inserted directly via SQL after inspecting the real schema
(``chunks`` + ``chunk_sessions`` join table in ``db.py``) so the tests stay
offline and deterministic — no Ollama embedder is invoked.
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

from knowledge_base.routes.search import co_occurrence


def _add_chunk(
    conn: sqlite3.Connection,
    *,
    content_hash: str,
    content: str,
    source_uri: str,
    session_id: str,
    source_type: str = "note",
    chunk_index: int = 0,
) -> int:
    """Insert a chunk and record its ingestion session, mirroring the ingest path.

    Co-ingestion is recorded by ``chunk_sessions`` (chunk_id, session_id); the
    co-occurrence query joins it back to ``chunks`` on ``chunk_id`` to derive
    each document's (source_uri, session_id) set. Returns the new chunk id.
    """
    cur = conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, session_id)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (content_hash, content, source_type, source_uri, chunk_index, session_id),
    )
    chunk_id = cur.lastrowid
    assert chunk_id is not None  # lastrowid is set after a successful INSERT
    conn.execute(
        "INSERT INTO chunk_sessions (chunk_id, session_id) VALUES (?, ?)",
        (chunk_id, session_id),
    )
    conn.commit()
    return chunk_id


def test_co_occurrence_returns_pair_for_shared_session(kb_conn):
    """Two docs ingested in the same session produce a co-occurrence pair."""
    _add_chunk(
        kb_conn,
        content_hash="h-a",
        content="alpha document",
        source_uri="doc_a.md",
        session_id="sess-1",
    )
    _add_chunk(
        kb_conn,
        content_hash="h-b",
        content="beta document",
        source_uri="doc_b.md",
        session_id="sess-1",
    )

    with patch("knowledge_base.routes.search._get_conn", return_value=kb_conn):
        result = json.loads(co_occurrence(min_sessions=1))

    assert result == [{"source_uri_a": "doc_a.md", "source_uri_b": "doc_b.md", "co_sessions": 1}]


def test_co_occurrence_counts_multiple_shared_sessions(kb_conn):
    """co_sessions reflects the number of distinct shared ingestion sessions."""
    # Same pair co-ingested across two distinct sessions.
    _add_chunk(
        kb_conn,
        content_hash="h-a1",
        content="alpha s1",
        source_uri="doc_a.md",
        session_id="sess-1",
    )
    _add_chunk(
        kb_conn,
        content_hash="h-b1",
        content="beta s1",
        source_uri="doc_b.md",
        session_id="sess-1",
    )
    _add_chunk(
        kb_conn,
        content_hash="h-a2",
        content="alpha s2",
        source_uri="doc_a.md",
        session_id="sess-2",
    )
    _add_chunk(
        kb_conn,
        content_hash="h-b2",
        content="beta s2",
        source_uri="doc_b.md",
        session_id="sess-2",
    )

    with patch("knowledge_base.routes.search._get_conn", return_value=kb_conn):
        result = json.loads(co_occurrence(min_sessions=1))

    assert result == [{"source_uri_a": "doc_a.md", "source_uri_b": "doc_b.md", "co_sessions": 2}]


def test_co_occurrence_min_sessions_threshold_filters(kb_conn):
    """min_sessions excludes a pair sharing fewer than the threshold sessions."""
    # doc_a / doc_b share exactly 1 session.
    _add_chunk(
        kb_conn,
        content_hash="h-a",
        content="alpha",
        source_uri="doc_a.md",
        session_id="sess-1",
    )
    _add_chunk(
        kb_conn,
        content_hash="h-b",
        content="beta",
        source_uri="doc_b.md",
        session_id="sess-1",
    )

    with patch("knowledge_base.routes.search._get_conn", return_value=kb_conn):
        # min_sessions=1 includes the single-session pair.
        included = json.loads(co_occurrence(min_sessions=1))
        # min_sessions=2 excludes it (shares only 1 session).
        excluded = json.loads(co_occurrence(min_sessions=2))

    assert included == [{"source_uri_a": "doc_a.md", "source_uri_b": "doc_b.md", "co_sessions": 1}]
    assert excluded == []


def test_co_occurrence_no_shared_sessions_returns_empty(kb_conn):
    """Docs in disjoint sessions produce no pairs (valid empty-list JSON)."""
    _add_chunk(
        kb_conn,
        content_hash="h-a",
        content="alpha",
        source_uri="doc_a.md",
        session_id="sess-1",
    )
    _add_chunk(
        kb_conn,
        content_hash="h-b",
        content="beta",
        source_uri="doc_b.md",
        session_id="sess-2",
    )

    with patch("knowledge_base.routes.search._get_conn", return_value=kb_conn):
        result = json.loads(co_occurrence(min_sessions=1))

    assert result == []


def test_co_occurrence_empty_db_returns_empty_json(kb_conn):
    """An empty index returns a valid empty JSON array."""
    with patch("knowledge_base.routes.search._get_conn", return_value=kb_conn):
        response_str = co_occurrence()

    # Default min_sessions=1; no co-ingested docs exist.
    assert json.loads(response_str) == []
