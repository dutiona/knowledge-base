"""Vision-augmented figure extraction for research papers."""

from __future__ import annotations

import base64
import dataclasses
import json
import logging
import os
import re
import sqlite3
import subprocess
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import fitz
import httpx

from .embeddings import _get_ollama_url
from .db import delete_chunks_cascade, get_vec_table_name
from .exceptions import NotFoundError, ValidationError
from .ingest import (
    _content_hash,
    _embed_with_config,
    _insert_chunk,
    pdf_image_dir,
)
from .web import _cleanup_figure_fk_refs

logger = logging.getLogger(__name__)

__all__ = [
    "configure_omniparser",
    "configure_vision",
    "estimate_figures_time",
    "extract_figures",
]

_CAPTION_RE = re.compile(r"(?:Figure|Fig\.|Table)\s+\d+", re.IGNORECASE)

# Figure chunk_index encoding: 1_000_000 + page_num * FIGS_PER_PAGE + fig_idx
_FIGURE_BASE = 1_000_000
_FIGS_PER_PAGE = 1_000


# ---------------------------------------------------------------------------
# Pipeline dataclasses
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class PaperContext:
    """Validated paper identity and resolved PDF path."""

    paper_id: int
    title: str
    source_uri: str
    pdf_path: Path


@dataclasses.dataclass
class DualPathInputs:
    """Inputs for the dual-path extraction pipeline."""

    extracted_images: list[tuple[Path, int]]
    vector_pages: list[int]
    pages_with_images: set[int]


@dataclasses.dataclass
class OmniParserResults:
    """Aggregated OmniParser output for both paths."""

    page_data: dict[
        int,
        tuple[dict | None, list[tuple[float, float, float, float]], list[bytes]],
    ]
    image_data: dict[
        str,
        tuple[dict | None, list[tuple[float, float, float, float]], list[bytes]],
    ]
    elapsed: float


@dataclasses.dataclass
class VisionResults:
    """Aggregated vision-model output."""

    page_results: dict[int, list[dict]]
    errors: list[str]
    pages_failed: int
    elapsed: float


# ---------------------------------------------------------------------------
# Step 1: Config
# ---------------------------------------------------------------------------


def _get_vision_config(conn: sqlite3.Connection) -> dict:
    """Read vision configuration from config table."""
    model_row = conn.execute(
        "SELECT value FROM config WHERE key = 'vision_model'"
    ).fetchone()
    base_url_row = conn.execute(
        "SELECT value FROM config WHERE key = 'vision_base_url'"
    ).fetchone()

    base_url = base_url_row["value"] if base_url_row else _get_ollama_url()

    return {
        "model": model_row["value"] if model_row else "gemma3:27b",
        "base_url": base_url.rstrip("/").removesuffix("/v1"),
    }


def configure_vision(
    conn: sqlite3.Connection,
    model: str | None = None,
    base_url: str | None = None,
) -> dict:
    """Configure vision model settings."""
    if model:
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES ('vision_model', ?)",
            (model,),
        )
    if base_url:
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES ('vision_base_url', ?)",
            (base_url,),
        )
    conn.commit()
    return _get_vision_config(conn)


def _get_omniparser_config(conn: sqlite3.Connection) -> str | None:
    """Read omniparser_path from config table. Returns None when unset."""
    row = conn.execute(
        "SELECT value FROM config WHERE key = 'omniparser_path'"
    ).fetchone()
    return row["value"] if row else None


def _validate_omniparser_path(path: str) -> Path:
    """Validate and resolve an OmniParser directory path.

    Returns the resolved absolute path.  Raises ``ValidationError`` on any
    policy violation.

    Security policy (trust model):
      The caller is the local MCP user — the same principal whose files are
      executed.  Validation prevents accidental mis-configuration (relative
      paths, stale symlinks, non-executable interpreters) rather than
      defending against a hostile local user.
    """
    omni_dir = Path(path)

    if not omni_dir.is_absolute():
        raise ValidationError(f"omniparser_path must be an absolute path, got: {path}")

    # Resolve symlinks and .. components so the stored path is canonical.
    omni_dir = omni_dir.resolve()

    parse_script = omni_dir / "parse.py"
    venv_python = omni_dir / ".venv" / "bin" / "python"

    if not parse_script.exists():
        raise ValidationError(f"parse.py not found at {parse_script}")
    if not venv_python.exists():
        raise ValidationError(f"venv python not found at {venv_python}")
    if not os.access(venv_python, os.X_OK):
        raise ValidationError(f"python binary is not executable at {venv_python}")

    return omni_dir


def configure_omniparser(
    conn: sqlite3.Connection,
    path: str | None = None,
) -> dict:
    """Configure OmniParser for figure enrichment.

    Args:
        path: None to query, "" to disable, otherwise absolute path to set.

    The path is resolved (symlinks and ``..`` flattened) and validated before
    storage.  At execution time, ``_run_omniparser`` re-validates that the
    resolved files still exist on disk.
    """
    if path is None:
        return {"omniparser_path": _get_omniparser_config(conn)}

    if path == "":
        conn.execute("DELETE FROM config WHERE key = 'omniparser_path'")
        conn.commit()
        return {"omniparser_path": None}

    omni_dir = _validate_omniparser_path(path)
    resolved = str(omni_dir)

    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('omniparser_path', ?)",
        (resolved,),
    )
    conn.commit()
    return {"omniparser_path": resolved}


# ---------------------------------------------------------------------------
# Timing & timeout constants
# ---------------------------------------------------------------------------

_ETA_SECS_PER_PAGE_BASE = 4
_ETA_SECS_PER_PAGE_OMNIPARSER = 40
_VISION_CALL_TIMEOUT = 120
_OMNIPARSER_SUBPROCESS_TIMEOUT = 120
_TIMING_DRIFT_FACTOR = 2.0


def _run_omniparser(
    png_path: Path, omniparser_path: str, timeout: int = _OMNIPARSER_SUBPROCESS_TIMEOUT
) -> dict | None:
    """Invoke OmniParser as a subprocess. Returns parsed JSON or None on failure.

    Re-validates that the configured python binary and parse script still exist
    and are accessible before spawning the subprocess.  This guards against
    paths that were valid at configuration time but have since been moved or
    deleted.
    """
    omni = Path(omniparser_path)
    venv_python = omni / ".venv" / "bin" / "python"
    parse_script = omni / "parse.py"

    if not venv_python.is_file() or not os.access(venv_python, os.X_OK):
        logger.warning(
            "OmniParser python binary missing or not executable: %s", venv_python
        )
        return None
    if not parse_script.is_file():
        logger.warning("OmniParser parse.py missing: %s", parse_script)
        return None

    json_fd, json_out = tempfile.mkstemp(suffix=".json")
    t0 = time.monotonic()
    try:
        # Close the fd so the subprocess can write to it
        os.close(json_fd)
        subprocess.run(
            [venv_python, parse_script, str(png_path), "-j", json_out],
            timeout=timeout,
            capture_output=True,
            check=True,
        )
        with open(json_out) as f:
            result = json.load(f)
        elapsed = time.monotonic() - t0
        logger.info("OmniParser completed for %s in %.1fs", png_path.name, elapsed)
        if elapsed > _ETA_SECS_PER_PAGE_OMNIPARSER * _TIMING_DRIFT_FACTOR:
            logger.warning(
                "OmniParser took %.1fs for %s (expected ~%ds) — "
                "consider raising _ETA_SECS_PER_PAGE_OMNIPARSER or _OMNIPARSER_SUBPROCESS_TIMEOUT",
                elapsed,
                png_path.name,
                _ETA_SECS_PER_PAGE_OMNIPARSER,
            )
        return result
    except (
        subprocess.TimeoutExpired,
        subprocess.CalledProcessError,
        json.JSONDecodeError,
        OSError,
    ) as exc:
        elapsed = time.monotonic() - t0
        logger.warning(
            "OmniParser failed for %s after %.1fs: %s", png_path, elapsed, exc
        )
        return None
    finally:
        Path(json_out).unlink(missing_ok=True)


_OMNIPARSER_MAX_APPEND = 500

# Minimum gap (as fraction of image height) between element clusters
# to consider them separate figure regions.
_CLUSTER_GAP_THRESHOLD = 0.08
# Padding (as fraction of region dimension) added around cropped regions.
_CROP_PADDING = 0.02


def _cluster_bboxes(
    elements: list[dict],
    image_size: dict,
    *,
    gap_threshold: float = _CLUSTER_GAP_THRESHOLD,
) -> list[tuple[float, float, float, float]]:
    """Cluster OmniParser element bboxes into spatial regions.

    Uses 1-D gap analysis on the y-axis midpoints: sort elements by vertical
    center, then split wherever the gap exceeds *gap_threshold* (fraction of
    image height).  Each cluster is then bounded by the union of its elements'
    bboxes, giving one (x1, y1, x2, y2) region per cluster (ratios 0-1).

    Returns a list of region bboxes.  A single-element list means the page has
    one contiguous region (no splitting needed).
    """
    bboxes = []
    for el in elements:
        bbox = el.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        x1, y1, x2, y2 = bbox
        # Normalise order
        if x1 > x2:
            x1, x2 = x2, x1
        if y1 > y2:
            y1, y2 = y2, y1
        bboxes.append((x1, y1, x2, y2))

    if len(bboxes) < 2:
        return [(0.0, 0.0, 1.0, 1.0)]

    # Sort by vertical midpoint
    bboxes.sort(key=lambda b: (b[1] + b[3]) / 2)

    # 1-D gap splitting on y-axis
    clusters: list[list[tuple[float, float, float, float]]] = [[bboxes[0]]]
    for prev, cur in zip(bboxes, bboxes[1:]):
        prev_bottom = prev[3]
        cur_top = cur[1]
        gap = cur_top - prev_bottom
        if gap >= gap_threshold:
            clusters.append([cur])
        else:
            clusters[-1].append(cur)

    # Also try x-axis splitting within each y-cluster
    # (handles side-by-side layouts and 2x2 grids)
    final_clusters: list[list[tuple[float, float, float, float]]] = []
    for cluster in clusters:
        sub = _split_cluster_x(cluster, gap_threshold)
        final_clusters.extend(sub)

    if len(final_clusters) < 2:
        return [(0.0, 0.0, 1.0, 1.0)]

    # Compute bounding box per cluster
    regions = []
    for cluster in final_clusters:
        rx1 = min(b[0] for b in cluster)
        ry1 = min(b[1] for b in cluster)
        rx2 = max(b[2] for b in cluster)
        ry2 = max(b[3] for b in cluster)
        regions.append((rx1, ry1, rx2, ry2))

    return regions


def _split_cluster_x(
    cluster: list[tuple[float, float, float, float]],
    gap_threshold: float,
) -> list[list[tuple[float, float, float, float]]]:
    """Try to split a cluster along the x-axis (for side-by-side figures)."""
    if len(cluster) < 2:
        return [cluster]

    cluster_x = sorted(cluster, key=lambda b: (b[0] + b[2]) / 2)
    sub_clusters: list[list[tuple[float, float, float, float]]] = [[cluster_x[0]]]
    for prev, cur in zip(cluster_x, cluster_x[1:]):
        prev_right = prev[2]
        cur_left = cur[0]
        gap = cur_left - prev_right
        if gap >= gap_threshold:
            sub_clusters.append([cur])
        else:
            sub_clusters[-1].append(cur)

    return sub_clusters


def _crop_regions(
    png_bytes: bytes,
    regions: list[tuple[float, float, float, float]],
    image_size: dict,
    *,
    padding: float = _CROP_PADDING,
) -> list[bytes]:
    """Crop a PNG image into sub-region PNGs based on ratio-bboxes.

    Args:
        png_bytes: Full-page PNG.
        regions: List of (x1, y1, x2, y2) in ratio coordinates (0-1).
        image_size: Dict with 'width' and 'height' keys (pixels).
        padding: Fractional padding to add around each crop.

    Returns:
        List of PNG bytes, one per region.
    """
    import io
    from PIL import Image

    with Image.open(io.BytesIO(png_bytes)) as img:
        w, h = img.size

        crops = []
        for x1, y1, x2, y2 in regions:
            # Convert ratios to pixels
            px1 = int(x1 * w)
            py1 = int(y1 * h)
            px2 = int(x2 * w)
            py2 = int(y2 * h)

            # Add padding
            pad_x = int((px2 - px1) * padding)
            pad_y = int((py2 - py1) * padding)
            px1 = max(0, px1 - pad_x)
            py1 = max(0, py1 - pad_y)
            px2 = min(w, px2 + pad_x)
            py2 = min(h, py2 + pad_y)

            with img.crop((px1, py1, px2, py2)) as cropped:
                buf = io.BytesIO()
                cropped.save(buf, format="PNG")
                crops.append(buf.getvalue())

    return crops


def _elements_in_region(
    elements: list[dict],
    region: tuple[float, float, float, float],
) -> list[dict]:
    """Filter OmniParser elements whose bbox center falls within *region*.

    Both element bboxes and region are in ratio coordinates (0-1).
    """
    rx1, ry1, rx2, ry2 = region
    result = []
    for el in elements:
        bbox = el.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        cx = (bbox[0] + bbox[2]) / 2
        cy = (bbox[1] + bbox[3]) / 2
        if rx1 <= cx <= rx2 and ry1 <= cy <= ry2:
            result.append(el)
    return result


def _merge_omniparser_elements(figure: dict, elements: list[dict]) -> dict:
    """Append OmniParser OCR text and icon captions to figure description.

    Deduplicates (case-insensitive), skips content < 2 chars,
    and caps total appended text at _OMNIPARSER_MAX_APPEND chars.
    Returns original dict if nothing to merge.
    """
    seen: set[str] = set()
    texts: list[str] = []
    icons: list[str] = []

    for el in elements:
        content = (el.get("content") or "").strip()
        if len(content) < 2:
            continue
        key = content.lower()
        if key in seen:
            continue
        seen.add(key)
        if el.get("type") == "text":
            texts.append(content)
        else:
            icons.append(content)

    if not texts and not icons:
        return figure

    parts: list[str] = []
    budget = _OMNIPARSER_MAX_APPEND

    if texts:
        line = "Detected text: " + ", ".join(f'"{t}"' for t in texts)
        if len(line) > budget:
            line = line[: budget - 1] + "\u2026"
        parts.append(line)
        budget -= len(line)

    if icons and budget > 20:
        line = "Detected elements: " + ", ".join(f'"{i}"' for i in icons)
        if len(line) > budget:
            line = line[: budget - 1] + "\u2026"
        parts.append(line)

    return {**figure, "description": figure["description"] + "\n\n" + "\n".join(parts)}


# ---------------------------------------------------------------------------
# Step 2: Figure validation
# ---------------------------------------------------------------------------


def _validate_figure(obj: dict) -> dict | None:
    """Validate and normalise a figure description dict.

    Required keys: figure_type (str), description (non-empty str).
    Optional: title (default None), entities_mentioned (default []).
    Returns cleaned dict or None if invalid.
    """
    figure_type = obj.get("figure_type")
    description = obj.get("description")

    if not isinstance(figure_type, str) or not figure_type:
        logger.warning("Invalid figure: missing or empty figure_type")
        return None

    if not isinstance(description, str) or not description.strip():
        logger.warning("Invalid figure: missing or empty description")
        return None

    return {
        "figure_type": figure_type,
        "description": description,
        "title": obj.get("title"),
        "entities_mentioned": obj.get("entities_mentioned", []),
    }


# ---------------------------------------------------------------------------
# Step 3: Page rendering
# ---------------------------------------------------------------------------


def _render_page(pdf_path: str, page_num: int) -> bytes:
    """Render a PDF page as PNG bytes.

    Args:
        pdf_path: Path to the PDF file.
        page_num: 0-indexed page number.

    Returns:
        PNG image bytes.

    Raises:
        IndexError: If page_num is out of range.
    """
    with fitz.open(pdf_path) as doc:
        if page_num < 0 or page_num >= len(doc):
            raise IndexError(
                f"Page {page_num} out of range for document with {len(doc)} pages"
            )
        page = doc[page_num]
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        return pix.tobytes("png")


# ---------------------------------------------------------------------------
# Step 4: Heuristic filter
# ---------------------------------------------------------------------------


def _heuristic_filter(pdf_path: str) -> list[int]:
    """Return 0-indexed page numbers likely containing figures.

    Uses four OR signals:
    1. page.get_images() count > 0
    2. page.get_drawings() count > 10
    3. Text density below 50% of page average
    4. Caption cues matching Figure/Fig./Table patterns

    Falls back to all pages if no candidates found.
    """
    with fitz.open(pdf_path) as doc:
        n = len(doc)
        if n == 0:
            return []

        # Collect page texts and lengths for density calculation
        page_texts: list[str] = []
        for page in doc:
            page_texts.append(page.get_text())
        text_lengths = [len(t) for t in page_texts]

        avg_text_len = sum(text_lengths) / n if n > 0 else 0
        threshold = avg_text_len * 0.5

        candidates: list[int] = []

        for i, page in enumerate(doc):
            # Signal 1: embedded images
            if len(page.get_images()) > 0:
                candidates.append(i)
                continue

            # Signal 2: vector drawings
            if len(page.get_drawings()) > 10:
                candidates.append(i)
                continue

            # Signal 3: low text density
            if avg_text_len > 0 and text_lengths[i] < threshold:
                candidates.append(i)
                continue

            # Signal 4: caption cues
            if _CAPTION_RE.search(page_texts[i]):
                candidates.append(i)
                continue

        # Fallback: if nothing matched, return all pages
        if not candidates:
            candidates = list(range(n))

        return candidates


# ---------------------------------------------------------------------------
# Step 5: Vision API call
# ---------------------------------------------------------------------------

_VISION_PROMPT = """Analyze this PDF page image. Identify all figures, diagrams, charts, tables, or significant visual elements.

Return a JSON array. One object per distinct figure. For sub-figures (a), (b), (c), create separate objects if they represent different concepts.

Each object:
{
  "figure_type": "diagram|chart|table|photo|equation",
  "title": "Exact caption as shown, or null if none visible",
  "description": "Detailed natural language description of visual content and relationships",
  "entities_mentioned": ["only names explicitly visible in the figure"]
}

Rules:
- Do NOT fabricate text not visible in the image
- If text is illegible, describe layout rather than guessing
- Return [] if no figures/diagrams/charts/tables are present"""

_FIGURE_VISION_PROMPT = """Analyze this figure image extracted from a research paper.

Return a JSON array with one object describing this figure.

Each object:
{
  "figure_type": "diagram|chart|table|photo|equation",
  "title": "Exact caption if visible, or null",
  "description": "Detailed natural language description of visual content, data relationships, and key takeaways",
  "entities_mentioned": ["only names explicitly visible in the figure"]
}

Rules:
- Do NOT fabricate text not visible in the image
- If text is illegible, describe layout rather than guessing
- Return [] if the image contains no meaningful visual content"""


def _vision_call(
    image_b64: str, prompt: str, *, base_url: str, model: str
) -> list[dict]:
    """Send an image to a vision model and return validated figure dicts.

    Takes base_url and model as plain strings (not conn) for thread safety
    with ThreadPoolExecutor.
    """
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                },
                {"type": "text", "text": prompt},
            ],
        }
    ]

    t0 = time.monotonic()
    resp = httpx.post(
        f"{base_url}/v1/chat/completions",
        json={"model": model, "messages": messages, "temperature": 0.1},
        timeout=_VISION_CALL_TIMEOUT,
    )
    resp.raise_for_status()
    elapsed = time.monotonic() - t0
    logger.info("Vision call completed in %.1fs", elapsed)
    if elapsed > _ETA_SECS_PER_PAGE_BASE * _TIMING_DRIFT_FACTOR:
        logger.warning(
            "Vision call took %.1fs (expected ~%ds) — "
            "consider raising _ETA_SECS_PER_PAGE_BASE or _VISION_CALL_TIMEOUT",
            elapsed,
            _ETA_SECS_PER_PAGE_BASE,
        )

    body = resp.json()
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"Malformed vision API response: {exc}") from exc
    content = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", content.strip())

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Vision model returned invalid JSON: {exc}") from exc

    # Unwrap dict wrapper: if result is a dict with a single key whose value is a list
    if isinstance(parsed, dict):
        values = list(parsed.values())
        if len(values) == 1 and isinstance(values[0], list):
            parsed = values[0]
        else:
            raise ValueError(
                f"Vision model returned a dict that cannot be unwrapped: {list(parsed.keys())}"
            )

    if not isinstance(parsed, list):
        raise ValueError(
            f"Vision model returned {type(parsed).__name__}, expected list"
        )

    return [
        v
        for obj in parsed
        if isinstance(obj, dict) and (v := _validate_figure(obj)) is not None
    ]


# ---------------------------------------------------------------------------
# Step 6: Source URI helper
# ---------------------------------------------------------------------------


def _get_paper_source_uri(conn: sqlite3.Connection, paper_id: int) -> str | None:
    """Resolve the source_uri for a paper via paper_paths table."""
    from .papers import get_paper_source_uri as _papers_get_paper_source_uri

    return _papers_get_paper_source_uri(conn, paper_id)


# ---------------------------------------------------------------------------
# Step 6b: Detect vector-drawn figure pages
# ---------------------------------------------------------------------------

_VECTOR_DRAWING_THRESHOLD = 10


def _detect_vector_pages(
    pdf_path: str,
    pages_with_extracted_images: set[int],
) -> list[int]:
    """Detect pages likely containing vector-drawn figures.

    These pages have many vector drawings (> threshold) but no
    pymupdf4llm-extracted images. They need the fallback full-page
    render path since pymupdf4llm can't export vector figures.

    Args:
        pdf_path: Path to PDF file.
        pages_with_extracted_images: Set of 0-indexed page numbers that
            already have extracted raster images (excluded from results).

    Returns:
        Sorted list of 0-indexed page numbers needing fallback rendering.
    """
    with fitz.open(pdf_path) as doc:
        result = []
        for i, page in enumerate(doc):
            if i in pages_with_extracted_images:
                continue
            if len(page.get_drawings()) > _VECTOR_DRAWING_THRESHOLD:
                result.append(i)
        return result


# ---------------------------------------------------------------------------
# Step 6c: Collect extracted images from ingest metadata
# ---------------------------------------------------------------------------


def _collect_extracted_images(
    conn: sqlite3.Connection,
    source_uri: str,
    image_dir: Path,
) -> list[tuple[Path, int]]:
    """Collect pymupdf4llm-extracted images from chunk metadata.

    Queries chunks for the given source_uri, reads the 'images' field from
    each chunk's metadata, and resolves basenames to full paths in image_dir.
    Deduplicates by filename, using the earliest page number.

    Returns:
        List of (image_path, page_num) sorted by page number then filename.
    """
    rows = conn.execute(
        "SELECT metadata FROM chunks WHERE source_uri = ? AND source_type = 'pdf'",
        (source_uri,),
    ).fetchall()

    # Map image basename -> earliest page number (1-indexed, from pymupdf4llm).
    # A chunk may span multiple pages; we use the chunk's first page as the
    # reference since pymupdf4llm doesn't provide per-image page mapping.
    seen: dict[str, int] = {}
    for row in rows:
        try:
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
        except (json.JSONDecodeError, TypeError):
            continue
        images = meta.get("images", [])
        pages = meta.get("pages", [])
        first_page = pages[0] if pages else 1  # 1-indexed; default to page 1
        for img_name in images:
            if img_name not in seen or first_page < seen[img_name]:
                seen[img_name] = first_page

    # Resolve to disk paths, filtering out missing files
    result: list[tuple[Path, int]] = []
    for img_name, page_num in seen.items():
        img_path = image_dir / img_name
        if img_path.exists():
            result.append((img_path, page_num))
        else:
            logger.warning("Extracted image %s not found on disk, skipping", img_name)

    result.sort(key=lambda x: (x[1], x[0].name))
    return result


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------


def _resolve_paper_context(conn: sqlite3.Connection, paper_id: int) -> PaperContext:
    """Validate paper exists and resolve its PDF path."""
    paper_row = conn.execute(
        "SELECT id, title FROM papers WHERE id = ?", (paper_id,)
    ).fetchone()
    if paper_row is None:
        raise NotFoundError(f"Paper {paper_id} not found")

    source_uri = _get_paper_source_uri(conn, paper_id)
    if source_uri is None:
        raise NotFoundError(f"No source URI found for paper {paper_id}")

    pdf_path = Path(source_uri)
    if pdf_path.suffix.lower() != ".pdf" or not pdf_path.exists():
        raise ValidationError(f"Source is not an existing PDF: {source_uri}")

    return PaperContext(
        paper_id=paper_id,
        title=paper_row["title"],
        source_uri=source_uri,
        pdf_path=pdf_path,
    )


def _validate_pages(pdf_path: Path, pages: list[int] | None) -> None:
    """Bounds-check explicit page numbers against the PDF."""
    if pages is None:
        return
    if not pages:
        return
    with fitz.open(str(pdf_path)) as doc:
        total_pages = len(doc)
    for p in pages:
        if p < 0 or p >= total_pages:
            raise ValidationError(
                f"Page {p} out of range (document has {total_pages} pages)"
            )


def _collect_dual_path_inputs(
    conn: sqlite3.Connection,
    source_uri: str,
    pdf_path: Path,
    pages: list[int] | None,
) -> DualPathInputs:
    """Collect extracted images and determine vector-page fallback set."""
    image_dir = pdf_image_dir(pdf_path)
    extracted_images = _collect_extracted_images(conn, source_uri, image_dir)
    pages_with_images: set[int] = {pn - 1 for _, pn in extracted_images}

    if pages is not None:
        pages_set = set(pages)
        extracted_images = [
            (p, pn) for p, pn in extracted_images if (pn - 1) in pages_set
        ]

    if pages is not None:
        vector_pages = [p for p in pages if p not in pages_with_images]
    elif not extracted_images:
        vector_pages = _heuristic_filter(str(pdf_path))
    else:
        vector_pages = _detect_vector_pages(str(pdf_path), pages_with_images)

    return DualPathInputs(
        extracted_images=extracted_images,
        vector_pages=vector_pages,
        pages_with_images=pages_with_images,
    )


def _check_eta_gate(
    n_items: int,
    omniparser_path: str | None,
    confirmed: bool,
) -> dict | None:
    """Return an ETA-confirmation dict if the job is large and unconfirmed."""
    per_page = _ETA_SECS_PER_PAGE_BASE + (
        _ETA_SECS_PER_PAGE_OMNIPARSER if omniparser_path else 0
    )
    estimated = n_items * per_page
    if estimated > 120 and not confirmed:
        return {
            "confirm_required": True,
            "estimated_seconds": estimated,
        }
    return None


def _render_vector_pages(
    pdf_path: Path,
    vector_pages: list[int],
    on_progress: Callable[[str], None] | None,
) -> dict[int, bytes]:
    """Render full-page PNGs for vector-figure fallback pages."""
    if not vector_pages:
        return {}
    if on_progress:
        on_progress(f"rendering {len(vector_pages)} vector-figure pages...")
    rendered: dict[int, bytes] = {}
    with fitz.open(str(pdf_path)) as doc:
        for page_num in vector_pages:
            page = doc[page_num]
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            rendered[page_num] = pix.tobytes("png")
    return rendered


def _run_omniparser_pipeline(
    omniparser_path: str | None,
    extracted_images: list[tuple[Path, int]],
    rendered: dict[int, bytes],
    on_progress: Callable[[str], None] | None,
) -> OmniParserResults:
    """Run OmniParser on extracted images and rendered vector pages."""
    page_data: dict[
        int,
        tuple[dict | None, list[tuple[float, float, float, float]], list[bytes]],
    ] = {}
    image_data: dict[
        str,
        tuple[dict | None, list[tuple[float, float, float, float]], list[bytes]],
    ] = {}
    elapsed = 0.0

    if not omniparser_path:
        return OmniParserResults(
            page_data=page_data, image_data=image_data, elapsed=0.0
        )

    if on_progress:
        on_progress("omniparser processing...")
    t_start = time.monotonic()

    # OmniParser on extracted images (already PNGs on disk)
    for img_path, _page_num in extracted_images:
        omni_result = _run_omniparser(img_path, omniparser_path)
        image_data[img_path.name] = (
            omni_result,
            [(0.0, 0.0, 1.0, 1.0)],
            [],
        )

    # OmniParser on rendered vector pages
    for page_num, png_bytes in rendered.items():
        png_fd, png_tmp = tempfile.mkstemp(suffix=".png")
        try:
            os.close(png_fd)
            Path(png_tmp).write_bytes(png_bytes)
            omni_result = _run_omniparser(Path(png_tmp), omniparser_path)
        finally:
            Path(png_tmp).unlink(missing_ok=True)

        if not omni_result or not omni_result.get("elements"):
            page_data[page_num] = (omni_result, [(0.0, 0.0, 1.0, 1.0)], [])
            continue

        image_size = omni_result.get("image_size", {})
        regions = _cluster_bboxes(omni_result["elements"], image_size)

        if len(regions) > 1:
            crops = _crop_regions(png_bytes, regions, image_size)
            logger.info(
                "Page %d: OmniParser detected %d figure regions, cropping",
                page_num,
                len(regions),
            )
        else:
            crops = []

        page_data[page_num] = (omni_result, regions, crops)

    elapsed = time.monotonic() - t_start
    return OmniParserResults(
        page_data=page_data, image_data=image_data, elapsed=elapsed
    )


def _dispatch_vision_calls(
    extracted_images: list[tuple[Path, int]],
    rendered: dict[int, bytes],
    omni_page_data: dict[
        int,
        tuple[dict | None, list[tuple[float, float, float, float]], list[bytes]],
    ],
    base_url: str,
    model: str,
    on_progress: Callable[[str], None] | None,
) -> VisionResults:
    """Dispatch vision API calls in a thread pool, collect results."""
    if on_progress:
        on_progress("vision processing...")

    page_results: dict[int, list[dict]] = {}
    errors: list[str] = []
    pages_failed = 0

    t_start = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as executor:
        future_to_key: dict = {}

        # Extracted images — use _FIGURE_VISION_PROMPT
        for image_idx, (img_path, page_num) in enumerate(extracted_images):
            page_0idx = page_num - 1
            img_bytes = img_path.read_bytes()
            b64 = base64.b64encode(img_bytes).decode("ascii")
            future = executor.submit(
                _vision_call,
                b64,
                _FIGURE_VISION_PROMPT,
                base_url=base_url,
                model=model,
            )
            future_to_key[future] = (page_0idx, image_idx, img_path.name)

        # Vector page fallback — use _VISION_PROMPT
        for page_num, png_bytes in rendered.items():
            _, regions, crops = omni_page_data.get(
                page_num, (None, [(0.0, 0.0, 1.0, 1.0)], [])
            )

            if len(crops) > 1:
                for region_idx, crop_bytes in enumerate(crops):
                    b64 = base64.b64encode(crop_bytes).decode("ascii")
                    future = executor.submit(
                        _vision_call,
                        b64,
                        _VISION_PROMPT,
                        base_url=base_url,
                        model=model,
                    )
                    future_to_key[future] = (page_num, region_idx, None)
            else:
                b64 = base64.b64encode(png_bytes).decode("ascii")
                future = executor.submit(
                    _vision_call,
                    b64,
                    _VISION_PROMPT,
                    base_url=base_url,
                    model=model,
                )
                future_to_key[future] = (page_num, None, None)

        # Collect results, grouping by page
        page_figures_by_region: dict[int, dict[int | None, list[dict]]] = {}

        for future in as_completed(future_to_key):
            page_num, region_idx, source_image_name = future_to_key[future]
            try:
                figures = future.result()
                if source_image_name:
                    for fig in figures:
                        fig["_source_image"] = source_image_name
                page_figures_by_region.setdefault(page_num, {})[region_idx] = figures
            except Exception as exc:
                pages_failed += 1
                errors.append(f"Page {page_num} region {region_idx}: {exc}")
                logger.warning(
                    "Vision call failed for page %d region %s: %s",
                    page_num,
                    region_idx,
                    exc,
                )

        # Flatten: merge all regions for each page into a single list
        for page_num, region_map in page_figures_by_region.items():
            merged: list[dict] = []
            for key in sorted(region_map, key=lambda k: (k is None, k)):
                for fig in region_map[key]:
                    fig["_region_idx"] = key
                    merged.append(fig)
            page_results[page_num] = merged

    elapsed = time.monotonic() - t_start
    logger.info(
        "Vision phase: %d items in %.1fs",
        len(extracted_images) + len(rendered),
        elapsed,
    )
    return VisionResults(
        page_results=page_results,
        errors=errors,
        pages_failed=pages_failed,
        elapsed=elapsed,
    )


def _enrich_with_omniparser(
    page_results: dict[int, list[dict]],
    omni: OmniParserResults,
) -> int:
    """Enrich vision results with OmniParser text/icon data. Returns enriched count."""
    omniparser_enriched = 0

    for page_num in page_results:
        if not page_results[page_num]:
            continue

        figures_on_page = page_results[page_num]

        # Extracted images: look up OmniParser data by image name
        for i, fig in enumerate(figures_on_page):
            source_image_name = fig.get("_source_image")
            if source_image_name and source_image_name in omni.image_data:
                omni_result, _, _ = omni.image_data[source_image_name]
                if omni_result and omni_result.get("elements"):
                    enriched = _merge_omniparser_elements(fig, omni_result["elements"])
                    if enriched is not fig:
                        omniparser_enriched += 1
                    figures_on_page[i] = enriched

        # Vector pages: look up OmniParser data by page number
        omni_result, regions, crops = omni.page_data.get(
            page_num, (None, [(0.0, 0.0, 1.0, 1.0)], [])
        )
        if not omni_result or not omni_result.get("elements"):
            continue

        vector_figs = [
            (i, fig)
            for i, fig in enumerate(figures_on_page)
            if not fig.get("_source_image")
        ]
        if not vector_figs:
            continue

        omni_elements = omni_result["elements"]

        if len(vector_figs) == 1:
            idx, fig = vector_figs[0]
            enriched = _merge_omniparser_elements(fig, omni_elements)
            if enriched is not fig:
                omniparser_enriched += 1
            figures_on_page[idx] = enriched
        else:
            for idx, fig in vector_figs:
                region_idx = fig.get("_region_idx")
                if region_idx is not None and region_idx < len(regions):
                    region_elements = _elements_in_region(
                        omni_elements, regions[region_idx]
                    )
                else:
                    region_elements = omni_elements
                figures_on_page[idx] = {
                    **fig,
                    "_omniparser_elements": region_elements,
                }

    return omniparser_enriched


def _persist_figures(
    conn: sqlite3.Connection,
    source_uri: str,
    pages: list[int] | None,
    page_results: dict[int, list[dict]],
    model: str,
    on_progress: Callable[[str], None] | None,
) -> int:
    """Embed figures and persist to DB in an atomic transaction. Returns chunks_created."""
    # Collect all figure descriptions for batch embedding
    if on_progress:
        on_progress("embedding figures...")
    all_figures: list[tuple[int, int, dict]] = []
    texts: list[str] = []
    for page_num in sorted(page_results):
        for fig_idx, figure in enumerate(page_results[page_num]):
            all_figures.append((page_num, fig_idx, figure))
            texts.append(figure["description"])

    # Compute embeddings in one batch
    embeddings: list[list[float]] = []
    if texts:
        embeddings = _embed_with_config(conn, texts)

    # Determine candidate_pages for scoped DELETE (#79)
    candidate_pages = pages

    if candidate_pages is not None and candidate_pages:
        page_clauses = []
        page_params: list[int] = []
        for p in candidate_pages:
            page_clauses.append("(chunk_index >= ? AND chunk_index < ?)")
            page_params.extend(
                [
                    _FIGURE_BASE + p * _FIGS_PER_PAGE,
                    _FIGURE_BASE + (p + 1) * _FIGS_PER_PAGE,
                ]
            )
        page_filter = f" AND ({' OR '.join(page_clauses)})"
        fig_chunk_subquery = (
            f"(SELECT id FROM chunks WHERE source_uri = ? AND source_type = 'figure'"
            f"{page_filter})"
        )
        fig_delete_params: tuple = (source_uri, *page_params)
    else:
        fig_chunk_subquery = (
            "(SELECT id FROM chunks WHERE source_uri = ? AND source_type = 'figure')"
        )
        fig_delete_params = (source_uri,)

    vec_table = get_vec_table_name(conn)
    chunks_created = 0
    try:
        fig_chunk_ids = [
            r["id"]
            for r in conn.execute(
                fig_chunk_subquery[1:-1], fig_delete_params
            ).fetchall()
        ]
        _cleanup_figure_fk_refs(conn, fig_chunk_ids)
        delete_chunks_cascade(conn, fig_chunk_ids, table_name=vec_table)

        if all_figures:
            for i, (page_num, fig_idx, figure) in enumerate(all_figures):
                content = figure["description"]
                content_hash = _content_hash(content)

                existing = conn.execute(
                    "SELECT id FROM chunks WHERE content_hash = ?", (content_hash,)
                ).fetchone()
                if existing:
                    continue

                if fig_idx >= _FIGS_PER_PAGE:
                    logger.warning(
                        "Page %d has %d+ figures; capping chunk_index",
                        page_num,
                        fig_idx + 1,
                    )
                    fig_idx = _FIGS_PER_PAGE - 1
                chunk_index = _FIGURE_BASE + page_num * _FIGS_PER_PAGE + fig_idx
                meta_dict = {
                    "page": page_num,
                    "figure_type": figure["figure_type"],
                    "title": figure["title"],
                    "entities_mentioned": figure["entities_mentioned"],
                    "vision_model": model,
                }
                source_image_name = figure.get("_source_image")
                if source_image_name:
                    meta_dict["source_image"] = source_image_name
                if "_omniparser_elements" in figure:
                    meta_dict["omniparser_elements"] = figure["_omniparser_elements"]
                metadata = json.dumps(meta_dict)

                _insert_chunk(
                    conn,
                    content_hash=content_hash,
                    content=content,
                    source_type="figure",
                    source_uri=source_uri,
                    chunk_index=chunk_index,
                    embedding=embeddings[i],
                    metadata=metadata,
                    vec_table=vec_table,
                )
                chunks_created += 1

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return chunks_created


def _save_rendered_pngs(paper_id: int, rendered: dict[int, bytes]) -> None:
    """Save rendered vector-page PNGs to disk."""
    if not rendered:
        return
    figures_dir = (
        Path.home() / ".local" / "share" / "knowledge-base" / "figures" / str(paper_id)
    )
    try:
        figures_dir.mkdir(parents=True, exist_ok=True)
        for page_num, png_bytes in rendered.items():
            (figures_dir / f"page_{page_num}.png").write_bytes(png_bytes)
    except OSError as exc:
        logger.warning("Failed to save figure PNGs: %s", exc)


# ---------------------------------------------------------------------------
# Estimation
# ---------------------------------------------------------------------------


def estimate_figures_time(
    conn: sqlite3.Connection,
    paper_id: int,
    pages: list[int] | None = None,
) -> dict:
    """Estimate figure extraction time without running it.

    Uses the dual-path pipeline: counts extracted images (primary) and
    vector/heuristic pages (fallback) to compute the ETA.

    Returns {"error": ...} on validation failure.
    """
    ctx = _resolve_paper_context(conn, paper_id)
    _validate_pages(ctx.pdf_path, pages)

    inputs = _collect_dual_path_inputs(conn, ctx.source_uri, ctx.pdf_path, pages)
    n_extracted = len(inputs.extracted_images)
    n_vector = len(inputs.vector_pages)

    omniparser_path = _get_omniparser_config(conn)
    per_page = _ETA_SECS_PER_PAGE_BASE + (
        _ETA_SECS_PER_PAGE_OMNIPARSER if omniparser_path else 0
    )
    estimated = (n_extracted + n_vector) * per_page
    return {
        "extracted_images": n_extracted,
        "vector_pages": n_vector,
        "estimated_seconds": estimated,
        "has_omniparser": omniparser_path is not None,
    }


def extract_figures(
    conn: sqlite3.Connection,
    paper_id: int,
    pages: list[int] | None = None,
    confirmed: bool = False,
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    """Extract figures from a paper's PDF using vision models.

    Dual-path pipeline:
    - Primary: send pymupdf4llm-extracted figure images to vision LLM
    - Fallback: render full pages for vector-drawn figures (no extracted images)

    Thread-safe architecture: all SQLite access happens on the main thread.
    Vision API calls are dispatched to a thread pool.
    """
    # 1-2. Validate paper and resolve PDF
    ctx = _resolve_paper_context(conn, paper_id)

    # 3. Bounds-check explicit pages
    if pages is not None and not pages:
        return {"pages_processed": 0, "figures_found": 0, "chunks_created": 0}
    _validate_pages(ctx.pdf_path, pages)

    # 3b. Read omniparser config
    omniparser_path = _get_omniparser_config(conn)

    # 5+5b. Collect extracted images and determine vector-page fallback set
    inputs = _collect_dual_path_inputs(conn, ctx.source_uri, ctx.pdf_path, pages)

    # 4. ETA gate (computed after knowing the dual-path split)
    n_items = len(inputs.extracted_images) + len(inputs.vector_pages)
    gate = _check_eta_gate(n_items, omniparser_path, confirmed)
    if gate is not None:
        gate["extracted_images"] = len(inputs.extracted_images)
        gate["vector_pages"] = len(inputs.vector_pages)
        return gate

    # 5c. Render vector pages (fallback path)
    rendered = _render_vector_pages(ctx.pdf_path, inputs.vector_pages, on_progress)

    # 6. Read vision config once (main thread)
    config = _get_vision_config(conn)
    base_url = config["base_url"]
    model = config["model"]

    # 6b. Run OmniParser BEFORE vision calls
    omni = _run_omniparser_pipeline(
        omniparser_path, inputs.extracted_images, rendered, on_progress
    )

    # 7. Dispatch vision calls in thread pool
    vision = _dispatch_vision_calls(
        inputs.extracted_images, rendered, omni.page_data, base_url, model, on_progress
    )

    # 7c. Enrich with OmniParser text/icon data
    omniparser_enriched = 0
    if omniparser_path:
        omniparser_enriched = _enrich_with_omniparser(vision.page_results, omni)

    # 8-10. Embed and persist figures
    chunks_created = _persist_figures(
        conn, ctx.source_uri, pages, vision.page_results, model, on_progress
    )

    # 11. Save rendered PNGs to disk
    _save_rendered_pngs(paper_id, rendered)

    # Build result summary
    total_figures = sum(len(figs) for figs in vision.page_results.values())
    total_elapsed = vision.elapsed + omni.elapsed
    per_page = _ETA_SECS_PER_PAGE_BASE + (
        _ETA_SECS_PER_PAGE_OMNIPARSER if omniparser_path else 0
    )
    estimated = n_items * per_page

    result = {
        "pages_processed": len(vision.page_results),
        "pages_failed": vision.pages_failed,
        "figures_found": total_figures,
        "chunks_created": chunks_created,
        "extracted_images_processed": len(inputs.extracted_images),
        "vector_pages_rendered": len(rendered),
        "omniparser_enriched": omniparser_enriched,
        "errors": vision.errors,
        "timing": {
            "vision_secs": round(vision.elapsed, 1),
            "omniparser_secs": round(omni.elapsed, 1),
            "total_secs": round(total_elapsed, 1),
        },
    }

    if total_elapsed > estimated * _TIMING_DRIFT_FACTOR:
        logger.warning(
            "Total extraction took %.1fs vs %.0fs estimated (%.1fx) — "
            "ETA constants may need recalibration",
            total_elapsed,
            estimated,
            total_elapsed / estimated if estimated else 0,
        )

    return result
