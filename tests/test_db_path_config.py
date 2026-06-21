"""Configurable DB path resolution (#449).

Precedence: explicit CLI arg > ``KNOWLEDGE_BASE_DB`` env var > ``DEFAULT_DB_PATH``.
No silent fallback masks a configured-but-new path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_base.db import (
    DB_PATH_ENV_VAR,
    DEFAULT_DB_PATH,
    get_connection,
    resolve_db_path,
)


def _main_db_file(conn) -> Path:
    """The file backing the connection's ``main`` database."""
    rows = conn.execute("PRAGMA database_list").fetchall()
    return Path(next(r[2] for r in rows if r[1] == "main"))


# --- resolve_db_path precedence -------------------------------------------------


def test_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DB_PATH_ENV_VAR, raising=False)
    assert resolve_db_path() == DEFAULT_DB_PATH


def test_env_var_used(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "from_env.db"
    monkeypatch.setenv(DB_PATH_ENV_VAR, str(target))
    assert resolve_db_path() == target


def test_cli_overrides_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_path = tmp_path / "from_env.db"
    cli_path = tmp_path / "from_cli.db"
    monkeypatch.setenv(DB_PATH_ENV_VAR, str(env_path))
    assert resolve_db_path(cli_path) == cli_path


def test_empty_env_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(DB_PATH_ENV_VAR, "")
    assert resolve_db_path() == DEFAULT_DB_PATH


def test_whitespace_env_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(DB_PATH_ENV_VAR, "   ")
    assert resolve_db_path() == DEFAULT_DB_PATH


def test_tilde_expanded(monkeypatch: pytest.MonkeyPatch) -> None:
    # A leading ~ (from a config file or quoted arg, where the shell did not
    # expand it) must resolve to $HOME, not a literal "~" directory.
    monkeypatch.setenv(DB_PATH_ENV_VAR, "~/kb_via_env.db")
    assert resolve_db_path() == Path.home() / "kb_via_env.db"
    assert resolve_db_path(Path("~/kb_via_cli.db")) == Path.home() / "kb_via_cli.db"


# --- get_connection honors the resolved path (server path) ----------------------


def test_get_connection_honors_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The MCP server reaches the DB via get_connection() with no argument
    # (knowledge_base._conn._get_conn). Setting the env var must redirect it.
    target = tmp_path / "nested" / "server.db"  # parent does not exist yet
    monkeypatch.setenv(DB_PATH_ENV_VAR, str(target))
    conn = get_connection()
    try:
        assert _main_db_file(conn) == target
        assert target.exists(), "configured path is created, not silently defaulted"
    finally:
        conn.close()


def test_get_connection_explicit_path_ignores_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(DB_PATH_ENV_VAR, str(tmp_path / "env.db"))
    explicit = tmp_path / "explicit.db"
    conn = get_connection(explicit)
    try:
        assert _main_db_file(conn) == explicit
    finally:
        conn.close()


# --- MCP status() reports the resolved path, not the hardcoded default ----------


def test_status_reports_env_db_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import json
    import threading

    import knowledge_base._conn as conn_mod
    from knowledge_base.routes.search import status

    db_path = tmp_path / "configured.db"
    monkeypatch.setenv(DB_PATH_ENV_VAR, str(db_path))
    # Reset the thread-local connection cache so _get_conn opens the env DB fresh.
    monkeypatch.setattr(conn_mod, "_local", threading.local())
    monkeypatch.setattr(conn_mod, "_schema_ready", False)

    result = json.loads(status())
    assert result["db_path"] == str(db_path), "status must report the env-resolved DB"
