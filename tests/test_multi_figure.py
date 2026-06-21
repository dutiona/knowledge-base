"""Tests for multi-figure detection: bbox clustering and page cropping."""

import io
import pytest
from PIL import Image

from knowledge_base.db import DEFAULT_EMBED_DIM
from knowledge_base.vision import (
    _cluster_bboxes,
    _crop_regions,
    _elements_in_region,
    _split_cluster_x,
    _CLUSTER_GAP_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_elements(bboxes: list[tuple[float, float, float, float]]) -> list[dict]:
    """Build minimal OmniParser element dicts from bboxes."""
    return [{"type": "text", "bbox": list(b), "content": f"el{i}"} for i, b in enumerate(bboxes)]


def _make_png(width: int = 200, height: int = 200, color: str = "white") -> bytes:
    """Create a solid-color PNG for cropping tests."""
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


IMAGE_SIZE = {"width": 1000, "height": 1000}


# ---------------------------------------------------------------------------
# _cluster_bboxes
# ---------------------------------------------------------------------------


class TestClusterBboxes:
    def test_empty_elements_returns_fullpage(self):
        result = _cluster_bboxes([], IMAGE_SIZE)
        assert result == [(0.0, 0.0, 1.0, 1.0)]

    def test_single_element_returns_fullpage(self):
        elements = _make_elements([(0.1, 0.1, 0.5, 0.5)])
        result = _cluster_bboxes(elements, IMAGE_SIZE)
        assert result == [(0.0, 0.0, 1.0, 1.0)]

    def test_elements_missing_bbox_skipped(self):
        elements = [{"type": "text", "content": "no bbox"}, {"type": "icon"}]
        result = _cluster_bboxes(elements, IMAGE_SIZE)
        assert result == [(0.0, 0.0, 1.0, 1.0)]

    def test_two_vertical_clusters(self):
        """Two groups of elements separated by a large y-gap."""
        top = [(0.1, 0.05, 0.9, 0.15), (0.1, 0.16, 0.9, 0.35)]
        bottom = [(0.1, 0.55, 0.9, 0.65), (0.1, 0.66, 0.9, 0.85)]
        elements = _make_elements(top + bottom)
        regions = _cluster_bboxes(elements, IMAGE_SIZE)
        assert len(regions) == 2
        # First region should cover top area, second should cover bottom
        assert regions[0][3] < regions[1][1]  # top.y2 < bottom.y1

    def test_two_horizontal_clusters(self):
        """Two groups of elements separated by a large x-gap (side-by-side)."""
        left = [(0.05, 0.1, 0.35, 0.9)]
        right = [(0.55, 0.1, 0.85, 0.9)]
        elements = _make_elements(left + right)
        regions = _cluster_bboxes(elements, IMAGE_SIZE)
        assert len(regions) == 2
        # Regions should be left and right
        assert regions[0][2] < regions[1][0]  # left.x2 < right.x1

    def test_2x2_grid(self):
        """Four quadrant figures should produce 4 regions."""
        tl = [(0.05, 0.05, 0.35, 0.35)]
        tr = [(0.55, 0.05, 0.85, 0.35)]
        bl = [(0.05, 0.55, 0.35, 0.85)]
        br = [(0.55, 0.55, 0.85, 0.85)]
        elements = _make_elements(tl + tr + bl + br)
        regions = _cluster_bboxes(elements, IMAGE_SIZE)
        assert len(regions) == 4

    def test_close_elements_not_split(self):
        """Elements within gap threshold should stay in one cluster."""
        bboxes = [(0.1, 0.1, 0.9, 0.2), (0.1, 0.22, 0.9, 0.3)]
        elements = _make_elements(bboxes)
        regions = _cluster_bboxes(elements, IMAGE_SIZE)
        assert len(regions) == 1
        assert regions == [(0.0, 0.0, 1.0, 1.0)]

    def test_inverted_bbox_normalised(self):
        """Bboxes with x1>x2 or y1>y2 should be normalised."""
        bboxes = [(0.9, 0.9, 0.1, 0.1), (0.9, 0.05, 0.1, 0.01)]
        elements = _make_elements(bboxes)
        # Should not crash, and should detect a gap
        regions = _cluster_bboxes(elements, IMAGE_SIZE)
        assert len(regions) >= 1

    def test_custom_gap_threshold(self):
        """A smaller threshold should produce more clusters."""
        bboxes = [(0.1, 0.0, 0.9, 0.1), (0.1, 0.15, 0.9, 0.25)]
        elements = _make_elements(bboxes)
        # With default threshold (0.08), gap of 0.05 should NOT split
        regions_default = _cluster_bboxes(elements, IMAGE_SIZE)
        assert len(regions_default) == 1
        # With smaller threshold (0.03), gap of 0.05 should split
        regions_small = _cluster_bboxes(elements, IMAGE_SIZE, gap_threshold=0.03)
        assert len(regions_small) == 2


# ---------------------------------------------------------------------------
# _split_cluster_x
# ---------------------------------------------------------------------------


class TestSplitClusterX:
    def test_single_element(self):
        result = _split_cluster_x([(0.1, 0.1, 0.3, 0.3)], _CLUSTER_GAP_THRESHOLD)
        assert len(result) == 1

    def test_side_by_side(self):
        cluster = [(0.05, 0.1, 0.35, 0.4), (0.55, 0.1, 0.85, 0.4)]
        result = _split_cluster_x(cluster, _CLUSTER_GAP_THRESHOLD)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# _elements_in_region
# ---------------------------------------------------------------------------


class TestElementsInRegion:
    def test_filters_by_center(self):
        elements = _make_elements([(0.1, 0.1, 0.3, 0.3), (0.6, 0.6, 0.8, 0.8)])
        # Region covers top-left quadrant only
        result = _elements_in_region(elements, (0.0, 0.0, 0.5, 0.5))
        assert len(result) == 1
        assert result[0]["content"] == "el0"

    def test_all_inside(self):
        elements = _make_elements([(0.1, 0.1, 0.4, 0.4), (0.2, 0.2, 0.3, 0.3)])
        result = _elements_in_region(elements, (0.0, 0.0, 1.0, 1.0))
        assert len(result) == 2

    def test_none_inside(self):
        elements = _make_elements([(0.6, 0.6, 0.9, 0.9)])
        result = _elements_in_region(elements, (0.0, 0.0, 0.4, 0.4))
        assert len(result) == 0

    def test_skips_missing_bbox(self):
        elements = [{"type": "text", "content": "no bbox"}]
        result = _elements_in_region(elements, (0.0, 0.0, 1.0, 1.0))
        assert len(result) == 0


# ---------------------------------------------------------------------------
# _crop_regions
# ---------------------------------------------------------------------------


class TestCropRegions:
    def test_single_fullpage_crop(self):
        png = _make_png(400, 600)
        crops = _crop_regions(png, [(0.0, 0.0, 1.0, 1.0)], {"width": 400, "height": 600}, padding=0.0)
        assert len(crops) == 1
        img = Image.open(io.BytesIO(crops[0]))
        assert img.size == (400, 600)

    def test_two_vertical_crops(self):
        png = _make_png(400, 600)
        regions = [(0.0, 0.0, 1.0, 0.4), (0.0, 0.6, 1.0, 1.0)]
        crops = _crop_regions(png, regions, {"width": 400, "height": 600}, padding=0.0)
        assert len(crops) == 2
        top = Image.open(io.BytesIO(crops[0]))
        bottom = Image.open(io.BytesIO(crops[1]))
        assert top.size == (400, 240)
        assert bottom.size == (400, 240)

    def test_padding_expands_crop(self):
        png = _make_png(400, 400)
        regions = [(0.25, 0.25, 0.75, 0.75)]  # Center 200x200
        # With padding=0.1, should expand by 10% of region dimension
        crops = _crop_regions(png, regions, {"width": 400, "height": 400}, padding=0.1)
        img = Image.open(io.BytesIO(crops[0]))
        # 200px region + 20px padding each side = 240, but clamped to image bounds
        assert img.size[0] == 240
        assert img.size[1] == 240

    def test_padding_clamped_to_bounds(self):
        png = _make_png(200, 200)
        regions = [(0.0, 0.0, 0.5, 0.5)]  # Top-left quadrant, 100x100
        crops = _crop_regions(png, regions, {"width": 200, "height": 200}, padding=0.2)
        img = Image.open(io.BytesIO(crops[0]))
        # Padding would try to go below 0, clamped
        assert img.size[0] <= 200
        assert img.size[1] <= 200

    def test_output_is_valid_png(self):
        png = _make_png(300, 300)
        regions = [(0.1, 0.1, 0.5, 0.5)]
        crops = _crop_regions(png, regions, {"width": 300, "height": 300})
        assert len(crops) == 1
        # Should be parseable as PNG
        img = Image.open(io.BytesIO(crops[0]))
        assert img.format == "PNG"


# ---------------------------------------------------------------------------
# Integration: extract_figures pipeline with mocked OmniParser + vision
# ---------------------------------------------------------------------------


class TestMultiFigureIntegration:
    """Test that the pipeline sends crops when OmniParser detects multiple regions."""

    @pytest.fixture
    def _db_and_pdf(self, tmp_path):
        """Set up a minimal DB and a 2-page PDF with dummy content."""
        import fitz
        from knowledge_base.db import get_connection, init_schema

        # Create a simple PDF
        pdf_path = tmp_path / "test.pdf"
        doc = fitz.open()
        # Page 0: will simulate multi-figure
        page = doc.new_page(width=612, height=792)
        page.insert_text((100, 100), "Figure 1a")
        page.insert_text((400, 100), "Figure 1b")
        page.insert_text((100, 500), "Figure 1c")
        page.insert_text((400, 500), "Figure 1d")
        doc.save(str(pdf_path))
        doc.close()

        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_schema(conn)

        # Register paper
        conn.execute(
            "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
            "VALUES ('abc', 'abstract', 'pdf', ?, 0)",
            (str(pdf_path),),
        )
        chunk_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO papers (title, abstract_chunk_id) VALUES ('Test Paper', ?)",
            (chunk_id,),
        )
        conn.commit()

        return conn, str(pdf_path)

    def test_multi_region_sends_multiple_vision_calls(self, _db_and_pdf, monkeypatch):
        """When OmniParser detects 2 regions, vision is called twice (once per crop)."""
        from knowledge_base import vision

        conn, _pdf_path = _db_and_pdf

        # Mock OmniParser to return elements in two vertical clusters
        omni_result = {
            "elements": [
                {
                    "type": "text",
                    "bbox": [0.05, 0.05, 0.45, 0.35],
                    "content": "top-left",
                },
                {
                    "type": "text",
                    "bbox": [0.55, 0.05, 0.95, 0.35],
                    "content": "top-right",
                },
                {
                    "type": "text",
                    "bbox": [0.05, 0.55, 0.45, 0.85],
                    "content": "bot-left",
                },
                {
                    "type": "text",
                    "bbox": [0.55, 0.55, 0.95, 0.85],
                    "content": "bot-right",
                },
            ],
            "image_size": {"width": 1224, "height": 1584},
        }

        monkeypatch.setattr(vision, "_get_omniparser_config", lambda conn: "/fake/omniparser")
        monkeypatch.setattr(vision, "_run_omniparser", lambda path, omni_path, **kw: omni_result)

        # Track vision calls
        vision_calls = []

        def fake_vision_call(image_b64, prompt, *, base_url, model):
            vision_calls.append(image_b64)
            return [
                {
                    "figure_type": "diagram",
                    "description": f"figure from crop {len(vision_calls)}",
                    "title": None,
                    "entities_mentioned": [],
                }
            ]

        monkeypatch.setattr(vision, "_vision_call", fake_vision_call)
        monkeypatch.setattr(
            vision,
            "_embed_with_config",
            lambda conn, texts: [[0.0] * DEFAULT_EMBED_DIM] * len(texts),
        )

        result = vision.extract_figures(conn, paper_id=1, pages=[0], confirmed=True)

        assert result["figures_found"] == 4, f"Expected 4 figures, got {result}"
        assert len(vision_calls) == 4, f"Expected 4 vision calls (one per crop), got {len(vision_calls)}"

        # Verify per-region element filtering: each figure should only have
        # elements from its own region, not all 4 elements
        import json

        rows = conn.execute("SELECT metadata FROM chunks WHERE source_type = 'figure'").fetchall()
        assert len(rows) == 4
        for row in rows:
            meta = json.loads(row[0])
            if "omniparser_elements" in meta:
                assert len(meta["omniparser_elements"]) == 1, (
                    f"Expected 1 element per region, got {len(meta['omniparser_elements'])}"
                )

    def test_no_omniparser_falls_back_to_full_page(self, _db_and_pdf, monkeypatch):
        """Without OmniParser, pipeline sends full page (original behavior)."""
        from knowledge_base import vision

        conn, _pdf_path = _db_and_pdf

        monkeypatch.setattr(vision, "_get_omniparser_config", lambda conn: None)

        vision_calls = []

        def fake_vision_call(image_b64, prompt, *, base_url, model):
            vision_calls.append(image_b64)
            return [
                {
                    "figure_type": "diagram",
                    "description": "single figure",
                    "title": None,
                    "entities_mentioned": [],
                }
            ]

        monkeypatch.setattr(vision, "_vision_call", fake_vision_call)
        monkeypatch.setattr(
            vision,
            "_embed_with_config",
            lambda conn, texts: [[0.0] * DEFAULT_EMBED_DIM] * len(texts),
        )

        result = vision.extract_figures(conn, paper_id=1, pages=[0], confirmed=True)

        assert len(vision_calls) == 1, "Should send 1 full-page call without OmniParser"
        assert result["figures_found"] == 1

    def test_single_cluster_sends_full_page(self, _db_and_pdf, monkeypatch):
        """When OmniParser finds elements but they form a single cluster, send full page."""
        from knowledge_base import vision

        conn, _pdf_path = _db_and_pdf

        omni_result = {
            "elements": [
                {"type": "text", "bbox": [0.1, 0.1, 0.9, 0.3], "content": "line1"},
                {"type": "text", "bbox": [0.1, 0.32, 0.9, 0.5], "content": "line2"},
            ],
            "image_size": {"width": 1224, "height": 1584},
        }

        monkeypatch.setattr(vision, "_get_omniparser_config", lambda conn: "/fake/omniparser")
        monkeypatch.setattr(vision, "_run_omniparser", lambda path, omni_path, **kw: omni_result)

        vision_calls = []

        def fake_vision_call(image_b64, prompt, *, base_url, model):
            vision_calls.append(image_b64)
            return [
                {
                    "figure_type": "chart",
                    "description": "one chart",
                    "title": None,
                    "entities_mentioned": [],
                }
            ]

        monkeypatch.setattr(vision, "_vision_call", fake_vision_call)
        monkeypatch.setattr(
            vision,
            "_embed_with_config",
            lambda conn, texts: [[0.0] * DEFAULT_EMBED_DIM] * len(texts),
        )

        vision.extract_figures(conn, paper_id=1, pages=[0], confirmed=True)

        assert len(vision_calls) == 1, "Single cluster should send full page"

    def test_omniparser_failure_falls_back(self, _db_and_pdf, monkeypatch):
        """When OmniParser fails (returns None), pipeline falls back to full page."""
        from knowledge_base import vision

        conn, _pdf_path = _db_and_pdf

        monkeypatch.setattr(vision, "_get_omniparser_config", lambda conn: "/fake/omniparser")
        monkeypatch.setattr(vision, "_run_omniparser", lambda path, omni_path, **kw: None)

        vision_calls = []

        def fake_vision_call(image_b64, prompt, *, base_url, model):
            vision_calls.append(1)
            return [
                {
                    "figure_type": "diagram",
                    "description": "fallback figure",
                    "title": None,
                    "entities_mentioned": [],
                }
            ]

        monkeypatch.setattr(vision, "_vision_call", fake_vision_call)
        monkeypatch.setattr(
            vision,
            "_embed_with_config",
            lambda conn, texts: [[0.0] * DEFAULT_EMBED_DIM] * len(texts),
        )

        result = vision.extract_figures(conn, paper_id=1, pages=[0], confirmed=True)

        assert len(vision_calls) == 1, "OmniParser failure should fall back to full page"
        assert result["figures_found"] == 1
