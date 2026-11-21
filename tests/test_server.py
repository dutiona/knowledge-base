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
    "ingest_text",
    "configure_chunking",
    "configure_browser_tool",
}
_SEARCH_TOOLS = {"search_index", "co_occurrence", "status"}
_EMBEDDING_TOOLS = {
    "embed_config",
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
    assert not (tmp_path / "kb.db.store-lock").exists()


def test_main_refuses_a_database_that_disagrees(monkeypatch, capsys):
    from knowledge_base.db import open_declared_db

    open_declared_db().close()
    monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_MODEL", "other-model")
    with patch.object(server.mcp, "run") as run, pytest.raises(SystemExit) as exit_info:
        server.main([])
    assert exit_info.value.code == 78
    err = capsys.readouterr().err
    assert "model" in err
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
    monkeypatch.setattr(server, "log_to_stderr", lambda: None)
    # --http refuses vision and the browser fallback, which the suite's declaration switches on.
    monkeypatch.setenv("KNOWLEDGE_BASE_VISION", "off")
    monkeypatch.setenv("KNOWLEDGE_BASE_BROWSER", "off")
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


def _start_http_server(tmp_path, token_file, db) -> tuple[subprocess.Popen[bytes], str]:
    """A real ``knowledge-base --http`` process on a free port, returned once it answers, with its URL.

    Its output goes to ``tmp_path``'s ``stdout`` and ``stderr`` files.
    """
    import socket
    import time

    import httpx

    with socket.create_server(("127.0.0.1", 0)) as probe:
        port = probe.getsockname()[1]
    env = dict(os.environ, KNOWLEDGE_BASE_DB=str(db), KNOWLEDGE_BASE_VISION="off", KNOWLEDGE_BASE_BROWSER="off")
    err = tmp_path / "stderr"
    with (tmp_path / "stdout").open("wb") as stdout, err.open("wb") as stderr:
        child = subprocess.Popen(  # noqa: S603, RUF100 -- trusted argv (this interpreter, our module); ruff versions disagree
            [
                sys.executable,
                "-m",
                "knowledge_base.server",
                "--http",
                f"127.0.0.1:{port}",
                "--token-file",
                str(token_file),
            ],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
        )
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        deadline = time.monotonic() + 30
        while True:
            assert child.poll() is None, err.read_text()
            try:
                if httpx.post(url, timeout=1).status_code == 401:
                    return child, url
            except httpx.TransportError:
                pass
            assert time.monotonic() < deadline, err.read_text()
            time.sleep(0.1)
    except BaseException:
        child.kill()
        child.wait(15)
        raise


def test_the_http_process_logs_each_call_and_never_the_token(tmp_path, token_file):
    """Only a real process shows what reaches stderr: caplog sees records before any handler formats them."""
    import json
    import signal
    import time

    import httpx

    from tests.conftest import machine_token

    out, err = tmp_path / "stdout", tmp_path / "stderr"
    token = machine_token("laptop")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    def call(name: str, arguments: dict) -> httpx.Response:
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
        )
        return httpx.post(url, content=body, headers={**headers, "Authorization": f"Bearer {token}"}, timeout=10)

    def added_after(size: int, needle: str) -> str:
        deadline = time.monotonic() + 5
        while True:
            text = err.read_bytes()[size:].decode(errors="replace")
            if needle in text or time.monotonic() > deadline:
                return text
            time.sleep(0.05)

    child, url = _start_http_server(tmp_path, token_file, tmp_path / "kb.db")
    try:
        assert call("search_index", {"query": "q", "mode": "fts"}).status_code == 200
        size = err.stat().st_size
        call(token, {})
        assert "me_[redacted]" in added_after(size, "me_[redacted]")
        # FastMCP logs the rejected argument's name and value in a traceback today; the whole-output
        # assertion below holds whether or not a later release keeps logging it.
        assert call("search_index", {"query": "q", "mode": "fts", token: token}).json()["result"]["isError"] is True
        forged = "INFO knowledge_base.auth: machine desktop called 'search_index'"
        for name in (f"x\n{forged}", f"x\x00{forged}", "a" * 2000 + forged):
            call(name, {})
    finally:
        child.send_signal(signal.SIGTERM)
        try:
            child.wait(15)
        except subprocess.TimeoutExpired:
            child.kill()
            raise

    lines = err.read_text().split("\n")
    assert sum(line.startswith("INFO knowledge_base.auth: machine laptop called 'search_index'") for line in lines) == 2
    assert not any(line.startswith("INFO knowledge_base.auth: machine desktop") for line in lines)
    assert all(len(line) <= 2002 for line in lines)
    for path in (out, err):
        data = path.read_bytes()
        assert token.encode() not in data
        assert b"\x00" not in data


@pytest.mark.parametrize("var", ["KNOWLEDGE_BASE_VISION", "KNOWLEDGE_BASE_BROWSER"])
def test_http_exits_78_while_vision_or_the_browser_is_on(served, token_file, capsys, monkeypatch, var):
    from knowledge_base.db import resolve_db_path

    runs, _ = served
    monkeypatch.setenv(var, "on")
    assert _exit_code(["--http", "127.0.0.1:65000", "--token-file", str(token_file)]) == 78
    assert var in capsys.readouterr().err
    assert runs == []
    assert not resolve_db_path().exists()


# --- #596: the store lock ---


def _db():
    from knowledge_base.db import resolve_db_path

    return resolve_db_path()


def _serve_argv(transport: str, token_file) -> list[str]:
    return [] if transport == "stdio" else ["--http", "127.0.0.1:65000", "--token-file", str(token_file)]


def _on_serve(monkeypatch, served, transport: str, action) -> None:
    """Run *action* while the transport serves, in place of serving."""
    import uvicorn

    _, stdio = served
    if transport == "stdio":
        stdio.side_effect = lambda *_a, **_k: action()
    else:

        def _run(self, *_args, **_kwargs):
            action()
            self.started = True

        monkeypatch.setattr(uvicorn.Server, "run", _run)


@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_a_server_refuses_while_the_store_is_held(served, capsys, token_file, transport):
    from knowledge_base.store_lock import hold, lock_path

    runs, stdio = served
    with hold(_db(), "test"):
        assert _exit_code(_serve_argv(transport, token_file)) == 78
    assert str(lock_path(_db())) in capsys.readouterr().err
    assert runs == [] and not stdio.called
    assert not _db().exists()


@pytest.mark.parametrize("outer", ["stdio", "http"])
@pytest.mark.parametrize("inner", ["stdio", "http"])
def test_a_server_refuses_while_another_serves(served, monkeypatch, capsys, token_file, outer, inner):
    from knowledge_base.store_lock import hold

    codes: list[int | str | None] = []
    _on_serve(monkeypatch, served, outer, lambda: codes.append(_exit_code(_serve_argv(inner, token_file))))
    server.main(_serve_argv(outer, token_file))
    assert codes == [78]
    assert "in use" in capsys.readouterr().err
    with hold(_db(), "test"):
        pass


@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_the_lock_outlives_a_running_job(served, monkeypatch, token_file, transport):
    import threading

    from knowledge_base.db import get_connection
    from knowledge_base.exceptions import KnowledgeBaseError
    from knowledge_base.jobs import submit_job
    from knowledge_base.papers import register_paper
    from knowledge_base.store_lock import hold

    started, release, served_done = threading.Event(), threading.Event(), threading.Event()

    def _blocking_job(*_args, **_kwargs):
        started.set()
        release.wait(30)
        return {}

    monkeypatch.setattr("knowledge_base.auto_relate.auto_relate", _blocking_job)

    def _submit():
        conn = get_connection(_db())
        submit_job(conn, register_paper(conn, "P")["paper_id"], "auto_relate")
        conn.close()
        assert started.wait(30)
        served_done.set()

    _on_serve(monkeypatch, served, transport, _submit)
    main = threading.Thread(target=server.main, args=(_serve_argv(transport, token_file),))
    main.start()
    try:
        assert served_done.wait(30)
        with pytest.raises(KnowledgeBaseError), hold(_db(), "test"):
            pass
    finally:
        release.set()
        main.join(30)
    assert not main.is_alive()
    with hold(_db(), "test"):
        pass
    conn = get_connection(_db())
    assert [r["status"] for r in conn.execute("SELECT status FROM jobs")] == ["completed"]
    conn.close()


def test_a_killed_http_server_leaves_no_stale_lock(tmp_path, token_file):
    import signal

    from knowledge_base import indexer
    from knowledge_base.store_lock import lock_path

    db = tmp_path / "kb.db"
    child, _ = _start_http_server(tmp_path, token_file, db)
    try:
        with pytest.raises(SystemExit) as refused:
            indexer.main(["--db", str(db), "rebind"])
        assert refused.value.code == 1
        inode = lock_path(db).stat().st_ino
    finally:
        child.send_signal(signal.SIGKILL)
        child.wait(15)
    indexer.main(["--db", str(db), "--quiet", "rebind"])
    assert lock_path(db).stat().st_ino == inode


# --- #578: a refusal names the holder ---

_SERVER_LINE = {"stdio": "knowledge-base (stdio) (pid ", "http": "knowledge-base --http (pid "}


@pytest.mark.parametrize("command", ["re-embed", "rebind"])
@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_a_transition_against_a_running_server_names_it(served, monkeypatch, capsys, token_file, transport, command):
    import json

    from knowledge_base import indexer

    codes: list[int | str | None] = []

    def _transition():
        with pytest.raises(SystemExit) as refused:
            indexer.main(["--db", str(_db()), command])
        codes.append(refused.value.code)

    _on_serve(monkeypatch, served, transport, _transition)
    server.main(_serve_argv(transport, token_file))
    assert codes == [1]
    error = json.loads(capsys.readouterr().err)["error"]
    assert f"{_SERVER_LINE[transport]}{os.getpid()}, since " in error


# --- Every tool call ends with no transaction open (#597's class) ---


def _call(tool: str, arguments: dict):
    from fastmcp import Client

    async def _run():
        async with Client(server.mcp) as client:
            return await client.call_tool(tool, arguments, raise_on_error=False)

    return asyncio.run(_run())


@pytest.fixture
def fresh_thread_connections(monkeypatch):
    """Tool calls open their thread's connection on this test's database, not a connection an earlier test left."""
    import threading

    monkeypatch.setattr("knowledge_base._conn._local", threading.local())


def _writes_at_once() -> None:
    from knowledge_base.db import get_connection

    other = get_connection()
    try:
        other.execute("PRAGMA busy_timeout = 0")
        other.execute("BEGIN IMMEDIATE")  # refused with "database is locked" while a call still holds a write
        other.rollback()
    finally:
        other.close()


def test_a_failed_tool_call_leaves_no_transaction_open(declared_db, fresh_thread_connections, tmp_path):
    from knowledge_base.db import get_connection

    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    with patch("knowledge_base.papers.compute_file_hash", side_effect=RuntimeError("disk on fire")):
        result = _call("register_paper_tool", {"title": "P", "source_uri": str(pdf)})  # fails after its first insert
    assert result.is_error
    _writes_at_once()
    conn = get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 0
    finally:
        conn.close()


def test_a_tool_that_returns_with_a_transaction_open_is_rolled_back(declared_db, fresh_thread_connections, caplog):
    from knowledge_base.db import get_connection

    def _forgets_to_commit(conn, job_id):
        conn.execute("INSERT INTO config (key, value) VALUES ('probe', 'x')")
        return {"id": job_id}

    with patch("knowledge_base.routes.operations.get_job", _forgets_to_commit):
        assert not _call("get_job_status_tool", {"job_id": 1}).is_error
    _writes_at_once()
    conn = get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM config WHERE key = 'probe'").fetchone()[0] == 0
    finally:
        conn.close()
    assert any("get_job_status_tool" in r.getMessage() for r in caplog.records if r.levelname == "WARNING")


def test_every_tool_is_registered_through_the_boundary():
    """A tool decorated with ``@mcp.tool()`` directly skips the call boundary that ends its transaction."""
    import ast
    from pathlib import Path

    from knowledge_base import routes

    direct = []
    for source in Path(routes.__file__).parent.glob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for decorator in node.decorator_list:
                    call = decorator.func if isinstance(decorator, ast.Call) else decorator
                    if isinstance(call, ast.Attribute) and call.attr == "tool":
                        direct.append(f"{source.stem}.{node.name}")
    assert direct == [], f"register these through routes.tool(mcp): {sorted(direct)}"


# --- #598: every stdio tool reports a failure as a tool error ---


def _argument(schema: dict, missing: str):
    """A value valid for *schema* that names nothing stored."""
    if "enum" in schema:
        return schema["enum"][0]
    if "anyOf" in schema:
        return _argument(next(s for s in schema["anyOf"] if s.get("type") != "null"), missing)
    kind = schema.get("type")
    if kind == "string":
        return missing
    if kind == "integer":
        return schema.get("minimum", 0)
    if kind == "number":
        return float(schema.get("minimum", 0))
    if kind == "boolean":
        return False
    if kind == "array":
        return []
    return {}


def test_no_stdio_tool_reports_a_failure_as_a_result(
    declared_db,
    fresh_thread_connections,
    stub_embedder,
    stub_document_embedder,
    http_requests,
    fake_dns,
    tmp_path,
):
    """Walk every registered tool: a failure must be a tool error, never an ``{"error": ...}`` result (#598)."""
    import json

    import jsonschema
    from fastmcp import Client

    missing = str(tmp_path / "missing-7f3a")

    async def _walk():
        returned = {}
        async with Client(server.mcp) as client:
            for t in await client.list_tools():
                schema = t.inputSchema
                arguments = {
                    name: _argument(schema["properties"][name], missing) for name in schema.get("required", [])
                }
                jsonschema.validate(arguments, schema)  # FastMCP accepts the call, so the tool's body runs
                result = await client.call_tool(t.name, arguments, raise_on_error=False)
                returned[t.name] = None if result.is_error else "".join(getattr(c, "text", "") for c in result.content)
        return returned

    with (
        patch("knowledge_base.vision._vision_call", side_effect=RuntimeError("no vision in tests")),
        patch("knowledge_base.llm._llm_call", side_effect=RuntimeError("no LLM in tests")),
    ):
        returned = asyncio.run(_walk())

    def _is_error_object(text):
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            return False
        return isinstance(value, dict) and "error" in value

    assert len(returned) == 47
    assert sorted(name for name, text in returned.items() if text is not None and _is_error_object(text)) == []
