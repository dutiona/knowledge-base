"""ADR-0015 embedding identity (#573): the schema, the declaration, the gates and each space's query instruction."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from knowledge_base import migrate, server
from knowledge_base.db import get_connection, init_schema, resolve_db_path

V2_COLUMNS = ("artifact_sha256", "pooling", "normalization", "max_input_tokens", "query_instruction")


def _columns(conn: sqlite3.Connection) -> dict[str, tuple]:
    return {row[1]: (row[2], row[3], row[4]) for row in conn.execute("PRAGMA table_info(embed_spaces)")}


def _v1_database(path, monkeypatch) -> sqlite3.Connection:
    """A database built by the v1 code path: no v2 columns, stamped 1."""
    with monkeypatch.context() as patched:
        patched.setattr(migrate, "CURRENT_SCHEMA_VERSION", 1)
        patched.setattr(migrate, "add_v2_space_columns", lambda _conn: None)
        conn = get_connection(path)
        init_schema(conn)
    assert not set(V2_COLUMNS) & set(_columns(conn))
    assert migrate.get_schema_version(conn) == 1
    return conn


# --- schema v2 ------------------------------------------------------------------


def test_every_origin_converges_to_one_v2_shape(tmp_path, monkeypatch):
    fresh = get_connection(tmp_path / "fresh.db")
    init_schema(fresh)

    stamped_v1 = _v1_database(tmp_path / "v1.db", monkeypatch)
    migrate.migrate(stamped_v1, backup_dir=tmp_path / "backups")

    legacy = _v1_database(tmp_path / "legacy.db", monkeypatch)
    legacy.execute("DELETE FROM config WHERE key = 'schema_version'")
    legacy.commit()
    init_schema(legacy)

    shapes = [_columns(conn) for conn in (fresh, stamped_v1, legacy)]
    assert shapes[0] == shapes[1] == shapes[2]
    assert set(V2_COLUMNS) <= set(shapes[0])
    for conn in (stamped_v1, legacy):
        assert migrate.get_schema_version(conn) == migrate.CURRENT_SCHEMA_VERSION
        row = conn.execute(f"SELECT {', '.join(V2_COLUMNS)} FROM embed_spaces").fetchone()  # noqa: S608  # literal names
        assert tuple(row) == (None,) * len(V2_COLUMNS)


def test_a_v1_database_refuses_to_start_until_migrated(monkeypatch, capsys, stub_document_embedder):
    from knowledge_base.indexer import main as cli
    from knowledge_base.ingest import ingest_text

    db = resolve_db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = _v1_database(db, monkeypatch)
    ingest_text(conn, "A chunk stored before the upgrade.", source_uri="/docs/a.md", source_type="markdown")
    conn.close()

    with patch.object(server.mcp, "run") as run, pytest.raises(SystemExit) as exit_info:
        server.main([])
    assert exit_info.value.code == 78
    assert "knowledge-base-ingest migrate" in capsys.readouterr().err
    run.assert_not_called()

    cli(["--db", str(db), "--quiet", "migrate"])
    assert list((db.parent / "backups").iterdir())
    conn = get_connection(db)
    assert conn.execute("SELECT COUNT(*) FROM chunks_vec").fetchone()[0] == 1
    conn.close()

    with patch.object(server.mcp, "run") as run:
        server.main([])
    run.assert_called_once_with(transport="stdio")
