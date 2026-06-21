"""Shared pytest fixtures for the knowledge-base test suite.

Fixtures here are opt-in by argument name — they do not change the behavior of
existing tests that build their own temp databases inline.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from knowledge_base.db import get_connection, init_schema


@pytest.fixture
def kb_conn(tmp_path) -> Iterator[sqlite3.Connection]:
    """A fresh, schema-initialized SQLite connection backed by a temp-dir DB.

    Every test gets an isolated database file under its own ``tmp_path`` — the
    real DB at ``~/.local/share/knowledge-base`` is never touched. Use together
    with ``patch("knowledge_base.routes.<mod>._get_conn", return_value=kb_conn)``
    to exercise an MCP tool wrapper against a real database.
    """
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    try:
        yield conn
    finally:
        # Close so WAL handles don't accumulate across the session — leaked
        # connections raise resource/timing pressure that can surface latent
        # concurrency races elsewhere in the suite.
        conn.close()
