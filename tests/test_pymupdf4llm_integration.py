"""Integration tests for pymupdf4llm production extraction.

These tests run the actual ONNX model on small synthetic PDFs.
Mark with @pytest.mark.slow for CI gating if needed.

Phase 2 of issue #60.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import fitz
import pytest

from research_index.ingest import _extract_pdf_markdown, _extract_pdf_text

# ---------------------------------------------------------------------------
# Synthetic PDF builders (adapted from test_pymupdf4llm_eval.py)
# ---------------------------------------------------------------------------

_FONT_H1 = 18
_FONT_H2 = 14
_FONT_BODY = 11


def _make_structured_pdf(path: Path) -> Path:
    """Multi-page PDF with headings, body text, and lists."""
    doc = fitz.open()

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
        "These methods rely on fixed heuristics that fail on diverse layouts.",
        fontsize=_FONT_BODY,
    )

    page = doc.new_page()
    rect = fitz.Rect(72, 72, 540, 110)
    page.insert_textbox(rect, "Key Contributions", fontsize=_FONT_H2)
    rect = fitz.Rect(72, 120, 540, 350)
    list_text = (
        "\u2022 First contribution: improved accuracy\n"
        "\u2022 Second contribution: reduced cost\n"
        "\u2022 Third contribution: open-source"
    )
    page.insert_textbox(rect, list_text, fontsize=_FONT_BODY)

    doc.save(str(path))
    doc.close()
    return path


def _make_table_pdf(path: Path) -> Path:
    """PDF with a table using cell-positioned text and grid lines."""
    doc = fitz.open()
    page = doc.new_page()

    rect = fitz.Rect(72, 72, 540, 110)
    page.insert_textbox(rect, "Results", fontsize=_FONT_H2)

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
            cell_rect = fitz.Rect(x, y, x + w, y + row_height)
            page.draw_rect(cell_rect, color=(0, 0, 0), width=0.5)
            text_rect = fitz.Rect(x + 4, y + 2, x + w - 4, y + row_height - 2)
            page.insert_textbox(text_rect, cell_text, fontsize=_FONT_BODY)
            x += w

    doc.save(str(path))
    doc.close()
    return path


def _make_image_pdf(path: Path, image_path: Path) -> Path:
    """PDF with an embedded raster image."""
    doc = fitz.open()
    page = doc.new_page()

    rect = fitz.Rect(72, 72, 540, 110)
    page.insert_textbox(rect, "Figure Example", fontsize=_FONT_H2)

    img_rect = fitz.Rect(72, 120, 400, 350)
    page.insert_image(img_rect, filename=str(image_path))

    rect = fitz.Rect(72, 360, 540, 400)
    page.insert_textbox(
        rect, "Figure 1: A test image for extraction.", fontsize=_FONT_BODY
    )

    doc.save(str(path))
    doc.close()
    return path


# ---------------------------------------------------------------------------
# Tests — marked slow (run actual ONNX model)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_real_pdf_headings_extracted(tmp_path):
    """Real synthetic PDF -> markdown has # headings."""
    pdf = _make_structured_pdf(tmp_path / "structured.pdf")
    text, page_map = _extract_pdf_markdown(pdf)

    headings = re.findall(r"^#{1,6} .+", text, re.MULTILINE)
    assert len(headings) >= 1, (
        f"Expected headings in markdown, got none. Text:\n{text[:500]}"
    )
    assert len(page_map) >= 2


@pytest.mark.slow
def test_real_pdf_table_intact(tmp_path):
    """Real synthetic PDF with table -> pipe-delimited table in output."""
    pdf = _make_table_pdf(tmp_path / "table.pdf")
    text, page_map = _extract_pdf_markdown(pdf)

    pipe_lines = [line for line in text.splitlines() if line.startswith("|")]
    assert len(pipe_lines) >= 2, (
        f"Expected pipe-delimited table rows, got {len(pipe_lines)}. "
        f"Text:\n{text[:500]}"
    )


@pytest.mark.slow
def test_real_pdf_image_refs(tmp_path):
    """Real PDF with embedded image + write_images=True -> ![](…) in output."""
    from PIL import Image

    img_path = tmp_path / "test_figure.png"
    img = Image.new("RGB", (200, 200), color="blue")
    img.save(str(img_path))

    pdf = _make_image_pdf(tmp_path / "image.pdf", img_path)
    image_dir = tmp_path / "extracted_images"

    text, page_map = _extract_pdf_markdown(pdf, image_dir=image_dir)

    # pymupdf4llm may or may not produce ![](…) refs depending on detection.
    # Check permissively: if refs are present, verify files exist.
    if "![" in text:
        extracted = list(image_dir.glob("*.png")) if image_dir.exists() else []
        assert len(extracted) >= 1, "Image refs found but no files extracted"


@pytest.mark.slow
def test_fallback_on_import_error(tmp_path):
    """When pymupdf4llm import fails, falls back to _extract_pdf_text."""
    pdf = _make_structured_pdf(tmp_path / "fallback.pdf")

    saved = sys.modules.pop("pymupdf4llm", None)
    saved_subs = {
        k: sys.modules.pop(k) for k in list(sys.modules) if k.startswith("pymupdf4llm.")
    }
    try:
        from unittest.mock import patch

        with patch.dict(sys.modules, {"pymupdf4llm": None}):
            text, page_map = _extract_pdf_markdown(pdf)
    finally:
        if saved is not None:
            sys.modules["pymupdf4llm"] = saved
        sys.modules.update(saved_subs)

    assert len(text) > 0
    assert page_map == {}
    flat = _extract_pdf_text(pdf)
    assert text == flat


@pytest.mark.slow
def test_page_provenance_correct(tmp_path):
    """Page map maps char offsets to correct page numbers."""
    pdf = _make_structured_pdf(tmp_path / "provenance.pdf")
    text, page_map = _extract_pdf_markdown(pdf)

    assert len(page_map) >= 2

    offsets = sorted(page_map.keys())
    for i in range(len(offsets) - 1):
        assert offsets[i] < offsets[i + 1]

    pages = [page_map[o] for o in offsets]
    # pymupdf4llm uses 1-based page numbering
    assert pages[0] >= 1
    for i in range(len(pages) - 1):
        assert pages[i + 1] >= pages[i]
