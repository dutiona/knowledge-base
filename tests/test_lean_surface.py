"""The lean HTTP surface (#543): what each surface serves, and the reference that documents both."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from knowledge_base import server

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
    from urllib.parse import urlparse

    from tests.declared_env import TEST_EMBED_URL

    stub_embedder.down = 503
    result = _call(kb_http, "search_index", {"query": "attention"})
    endpoint = urlparse(TEST_EMBED_URL)
    assert result["isError"] is True
    assert "HTTP 503" in _text(result)
    assert endpoint.hostname not in _text(result) and str(endpoint.port) not in _text(result)

    assert _call(kb_http, "search_index", {"query": "attention", "mode": "fts"})["isError"] is False
