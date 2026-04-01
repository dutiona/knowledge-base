"""Tests for paper registration, retrieval, relationships, and suggestions."""

import hashlib
from unittest.mock import patch

import pytest

from knowledge_base.db import DEFAULT_EMBED_DIM, get_connection, init_schema
from knowledge_base.ingest import ingest_file
from knowledge_base.papers import (
    add_relationship,
    compute_file_hash,
    get_paper,
    get_paper_chunks,
    get_paper_paths,
    get_paper_source_uri,
    get_relationships,
    register_paper,
    relocate_paper,
    suggest_relationships,
)


def _fake_embed(texts, model="bge-m3", expected_dim=None, **_kwargs):
    dim = expected_dim if expected_dim is not None else DEFAULT_EMBED_DIM
    return [[0.1] * dim for _ in texts]


def _setup(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    return conn


# --- register_paper / get_paper ---


def test_register_and_get_paper(tmp_path):
    conn = _setup(tmp_path)
    result = register_paper(
        conn, "Attention Is All You Need", ["Vaswani, A."], 2017, "NeurIPS"
    )
    assert "paper_id" in result

    papers = get_paper(conn, paper_id=result["paper_id"])
    assert len(papers) == 1
    assert papers[0]["title"] == "Attention Is All You Need"
    assert papers[0]["authors"] == ["Vaswani, A."]
    assert papers[0]["year"] == 2017


def test_get_paper_by_doi(tmp_path):
    conn = _setup(tmp_path)
    register_paper(conn, "Test Paper", doi="10.1234/test")
    papers = get_paper(conn, doi="10.1234/test")
    assert len(papers) == 1
    assert papers[0]["doi"] == "10.1234/test"


def test_get_paper_by_title_pattern(tmp_path):
    conn = _setup(tmp_path)
    register_paper(conn, "Deep Residual Learning for Image Recognition")
    register_paper(conn, "Attention Is All You Need")

    papers = get_paper(conn, title_pattern="Residual")
    assert len(papers) == 1
    assert "Residual" in papers[0]["title"]


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_register_paper_links_chunks(tmp_path):
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("Abstract of the paper about transformers.\n")
    ingest_file(conn, md)

    result = register_paper(conn, "Transformer Paper", source_uri=str(md.resolve()))
    assert result["abstract_chunk_id"] is not None

    papers = get_paper(conn, paper_id=result["paper_id"])
    assert len(papers[0]["chunks"]) >= 1


def test_get_paper_no_match(tmp_path):
    conn = _setup(tmp_path)
    assert get_paper(conn, paper_id=999) == []
    assert get_paper(conn, doi="nonexistent") == []
    assert get_paper(conn, title_pattern="nonexistent") == []
    assert get_paper(conn) == []


def test_get_paper_title_pattern_escapes_like_wildcards(tmp_path):
    """LIKE wildcards % and _ in user input must be treated as literals."""
    conn = _setup(tmp_path)
    register_paper(conn, "100% Accurate Object Detection")
    register_paper(conn, "Totally Different Topic")

    # '%' in search should be literal, not a wildcard
    papers = get_paper(conn, title_pattern="100%")
    assert len(papers) == 1
    assert papers[0]["title"] == "100% Accurate Object Detection"

    # '_' in search should be literal single-char, not wildcard
    register_paper(conn, "A_B Method for Classification")
    register_paper(conn, "AXB Method for Classification")
    papers = get_paper(conn, title_pattern="A_B")
    assert len(papers) == 1
    assert papers[0]["title"] == "A_B Method for Classification"


# --- add_relationship / get_relationships ---


def test_add_and_get_relationship(tmp_path):
    conn = _setup(tmp_path)
    p1 = register_paper(conn, "Paper A")["paper_id"]
    p2 = register_paper(conn, "Paper B")["paper_id"]

    result = add_relationship(conn, p1, p2, "extends", 0.9)
    assert result["relation_type"] == "extends"

    rels = get_relationships(conn, p1)
    assert len(rels) == 1
    assert rels[0]["target_title"] == "Paper B"
    assert rels[0]["confidence"] == 0.9


def test_relationship_upsert(tmp_path):
    conn = _setup(tmp_path)
    p1 = register_paper(conn, "Paper A")["paper_id"]
    p2 = register_paper(conn, "Paper B")["paper_id"]

    add_relationship(conn, p1, p2, "cites", 0.5)
    add_relationship(conn, p1, p2, "cites", 0.95)  # upsert

    rels = get_relationships(conn, p1)
    assert len(rels) == 1
    assert rels[0]["confidence"] == 0.95


def test_relationship_invalid_type(tmp_path):
    conn = _setup(tmp_path)
    p1 = register_paper(conn, "Paper A")["paper_id"]
    p2 = register_paper(conn, "Paper B")["paper_id"]

    result = add_relationship(conn, p1, p2, "invalid_type")
    assert "error" in result


def test_relationship_direction_filter(tmp_path):
    conn = _setup(tmp_path)
    p1 = register_paper(conn, "Paper A")["paper_id"]
    p2 = register_paper(conn, "Paper B")["paper_id"]
    add_relationship(conn, p1, p2, "cites")

    outgoing = get_relationships(conn, p1, direction="outgoing")
    assert len(outgoing) == 1

    incoming = get_relationships(conn, p1, direction="incoming")
    assert len(incoming) == 0

    incoming_p2 = get_relationships(conn, p2, direction="incoming")
    assert len(incoming_p2) == 1


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_relationship_with_evidence(tmp_path):
    conn = _setup(tmp_path)
    md = tmp_path / "evidence.md"
    md.write_text("Paper A extends Paper B by adding attention.\n")
    ingest_file(conn, md)
    chunk_id = conn.execute("SELECT id FROM chunks LIMIT 1").fetchone()["id"]

    p1 = register_paper(conn, "Paper A")["paper_id"]
    p2 = register_paper(conn, "Paper B")["paper_id"]
    add_relationship(conn, p1, p2, "extends", evidence_chunk_id=chunk_id)

    rels = get_relationships(conn, p1)
    assert rels[0]["evidence_chunk_id"] == chunk_id
    assert "attention" in rels[0]["evidence_content"]


# --- suggest_relationships ---


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_suggest_relationships_by_doi(tmp_path):
    conn = _setup(tmp_path)

    # Ingest a file that mentions a DOI
    md = tmp_path / "citing.md"
    md.write_text("We build on the work of 10.1234/target for our method.\n")
    ingest_file(conn, md)

    # Register the citing paper linked to the ingested file
    p1 = register_paper(conn, "Citing Paper", source_uri=str(md.resolve()))["paper_id"]
    # Register the target paper with matching DOI
    register_paper(conn, "Target Paper", doi="10.1234/target")

    result = suggest_relationships(conn, p1)
    assert len(result["suggestions"]) >= 1
    assert result["suggestions"][0]["match_method"] == "doi"
    assert result["suggestions"][0]["target_title"] == "Target Paper"


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_suggest_relationships_by_title(tmp_path):
    conn = _setup(tmp_path)

    md = tmp_path / "citing.md"
    md.write_text("We extend Attention Is All You Need with sparse patterns.\n")
    ingest_file(conn, md)

    p1 = register_paper(conn, "Sparse Attention", source_uri=str(md.resolve()))[
        "paper_id"
    ]
    register_paper(conn, "Attention Is All You Need")

    result = suggest_relationships(conn, p1)
    # Exact substring should now match via FTS5 as title_fts
    assert len(result["suggestions"]) >= 1
    assert result["suggestions"][0]["match_method"] == "title_words"


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_suggest_skips_existing_relationships(tmp_path):
    conn = _setup(tmp_path)

    md = tmp_path / "citing.md"
    md.write_text("References 10.1234/existing in the bibliography.\n")
    ingest_file(conn, md)

    p1 = register_paper(conn, "Paper A", source_uri=str(md.resolve()))["paper_id"]
    p2 = register_paper(conn, "Paper B", doi="10.1234/existing")["paper_id"]
    add_relationship(conn, p1, p2, "cites")

    result = suggest_relationships(conn, p1)
    assert len(result["suggestions"]) == 0


def test_relationship_invalid_direction(tmp_path):
    conn = _setup(tmp_path)
    p1 = register_paper(conn, "Paper A")["paper_id"]
    rels = get_relationships(conn, p1, direction="invalid")
    assert rels == []


def test_relationship_confidence_range(tmp_path):
    conn = _setup(tmp_path)
    p1 = register_paper(conn, "Paper A")["paper_id"]
    p2 = register_paper(conn, "Paper B")["paper_id"]

    result = add_relationship(conn, p1, p2, "cites", confidence=1.5)
    assert "error" in result

    result = add_relationship(conn, p1, p2, "cites", confidence=-0.1)
    assert "error" in result


def test_suggest_no_chunks(tmp_path):
    conn = _setup(tmp_path)
    p1 = register_paper(conn, "Lonely Paper")["paper_id"]
    result = suggest_relationships(conn, p1)
    assert result == {"suggestions": [], "unmatched": []}


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_suggest_title_fts5_fuzzy_match(tmp_path):
    """FTS5 should match even when title words appear in different order or with extra words."""
    conn = _setup(tmp_path)

    md = tmp_path / "citing.md"
    # Text mentions the title words but not as an exact substring
    md.write_text(
        "We build on residual learning techniques for deep image recognition tasks.\n"
    )
    ingest_file(conn, md)

    p1 = register_paper(conn, "Our Method", source_uri=str(md.resolve()))["paper_id"]
    register_paper(conn, "Deep Residual Learning for Image Recognition")

    result = suggest_relationships(conn, p1)
    title_matches = [
        s for s in result["suggestions"] if s["match_method"] == "title_words"
    ]
    assert len(title_matches) >= 1
    assert (
        title_matches[0]["target_title"]
        == "Deep Residual Learning for Image Recognition"
    )
    assert 0.3 <= title_matches[0]["confidence"] <= 0.7


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_suggest_title_fts5_short_title_skipped(tmp_path):
    """Titles with fewer than 3 words should not be FTS5-matched (too ambiguous)."""
    conn = _setup(tmp_path)

    md = tmp_path / "citing.md"
    md.write_text("We study attention mechanisms in deep learning.\n")
    ingest_file(conn, md)

    p1 = register_paper(conn, "Our Paper", source_uri=str(md.resolve()))["paper_id"]
    register_paper(conn, "Deep Learning")  # 2-word title

    result = suggest_relationships(conn, p1)
    title_matches = [
        s for s in result["suggestions"] if s["match_method"] == "title_words"
    ]
    assert len(title_matches) == 0


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_suggest_author_year_parenthetical(tmp_path):
    """Match '(Vaswani et al., 2017)' style citations."""
    conn = _setup(tmp_path)

    md = tmp_path / "citing.md"
    md.write_text(
        "The transformer architecture (Vaswani et al., 2017) revolutionized NLP.\n"
    )
    ingest_file(conn, md)

    p1 = register_paper(conn, "Our NLP Paper", source_uri=str(md.resolve()))["paper_id"]
    register_paper(
        conn, "Attention Is All You Need", ["Vaswani, A.", "Shazeer, N."], 2017
    )

    result = suggest_relationships(conn, p1)
    author_matches = [
        s for s in result["suggestions"] if s["match_method"] == "author_year"
    ]
    assert len(author_matches) >= 1
    assert author_matches[0]["target_title"] == "Attention Is All You Need"
    assert 0.3 <= author_matches[0]["confidence"] <= 0.6


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_suggest_author_year_narrative(tmp_path):
    """Match 'Vaswani et al. (2017)' narrative style citations."""
    conn = _setup(tmp_path)

    md = tmp_path / "citing.md"
    md.write_text("Vaswani et al. (2017) introduced the transformer architecture.\n")
    ingest_file(conn, md)

    p1 = register_paper(conn, "Follow-up Paper", source_uri=str(md.resolve()))[
        "paper_id"
    ]
    register_paper(conn, "Attention Is All You Need", ["Vaswani, A."], 2017)

    result = suggest_relationships(conn, p1)
    author_matches = [
        s for s in result["suggestions"] if s["match_method"] == "author_year"
    ]
    assert len(author_matches) >= 1


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_suggest_unmatched_dois_reported(tmp_path):
    """DOIs that don't match any registered paper should appear in unmatched."""
    conn = _setup(tmp_path)

    md = tmp_path / "citing.md"
    md.write_text("We cite 10.1234/known and 10.5678/unknown in our work.\n")
    ingest_file(conn, md)

    p1 = register_paper(conn, "Citing Paper", source_uri=str(md.resolve()))["paper_id"]
    register_paper(conn, "Known Paper", doi="10.1234/known")

    result = suggest_relationships(conn, p1)
    # result is now a dict with 'suggestions' and 'unmatched'
    assert any(u["doi"] == "10.5678/unknown" for u in result["unmatched"])


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_suggest_returns_structured_result(tmp_path):
    """suggest_relationships should return a dict with 'suggestions' and 'unmatched' keys."""
    conn = _setup(tmp_path)

    md = tmp_path / "citing.md"
    md.write_text("Some text referencing 10.9999/orphan in passing.\n")
    ingest_file(conn, md)

    p1 = register_paper(conn, "Paper X", source_uri=str(md.resolve()))["paper_id"]

    result = suggest_relationships(conn, p1)
    assert isinstance(result, dict)
    assert "suggestions" in result
    assert "unmatched" in result


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_suggest_author_year_compound_surname(tmp_path):
    """Match compound surnames like O'Malley and MacDonald."""
    conn = _setup(tmp_path)

    md = tmp_path / "citing.md"
    md.write_text(
        "Prior work by O'Malley et al. (2020) and (MacDonald, 2019) is relevant.\n"
    )
    ingest_file(conn, md)

    p1 = register_paper(conn, "Our Paper", source_uri=str(md.resolve()))["paper_id"]
    register_paper(conn, "O'Malley Study", ["O'Malley, Sean"], 2020)
    register_paper(conn, "MacDonald Analysis", ["MacDonald, Ian"], 2019)

    result = suggest_relationships(conn, p1)
    author_matches = [
        s for s in result["suggestions"] if s["match_method"] == "author_year"
    ]
    matched_titles = {s["target_title"] for s in author_matches}
    assert "O'Malley Study" in matched_titles
    assert "MacDonald Analysis" in matched_titles


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_suggest_title_no_substring_false_positive(tmp_path):
    """Title word matching should not match 'net' inside 'internet'."""
    conn = _setup(tmp_path)

    md = tmp_path / "citing.md"
    md.write_text("We studied internet protocols and network architecture.\n")
    ingest_file(conn, md)

    p1 = register_paper(conn, "Internet Paper", source_uri=str(md.resolve()))[
        "paper_id"
    ]
    # "net" should NOT match via substring of "internet"
    register_paper(conn, "The Net Effect on Random Systems")

    result = suggest_relationships(conn, p1)
    title_matches = [
        s for s in result["suggestions"] if s["match_method"] == "title_words"
    ]
    # "net", "effect", "random", "systems" — only "systems" is absent,
    # but "net" should not match as a word in "internet"
    false_positives = [s for s in title_matches if "Net Effect" in s["target_title"]]
    assert len(false_positives) == 0


# --- paper_paths table ---


def test_paper_paths_table_exists(tmp_path):
    conn = _setup(tmp_path)
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='paper_paths'"
    ).fetchone()
    assert row is not None
    assert "paper_id" in row[0]
    assert "content_hash" in row[0]
    assert "is_primary" in row[0]


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_migrate_paper_paths_from_existing(tmp_path):
    """Migration populates paper_paths from papers with abstract_chunk_id."""
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("Some paper content.\n")
    ingest_file(conn, md)
    source_uri = str(md.resolve())

    conn.execute(
        "INSERT INTO papers (title, abstract_chunk_id) VALUES (?, (SELECT id FROM chunks WHERE source_uri = ? LIMIT 1))",
        ("Old Paper", source_uri),
    )
    conn.commit()

    # Clear paper_paths to simulate pre-migration state
    conn.execute("DELETE FROM paper_paths")
    conn.commit()

    from knowledge_base.db import _migrate_paper_paths

    _migrate_paper_paths(conn)

    row = conn.execute("SELECT * FROM paper_paths").fetchone()
    assert row is not None
    assert row["path"] == source_uri
    assert row["is_primary"] == 1


# --- core helpers ---


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_get_paper_paths(tmp_path):
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("Content.\n")
    ingest_file(conn, md)
    source_uri = str(md.resolve())

    pid = register_paper(conn, "Test", source_uri=source_uri)["paper_id"]

    paths = get_paper_paths(conn, pid)
    assert len(paths) == 1
    assert paths[0]["path"] == source_uri
    assert paths[0]["is_primary"] == 1


def test_compute_file_hash(tmp_path):
    f = tmp_path / "test.pdf"
    f.write_bytes(b"hello world")
    h = compute_file_hash(f)
    assert len(h) == 64
    assert h == hashlib.sha256(b"hello world").hexdigest()


def test_compute_file_hash_large_file(tmp_path):
    f = tmp_path / "large.bin"
    f.write_bytes(b"x" * 20000)
    h = compute_file_hash(f)
    assert h == hashlib.sha256(b"x" * 20000).hexdigest()


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_get_paper_source_uri(tmp_path):
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("Content.\n")
    ingest_file(conn, md)
    source_uri = str(md.resolve())

    pid = register_paper(conn, "Test", source_uri=source_uri)["paper_id"]

    assert get_paper_source_uri(conn, pid) == source_uri


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_get_paper_source_uri_fallback(tmp_path):
    """Falls back to abstract_chunk_id hop when no paper_paths entry."""
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("Content.\n")
    ingest_file(conn, md)
    source_uri = str(md.resolve())

    pid = register_paper(conn, "Test", source_uri=source_uri)["paper_id"]
    conn.execute("DELETE FROM paper_paths WHERE paper_id = ?", (pid,))
    conn.commit()

    assert get_paper_source_uri(conn, pid) == source_uri


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_get_paper_chunks(tmp_path):
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("Content of the paper.\n")
    ingest_file(conn, md)
    source_uri = str(md.resolve())

    pid = register_paper(conn, "Test", source_uri=source_uri)["paper_id"]

    chunks = get_paper_chunks(conn, pid)
    assert len(chunks) >= 1
    assert "Content of the paper" in chunks[0]["content"]


# --- register_paper populates paper_paths ---


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_register_paper_creates_paper_path(tmp_path):
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("Abstract.\n")
    ingest_file(conn, md)

    result = register_paper(conn, "Test Paper", source_uri=str(md.resolve()))
    paths = get_paper_paths(conn, result["paper_id"])
    assert len(paths) == 1
    assert paths[0]["path"] == str(md.resolve())
    assert paths[0]["is_primary"] == 1
    assert paths[0]["content_hash"] is not None


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_register_paper_path_conflict_skips_insert(tmp_path):
    """If source_uri is already owned by another paper, paper_paths insert is skipped."""
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("Shared content.\n")
    ingest_file(conn, md)
    source_uri = str(md.resolve())

    p1 = register_paper(conn, "First Paper", source_uri=source_uri)
    p2 = register_paper(conn, "Second Paper", source_uri=source_uri)

    # First paper owns the path
    paths1 = get_paper_paths(conn, p1["paper_id"])
    assert len(paths1) == 1

    # Second paper has no paper_paths entry (conflict skipped)
    paths2 = get_paper_paths(conn, p2["paper_id"])
    assert len(paths2) == 0


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_get_paper_resilient_to_broken_abstract_chunk(tmp_path):
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("Paper content for resilience test.\n")
    ingest_file(conn, md)

    result = register_paper(conn, "Resilient", source_uri=str(md.resolve()))
    paper_id = result["paper_id"]

    # Break the abstract_chunk_id link
    conn.execute("UPDATE papers SET abstract_chunk_id = NULL WHERE id = ?", (paper_id,))
    conn.commit()

    papers = get_paper(conn, paper_id=paper_id)
    assert len(papers[0]["chunks"]) >= 1


# --- relocate_paper ---


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_relocate_paper(tmp_path):
    conn = _setup(tmp_path)
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    md = old_dir / "paper.md"
    md.write_text("Paper content.\n")
    ingest_file(conn, md)

    result = register_paper(conn, "Movable", source_uri=str(md.resolve()))
    paper_id = result["paper_id"]

    new_dir = tmp_path / "new"
    new_dir.mkdir()
    new_path = new_dir / "paper.md"
    md.rename(new_path)

    res = relocate_paper(conn, paper_id, str(new_path.resolve()))
    assert res["old_path"] == str((old_dir / "paper.md").resolve())
    assert res["new_path"] == str(new_path.resolve())

    # paper_paths updated
    paths = get_paper_paths(conn, paper_id)
    assert paths[0]["path"] == str(new_path.resolve())
    assert paths[0]["content_hash"] is not None

    # chunks.source_uri updated
    chunks = conn.execute(
        "SELECT id FROM chunks WHERE source_uri = ?", (str(new_path.resolve()),)
    ).fetchall()
    assert len(chunks) >= 1

    # Old path has no chunks
    old_chunks = conn.execute(
        "SELECT id FROM chunks WHERE source_uri = ?",
        (str((old_dir / "paper.md").resolve()),),
    ).fetchall()
    assert len(old_chunks) == 0


def test_relocate_paper_no_entry(tmp_path):
    conn = _setup(tmp_path)
    pid = register_paper(conn, "No Path Paper")["paper_id"]
    conn.execute("DELETE FROM paper_paths WHERE paper_id = ?", (pid,))
    conn.commit()
    result = relocate_paper(conn, pid, "/new/path")
    assert "error" in result


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_relocate_paper_rolls_back_on_partial_failure(tmp_path):
    """If the chunks UPDATE fails, the paper_paths UPDATE must be rolled back."""
    import sqlite3

    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("Some content.\n")
    ingest_file(conn, md)

    result = register_paper(conn, "Rollback Test", source_uri=str(md.resolve()))
    paper_id = result["paper_id"]

    old_path = get_paper_paths(conn, paper_id)[0]["path"]

    new_dir = tmp_path / "moved"
    new_dir.mkdir()
    new_path = new_dir / "paper.md"
    md.rename(new_path)

    # Install a trigger that makes the chunks UPDATE fail via RAISE(ABORT)
    conn.execute(
        """
        CREATE TRIGGER _test_fail_chunks_update
        BEFORE UPDATE OF source_uri ON chunks
        BEGIN
            SELECT RAISE(ABORT, 'simulated chunk update failure');
        END
        """
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="simulated chunk update"):
        relocate_paper(conn, paper_id, str(new_path.resolve()))

    # Connection must be clean (not left in a dirty transaction)
    assert not conn.in_transaction, "connection left dirty after partial failure"

    # paper_paths must be unchanged — the first UPDATE was rolled back
    paths = get_paper_paths(conn, paper_id)
    assert paths[0]["path"] == old_path, (
        "paper_paths was not rolled back after partial failure"
    )
