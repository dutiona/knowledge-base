"""#604: no embedding input exceeds the served artifact's max_input_tokens, as the server counts it.

``bounded_embedder`` plays llama-server at the httpx transport: ``/tokenize`` counts one token per
whitespace-separated word plus the EOS it appends, and ``/v1/embeddings`` refuses a whole request with
the 400 ``exceed_context_size_error`` as soon as one input counts more than ``LIMIT``.
"""

from __future__ import annotations

import json
import sqlite3

import httpx
import pytest

LIMIT = 64
DIM = 8


def count(text: str) -> int:
    return len(text.split()) + 1


class BoundedEmbedder:
    def __init__(self) -> None:
        self.inputs: list[list[str]] = []
        self.refused = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path == "/tokenize":
            assert body["add_special"] is True
            return httpx.Response(200, json={"tokens": [0] * count(body["content"])})
        self.inputs.append(body["input"])
        over = [n for n in map(count, body["input"]) if n > LIMIT]
        if over:
            self.refused += 1
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": 400,
                        "message": f"request ({over[0]} tokens) exceeds the available context size",
                        "type": "exceed_context_size_error",
                    }
                },
            )
        vectors = [[1.0] + [0.0] * (DIM - 1) for _ in body["input"]]
        return httpx.Response(200, json={"data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)]})


@pytest.fixture
def bounded_embedder(monkeypatch, fake_dns, http_requests) -> BoundedEmbedder:
    for name, value in {
        "KNOWLEDGE_BASE_EMBED_PROVIDER": "llama.cpp",
        "KNOWLEDGE_BASE_EMBED_MODEL": "Qwen/Qwen3-Embedding-0.6B",
        "KNOWLEDGE_BASE_EMBED_DIM": str(DIM),
        "KNOWLEDGE_BASE_EMBED_URL": "http://embedder.example.test",
        "KNOWLEDGE_BASE_EMBED_ARTIFACT_SHA256": "a" * 64,
        "KNOWLEDGE_BASE_EMBED_POOLING": "last",
        "KNOWLEDGE_BASE_EMBED_NORMALIZATION": "l2",
        "KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS": str(LIMIT),
    }.items():
        monkeypatch.setenv(name, value)
    fake_dns["embedder.example.test"] = ["100.64.0.10"]
    stub = BoundedEmbedder()
    http_requests.handlers["embedder.example.test"] = stub
    return stub


@pytest.fixture
def conn(bounded_embedder) -> sqlite3.Connection:
    from knowledge_base.db import open_declared_db

    return open_declared_db()


def test_an_over_length_folder_summary_no_longer_fails_its_batch(conn, bounded_embedder):
    from knowledge_base.folder_summaries import update_folder_summary

    for i in range(3):  # each file contributes 200 characters of two-character words to the summary
        conn.execute(
            "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) VALUES (?, ?, ?, ?, 0)",
            (f"h{i}", "ab " * 100, "markdown", f"/docs/f/file{i}.md"),
        )
    conn.commit()
    summary_tokens = count(" ".join(f"file{i}.md: " + ("ab " * 100)[:200] for i in range(3)))
    assert summary_tokens > LIMIT

    assert update_folder_summary(conn, "/docs/f") is True
    assert conn.execute("SELECT COUNT(*) FROM folder_summaries_vec").fetchone()[0] == 1
    assert bounded_embedder.refused == 0
    ((sent,),) = bounded_embedder.inputs
    assert count(sent) <= LIMIT
