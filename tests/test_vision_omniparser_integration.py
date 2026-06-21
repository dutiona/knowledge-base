"""Integration tests for OmniParser OCR in the hybrid enrichment pipeline.

These tests exercise the real OmniParser subprocess (no mocking of _run_omniparser).
They validate that the hybrid enrichment pipeline correctly:
1. Produces [OCR] sections alongside [Caption] and [Description]
2. Tracks "omniparser" in enrichment_layers metadata
3. Sets ocr_source: "omniparser" in metadata
4. Keeps OCR text OUT of the [Description] section (duplication guard)
5. Produces no [OCR] section for figures with no baked-in text (graceful empty)

Requires:
- OmniParser installed at ~/.local/opt/omniparser/ with .venv/bin/python
- GPU or CPU inference for OmniParser models

Refs: #343, #111, #340, #335
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import fitz
import pytest

from knowledge_base.db import get_connection, init_schema
from knowledge_base.embeddings import DEFAULT_EMBED_DIM
from knowledge_base.vision import configure_omniparser, extract_figures

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OMNIPARSER_PATH = Path(os.environ.get("OMNIPARSER_PATH", str(Path.home() / ".local" / "opt" / "omniparser")))


def _omniparser_available() -> bool:
    """Check whether OmniParser is installed and runnable."""
    venv_python = _OMNIPARSER_PATH / ".venv" / "bin" / "python"
    parse_script = _OMNIPARSER_PATH / "parse.py"
    return venv_python.is_file() and parse_script.is_file()


skip_no_omniparser = pytest.mark.skipif(
    not _omniparser_available(),
    reason=f"OmniParser not installed at {_OMNIPARSER_PATH}",
)

# All tests in this module are slow (real subprocess calls).
pytestmark = [pytest.mark.slow, skip_no_omniparser]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chart_png(path: Path) -> None:
    """Create a PNG image containing a simple bar chart with text labels.

    Uses fitz (PyMuPDF) to draw shapes + text, then export as PNG.
    The image has axis labels, a title, and bar annotations — all baked-in
    raster text that OmniParser should detect.
    """
    # Create a 400x300 document page as our canvas
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)

    # Background
    page.draw_rect(fitz.Rect(0, 0, 400, 300), color=(1, 1, 1), fill=(1, 1, 1))

    # Title
    page.insert_text((100, 30), "Accuracy vs Epochs", fontsize=16)

    # Y-axis label
    page.insert_text((10, 160), "Accuracy", fontsize=10)

    # X-axis label
    page.insert_text((170, 290), "Epochs", fontsize=10)

    # Bar labels
    page.insert_text((85, 265), "10", fontsize=9)
    page.insert_text((165, 265), "20", fontsize=9)
    page.insert_text((245, 265), "30", fontsize=9)

    # Draw bars
    bars = [
        (70, 200, 120, 250),  # bar 1 (shorter)
        (150, 120, 200, 250),  # bar 2 (medium)
        (230, 80, 280, 250),  # bar 3 (tallest)
    ]
    for x0, y0, x1, y1 in bars:
        page.draw_rect(
            fitz.Rect(x0, y0, x1, y1),
            color=(0.2, 0.4, 0.8),
            fill=(0.3, 0.5, 0.9),
        )

    # Bar value annotations
    page.insert_text((80, 195), "0.72", fontsize=8)
    page.insert_text((160, 115), "0.88", fontsize=8)
    page.insert_text((240, 75), "0.95", fontsize=8)

    # Legend
    page.insert_text((300, 60), "ResNet", fontsize=9)

    # Render to PNG
    pix = page.get_pixmap(dpi=150)
    pix.save(str(path))
    doc.close()


def _make_blank_png(path: Path) -> None:
    """Create a solid-color PNG with no text — OmniParser should find nothing."""
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.draw_rect(fitz.Rect(0, 0, 200, 200), color=(0.9, 0.9, 0.9), fill=(0.9, 0.9, 0.9))
    pix = page.get_pixmap(dpi=72)
    pix.save(str(path))
    doc.close()


def _make_pdf_with_embedded_image(pdf_path: Path, image_path: Path) -> str:
    """Create a PDF with an embedded raster image (the chart PNG).

    Inserts the image on page 0 and adds a text caption below it,
    simulating a real research paper figure page.
    """
    doc = fitz.open()
    page = doc.new_page()
    img_rect = fitz.Rect(72, 72, 500, 372)
    page.insert_image(img_rect, filename=str(image_path))
    page.insert_text((72, 400), "Figure 1: Accuracy comparison across training epochs.")
    # Second page: plain text (no figures)
    page2 = doc.new_page()
    page2.insert_text((72, 72), "This page has no figures.")
    doc.save(str(pdf_path))
    doc.close()
    return str(pdf_path)


def _setup_paper(tmp_path: Path, pdf_path: str) -> tuple:
    """Create DB, paper record, and ingest chunks referencing the PDF.

    Returns (conn, paper_id).
    """
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Insert base chunk so paper has a source_uri
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('abs_hash', 'abstract text', 'pdf', ?, 0)",
        (pdf_path,),
    )
    chunk_id = conn.execute("SELECT id FROM chunks WHERE content_hash = 'abs_hash'").fetchone()["id"]
    conn.execute(
        "INSERT INTO papers (title, abstract_chunk_id) VALUES ('Test Paper', ?)",
        (chunk_id,),
    )
    paper_id = conn.execute("SELECT id FROM papers WHERE title = 'Test Paper'").fetchone()["id"]
    conn.commit()
    return conn, paper_id


def _setup_extracted_image(conn, source_uri: str, image_dir: Path, image_name: str, image_path: Path) -> None:
    """Register an extracted image in the DB (simulating pymupdf4llm ingest).

    Creates an ingest chunk referencing the image and a caption, then copies
    the image file to the expected directory.
    """
    import shutil

    image_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(image_path, image_dir / image_name)

    # Insert chunk with image ref and caption (mimics pymupdf4llm output)
    chunk_text = (
        f"![]({image_name})\n\n"
        "Figure 1: Accuracy comparison across training epochs.\n\n"
        "The chart shows ResNet accuracy improving over 30 epochs."
    )
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, "
        "chunk_index, metadata) VALUES (?, ?, 'pdf', ?, 1, ?)",
        (
            f"caption_hash_{image_name}",
            chunk_text,
            source_uri,
            json.dumps({"images": [image_name], "pages": [1]}),
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@patch("knowledge_base.vision._embed_with_config")
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision.pdf_image_dir")
def test_ocr_section_appears_with_caption_and_description(mock_img_dir, mock_vision, mock_embed, tmp_path):
    """AC-1: [OCR] section appears in figure chunk content alongside [Caption] and [Description]."""
    # Create chart PNG with baked-in text
    chart_png = tmp_path / "chart.png"
    _make_chart_png(chart_png)

    # Create PDF with embedded image
    pdf_path = _make_pdf_with_embedded_image(tmp_path / "paper.pdf", chart_png)

    # Setup DB
    conn, paper_id = _setup_paper(tmp_path, pdf_path)

    # Configure OmniParser (real)
    configure_omniparser(conn, path=str(_OMNIPARSER_PATH))

    # Setup extracted image (simulating pymupdf4llm ingest)
    image_dir = tmp_path / "extracted_images"
    mock_img_dir.return_value = image_dir
    _setup_extracted_image(conn, pdf_path, image_dir, "img-0001.png", chart_png)

    # Mock vision LLM (we're testing OmniParser, not the LLM)
    mock_vision.return_value = [
        {
            "figure_type": "chart",
            "description": "A bar chart showing accuracy over epochs",
            "title": "Fig 1",
            "entities_mentioned": ["ResNet"],
        }
    ]
    mock_embed.return_value = [[0.1] * DEFAULT_EMBED_DIM]

    result = extract_figures(conn, paper_id=paper_id, pages=[0], confirmed=True)

    assert result["chunks_created"] >= 1
    assert result["omniparser_enriched"] >= 1

    fig_chunk = conn.execute("SELECT content, metadata FROM chunks WHERE source_type = 'figure'").fetchone()
    assert fig_chunk is not None

    content = fig_chunk["content"]

    # All three section markers should be present
    assert "[Caption]" in content, f"Missing [Caption] in: {content!r}"
    assert "[Description]" in content, f"Missing [Description] in: {content!r}"
    assert "[OCR]" in content, f"Missing [OCR] in: {content!r}"


@patch("knowledge_base.vision._embed_with_config")
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision.pdf_image_dir")
def test_enrichment_layers_includes_omniparser(mock_img_dir, mock_vision, mock_embed, tmp_path):
    """AC-2: enrichment_layers metadata includes "omniparser"."""
    chart_png = tmp_path / "chart.png"
    _make_chart_png(chart_png)
    pdf_path = _make_pdf_with_embedded_image(tmp_path / "paper.pdf", chart_png)
    conn, paper_id = _setup_paper(tmp_path, pdf_path)
    configure_omniparser(conn, path=str(_OMNIPARSER_PATH))

    image_dir = tmp_path / "extracted_images"
    mock_img_dir.return_value = image_dir
    _setup_extracted_image(conn, pdf_path, image_dir, "img-0001.png", chart_png)

    mock_vision.return_value = [
        {
            "figure_type": "chart",
            "description": "Bar chart",
            "title": "Fig 1",
            "entities_mentioned": [],
        }
    ]
    mock_embed.return_value = [[0.1] * DEFAULT_EMBED_DIM]

    extract_figures(conn, paper_id=paper_id, pages=[0], confirmed=True)

    fig_chunk = conn.execute("SELECT metadata FROM chunks WHERE source_type = 'figure'").fetchone()
    meta = json.loads(fig_chunk["metadata"])

    assert "enrichment_layers" in meta
    assert "omniparser" in meta["enrichment_layers"], (
        f"Expected 'omniparser' in enrichment_layers, got: {meta['enrichment_layers']}"
    )


@patch("knowledge_base.vision._embed_with_config")
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision.pdf_image_dir")
def test_ocr_source_metadata(mock_img_dir, mock_vision, mock_embed, tmp_path):
    """AC-3: ocr_source: "omniparser" is set in metadata."""
    chart_png = tmp_path / "chart.png"
    _make_chart_png(chart_png)
    pdf_path = _make_pdf_with_embedded_image(tmp_path / "paper.pdf", chart_png)
    conn, paper_id = _setup_paper(tmp_path, pdf_path)
    configure_omniparser(conn, path=str(_OMNIPARSER_PATH))

    image_dir = tmp_path / "extracted_images"
    mock_img_dir.return_value = image_dir
    _setup_extracted_image(conn, pdf_path, image_dir, "img-0001.png", chart_png)

    mock_vision.return_value = [
        {
            "figure_type": "chart",
            "description": "Bar chart",
            "title": "Fig 1",
            "entities_mentioned": [],
        }
    ]
    mock_embed.return_value = [[0.1] * DEFAULT_EMBED_DIM]

    extract_figures(conn, paper_id=paper_id, pages=[0], confirmed=True)

    fig_chunk = conn.execute("SELECT metadata FROM chunks WHERE source_type = 'figure'").fetchone()
    meta = json.loads(fig_chunk["metadata"])

    assert meta.get("ocr_source") == "omniparser", f"Expected ocr_source='omniparser', got: {meta.get('ocr_source')!r}"


@patch("knowledge_base.vision._embed_with_config")
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision.pdf_image_dir")
def test_ocr_text_not_in_description_section(mock_img_dir, mock_vision, mock_embed, tmp_path):
    """AC-4: OCR text does NOT appear inside [Description] (duplication guard).

    The [Description] section should contain only the vision LLM output.
    OCR text must appear exclusively in the [OCR] section.
    """
    chart_png = tmp_path / "chart.png"
    _make_chart_png(chart_png)
    pdf_path = _make_pdf_with_embedded_image(tmp_path / "paper.pdf", chart_png)
    conn, paper_id = _setup_paper(tmp_path, pdf_path)
    configure_omniparser(conn, path=str(_OMNIPARSER_PATH))

    image_dir = tmp_path / "extracted_images"
    mock_img_dir.return_value = image_dir
    _setup_extracted_image(conn, pdf_path, image_dir, "img-0001.png", chart_png)

    vision_description = "A bar chart comparing neural network performance metrics"
    mock_vision.return_value = [
        {
            "figure_type": "chart",
            "description": vision_description,
            "title": "Fig 1",
            "entities_mentioned": [],
        }
    ]
    mock_embed.return_value = [[0.1] * DEFAULT_EMBED_DIM]

    extract_figures(conn, paper_id=paper_id, pages=[0], confirmed=True)

    fig_chunk = conn.execute("SELECT content FROM chunks WHERE source_type = 'figure'").fetchone()
    content = fig_chunk["content"]

    # Parse sections: extract the text after each marker
    sections: dict[str, str] = {}
    current_marker = None
    current_lines: list[str] = []
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("[") and "]" in stripped:
            bracket_end = stripped.index("]")
            marker = stripped[: bracket_end + 1]
            if marker in ("[Caption]", "[Description]", "[OCR]"):
                if current_marker:
                    sections[current_marker] = "\n".join(current_lines).strip()
                current_marker = marker
                # Text on same line after marker
                current_lines = [stripped[bracket_end + 1 :].strip()]
                continue
        if current_marker:
            current_lines.append(line)
    if current_marker:
        sections[current_marker] = "\n".join(current_lines).strip()

    # [Description] must contain the vision LLM description, not OCR text
    assert "[Description]" in sections, f"No [Description] section found in: {content!r}"
    desc_text = sections["[Description]"]
    assert vision_description in desc_text

    # If [OCR] is present, its text must NOT appear in [Description].
    # We check both the formatter prefixes AND actual OCR tokens to guard
    # against leaks regardless of formatter wording changes.
    if "[OCR]" in sections:
        ocr_text = sections["[OCR]"]
        assert "Detected text:" not in desc_text, f"OCR formatter prefix leaked into [Description]: {desc_text!r}"
        assert "Detected elements:" not in desc_text, f"OCR formatter prefix leaked into [Description]: {desc_text!r}"
        # Also check that recognizable OCR tokens from the chart don't
        # appear in the description section (tests the actual invariant,
        # not just the formatter's prefix).
        ocr_tokens = {"Accuracy", "Epochs", "ResNet"}
        leaked = {t for t in ocr_tokens if t in desc_text and t in ocr_text}
        assert not leaked, f"OCR tokens {leaked} leaked into [Description]: {desc_text!r}"


@patch("knowledge_base.vision._embed_with_config")
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision.pdf_image_dir")
def test_no_ocr_section_for_blank_image(mock_img_dir, mock_vision, mock_embed, tmp_path):
    """AC-5: Figures with no baked-in text produce no [OCR] section (graceful empty)."""
    blank_png = tmp_path / "blank.png"
    _make_blank_png(blank_png)

    # Create PDF with blank image
    doc = fitz.open()
    page = doc.new_page()
    page.insert_image(fitz.Rect(72, 72, 300, 300), filename=str(blank_png))
    pdf_path = str(tmp_path / "paper_blank.pdf")
    doc.save(pdf_path)
    doc.close()

    conn, paper_id = _setup_paper(tmp_path, pdf_path)
    configure_omniparser(conn, path=str(_OMNIPARSER_PATH))

    # Setup extracted blank image
    image_dir = tmp_path / "extracted_images"
    mock_img_dir.return_value = image_dir
    _setup_extracted_image(conn, pdf_path, image_dir, "img-0001.png", blank_png)

    mock_vision.return_value = [
        {
            "figure_type": "photo",
            "description": "A gray rectangle",
            "title": None,
            "entities_mentioned": [],
        }
    ]
    mock_embed.return_value = [[0.1] * DEFAULT_EMBED_DIM]

    result = extract_figures(conn, paper_id=paper_id, pages=[0], confirmed=True)

    assert result["chunks_created"] >= 1
    # OmniParser should find nothing on a blank image
    assert result["omniparser_enriched"] == 0

    fig_chunk = conn.execute("SELECT content, metadata FROM chunks WHERE source_type = 'figure'").fetchone()
    content = fig_chunk["content"]
    meta = json.loads(fig_chunk["metadata"])

    # No [OCR] section should be present
    assert "[OCR]" not in content, f"Unexpected [OCR] section for blank image: {content!r}"
    # omniparser should NOT be in enrichment_layers
    assert "omniparser" not in meta.get("enrichment_layers", [])
    assert "ocr_source" not in meta
