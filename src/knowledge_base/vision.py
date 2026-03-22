"""Vision-augmented figure extraction for research papers."""

from __future__ import annotations

import base64
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
from .ingest import _content_hash, _embed_with_config, _serialize_f32, pdf_image_dir

logger = logging.getLogger(__name__)

_CAPTION_RE = re.compile(r"(?:Figure|Fig\.|Table)\s+\d+", re.IGNORECASE)

# Figure chunk_index encoding: 1_000_000 + page_num * FIGS_PER_PAGE + fig_idx
_FIGURE_BASE = 1_000_000
_FIGS_PER_PAGE = 1_000


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


def configure_omniparser(
    conn: sqlite3.Connection,
    path: str | None = None,
) -> dict:
    """Configure OmniParser for figure enrichment.

    Args:
        path: None to query, "" to disable, otherwise absolute path to set.
    """
    if path is None:
        return {"omniparser_path": _get_omniparser_config(conn)}

    if path == "":
        conn.execute("DELETE FROM config WHERE key = 'omniparser_path'")
        conn.commit()
        return {"omniparser_path": None}

    omni_dir = Path(path)
    parse_script = omni_dir / "parse.py"
    venv_python = omni_dir / ".venv" / "bin" / "python"

    if not parse_script.exists():
        return {"error": f"parse.py not found at {parse_script}"}
    if not venv_python.exists():
        return {"error": f"venv python not found at {venv_python}"}

    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('omniparser_path', ?)",
        (path,),
    )
    conn.commit()
    return {"omniparser_path": path}


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
    """Invoke OmniParser as a subprocess. Returns parsed JSON or None on failure."""
    venv_python = str(Path(omniparser_path) / ".venv" / "bin" / "python")
    parse_script = str(Path(omniparser_path) / "parse.py")

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

    img = Image.open(io.BytesIO(png_bytes))
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

        cropped = img.crop((px1, py1, px2, py2))
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
# Step 7: Orchestrator
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
    paper_row = conn.execute(
        "SELECT id FROM papers WHERE id = ?", (paper_id,)
    ).fetchone()
    if paper_row is None:
        return {"error": f"Paper {paper_id} not found"}

    source_uri = _get_paper_source_uri(conn, paper_id)
    if source_uri is None:
        return {"error": f"No source URI found for paper {paper_id}"}

    pdf_path = Path(source_uri)
    if pdf_path.suffix.lower() != ".pdf" or not pdf_path.exists():
        return {"error": f"Source is not an existing PDF: {source_uri}"}

    if pages is not None:
        with fitz.open(str(pdf_path)) as doc:
            total_pages = len(doc)
        for p in pages:
            if p < 0 or p >= total_pages:
                return {
                    "error": f"Page {p} out of range (document has {total_pages} pages)"
                }

    # Collect extracted images
    image_dir = pdf_image_dir(pdf_path)
    extracted_images = _collect_extracted_images(conn, source_uri, image_dir)
    pages_with_images: set[int] = {pn - 1 for _, pn in extracted_images}

    # Determine fallback pages
    if pages is not None:
        n_vector = len([p for p in pages if p not in pages_with_images])
        # Count actual extracted images on requested pages (not unique pages)
        pages_set = set(pages)
        n_extracted = len(
            [(p, pn) for p, pn in extracted_images if (pn - 1) in pages_set]
        )
    elif not extracted_images:
        fallback = _heuristic_filter(str(pdf_path))
        n_vector = len(fallback)
        n_extracted = 0
    else:
        fallback = _detect_vector_pages(str(pdf_path), pages_with_images)
        n_vector = len(fallback)
        n_extracted = len(extracted_images)

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
    # 1. Verify paper exists
    paper_row = conn.execute(
        "SELECT id, title FROM papers WHERE id = ?", (paper_id,)
    ).fetchone()
    if paper_row is None:
        return {"error": f"Paper {paper_id} not found"}

    # 2. Resolve source URI
    source_uri = _get_paper_source_uri(conn, paper_id)
    if source_uri is None:
        return {"error": f"No source URI found for paper {paper_id}"}

    pdf_path = Path(source_uri)
    if pdf_path.suffix.lower() != ".pdf" or not pdf_path.exists():
        return {"error": f"Source is not an existing PDF: {source_uri}"}

    # 3. Bounds-check explicit pages
    if pages is not None:
        if not pages:
            return {"pages_processed": 0, "figures_found": 0, "chunks_created": 0}
        with fitz.open(str(pdf_path)) as doc:
            total_pages = len(doc)
        for p in pages:
            if p < 0 or p >= total_pages:
                return {
                    "error": f"Page {p} out of range (document has {total_pages} pages)"
                }

    # 3b. Read omniparser config
    omniparser_path = _get_omniparser_config(conn)
    omniparser_enriched = 0

    # 5. Collect extracted images (primary path)
    image_dir = pdf_image_dir(pdf_path)
    extracted_images = _collect_extracted_images(conn, source_uri, image_dir)

    # Convert 1-indexed page numbers (from ingest metadata) to 0-indexed (for fitz)
    pages_with_images: set[int] = {pn - 1 for _, pn in extracted_images}

    # If explicit pages requested, filter extracted images to those pages only
    if pages is not None:
        pages_set = set(pages)
        extracted_images = [
            (p, pn) for p, pn in extracted_images if (pn - 1) in pages_set
        ]

    # 5b. Determine which pages need full-page rendering (fallback path).
    # NOTE: Pages with extracted raster images skip full-page rendering even
    # if they also contain vector figures. This is by design — the primary
    # path deliberately avoids full-page rendering for pages that already
    # have high-quality extracted images. Vector figures on mixed pages
    # will be captured in a future iteration if needed.
    if pages is not None:
        # User explicitly requested pages — render any that don't have extracted images
        vector_pages = [p for p in pages if p not in pages_with_images]
    elif not extracted_images:
        # No extracted images at all — fall back entirely to heuristic
        vector_pages = _heuristic_filter(str(pdf_path))
    else:
        # Auto-detect: only render pages with vector drawings that lack images
        vector_pages = _detect_vector_pages(str(pdf_path), pages_with_images)

    # 4. ETA gate (computed after knowing the dual-path split)
    n_items = len(extracted_images) + len(vector_pages)
    per_page = _ETA_SECS_PER_PAGE_BASE + (
        _ETA_SECS_PER_PAGE_OMNIPARSER if omniparser_path else 0
    )
    estimated = n_items * per_page
    if estimated > 120 and not confirmed:
        return {
            "confirm_required": True,
            "estimated_seconds": estimated,
            "extracted_images": len(extracted_images),
            "vector_pages": len(vector_pages),
        }

    # 5c. Render only vector pages (fallback path)
    rendered: dict[int, bytes] = {}
    if vector_pages:
        if on_progress:
            on_progress(f"rendering {len(vector_pages)} vector-figure pages...")
        with fitz.open(str(pdf_path)) as doc:
            for page_num in vector_pages:
                page = doc[page_num]
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                rendered[page_num] = pix.tobytes("png")

    # 6. Read vision config once (main thread)
    config = _get_vision_config(conn)
    base_url = config["base_url"]
    model = config["model"]

    # 6b. Run OmniParser BEFORE vision calls (sequential, CPU/GPU-bound).
    # Runs on BOTH extracted images and rendered vector pages.
    omni_data: dict[
        int, tuple[dict | None, list[tuple[float, float, float, float]], list[bytes]]
    ] = {}
    omni_data_by_image: dict[
        str, tuple[dict | None, list[tuple[float, float, float, float]], list[bytes]]
    ] = {}
    omniparser_elapsed = 0.0
    if omniparser_path:
        if on_progress:
            on_progress("omniparser processing...")
        t_omni_start = time.monotonic()

        # OmniParser on extracted images (already PNGs on disk).
        # Key by image name (unique) rather than page index (not unique).
        omni_data_by_image: dict[
            str,
            tuple[dict | None, list[tuple[float, float, float, float]], list[bytes]],
        ] = {}
        for img_path, page_num in extracted_images:
            omni_result = _run_omniparser(img_path, omniparser_path)
            omni_data_by_image[img_path.name] = (
                omni_result,
                [(0.0, 0.0, 1.0, 1.0)],
                [],
            )

        # OmniParser on rendered vector pages (existing logic)
        for page_num, png_bytes in rendered.items():
            png_fd, png_tmp = tempfile.mkstemp(suffix=".png")
            try:
                os.close(png_fd)
                Path(png_tmp).write_bytes(png_bytes)
                omni_result = _run_omniparser(Path(png_tmp), omniparser_path)
            finally:
                Path(png_tmp).unlink(missing_ok=True)

            if not omni_result or not omni_result.get("elements"):
                omni_data[page_num] = (omni_result, [(0.0, 0.0, 1.0, 1.0)], [])
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

            omni_data[page_num] = (omni_result, regions, crops)
        omniparser_elapsed = time.monotonic() - t_omni_start

    # 7. Dispatch vision calls in thread pool (no conn access)
    if on_progress:
        on_progress("vision processing...")
    # future_to_key maps future -> (page_0idx, region_idx, source_image_name)
    # source_image_name is set for extracted images, None for rendered pages
    page_results: dict[int, list[dict]] = {}
    errors: list[str] = []
    pages_failed = 0

    t_vision_start = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as executor:
        future_to_key: dict = {}

        # 7a. Extracted images — use _FIGURE_VISION_PROMPT.
        # Each extracted image gets a unique image_idx as its region key
        # so multiple images on the same page don't overwrite each other.
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

        # 7b. Vector page fallback — use _VISION_PROMPT (original full-page)
        for page_num, png_bytes in rendered.items():
            _, regions, crops = omni_data.get(
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
                # Tag each figure with source image name (for metadata later)
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

    vision_elapsed = time.monotonic() - t_vision_start
    logger.info(
        "Vision phase: %d items in %.1fs",
        len(extracted_images) + len(rendered),
        vision_elapsed,
    )

    # 7c. Enrich with OmniParser text/icon data (post-vision)
    if omniparser_path:
        for page_num in page_results:
            if not page_results[page_num]:
                continue

            figures_on_page = page_results[page_num]

            # Extracted images: look up OmniParser data by image name
            for i, fig in enumerate(figures_on_page):
                source_image_name = fig.get("_source_image")
                if source_image_name and source_image_name in omni_data_by_image:
                    omni_result, _, _ = omni_data_by_image[source_image_name]
                    if omni_result and omni_result.get("elements"):
                        enriched = _merge_omniparser_elements(
                            fig, omni_result["elements"]
                        )
                        if enriched is not fig:
                            omniparser_enriched += 1
                        figures_on_page[i] = enriched

            # Vector pages: look up OmniParser data by page number
            omni_result, regions, crops = omni_data.get(
                page_num, (None, [(0.0, 0.0, 1.0, 1.0)], [])
            )
            if not omni_result or not omni_result.get("elements"):
                continue

            # Only process figures from the vector path (no _source_image tag)
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

    # 8. Collect all figure descriptions for batch embedding
    if on_progress:
        on_progress("embedding figures...")
    all_figures: list[tuple[int, int, dict]] = []  # (page_num, fig_idx, figure)
    texts: list[str] = []
    for page_num in sorted(page_results):
        for fig_idx, figure in enumerate(page_results[page_num]):
            all_figures.append((page_num, fig_idx, figure))
            texts.append(figure["description"])

    # 9. Compute embeddings in one batch (main thread)
    embeddings: list[list[float]] = []
    if texts:
        embeddings = _embed_with_config(conn, texts)

    # 10. Atomic transaction: delete old, insert new (main thread)
    chunks_created = 0
    # Determine candidate_pages for scoped DELETE (#79).
    # When pages=None (full refresh), always do unscoped DELETE to remove
    # stale figure chunks from pages no longer detected.
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

    try:
        # Clean up FK references before deleting figure chunks (#53)
        conn.execute(
            f"DELETE FROM entity_mentions WHERE chunk_id IN {fig_chunk_subquery}",
            fig_delete_params,
        )
        conn.execute(
            f"UPDATE methods SET chunk_id = NULL WHERE chunk_id IN {fig_chunk_subquery}",
            fig_delete_params,
        )
        conn.execute(
            f"UPDATE datasets SET chunk_id = NULL WHERE chunk_id IN {fig_chunk_subquery}",
            fig_delete_params,
        )
        conn.execute(
            f"DELETE FROM chunks_vec WHERE chunk_id IN {fig_chunk_subquery}",
            fig_delete_params,
        )
        if candidate_pages is not None and candidate_pages:
            conn.execute(
                f"DELETE FROM chunks WHERE source_uri = ? AND source_type = 'figure'"
                f"{page_filter}",
                (source_uri, *page_params),
            )
        else:
            conn.execute(
                "DELETE FROM chunks WHERE source_uri = ? AND source_type = 'figure'",
                (source_uri,),
            )

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
                # Track source image for extracted-image figures
                source_image_name = figure.get("_source_image")
                if source_image_name:
                    meta_dict["source_image"] = source_image_name
                # Store omniparser elements in metadata (multi-figure pages)
                if "_omniparser_elements" in figure:
                    meta_dict["omniparser_elements"] = figure["_omniparser_elements"]
                metadata = json.dumps(meta_dict)

                cursor = conn.execute(
                    "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, metadata) "
                    "VALUES (?, ?, 'figure', ?, ?, ?)",
                    (content_hash, content, source_uri, chunk_index, metadata),
                )
                chunk_id = cursor.lastrowid

                embedding_blob = _serialize_f32(embeddings[i])
                conn.execute(
                    "INSERT INTO chunks_vec (rowid, embedding, chunk_id) VALUES (?, ?, ?)",
                    (chunk_id, embedding_blob, chunk_id),
                )
                chunks_created += 1

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    # 11. Save PNGs to disk — only for vector-rendered pages
    #     (extracted images are already on disk from ingest)
    if rendered:
        figures_dir = (
            Path.home()
            / ".local"
            / "share"
            / "knowledge-base"
            / "figures"
            / str(paper_id)
        )
        try:
            figures_dir.mkdir(parents=True, exist_ok=True)
            for page_num, png_bytes in rendered.items():
                (figures_dir / f"page_{page_num}.png").write_bytes(png_bytes)
        except OSError as exc:
            logger.warning("Failed to save figure PNGs: %s", exc)

    total_elapsed = vision_elapsed + omniparser_elapsed
    result = {
        "pages_processed": len(page_results),
        "pages_failed": pages_failed,
        "figures_found": len(all_figures),
        "chunks_created": chunks_created,
        "extracted_images_processed": len(extracted_images),
        "vector_pages_rendered": len(rendered),
        "omniparser_enriched": omniparser_enriched,
        "errors": errors,
        "timing": {
            "vision_secs": round(vision_elapsed, 1),
            "omniparser_secs": round(omniparser_elapsed, 1),
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
