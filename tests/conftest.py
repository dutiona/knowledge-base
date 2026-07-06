"""Shared pytest fixtures for the knowledge-base test suite.

Database fixtures are opt-in by argument name — they do not change the
behavior of existing tests that build their own temp databases inline.
Two autouse guards apply to every test: the job-worker reset and the
data-dir redirect (#391).
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
        # Closed per test so WAL handles don't accumulate across the session —
        # leaked connections raise resource/timing pressure that can surface
        # latent concurrency races elsewhere in the suite.
        conn.close()


@pytest.fixture(autouse=True)
def _reset_job_worker():
    """Guarantee the process-global job-worker singleton is stopped at every test
    boundary.

    The worker (``knowledge_base.jobs.get_worker()``) is a process-global
    singleton; any test that triggers ``submit_job`` → ``_ensure_worker_running``
    starts a daemon thread bound to *that test's* temp DB. Without a reset, a
    later test could inherit a live daemon pointed at a deleted database (a
    flakiness and DB-lock source). Resetting before and after each test
    centralizes the invariant so individual modules don't have to remember it.

    Idempotent: ``reset()`` on an already-stopped worker is a cheap no-op. Tests
    that exercise worker *behavior* instantiate their own ``_JobWorker()`` rather
    than the singleton, so they are unaffected.
    """
    from knowledge_base.jobs import get_worker

    get_worker().reset()
    yield
    get_worker().reset()


@pytest.fixture(autouse=True)
def _isolated_kb_data_dir(tmp_path, monkeypatch):
    """Point KNOWLEDGE_BASE_DATA_DIR at a per-test temp dir (#391).

    ``kb_data_dir()`` (ingest.py) resolves the base directory for on-disk
    artifacts — figure PNGs, rendered vector pages — at call time. Without
    this guard, any test exercising a code path that writes artifacts (e.g.
    ``vision._save_rendered_pngs``) would pollute the developer's real
    ``~/.local/share/knowledge-base``. Tests asserting on artifact locations
    may still override the variable themselves; monkeypatch scoping keeps
    each test hermetic either way.
    """
    monkeypatch.setenv("KNOWLEDGE_BASE_DATA_DIR", str(tmp_path / "kb-data"))
