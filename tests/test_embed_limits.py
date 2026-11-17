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


#: One section of 3 x LIMIT distinct words, under the 1,000 characters every chunker keeps as one chunk.
WORDS = [f"w{i}" for i in range(3 * LIMIT)]
SECTION = " ".join(WORDS)


def _stored(conn: sqlite3.Connection, source_uri: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT content, metadata FROM chunks WHERE source_uri = ? ORDER BY chunk_index", (source_uri,)
    ).fetchall()


def _assert_split(conn: sqlite3.Connection, embedder: BoundedEmbedder, source_uri: str, text: str) -> list:
    rows = _stored(conn, source_uri)
    assert embedder.refused == 0
    assert len(rows) > 1
    assert all(count(row["content"]) <= LIMIT for row in rows)
    assert "".join(row["content"] for row in rows) == text
    return [json.loads(row["metadata"]) for row in rows]


def test_an_over_length_markdown_section_is_split_not_failed(conn, bounded_embedder, tmp_path):
    from knowledge_base.ingest import ingest_file

    path = tmp_path / "notes.md"
    path.write_text(SECTION)
    ingest_file(conn, path)
    _assert_split(conn, bounded_embedder, path.resolve().as_posix(), SECTION)


def test_an_over_length_semantic_pdf_section_is_split_with_each_piece_s_own_images(
    conn, bounded_embedder, tmp_path, monkeypatch
):
    from knowledge_base import ingest

    words = [*WORDS[: LIMIT * 2], "![](fig.png)", *WORDS[LIMIT * 2 :]]
    text = "## Methods\n\n" + " ".join(words)
    monkeypatch.setattr(ingest, "_extract_pdf_markdown", lambda _path, image_dir=None: (text, {0: 1}))
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('chunk_strategy', 'semantic')")
    conn.commit()
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.7 not parsed: the extractor is patched")
    ingest.ingest_file(conn, path)
    metas = _assert_split(conn, bounded_embedder, path.resolve().as_posix(), text)
    assert [meta.get("images") for meta in metas].count(["fig.png"]) == 1
    assert sum("images" in meta for meta in metas) == 1
    assert all(meta["pages"] == [1] for meta in metas)


def test_an_image_reference_cut_by_the_split_still_lists_its_image(conn, bounded_embedder, tmp_path, monkeypatch):
    from knowledge_base import ingest

    caption = " ".join(f"c{i}" for i in range(LIMIT + 16))  # longer than a piece, so a cut falls inside it
    words = [*WORDS[: LIMIT - 5], f"![{caption}](fig.png)", *WORDS[LIMIT - 5 :]]
    text = "## Methods\n\n" + " ".join(words)
    monkeypatch.setattr(ingest, "_extract_pdf_markdown", lambda _path, image_dir=None: (text, {0: 1}))
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('chunk_strategy', 'semantic')")
    conn.commit()
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.7 not parsed: the extractor is patched")
    ingest.ingest_file(conn, path)
    metas = _assert_split(conn, bounded_embedder, path.resolve().as_posix(), text)
    rows = _stored(conn, path.resolve().as_posix())
    assert not any("![" in row["content"] and "](fig.png)" in row["content"] for row in rows)
    assert [meta.get("images") for meta in metas].count(["fig.png"]) == 1


def test_an_image_reference_on_its_own_line_is_never_cut(conn, bounded_embedder, tmp_path, monkeypatch):
    from knowledge_base import ingest

    caption = " ".join(f"c{i}" for i in range(10))
    text = "## Methods\n\n" + " ".join(WORDS[: LIMIT - 5]) + f"\n![{caption}](fig.png)\n" + " ".join(WORDS[LIMIT:])
    monkeypatch.setattr(ingest, "_extract_pdf_markdown", lambda _path, image_dir=None: (text, {0: 1}))
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('chunk_strategy', 'semantic')")
    conn.commit()
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.7 not parsed: the extractor is patched")
    ingest.ingest_file(conn, path)
    _assert_split(conn, bounded_embedder, path.resolve().as_posix(), text)
    assert any(f"![{caption}](fig.png)" in row["content"] for row in _stored(conn, path.resolve().as_posix()))


def test_an_over_length_python_function_is_split_into_named_pieces(conn, bounded_embedder, tmp_path):
    from knowledge_base.ingest import ingest_file

    source = f'def f():\n    s = "{SECTION}"\n    return s'
    path = tmp_path / "mod.py"
    path.write_text(source + "\n")
    ingest_file(conn, path)
    metas = _assert_split(conn, bounded_embedder, path.resolve().as_posix(), source)
    assert [meta["name"] for meta in metas] == [f"f[{k}]" for k in range(len(metas))]
    assert {(meta["start_line"], meta["end_line"]) for meta in metas} == {(1, 3)}


def test_an_over_length_stored_text_is_split_not_failed(conn, bounded_embedder):
    from knowledge_base.ingest import ingest_text

    ingest_text(conn, SECTION, source_uri="/docs/stored.md", source_type="markdown")
    _assert_split(conn, bounded_embedder, "/docs/stored.md", SECTION)


def test_a_space_recording_another_limit_is_refused_before_any_request(conn, bounded_embedder, http_requests):
    """A space's limit can only be the declaration's: one recording another is never embedded into (#573's check)."""
    from knowledge_base.embed_swap import backfill_space, create_space
    from knowledge_base.exceptions import EmbeddingModelMismatchError

    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) VALUES ('h', ?, ?, ?, 0)",
        (SECTION, "markdown", "/docs/a.md"),
    )
    conn.commit()
    create_space(conn, "alt", "Qwen/Qwen3-Embedding-0.6B", DIM)
    conn.execute("UPDATE embed_spaces SET max_input_tokens = ? WHERE name = 'alt'", (LIMIT * 2,))
    conn.commit()
    with pytest.raises(EmbeddingModelMismatchError, match="max_input_tokens"):
        backfill_space(conn, "alt")
    assert http_requests == []


def test_re_ingesting_an_unchanged_file_counts_nothing(conn, bounded_embedder, http_requests, tmp_path):
    from knowledge_base.ingest import ingest_file

    path = tmp_path / "short.md"
    path.write_text("A short note that fits.")
    ingest_file(conn, path)
    before = len(http_requests)
    assert ingest_file(conn, path)["chunks_skipped"] == 1
    assert [r.url.path for r in http_requests[before:]] == []


def test_a_piece_the_limit_split_cuts_is_still_a_redaction_fixpoint(conn, bounded_embedder, monkeypatch):
    """#592: the split can cut mid-word, which a redaction rule reads as a boundary: each stored piece is scrubbed."""
    from knowledge_base import ingest
    from knowledge_base.redact import scrub

    monkeypatch.setattr(ingest, "split_inputs", lambda texts, cfg: [[t[:400], t[400:]] for t in texts])
    text = "x" * 399 + "_me_" + "0" * 32 + " plain words"
    ingest.ingest_text(conn, text, source_uri="/docs/cut.md", source_type="markdown")
    contents = [r[0] for r in conn.execute("SELECT content FROM chunks WHERE source_uri = '/docs/cut.md'")]
    assert len(contents) == 2
    assert all(c == scrub(c) for c in contents)


def test_re_ingesting_a_file_whose_chunk_redaction_changed_counts_nothing(
    conn, bounded_embedder, http_requests, tmp_path
):
    """#592 with #604: the split's stored-chunk lookup hashes the text that is stored, the scrubbed one."""
    from knowledge_base.ingest import ingest_file

    path = tmp_path / "edge.md"
    # The second 1000-character window starts at "me_", which the per-chunk scrub then redacts.
    path.write_text("x" * 799 + "_me_" + "0" * 32 + " " + "word " * 40)  # each chunk fits the limit
    ingest_file(conn, path)
    before = len(http_requests)
    assert ingest_file(conn, path)["chunks_added"] == 0
    assert [r.url.path for r in http_requests[before:]] == []
