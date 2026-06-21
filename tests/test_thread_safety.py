"""Regression test for issue #19: SQLite thread-safety under parallel tool calls."""

import threading
from functools import partial

from knowledge_base._conn import _get_conn
from knowledge_base.db import get_connection


def _patch_conn(monkeypatch, tmp_path):
    """Reset thread-local and schema state, redirect get_connection to a temp DB."""
    monkeypatch.setattr("knowledge_base._conn._local", threading.local())
    monkeypatch.setattr("knowledge_base._conn._schema_ready", False)
    monkeypatch.setattr(
        "knowledge_base._conn.get_connection",
        partial(get_connection, tmp_path / "test.db"),
    )


def test_get_conn_returns_separate_connections_per_thread(tmp_path, monkeypatch):
    """Each thread must get its own SQLite connection to avoid cross-thread errors."""
    _patch_conn(monkeypatch, tmp_path)

    # Initialise schema on the main thread first — mirrors real usage where
    # the MCP server boots before handling parallel tool calls.  Without this,
    # concurrent init_schema() writes race under full-suite WAL pressure and
    # cause intermittent "database is locked" errors (issue #175).
    _get_conn()

    results = {}
    errors = []

    def worker(name):
        try:
            conn = _get_conn()
            results[name] = id(conn)
            conn.execute("SELECT COUNT(*) FROM chunks").fetchone()
        except Exception as e:
            errors.append((name, e))

    threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Thread errors: {errors}"
    conn_ids = list(results.values())
    assert len(set(conn_ids)) == 4, f"Expected 4 distinct connections, got {len(set(conn_ids))}: {conn_ids}"


def test_get_conn_reuses_connection_within_same_thread(tmp_path, monkeypatch):
    """Repeated calls on the same thread should return the same connection."""
    _patch_conn(monkeypatch, tmp_path)

    conn1 = _get_conn()
    conn2 = _get_conn()
    assert conn1 is conn2


def test_cross_thread_usage_after_parallel_burst(tmp_path, monkeypatch):
    """Simulates the exact failure from issue #19: parallel ingestion followed by
    a tool call on a different thread."""
    _patch_conn(monkeypatch, tmp_path)

    # Initialise schema on the main thread first — mirrors the sibling test and
    # the real MCP server (which boots before handling parallel tool calls).
    # Without it, the 4 workers race to run init_schema() concurrently and
    # intermittently hit "database is locked" (issue #175) — a schema-init race,
    # not the cross-thread connection bug (#19) this test targets.
    _get_conn()

    barrier = threading.Barrier(4)
    errors = []

    import traceback

    def parallel_worker():
        try:
            conn = _get_conn()
            barrier.wait(timeout=5)
            conn.execute(
                "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
                "VALUES (?, 'test', 'note', '/tmp/t.md', 0)",
                (f"hash_{threading.current_thread().ident}",),
            )
            conn.commit()
        except Exception:
            errors.append(traceback.format_exc())

    # Phase 1: parallel burst (simulates concurrent ingest calls)
    threads = [threading.Thread(target=parallel_worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Parallel phase errors: {errors}"

    # Phase 2: subsequent call on yet another thread (simulates register_paper_tool)
    post_error = []

    def subsequent_call():
        try:
            conn = _get_conn()
            count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            assert count == 4, f"Expected 4 rows, got {count}"
        except Exception as e:
            post_error.append(e)

    t = threading.Thread(target=subsequent_call)
    t.start()
    t.join()

    assert not post_error, f"Post-burst call failed: {post_error}"
