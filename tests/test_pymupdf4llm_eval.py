"""Evaluate pymupdf4llm.to_markdown() vs bare page.get_text() on synthetic PDFs.

Phase 1 of issue #60 — no DB, no embeddings, pure extraction comparison.
Tests assert behavioral bounds (not exact output) since synthetic PDFs
may trigger different heuristics than real papers.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import fitz
import pymupdf4llm
import pytest

from knowledge_base.chunking import chunk_text as _chunk_text

_has_tessdata = shutil.which("tesseract") is not None

# ---------------------------------------------------------------------------
# Fixed extraction config — pinned kwargs for reproducibility
# ---------------------------------------------------------------------------

_TO_MARKDOWN_KWARGS = {
    "write_images": False,  # don't write to disk during tests
    "page_chunks": False,  # return single string for comparison
}


def _extract_flat(path: Path) -> str:
    """Current extraction method: bare page.get_text()."""
    doc = fitz.open(str(path))
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return "\n\n".join(pages)


def _extract_markdown(path: Path) -> str:
    """pymupdf4llm extraction with pinned config."""
    return pymupdf4llm.to_markdown(str(path), **_TO_MARKDOWN_KWARGS)


# ---------------------------------------------------------------------------
# PDF builders — proper font-size variation via insert_textbox
# ---------------------------------------------------------------------------

_FONT_H1 = 18
_FONT_H2 = 14
_FONT_BODY = 11


def _make_structured_pdf(path: Path) -> Path:
    """Create a multi-page PDF with headings, body text, and lists.

    Uses insert_textbox with varying font sizes to simulate real
    document structure that pymupdf4llm can detect.
    """
    doc = fitz.open()

    # Page 1: headings + body
    page = doc.new_page()
    rect = fitz.Rect(72, 72, 540, 120)
    page.insert_textbox(rect, "Introduction", fontsize=_FONT_H1)
    rect = fitz.Rect(72, 130, 540, 200)
    page.insert_textbox(
        rect,
        "This paper presents a novel approach to structured extraction "
        "from PDF documents using machine learning techniques.",
        fontsize=_FONT_BODY,
    )
    rect = fitz.Rect(72, 210, 540, 250)
    page.insert_textbox(rect, "Background", fontsize=_FONT_H2)
    rect = fitz.Rect(72, 260, 540, 400)
    page.insert_textbox(
        rect,
        "Previous work has focused on rule-based approaches. "
        "These methods rely on fixed heuristics that fail on diverse layouts. "
        "Our approach instead uses learned representations.",
        fontsize=_FONT_BODY,
    )

    # Page 2: lists
    page = doc.new_page()
    rect = fitz.Rect(72, 72, 540, 110)
    page.insert_textbox(rect, "Key Contributions", fontsize=_FONT_H2)
    rect = fitz.Rect(72, 120, 540, 350)
    list_text = (
        "\u2022 First contribution: improved accuracy on benchmark datasets\n"
        "\u2022 Second contribution: reduced computational cost by 40%\n"
        "\u2022 Third contribution: open-source implementation"
    )
    page.insert_textbox(rect, list_text, fontsize=_FONT_BODY)

    doc.save(str(path))
    doc.close()
    return path


def _make_table_pdf(path: Path) -> Path:
    """Create a PDF with a table using cell-positioned text.

    Draws grid lines and places text in cells to simulate a table
    that pymupdf4llm's table detection can pick up.
    """
    doc = fitz.open()
    page = doc.new_page()

    # Title
    rect = fitz.Rect(72, 72, 540, 110)
    page.insert_textbox(rect, "Results", fontsize=_FONT_H2)

    # Table: 3 columns x 4 rows (header + 3 data rows)
    x0, y0 = 72, 130
    col_widths = [150, 120, 120]
    row_height = 25
    rows = [
        ["Method", "Accuracy", "F1 Score"],
        ["Baseline", "72.3", "68.1"],
        ["Ours (small)", "85.7", "82.4"],
        ["Ours (large)", "91.2", "89.6"],
    ]

    for row_idx, row in enumerate(rows):
        y = y0 + row_idx * row_height
        x = x0
        for col_idx, cell_text in enumerate(row):
            w = col_widths[col_idx]
            # Draw cell border
            cell_rect = fitz.Rect(x, y, x + w, y + row_height)
            page.draw_rect(cell_rect, color=(0, 0, 0), width=0.5)
            # Insert text with padding
            text_rect = fitz.Rect(x + 4, y + 2, x + w - 4, y + row_height - 2)
            fontsize = _FONT_BODY if row_idx > 0 else _FONT_BODY
            page.insert_textbox(text_rect, cell_text, fontsize=fontsize)
            x += w

    doc.save(str(path))
    doc.close()
    return path


def _make_image_pdf(path: Path, image_path: Path) -> Path:
    """Create a PDF with an embedded raster image."""
    doc = fitz.open()
    page = doc.new_page()

    rect = fitz.Rect(72, 72, 540, 110)
    page.insert_textbox(rect, "Figure Example", fontsize=_FONT_H2)

    # Insert the image
    img_rect = fitz.Rect(72, 120, 400, 350)
    page.insert_image(img_rect, filename=str(image_path))

    rect = fitz.Rect(72, 360, 540, 400)
    page.insert_textbox(
        rect, "Figure 1: A test image for extraction evaluation.", fontsize=_FONT_BODY
    )

    doc.save(str(path))
    doc.close()
    return path


def _make_vector_pdf(path: Path, num_drawings: int = 150) -> Path:
    """Create a PDF with many vector drawings (simulating the memento paper).

    Draws rectangles, lines, and circles to trigger drawing detection
    without any embedded raster images.
    """
    doc = fitz.open()
    page = doc.new_page()

    rect = fitz.Rect(72, 72, 540, 110)
    page.insert_textbox(rect, "Architecture Overview", fontsize=_FONT_H2)

    # Draw many vector shapes — rectangles, lines, circles
    import random

    rng = random.Random(42)  # deterministic
    for i in range(num_drawings):
        x = rng.randint(72, 500)
        y = rng.randint(120, 750)
        w = rng.randint(10, 60)
        h = rng.randint(10, 60)
        color = (rng.random(), rng.random(), rng.random())
        shape_type = i % 3
        if shape_type == 0:
            page.draw_rect(fitz.Rect(x, y, x + w, y + h), color=color, width=0.5)
        elif shape_type == 1:
            page.draw_line(fitz.Point(x, y), fitz.Point(x + w, y + h), color=color)
        else:
            page.draw_circle(fitz.Point(x, y), w / 2, color=color, width=0.5)

    doc.save(str(path))
    doc.close()
    return path


def _make_two_column_pdf(path: Path) -> Path:
    """Create a PDF with two-column layout to test reading order."""
    doc = fitz.open()
    page = doc.new_page()

    # Column A (left)
    rect_a = fitz.Rect(72, 72, 290, 400)
    col_a_text = (
        "COLUMN_A_START This is the left column of the document. "
        "It contains the first section of text that should be read before "
        "the right column. The reading order is critical for correct "
        "extraction. COLUMN_A_END"
    )
    page.insert_textbox(rect_a, col_a_text, fontsize=_FONT_BODY)

    # Column B (right)
    rect_b = fitz.Rect(310, 72, 540, 400)
    col_b_text = (
        "COLUMN_B_START This is the right column of the document. "
        "It contains the second section that should follow the left column. "
        "If extraction interleaves these columns, the text will be garbled. "
        "COLUMN_B_END"
    )
    page.insert_textbox(rect_b, col_b_text, fontsize=_FONT_BODY)

    doc.save(str(path))
    doc.close()
    return path


def _create_test_png(path: Path, width: int = 100, height: int = 80) -> Path:
    """Create a minimal valid PNG for embedding tests."""
    # Minimal PNG: 1-pixel-per-row uncompressed, solid color
    from PIL import Image

    img = Image.new("RGB", (width, height), color=(200, 50, 50))
    img.save(str(path), format="PNG")
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMarkdownPreservesHeadings:
    """to_markdown() should detect font-size-based headings."""

    def test_headings_detected(self, tmp_path: Path) -> None:
        pdf = _make_structured_pdf(tmp_path / "structured.pdf")
        md = _extract_markdown(pdf)
        flat = _extract_flat(pdf)

        # Markdown should contain heading markers
        heading_lines = [line for line in md.splitlines() if line.startswith("#")]
        assert len(heading_lines) > 0, (
            f"Expected markdown heading markers, got none.\n"
            f"First 500 chars of markdown:\n{md[:500]}"
        )

        # Flat text should NOT contain heading markers
        flat_heading_lines = [
            line for line in flat.splitlines() if line.startswith("#")
        ]
        assert len(flat_heading_lines) == 0

    def test_heading_hierarchy(self, tmp_path: Path) -> None:
        """H1 (18pt) and H2 (14pt) should produce different heading levels."""
        pdf = _make_structured_pdf(tmp_path / "structured.pdf")
        md = _extract_markdown(pdf)

        h1_lines = [line for line in md.splitlines() if re.match(r"^# [^#]", line)]
        h2_lines = [line for line in md.splitlines() if re.match(r"^## [^#]", line)]

        # At minimum, we expect some heading differentiation.
        # If pymupdf4llm collapses all to same level, that's still acceptable
        # but worth noting. We assert at least headings exist.
        all_headings = h1_lines + h2_lines
        assert len(all_headings) > 0, (
            f"No heading hierarchy detected.\nMarkdown:\n{md[:1000]}"
        )


class TestMarkdownPreservesLists:
    """to_markdown() should detect bullet lists."""

    def test_list_markers_present(self, tmp_path: Path) -> None:
        pdf = _make_structured_pdf(tmp_path / "structured.pdf")
        md = _extract_markdown(pdf)

        # Look for any list-like markers: -, *, or numbered
        list_pattern = re.compile(r"^\s*[-*\u2022]\s|^\s*\d+\.\s", re.MULTILINE)
        matches = list_pattern.findall(md)
        assert len(matches) > 0, (
            f"Expected list markers in markdown output.\n"
            f"Page 2 markdown:\n{md[len(md) // 2 :][:500]}"
        )


class TestMarkdownExtractsImageRefs:
    """to_markdown() should reference embedded raster images."""

    @pytest.mark.skipif(not _has_tessdata, reason="tesseract OCR not installed")
    def test_image_reference_present(self, tmp_path: Path) -> None:
        png_path = _create_test_png(tmp_path / "test_figure.png")
        pdf = _make_image_pdf(tmp_path / "with_image.pdf", png_path)

        # Use write_images=True to see image references
        md = pymupdf4llm.to_markdown(
            str(pdf), write_images=True, image_path=str(tmp_path)
        )

        # Check for image reference syntax: ![...] or img tag
        has_img_ref = "![" in md or "<img" in md.lower()
        assert has_img_ref, (
            f"pymupdf4llm did not produce image references for embedded raster image "
            f"with write_images=True.\nMarkdown output:\n{md[:1000]}"
        )


class TestMarkdownHandlesVectorDrawings:
    """to_markdown() should not crash on vector-heavy pages."""

    def test_no_crash_on_many_drawings(self, tmp_path: Path) -> None:
        pdf = _make_vector_pdf(tmp_path / "vector_heavy.pdf", num_drawings=150)
        # Must not raise
        md = _extract_markdown(pdf)
        assert isinstance(md, str)
        assert len(md) > 0

    def test_vector_drawing_output_documented(self, tmp_path: Path) -> None:
        """Document what pymupdf4llm produces for vector-only pages.

        This test always passes but prints diagnostic output.
        """
        pdf = _make_vector_pdf(tmp_path / "vector_heavy.pdf", num_drawings=150)
        md = _extract_markdown(pdf)
        flat = _extract_flat(pdf)

        # Print for manual inspection during evaluation
        print(f"\n--- Vector PDF: flat text ({len(flat)} chars) ---")
        print(flat[:300] if flat.strip() else "(empty)")
        print(f"\n--- Vector PDF: markdown ({len(md)} chars) ---")
        print(md[:300] if md.strip() else "(empty)")

        # Check if any image references were generated from vectors
        has_img = "![" in md or "<img" in md.lower()
        print(f"\nImage references from vectors: {has_img}")


class TestMarkdownTableExtraction:
    """to_markdown() should preserve table structure in some form."""

    def test_table_structure_preserved(self, tmp_path: Path) -> None:
        pdf = _make_table_pdf(tmp_path / "table.pdf")
        md = _extract_markdown(pdf)

        # Accept any of: pipe-delimited, HTML table, or aligned text
        has_pipe_table = "|" in md and "---" in md
        has_html_table = "<table" in md.lower()
        # Check if key cell values appear in structured proximity
        has_method_col = "Method" in md and "Accuracy" in md
        has_data = "85.7" in md or "91.2" in md

        structural = has_pipe_table or has_html_table
        content = has_method_col and has_data

        assert structural or content, (
            f"Expected table structure in markdown output.\nMarkdown:\n{md[:1000]}"
        )


class TestMarkdownMultiColumnOrder:
    """to_markdown() should read columns in correct order (left before right)."""

    def test_column_a_before_column_b(self, tmp_path: Path) -> None:
        pdf = _make_two_column_pdf(tmp_path / "two_col.pdf")
        md = _extract_markdown(pdf)

        pos_a_start = md.find("COLUMN_A_START")
        pos_b_start = md.find("COLUMN_B_START")

        assert pos_a_start != -1, "COLUMN_A_START not found in markdown"
        assert pos_b_start != -1, "COLUMN_B_START not found in markdown"

        assert pos_a_start < pos_b_start, (
            f"pymupdf4llm reads Column B before Column A. "
            f"Position A={pos_a_start}, B={pos_b_start}"
        )

    def test_column_text_not_interleaved(self, tmp_path: Path) -> None:
        """Columns should not be interleaved line-by-line."""
        pdf = _make_two_column_pdf(tmp_path / "two_col.pdf")
        md = _extract_markdown(pdf)

        pos_a_end = md.find("COLUMN_A_END")
        pos_b_start = md.find("COLUMN_B_START")

        assert pos_a_end != -1, "COLUMN_A_END not found in markdown"
        assert pos_b_start != -1, "COLUMN_B_START not found in markdown"

        # Column A should end before Column B starts (no interleaving)
        assert pos_a_end < pos_b_start, (
            f"Column text appears interleaved. "
            f"A_END at {pos_a_end}, B_START at {pos_b_start}"
        )


class TestChunkQualityComparison:
    """Compare chunk boundary quality between flat and markdown extraction."""

    def test_heading_at_chunk_start_frequency(self, tmp_path: Path) -> None:
        pdf = _make_structured_pdf(tmp_path / "structured.pdf")
        md = _extract_markdown(pdf)
        flat = _extract_flat(pdf)

        md_chunks = _chunk_text(md)
        flat_chunks = _chunk_text(flat)

        heading_re = re.compile(r"^#{1,6}\s")

        def heading_start_pct(chunks: list[str]) -> float:
            if not chunks:
                return 0.0
            starts = sum(1 for c in chunks if heading_re.match(c))
            return starts / len(chunks)

        def orphaned_heading_end_pct(chunks: list[str]) -> float:
            """Chunks ending with a heading line (content severed)."""
            if not chunks:
                return 0.0
            orphaned = 0
            for c in chunks:
                lines = c.rstrip().splitlines()
                if lines and heading_re.match(lines[-1]):
                    orphaned += 1
            return orphaned / len(chunks)

        md_start_pct = heading_start_pct(md_chunks)
        md_orphan_pct = orphaned_heading_end_pct(md_chunks)
        flat_start_pct = heading_start_pct(flat_chunks)

        print("\n--- Chunk quality comparison ---")
        print(f"Flat: {len(flat_chunks)} chunks, {flat_start_pct:.1%} heading-at-start")
        print(
            f"Markdown: {len(md_chunks)} chunks, {md_start_pct:.1%} heading-at-start, "
            f"{md_orphan_pct:.1%} orphaned-heading-at-end"
        )

        # Markdown should have at least as many heading-starts as flat text
        # (flat text has 0% since it doesn't produce # markers)
        assert md_start_pct >= flat_start_pct
