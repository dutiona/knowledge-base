"""The lean HTTP surface (#543): what each surface serves, and the reference that documents both."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from knowledge_base import server
from tests.conftest import machine_token

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


def _count(table: str) -> int:
    from knowledge_base.db import get_connection

    conn = get_connection()
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608 -- a test's own literal
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
    from urllib.parse import urlparse

    from tests.declared_env import TEST_EMBED_URL

    endpoint = urlparse(TEST_EMBED_URL)
    for status in (503, 401):
        stub_document_embedder.down = status
        failed = _call(kb_http, "ingest_text", {"name": "down.md", "content": _DOCUMENT})
        assert failed["isError"] is True
        assert f"HTTP {status}" in _text(failed) and "nothing was stored" in _text(failed)
        assert endpoint.hostname not in _text(failed) and str(endpoint.port) not in _text(failed)
        assert (_count("chunks"), _count("chunk_sessions")) == (0, 0)

    stub_document_embedder.down = None
    assert _call(kb_http, "ingest_text", {"name": "down.md", "content": _DOCUMENT})["isError"] is False
    assert _count("chunks") > 0


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("", "text"),
        ("  ", "text"),
        ("a\nb", "text"),
        ("a‮b", "text"),
        ("a b", "text"),
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
    assert _count("chunks") == 1  # only the winner's passage

    retried = json.loads(_text(_call(kb_http, "ingest_text", {"name": "race.md", "content": _DOCUMENT})))
    assert retried["chunks_skipped"] == 1
    assert retried["chunks_added"] == len(set(chunk_text(_DOCUMENT))) - 1
