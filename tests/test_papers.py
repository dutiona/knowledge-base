"""Tests for paper registration, retrieval, relationships, BibTeX, and suggestions."""

from unittest.mock import patch

from research_index.db import EMBED_DIM, get_connection, init_schema
from research_index.ingest import ingest_file
from research_index.papers import (
    add_relationship,
    export_bibtex,
    get_paper,
    get_relationships,
    register_paper,
    suggest_relationships,
    sync_bibtex,
    _bibtex_key,
)


def _fake_embed(texts, model="nomic-embed-text", expected_dim=None):
    dim = expected_dim if expected_dim is not None else EMBED_DIM
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


@patch("research_index.ingest.embed", _fake_embed)
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


@patch("research_index.ingest.embed", _fake_embed)
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


# --- BibTeX export ---


def test_bibtex_key():
    assert _bibtex_key(["Vaswani, A."], 2017) == "vaswani2017"
    assert _bibtex_key(["John Smith"], 2023) == "smith2023"
    assert _bibtex_key([], None) == "unknownnd"


def test_export_bibtex_generated(tmp_path):
    conn = _setup(tmp_path)
    register_paper(
        conn, "Attention Is All You Need", ["Vaswani"], 2017, "NeurIPS", "10.1234/att"
    )

    bib = export_bibtex(conn)
    assert "@article{" in bib
    assert "Attention Is All You Need" in bib
    assert "2017" in bib
    assert "10.1234/att" in bib


def test_export_bibtex_stored(tmp_path):
    conn = _setup(tmp_path)
    custom_bib = "@inproceedings{custom2024, title={Custom}, year={2024}}"
    register_paper(conn, "Custom Paper", bibtex=custom_bib)

    bib = export_bibtex(conn)
    assert bib == custom_bib


def test_export_bibtex_filter_by_ids(tmp_path):
    conn = _setup(tmp_path)
    p1 = register_paper(conn, "Paper A", year=2020)["paper_id"]
    register_paper(conn, "Paper B", year=2021)

    bib = export_bibtex(conn, paper_ids=[p1])
    assert "Paper A" in bib
    assert "Paper B" not in bib


def test_export_bibtex_filter_by_title(tmp_path):
    conn = _setup(tmp_path)
    register_paper(conn, "Deep Learning", year=2015)
    register_paper(conn, "Reinforcement Learning", year=2018)

    bib = export_bibtex(conn, title_pattern="Deep")
    assert "Deep Learning" in bib
    assert "Reinforcement" not in bib


# --- sync_bibtex ---


def test_sync_bibtex_appends_new(tmp_path):
    conn = _setup(tmp_path)
    custom = "@article{papera2020,\n  title = {Paper A},\n  year = {2020},\n}"
    register_paper(conn, "Paper A", bibtex=custom)
    register_paper(conn, "Paper B", ["Author B"], 2021)

    bib_file = tmp_path / "refs.bib"
    # Pre-populate with Paper A's stored entry
    bib_file.write_text("@article{papera2020,\n  title = {Paper A},\n}\n")

    result = sync_bibtex(conn, str(bib_file))
    assert result["appended"] == 1
    assert result["skipped"] == 1

    content = bib_file.read_text()
    assert "Paper A" in content
    assert "Paper B" in content


def test_sync_bibtex_no_duplicates_stored(tmp_path):
    """Stored BibTeX entry with matching key is skipped."""
    conn = _setup(tmp_path)
    custom = "@article{custom2024,\n  title = {Custom},\n  year = {2024},\n}"
    register_paper(conn, "Custom Paper", bibtex=custom)

    bib_file = tmp_path / "refs.bib"
    bib_file.write_text("@article{custom2024,\n  title = {Custom},\n}\n")

    result = sync_bibtex(conn, str(bib_file))
    assert result["appended"] == 0
    assert result["skipped"] == 1


def test_sync_bibtex_creates_file(tmp_path):
    conn = _setup(tmp_path)
    register_paper(conn, "Paper A", ["Author A"], 2020)

    bib_file = tmp_path / "new_refs.bib"
    assert not bib_file.exists()

    result = sync_bibtex(conn, str(bib_file))
    assert result["appended"] == 1
    assert result["skipped"] == 0
    assert bib_file.exists()
    assert "Paper A" in bib_file.read_text()


def test_sync_bibtex_with_stored_bibtex(tmp_path):
    conn = _setup(tmp_path)
    custom = "@inproceedings{custom2024,\n  title = {Custom},\n  year = {2024},\n}"
    register_paper(conn, "Custom Paper", bibtex=custom)

    bib_file = tmp_path / "refs.bib"
    bib_file.write_text("@article{other2023,\n  title = {Other},\n}\n")

    result = sync_bibtex(conn, str(bib_file))
    assert result["appended"] == 1

    content = bib_file.read_text()
    assert "custom2024" in content
    assert "other2023" in content


def test_sync_bibtex_key_collision_across_entries(tmp_path):
    """Two papers with same surname+year get distinct keys in sync output."""
    conn = _setup(tmp_path)
    register_paper(conn, "Paper One", ["Smith, Alice"], 2024)
    register_paper(conn, "Paper Two", ["Smith, Bob"], 2024)

    bib_file = tmp_path / "refs.bib"
    result = sync_bibtex(conn, str(bib_file))
    assert result["appended"] == 2

    content = bib_file.read_text()
    assert "smith2024," in content
    assert "smith2024a," in content
    assert "Paper One" in content
    assert "Paper Two" in content


def test_sync_bibtex_different_paper_same_base_key(tmp_path):
    """A different paper with the same base key gets a suffixed key, not skipped."""
    conn = _setup(tmp_path)
    register_paper(conn, "New Smith Paper", ["Smith"], 2024)

    bib_file = tmp_path / "refs.bib"
    bib_file.write_text("@article{smith2024,\n  title = {Old Smith Paper},\n}\n")

    result = sync_bibtex(conn, str(bib_file))
    assert result["appended"] == 1
    assert result["skipped"] == 0

    content = bib_file.read_text()
    assert "smith2024a," in content
    assert "New Smith Paper" in content


def test_export_bibtex_key_collision(tmp_path):
    """export_bibtex generates distinct keys for same-surname-year papers."""
    conn = _setup(tmp_path)
    register_paper(conn, "Paper One", ["Smith, Alice"], 2024)
    register_paper(conn, "Paper Two", ["Smith, Bob"], 2024)

    bib = export_bibtex(conn)
    assert "smith2024," in bib
    assert "smith2024a," in bib


def test_export_bibtex_stored_vs_generated_collision(tmp_path):
    """Stored BibTeX key doesn't collide with generated key."""
    conn = _setup(tmp_path)
    custom = "@article{smith2024,\n  title = {Stored Smith},\n  year = {2024},\n}"
    register_paper(conn, "Stored Smith", bibtex=custom)
    register_paper(conn, "Generated Smith", ["Smith, Alice"], 2024)

    bib = export_bibtex(conn)
    assert "smith2024," in bib  # stored entry
    assert "smith2024a," in bib  # generated avoids collision


def test_sync_bibtex_stored_vs_generated_collision(tmp_path):
    """Stored and generated entries with same key get distinct keys in sync."""
    conn = _setup(tmp_path)
    custom = "@article{smith2024,\n  title = {Stored Smith},\n  year = {2024},\n}"
    register_paper(conn, "Stored Smith", bibtex=custom)
    register_paper(conn, "Generated Smith", ["Smith, Alice"], 2024)

    bib_file = tmp_path / "refs.bib"
    result = sync_bibtex(conn, str(bib_file))
    assert result["appended"] == 2

    content = bib_file.read_text()
    assert "smith2024," in content  # stored entry
    assert "smith2024a," in content  # generated avoids collision


def test_sync_bibtex_with_filters(tmp_path):
    conn = _setup(tmp_path)
    register_paper(conn, "Paper A", ["Author A"], 2020)
    register_paper(conn, "Paper B", ["Author B"], 2021)

    bib_file = tmp_path / "refs.bib"
    bib_file.write_text("")

    result = sync_bibtex(conn, str(bib_file), title_pattern="Paper A")
    assert result["appended"] == 1

    content = bib_file.read_text()
    assert "Paper A" in content
    assert "Paper B" not in content


# --- suggest_relationships ---


@patch("research_index.ingest.embed", _fake_embed)
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

    suggestions = suggest_relationships(conn, p1)
    assert len(suggestions) >= 1
    assert suggestions[0]["match_method"] == "doi"
    assert suggestions[0]["target_title"] == "Target Paper"


@patch("research_index.ingest.embed", _fake_embed)
def test_suggest_relationships_by_title(tmp_path):
    conn = _setup(tmp_path)

    md = tmp_path / "citing.md"
    md.write_text("We extend Attention Is All You Need with sparse patterns.\n")
    ingest_file(conn, md)

    p1 = register_paper(conn, "Sparse Attention", source_uri=str(md.resolve()))[
        "paper_id"
    ]
    register_paper(conn, "Attention Is All You Need")

    suggestions = suggest_relationships(conn, p1)
    assert len(suggestions) >= 1
    assert suggestions[0]["match_method"] == "title"


@patch("research_index.ingest.embed", _fake_embed)
def test_suggest_skips_existing_relationships(tmp_path):
    conn = _setup(tmp_path)

    md = tmp_path / "citing.md"
    md.write_text("References 10.1234/existing in the bibliography.\n")
    ingest_file(conn, md)

    p1 = register_paper(conn, "Paper A", source_uri=str(md.resolve()))["paper_id"]
    p2 = register_paper(conn, "Paper B", doi="10.1234/existing")["paper_id"]
    add_relationship(conn, p1, p2, "cites")

    suggestions = suggest_relationships(conn, p1)
    assert len(suggestions) == 0


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
    suggestions = suggest_relationships(conn, p1)
    assert suggestions == []
