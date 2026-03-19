"""Tests for _chunk_by_section — semantic section-level chunking for 32K models."""

from __future__ import annotations

from knowledge_base.ingest import _chunk_by_section


def test_basic_heading_split():
    """H2 headings produce one chunk per section."""
    text = "## Section A\nContent A paragraph.\n\n## Section B\nContent B paragraph.\n"
    result = _chunk_by_section(text)
    assert len(result) == 2
    assert result[0][0].startswith("## Section A")
    assert result[1][0].startswith("## Section B")


def test_h1_heading_split():
    """H1 headings also trigger section splits (pymupdf4llm emits H1)."""
    text = "# Title\nIntro text.\n\n# Methods\nMethod text.\n"
    result = _chunk_by_section(text)
    assert len(result) == 2
    assert result[0][0].startswith("# Title")
    assert result[1][0].startswith("# Methods")


def test_abstract_standalone():
    """Abstract section always gets its own chunk, never merged."""
    text = (
        "## Abstract\nShort abstract.\n\n"
        "## Introduction\nIntro text that is also short.\n"
    )
    result = _chunk_by_section(text)
    assert len(result) == 2
    assert "Abstract" in result[0][0]
    assert "Introduction" in result[1][0]


def test_references_own_chunk():
    """References section always gets its own chunk, never merged."""
    text = (
        "## Results\nSome results.\n\n"
        "## References\n[1] Author et al. Paper title.\n[2] Another paper.\n"
    )
    result = _chunk_by_section(text)
    assert len(result) == 2
    refs_chunk = [c for c in result if "References" in c[0]]
    assert len(refs_chunk) == 1


def test_oversized_splits_at_subheadings():
    """H2 section exceeding max_section_size splits at H3 boundaries."""
    sub1 = "### Sub A\n" + "Word " * 500 + "\n\n"  # ~2500 chars
    sub2 = "### Sub B\n" + "Word " * 500 + "\n\n"  # ~2500 chars
    text = f"## Big Section\n{sub1}{sub2}"
    result = _chunk_by_section(text, max_section_size=3000)
    assert len(result) >= 2, f"Expected >=2 chunks, got {len(result)}"
    # Each chunk should contain its sub-heading content
    texts = [c[0] for c in result]
    assert any("Sub A" in t for t in texts)
    assert any("Sub B" in t for t in texts)


def test_paragraph_fallback():
    """Section still too large after H3 split falls back to paragraph splitting."""
    # Single H3 section with no further sub-headings, but very long
    paragraphs = "\n\n".join(["Paragraph " + "word " * 200 for _ in range(5)])
    text = f"## Big Section\n### Only Sub\n{paragraphs}"
    result = _chunk_by_section(text, max_section_size=1500)
    assert len(result) >= 2, (
        f"Expected >=2 chunks from paragraph fallback, got {len(result)}"
    )


def test_deep_headings_no_split():
    """H4 and deeper do NOT trigger splits — treated as body text."""
    text = (
        "## Section\n#### Deep heading 1\nContent 1.\n#### Deep heading 2\nContent 2.\n"
    )
    result = _chunk_by_section(text)
    assert len(result) == 1
    assert "Deep heading 1" in result[0][0]
    assert "Deep heading 2" in result[0][0]


def test_table_integrity():
    """Table never split from its parent section."""
    table_rows = "\n".join(f"| col1_{i} | col2_{i} |" for i in range(20))
    table = f"| Header1 | Header2 |\n|---------|----------|\n{table_rows}"
    text = f"## Table Section\n{table}\n"
    result = _chunk_by_section(text, max_section_size=200)
    # Table should stay in one chunk even if it exceeds max_section_size
    table_chunks = [c for c in result if "|" in c[0]]
    assert len(table_chunks) >= 1
    # All table rows should be in a single chunk
    for tc_text, _ in table_chunks:
        if "col1_0" in tc_text:
            assert "col1_19" in tc_text, "Table was split across chunks"
            break


def test_no_overlap():
    """Chunks produced by _chunk_by_section do not overlap."""
    text = (
        "## Alpha\nUnique_alpha text here.\n\n"
        "## Beta\nUnique_beta text here.\n\n"
        "## Gamma\nUnique_gamma text here.\n"
    )
    result = _chunk_by_section(text)
    assert len(result) == 3
    # Each unique marker should appear in exactly one chunk
    for marker in ("Unique_alpha", "Unique_beta", "Unique_gamma"):
        count = sum(1 for c, _ in result if marker in c)
        assert count == 1, f"Marker '{marker}' appears in {count} chunks"


def test_page_provenance():
    """Page numbers correctly propagated via page_map."""
    text = "## Section A\nContent A.\n\n## Section B\nContent B.\n"
    # Section A starts at char 0, Section B at ~30
    page_map = {0: 1, 30: 2}
    result = _chunk_by_section(text, page_map=page_map)
    assert len(result) == 2
    # First chunk should reference page 1
    assert 1 in result[0][1]
    # Second chunk should reference page 2
    assert 2 in result[1][1]


def test_no_headings_fallback():
    """Text without headings returns single chunk (or paragraph split if oversized)."""
    short_text = "Just plain text without any headings."
    result = _chunk_by_section(short_text)
    assert len(result) == 1
    assert result[0][0] == short_text

    # Oversized text without headings should paragraph-split
    long_text = "\n\n".join(["Paragraph " + "word " * 200 for _ in range(5)])
    result = _chunk_by_section(long_text, max_section_size=1500)
    assert len(result) >= 2


def test_return_type_matches_chunk_markdown():
    """Returns list[tuple[str, list[int]]] matching _chunk_markdown signature."""
    text = "## Section\nContent.\n"
    result = _chunk_by_section(text)
    assert isinstance(result, list)
    assert len(result) > 0
    for item in result:
        assert isinstance(item, tuple)
        assert len(item) == 2
        assert isinstance(item[0], str)
        assert isinstance(item[1], list)


def test_empty_section_skipped():
    """Heading with no body produces no chunk."""
    text = "## Empty\n\n## Has Content\nActual content here.\n"
    result = _chunk_by_section(text)
    # Only the section with content should produce a chunk
    assert len(result) == 1
    assert "Has Content" in result[0][0]


def test_image_refs_sanitized():
    """Image paths are sanitized like _chunk_markdown."""
    text = "## Section\n![alt](/absolute/path/to/image.png)\n"
    result = _chunk_by_section(text)
    assert len(result) == 1
    assert "(image.png)" in result[0][0]
    assert "/absolute/path/to/" not in result[0][0]
