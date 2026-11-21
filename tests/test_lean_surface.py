"""The lean HTTP surface (#543): what each surface serves, and the reference that documents both."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx
from unittest.mock import patch

import pytest

from knowledge_base import server
from tests.conftest import machine_token, names_the_embedder

_ROOT = Path(__file__).resolve().parent.parent


def _names(hub) -> set[str]:
    return {tool.name for tool in asyncio.run(hub.list_tools())}


def test_the_reference_documents_every_stdio_tool():
    reference = (_ROOT / "docs/reference/mcp-tools.md").read_text()
    assert set(re.findall(r"^### (\S+)$", reference, re.MULTILINE)) == _names(server.mcp)


def _call(kb_http, name, arguments, machine="laptop"):
    response = kb_http.rpc("tools/call", {"name": name, "arguments": arguments}, machine=machine)
    assert response.status_code == 200
    return response.json()["result"]


def _text(result) -> str:
    return "".join(part.get("text", "") for part in result["content"])


def test_search_fails_loudly_while_the_embedder_is_down(kb_http, stub_embedder):

    stub_embedder.down = 503
    result = _call(kb_http, "search_index", {"query": "attention"})
    assert result["isError"] is True
    assert "HTTP 503" in _text(result)
    assert not names_the_embedder(_text(result))

    assert _call(kb_http, "search_index", {"query": "attention", "mode": "fts"})["isError"] is False


def _count(table: str) -> int:
    from knowledge_base.db import get_connection

    conn = get_connection()
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608 -- a test's own literal
    finally:
        conn.close()


def _vec_table() -> str:
    from knowledge_base.db import get_connection, get_vec_table_name

    conn = get_connection()
    try:
        return get_vec_table_name(conn)
    finally:
        conn.close()


_DOCUMENT = "# Cross-machine notes\n\n" + "".join(
    f"Paragraph {i} explains why zanzibarquokka retrieval matters across machines. " for i in range(40)
)


def test_text_ingested_by_one_machine_is_found_by_another(kb_http):
    stored = _call(kb_http, "ingest_text", {"name": "notes/cross-machine.md", "content": _DOCUMENT}, machine="laptop")
    assert stored["isError"] is False
    assert json.loads(_text(stored))["source_uri"] == "content://laptop/notes/cross-machine.md"

    found = _call(kb_http, "search_index", {"query": "zanzibarquokka"}, machine="desktop")
    assert found["isError"] is False
    uris = {hit["source_uri"] for hit in json.loads(_text(found))}
    assert uris == {"content://laptop/notes/cross-machine.md"}


def test_identical_text_is_stored_once(kb_http):
    first = json.loads(_text(_call(kb_http, "ingest_text", {"name": "a.md", "content": _DOCUMENT})))
    again = json.loads(_text(_call(kb_http, "ingest_text", {"name": "a.md", "content": _DOCUMENT})))
    assert first["chunks_added"] > 0
    assert again["chunks_added"] == 0
    assert again["chunks_skipped"] == first["chunks_added"]


def test_ingest_fails_loudly_while_the_embedder_is_down(kb_http, stub_document_embedder):
    for status in (503, 401):
        stub_document_embedder.down = status
        failed = _call(kb_http, "ingest_text", {"name": "down.md", "content": _DOCUMENT})
        assert failed["isError"] is True
        assert f"HTTP {status}" in _text(failed) and "nothing was stored" in _text(failed)
        assert not names_the_embedder(_text(failed))
        assert (_count("chunks"), _count("chunk_sessions"), _count(_vec_table())) == (0, 0, 0)

    stub_document_embedder.down = None
    assert _call(kb_http, "ingest_text", {"name": "down.md", "content": _DOCUMENT})["isError"] is False
    assert _count("chunks") > 0


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("", "text"),
        ("  ", "text"),
        ("a\nb", "text"),
        ("a\u202eb", "text"),
        ("a\u2028b", "text"),
        ("/notes.md", "text"),
        ("n" * 256, "text"),
        ("ok.md", ""),
        ("ok.md", "  \n"),
    ],
    ids=[
        "empty",
        "blank",
        "newline",
        "bidi",
        "line-separator",
        "leading-slash",
        "too-long",
        "no-content",
        "blank-content",
    ],
)
def test_ingest_text_refuses_a_bad_name_or_empty_content(kb_http, name, content):
    refused = _call(kb_http, "ingest_text", {"name": name, "content": content})
    assert refused["isError"] is True
    assert ("content is empty" if name == "ok.md" else "name must be") in _text(refused)
    assert _count("chunks") == 0


def test_ingest_text_refuses_a_lone_surrogate_name(kb_http):
    arguments = '{"name":"a\\ud800b","content":"text"}'  # a lone surrogate, as a JSON escape
    call = '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"ingest_text","arguments":'
    body = call + arguments + "}}"
    response = kb_http.request(
        "POST",
        authorization=f"Bearer {machine_token('laptop')}",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        content=body.encode(),
    )
    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True
    assert "name must be" in _text(response.json()["result"])
    assert _count("chunks") == 0


def test_a_concurrent_duplicate_rolls_back_and_a_retry_completes(kb_http, monkeypatch):
    from knowledge_base.chunking import chunk_text
    from knowledge_base.db import get_connection
    from knowledge_base.utils import content_hash
    from tests.declared_env import TEST_EMBED_DIM

    second = chunk_text(_DOCUMENT)[1]
    raced: list[bool] = []

    def _racing_embed(batch, *_args, expected_dim=None, **_kwargs):
        if not raced:  # another machine stores the same passage while this call embeds
            raced.append(True)
            other = get_connection()
            other.execute(
                "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index)"
                " VALUES (?, ?, ?, ?, ?)",
                (content_hash(second), second, "markdown", "content://desktop/other.md", 0),
            )
            other.commit()
            other.close()
        return [[0.1] * (expected_dim or TEST_EMBED_DIM) for _ in batch]

    monkeypatch.setattr("knowledge_base.ingest.embed", _racing_embed)
    lost = _call(kb_http, "ingest_text", {"name": "race.md", "content": _DOCUMENT})
    assert lost["isError"] is True
    assert "another ingest stored some of the same passages meanwhile" in _text(lost)
    assert _count("chunks") == 1  # the winner's passage; the rollback itself is test_ingest's rolls_back test

    retried = json.loads(_text(_call(kb_http, "ingest_text", {"name": "race.md", "content": _DOCUMENT})))
    assert retried["chunks_skipped"] == 1
    assert retried["chunks_added"] == len(set(chunk_text(_DOCUMENT))) - 1


# --- the HTTP surface is exactly the approved map ---

_APPROVED = {"search_index", "ingest_text", "ingest_url"}


def test_http_serves_exactly_the_approved_tools(kb_http):
    response = kb_http.rpc("tools/list")
    assert response.status_code == 200
    assert {tool["name"] for tool in response.json()["result"]["tools"]} == _APPROVED


def test_every_http_tool_is_its_stdio_tool():
    stdio = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    for tool in asyncio.run(server.network.list_tools()):
        assert tool.parameters == stdio[tool.name].parameters
        assert tool.description == stdio[tool.name].description


def test_no_stdio_only_tool_is_reachable_over_http(kb_http, tmp_path, stub_document_embedder):
    canary = tmp_path / "canary.md"
    canary.write_text("ultraviolet canary word")
    for name in _names(server.mcp) - _APPROVED:
        arguments = {"path": str(canary)} if name in ("ingest", "reingest") else {}
        assert _call(kb_http, name, arguments)["isError"] is True, name
    assert _call(kb_http, "ingest", {"path": "/proc/self/environ"})["isError"] is True
    assert (_count("chunks"), list(stub_document_embedder)) == (0, [])


def test_no_http_tool_takes_a_path():
    for tool in asyncio.run(server.network.list_tools()):
        for prop in tool.parameters.get("properties", {}):
            assert prop != "path" and not prop.endswith(("_path", "_dir")), (tool.name, prop)


def test_an_unexpected_failure_reveals_nothing_over_http(kb_http, monkeypatch):
    def _broken(*_args, **_kwargs):
        raise RuntimeError("marker-7f3a internal detail")

    monkeypatch.setattr("knowledge_base.routes.search.search", _broken)
    result = _call(kb_http, "search_index", {"query": "q"})
    assert result["isError"] is True
    assert "marker-7f3a" not in _text(result)


@pytest.mark.parametrize("hub", ["mcp", "network"])
def test_each_hubs_instructions_name_its_real_tools(hub):
    instructions = getattr(server, hub).instructions
    quoted = set(re.findall(r"'([a-z_]+)'", instructions))
    assert quoted <= _names(getattr(server, hub))
    if hub == "network":
        assert quoted == _APPROVED


# --- no HTTP tool deletes: the vision/browser refusal, and the behaviour it protects ---


@pytest.mark.parametrize("var", ["KNOWLEDGE_BASE_VISION", "KNOWLEDGE_BASE_BROWSER"])
def test_http_refuses_to_build_while_vision_or_the_browser_is_on(monkeypatch, token_file, var):
    from knowledge_base.auth import TokenTable
    from knowledge_base.exceptions import ValidationError

    monkeypatch.setenv("KNOWLEDGE_BASE_VISION", "off")
    monkeypatch.setenv("KNOWLEDGE_BASE_BROWSER", "off")
    monkeypatch.setenv(var, "on")
    with pytest.raises(ValidationError, match=var):
        server.build_http_app(TokenTable.load(token_file))


def _snapshot(conn) -> dict[str, set[tuple]]:
    """Every row of every regular table. Virtual tables and their shadow tables (FTS5, sqlite-vec) are internal
    storage an insert rewrites in place, so they are left out."""
    rows = conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'").fetchall()
    virtual = [name for name, sql in rows if (sql or "").upper().startswith("CREATE VIRTUAL")]
    tables = [name for name, _ in rows if not name.startswith(("sqlite_", *virtual))]
    return {t: {tuple(r) for r in conn.execute(f"SELECT * FROM {t}")} for t in tables}  # noqa: S608 -- schema names


#: The only rows a network tool may add; every other table must be exactly as it was.
_INSERT_ONLY = {"chunks", "chunk_sessions", "prediction_errors"}


def test_no_http_tool_deletes(
    declared_db,
    monkeypatch,
    http_requests,
    fake_dns,
    public_net,
    stub_embedder,
    stub_document_embedder,
    operator_ollama,
):
    from fastmcp import Client

    from knowledge_base.conclusions import record_conclusion
    from knowledge_base.db import get_connection
    from knowledge_base.ingest import ingest_text
    from knowledge_base.papers import register_paper
    from knowledge_base.utils import content_hash
    from knowledge_base.web import ingest_url

    url = "https://page.example.test/article"
    fake_dns["page.example.test"] = ["203.0.113.10"]
    page = "<html><head><title>T</title></head><body><article><h1>A</h1>{}</article></body></html>"

    def _serve(text):
        http_requests.handlers["page.example.test"] = lambda request: httpx.Response(
            200, html=page.format(f"<p>{text}</p>")
        )

    conn = get_connection()
    ingest_text(conn, "the original notes about quokkas", source_uri="content:///notes.md", source_type="markdown")
    ingest_text(conn, "the original page about quokkas " * 30, source_uri=url, source_type="web")
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index)"
        " VALUES (?, ?, 'figure', ?, ?)",
        (content_hash("a figure"), "a figure", url, 2_000_000),
    )
    conn.commit()
    register_paper(conn, "A paper", source_uri=url)
    ids = [r[0] for r in conn.execute("SELECT id FROM chunks ORDER BY id LIMIT 2")]
    record_conclusion(conn, "quokkas are happy", 0.9, ids)
    before = _snapshot(conn)

    monkeypatch.setenv("KNOWLEDGE_BASE_VISION", "off")
    monkeypatch.setenv("KNOWLEDGE_BASE_BROWSER", "off")
    _serve("an edited page about quokkas, now with other words entirely")

    async def _run():
        async with Client(server.network) as client:
            for mode in ("hybrid", "fts", "vec"):
                await client.call_tool("search_index", {"query": "quokkas", "mode": mode})
            await client.call_tool("ingest_text", {"name": "notes.md", "content": "edited notes about quokkas"})
            await client.call_tool("ingest_text", {"name": "notes.md", "content": "the original notes about quokkas"})
            await client.call_tool("ingest_url", {"url": url})

    asyncio.run(_run())
    after = _snapshot(conn)
    for table, rows in before.items():
        if table in _INSERT_ONLY:
            assert rows <= after[table], table  # rows added, none deleted or rewritten
        elif table == "embed_spaces":  # only the active space's chunk_count, which may only grow
            columns = [c[1] for c in conn.execute("PRAGMA table_info(embed_spaces)")]
            k, name, status = (columns.index(c) for c in ("chunk_count", "name", "status"))
            assert {r[:k] + r[k + 1 :] for r in after[table]} == {r[:k] + r[k + 1 :] for r in rows}
            old = {r[name]: r for r in rows}
            for r in after[table]:
                grown = r[k] >= old[r[name]][k] if r[status] == "active" else r[k] == old[r[name]][k]
                assert grown, r[name]
        else:
            assert after[table] == rows, table  # e.g. no job queued, no relationship touched
    assert conn.execute("SELECT COUNT(*) FROM chunks WHERE source_type = 'figure'").fetchone()[0] == 1

    # Control: the same URL through the local tool with vision on replaces the page's figure chunk.
    monkeypatch.setenv("KNOWLEDGE_BASE_VISION", "on")
    ingest_url(conn, url)
    assert conn.execute("SELECT COUNT(*) FROM chunks WHERE source_type = 'figure'").fetchone()[0] == 0
    conn.close()


# --- the request-body cap ---

_JSON_HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}


def _oversized_ingest() -> bytes:
    arguments = {"name": "big.md", "content": "x" * server.MAX_REQUEST_BYTES}
    return json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "ingest_text", "arguments": arguments}}
    ).encode()


def test_a_body_over_the_cap_is_refused_before_any_tool_runs(kb_http, stub_document_embedder):
    authorization = {"Authorization": f"Bearer {machine_token('laptop')}"}
    body = _oversized_ingest()
    sized = httpx.post(kb_http.url + "/mcp", content=body, headers={**_JSON_HEADERS, **authorization}, timeout=30)
    chunked = httpx.post(
        kb_http.url + "/mcp",
        content=iter([body[i : i + 65536] for i in range(0, len(body), 65536)]),
        headers={**_JSON_HEADERS, **authorization},
        timeout=30,
    )
    assert (sized.status_code, sized.content) == (413, b"")
    assert (chunked.status_code, chunked.content) == (413, b"")
    assert (_count("chunks"), list(stub_document_embedder)) == (0, [])


def test_a_body_at_the_cap_is_served(kb_http):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode()
    body += b" " * (server.MAX_REQUEST_BYTES - len(body))
    response = kb_http.request(
        "POST", authorization=f"Bearer {machine_token('laptop')}", headers=_JSON_HEADERS, content=body
    )
    assert response.status_code == 200
    assert {tool["name"] for tool in response.json()["result"]["tools"]} == _APPROVED


def test_an_unauthenticated_oversized_body_meets_the_gate(kb_http):
    response = kb_http.request("POST", headers=_JSON_HEADERS, content=_oversized_ingest())
    assert response.status_code == 401


def test_docs_state_the_counts_per_surface():
    counts = f"{len(_names(server.mcp))} tools over stdio, {len(_names(server.network))} over HTTP"
    for doc in ("README.md", "AGENTS.md", "GEMINI.md", "docs/reference/mcp-tools.md"):
        assert counts in (_ROOT / doc).read_text(), doc


@pytest.mark.parametrize(
    ("hub", "tool", "target", "arguments"),
    [
        ("mcp", "get_paper_tool", "knowledge_base.routes.papers.get_paper", {"paper_id": 1}),
        ("network", "search_index", "knowledge_base.routes.search.search", {"query": "q"}),
    ],
    ids=["stdio", "network"],
)
def test_a_domain_error_reads_as_its_message(declared_db, monkeypatch, hub, tool, target, arguments):
    """A KnowledgeBaseError a tool lets through reads as its own text on both hubs (#598, #543 D6)."""
    import threading

    from fastmcp import Client

    from knowledge_base.exceptions import NotFoundError

    monkeypatch.setattr("knowledge_base._conn._local", threading.local())

    async def _run():
        async with Client(getattr(server, hub)) as client:
            return await client.call_tool(tool, arguments, raise_on_error=False)

    with patch(target, side_effect=NotFoundError("marker refusal")):
        result = asyncio.run(_run())
    assert result.is_error
    assert [getattr(c, "text", None) for c in result.content] == ["marker refusal"]
