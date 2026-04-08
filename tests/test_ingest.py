"""Tests for ingest pipeline (embeddings mocked)."""

import json
import socket
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

from knowledge_base.chunking import chunk_python_ast as _chunk_python_ast
from knowledge_base.chunking import chunk_text as _chunk_text
from knowledge_base.db import DEFAULT_EMBED_DIM, get_connection, init_schema
from knowledge_base.ingest import (
    _cleanup_conclusion_refs,
    _extract_pdf_markdown,
    ingest_directory,
    ingest_file,
    pdf_image_dir,
    reingest_file,
)
from knowledge_base.utils import is_private_ip
from knowledge_base.web import (
    _cleanup_stale_inline_images,
    _extract_html_images,
    _extract_web_figures,
    _get_browser_config,
    _parse_image_candidates,
    _parse_srcset,
    _render_with_browser,
    _validate_image_url,
    configure_browser,
    ingest_url,
)
from knowledge_base.papers import (
    register_paper,
    get_paper,
    add_relationship,
    get_relationships,
)
from knowledge_base.conclusions import (
    record_conclusion,
    get_conclusions,
    supersede_conclusion,
)
from knowledge_base.exceptions import NotFoundError, ValidationError
from knowledge_base.extraction import record_method, record_dataset, record_metric
import pytest


def _fake_embed(texts, model="bge-m3", expected_dim=None, **_kwargs):
    dim = expected_dim if expected_dim is not None else DEFAULT_EMBED_DIM
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


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
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


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_ingest_file_result_uses_forward_slashes(tmp_path):
    """ingest_file return dict 'file' key must use forward slashes (#158)."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md_file = tmp_path / "test.md"
    md_file.write_text("Content.\n")
    result = ingest_file(conn, md_file)

    assert "\\" not in result["file"], f"Backslash in result: {result['file']}"
    assert result["file"] == md_file.as_posix()


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
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


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_reingest_updates_paper_paths_hash(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "paper.md"
    md.write_text("Original content.\n")
    ingest_file(conn, md)
    source_uri = str(md.resolve())

    register_paper(conn, "Test", source_uri=source_uri)

    old_hash = conn.execute(
        "SELECT content_hash FROM paper_paths WHERE path = ?", (source_uri,)
    ).fetchone()["content_hash"]

    md.write_text("Updated content.\n")
    reingest_file(conn, md)

    new_hash = conn.execute(
        "SELECT content_hash FROM paper_paths WHERE path = ?", (source_uri,)
    ).fetchone()["content_hash"]

    assert new_hash != old_hash
    assert new_hash is not None


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
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


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
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


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
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


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
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


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
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


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
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

    register_paper(conn, "Paper A", source_uri=str(md_a.resolve()))
    paper_b = register_paper(conn, "Paper B", source_uri=str(md_b.resolve()))
    old_b_chunk = paper_b["abstract_chunk_id"]

    # Reingest only file A
    md_a.write_text("Updated A.\n")
    reingest_file(conn, md_a)

    # Paper B should be untouched
    papers_b = get_paper(conn, paper_id=paper_b["paper_id"])
    assert papers_b[0]["abstract_chunk_id"] == old_b_chunk


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
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


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_reingest_deletes_zombie_conclusion_with_empty_chunk_refs(tmp_path):
    """Conclusion whose *all* source_chunk_ids are removed should be deleted (#160)."""
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
    assert len(conclusions) == 0, "Zombie conclusion with no evidence should be deleted"


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_reingest_keeps_conclusion_with_remaining_chunk_refs(tmp_path):
    """Conclusion with some surviving source_chunk_ids should be kept (#160)."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Ingest two separate files so we get chunk_ids from different sources
    md1 = tmp_path / "evidence1.md"
    md1.write_text("First piece of evidence.\n")
    ingest_file(conn, md1)
    chunk_id_1 = conn.execute(
        "SELECT id FROM chunks WHERE source_uri = ?", (str(md1.resolve()),)
    ).fetchone()["id"]

    md2 = tmp_path / "evidence2.md"
    md2.write_text("Second piece of evidence.\n")
    ingest_file(conn, md2)
    chunk_id_2 = conn.execute(
        "SELECT id FROM chunks WHERE source_uri = ?", (str(md2.resolve()),)
    ).fetchone()["id"]

    record_conclusion(conn, "Multi-evidence claim", 0.9, [chunk_id_1, chunk_id_2])

    # Reingest only md1 — chunk_id_1 is replaced, chunk_id_2 survives
    md1.write_text("Rewritten evidence.\n")
    reingest_file(conn, md1)

    conclusions = get_conclusions(conn)
    assert len(conclusions) == 1, "Conclusion with surviving evidence should be kept"
    assert chunk_id_1 not in conclusions[0]["source_chunk_ids"]
    assert chunk_id_2 in conclusions[0]["source_chunk_ids"]


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_reingest_zombie_deletion_clears_superseded_by_fk(tmp_path):
    """Deleting a zombie must not violate superseded_by FK constraint (#160)."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "source.md"
    md.write_text("Evidence for original claim.\n")
    ingest_file(conn, md)
    chunk_id = conn.execute("SELECT id FROM chunks LIMIT 1").fetchone()["id"]

    # v1 → superseded by v2 (both reference the same chunk)
    v1 = record_conclusion(conn, "Original claim", 0.9, [chunk_id])
    supersede_conclusion(conn, v1["conclusion_id"], "Revised claim", 0.95, [chunk_id])

    # Reingest deletes the chunk — both conclusions lose all evidence
    md.write_text("Completely different content.\n")
    reingest_file(conn, md)

    # Both zombies should be deleted without IntegrityError
    conclusions = get_conclusions(conn, include_superseded=True)
    assert len(conclusions) == 0, "Both zombie conclusions should be deleted"


def test_cleanup_conclusion_refs_only_touches_affected_rows(tmp_path):
    """_cleanup_conclusion_refs must not modify conclusions unrelated to the deleted IDs (#277)."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Two conclusions: one references chunk 10, the other references chunk 20
    conn.execute(
        "INSERT INTO conclusions (claim, source_chunk_ids) VALUES (?, ?)",
        ("Affected claim", json.dumps([10, 20])),
    )
    conn.execute(
        "INSERT INTO conclusions (claim, source_chunk_ids) VALUES (?, ?)",
        ("Unrelated claim", json.dumps([30, 40])),
    )

    _cleanup_conclusion_refs(conn, [10])

    rows = conn.execute(
        "SELECT claim, source_chunk_ids FROM conclusions ORDER BY claim"
    ).fetchall()
    assert len(rows) == 2
    assert json.loads(rows[0]["source_chunk_ids"]) == [20]  # 10 removed
    assert json.loads(rows[1]["source_chunk_ids"]) == [30, 40]  # untouched


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_reingest_relinks_method_by_name_search(tmp_path):
    """After reingest, method.chunk_id should point to the new chunk containing the method name."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Create a multi-chunk document where "TransformerXL" appears in chunk 1 (not chunk 0)
    # Each chunk needs to be large enough to be its own chunk
    chunk0_text = "Introduction. " * 80  # ~1100 chars, no method name
    chunk1_text = (
        "We propose TransformerXL for sequence modeling. " * 30
    )  # has the method name
    md = tmp_path / "paper.md"
    md.write_text(chunk0_text + chunk1_text)
    ingest_file(conn, md)

    # Verify we got multiple chunks
    chunks = conn.execute(
        "SELECT id, chunk_index, content FROM chunks WHERE source_uri = ? ORDER BY chunk_index",
        (str(md.resolve()),),
    ).fetchall()
    assert len(chunks) >= 2, f"Expected >=2 chunks, got {len(chunks)}"

    # Find the chunk that contains "TransformerXL"
    method_chunk = next(c for c in chunks if "TransformerXL" in c["content"])
    old_chunk_id = method_chunk["id"]

    # Register a paper and a method linked to that chunk
    paper = register_paper(conn, "Test Paper", source_uri=str(md.resolve()))
    record_method(conn, "TransformerXL", paper["paper_id"], "A method", old_chunk_id)

    # Reingest with slightly different content but same method name still in a non-first chunk
    chunk0_text_new = "Revised introduction. " * 80
    chunk1_text_new = "We introduce TransformerXL as an improved architecture. " * 30
    md.write_text(chunk0_text_new + chunk1_text_new)
    reingest_file(conn, md)

    # Method should be re-linked to the new chunk containing "TransformerXL"
    row = conn.execute(
        "SELECT chunk_id FROM methods WHERE paper_id = ? AND name = 'TransformerXL'",
        (paper["paper_id"],),
    ).fetchone()
    assert row["chunk_id"] is not None, (
        "method chunk_id should not be None after reingest"
    )
    assert row["chunk_id"] != old_chunk_id, "Should point to new chunk, not old one"

    # Verify the new chunk contains the method name
    new_chunk = conn.execute(
        "SELECT content FROM chunks WHERE id = ?", (row["chunk_id"],)
    ).fetchone()
    assert "TransformerXL" in new_chunk["content"]


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_reingest_relinks_dataset_by_name_search(tmp_path):
    """After reingest, dataset.chunk_id should point to the new chunk containing the dataset name."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    chunk0_text = "Background section. " * 80
    chunk1_text = "We evaluate on ImageNet-1K benchmark dataset. " * 30
    md = tmp_path / "paper.md"
    md.write_text(chunk0_text + chunk1_text)
    ingest_file(conn, md)

    chunks = conn.execute(
        "SELECT id, chunk_index, content FROM chunks WHERE source_uri = ? ORDER BY chunk_index",
        (str(md.resolve()),),
    ).fetchall()
    assert len(chunks) >= 2

    dataset_chunk = next(c for c in chunks if "ImageNet-1K" in c["content"])
    old_chunk_id = dataset_chunk["id"]

    paper = register_paper(conn, "Test Paper", source_uri=str(md.resolve()))
    record_dataset(conn, "ImageNet-1K", paper["paper_id"], "A dataset", old_chunk_id)

    chunk0_new = "Updated background. " * 80
    chunk1_new = "Results on ImageNet-1K show improvements. " * 30
    md.write_text(chunk0_new + chunk1_new)
    reingest_file(conn, md)

    row = conn.execute(
        "SELECT chunk_id FROM datasets WHERE paper_id = ? AND name = 'ImageNet-1K'",
        (paper["paper_id"],),
    ).fetchone()
    assert row["chunk_id"] is not None, (
        "dataset chunk_id should not be None after reingest"
    )
    assert row["chunk_id"] != old_chunk_id


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_reingest_relinks_metric_by_name_search(tmp_path):
    """After reingest, metric.chunk_id should point to the new chunk containing the metric name."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    chunk0_text = "Introduction to our work. " * 80
    chunk1_text = "We achieve top-1 accuracy of 85.3% on the benchmark. " * 30
    md = tmp_path / "paper.md"
    md.write_text(chunk0_text + chunk1_text)
    ingest_file(conn, md)

    chunks = conn.execute(
        "SELECT id, chunk_index, content FROM chunks WHERE source_uri = ? ORDER BY chunk_index",
        (str(md.resolve()),),
    ).fetchall()
    assert len(chunks) >= 2

    metric_chunk = next(c for c in chunks if "accuracy" in c["content"])
    old_chunk_id = metric_chunk["id"]

    paper = register_paper(conn, "Test Paper", source_uri=str(md.resolve()))
    record_metric(conn, "accuracy", 85.3, paper["paper_id"], chunk_id=old_chunk_id)

    chunk0_new = "Revised introduction. " * 80
    chunk1_new = "Our model achieves accuracy of 87.1% surpassing prior work. " * 30
    md.write_text(chunk0_new + chunk1_new)
    reingest_file(conn, md)

    row = conn.execute(
        "SELECT chunk_id FROM metrics WHERE paper_id = ? AND name = 'accuracy'",
        (paper["paper_id"],),
    ).fetchone()
    assert row["chunk_id"] is not None, (
        "metric chunk_id should not be None after reingest"
    )
    assert row["chunk_id"] != old_chunk_id


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_reingest_relinks_entity_at_chunk_index_zero(tmp_path):
    """Entities linked to chunk_index=0 should be re-linked to the new first chunk."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "paper.md"
    md.write_text("We use BERT for text classification.\n")
    ingest_file(conn, md)

    first_chunk = conn.execute(
        "SELECT id FROM chunks WHERE source_uri = ? AND chunk_index = 0",
        (str(md.resolve()),),
    ).fetchone()
    old_chunk_id = first_chunk["id"]

    paper = register_paper(conn, "Test Paper", source_uri=str(md.resolve()))
    record_method(conn, "BERT", paper["paper_id"], "A method", old_chunk_id)

    md.write_text("Updated: we fine-tune BERT on downstream tasks.\n")
    reingest_file(conn, md)

    row = conn.execute(
        "SELECT chunk_id FROM methods WHERE paper_id = ? AND name = 'BERT'",
        (paper["paper_id"],),
    ).fetchone()
    assert row["chunk_id"] is not None, (
        "method chunk_id should not be None after reingest"
    )
    assert row["chunk_id"] != old_chunk_id


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_reingest_leaves_null_when_no_name_match(tmp_path):
    """If the entity name doesn't appear in any new chunk, chunk_id stays NULL."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "paper.md"
    md.write_text("We use SpecialMethod for experiments.\n")
    ingest_file(conn, md)

    chunk = conn.execute(
        "SELECT id FROM chunks WHERE source_uri = ? LIMIT 1",
        (str(md.resolve()),),
    ).fetchone()

    paper = register_paper(conn, "Test Paper", source_uri=str(md.resolve()))
    record_method(conn, "SpecialMethod", paper["paper_id"], "A method", chunk["id"])

    # Reingest with content that does NOT contain "SpecialMethod"
    md.write_text("Completely different content with no method references.\n")
    reingest_file(conn, md)

    row = conn.execute(
        "SELECT chunk_id FROM methods WHERE paper_id = ? AND name = 'SpecialMethod'",
        (paper["paper_id"],),
    ).fetchone()
    assert row["chunk_id"] is None, (
        "Should remain NULL when name not found in new chunks"
    )


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_reingest_does_not_relink_unrelated_entities(tmp_path):
    """Entities from other papers should not be affected by reingest."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md_a = tmp_path / "a.md"
    md_a.write_text("Paper A uses MethodA for analysis.\n")
    ingest_file(conn, md_a)

    md_b = tmp_path / "b.md"
    md_b.write_text("Paper B uses MethodB for testing.\n")
    ingest_file(conn, md_b)

    chunk_b = conn.execute(
        "SELECT id FROM chunks WHERE source_uri = ? LIMIT 1",
        (str(md_b.resolve()),),
    ).fetchone()

    paper_b = register_paper(conn, "Paper B", source_uri=str(md_b.resolve()))
    record_method(conn, "MethodB", paper_b["paper_id"], "B's method", chunk_b["id"])
    old_b_chunk_id = chunk_b["id"]

    # Reingest only file A
    md_a.write_text("Updated paper A content.\n")
    reingest_file(conn, md_a)

    # Paper B's method should be untouched
    row = conn.execute(
        "SELECT chunk_id FROM methods WHERE paper_id = ? AND name = 'MethodB'",
        (paper_b["paper_id"],),
    ).fetchone()
    assert row["chunk_id"] == old_b_chunk_id, "Unrelated entity should not be affected"


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_reingest_nonexistent_source(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "never_ingested.md"
    md.write_text("Some content.\n")

    with pytest.raises(NotFoundError, match="No chunks found"):
        reingest_file(conn, md)


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
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


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.db._SQL_BATCH_SIZE", 2)
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

    # Conclusion with all source_chunk_ids removed should be deleted (#160)
    conclusions = get_conclusions(conn)
    assert len(conclusions) == 0

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


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.web.httpx.get", _mock_httpx_get)
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


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.web.httpx.get", _mock_httpx_get)
def test_ingest_url_dedup(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    r1 = ingest_url(conn, "https://example.com/paper")
    r2 = ingest_url(conn, "https://example.com/paper")

    assert r1["chunks_added"] >= 1
    assert r2["chunks_added"] == 0
    assert r2["chunks_skipped"] >= 1


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.web.httpx.get", _mock_httpx_get)
def test_ingest_url_stores_title_in_metadata(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    ingest_url(conn, "https://example.com/paper")

    row = conn.execute("SELECT metadata FROM chunks LIMIT 1").fetchone()
    meta = json.loads(row["metadata"])
    assert "title" in meta


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_ingest_url_http_error(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    def _mock_get_error(url, **kwargs):
        import httpx

        raise httpx.HTTPError("Connection refused")

    with patch("knowledge_base.web.httpx.get", _mock_get_error):
        with pytest.raises(ValidationError, match="Failed to fetch"):
            ingest_url(conn, "https://example.com/down")


def test_ingest_url_rejects_non_http_schemes(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    with pytest.raises(ValidationError, match="http or https"):
        ingest_url(conn, "file:///etc/passwd")

    with pytest.raises(ValidationError, match="http or https"):
        ingest_url(conn, "ftp://internal/data")


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
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

    with patch("knowledge_base.web.httpx.get", _mock_get_empty):
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


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
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


# ---------- entity_mentions FK cleanup on reingest (#47) ----------


def _create_entity_with_mention(conn, paper_id, canonical_name, chunk_id, surface_form):
    """Insert an entity + mention row, return the entity id."""
    conn.execute(
        "INSERT INTO entities (canonical_name, entity_type, paper_id) VALUES (?, ?, ?)",
        (canonical_name, "method", paper_id),
    )
    entity_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO entity_mentions (entity_id, surface_form, chunk_id) VALUES (?, ?, ?)",
        (entity_id, surface_form, chunk_id),
    )
    return entity_id


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_reingest_deletes_entity_mentions(tmp_path):
    """reingest_file must delete entity_mentions referencing old chunks."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "entities.md"
    md.write_text("Transformers use self-attention mechanisms.\n")
    ingest_file(conn, md)
    chunk_id = conn.execute("SELECT id FROM chunks LIMIT 1").fetchone()["id"]

    paper_id = register_paper(conn, "Test Paper")["paper_id"]
    entity_id = _create_entity_with_mention(
        conn, paper_id, "transformer", chunk_id, "Transformers"
    )
    conn.commit()

    # Reingest — should NOT raise FK violation
    md.write_text("Updated content about attention.\n")
    reingest_file(conn, md)

    # entity_mentions referencing old chunk should be gone
    remaining = conn.execute(
        "SELECT COUNT(*) as cnt FROM entity_mentions WHERE entity_id = ?",
        (entity_id,),
    ).fetchone()["cnt"]
    assert remaining == 0


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_reingest_preserves_unrelated_entity_mentions(tmp_path):
    """reingest_file must not delete entity_mentions for other source files."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Ingest two files
    md1 = tmp_path / "file1.md"
    md1.write_text("Content about BERT.\n")
    ingest_file(conn, md1)
    chunk_id_1 = conn.execute(
        "SELECT id FROM chunks WHERE source_uri = ?", (str(md1),)
    ).fetchone()["id"]

    md2 = tmp_path / "file2.md"
    md2.write_text("Content about GPT.\n")
    ingest_file(conn, md2)
    chunk_id_2 = conn.execute(
        "SELECT id FROM chunks WHERE source_uri = ?", (str(md2),)
    ).fetchone()["id"]

    paper_id = register_paper(conn, "Test Paper")["paper_id"]
    _create_entity_with_mention(conn, paper_id, "bert", chunk_id_1, "BERT")
    eid2 = _create_entity_with_mention(conn, paper_id, "gpt", chunk_id_2, "GPT")
    conn.commit()

    # Reingest only file1
    md1.write_text("Updated BERT content.\n")
    reingest_file(conn, md1)

    # file2's entity_mentions should be untouched
    remaining = conn.execute(
        "SELECT COUNT(*) as cnt FROM entity_mentions WHERE entity_id = ?",
        (eid2,),
    ).fetchone()["cnt"]
    assert remaining == 1


# ---------------------------------------------------------------------------
# Browser rendering config tests
# ---------------------------------------------------------------------------


def test_get_browser_config_default(tmp_path):
    """Returns None when no browser config is set."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    assert _get_browser_config(conn) is None


def test_configure_browser_cdp(tmp_path):
    """CDP mode stores mode, endpoint, and venv."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    venv = tmp_path / "fakevenv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("#!/bin/sh\n")

    result = configure_browser(
        conn, cdp_endpoint="ws://localhost:3000", venv_path=str(venv)
    )
    cfg = result["browser"]
    assert cfg["mode"] == "cdp"
    assert cfg["endpoint"] == "ws://localhost:3000"
    assert cfg["venv"] == str(venv)


def test_configure_browser_local_venv(tmp_path):
    """Local mode stores mode and venv, no endpoint."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    venv = tmp_path / "fakevenv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("#!/bin/sh\n")

    result = configure_browser(conn, venv_path=str(venv))
    cfg = result["browser"]
    assert cfg["mode"] == "local"
    assert cfg["venv"] == str(venv)
    assert "endpoint" not in cfg


def test_configure_browser_invalid_cdp(tmp_path):
    """Non-ws:// CDP endpoint raises ValidationError."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    venv = tmp_path / "fakevenv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("#!/bin/sh\n")

    with pytest.raises(ValidationError, match="ws://"):
        configure_browser(
            conn, cdp_endpoint="http://localhost:3000", venv_path=str(venv)
        )


def test_configure_browser_invalid_venv(tmp_path):
    """Nonexistent venv path raises ValidationError."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    with pytest.raises(ValidationError, match="Python executable not found"):
        configure_browser(conn, venv_path="/nonexistent/venv")


def test_configure_browser_cdp_without_venv(tmp_path):
    """CDP endpoint without venv raises ValidationError."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    with pytest.raises(ValidationError, match="venv_path is required"):
        configure_browser(conn, cdp_endpoint="ws://localhost:3000")


def test_configure_browser_disable(tmp_path):
    """Empty strings clear all browser config."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    venv = tmp_path / "fakevenv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("#!/bin/sh\n")

    configure_browser(conn, cdp_endpoint="ws://localhost:3000", venv_path=str(venv))
    assert _get_browser_config(conn) is not None

    result = configure_browser(conn, cdp_endpoint="", venv_path="")
    assert result["browser"] is None
    assert _get_browser_config(conn) is None


def test_configure_browser_query_mode(tmp_path):
    """None args return current config without modifying."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    # Query when unconfigured
    result = configure_browser(conn)
    assert result["browser"] is None

    # Configure, then query
    venv = tmp_path / "fakevenv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("#!/bin/sh\n")
    configure_browser(conn, venv_path=str(venv))

    result = configure_browser(conn)
    assert result["browser"]["mode"] == "local"


# ---------------------------------------------------------------------------
# Subprocess render tests
# ---------------------------------------------------------------------------


def _make_fake_venv(base: Path) -> Path:
    """Create a fake venv directory with a python binary stub."""
    venv = base / "fakevenv"
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    py = venv / "bin" / "python"
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    return venv


@patch("knowledge_base.web.subprocess.run")
def test_render_with_browser_success(mock_run, tmp_path):
    """Successful render returns html and screenshot_path."""
    venv = _make_fake_venv(tmp_path)

    def _fake_run(cmd, **kwargs):
        # Write output files that the real script would produce
        out_dir = Path(cmd[3])
        (out_dir / "page.html").write_text("<html><body>Rendered</body></html>")
        (out_dir / "screenshot.png").write_bytes(b"PNG_FAKE")
        return subprocess.CompletedProcess(cmd, 0)

    mock_run.side_effect = _fake_run

    config = {"mode": "local", "venv": str(venv)}
    result = _render_with_browser("https://example.com", config)

    assert result is not None
    assert "Rendered" in result["html"]
    assert result["screenshot_path"].exists()
    assert result["tmpdir"].exists()

    # Cleanup
    import shutil

    shutil.rmtree(result["tmpdir"], ignore_errors=True)


@patch("knowledge_base.web.subprocess.run")
def test_render_with_browser_timeout(mock_run, tmp_path):
    """TimeoutExpired returns None and cleans tmpdir."""
    venv = _make_fake_venv(tmp_path)
    mock_run.side_effect = subprocess.TimeoutExpired(cmd=["x"], timeout=60)

    config = {"mode": "local", "venv": str(venv)}
    result = _render_with_browser("https://example.com", config)
    assert result is None


@patch("knowledge_base.web.subprocess.run")
def test_render_with_browser_subprocess_error(mock_run, tmp_path):
    """CalledProcessError returns None."""
    venv = _make_fake_venv(tmp_path)
    mock_run.side_effect = subprocess.CalledProcessError(1, cmd=["x"])

    config = {"mode": "local", "venv": str(venv)}
    result = _render_with_browser("https://example.com", config)
    assert result is None


@patch("knowledge_base.web.subprocess.run")
def test_render_with_browser_cdp_mode_args(mock_run, tmp_path):
    """CDP mode includes --cdp flag in subprocess command."""
    venv = _make_fake_venv(tmp_path)

    def _fake_run(cmd, **kwargs):
        out_dir = Path(cmd[3])
        (out_dir / "page.html").write_text("<html>OK</html>")
        return subprocess.CompletedProcess(cmd, 0)

    mock_run.side_effect = _fake_run

    config = {
        "mode": "cdp",
        "endpoint": "ws://localhost:3000",
        "venv": str(venv),
    }
    result = _render_with_browser("https://example.com", config)

    # Verify --cdp flag was passed
    called_cmd = mock_run.call_args[0][0]
    assert "--cdp" in called_cmd
    assert "ws://localhost:3000" in called_cmd

    if result and result.get("tmpdir"):
        import shutil

        shutil.rmtree(result["tmpdir"], ignore_errors=True)


def test_render_with_browser_invalid_venv(tmp_path):
    """Returns None when venv python is not found."""
    config = {"mode": "local", "venv": str(tmp_path / "nonexistent")}
    result = _render_with_browser("https://example.com", config)
    assert result is None


# ---------------------------------------------------------------------------
# Integration tests (ingest_url with browser fallback)
# ---------------------------------------------------------------------------

# HTML that trafilatura will extract < 200 chars from (JS-only page)
_JS_ONLY_HTML = (
    "<html><head><title>App</title></head><body><div id='app'></div></body></html>"
)

# HTML with substantial content (> 200 chars) for rendered fallback
_RENDERED_HTML = (
    "<html><head><title>Rendered App</title></head><body><article>"
    + "<p>"
    + "This is a paragraph of rendered content about transformers. " * 20
    + "</p>"
    + "</article></body></html>"
)


def _mock_httpx_js_only(url, **kwargs):
    """Simulates a JS-only page that trafilatura can't extract from."""
    resp = MagicMock()
    resp.status_code = 200
    resp.text = _JS_ONLY_HTML
    resp.raise_for_status = MagicMock()
    return resp


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.web.httpx.get", _mock_httpx_js_only)
@patch("knowledge_base.web._render_with_browser")
def test_ingest_url_browser_rendered(mock_render, tmp_path):
    """Browser fallback fires and produces chunks when trafilatura fails."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    # Configure browser
    venv = tmp_path / "fakevenv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("#!/bin/sh\n")
    configure_browser(conn, venv_path=str(venv))

    tmpdir = tmp_path / "render_out"
    tmpdir.mkdir()
    mock_render.return_value = {
        "html": _RENDERED_HTML,
        "screenshot_path": None,
        "tmpdir": tmpdir,
    }

    result = ingest_url(conn, "https://example.com/spa")
    assert result["chunks_added"] >= 1
    assert result["browser_rendered"] is True


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.web.httpx.get", _mock_httpx_js_only)
def test_ingest_url_no_browser_configured(tmp_path):
    """No browser config → 0 chunks, browser_rendered False."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    result = ingest_url(conn, "https://example.com/spa")
    assert result["chunks_added"] == 0
    assert result["browser_rendered"] is False


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.web.httpx.get", _mock_httpx_js_only)
@patch("knowledge_base.web._render_with_browser")
def test_ingest_url_browser_fallback_also_empty(mock_render, tmp_path):
    """Browser renders but trafilatura still extracts nothing → 0 chunks."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    venv = tmp_path / "fakevenv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("#!/bin/sh\n")
    configure_browser(conn, venv_path=str(venv))

    tmpdir = tmp_path / "render_out"
    tmpdir.mkdir()
    mock_render.return_value = {
        "html": "<html><body></body></html>",
        "screenshot_path": None,
        "tmpdir": tmpdir,
    }

    result = ingest_url(conn, "https://example.com/spa")
    assert result["chunks_added"] == 0


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.web.httpx.get", _mock_httpx_get)
@patch("knowledge_base.web._render_with_browser")
def test_ingest_url_no_fallback_when_trafilatura_succeeds(mock_render, tmp_path):
    """When trafilatura extracts >= 200 chars, browser fallback is not called."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    result = ingest_url(conn, "https://example.com/paper")
    assert result["chunks_added"] >= 1
    assert result["browser_rendered"] is False
    mock_render.assert_not_called()


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.web.httpx.get", _mock_httpx_js_only)
@patch("knowledge_base.web._render_with_browser")
def test_ingest_url_browser_fallback_near_empty(mock_render, tmp_path):
    """Trafilatura < 200 chars, browser renders better content → fallback used."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    venv = tmp_path / "fakevenv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("#!/bin/sh\n")
    configure_browser(conn, venv_path=str(venv))

    tmpdir = tmp_path / "render_out"
    tmpdir.mkdir()
    mock_render.return_value = {
        "html": _RENDERED_HTML,
        "screenshot_path": None,
        "tmpdir": tmpdir,
    }

    result = ingest_url(conn, "https://example.com/spa")
    assert result["browser_rendered"] is True
    assert result["chunks_added"] >= 1


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.web.httpx.get", _mock_httpx_js_only)
@patch("knowledge_base.web._render_with_browser")
def test_ingest_url_browser_fallback_tmpdir_cleanup(mock_render, tmp_path):
    """Tmpdir is cleaned up after browser fallback (both success and exception)."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    venv = tmp_path / "fakevenv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("#!/bin/sh\n")
    configure_browser(conn, venv_path=str(venv))

    tmpdir = tmp_path / "render_out"
    tmpdir.mkdir()
    mock_render.return_value = {
        "html": _RENDERED_HTML,
        "screenshot_path": None,
        "tmpdir": tmpdir,
    }

    ingest_url(conn, "https://example.com/spa")
    # tmpdir should be cleaned up by the finally block
    assert not tmpdir.exists()


# ---------------------------------------------------------------------------
# _extract_web_figures tests
# ---------------------------------------------------------------------------


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._get_vision_config")
def test_extract_web_figures_success(mock_vision_cfg, mock_vision_call, tmp_path):
    """Extracts a figure chunk from a screenshot via vision pipeline."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    mock_vision_cfg.return_value = {
        "base_url": "http://localhost:11434",
        "model": "llava",
    }
    mock_vision_call.return_value = [
        {
            "description": "A bar chart comparing model accuracy across benchmarks.",
            "figure_type": "chart",
            "title": "Accuracy Comparison",
        }
    ]

    # Create a fake screenshot
    screenshot = tmp_path / "screenshot.png"
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\nFAKE")

    count = _extract_web_figures(conn, "https://example.com/page", screenshot)
    assert count == 1

    row = conn.execute(
        "SELECT content, source_type, source_uri, metadata FROM chunks WHERE source_type = 'figure'"
    ).fetchone()
    assert row is not None
    assert "bar chart" in row["content"]
    assert row["source_uri"] == "https://example.com/page"
    meta = json.loads(row["metadata"])
    assert meta["original_source_type"] == "web"
    assert meta["figure_type"] == "chart"


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._get_vision_config")
def test_extract_web_figures_no_vision_config(mock_vision_cfg, tmp_path):
    """Returns 0 when vision is not configured."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    mock_vision_cfg.side_effect = Exception("not configured")

    screenshot = tmp_path / "screenshot.png"
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\nFAKE")

    count = _extract_web_figures(conn, "https://example.com/page", screenshot)
    assert count == 0


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._get_vision_config")
def test_extract_web_figures_dedup(mock_vision_cfg, mock_vision_call, tmp_path):
    """Does not duplicate figure chunks on re-ingest."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    mock_vision_cfg.return_value = {
        "base_url": "http://localhost:11434",
        "model": "llava",
    }
    mock_vision_call.return_value = [
        {
            "description": "A diagram of attention mechanism.",
            "figure_type": "diagram",
            "title": "Attention",
        }
    ]

    screenshot = tmp_path / "screenshot.png"
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\nFAKE")

    count1 = _extract_web_figures(conn, "https://example.com/page", screenshot)
    assert count1 == 1

    # Re-ingest: old figures cleaned up, new one inserted (same content → 1 total)
    count2 = _extract_web_figures(conn, "https://example.com/page", screenshot)
    assert count2 == 1

    total = conn.execute(
        "SELECT COUNT(*) as cnt FROM chunks WHERE source_uri = ? AND source_type = 'figure'",
        ("https://example.com/page",),
    ).fetchone()["cnt"]
    assert total == 1


# ---------------------------------------------------------------------------
# _extract_html_images tests (issue #82)
# ---------------------------------------------------------------------------

# Helpers for image tests

_VISION_CFG = {"base_url": "http://localhost:11434", "model": "llava"}

_FIGURE_RESPONSE = [
    {
        "description": "A scientific diagram showing neural network architecture.",
        "figure_type": "diagram",
        "title": "Architecture Overview",
    }
]


def _make_test_png(width=200, height=200):
    """Create a minimal valid PNG of given dimensions using Pillow."""
    import io

    from PIL import Image

    img = Image.new("RGB", (width, height), color=(128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_test_jpeg(width=200, height=200):
    """Create a minimal valid JPEG of given dimensions."""
    import io

    from PIL import Image

    img = Image.new("RGB", (width, height), color=(128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _mock_image_stream(image_bytes, url="https://example.com/img.png"):
    """Create a mock httpx streaming response for image download."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.url = url
    mock_response.headers = {"content-length": str(len(image_bytes))}
    mock_response.iter_bytes = MagicMock(return_value=iter([image_bytes]))
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)
    return mock_response


# --- Candidate parsing ---


def test_parse_image_candidates_basic():
    """Parses qualifying <img> tags and returns (url, alt) tuples."""
    html = (
        "<html><body>"
        '<img src="https://example.com/fig.png" alt="A figure">'
        '<img src="data:image/png;base64,abc" alt="inline">'
        '<img src="https://example.com/icon.svg">'
        "</body></html>"
    )
    result = _parse_image_candidates(html, "https://example.com/page")
    assert result == [("https://example.com/fig.png", "A figure")]


def test_parse_image_candidates_exclude_urls():
    """Images in exclude_urls are skipped (cross-source dedup)."""
    html = (
        "<html><body>"
        '<img src="https://example.com/already-seen.png">'
        '<img src="https://example.com/new-image.png">'
        "</body></html>"
    )
    result = _parse_image_candidates(
        html,
        "https://example.com/page",
        exclude_urls=frozenset({"https://example.com/already-seen.png"}),
    )
    assert len(result) == 1
    assert result[0][0] == "https://example.com/new-image.png"


def test_parse_image_candidates_empty_vs_parse_failure():
    """Empty HTML returns [] (no images); parse failure returns None."""
    # Empty HTML parses fine but has no <img> tags → empty list
    result = _parse_image_candidates("", "https://example.com/page")
    assert result == []

    # Parse exception → None (caller must not trigger stale cleanup)
    with patch("knowledge_base.web._ImgTagParser.feed", side_effect=Exception("boom")):
        result = _parse_image_candidates(
            "<html><body></body></html>", "https://example.com/page"
        )
        assert result is None


# --- _parse_srcset unit tests ---


def test_parse_srcset_w_descriptors():
    result = _parse_srcset("small.jpg 300w, medium.jpg 600w, large.jpg 1200w")
    assert result == "large.jpg"


def test_parse_srcset_x_descriptors():
    result = _parse_srcset("normal.jpg 1x, retina.jpg 2x, ultra.jpg 3x")
    assert result == "ultra.jpg"


def test_parse_srcset_x_with_src_baseline():
    """src acts as implicit 1x — should not be downgraded by lower-density srcset."""
    result = _parse_srcset("thumb.jpg 0.5x", current_src="default.jpg")
    assert result == "default.jpg"


def test_parse_srcset_x_upgrade_over_src():
    """srcset with higher density should beat the implicit 1x src."""
    result = _parse_srcset("retina.jpg 2x", current_src="default.jpg")
    assert result == "retina.jpg"


def test_parse_srcset_no_descriptors():
    result = _parse_srcset("first.jpg, second.jpg, third.jpg")
    assert result == "third.jpg"


def test_parse_srcset_skips_svg():
    result = _parse_srcset("vector.svg 2x, raster.png 1x")
    assert result == "raster.png"


def test_parse_srcset_skips_data_uri():
    result = _parse_srcset("data:image/png;base64,abc 1x, real.jpg 2x")
    assert result == "real.jpg"


def test_parse_srcset_malformed():
    """Malformed descriptors are skipped; valid entries survive."""
    result = _parse_srcset("bad.jpg ???, good.jpg 2x, broken.jpg w")
    assert result == "good.jpg"


def test_parse_srcset_whitespace():
    """Handles extraneous whitespace around entries."""
    result = _parse_srcset("  small.jpg 300w ,  large.jpg 1200w  ")
    assert result == "large.jpg"


def test_parse_srcset_empty():
    assert _parse_srcset("") is None
    assert _parse_srcset("   ") is None


def test_parse_srcset_all_svg():
    """All candidates are SVG — returns None."""
    assert _parse_srcset("a.svg 1x, b.svgz 2x") is None


# --- _parse_image_candidates with srcset/picture ---


def test_parse_image_candidates_img_srcset():
    """Standalone <img srcset> resolves to best candidate."""
    html = (
        '<img src="fallback.jpg" srcset="small.jpg 300w, large.jpg 1200w" alt="test">'
    )
    result = _parse_image_candidates(html, "https://example.com/page")
    assert result is not None
    assert len(result) == 1
    assert result[0] == ("https://example.com/large.jpg", "test")


def test_parse_image_candidates_picture_element():
    """<picture> with <source> picks first qualifying source's best candidate."""
    html = """
    <picture>
      <source srcset="hero.webp 1200w, hero-sm.webp 600w" type="image/webp">
      <source srcset="hero.jpg 1200w, hero-sm.jpg 600w" type="image/jpeg">
      <img src="fallback.jpg" alt="Hero">
    </picture>
    """
    result = _parse_image_candidates(html, "https://example.com/")
    assert result is not None
    assert len(result) == 1
    assert result[0] == ("https://example.com/hero.webp", "Hero")


def test_parse_image_candidates_picture_source_ordering():
    """Second source is ignored when first qualifies."""
    html = """
    <picture>
      <source srcset="first.jpg 800w">
      <source srcset="second.jpg 1200w">
      <img src="fallback.jpg" alt="test">
    </picture>
    """
    result = _parse_image_candidates(html, "https://example.com/")
    assert result is not None
    assert len(result) == 1
    # First source wins even though second has higher resolution
    assert result[0][0] == "https://example.com/first.jpg"


def test_parse_image_candidates_picture_fallback():
    """No qualifying sources → falls back to <img src>."""
    html = """
    <picture>
      <source srcset="vector.svg 2x" type="image/svg+xml">
      <img src="fallback.jpg" alt="test">
    </picture>
    """
    result = _parse_image_candidates(html, "https://example.com/")
    assert result is not None
    assert len(result) == 1
    assert result[0] == ("https://example.com/fallback.jpg", "test")


def test_parse_image_candidates_picture_svg_source_skipped():
    """type='image/svg+xml' source is skipped; next raster source wins."""
    html = """
    <picture>
      <source srcset="vector.svg 2x" type="image/svg+xml">
      <source srcset="raster.jpg 1200w" type="image/jpeg">
      <img src="fallback.jpg" alt="test">
    </picture>
    """
    result = _parse_image_candidates(html, "https://example.com/")
    assert result is not None
    assert len(result) == 1
    assert result[0][0] == "https://example.com/raster.jpg"


def test_parse_image_candidates_srcset_mixed_svg_raster():
    """SVG filtered pre-selection in srcset; raster candidate wins over SVG."""
    # With 2x raster surviving after SVG filter, it beats implicit 1x src
    html = '<img src="fallback.jpg" srcset="vector.svg 3x, raster.png 2x" alt="test">'
    result = _parse_image_candidates(html, "https://example.com/")
    assert result is not None
    assert len(result) == 1
    assert result[0][0] == "https://example.com/raster.png"


def test_parse_image_candidates_srcset_dedup():
    """srcset URL deduped against plain src from another img."""
    html = """
    <img src="photo.jpg" alt="first">
    <img src="other.jpg" srcset="photo.jpg 2x" alt="second">
    """
    result = _parse_image_candidates(html, "https://example.com/")
    assert result is not None
    # photo.jpg appears once (from first img); second img's srcset resolves
    # to photo.jpg which is deduped
    assert len(result) == 1
    assert result[0] == ("https://example.com/photo.jpg", "first")


# --- Integration: srcset/picture through _extract_html_images ---


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
def test_extract_html_images_srcset_end_to_end(
    mock_stream, _mock_ip, mock_vision_cfg, mock_vision_call, tmp_path
):
    """Full pipeline: <picture> + srcset HTML -> vision -> figure chunks."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    mock_vision_cfg.return_value = _VISION_CFG
    mock_vision_call.return_value = _FIGURE_RESPONSE

    png_bytes = _make_test_png()
    mock_stream.return_value = _mock_image_stream(png_bytes)

    html = """<html><body>
    <picture>
      <source srcset="hero-sm.webp 600w, hero-lg.webp 1200w" type="image/webp">
      <img src="hero-fallback.jpg" alt="Hero diagram">
    </picture>
    </body></html>"""
    count = _extract_html_images(conn, html, "https://example.com/page")
    assert count == 1

    row = conn.execute(
        "SELECT content, metadata FROM chunks WHERE source_type = 'figure'"
    ).fetchone()
    assert row is not None
    meta = json.loads(row["metadata"])
    assert meta["figure_type"] == "web_image"
    # Should have resolved to the best srcset candidate (hero-lg.webp 1200w)
    assert meta["image_url"] == "https://example.com/hero-lg.webp"
    assert meta["alt_text"] == "Hero diagram"


# --- Core extraction ---


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
def test_extract_html_images_basic(
    mock_stream, _mock_ip, mock_vision_cfg, mock_vision_call, tmp_path
):
    """Extracts one qualifying <img> and stores as figure chunk."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    mock_vision_cfg.return_value = _VISION_CFG
    mock_vision_call.return_value = _FIGURE_RESPONSE

    png_bytes = _make_test_png()
    mock_stream.return_value = _mock_image_stream(png_bytes)

    html = '<html><body><img src="https://example.com/diagram.png"></body></html>'
    count = _extract_html_images(conn, html, "https://example.com/page")
    assert count == 1

    row = conn.execute(
        "SELECT content, source_type, source_uri, chunk_index, metadata "
        "FROM chunks WHERE source_type = 'figure'"
    ).fetchone()
    assert row is not None
    assert "neural network" in row["content"]
    assert row["source_uri"] == "https://example.com/page"
    assert row["chunk_index"] >= 2_000_000
    meta = json.loads(row["metadata"])
    assert meta["figure_type"] == "web_image"
    assert meta["original_source_type"] == "web"
    assert meta["image_url"] == "https://example.com/diagram.png"


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
def test_extract_html_images_multiple(
    mock_stream, _mock_ip, mock_vision_cfg, mock_vision_call, tmp_path
):
    """Extracts multiple qualifying images."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    mock_vision_cfg.return_value = _VISION_CFG
    # Return different descriptions per call to avoid content-hash dedup
    mock_vision_call.side_effect = [
        [
            {
                "description": f"Figure {i} description.",
                "figure_type": "diagram",
                "title": f"Fig {i}",
            }
        ]
        for i in range(3)
    ]

    png_bytes = _make_test_png()
    # Each call needs a fresh stream mock (streams get consumed)
    mock_stream.side_effect = [_mock_image_stream(png_bytes) for _ in range(3)]

    html = (
        "<html><body>"
        '<img src="https://example.com/fig1.png">'
        '<img src="https://example.com/fig2.png">'
        '<img src="https://example.com/fig3.png">'
        "</body></html>"
    )
    count = _extract_html_images(conn, html, "https://example.com/page")
    assert count == 3


# --- Filtering ---


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
def test_extract_html_images_filters_small_html_attrs(
    mock_stream, _mock_ip, mock_vision_cfg, tmp_path
):
    """Skips images with HTML width/height < 100px without downloading."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG

    html = '<html><body><img src="https://example.com/small.png" width="50" height="50"></body></html>'
    count = _extract_html_images(conn, html, "https://example.com/page")
    assert count == 0
    mock_stream.assert_not_called()


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
def test_extract_html_images_filters_small_pixels(
    mock_stream, _mock_ip, mock_vision_cfg, tmp_path
):
    """Skips images whose downloaded pixels are < 100px."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG

    small_png = _make_test_png(width=50, height=50)
    mock_stream.return_value = _mock_image_stream(small_png)

    html = '<html><body><img src="https://example.com/small.png"></body></html>'
    count = _extract_html_images(conn, html, "https://example.com/page")
    assert count == 0


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
def test_extract_html_images_filters_decorative_url(
    mock_stream, _mock_ip, mock_vision_cfg, tmp_path
):
    """Skips images with decorative URL patterns (logo, icon, etc.)."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG

    html = '<html><body><img src="https://example.com/logo.png"></body></html>'
    count = _extract_html_images(conn, html, "https://example.com/page")
    assert count == 0
    mock_stream.assert_not_called()


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
def test_extract_html_images_filters_decorative_alt(
    mock_stream, _mock_ip, mock_vision_cfg, tmp_path
):
    """Skips images with decorative alt text (word boundary match)."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG

    html = '<html><body><img src="https://example.com/img.png" alt="Company logo"></body></html>'
    count = _extract_html_images(conn, html, "https://example.com/page")
    assert count == 0
    mock_stream.assert_not_called()


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
def test_extract_html_images_filters_svg(
    mock_stream, _mock_ip, mock_vision_cfg, tmp_path
):
    """Skips SVG images."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG

    html = '<html><body><img src="https://example.com/diagram.svg"></body></html>'
    count = _extract_html_images(conn, html, "https://example.com/page")
    assert count == 0
    mock_stream.assert_not_called()


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
def test_extract_html_images_filters_data_uri(
    mock_stream, _mock_ip, mock_vision_cfg, tmp_path
):
    """Skips data URI images."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG

    html = '<html><body><img src="data:image/png;base64,iVBOR..."></body></html>'
    count = _extract_html_images(conn, html, "https://example.com/page")
    assert count == 0
    mock_stream.assert_not_called()


# --- URL handling ---


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
def test_extract_html_images_resolves_relative_urls(
    mock_stream, _mock_ip, mock_vision_cfg, mock_vision_call, tmp_path
):
    """Resolves relative <img> src against base URL."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG
    mock_vision_call.return_value = _FIGURE_RESPONSE

    png_bytes = _make_test_png()
    mock_stream.return_value = _mock_image_stream(png_bytes)

    html = '<html><body><img src="/images/fig1.png"></body></html>'
    _extract_html_images(conn, html, "https://example.com/papers/page")

    # Verify the stream call used the resolved absolute URL
    call_args = mock_stream.call_args
    assert (
        call_args[1].get("url", call_args[0][1] if len(call_args[0]) > 1 else None)
        == "https://example.com/images/fig1.png"
        or "https://example.com/images/fig1.png" in str(call_args)
    )


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.httpx.stream")
def test_extract_html_images_rejects_private_ip(mock_stream, mock_vision_cfg, tmp_path):
    """SSRF guard: rejects private IP image URLs."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG

    html = '<html><body><img src="http://192.168.1.1/secret.png"></body></html>'
    count = _extract_html_images(conn, html, "https://example.com/page")
    assert count == 0
    mock_stream.assert_not_called()


def test_is_private_ip_literals():
    """is_private_ip correctly identifies non-global IPs."""
    assert is_private_ip("127.0.0.1") is True
    assert is_private_ip("192.168.1.1") is True
    assert is_private_ip("10.0.0.1") is True
    assert is_private_ip("169.254.169.254") is True
    assert is_private_ip("localhost") is True
    # is_global coverage: unspecified address
    assert is_private_ip("0.0.0.0") is True


@patch("knowledge_base.utils.socket.getaddrinfo")
def test_is_private_ip_rejects_mixed_resolution(mock_getaddrinfo):
    """is_private_ip rejects hostnames that resolve to any private IP (#151)."""
    # Simulate DNS returning both a public and a private address
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 0)),
    ]
    assert is_private_ip("multi-homed.example.com") is True


@patch("knowledge_base.utils.socket.getaddrinfo")
def test_is_private_ip_allows_all_public(mock_getaddrinfo):
    """is_private_ip allows hostnames where all resolved IPs are public."""
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.35", 0)),
    ]
    assert is_private_ip("safe.example.com") is False


@patch("knowledge_base.utils.socket.getaddrinfo")
def test_is_private_ip_rejects_ipv6_loopback(mock_getaddrinfo):
    """is_private_ip rejects hostnames resolving to IPv6 loopback (#151)."""
    mock_getaddrinfo.return_value = [
        (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", 0, 0, 0)),
    ]
    assert is_private_ip("ipv6-loopback.example.com") is True


def test_validate_image_url():
    """_validate_image_url rejects non-http, private IPs."""
    # Scheme checks (no DNS needed)
    assert _validate_image_url("ftp://example.com/img.png") is False
    # IP literal checks (no DNS needed)
    assert _validate_image_url("http://192.168.1.1/img.png") is False
    assert _validate_image_url("http://127.0.0.1/img.png") is False
    assert _validate_image_url("http://localhost/img.png") is False
    # Public IP literal (should pass)
    assert _validate_image_url("https://93.184.216.34/img.png") is True


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._get_vision_config")
@patch(
    "knowledge_base.web.is_private_ip",
    side_effect=lambda h: h in ("169.254.169.254",),
)
@patch("knowledge_base.web.httpx.stream")
def test_extract_html_images_rejects_redirect_to_private(
    mock_stream, _mock_ip, mock_vision_cfg, tmp_path
):
    """SSRF guard: rejects images that redirect to private IPs."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG

    # Mock a response whose final URL is a private IP
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.url = "http://169.254.169.254/latest/meta-data"
    mock_resp.headers = {"content-length": "100"}
    mock_resp.iter_bytes = MagicMock(return_value=iter([_make_test_png()]))
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_stream.return_value = mock_resp

    html = '<html><body><img src="https://example.com/redirect-img.png"></body></html>'
    count = _extract_html_images(conn, html, "https://example.com/page")
    assert count == 0


# --- Dedup ---


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
def test_extract_html_images_dedup_by_url(
    mock_stream, _mock_ip, mock_vision_cfg, mock_vision_call, tmp_path
):
    """Two <img> with same src → only one download."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG
    mock_vision_call.return_value = _FIGURE_RESPONSE

    png_bytes = _make_test_png()
    mock_stream.return_value = _mock_image_stream(png_bytes)

    html = (
        "<html><body>"
        '<img src="https://example.com/same.png">'
        '<img src="https://example.com/same.png">'
        "</body></html>"
    )
    count = _extract_html_images(conn, html, "https://example.com/page")
    assert count == 1
    assert mock_stream.call_count == 1


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
def test_extract_html_images_dedup_by_content_hash(
    mock_stream, _mock_ip, mock_vision_cfg, mock_vision_call, tmp_path
):
    """Two different images with identical vision description → one chunk."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG
    # Both images produce identical description
    mock_vision_call.return_value = _FIGURE_RESPONSE

    png_bytes = _make_test_png()
    mock_stream.return_value = _mock_image_stream(png_bytes)

    html = (
        "<html><body>"
        '<img src="https://example.com/img1.png">'
        '<img src="https://example.com/img2.png">'
        "</body></html>"
    )
    count = _extract_html_images(conn, html, "https://example.com/page")
    # Second image should be skipped due to content hash collision
    assert count == 1


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
def test_extract_html_images_cross_page_content_hash(
    mock_stream, _mock_ip, mock_vision_cfg, mock_vision_call, tmp_path
):
    """Global content_hash uniqueness: same description from different pages → second skipped."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG
    mock_vision_call.return_value = _FIGURE_RESPONSE

    png_bytes = _make_test_png()
    mock_stream.return_value = _mock_image_stream(png_bytes)

    html = '<html><body><img src="https://example.com/fig.png"></body></html>'
    count1 = _extract_html_images(conn, html, "https://page1.com/a")
    assert count1 == 1

    # Different page, different image URL, same vision description
    mock_stream.return_value = _mock_image_stream(png_bytes)
    html2 = '<html><body><img src="https://other.com/fig.png"></body></html>'
    count2 = _extract_html_images(conn, html2, "https://page2.com/b")
    assert count2 == 0  # global dedup


# --- Error handling ---


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
def test_extract_html_images_download_failure(
    mock_stream, _mock_ip, mock_vision_cfg, mock_vision_call, tmp_path
):
    """Download failure on one image doesn't block others."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG
    mock_vision_call.return_value = _FIGURE_RESPONSE

    png_bytes = _make_test_png()
    # First call fails, second succeeds
    fail_resp = MagicMock()
    fail_resp.__enter__ = MagicMock(side_effect=Exception("connection refused"))
    fail_resp.__exit__ = MagicMock(return_value=False)

    ok_resp = _mock_image_stream(png_bytes)
    mock_stream.side_effect = [fail_resp, ok_resp]

    html = (
        "<html><body>"
        '<img src="https://example.com/broken.png">'
        '<img src="https://example.com/good.png">'
        "</body></html>"
    )
    count = _extract_html_images(conn, html, "https://example.com/page")
    assert count == 1


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
def test_extract_html_images_non_png_conversion(
    mock_stream, _mock_ip, mock_vision_cfg, mock_vision_call, tmp_path
):
    """JPEG images are converted to PNG before vision call."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG
    mock_vision_call.return_value = _FIGURE_RESPONSE

    jpeg_bytes = _make_test_jpeg()
    mock_stream.return_value = _mock_image_stream(jpeg_bytes)

    html = '<html><body><img src="https://example.com/photo.jpg"></body></html>'
    count = _extract_html_images(conn, html, "https://example.com/page")
    assert count == 1

    # Verify vision was called with PNG base64
    b64_arg = mock_vision_call.call_args[0][0]
    import base64

    decoded = base64.b64decode(b64_arg)
    assert decoded[:4] == b"\x89PNG"


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
def test_extract_html_images_oversized_download(
    mock_stream, _mock_ip, mock_vision_cfg, tmp_path
):
    """Skips images that exceed _MAX_IMAGE_DOWNLOAD_BYTES."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG

    # Simulate a stream that yields more than 10MB
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.url = "https://example.com/huge.png"
    mock_resp.headers = {}  # no content-length — relies on byte counter
    chunk = b"\x00" * (1024 * 1024)  # 1MB chunks
    mock_resp.iter_bytes = MagicMock(return_value=iter([chunk] * 12))  # 12MB
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_stream.return_value = mock_resp

    html = '<html><body><img src="https://example.com/huge.png"></body></html>'
    count = _extract_html_images(conn, html, "https://example.com/page")
    assert count == 0


# --- Lifecycle ---


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
def test_extract_html_images_stale_cleanup(
    mock_stream, _mock_ip, mock_vision_cfg, mock_vision_call, tmp_path
):
    """Re-extraction replaces old inline image chunks."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG

    png_bytes = _make_test_png()
    source_url = "https://example.com/page"

    # First extraction
    mock_vision_call.return_value = [
        {
            "description": "Old figure description.",
            "figure_type": "diagram",
            "title": "Old",
        }
    ]
    mock_stream.return_value = _mock_image_stream(png_bytes)
    html = '<html><body><img src="https://example.com/fig.png"></body></html>'
    count1 = _extract_html_images(conn, html, source_url)
    assert count1 == 1

    # Second extraction with different description
    mock_vision_call.return_value = [
        {
            "description": "New figure description.",
            "figure_type": "chart",
            "title": "New",
        }
    ]
    mock_stream.return_value = _mock_image_stream(png_bytes)
    count2 = _extract_html_images(conn, html, source_url)
    assert count2 == 1

    # Only one chunk should remain (old one cleaned up)
    total = conn.execute(
        "SELECT COUNT(*) as cnt FROM chunks WHERE source_uri = ? "
        "AND source_type = 'figure' AND chunk_index >= 2000000",
        (source_url,),
    ).fetchone()["cnt"]
    assert total == 1
    # And it should be the new one
    row = conn.execute(
        "SELECT content FROM chunks WHERE source_uri = ? "
        "AND source_type = 'figure' AND chunk_index >= 2000000",
        (source_url,),
    ).fetchone()
    assert "New figure" in row["content"]


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
def test_extract_html_images_reingest_same_description(
    mock_stream, _mock_ip, mock_vision_cfg, mock_vision_call, tmp_path
):
    """Re-ingest with identical vision output leaves exactly 1 chunk (regression)."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG
    mock_vision_call.return_value = _FIGURE_RESPONSE

    png_bytes = _make_test_png()
    source_url = "https://example.com/page"
    html = '<html><body><img src="https://example.com/fig.png"></body></html>'

    mock_stream.return_value = _mock_image_stream(png_bytes)
    count1 = _extract_html_images(conn, html, source_url)
    assert count1 == 1

    # Re-ingest with same description — stale cleanup first, then dedup should still insert
    mock_stream.return_value = _mock_image_stream(png_bytes)
    count2 = _extract_html_images(conn, html, source_url)
    assert count2 == 1

    total = conn.execute(
        "SELECT COUNT(*) as cnt FROM chunks WHERE source_uri = ? "
        "AND source_type = 'figure' AND chunk_index >= 2000000",
        (source_url,),
    ).fetchone()["cnt"]
    assert total == 1


@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
def test_extract_html_images_embed_failure_preserves_old(
    mock_stream, _mock_ip, mock_vision_cfg, mock_vision_call, tmp_path
):
    """If embedding fails, old chunks are preserved (no data loss)."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG
    mock_vision_call.return_value = _FIGURE_RESPONSE

    png_bytes = _make_test_png()
    source_url = "https://example.com/page"
    html = '<html><body><img src="https://example.com/fig.png"></body></html>'

    # First extraction with real embed mock
    with patch("knowledge_base.ingest.embed", _fake_embed):
        mock_stream.return_value = _mock_image_stream(png_bytes)
        count1 = _extract_html_images(conn, html, source_url)
        assert count1 == 1

    # Second extraction: embed raises → should preserve old chunks
    with patch("knowledge_base.ingest.embed", side_effect=Exception("embed failed")):
        mock_stream.return_value = _mock_image_stream(png_bytes)
        mock_vision_call.return_value = [
            {
                "description": "Different description.",
                "figure_type": "chart",
                "title": "X",
            }
        ]
        try:
            _extract_html_images(conn, html, source_url)
        except Exception:
            pass  # Expected to propagate

    # Old chunk should still be there
    total = conn.execute(
        "SELECT COUNT(*) as cnt FROM chunks WHERE source_uri = ? "
        "AND source_type = 'figure' AND chunk_index >= 2000000",
        (source_url,),
    ).fetchone()["cnt"]
    assert total == 1


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
def test_cleanup_stale_inline_images_on_zero_new(
    mock_stream, _mock_ip, mock_vision_cfg, mock_vision_call, tmp_path
):
    """Stale inline image chunks are deleted when page loses all images (#152)."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG
    mock_vision_call.return_value = _FIGURE_RESPONSE

    png_bytes = _make_test_png()
    source_url = "https://example.com/page"

    # First ingestion: page has one image → 1 figure chunk created
    mock_stream.return_value = _mock_image_stream(png_bytes)
    html_with_img = '<html><body><img src="https://example.com/fig.png"></body></html>'
    count = _extract_html_images(conn, html_with_img, source_url)
    assert count == 1

    before = conn.execute(
        "SELECT COUNT(*) as cnt FROM chunks WHERE source_uri = ? "
        "AND source_type = 'figure' AND chunk_index >= 2000000",
        (source_url,),
    ).fetchone()["cnt"]
    assert before == 1

    # Page now has no images — _cleanup_stale_inline_images should remove the chunk
    deleted = _cleanup_stale_inline_images(conn, source_url)
    assert deleted == 1

    after = conn.execute(
        "SELECT COUNT(*) as cnt FROM chunks WHERE source_uri = ? "
        "AND source_type = 'figure' AND chunk_index >= 2000000",
        (source_url,),
    ).fetchone()["cnt"]
    assert after == 0


def test_cleanup_stale_inline_images_noop_when_none(tmp_path):
    """No-op when there are no stale inline image chunks."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    deleted = _cleanup_stale_inline_images(conn, "https://example.com/no-images")
    assert deleted == 0


# --- Rendered DOM (Phase 2) ---


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
def test_extract_html_images_rendered_dom_dedup(
    mock_stream, _mock_ip, mock_vision_cfg, mock_vision_call, tmp_path
):
    """Rendered DOM adds JS-injected images but deduplicates shared ones."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG
    mock_vision_call.side_effect = [
        [{"description": f"Fig {i}.", "figure_type": "diagram", "title": f"F{i}"}]
        for i in range(3)
    ]
    png_bytes = _make_test_png()
    mock_stream.side_effect = [_mock_image_stream(png_bytes) for _ in range(3)]

    static_html = (
        "<html><body>"
        '<img src="https://example.com/shared.png">'
        '<img src="https://example.com/static-only.png">'
        "</body></html>"
    )
    rendered_html = (
        "<html><body>"
        '<img src="https://example.com/shared.png">'
        '<img src="https://example.com/js-injected.png">'
        "</body></html>"
    )
    count = _extract_html_images(
        conn,
        static_html,
        source_url="https://example.com/page",
        extra_html_sources=[(rendered_html, "https://example.com/page")],
    )
    assert count == 3

    urls = {
        json.loads(r["metadata"])["image_url"]
        for r in conn.execute(
            "SELECT metadata FROM chunks WHERE source_type = 'figure'"
        ).fetchall()
    }
    assert urls == {
        "https://example.com/shared.png",
        "https://example.com/static-only.png",
        "https://example.com/js-injected.png",
    }


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
def test_extract_html_images_rendered_only_when_static_empty(
    mock_stream, _mock_ip, mock_vision_cfg, mock_vision_call, tmp_path
):
    """When static HTML has no images, rendered DOM images are still extracted."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG
    mock_vision_call.return_value = _FIGURE_RESPONSE
    png_bytes = _make_test_png()
    mock_stream.return_value = _mock_image_stream(png_bytes)

    static_html = "<html><body>No images here.</body></html>"
    rendered_html = '<html><body><img src="https://example.com/lazy.png"></body></html>'

    count = _extract_html_images(
        conn,
        static_html,
        source_url="https://example.com/page",
        extra_html_sources=[(rendered_html, "https://example.com/page")],
    )
    assert count == 1


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
def test_extract_html_images_aborts_on_extra_source_parse_failure(
    mock_stream, _mock_ip, mock_vision_cfg, mock_vision_call, tmp_path
):
    """When an extra HTML source fails to parse, abort to avoid data loss.

    The delete-all-then-reinsert pattern means proceeding with partial
    candidates would lose figures previously extracted from the failed source.
    """
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG
    mock_vision_call.return_value = _FIGURE_RESPONSE
    png_bytes = _make_test_png()
    mock_stream.return_value = _mock_image_stream(png_bytes)

    # Pre-seed a figure chunk to verify it survives
    conn.execute(
        "INSERT INTO chunks (source_uri, source_type, chunk_index, content, content_hash)"
        " VALUES (?, 'figure', 10000, 'existing figure', 'hash_existing')",
        ("https://example.com/page",),
    )
    conn.commit()
    before = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE source_uri = ? AND source_type = 'figure'",
        ("https://example.com/page",),
    ).fetchone()[0]
    assert before == 1

    static_html = '<html><body><img src="https://example.com/good.png"></body></html>'

    with patch("knowledge_base.web._parse_image_candidates") as mock_parse:
        # First call (primary HTML) succeeds, second call (extra source) fails
        mock_parse.side_effect = [
            [("https://example.com/good.png", "good image")],
            None,  # parse failure
        ]
        count = _extract_html_images(
            conn,
            static_html,
            source_url="https://example.com/page",
            extra_html_sources=[("bad html", "https://example.com/page")],
        )

    assert count == 0, "should abort when extra source parse fails"

    after = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE source_uri = ? AND source_type = 'figure'",
        ("https://example.com/page",),
    ).fetchone()[0]
    assert after == before, "existing figure chunks must not be deleted"


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
@patch("knowledge_base.web.httpx.get")
def test_ingest_url_preserves_figures_on_extraction_failure(
    mock_get, mock_stream, _mock_ip, mock_vision_cfg, mock_vision_call, tmp_path
):
    """ingest_url must NOT delete stale figures when _extract_html_images raises."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG
    mock_vision_call.return_value = _FIGURE_RESPONSE

    source_url = "https://example.com/article"
    html_with_img = (
        "<html><head><title>Page</title></head><body>"
        "<p>" + "word " * 80 + "</p>"
        '<img src="https://example.com/fig.png">'
        "</body></html>"
    )

    # First ingest: create a figure chunk via _extract_html_images
    png_bytes = _make_test_png()
    mock_stream.return_value = _mock_image_stream(png_bytes)
    resp = MagicMock()
    resp.status_code = 200
    resp.text = html_with_img
    resp.url = source_url
    resp.raise_for_status = MagicMock()
    mock_get.return_value = resp
    result = ingest_url(conn, source_url)
    assert result.get("figures_extracted", 0) >= 1

    before = conn.execute(
        "SELECT COUNT(*) as cnt FROM chunks WHERE source_uri = ? "
        "AND source_type = 'figure' AND chunk_index >= 2000000",
        (source_url,),
    ).fetchone()["cnt"]
    assert before >= 1

    # Second ingest: _extract_html_images raises — figures must be preserved
    with patch(
        "knowledge_base.web._extract_html_images",
        side_effect=Exception("vision unavailable"),
    ):
        mock_get.return_value = resp
        ingest_url(conn, source_url)

    after = conn.execute(
        "SELECT COUNT(*) as cnt FROM chunks WHERE source_uri = ? "
        "AND source_type = 'figure' AND chunk_index >= 2000000",
        (source_url,),
    ).fetchone()["cnt"]
    assert after == before, "Figures deleted despite extraction failure — data loss!"


# --- Integration with ingest_url ---


_RICH_HTML = """<html>
<head><title>Test Page</title></head>
<body>
<h1>Research Results</h1>
<p>This is a paragraph with enough text content to pass the 200 character threshold
that trafilatura needs to consider this page as having real content. We add more text
here to ensure we get well past the minimum. More filler text for the threshold.</p>
<img src="https://example.com/results-chart.png" alt="Results chart">
</body>
</html>"""


def _mock_httpx_get_with_images(url, **kwargs):
    resp = MagicMock()
    resp.status_code = 200
    resp.text = _RICH_HTML
    resp.url = url  # post-redirect URL
    resp.raise_for_status = MagicMock()
    return resp


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
@patch("knowledge_base.web.httpx.get", _mock_httpx_get_with_images)
def test_ingest_url_extracts_inline_images(
    mock_stream, _mock_ip, mock_vision_cfg, mock_vision_call, tmp_path
):
    """ingest_url with <img> tags extracts inline images when no browser fallback."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG
    mock_vision_call.return_value = _FIGURE_RESPONSE

    png_bytes = _make_test_png()
    mock_stream.return_value = _mock_image_stream(png_bytes)

    result = ingest_url(conn, "https://example.com/article")
    assert result.get("figures_extracted", 0) >= 1


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.web._extract_html_images")
@patch("knowledge_base.web._extract_web_figures")
@patch("knowledge_base.web._render_with_browser")
@patch("knowledge_base.web._get_browser_config")
@patch("knowledge_base.web.httpx.get")
def test_ingest_url_runs_inline_even_with_screenshot_figures(
    mock_get,
    mock_browser_cfg,
    mock_render,
    mock_web_figures,
    mock_html_images,
    tmp_path,
):
    """Phase 1 inline extraction runs even when screenshot figures were extracted."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    resp = MagicMock()
    resp.status_code = 200
    resp.text = "<html><body>Short</body></html>"
    resp.url = "https://example.com/page"
    resp.raise_for_status = MagicMock()
    mock_get.return_value = resp

    mock_browser_cfg.return_value = {"mode": "local", "venv": "/tmp/venv"}
    mock_render.return_value = {
        "html": "<html><body>Rendered content that is long enough</body></html>",
        "screenshot_path": Path("/tmp/fake.png"),
        "final_url": "https://example.com/page",
        "tmpdir": None,
    }
    mock_web_figures.return_value = 2
    mock_html_images.return_value = 1

    with patch("knowledge_base.web.Path.exists", return_value=True):
        result = ingest_url(conn, "https://example.com/page")

    # Phase 1 always runs now — not skipped when screenshots extracted
    mock_html_images.assert_called_once()
    # Total = screenshot figures + inline figures
    assert result["figures_extracted"] == 3


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.web._extract_html_images")
@patch("knowledge_base.web._extract_web_figures")
@patch("knowledge_base.web._render_with_browser")
@patch("knowledge_base.web._get_browser_config")
@patch("knowledge_base.web.httpx.get")
def test_ingest_url_passes_rendered_html_to_extract_images(
    mock_get,
    mock_browser_cfg,
    mock_render,
    mock_web_figures,
    mock_html_images,
    tmp_path,
):
    """When browser fallback fires, rendered HTML is passed as extra_html_sources."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    resp = MagicMock()
    resp.status_code = 200
    resp.text = "<html><body>Short</body></html>"
    resp.url = "https://example.com/spa"
    resp.raise_for_status = MagicMock()
    mock_get.return_value = resp

    rendered_html = (
        "<html><body>Rendered with lots of content for the page</body></html>"
    )
    mock_browser_cfg.return_value = {"mode": "local", "venv": "/tmp/venv"}
    mock_render.return_value = {
        "html": rendered_html,
        "screenshot_path": None,
        "final_url": "https://example.com/spa-final",
        "tmpdir": None,
    }
    mock_web_figures.return_value = 0
    mock_html_images.return_value = 2

    ingest_url(conn, "https://example.com/spa")

    mock_html_images.assert_called_once()
    call_kwargs = mock_html_images.call_args
    # Base URL should use Playwright's final URL, not httpx response.url
    assert call_kwargs.kwargs.get("extra_html_sources") == [
        (rendered_html, "https://example.com/spa-final"),
    ]


_EMPTY_HTML_WITH_IMG = """<html>
<head><title>Image Only Page</title></head>
<body>
<img src="https://example.com/diagram.png" alt="Architecture diagram">
</body>
</html>"""


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
@patch("knowledge_base.web.httpx.get")
def test_ingest_url_extracts_images_even_when_no_text(
    mock_get, mock_stream, _mock_ip, mock_vision_cfg, mock_vision_call, tmp_path
):
    """Inline image extraction runs even when trafilatura returns no text."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG
    mock_vision_call.return_value = _FIGURE_RESPONSE

    resp = MagicMock()
    resp.status_code = 200
    resp.text = _EMPTY_HTML_WITH_IMG
    resp.url = "https://example.com/page"
    resp.raise_for_status = MagicMock()
    mock_get.return_value = resp

    png_bytes = _make_test_png()
    mock_stream.return_value = _mock_image_stream(png_bytes)

    # trafilatura returns empty for this HTML
    with patch("knowledge_base.web.trafilatura.extract", return_value=""):
        with patch(
            "knowledge_base.web.trafilatura.extract_metadata", return_value=None
        ):
            result = ingest_url(conn, "https://example.com/page")

    assert result.get("figures_extracted", 0) >= 1


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._get_vision_config")
@patch("knowledge_base.web.is_private_ip", return_value=False)
@patch("knowledge_base.web.httpx.stream")
@patch("knowledge_base.web.httpx.get")
def test_ingest_url_uses_response_url_for_base(
    mock_get, mock_stream, _mock_ip, mock_vision_cfg, mock_vision_call, tmp_path
):
    """Relative <img> src resolved against response.url (post-redirect), not original URL."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    mock_vision_cfg.return_value = _VISION_CFG
    mock_vision_call.return_value = _FIGURE_RESPONSE

    html = (
        "<html><head><title>Redirected</title></head><body>"
        "<p>" + "x" * 300 + "</p>"
        '<img src="/fig.png">'
        "</body></html>"
    )
    resp = MagicMock()
    resp.status_code = 200
    resp.text = html
    # Original URL was example.com/old, redirected to example.com/new
    resp.url = "https://example.com/new"
    resp.raise_for_status = MagicMock()
    mock_get.return_value = resp

    png_bytes = _make_test_png()
    mock_stream.return_value = _mock_image_stream(png_bytes)

    ingest_url(conn, "https://example.com/old")

    # The stream should have been called with the resolved URL using response.url
    call_args = mock_stream.call_args
    assert "example.com/fig.png" in str(call_args), (
        f"Expected resolved URL with example.com/fig.png, got {call_args}"
    )


# --- duplicate detection by content hash (issue #59, task 10) ---


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_ingest_detects_duplicate_by_hash(tmp_path):
    """If a file with same content hash exists under a different path, return info."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md1 = tmp_path / "dir1" / "paper.md"
    md1.parent.mkdir()
    md1.write_text("Identical content.\n")
    ingest_file(conn, md1)
    register_paper(conn, "Paper A", source_uri=str(md1.resolve()))

    # Copy same content to different path
    md2 = tmp_path / "dir2" / "paper.md"
    md2.parent.mkdir()
    md2.write_text("Identical content.\n")
    result = ingest_file(conn, md2)

    # Chunks are deduplicated by content_hash, so no new chunks added
    assert result["chunks_added"] == 0
    assert result["chunks_skipped"] >= 1
    assert result["duplicate_of_paper_id"] is not None


# ---------------------------------------------------------------------------
# PDF markdown extraction tests (Phase 2, issue #60)
# ---------------------------------------------------------------------------


def _make_mock_pymupdf4llm(pages_data):
    """Create a mock pymupdf4llm module that returns given pages_data."""
    mock_mod = MagicMock()
    mock_mod.__version__ = "1.27.2.1"
    mock_mod.to_markdown.return_value = pages_data
    return mock_mod


_SIMPLE_PAGES = [
    {
        "metadata": {"page": 1},
        "text": "## Introduction\nThis paper presents a novel approach.\n",
    },
    {
        "metadata": {"page": 2},
        "text": "## Methods\nWe used a transformer architecture.\n",
    },
]


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_extract_pdf_markdown_basic(tmp_path):
    """Mock pymupdf4llm returns structured markdown with page map."""
    import sys

    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    mock_mod = _make_mock_pymupdf4llm(_SIMPLE_PAGES)
    with patch.dict(sys.modules, {"pymupdf4llm": mock_mod}):
        text, page_map = _extract_pdf_markdown(pdf)

    assert "## Introduction" in text
    assert "## Methods" in text
    # page_map should have two entries
    assert len(page_map) == 2
    assert 1 in page_map.values()
    assert 2 in page_map.values()


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_extract_pdf_markdown_with_images(tmp_path):
    """image_dir is created and passed to to_markdown."""
    import sys

    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    image_dir = tmp_path / "images"

    mock_mod = _make_mock_pymupdf4llm(_SIMPLE_PAGES)
    with patch.dict(sys.modules, {"pymupdf4llm": mock_mod}):
        text, page_map = _extract_pdf_markdown(pdf, image_dir=image_dir)

    assert image_dir.exists()
    # Verify to_markdown was called with write_images=True
    call_kwargs = mock_mod.to_markdown.call_args
    assert call_kwargs[1]["write_images"] is True
    assert call_kwargs[1]["image_path"] == str(image_dir)


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_extract_pdf_markdown_fallback_on_import_error(tmp_path):
    """Falls back to flat extraction when pymupdf4llm is unavailable."""
    import sys

    pdf = tmp_path / "test.pdf"
    # Create a minimal valid PDF so fitz.open works
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello fallback world")
    doc.save(str(pdf))
    doc.close()

    # Remove pymupdf4llm from sys.modules to trigger ImportError
    saved = sys.modules.pop("pymupdf4llm", None)
    try:
        with patch.dict(sys.modules, {"pymupdf4llm": None}):
            text, page_map = _extract_pdf_markdown(pdf)
    finally:
        if saved is not None:
            sys.modules["pymupdf4llm"] = saved

    assert "Hello fallback world" in text
    assert page_map == {}


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_ingest_pdf_uses_markdown_chunks(tmp_path):
    """End-to-end: ingest a PDF → chunks have markdown structure + metadata."""
    import sys

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    mock_mod = _make_mock_pymupdf4llm(_SIMPLE_PAGES)
    with patch.dict(sys.modules, {"pymupdf4llm": mock_mod}):
        result = ingest_file(conn, pdf, source_type="pdf")

    assert result["chunks_added"] >= 1

    rows = conn.execute(
        "SELECT content, metadata FROM chunks WHERE source_type = 'pdf'"
    ).fetchall()
    assert len(rows) >= 1
    # At least one chunk should start with a heading
    assert any(r["content"].startswith("##") for r in rows)
    # Metadata should contain extractor tag
    for r in rows:
        meta = json.loads(r["metadata"])
        assert "extractor" in meta
        assert meta["extractor"].startswith("pymupdf4llm")


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_reingest_pdf_uses_markdown(tmp_path):
    """Reingest produces markdown-aware chunks for PDFs."""
    import sys

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    mock_mod = _make_mock_pymupdf4llm(_SIMPLE_PAGES)
    with patch.dict(sys.modules, {"pymupdf4llm": mock_mod}):
        ingest_file(conn, pdf, source_type="pdf")
        result = reingest_file(conn, pdf)

    assert result["chunks_added"] >= 1
    assert result["chunks_deleted"] >= 1

    rows = conn.execute(
        "SELECT metadata FROM chunks WHERE source_type = 'pdf'"
    ).fetchall()
    for r in rows:
        meta = json.loads(r["metadata"])
        assert "extractor" in meta


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_pdf_chunk_metadata_extractor(tmp_path):
    """PDF chunk metadata includes extractor version and page numbers."""
    import sys

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    mock_mod = _make_mock_pymupdf4llm(_SIMPLE_PAGES)
    with patch.dict(sys.modules, {"pymupdf4llm": mock_mod}):
        ingest_file(conn, pdf, source_type="pdf")

    rows = conn.execute(
        "SELECT metadata FROM chunks WHERE source_type = 'pdf'"
    ).fetchall()
    assert len(rows) >= 1
    for r in rows:
        meta = json.loads(r["metadata"])
        assert meta["extractor"] == "pymupdf4llm@1.27.2.1"
        assert "pages" in meta
        assert isinstance(meta["pages"], list)


def test_pdf_image_dir_content_hash(tmp_path):
    """pdf_image_dir uses content hash, so different content → different dirs."""
    pdf_a = tmp_path / "paper.pdf"
    pdf_a.write_bytes(b"%PDF content A")
    pdf_b = tmp_path / "paper2.pdf"
    pdf_b.write_bytes(b"%PDF content B")

    dir_a = pdf_image_dir(pdf_a)
    dir_b = pdf_image_dir(pdf_b)
    assert dir_a != dir_b

    # Same content, different filename → same hash prefix
    pdf_c = tmp_path / "copy.pdf"
    pdf_c.write_bytes(b"%PDF content A")
    dir_c = pdf_image_dir(pdf_c)
    # Hash part should match but stem differs
    assert dir_a.parent.name != dir_c.parent.name  # different stems
    # But both contain the same hash substring
    hash_a = dir_a.parent.name.split("_")[-1]
    hash_c = dir_c.parent.name.split("_")[-1]
    assert hash_a == hash_c


# --- Session ID tests ---


@patch("knowledge_base.ingest.embed", _fake_embed)
def test_ingest_file_with_session_id(tmp_path):
    """ingest_file stores session_id on all inserted chunks."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "doc.md"
    md.write_text("Hello world from session test")

    result = ingest_file(conn, md, session_id="test-session-42")
    assert result["chunks_added"] >= 1

    rows = conn.execute(
        "SELECT session_id FROM chunks WHERE source_uri = ?", (str(md.resolve()),)
    ).fetchall()
    assert all(r["session_id"] == "test-session-42" for r in rows)


@patch("knowledge_base.ingest.embed", _fake_embed)
def test_ingest_file_without_session_id(tmp_path):
    """Omitting session_id stores NULL — backwards compatible."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "doc.md"
    md.write_text("No session content here")

    ingest_file(conn, md)

    rows = conn.execute(
        "SELECT session_id FROM chunks WHERE source_uri = ?", (str(md.resolve()),)
    ).fetchall()
    assert all(r["session_id"] is None for r in rows)


@patch("knowledge_base.ingest.embed", _fake_embed)
def test_ingest_directory_shares_session_id(tmp_path):
    """All files in a directory ingest share the same session_id."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("Document A content for session")
    (docs / "b.md").write_text("Document B content for session")

    ingest_directory(conn, docs)

    rows = conn.execute("SELECT DISTINCT session_id FROM chunks").fetchall()
    session_ids = [r["session_id"] for r in rows]
    assert len(session_ids) == 1
    assert session_ids[0] is not None  # auto-generated UUID


@patch("knowledge_base.ingest.embed", _fake_embed)
def test_ingest_directory_explicit_session_id(tmp_path):
    """Caller-provided session_id is used instead of auto-generated UUID."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("Document A explicit session")
    (docs / "b.md").write_text("Document B explicit session")

    ingest_directory(conn, docs, session_id="my-custom-session")

    rows = conn.execute("SELECT DISTINCT session_id FROM chunks").fetchall()
    session_ids = [r["session_id"] for r in rows]
    assert session_ids == ["my-custom-session"]


@patch("knowledge_base.ingest.embed", _fake_embed)
def test_reingest_file_with_session_id(tmp_path):
    """reingest_file stores the new session_id on re-inserted chunks."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "doc.md"
    md.write_text("Original content for reingest")

    ingest_file(conn, md, session_id="old-session")

    # Modify and reingest with new session
    md.write_text("Updated content for reingest test")
    result = reingest_file(conn, md, session_id="new-session")
    assert result["chunks_added"] >= 1

    rows = conn.execute(
        "SELECT session_id FROM chunks WHERE source_uri = ?", (str(md.resolve()),)
    ).fetchall()
    assert all(r["session_id"] == "new-session" for r in rows)


# --- chunk_sessions join table tests (#139) ---


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_ingest_file_dedup_records_session(tmp_path):
    """When chunks are deduped, the new session is still recorded in chunk_sessions."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    test_file = tmp_path / "a.md"
    test_file.write_text("# Hello\n\nSome content here for testing.")

    # First ingest — creates chunks with session-1
    r1 = ingest_file(conn, test_file, session_id="session-1")
    assert r1["chunks_added"] > 0

    # Second ingest — same content, different session
    r2 = ingest_file(conn, test_file, session_id="session-2")
    assert r2["chunks_added"] == 0  # All deduped
    assert r2["chunks_skipped"] > 0

    # Both sessions should be recorded in chunk_sessions
    chunk_id = conn.execute(
        "SELECT id FROM chunks WHERE source_uri = ?", (str(test_file.resolve()),)
    ).fetchone()["id"]
    sessions = conn.execute(
        "SELECT session_id FROM chunk_sessions WHERE chunk_id = ? ORDER BY session_id",
        (chunk_id,),
    ).fetchall()
    assert len(sessions) == 2
    assert sessions[0]["session_id"] == "session-1"
    assert sessions[1]["session_id"] == "session-2"


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.web.httpx.get", _mock_httpx_get)
def test_ingest_url_dedup_records_session(tmp_path):
    """When URL chunks are deduped, the new session is still recorded."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    url = "https://example.com/test"
    r1 = ingest_url(conn, url, session_id="ws-1")
    assert r1["chunks_added"] > 0

    r2 = ingest_url(conn, url, session_id="ws-2")
    assert r2["chunks_added"] == 0
    assert r2["chunks_skipped"] > 0

    chunk_id = conn.execute(
        "SELECT id FROM chunks WHERE source_uri = ?", (url,)
    ).fetchone()["id"]
    sessions = conn.execute(
        "SELECT session_id FROM chunk_sessions WHERE chunk_id = ? ORDER BY session_id",
        (chunk_id,),
    ).fetchall()
    assert len(sessions) == 2
    assert sessions[0]["session_id"] == "ws-1"
    assert sessions[1]["session_id"] == "ws-2"


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_reingest_preserves_historical_sessions(tmp_path):
    """reingest_file preserves session associations from prior ingestions."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    test_file = tmp_path / "a.md"
    test_file.write_text("# Original\n\nOriginal content here.")
    ingest_file(conn, test_file, session_id="sess-1")

    # Simulate a second session via direct chunk_sessions insert
    chunk_id = conn.execute(
        "SELECT id FROM chunks WHERE source_uri = ?", (str(test_file.resolve()),)
    ).fetchone()["id"]
    conn.execute(
        "INSERT OR IGNORE INTO chunk_sessions (chunk_id, session_id) VALUES (?, 'sess-2')",
        (chunk_id,),
    )
    conn.commit()

    # Reingest with modified content
    test_file.write_text("# Updated\n\nUpdated content here.")
    result = reingest_file(conn, test_file, session_id="sess-3")
    assert result["chunks_added"] > 0

    # New chunks should have ALL three sessions: sess-1, sess-2, sess-3
    new_chunk_id = conn.execute(
        "SELECT id FROM chunks WHERE source_uri = ?", (str(test_file.resolve()),)
    ).fetchone()["id"]
    sessions = conn.execute(
        "SELECT session_id FROM chunk_sessions WHERE chunk_id = ? ORDER BY session_id",
        (new_chunk_id,),
    ).fetchall()
    session_ids = {r["session_id"] for r in sessions}
    assert session_ids == {"sess-1", "sess-2", "sess-3"}


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_multi_session_dedup_co_occurrence(tmp_path):
    """End-to-end: deduped chunks still produce correct co-occurrence counts."""
    from knowledge_base.db import co_occurrence_pairs

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("# Paper A\n\nContent of paper A.")
    b.write_text("# Paper B\n\nContent of paper B.")

    # Session 1: ingest both files
    ingest_file(conn, a, session_id="s1")
    ingest_file(conn, b, session_id="s1")

    # Session 2: re-ingest same files (all deduped)
    r_a = ingest_file(conn, a, session_id="s2")
    r_b = ingest_file(conn, b, session_id="s2")
    assert r_a["chunks_skipped"] > 0
    assert r_b["chunks_skipped"] > 0

    # co_occurrence should see 2 shared sessions for (a, b)
    pairs = co_occurrence_pairs(conn, min_sessions=1)
    assert len(pairs) == 1
    assert pairs[0]["co_sessions"] == 2

    # min_sessions=2 should still include them
    pairs_2 = co_occurrence_pairs(conn, min_sessions=2)
    assert len(pairs_2) == 1

    # min_sessions=3 should exclude them
    pairs_3 = co_occurrence_pairs(conn, min_sessions=3)
    assert len(pairs_3) == 0


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_embed_failure_does_not_leave_orphan_session_rows(tmp_path):
    """If _embed_with_config raises, no chunk_sessions rows from the failed call persist.

    Scenario: file has 2 chunks. First ingest succeeds (session-1). File is then
    modified to keep one existing chunk and add a new one. Second ingest (session-2)
    deduplicates the first chunk (writing a chunk_sessions row), then calls
    _embed_with_config for the new chunk — which raises. The chunk_sessions row
    from the dedup phase must not persist.  Regression test for #180.
    """
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    test_file = tmp_path / "doc.md"
    # Two chunks worth of content (each chunk is ~2000 chars by default)
    chunk_a = "# Shared Section\n\n" + ("Existing content. " * 120)
    chunk_b = "# Original Section\n\n" + ("Original content. " * 120)
    test_file.write_text(chunk_a + "\n\n" + chunk_b)

    # First ingest — succeeds
    r1 = ingest_file(conn, test_file, session_id="session-1")
    assert r1["chunks_added"] >= 2, f"Expected >=2 chunks, got {r1['chunks_added']}"

    # Verify session-1 rows exist
    s1_rows = conn.execute(
        "SELECT COUNT(*) as cnt FROM chunk_sessions WHERE session_id = 'session-1'"
    ).fetchone()["cnt"]
    assert s1_rows >= 2

    # Modify file: keep chunk_a (will be deduped), replace chunk_b (new chunk)
    chunk_c = "# Brand New Section\n\n" + ("Completely new content. " * 120)
    test_file.write_text(chunk_a + "\n\n" + chunk_c)

    # Patch _embed_with_config to raise after chunk_sessions writes happen
    with patch(
        "knowledge_base.ingest._embed_with_config",
        side_effect=RuntimeError("Ollama down"),
    ):
        try:
            ingest_file(conn, test_file, session_id="session-2")
        except RuntimeError:
            pass  # Expected

    # The critical assertion: no session-2 rows should exist in chunk_sessions
    s2_rows = conn.execute(
        "SELECT COUNT(*) as cnt FROM chunk_sessions WHERE session_id = 'session-2'"
    ).fetchone()["cnt"]
    assert s2_rows == 0, (
        f"Expected 0 chunk_sessions rows for failed session-2, got {s2_rows}. "
        "Orphan session rows leaked from a failed embed call."
    )

    # Session-1 rows must be unaffected
    s1_after = conn.execute(
        "SELECT COUNT(*) as cnt FROM chunk_sessions WHERE session_id = 'session-1'"
    ).fetchone()["cnt"]
    assert s1_after == s1_rows, "session-1 rows were corrupted by the failed session-2"

    # No new chunks should have been inserted
    total_chunks = conn.execute("SELECT COUNT(*) as cnt FROM chunks").fetchone()["cnt"]
    assert total_chunks == r1["chunks_added"], "Chunks leaked from failed embed call"


# --- chunk_strategy dispatch tests ---


def test_get_chunk_strategy_default(tmp_path):
    """No config row returns 'mechanical'."""
    from knowledge_base.ingest import _get_chunk_strategy

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.commit()
    assert _get_chunk_strategy(conn) == "mechanical"


def test_get_chunk_strategy_reads_config(tmp_path):
    """Config set to 'semantic' returns 'semantic'."""
    from knowledge_base.ingest import _get_chunk_strategy

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('chunk_strategy', 'semantic')"
    )
    conn.commit()
    assert _get_chunk_strategy(conn) == "semantic"


@patch("knowledge_base.ingest.embed", _fake_embed)
def test_ingest_file_mechanical_default(tmp_path):
    """Default config: chunks have chunk_strategy='mechanical'."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md = tmp_path / "doc.md"
    md.write_text("Simple content for mechanical test")

    ingest_file(conn, md)

    rows = conn.execute(
        "SELECT chunk_strategy FROM chunks WHERE source_uri = ?",
        (str(md.resolve()),),
    ).fetchall()
    assert len(rows) >= 1
    assert all(r["chunk_strategy"] == "mechanical" for r in rows)


@patch("knowledge_base.ingest.embed", _fake_embed)
def test_ingest_file_semantic_nonpdf(tmp_path):
    """Config 'semantic' with non-PDF file still uses mechanical chunking."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('chunk_strategy', 'semantic')"
    )
    conn.commit()

    md = tmp_path / "doc.md"
    md.write_text("Non-PDF content stays mechanical")

    ingest_file(conn, md)

    rows = conn.execute(
        "SELECT chunk_strategy FROM chunks WHERE source_uri = ?",
        (str(md.resolve()),),
    ).fetchall()
    assert len(rows) >= 1
    assert all(r["chunk_strategy"] == "mechanical" for r in rows)


# --- Zero-norm embedding handling (#150) ---


def _fake_embed_with_zero(texts, model="bge-m3", expected_dim=None, **_kwargs):
    """Return None for the second text to simulate a zero-norm embedding."""
    dim = expected_dim if expected_dim is not None else DEFAULT_EMBED_DIM
    result = []
    for i, _ in enumerate(texts):
        if i == 1:
            result.append(None)
        else:
            result.append([0.1] * dim)
    return result


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed_with_zero)
def test_ingest_skips_vec_for_none_embedding(tmp_path):
    """Chunks with None embeddings get FTS-indexed but no vec row."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    md_file = tmp_path / "multi.md"
    # Each section needs >1000 chars to force separate chunks
    md_file.write_text(
        "# Section A\n\n" + "First chunk content. " * 80 + "\n\n"
        "# Section B\n\n" + "Second chunk zero-norm. " * 80 + "\n\n"
        "# Section C\n\n" + "Third chunk content ok. " * 80 + "\n"
    )

    ingest_file(conn, md_file)

    # All chunks should be in FTS (text table)
    chunk_count = conn.execute("SELECT COUNT(*) as c FROM chunks").fetchone()["c"]
    assert chunk_count >= 3

    # But vec table should have one fewer entry than chunks
    vec_count = conn.execute("SELECT COUNT(*) as c FROM chunks_vec").fetchone()["c"]
    assert vec_count == chunk_count - 1


# ---------------------------------------------------------------------------
# Phase 3: per-element canvas/SVG screenshot capture (#132)
# ---------------------------------------------------------------------------


class TestElementCapture:
    """Phase 3: per-element canvas/SVG screenshot capture."""

    def test_render_with_browser_produces_element_captures(self, tmp_path):
        """_render_with_browser returns element captures when present."""
        import json as _json

        elements_manifest = [
            {"file": "element_0.png", "tag": "canvas", "width": 400, "height": 300},
        ]

        def _fake_subprocess_run(cmd, **kwargs):
            out_dir = Path(cmd[3])  # 4th arg is output_dir
            (out_dir / "page.html").write_text("<html></html>")
            (out_dir / "screenshot.png").write_bytes(b"\x89PNG fake")
            (out_dir / "elements.json").write_text(_json.dumps(elements_manifest))
            (out_dir / "element_0.png").write_bytes(_make_test_png(400, 300))
            return MagicMock(returncode=0)

        with (
            patch(
                "knowledge_base.web._find_venv_python",
                return_value=Path("/usr/bin/python3"),
            ),
            patch(
                "knowledge_base.web.subprocess.run",
                side_effect=_fake_subprocess_run,
            ),
        ):
            result = _render_with_browser(
                "https://example.com", {"venv": "/fake", "mode": "local"}
            )

        assert result is not None
        assert result["element_captures"] == [
            {
                "path": result["tmpdir"] / "element_0.png",
                "tag": "canvas",
                "width": 400,
                "height": 300,
            },
        ]

    @patch("knowledge_base.folder_summaries.embed", _fake_embed)
    @patch("knowledge_base.ingest.embed", _fake_embed)
    @patch("knowledge_base.vision._vision_call")
    @patch("knowledge_base.vision._get_vision_config")
    def test_extract_element_captures_basic(
        self, mock_vision_cfg, mock_vision_call, tmp_path
    ):
        """Per-element captures are sent to vision and stored as figure chunks."""
        mock_vision_cfg.return_value = {
            "base_url": "http://localhost:11434",
            "model": "gemma3:27b",
        }
        mock_vision_call.return_value = [
            {
                "description": "An interactive D3.js bar chart showing quarterly revenue.",
                "figure_type": "chart",
                "title": "Quarterly Revenue",
            }
        ]

        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_schema(conn)
        png_bytes = _make_test_png(400, 300)
        capture_path = tmp_path / "element_0.png"
        capture_path.write_bytes(png_bytes)

        captures = [
            {"path": capture_path, "tag": "canvas", "width": 400, "height": 300}
        ]

        from knowledge_base.web import _extract_element_captures

        count = _extract_element_captures(
            conn, "https://example.com/dashboard", captures
        )

        assert count == 1
        row = conn.execute(
            "SELECT * FROM chunks WHERE source_type = 'figure' AND chunk_index >= 3000000"
        ).fetchone()
        assert row is not None
        meta = json.loads(row["metadata"])
        assert meta["figure_type"] == "chart"
        assert meta["element_tag"] == "canvas"
        assert "D3.js bar chart" in row["content"]

    @patch("knowledge_base.folder_summaries.embed", _fake_embed)
    @patch("knowledge_base.ingest.embed", _fake_embed)
    @patch("knowledge_base.vision._vision_call")
    @patch("knowledge_base.vision._get_vision_config")
    @patch("knowledge_base.web.is_private_ip", return_value=False)
    @patch("knowledge_base.web._render_with_browser")
    @patch("knowledge_base.web._get_browser_config")
    @patch("knowledge_base.web.httpx")
    def test_ingest_url_extracts_element_captures(
        self,
        mock_httpx,
        mock_browser_cfg,
        mock_render,
        _mock_ip,
        mock_vision_cfg,
        mock_vision_call,
        tmp_path,
    ):
        """ingest_url processes element captures from browser rendering."""
        mock_vision_cfg.return_value = {
            "base_url": "http://localhost:11434",
            "model": "gemma3:27b",
        }
        mock_vision_call.return_value = [
            {
                "description": "A D3 scatter plot of gene expression levels.",
                "figure_type": "chart",
                "title": "Expression Scatter",
            }
        ]

        # httpx returns minimal content (triggers browser fallback)
        mock_resp = MagicMock()
        mock_resp.text = "<html><body>short</body></html>"
        mock_resp.url = "https://example.com/viz"
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

        mock_browser_cfg.return_value = {"venv": "/fake", "mode": "local"}

        # Browser render returns element captures
        tmpdir = tmp_path / "browser_out"
        tmpdir.mkdir()
        capture_png = tmpdir / "element_0.png"
        capture_png.write_bytes(_make_test_png(500, 400))

        mock_render.return_value = {
            "html": "<html><canvas width='500' height='400'></canvas></html>",
            "screenshot_path": None,
            "final_url": "https://example.com/viz",
            "tmpdir": tmpdir,
            "element_captures": [
                {"path": capture_png, "tag": "canvas", "width": 500, "height": 400},
            ],
        }

        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_schema(conn)

        result = ingest_url(conn, "https://example.com/viz")

        assert result["figures_extracted"] >= 1
        row = conn.execute(
            "SELECT * FROM chunks WHERE source_type = 'figure'"
            " AND chunk_index >= 3000000"
        ).fetchone()
        assert row is not None
        assert "scatter plot" in row["content"].lower()

    @patch(
        "knowledge_base.vision._get_vision_config",
        side_effect=RuntimeError("no vision"),
    )
    def test_extract_element_captures_no_vision(self, _mock_cfg, tmp_path):
        """Returns 0 when vision is not configured."""
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_schema(conn)
        from knowledge_base.web import _extract_element_captures

        count = _extract_element_captures(
            conn,
            "https://x.com",
            [
                {
                    "path": tmp_path / "x.png",
                    "tag": "canvas",
                    "width": 100,
                    "height": 100,
                }
            ],
        )
        assert count == 0

    def test_extract_element_captures_empty_list(self, tmp_path):
        """Returns 0 for empty captures list."""
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_schema(conn)
        from knowledge_base.web import _extract_element_captures

        count = _extract_element_captures(conn, "https://x.com", [])
        assert count == 0

    @patch("knowledge_base.folder_summaries.embed", _fake_embed)
    @patch("knowledge_base.ingest.embed", _fake_embed)
    @patch("knowledge_base.vision._vision_call")
    @patch("knowledge_base.vision._get_vision_config")
    def test_extract_element_captures_stale_cleanup(
        self, mock_vision_cfg, mock_vision_call, tmp_path
    ):
        """Re-extraction deletes stale element-capture chunks."""
        mock_vision_cfg.return_value = {
            "base_url": "http://localhost:11434",
            "model": "gemma3:27b",
        }
        mock_vision_call.return_value = [
            {"description": "Old chart.", "figure_type": "chart", "title": "Old"}
        ]

        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_schema(conn)
        png = tmp_path / "el.png"
        png.write_bytes(_make_test_png(200, 200))

        from knowledge_base.web import _extract_element_captures

        _extract_element_captures(
            conn,
            "https://x.com",
            [{"path": png, "tag": "canvas", "width": 200, "height": 200}],
        )
        old_count = conn.execute(
            "SELECT COUNT(*) as c FROM chunks WHERE chunk_index >= 3000000"
        ).fetchone()["c"]
        assert old_count == 1

        # Re-extract with new description
        mock_vision_call.return_value = [
            {
                "description": "New chart with updated data.",
                "figure_type": "chart",
                "title": "New",
            }
        ]
        _extract_element_captures(
            conn,
            "https://x.com",
            [{"path": png, "tag": "canvas", "width": 200, "height": 200}],
        )
        rows = conn.execute(
            "SELECT content FROM chunks WHERE chunk_index >= 3000000"
        ).fetchall()
        assert len(rows) == 1
        assert "New chart" in rows[0]["content"]

    @patch("knowledge_base.folder_summaries.embed", _fake_embed)
    @patch("knowledge_base.ingest.embed", _fake_embed)
    @patch("knowledge_base.vision._vision_call")
    @patch("knowledge_base.vision._get_vision_config")
    def test_extract_element_captures_svg(
        self, mock_vision_cfg, mock_vision_call, tmp_path
    ):
        """SVG elements use svg_capture figure_type when vision returns empty type."""
        mock_vision_cfg.return_value = {
            "base_url": "http://localhost:11434",
            "model": "gemma3:27b",
        }
        mock_vision_call.return_value = [
            {"description": "An SVG flow diagram.", "figure_type": "", "title": "Flow"}
        ]

        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_schema(conn)
        png = tmp_path / "el.png"
        png.write_bytes(_make_test_png(300, 200))

        from knowledge_base.web import _extract_element_captures

        _extract_element_captures(
            conn,
            "https://x.com",
            [{"path": png, "tag": "svg", "width": 300, "height": 200}],
        )
        meta = json.loads(
            conn.execute(
                "SELECT metadata FROM chunks WHERE chunk_index >= 3000000"
            ).fetchone()["metadata"]
        )
        assert meta["figure_type"] == "svg_capture"
        assert meta["element_tag"] == "svg"

    def test_extract_element_captures_missing_png(self, tmp_path):
        """Skips captures whose PNG file does not exist."""
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_schema(conn)
        from knowledge_base.web import _extract_element_captures

        with patch(
            "knowledge_base.vision._get_vision_config",
            return_value={
                "base_url": "http://localhost:11434",
                "model": "gemma3:27b",
            },
        ):
            count = _extract_element_captures(
                conn,
                "https://x.com",
                [
                    {
                        "path": tmp_path / "nonexistent.png",
                        "tag": "canvas",
                        "width": 200,
                        "height": 200,
                    }
                ],
            )
        assert count == 0
