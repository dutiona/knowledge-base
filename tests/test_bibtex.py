"""Tests for BibTeX key generation, export, and file synchronisation."""

from knowledge_base.bibtex import _bibtex_key, export_bibtex, sync_bibtex
from knowledge_base.db import get_connection, init_schema
from knowledge_base.papers import register_paper


def _setup(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    return conn


# --- _bibtex_key ---


def test_bibtex_key():
    assert _bibtex_key(["Vaswani, A."], 2017) == "vaswani2017"
    assert _bibtex_key(["John Smith"], 2023) == "smith2023"
    assert _bibtex_key([], None) == "unknownnd"


# --- export_bibtex ---


def test_export_bibtex_generated(tmp_path):
    conn = _setup(tmp_path)
    register_paper(conn, "Attention Is All You Need", ["Vaswani"], 2017, "NeurIPS", "10.1234/att")

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


def test_export_bibtex_key_collision(tmp_path):
    conn = _setup(tmp_path)
    register_paper(conn, "Paper A", ["Smith"], year=2024)
    register_paper(conn, "Paper B", ["Smith"], year=2024)

    bib = export_bibtex(conn)
    assert "smith2024," in bib
    assert "smith2024a," in bib


def test_export_bibtex_dedup_stored_keys(tmp_path):
    """Two papers with same stored BibTeX key: only first emitted."""
    conn = _setup(tmp_path)
    stored = "@article{dup2020,\n  title = {Dup A},\n}"
    register_paper(conn, "Dup A", ["Author"], year=2020, bibtex=stored)
    register_paper(conn, "Dup B", ["Author"], year=2020, bibtex=stored)

    bib = export_bibtex(conn)
    assert bib.count("dup2020") == 1


def test_export_bibtex_stored_vs_generated_collision(tmp_path):
    """Stored key doesn't collide with generated key."""
    conn = _setup(tmp_path)
    stored = "@article{smith2024,\n  title = {Stored},\n}"
    register_paper(conn, "Stored Paper", ["Smith"], year=2024, bibtex=stored)
    register_paper(conn, "Generated Paper", ["Smith"], year=2024)

    bib = export_bibtex(conn)
    assert "smith2024," in bib
    assert "smith2024a," in bib


# --- sync_bibtex ---


def test_sync_bibtex_creates_file(tmp_path):
    conn = _setup(tmp_path)
    register_paper(conn, "New Paper", ["Author A"], year=2023)
    bib_path = tmp_path / "out.bib"

    result = sync_bibtex(conn, str(bib_path))
    assert result["appended"] == 1
    assert result["skipped"] == 0
    content = bib_path.read_text()
    assert "New Paper" in content


def test_sync_bibtex_appends_new(tmp_path):
    conn = _setup(tmp_path)
    register_paper(conn, "Existing Paper", ["Smith"], year=2020)
    register_paper(conn, "Brand New", ["Jones"], year=2024)

    bib_path = tmp_path / "refs.bib"
    # First sync seeds the file
    sync_bibtex(conn, str(bib_path))
    # Second sync with only the new paper
    p2 = conn.execute("SELECT id FROM papers WHERE title='Brand New'").fetchone()["id"]
    # Re-create file with just the first entry to simulate existing state
    first_entry = bib_path.read_text().split("\n\n")[0] + "\n"
    bib_path.write_text(first_entry)

    result = sync_bibtex(conn, str(bib_path), paper_ids=[p2])
    assert result["appended"] == 1
    content = bib_path.read_text()
    assert "Brand New" in content
    assert "Existing Paper" in content  # original preserved


def test_sync_bibtex_no_duplicates_stored(tmp_path):
    conn = _setup(tmp_path)
    stored_bib = "@article{smith2020,\n  title = {My Paper},\n}"
    register_paper(conn, "My Paper", ["Smith"], year=2020, bibtex=stored_bib)

    bib_path = tmp_path / "refs.bib"
    bib_path.write_text(stored_bib + "\n")

    result = sync_bibtex(conn, str(bib_path))
    assert result["appended"] == 0
    assert result["skipped"] == 1


def test_sync_bibtex_with_stored_bibtex(tmp_path):
    conn = _setup(tmp_path)
    stored_bib = "@article{custom2020,\n  title = {Custom Entry},\n}"
    register_paper(conn, "Custom Entry", ["Author"], year=2020, bibtex=stored_bib)

    bib_path = tmp_path / "refs.bib"
    bib_path.write_text("@article{other2019,\n  title = {Other},\n}\n")

    result = sync_bibtex(conn, str(bib_path))
    assert result["appended"] == 1
    content = bib_path.read_text()
    assert "custom2020" in content
    assert "other2019" in content


def test_sync_bibtex_key_collision_across_entries(tmp_path):
    conn = _setup(tmp_path)
    register_paper(conn, "Paper One", ["Smith"], year=2024)
    register_paper(conn, "Paper Two", ["Smith"], year=2024)

    bib_path = tmp_path / "refs.bib"
    result = sync_bibtex(conn, str(bib_path))
    assert result["appended"] == 2
    content = bib_path.read_text()
    assert "smith2024" in content
    assert "smith2024a" in content


def test_sync_bibtex_different_paper_same_base_key(tmp_path):
    """Different papers with same surname+year get distinct keys, not skipped."""
    conn = _setup(tmp_path)
    register_paper(conn, "First Smith Paper", ["Smith"], year=2024)
    register_paper(conn, "Second Smith Paper", ["Smith"], year=2024)

    bib_path = tmp_path / "refs.bib"
    # Sync first paper
    p1 = conn.execute("SELECT id FROM papers WHERE title='First Smith Paper'").fetchone()["id"]
    sync_bibtex(conn, str(bib_path), paper_ids=[p1])
    # Now sync second paper
    p2 = conn.execute("SELECT id FROM papers WHERE title='Second Smith Paper'").fetchone()["id"]
    result = sync_bibtex(conn, str(bib_path), paper_ids=[p2])
    assert result["appended"] == 1  # not skipped
    content = bib_path.read_text()
    assert "smith2024a" in content


def test_sync_bibtex_stored_vs_generated_collision(tmp_path):
    """Stored + generated same base key get distinct keys."""
    conn = _setup(tmp_path)
    stored = "@article{smith2024,\n  title = {Stored},\n}"
    register_paper(conn, "Stored Paper", ["Smith"], year=2024, bibtex=stored)
    register_paper(conn, "Generated Paper", ["Smith"], year=2024)

    bib_path = tmp_path / "refs.bib"
    result = sync_bibtex(conn, str(bib_path))
    assert result["appended"] == 2
    content = bib_path.read_text()
    assert "smith2024" in content
    # Generated entry gets suffixed to avoid collision with stored
    assert "smith2024a" in content


def test_sync_bibtex_duplicate_stored_keys_same_run(tmp_path):
    """Two stored entries with same key: only first appended."""
    conn = _setup(tmp_path)
    stored = "@article{dup2020,\n  title = {Dup},\n}"
    register_paper(conn, "Dup A", bibtex=stored)
    register_paper(conn, "Dup B", bibtex=stored)

    bib_path = tmp_path / "refs.bib"
    result = sync_bibtex(conn, str(bib_path))
    assert result["appended"] == 1
    assert result["skipped"] == 1


def test_sync_bibtex_idempotent_resync(tmp_path):
    """Re-running sync doesn't duplicate entries (uses paper ID marker)."""
    conn = _setup(tmp_path)
    register_paper(conn, "Idempotent Paper", ["Author"], year=2023)

    bib_path = tmp_path / "refs.bib"
    sync_bibtex(conn, str(bib_path))
    first_content = bib_path.read_text()

    result = sync_bibtex(conn, str(bib_path))
    assert result["appended"] == 0
    assert result["skipped"] == 1
    assert bib_path.read_text() == first_content


def test_sync_bibtex_with_filters(tmp_path):
    conn = _setup(tmp_path)
    register_paper(conn, "Deep Learning", ["Author A"], year=2020)
    register_paper(conn, "Reinforcement Learning", ["Author B"], year=2021)

    bib_path = tmp_path / "refs.bib"
    result = sync_bibtex(conn, str(bib_path), title_pattern="Deep")
    assert result["appended"] == 1
    content = bib_path.read_text()
    assert "Deep Learning" in content
    assert "Reinforcement" not in content
