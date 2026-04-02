"""Tests for _validate_bib_path return value usage in server tool wrappers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from knowledge_base.db import get_connection, init_schema
from knowledge_base.papers import register_paper
from knowledge_base.routes.papers import _validate_bib_path


# --- _validate_bib_path unit tests ---


def test_validate_bib_path_returns_resolved(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    bib = tmp_path / "refs.bib"
    result = _validate_bib_path(str(bib))
    assert isinstance(result, Path)
    assert result == bib.resolve()


def test_validate_bib_path_rejects_bad_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    with pytest.raises(ValueError, match="extension"):
        _validate_bib_path(str(tmp_path / "evil.txt"))


# --- export_bibtex_tool uses validated path ---


def test_export_bibtex_tool_uses_validated_path(tmp_path, monkeypatch):
    """export_bibtex_tool must write to the path returned by _validate_bib_path,
    not re-resolve the raw input."""
    from knowledge_base.routes import papers as papers_routes

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    register_paper(conn, "Test Paper", ["Auth"], 2024)

    monkeypatch.setattr(papers_routes, "_get_conn", lambda: conn)

    # _validate_bib_path returns a *different* resolved path than naive resolution
    # would produce.  If the code discards the return value, it won't write here.
    redirected = tmp_path / "redirected.bib"
    with patch.object(
        papers_routes, "_validate_bib_path", return_value=redirected
    ) as mock:
        raw_input = str(tmp_path / "original.bib")
        result = json.loads(papers_routes.export_bibtex_tool(output_path=raw_input))
        mock.assert_called_once_with(raw_input)

    # The file should be written to the *validated* path, not the raw input
    assert redirected.exists(), "File was not written to the validated path"
    assert "written_to" in result
    assert result["written_to"] == str(redirected)


# --- sync_bibtex_tool uses validated path ---


def test_sync_bibtex_tool_uses_validated_path(tmp_path, monkeypatch):
    """sync_bibtex_tool must pass the validated path to sync_bibtex,
    not the raw input string."""
    from knowledge_base.routes import papers as papers_routes

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    register_paper(conn, "Test Paper", ["Auth"], 2024)

    monkeypatch.setattr(papers_routes, "_get_conn", lambda: conn)

    redirected = tmp_path / "redirected.bib"
    with patch.object(
        papers_routes, "_validate_bib_path", return_value=redirected
    ) as mock:
        raw_input = str(tmp_path / "original.bib")

        # Also patch sync_bibtex to capture what path it receives
        with patch.object(
            papers_routes, "sync_bibtex", return_value={"synced": 1, "skipped": 0}
        ) as sync_mock:
            papers_routes.sync_bibtex_tool(output_path=raw_input)
            mock.assert_called_once_with(raw_input)
            # sync_bibtex should receive the validated path as a string
            actual_path = sync_mock.call_args[0][1]  # second positional arg
            assert actual_path == str(redirected), (
                f"sync_bibtex received raw input {actual_path!r} "
                f"instead of validated path {str(redirected)!r}"
            )
