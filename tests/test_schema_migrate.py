"""Schema-version + backup-before-migrate framework (#450).

Covers the 4 acceptance criteria and the review-hardened seams (WAL-safe restore,
stamp-validate split, atomic backup, in-memory guards).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from knowledge_base import migrate as m
from knowledge_base.db import _migrate_normalize_source_uri, get_connection, init_schema
from knowledge_base.exceptions import KnowledgeBaseError


def _seed(conn: sqlite3.Connection) -> int:
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index)"
        " VALUES ('h1', 'c1', 'note', 'a.md', 0)"
    )
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]


# --- version read/write --------------------------------------------------------


def test_fresh_db_stamps_current_version(tmp_path):  # AC-1
    conn = get_connection(tmp_path / "k.db")
    init_schema(conn)
    assert m.get_schema_version(conn) == m.CURRENT_SCHEMA_VERSION


def test_get_schema_version_missing_config_returns_none():
    conn = sqlite3.connect(":memory:")
    assert m.get_schema_version(conn) is None


def test_get_schema_version_corrupt_raises():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO config VALUES ('schema_version', 'notanint')")
    with pytest.raises(KnowledgeBaseError):
        m.get_schema_version(conn)


def test_set_schema_version_stores_text(tmp_path):  # F6
    conn = get_connection(tmp_path / "k.db")
    init_schema(conn)
    m.set_schema_version(conn, 5)
    val = conn.execute(
        "SELECT value FROM config WHERE key='schema_version'"
    ).fetchone()[0]
    assert val == "5" and isinstance(val, str)


# --- init_schema validate-then-converge (F2) -----------------------------------


def test_init_schema_early_returns_when_current(tmp_path):
    conn = get_connection(tmp_path / "k.db")
    init_schema(conn)
    init_schema(conn)  # second call: version == CURRENT → no-op
    assert m.get_schema_version(conn) == 1


def test_init_schema_raises_on_newer_db(tmp_path, monkeypatch):
    conn = get_connection(tmp_path / "k.db")
    init_schema(conn)  # stamps 1
    monkeypatch.setattr(m, "CURRENT_SCHEMA_VERSION", 0)  # pretend code is older
    with pytest.raises(KnowledgeBaseError, match="newer"):
        init_schema(conn)


def test_init_schema_raises_when_behind(tmp_path, monkeypatch):
    conn = get_connection(tmp_path / "k.db")
    init_schema(conn)  # stamps 1
    monkeypatch.setattr(m, "CURRENT_SCHEMA_VERSION", 2)  # pretend code is newer
    with pytest.raises(KnowledgeBaseError, match="behind|migrate"):
        init_schema(conn)


# --- backup path resolution (F3, F8) -------------------------------------------


def test_resolve_backup_path_beside_db(tmp_path):
    conn = get_connection(tmp_path / "k.db")
    init_schema(conn)
    p = m.resolve_backup_path(conn)
    assert p is not None
    assert p.parent == tmp_path / "backups"
    assert p.suffix == ".db"


def test_resolve_backup_path_memory_returns_none():
    conn = sqlite3.connect(":memory:")
    assert m.resolve_backup_path(conn) is None


# --- backup + restore (R1, R5; AC-2) -------------------------------------------


def test_backup_creates_self_contained_file(tmp_path):  # AC-2
    conn = get_connection(tmp_path / "k.db")
    init_schema(conn)
    _seed(conn)
    out = m.backup_database(conn, m.resolve_backup_path(conn))
    assert out.is_file()
    b = sqlite3.connect(f"file:{out.as_posix()}?mode=ro", uri=True)
    try:
        assert b.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert b.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1
    finally:
        b.close()


def test_restore_backup_roundtrip_deletes_sidecars(tmp_path):  # R1
    db = tmp_path / "k.db"
    conn = get_connection(db)
    init_schema(conn)
    n = _seed(conn)
    bp = m.backup_database(conn, m.resolve_backup_path(conn))
    conn.execute("DELETE FROM chunks")
    conn.commit()
    conn.close()
    Path(str(db) + "-wal").write_bytes(b"stale")  # orphan sidecar
    m.restore_backup(db, bp)
    assert not Path(str(db) + "-wal").exists()  # sidecar deleted
    conn2 = get_connection(db)
    try:
        assert conn2.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == n
        assert conn2.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn2.close()


# --- migrate() (no-op for v1; restore-on-fail keystone, AC-4) ------------------


def test_migrate_v1_is_noop(tmp_path):
    conn = get_connection(tmp_path / "k.db")
    init_schema(conn)
    rep = m.migrate(conn)
    assert rep["pending"] == []
    assert rep["backup_path"] is None
    assert rep["applied"] == []


def test_failed_migration_restores_backup(tmp_path, monkeypatch):  # AC-4 keystone
    db = tmp_path / "k.db"
    conn = get_connection(db)
    init_schema(conn)
    before = _seed(conn)

    def boom(c: sqlite3.Connection) -> None:
        c.execute("DELETE FROM chunks")  # destructive mutation
        raise RuntimeError("boom")

    monkeypatch.setattr(m, "CURRENT_SCHEMA_VERSION", 2)
    monkeypatch.setattr(m, "_MIGRATIONS", {2: (boom, False)})

    with pytest.raises(RuntimeError, match="boom"):
        m.migrate(conn, backup_dir=tmp_path / "bk")

    # Immediately after restore (no connection open): the live WAL sidecars were
    # deleted, so no orphan can replay the partial migration on next open.
    assert not Path(str(db) + "-wal").exists()
    assert not Path(str(db) + "-shm").exists()

    conn2 = get_connection(db)  # migrate closed the original conn on failure
    try:
        assert m.get_schema_version(conn2) == 1, "version must NOT advance"
        assert conn2.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == before
        assert conn2.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn2.close()


# --- peek + report (Phase 3, F7) -----------------------------------------------


def test_peek_missing_file_returns_none(tmp_path):
    p = tmp_path / "nope.db"
    assert m.peek_schema_version(p) is None
    assert not p.exists()  # read-only peek must not create it


def test_peek_reads_stamped_version(tmp_path):
    db = tmp_path / "k.db"
    conn = get_connection(db)
    init_schema(conn)
    conn.close()
    assert m.peek_schema_version(db) == 1


def test_schema_report_matches(tmp_path):  # AC-3
    db = tmp_path / "k.db"
    conn = get_connection(db)
    init_schema(conn)
    conn.close()
    r = m.schema_report(db)
    assert r["matches"] is True
    assert r["schema_version"] == 1
    assert r["newer"] is False


def test_schema_report_unstamped_is_mismatch(tmp_path):  # AC-3
    r = m.schema_report(tmp_path / "nope.db")
    assert r["matches"] is False
    assert r["schema_version"] is None


def test_normalize_source_uri_migration_still_works(tmp_path):
    # The legacy idempotent build is now the v1 baseline; it still normalizes.
    db = tmp_path / "k.db"
    conn = get_connection(db)
    init_schema(conn)
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index)"
        r" VALUES ('h2', 'c', 'note', 'C:\a\b.md', 0)"
    )
    conn.commit()
    _migrate_normalize_source_uri(conn)
    conn.commit()
    got = conn.execute(
        "SELECT source_uri FROM chunks WHERE content_hash='h2'"
    ).fetchone()[0]
    assert got == "C:/a/b.md"
