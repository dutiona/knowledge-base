"""Tests for the server hub: route mounting and the entry point, stdio or ``--http``.

``server.py`` is a thin hub: it mounts the six route sub-modules and exposes
``main()``, which serves stdio or, with ``--http``, the gated HTTP app. These
tests guard that wiring: if a route fails to mount (bad import, renamed ``mcp``
export, forgotten ``mcp.mount`` call), the tool set shrinks and the assertions
below catch it.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from unittest.mock import patch

import pytest
from fastmcp import FastMCP

from knowledge_base import server
from tests.conftest import token_line

# The full tool surface, grouped by the route module that registers it. Keeping
# the groups explicit makes a dropped mount point obvious: a missing route shows
# up as exactly that route's block disappearing from the diff.
_INGESTION_TOOLS = {
    "ingest",
    "reingest",
    "ingest_url",
    "configure_chunking",
    "configure_browser_tool",
}
_SEARCH_TOOLS = {"search_index", "co_occurrence", "status"}
_EMBEDDING_TOOLS = {
    "embed_config",
    "re_embed_tool",
    "list_embed_spaces_tool",
    "create_embed_space_tool",
    "backfill_embed_space_tool",
    "promote_embed_space_tool",
    "deprecate_embed_space_tool",
    "cleanup_embed_space_tool",
    "compare_spaces_tool",
    "batch_compare_spaces_tool",
    "benchmark_spaces_tool",
}
_PAPER_TOOLS = {
    "register_paper_tool",
    "get_paper_tool",
    "add_relationship_tool",
    "get_relationships_tool",
    "record_conclusion_tool",
    "get_conclusions_tool",
    "supersede_conclusion_tool",
    "get_conclusion_chain_tool",
    "export_bibtex_tool",
    "sync_bibtex_tool",
    "suggest_relationships_tool",
    "scan_relationships",
    "relocate_paper_tool",
    "get_paper_paths_tool",
}
_EXTRACTION_TOOLS = {
    "record_method_tool",
    "record_dataset_tool",
    "record_metric_tool",
    "compare_papers_tool",
    "extract_structure_tool",
    "configure_llm_tool",
    "get_entities_tool",
    "extract_figures_tool",
    "configure_vision_tool",
    "configure_omniparser_tool",
}
_OPERATIONS_TOOLS = {
    "get_job_status_tool",
    "list_jobs_tool",
    "list_prediction_errors_tool",
    "resolve_prediction_error_tool",
}

_ALL_EXPECTED_TOOLS = (
    _INGESTION_TOOLS | _SEARCH_TOOLS | _EMBEDDING_TOOLS | _PAPER_TOOLS | _EXTRACTION_TOOLS | _OPERATIONS_TOOLS
)


def _registered_tool_names() -> set[str]:
    """Resolve the live tool names from the mounted FastMCP hub."""
    tools = asyncio.run(server.mcp.list_tools())
    return {t.name for t in tools}


def test_mcp_is_fastmcp_instance():
    assert isinstance(server.mcp, FastMCP)


def test_instructions_present_and_mentions_core_tools():
    """The hub advertises its core tools in the instructions string."""
    instructions = server.mcp.instructions
    assert instructions is not None and instructions.strip()
    for phrase in ("search", "ingest", "status"):
        assert phrase in instructions


def test_all_routes_are_mounted():
    """Every tool from all six route modules is exposed by the hub."""
    assert _registered_tool_names() == _ALL_EXPECTED_TOOLS


def test_tool_count_matches_route_inventory():
    """Sanity: the six route blocks sum to the registered tool count (no dupes)."""
    block_total = (
        len(_INGESTION_TOOLS)
        + len(_SEARCH_TOOLS)
        + len(_EMBEDDING_TOOLS)
        + len(_PAPER_TOOLS)
        + len(_EXTRACTION_TOOLS)
        + len(_OPERATIONS_TOOLS)
    )
    assert block_total == len(_ALL_EXPECTED_TOOLS)  # no name collisions across routes
    assert len(_registered_tool_names()) == len(_ALL_EXPECTED_TOOLS)


def test_each_route_block_is_fully_mounted():
    """Per-route assertion: pinpoints which route dropped if one fails to mount."""
    registered = _registered_tool_names()
    assert registered >= _INGESTION_TOOLS
    assert registered >= _SEARCH_TOOLS
    assert registered >= _EMBEDDING_TOOLS
    assert registered >= _PAPER_TOOLS
    assert registered >= _EXTRACTION_TOOLS
    assert registered >= _OPERATIONS_TOOLS


def test_main_runs_stdio_transport():
    """main() starts the server over the stdio transport."""
    with patch.object(server.mcp, "run") as mock_run:
        server.main([])
    mock_run.assert_called_once_with(transport="stdio")


# --- #569: the startup gate ---

_OPENAI_COMPAT = {
    "KNOWLEDGE_BASE_EMBED_PROVIDER": "openai_compat",
    "KNOWLEDGE_BASE_EMBED_MODEL": "qwen3-embedding",
    "KNOWLEDGE_BASE_EMBED_DIM": "8",
    "KNOWLEDGE_BASE_EMBED_URL": "https://embedder.example.test",
}


@pytest.mark.parametrize("missing", list(_OPENAI_COMPAT))
def test_main_exits_before_serving_when_a_variable_is_missing(missing, monkeypatch, capsys, http_requests, tmp_path):
    db = tmp_path / "kb.db"
    monkeypatch.setenv("KNOWLEDGE_BASE_DB", str(db))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-only")
    for name, value in _OPENAI_COMPAT.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(missing)
    with patch.object(server.mcp, "run") as run, pytest.raises(SystemExit) as exit_info:
        server.main([])
    assert exit_info.value.code == 78
    assert missing in capsys.readouterr().err
    run.assert_not_called()
    assert http_requests == []
    assert not db.exists()


def test_main_refuses_a_database_that_disagrees(monkeypatch, capsys):
    from knowledge_base.db import open_declared_db

    open_declared_db().close()
    monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_MODEL", "other-model")
    with patch.object(server.mcp, "run") as run, pytest.raises(SystemExit) as exit_info:
        server.main([])
    assert exit_info.value.code == 78
    err = capsys.readouterr().err
    assert "embed_model" in err
    assert "KNOWLEDGE_BASE_EMBED_MODEL" in err
    run.assert_not_called()


def test_main_records_a_legacy_endpoint_and_serves(monkeypatch):
    from knowledge_base.db import get_connection, init_schema

    conn = get_connection()
    init_schema(conn)
    conn.execute("DELETE FROM config WHERE key = 'embed_base_url'")
    conn.commit()
    conn.close()
    with patch.object(server.mcp, "run") as run:
        server.main([])
    run.assert_called_once_with(transport="stdio")
    conn = get_connection()
    url = conn.execute("SELECT value FROM config WHERE key = 'embed_base_url'").fetchone()["value"]
    conn.close()
    assert url == "http://embedder.example.test:11434"


def test_main_refuses_a_bad_switch(monkeypatch, capsys):
    monkeypatch.setenv("KNOWLEDGE_BASE_VISION", "yes")
    with patch.object(server.mcp, "run") as run, pytest.raises(SystemExit) as exit_info:
        server.main([])
    assert exit_info.value.code == 78
    assert "KNOWLEDGE_BASE_VISION" in capsys.readouterr().err
    run.assert_not_called()


def _run_server(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603, RUF100 -- trusted argv (this interpreter, our module); ruff versions disagree
        [sys.executable, "-m", "knowledge_base.server"],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_server_process_exits_78_without_a_declaration(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("KNOWLEDGE_BASE_EMBED_")}
    env["KNOWLEDGE_BASE_DB"] = str(tmp_path / "kb.db")
    proc = _run_server(env)
    assert proc.returncode == 78
    assert "KNOWLEDGE_BASE_EMBED_PROVIDER" in proc.stderr
    assert not (tmp_path / "kb.db").exists()


def test_server_process_serves_under_a_declaration(tmp_path):
    env = dict(os.environ)
    env["KNOWLEDGE_BASE_DB"] = str(tmp_path / "kb.db")
    proc = _run_server(env)
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "kb.db").exists()


# --- #571: --http and --token-file ---


@pytest.fixture
def served(monkeypatch):
    """Patch both transports; the HTTP one records each server it runs and marks it started."""
    import uvicorn

    runs: list[uvicorn.Server] = []

    def _run(self, *_args, **_kwargs):
        runs.append(self)
        self.started = True

    monkeypatch.setattr(uvicorn.Server, "run", _run)
    monkeypatch.setattr(server, "log_to_stderr", lambda: None, raising=False)
    with patch.object(server.mcp, "run") as stdio:
        yield runs, stdio


def _exit_code(argv: list[str]) -> int | str | None:
    with pytest.raises(SystemExit) as info:
        server.main(argv)
    return info.value.code


def test_main_serves_http_through_the_gate(served, token_file):
    from knowledge_base.auth import RequireMachine

    runs, stdio = served
    server.main(["--http", "127.0.0.1:65000", "--token-file", str(token_file)])
    (run,) = runs
    assert isinstance(run.config.app, RequireMachine)
    assert (run.config.host, run.config.port) == ("127.0.0.1", 65000)
    stdio.assert_not_called()


def test_a_bracketed_ipv6_host_is_served_without_its_brackets(served, token_file):
    runs, _ = served
    server.main(["--http", "[::1]:8000", "--token-file", str(token_file)])
    assert runs[0].config.host == "::1"


def test_http_needs_a_token_file(served, capsys, tmp_path):
    runs, stdio = served
    assert _exit_code(["--http", "127.0.0.1:8000"]) == 78
    assert "--token-file" in capsys.readouterr().err
    assert runs == [] and not stdio.called
    assert not (tmp_path / "kb-data" / "knowledge.db").exists()


def test_a_token_file_needs_http(served, capsys, token_file):
    runs, stdio = served
    assert _exit_code(["--token-file", str(token_file)]) == 78
    assert "--http" in capsys.readouterr().err
    assert runs == [] and not stdio.called


_UPPERCASE_LINE_2 = token_line("laptop") + token_line("desktop").upper()


@pytest.mark.parametrize("content", [None, _UPPERCASE_LINE_2], ids=["missing", "bad"])
def test_a_bad_token_file_exits_78_before_the_database(served, capsys, tmp_path, content):
    runs, stdio = served
    path = tmp_path / "token-hashes"
    if content is not None:
        path.write_text(content)
    assert _exit_code(["--http", "127.0.0.1:8000", "--token-file", str(path)]) == 78
    err = capsys.readouterr().err
    assert str(path) in err
    assert content is None or "line 2" in err
    assert runs == [] and not stdio.called
    assert not (tmp_path / "kb-data" / "knowledge.db").exists()


def test_the_declaration_gate_runs_before_http_is_served(served, capsys, monkeypatch, token_file):
    runs, _ = served
    monkeypatch.delenv("KNOWLEDGE_BASE_EMBED_MODEL")
    assert _exit_code(["--http", "127.0.0.1:8000", "--token-file", str(token_file)]) == 78
    assert "KNOWLEDGE_BASE_EMBED_MODEL" in capsys.readouterr().err
    assert runs == []


@pytest.mark.parametrize(
    "argv",
    [
        ["--http", "nope"],
        ["--http", "127.0.0.1:"],
        ["--http", ":80"],
        ["--http", "127.0.0.1:0"],
        ["--http", "127.0.0.1:65536"],
        ["--http", "127.0.0.1:+80"],
        ["--http", "::1:80"],
        ["--http", "[::1"],
        ["--http", "127.0.0.l:80"],
        ["--http", "localhost:80"],
        ["--http", "127.0.0.1:\uff18\uff10"],
        ["--http"],
        ["--bogus"],
    ],
)
def test_a_bad_command_line_exits_78(served, capsys, token_file, argv):
    runs, stdio = served
    if argv[0] == "--http" and len(argv) == 2:
        argv = [*argv, "--token-file", str(token_file)]
    assert _exit_code(argv) == 78
    err = capsys.readouterr().err
    assert argv[0] in err
    assert runs == [] and not stdio.called


def test_a_server_that_never_starts_exits_1(served, monkeypatch, token_file):
    import uvicorn

    monkeypatch.setattr(uvicorn.Server, "run", lambda self, *a, **k: None)
    assert _exit_code(["--http", "127.0.0.1:8000", "--token-file", str(token_file)]) == 1


def test_help_exits_0(served, capsys):
    assert _exit_code(["--help"]) == 0
    assert "--http" in capsys.readouterr().out
