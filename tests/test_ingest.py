"""Tests for ingest pipeline (embeddings mocked)."""

import json
from unittest.mock import patch, MagicMock

from research_index.db import EMBED_DIM, get_connection, init_schema
from research_index.ingest import (
    ingest_file,
    reingest_file,
    ingest_url,
    _chunk_text,
    _chunk_python_ast,
)
from research_index.papers import (
    register_paper,
    get_paper,
    add_relationship,
    get_relationships,
)
from research_index.conclusions import record_conclusion, get_conclusions


def _fake_embed(texts, model="nomic-embed-text", expected_dim=None):
    dim = expected_dim if expected_dim is not None else EMBED_DIM
    return [[0.1] * dim for _ in texts]


def test_chunk_text_basic():
    text = "a" * 2000
    chunks = _chunk_text(text, size=1000, overlap=200)
    # 0-1000, 800-1800, 1600-2000 = 3 chunks with 200 overlap
    assert len(chunks) == 3
    assert len(chunks[0]) == 1000


def test_chunk_text_short():
    chunks = _chunk_text("short text", size=1000)
    assert len(chunks) == 1
    assert chunks[0] == "short text"


def test_chunk_text_empty():
    assert _chunk_text("") == []
    assert _chunk_text("   ") == []


@patch("research_index.ingest.embed", _fake_embed)
def test_ingest_markdown_file(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md_file = tmp_path / "test.md"
    md_file.write_text(
        "# Test\n\nSome content about transformers and attention mechanisms.\n"
    )

    result = ingest_file(conn, md_file)
    assert result["chunks_added"] >= 1
    assert result["chunks_skipped"] == 0

    # Verify FTS works on ingested content
    rows = conn.execute(
        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'transformers'"
    ).fetchall()
    assert len(rows) >= 1


@patch("research_index.ingest.embed", _fake_embed)
def test_ingest_dedup(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md_file = tmp_path / "test.md"
    md_file.write_text("Duplicate content test paragraph.\n")

    r1 = ingest_file(conn, md_file)
    r2 = ingest_file(conn, md_file)

    assert r1["chunks_added"] == 1
    assert r2["chunks_added"] == 0
    assert r2["chunks_skipped"] == 1


# --- reingest_file ---


@patch("research_index.ingest.embed", _fake_embed)
def test_reingest_replaces_chunks(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "doc.md"
    md.write_text("Original content about neural networks.\n")
    ingest_file(conn, md)

    old_ids = [r["id"] for r in conn.execute("SELECT id FROM chunks").fetchall()]
    assert len(old_ids) >= 1

    # Modify file and reingest
    md.write_text("Updated content about reinforcement learning.\n")
    result = reingest_file(conn, md)

    assert result["chunks_deleted"] == len(old_ids)
    assert result["chunks_added"] >= 1

    # Old content gone from chunks table
    remaining = conn.execute(
        "SELECT id FROM chunks WHERE id IN ({})".format(",".join("?" * len(old_ids))),
        old_ids,
    ).fetchall()
    assert len(remaining) == 0

    # New content present
    rows = conn.execute("SELECT content FROM chunks").fetchall()
    assert any("reinforcement" in r["content"] for r in rows)


@patch("research_index.ingest.embed", _fake_embed)
def test_reingest_updates_fts(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "doc.md"
    md.write_text("Attention mechanisms in transformers.\n")
    ingest_file(conn, md)

    md.write_text("Convolutional neural networks for vision.\n")
    reingest_file(conn, md)

    # Old term should not match
    old_matches = conn.execute(
        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'transformers'"
    ).fetchall()
    assert len(old_matches) == 0

    # New term should match
    new_matches = conn.execute(
        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'convolutional'"
    ).fetchall()
    assert len(new_matches) >= 1


@patch("research_index.ingest.embed", _fake_embed)
def test_reingest_cleans_vec_table(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "doc.md"
    md.write_text("Vector embedding test content.\n")
    ingest_file(conn, md)

    old_vec_count = conn.execute("SELECT COUNT(*) as c FROM chunks_vec").fetchone()["c"]
    assert old_vec_count >= 1

    md.write_text("Replaced vector content.\n")
    reingest_file(conn, md)

    new_vec_count = conn.execute("SELECT COUNT(*) as c FROM chunks_vec").fetchone()["c"]
    # Should have same number (1 chunk replaced by 1 chunk), not doubled
    assert new_vec_count == old_vec_count


@patch("research_index.ingest.embed", _fake_embed)
def test_reingest_relinks_paper_abstract_chunk(tmp_path):
    """After reingest, abstract_chunk_id should point to the new first chunk."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "paper.md"
    md.write_text("Abstract of the paper about transformers.\n")
    ingest_file(conn, md)

    paper = register_paper(conn, "Test Paper", source_uri=str(md.resolve()))
    old_chunk_id = paper["abstract_chunk_id"]
    assert old_chunk_id is not None

    md.write_text("Completely rewritten paper.\n")
    reingest_file(conn, md)

    # Paper should still exist with abstract_chunk_id pointing to the NEW first chunk
    papers = get_paper(conn, paper_id=paper["paper_id"])
    assert len(papers) == 1
    new_chunk_id = papers[0]["abstract_chunk_id"]
    assert new_chunk_id is not None, (
        "abstract_chunk_id should not be None after reingest"
    )
    assert new_chunk_id != old_chunk_id, "Should point to new chunk, not old one"

    # Verify the new chunk actually exists and belongs to the same source_uri
    chunk = conn.execute(
        "SELECT * FROM chunks WHERE id = ?", (new_chunk_id,)
    ).fetchone()
    assert chunk is not None
    assert chunk["source_uri"] == str(md.resolve())
    assert chunk["chunk_index"] == 0


@patch("research_index.ingest.embed", _fake_embed)
def test_reingest_relinks_multiple_papers_same_source(tmp_path):
    """Multiple papers linked to same source should all get relinked."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "paper.md"
    md.write_text("Shared abstract content.\n")
    ingest_file(conn, md)

    p1 = register_paper(conn, "Paper A", source_uri=str(md.resolve()))
    p2 = register_paper(conn, "Paper B", source_uri=str(md.resolve()))
    assert p1["abstract_chunk_id"] is not None
    assert p2["abstract_chunk_id"] is not None

    md.write_text("Updated shared content.\n")
    reingest_file(conn, md)

    papers_a = get_paper(conn, paper_id=p1["paper_id"])
    papers_b = get_paper(conn, paper_id=p2["paper_id"])
    assert papers_a[0]["abstract_chunk_id"] is not None
    assert papers_b[0]["abstract_chunk_id"] is not None

    # Both should point to the same new first chunk
    assert papers_a[0]["abstract_chunk_id"] == papers_b[0]["abstract_chunk_id"]


@patch("research_index.ingest.embed", _fake_embed)
def test_reingest_does_not_relink_unrelated_papers(tmp_path):
    """Papers linked to a different source should not be affected."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md_a = tmp_path / "a.md"
    md_a.write_text("Content A.\n")
    ingest_file(conn, md_a)

    md_b = tmp_path / "b.md"
    md_b.write_text("Content B.\n")
    ingest_file(conn, md_b)

    paper_a = register_paper(conn, "Paper A", source_uri=str(md_a.resolve()))
    paper_b = register_paper(conn, "Paper B", source_uri=str(md_b.resolve()))
    old_b_chunk = paper_b["abstract_chunk_id"]

    # Reingest only file A
    md_a.write_text("Updated A.\n")
    reingest_file(conn, md_a)

    # Paper B should be untouched
    papers_b = get_paper(conn, paper_id=paper_b["paper_id"])
    assert papers_b[0]["abstract_chunk_id"] == old_b_chunk


@patch("research_index.ingest.embed", _fake_embed)
def test_reingest_nullifies_relationship_evidence(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "evidence.md"
    md.write_text("Paper A extends Paper B by adding attention.\n")
    ingest_file(conn, md)
    chunk_id = conn.execute("SELECT id FROM chunks LIMIT 1").fetchone()["id"]

    p1 = register_paper(conn, "Paper A")["paper_id"]
    p2 = register_paper(conn, "Paper B")["paper_id"]
    add_relationship(conn, p1, p2, "extends", evidence_chunk_id=chunk_id)

    md.write_text("New evidence content.\n")
    reingest_file(conn, md)

    rels = get_relationships(conn, p1)
    assert len(rels) == 1
    assert rels[0]["evidence_chunk_id"] is None


@patch("research_index.ingest.embed", _fake_embed)
def test_reingest_cleans_conclusion_chunk_refs(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "source.md"
    md.write_text("Evidence for a conclusion.\n")
    ingest_file(conn, md)
    chunk_id = conn.execute("SELECT id FROM chunks LIMIT 1").fetchone()["id"]

    record_conclusion(conn, "Test claim", 0.9, [chunk_id])

    md.write_text("Different evidence.\n")
    reingest_file(conn, md)

    conclusions = get_conclusions(conn)
    assert len(conclusions) == 1
    # The deleted chunk_id should be removed from source_chunk_ids
    assert chunk_id not in conclusions[0]["source_chunk_ids"]


@patch("research_index.ingest.embed", _fake_embed)
def test_reingest_nonexistent_source(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "never_ingested.md"
    md.write_text("Some content.\n")

    result = reingest_file(conn, md)
    assert result["error"] is not None


@patch("research_index.ingest.embed", _fake_embed)
def test_reingest_idempotent_content(tmp_path):
    """Reingest same content = delete old + insert new (same result)."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "doc.md"
    md.write_text("Stable content that does not change.\n")
    r1 = ingest_file(conn, md)

    r2 = reingest_file(conn, md)
    assert r2["chunks_deleted"] == r1["chunks_added"]
    assert r2["chunks_added"] == r1["chunks_added"]

    total = conn.execute("SELECT COUNT(*) as c FROM chunks").fetchone()["c"]
    assert total == r1["chunks_added"]


# --- reingest batching (issue #40) ---


@patch("research_index.ingest.embed", _fake_embed)
@patch("research_index.ingest._SQL_BATCH_SIZE", 2)
def test_reingest_batches_in_clauses(tmp_path):
    """reingest_file works when old chunk count exceeds _SQL_BATCH_SIZE.

    With batch size 2, a file producing 4 chunks forces multi-batch IN clauses
    for every FK cleanup and deletion step.
    """
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Create a file that produces exactly 4 chunks (each paragraph is a chunk)
    paragraphs = [f"Paragraph {i} " + "word " * 200 for i in range(4)]
    md = tmp_path / "big.md"
    md.write_text("\n\n".join(paragraphs))
    ingest_file(conn, md)

    old_ids = [r["id"] for r in conn.execute("SELECT id FROM chunks").fetchall()]
    assert len(old_ids) >= 3, f"Need >=3 chunks to test batching, got {len(old_ids)}"

    # Set up FK references that span multiple batches
    source_uri = str(md.resolve())
    paper = register_paper(conn, "Batched Paper", source_uri=source_uri)
    assert paper["abstract_chunk_id"] is not None

    # Add relationship evidence pointing to one of the old chunks
    p2 = register_paper(conn, "Other Paper")
    add_relationship(
        conn,
        paper["paper_id"],
        p2["paper_id"],
        "cites",
        evidence_chunk_id=old_ids[1],
    )

    # Add conclusion referencing multiple old chunks
    record_conclusion(
        conn,
        claim="Test claim",
        source_chunk_ids=old_ids[:3],
    )

    # Add method/dataset/metric referencing old chunks
    pid = paper["paper_id"]
    conn.execute(
        "INSERT INTO methods (name, chunk_id, paper_id) VALUES (?, ?, ?)",
        ("test_method", old_ids[0], pid),
    )
    conn.execute(
        "INSERT INTO datasets (name, chunk_id, paper_id) VALUES (?, ?, ?)",
        ("test_dataset", old_ids[1], pid),
    )
    conn.execute(
        "INSERT INTO metrics (name, value, chunk_id, paper_id) VALUES (?, ?, ?, ?)",
        ("test_metric", 0.95, old_ids[2], pid),
    )
    conn.commit()

    # Reingest with batch_size=2 (monkeypatched above)
    new_paragraphs = [f"New paragraph {i} " + "text " * 200 for i in range(4)]
    md.write_text("\n\n".join(new_paragraphs))
    result = reingest_file(conn, md)

    assert result["chunks_deleted"] == len(old_ids)
    assert result["chunks_added"] >= 3

    # All old chunks should be gone
    remaining = conn.execute(
        "SELECT id FROM chunks WHERE id IN ({})".format(",".join("?" * len(old_ids))),
        old_ids,
    ).fetchall()
    assert len(remaining) == 0

    # Paper should be re-linked to new first chunk
    papers = get_paper(conn, paper_id=paper["paper_id"])
    assert papers[0]["abstract_chunk_id"] is not None
    new_chunk = conn.execute(
        "SELECT * FROM chunks WHERE id = ?", (papers[0]["abstract_chunk_id"],)
    ).fetchone()
    assert new_chunk["chunk_index"] == 0
    assert new_chunk["source_uri"] == source_uri

    # Relationship evidence should be nullified
    rels = get_relationships(conn, paper["paper_id"])
    assert rels[0]["evidence_chunk_id"] is None

    # Conclusion chunk refs should have old IDs removed
    conclusions = get_conclusions(conn)
    chunk_ids = conclusions[0]["source_chunk_ids"]
    if isinstance(chunk_ids, str):
        chunk_ids = json.loads(chunk_ids)
    for old_id in old_ids[:3]:
        assert old_id not in chunk_ids

    # Methods/datasets/metrics should be nullified
    assert (
        conn.execute(
            "SELECT chunk_id FROM methods WHERE name='test_method'"
        ).fetchone()["chunk_id"]
        is None
    )
    assert (
        conn.execute(
            "SELECT chunk_id FROM datasets WHERE name='test_dataset'"
        ).fetchone()["chunk_id"]
        is None
    )
    assert (
        conn.execute(
            "SELECT chunk_id FROM metrics WHERE name='test_metric'"
        ).fetchone()["chunk_id"]
        is None
    )

    # Vec table should only have new chunks
    vec_count = conn.execute("SELECT COUNT(*) as c FROM chunks_vec").fetchone()["c"]
    assert vec_count == result["chunks_added"]


# --- ingest_url ---

FAKE_HTML = """
<html><head><title>Test Page</title></head>
<body>
<article>
<h1>Attention Is All You Need</h1>
<p>The dominant sequence transduction models are based on complex recurrent or
convolutional neural networks that include an encoder and a decoder.</p>
</article>
</body></html>
"""


def _mock_httpx_get(url, **kwargs):
    resp = MagicMock()
    resp.status_code = 200
    resp.text = FAKE_HTML
    resp.raise_for_status = MagicMock()
    return resp


@patch("research_index.ingest.embed", _fake_embed)
@patch("research_index.ingest.httpx.get", _mock_httpx_get)
def test_ingest_url_basic(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    result = ingest_url(conn, "https://example.com/paper")
    assert result["chunks_added"] >= 1
    assert result["source_uri"] == "https://example.com/paper"
    assert result["source_type"] == "web"

    # Check chunk is stored with correct source_uri
    rows = conn.execute("SELECT source_uri, source_type FROM chunks").fetchall()
    assert all(r["source_uri"] == "https://example.com/paper" for r in rows)
    assert all(r["source_type"] == "web" for r in rows)


@patch("research_index.ingest.embed", _fake_embed)
@patch("research_index.ingest.httpx.get", _mock_httpx_get)
def test_ingest_url_dedup(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    r1 = ingest_url(conn, "https://example.com/paper")
    r2 = ingest_url(conn, "https://example.com/paper")

    assert r1["chunks_added"] >= 1
    assert r2["chunks_added"] == 0
    assert r2["chunks_skipped"] >= 1


@patch("research_index.ingest.embed", _fake_embed)
@patch("research_index.ingest.httpx.get", _mock_httpx_get)
def test_ingest_url_stores_title_in_metadata(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    ingest_url(conn, "https://example.com/paper")

    row = conn.execute("SELECT metadata FROM chunks LIMIT 1").fetchone()
    meta = json.loads(row["metadata"])
    assert "title" in meta


@patch("research_index.ingest.embed", _fake_embed)
def test_ingest_url_http_error(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    def _mock_get_error(url, **kwargs):
        import httpx

        raise httpx.HTTPError("Connection refused")

    with patch("research_index.ingest.httpx.get", _mock_get_error):
        result = ingest_url(conn, "https://example.com/down")
    assert "error" in result


def test_ingest_url_rejects_non_http_schemes(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    result = ingest_url(conn, "file:///etc/passwd")
    assert "error" in result

    result = ingest_url(conn, "ftp://internal/data")
    assert "error" in result


@patch("research_index.ingest.embed", _fake_embed)
def test_ingest_url_no_content(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    def _mock_get_empty(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "<html><body></body></html>"
        resp.raise_for_status = MagicMock()
        return resp

    with patch("research_index.ingest.httpx.get", _mock_get_empty):
        result = ingest_url(conn, "https://example.com/empty")
    assert result["chunks_added"] == 0


# --- AST-aware code chunking ---

SAMPLE_PYTHON = '''\
"""Module docstring."""

import os

CONSTANT = 42


def helper(x):
    """A helper function."""
    return x + 1


class MyModel:
    """A model class."""

    def __init__(self, dim):
        self.dim = dim

    def forward(self, x):
        return x * self.dim


def train():
    model = MyModel(10)
    return model.forward(5)
'''


def test_chunk_python_ast_splits_by_symbol():
    chunks = _chunk_python_ast(SAMPLE_PYTHON)
    # Should have: module-level block (imports + CONSTANT), helper, MyModel, train
    assert len(chunks) >= 3

    texts = [c["text"] for c in chunks]
    # Each function/class should be its own chunk
    assert any("def helper" in t for t in texts)
    assert any("class MyModel" in t for t in texts)
    assert any("def train" in t for t in texts)


def test_chunk_python_ast_metadata():
    chunks = _chunk_python_ast(SAMPLE_PYTHON)
    for chunk in chunks:
        assert "name" in chunk
        assert "type" in chunk
        assert "start_line" in chunk
        assert "end_line" in chunk
        assert chunk["start_line"] <= chunk["end_line"]

    # Find the helper function chunk
    helper_chunk = next(c for c in chunks if c["name"] == "helper")
    assert helper_chunk["type"] == "function"

    # Find the class chunk
    model_chunk = next(c for c in chunks if c["name"] == "MyModel")
    assert model_chunk["type"] == "class"


def test_chunk_python_ast_module_level():
    """Module-level code (imports, constants) should be captured."""
    chunks = _chunk_python_ast(SAMPLE_PYTHON)
    module_chunk = next(c for c in chunks if c["type"] == "module")
    assert "import os" in module_chunk["text"]
    assert "CONSTANT = 42" in module_chunk["text"]


@patch("research_index.ingest.embed", _fake_embed)
def test_ingest_python_file_uses_ast(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    py_file = tmp_path / "model.py"
    py_file.write_text(SAMPLE_PYTHON)
    result = ingest_file(conn, py_file)

    assert result["chunks_added"] >= 3

    # Check metadata is stored
    rows = conn.execute(
        "SELECT metadata FROM chunks WHERE source_type = 'code'"
    ).fetchall()
    for row in rows:
        meta = json.loads(row["metadata"])
        assert "name" in meta
        assert "type" in meta


def test_chunk_python_ast_syntax_error():
    """Invalid Python should fall back gracefully."""
    chunks = _chunk_python_ast("def broken(:\n    pass\n")
    # Should return empty list (caller falls back to fixed-size)
    assert chunks == []


def test_chunk_python_ast_empty():
    chunks = _chunk_python_ast("")
    assert chunks == []
