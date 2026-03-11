"""Tests for _chunk_markdown — heading-aware chunking with page provenance."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from research_index.ingest import _chunk_markdown, _chunk_text


def test_chunk_markdown_no_headings():
    """Text without headings falls back to _chunk_text behavior."""
    text = "No headings here, just plain text. " * 50  # ~1750 chars
    result = _chunk_markdown(text, max_chunk_size=1000)
    flat = _chunk_text(text, size=1000)
    assert len(result) == len(flat)
    for (chunk_text, pages), flat_chunk in zip(result, flat):
        # _chunk_markdown strips chunks; _chunk_text doesn't
        assert chunk_text == flat_chunk.strip()
        assert pages == []


def test_chunk_markdown_heading_split():
    """Text with headings produces one chunk per heading section."""
    text = "## Section A\nContent A.\n\n## Section B\nContent B.\n"
    result = _chunk_markdown(text, max_chunk_size=1000)
    assert len(result) == 2
    assert result[0][0].startswith("## Section A")
    assert result[1][0].startswith("## Section B")


def test_chunk_markdown_heading_preserved():
    """Each chunk starts with its heading line."""
    text = "## Intro\nSome intro text.\n\n### Details\nDetailed content here.\n"
    result = _chunk_markdown(text, max_chunk_size=1000)
    for chunk_text, _ in result:
        assert chunk_text.startswith("#"), (
            f"Chunk does not start with heading: {chunk_text[:50]}"
        )


def test_chunk_markdown_oversized_section():
    """Long section is sub-chunked; heading prepended to first sub-chunk only."""
    body = "Word " * 300  # ~1500 chars
    text = f"## Big Section\n{body}"
    result = _chunk_markdown(text, max_chunk_size=500)
    assert len(result) > 1, "Should produce multiple chunks"
    assert result[0][0].startswith("## Big Section")
    # Second chunk should NOT start with the heading
    assert not result[1][0].startswith("## Big Section")


def test_chunk_markdown_table_intact():
    """Markdown table is never split across chunks."""
    table_rows = "\n".join(f"| col1_{i} | col2_{i} |" for i in range(30))
    table = f"| Header1 | Header2 |\n|---------|----------|\n{table_rows}"
    text = f"## Table Section\n{table}\n"
    # Table is ~700 chars, set chunk size small to test it stays intact
    result = _chunk_markdown(text, max_chunk_size=200)
    # Find the chunk(s) containing table rows
    table_chunks = [ct for ct, _ in result if "| col1_" in ct]
    assert len(table_chunks) == 1, (
        f"Table should be in exactly 1 chunk, got {len(table_chunks)}"
    )
    # Verify all rows are present
    for i in range(30):
        assert f"col1_{i}" in table_chunks[0]


def test_chunk_markdown_tiny_sections_merged():
    """Small deeper sections merge into preceding parent section."""
    text = "## Parent\nParent content.\n\n### Child A\nA.\n\n### Child B\nB.\n"
    result = _chunk_markdown(text, max_chunk_size=1000)
    # Children should merge into parent since they're deeper level
    assert len(result) == 1, f"Expected 1 merged chunk, got {len(result)}"
    assert "Parent content" in result[0][0]
    assert "Child A" in result[0][0]
    assert "Child B" in result[0][0]


def test_chunk_markdown_same_level_not_merged():
    """Same-level sibling sections are NOT merged."""
    text = "## Section A\nContent A.\n\n## Section B\nContent B.\n"
    result = _chunk_markdown(text, max_chunk_size=1000)
    assert len(result) == 2, f"Same-level siblings should not merge, got {len(result)}"


def test_chunk_markdown_image_refs_sanitized():
    """Absolute image paths in ![](…) refs are replaced with basenames."""
    text = "## Figures\n![fig1](/tmp/images/figure_001.png)\nSome text.\n"
    mock_dir = Path("/tmp/images")
    with patch.object(Path, "exists", return_value=True):
        result = _chunk_markdown(text, max_chunk_size=1000, image_dir=mock_dir)
    assert len(result) >= 1
    chunk_text = result[0][0]
    assert "![fig1](figure_001.png)" in chunk_text
    assert "/tmp/images/" not in chunk_text


def test_chunk_markdown_page_provenance():
    """Page numbers are correctly assigned to chunks via page_map."""
    text = (
        "## Page 0 Content\nFirst page text.\n\n## Page 1 Content\nSecond page text.\n"
    )
    page0_len = len("## Page 0 Content\nFirst page text.\n\n")
    page_map = {0: 0, page0_len: 1}
    result = _chunk_markdown(text, max_chunk_size=1000, page_map=page_map)
    assert len(result) == 2
    assert 0 in result[0][1], f"First chunk should be on page 0, got {result[0][1]}"
    assert 1 in result[1][1], f"Second chunk should be on page 1, got {result[1][1]}"


def test_chunk_markdown_empty_input():
    """Empty text produces no chunks."""
    assert _chunk_markdown("") == []
    assert _chunk_markdown("   \n  ") == []


def test_chunk_markdown_preamble_before_headings():
    """Text before the first heading becomes its own chunk."""
    text = "This is preamble text before any heading.\n\n## First Section\nContent.\n"
    result = _chunk_markdown(text, max_chunk_size=1000)
    assert len(result) >= 2
    assert "preamble" in result[0][0]
    assert result[1][0].startswith("## First Section")
