"""KB over HTTP behind per-machine Bearer tokens (#571), through the real app under uvicorn."""

from __future__ import annotations

import asyncio
import logging
import threading
import time

import pytest
from starlette.routing import Route

from knowledge_base import server
from tests.conftest import machine_token, token_line

_REFUSED_CREDENTIALS = {
    "none": None,
    "not-a-token": "Bearer not-a-token",
    "lowercase-scheme": f"bearer {machine_token('laptop')}",
    "basic": f"Basic {machine_token('laptop')}",
    "stranger": f"Bearer {machine_token('stranger')}",
    "twice": [f"Bearer {machine_token('laptop')}"] * 2,
    "two-machines": [f"Bearer {machine_token('laptop')}", f"Bearer {machine_token('desktop')}"],
}

_JSON = {"Accept": "application/json", "Content-Type": "application/json"}
_UPGRADE = {
    "Connection": "Upgrade",
    "Upgrade": "websocket",
    "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
    "Sec-WebSocket-Version": "13",
}
_PATHS = [
    "/",
    "/nope",
    "/mcp/",
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
    "/.well-known/oauth-authorization-server",
]


def _rpc_body(method: str, params: dict | None = None) -> bytes:
    import json

    return json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()


def _chunks() -> int:
    from knowledge_base.db import get_connection

    conn = get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    finally:
        conn.close()


_CALLS = [
    ("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}),
    ("tools/list", None),
    ("tools/call", {"name": "search_index", "arguments": {"query": "attention"}}),
    ("tools/call", {"name": "ingest_text", "arguments": {"name": "a.md", "content": "a note"}}),
]


@pytest.mark.parametrize("credential", list(_REFUSED_CREDENTIALS), ids=list(_REFUSED_CREDENTIALS))
def test_every_request_without_a_valid_token_is_refused(kb_http, stub_embedder, credential):
    authorization = _REFUSED_CREDENTIALS[credential]
    inner_paths = [
        r.path for r in server.network.http_app(path="/mcp", stateless_http=True).routes if isinstance(r, Route)
    ]
    attempts = [("POST", "/mcp", _JSON, _rpc_body(method, params)) for method, params in _CALLS]
    attempts += [("GET", "/mcp", {}, None), ("DELETE", "/mcp", {}, None), ("GET", "/mcp", _UPGRADE, None)]
    attempts += [(method, path, {}, None) for path in _PATHS + inner_paths for method in ("GET", "POST")]
    for method, path, headers, body in attempts:
        response = kb_http.request(method, path, authorization=authorization, headers=headers, content=body)
        assert (response.status_code, response.content) == (401, b""), (method, path)
        assert response.headers["www-authenticate"] == "Bearer"
    assert stub_embedder == []
    assert _chunks() == 0

    for method, params in _CALLS[2:]:
        response = kb_http.rpc(method, params)
        assert response.status_code == 200
        assert response.json()["result"]["isError"] is False
    assert stub_embedder == ["attention"]
    assert _chunks() == 1


def test_tools_list_is_served(kb_http):
    response = kb_http.rpc("tools/list")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    served = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert served == {tool.name for tool in asyncio.run(server.network.list_tools())}


def test_the_first_request_may_be_a_tool_call(kb_http, stub_embedder):
    response = kb_http.rpc("tools/call", {"name": "search_index", "arguments": {"query": "x"}}, machine="desktop")
    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert "mcp-session-id" not in response.headers
    assert stub_embedder == ["x"]


def test_the_tailnet_host_is_served(kb_http):
    proxied = {"Host": "kb.example-tailnet.ts.net", "X-Forwarded-For": "192.0.2.9", "X-Forwarded-Proto": "https"}
    assert kb_http.rpc("tools/list", headers=proxied).status_code == 200


def test_statuses_with_a_token(kb_http):
    authorization = f"Bearer {machine_token('laptop')}"
    assert kb_http.request("GET", "/mcp", authorization=authorization).status_code == 405
    assert kb_http.request("DELETE", "/mcp", authorization=authorization).status_code == 405
    assert kb_http.request("GET", "/nope", authorization=authorization).status_code == 404


def test_a_restart_needs_no_initialize(kb_http, token_file):
    initialize = kb_http.rpc(*_CALLS[0])
    assert initialize.status_code == 200
    assert "mcp-session-id" not in initialize.headers
    with token_file.open("a") as f:
        f.write(token_line("phone"))
    assert kb_http.rpc("tools/list", machine="phone").status_code == 401

    kb_http.restart()

    for machine in ("laptop", "phone"):
        response = kb_http.rpc(
            "tools/call", {"name": "search_index", "arguments": {"query": "q", "mode": "fts"}}, machine=machine
        )
        assert response.status_code == 200
        assert response.json()["result"]["isError"] is False


def test_a_stop_answers_the_call_in_flight(kb_http, monkeypatch):
    from tests.declared_env import TEST_EMBED_DIM

    entered, release = threading.Event(), threading.Event()

    def _blocking_embed(*_args, **_kwargs):
        entered.set()
        release.wait(10)
        return [0.1] * TEST_EMBED_DIM

    monkeypatch.setattr("knowledge_base.search.embed_query", _blocking_embed)
    answers = []
    call = threading.Thread(
        target=lambda: answers.append(kb_http.rpc("tools/call", {"name": "search_index", "arguments": {"query": "q"}}))
    )
    call.start()
    assert entered.wait(10)
    stopper = threading.Thread(target=kb_http.stop)
    stopper.start()
    time.sleep(0.5)
    release.set()
    call.join(10)
    stopper.join(10)
    assert not stopper.is_alive()
    (answer,) = answers
    assert answer.status_code == 200
    assert answer.json()["result"]["isError"] is False


# --- the calling machine, per call ---


def test_each_tool_call_logs_its_machine(kb_http, caplog):
    caplog.set_level(logging.INFO, logger="knowledge_base.auth")
    kb_http.rpc(*_CALLS[0])
    kb_http.rpc("tools/list")
    kb_http.rpc("tools/call", {"name": "search_index", "arguments": {"query": "q", "mode": "fts"}}, machine=None)
    kb_http.rpc("tools/call", {"name": "search_index", "arguments": {"query": "q"}})
    kb_http.rpc("tools/call", {"name": "search_index", "arguments": {"query": "q", "mode": "fts"}}, machine="desktop")
    records = [r.getMessage() for r in caplog.records if r.name == "knowledge_base.auth"]
    assert records == ["machine laptop called 'search_index'", "machine desktop called 'search_index'"]


@pytest.mark.parametrize("hub", ["mcp", "network"])
def test_a_gate_less_http_app_runs_no_tool(declared_db, stub_embedder, stub_document_embedder, hub):
    from tests.conftest import KbHttp

    bare = KbHttp(lambda: getattr(server, hub).http_app(path="/mcp", stateless_http=True, json_response=True))
    bare.start()
    try:
        arguments = {"name": "a.md", "content": "a note"}
        response = bare.rpc("tools/call", {"name": "ingest_text", "arguments": arguments}, machine=None)
    finally:
        bare.close()
    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True
    assert _chunks() == 0


def test_an_in_memory_call_still_runs(declared_db, caplog):
    from fastmcp import Client

    caplog.set_level(logging.INFO, logger="knowledge_base.auth")

    async def _call():
        async with Client(server.mcp) as client:
            return await client.call_tool("status", {})

    assert asyncio.run(_call()).is_error is False
    assert [r for r in caplog.records if r.name == "knowledge_base.auth"] == []
