"""Tests for hybrid search (embeddings mocked)."""

from unittest.mock import patch

from knowledge_base.db import DEFAULT_EMBED_DIM, get_connection, init_schema
from knowledge_base.ingest import ingest_file
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
