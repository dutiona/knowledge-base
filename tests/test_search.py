"""Tests for hybrid search (embeddings mocked)."""

import json
from unittest.mock import patch

import pytest

from knowledge_base.db import DEFAULT_EMBED_DIM, get_connection, init_schema
from knowledge_base.exceptions import ValidationError
from knowledge_base.ingest import ingest_file
from knowledge_base.routes.search import search_index
from knowledge_base.search import search, _rrf_merge


def _fake_embed(texts, model="bge-m3", expected_dim=None, **_kwargs):
    dim = expected_dim if expected_dim is not None else DEFAULT_EMBED_DIM
    return [[0.1] * dim for _ in texts]


def _fake_embed_single(text, model="bge-m3", **_kwargs):
    return [0.1] * DEFAULT_EMBED_DIM


def test_rrf_merge():
    fts = [(1, -2.0), (2, -1.5), (3, -1.0)]
    vec = [(2, 0.1), (4, 0.2), (1, 0.3)]

    merged = _rrf_merge(fts, vec)
    ids = [cid for cid, _ in merged]

    # IDs 1 and 2 appear in both lists, so they should rank highest
    assert 1 in ids[:2]
    assert 2 in ids[:2]


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", _fake_embed_single)
def test_search_fts_mode(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "paper.md"
    md.write_text("Attention mechanisms in neural networks enable selective focus.\n")
    ingest_file(conn, md)

    results = search(conn, "attention", mode="fts")
    assert len(results) >= 1
    assert "attention" in results[0].content.lower()


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", _fake_embed_single)
def test_search_vec_mode(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "paper.md"
    md.write_text("Attention mechanisms in neural networks enable selective focus.\n")
    ingest_file(conn, md)

    results = search(conn, "focus models", mode="vec")
    assert len(results) >= 1


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", _fake_embed_single)
def test_search_source_type_filter(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "notes.md"
    md.write_text("Some notes about transformers.\n")
    ingest_file(conn, md)

    # Filter by non-existent type
    results = search(conn, "transformers", source_type="pdf", mode="fts")
    assert len(results) == 0

    # Filter by correct type
    results = search(conn, "transformers", source_type="markdown", mode="fts")
    assert len(results) >= 1


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", _fake_embed_single)
def test_search_hybrid_uses_keyword_prefilter(tmp_path):
    """Hybrid search with keyword_prefilter=True extracts intent keywords for FTS."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "paper.md"
    md.write_text("Rust error handling in async code uses Result types and the ? operator.\n")
    ingest_file(conn, md)

    # With keyword prefilter, a verbose query should still find the doc
    results = search(
        conn,
        "What are the best practices for Rust error handling in async code?",
        mode="hybrid",
        keyword_prefilter=True,
    )
    assert len(results) >= 1
    assert "rust" in results[0].content.lower()


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", _fake_embed_single)
def test_search_keyword_prefilter_stopword_only_query(tmp_path):
    """When keyword_prefilter=True and query is all stopwords, FTS leg is skipped gracefully."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "doc.md"
    md.write_text("Some content about neural networks.\n")
    ingest_file(conn, md)

    # All-stopword query: FTS leg produces nothing, vec leg still works in hybrid
    results = search(
        conn,
        "what is the",
        mode="hybrid",
        keyword_prefilter=True,
    )
    # Should not crash — vec leg provides results even if FTS leg is empty
    assert isinstance(results, list)


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", _fake_embed_single)
def test_search_fts_with_keyword_prefilter(tmp_path):
    """FTS-only mode also respects keyword_prefilter."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "doc.md"
    md.write_text("Neural network architectures for image classification tasks.\n")
    ingest_file(conn, md)

    results = search(
        conn,
        "What neural network is best for image classification?",
        mode="fts",
        keyword_prefilter=True,
    )
    assert len(results) >= 1


# --- chunk_strategy filter tests ---


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", _fake_embed_single)
def test_search_no_strategy_filter_by_default(tmp_path):
    """Default search (no chunk_strategy) returns all chunks regardless of strategy."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Insert chunks with different strategies
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, chunk_strategy) "
        "VALUES ('mech1', 'mechanical attention mechanism', 'pdf', '/tmp/a.pdf', 0, 'mechanical')"
    )
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, chunk_strategy) "
        "VALUES ('sem1', 'semantic attention mechanism', 'pdf', '/tmp/b.pdf', 0, 'semantic')"
    )
    conn.commit()

    # Default: no strategy filter — both chunks visible (vec leg filters
    # implicitly via active space table, but FTS leg returns all)
    results = search(conn, "attention", mode="fts")
    assert len(results) == 2


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", _fake_embed_single)
def test_search_explicit_strategy_filter(tmp_path):
    """chunk_strategy='semantic' returns only semantic chunks."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, chunk_strategy) "
        "VALUES ('mech2', 'mechanical transformer model', 'pdf', '/tmp/a.pdf', 0, 'mechanical')"
    )
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, chunk_strategy) "
        "VALUES ('sem2', 'semantic transformer model', 'pdf', '/tmp/b.pdf', 0, 'semantic')"
    )
    conn.commit()

    results = search(conn, "transformer", mode="fts", chunk_strategy="semantic")
    assert len(results) == 1
    assert results[0].content.startswith("semantic")


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", _fake_embed_single)
def test_search_explicit_strategy_filter_both_directions(tmp_path):
    """Explicit chunk_strategy filters in both directions."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, chunk_strategy) "
        "VALUES ('md1', 'markdown deep learning notes', 'markdown', '/tmp/notes.md', 0, 'mechanical')"
    )
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, chunk_strategy) "
        "VALUES ('pdf1', 'semantic deep learning paper', 'pdf', '/tmp/paper.pdf', 0, 'semantic')"
    )
    conn.commit()

    # No filter — both visible
    all_results = search(conn, "deep learning", mode="fts")
    assert len(all_results) == 2

    # Explicit semantic filter
    sem_results = search(conn, "deep learning", mode="fts", chunk_strategy="semantic")
    assert len(sem_results) == 1
    assert sem_results[0].source_type == "pdf"

    # Explicit mechanical filter
    mech_results = search(conn, "deep learning", mode="fts", chunk_strategy="mechanical")
    assert len(mech_results) == 1
    assert mech_results[0].source_type == "markdown"


# --- Input validation (issue #188) ---


@pytest.mark.parametrize(
    "param, value, match",
    [
        ("mode", "invalid", "mode"),
        ("top_k", -1, "top_k"),
        ("top_k", 0, "top_k"),
        ("top_k", 501, "top_k"),
        ("source_type", "invalid_type", "source_type"),
        ("chunk_strategy", "invalid_strategy", "chunk_strategy"),
    ],
)
def test_search_input_validation(tmp_path, param, value, match):
    """search() should reject invalid parameter values."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    with pytest.raises(ValidationError, match=match):
        search(conn, "test", **{param: value})


def test_search_index_tool_returns_json_error(tmp_path):
    """search_index tool should return a JSON error instead of crashing on ValidationError."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    with patch("knowledge_base.routes.search._get_conn", return_value=conn):
        response_str = search_index("test", mode="invalid")
        response = json.loads(response_str)
        assert "error" in response
        assert "mode must be one of" in response["error"]
