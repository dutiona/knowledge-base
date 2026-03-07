"""Vision-augmented figure extraction for research papers."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import fitz
import httpx

from .embeddings import _get_ollama_url
from .ingest import _content_hash, _embed_with_config, _serialize_f32

logger = logging.getLogger(__name__)


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
        "base_url": base_url.rstrip("/"),
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
    doc = fitz.open(pdf_path)
    try:
        if page_num < 0 or page_num >= len(doc):
            raise IndexError(
                f"Page {page_num} out of range for document with {len(doc)} pages"
            )
        page = doc[page_num]
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        return pix.tobytes("png")
    finally:
        doc.close()


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
    doc = fitz.open(pdf_path)
    try:
        n = len(doc)
        if n == 0:
            return []

        # Collect text lengths for density calculation
        text_lengths: list[int] = []
        for page in doc:
            text_lengths.append(len(page.get_text()))

        avg_text_len = sum(text_lengths) / n if n > 0 else 0
        threshold = avg_text_len * 0.5

        candidates: list[int] = []
        caption_re = re.compile(r"(?:Figure|Fig\.|Table)\s+\d+", re.IGNORECASE)

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
            if caption_re.search(page.get_text()):
                candidates.append(i)
                continue

        # Fallback: if nothing matched, return all pages
        if not candidates:
            candidates = list(range(n))

        return candidates
    finally:
        doc.close()


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

    resp = httpx.post(
        f"{base_url}/v1/chat/completions",
        json={"model": model, "messages": messages, "temperature": 0.1},
        timeout=30,
    )
    resp.raise_for_status()

    content = resp.json()["choices"][0]["message"]["content"]
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
        raise ValueError(f"Vision model returned {type(parsed).__name__}, expected list")

    return [v for obj in parsed if (v := _validate_figure(obj)) is not None]


# ---------------------------------------------------------------------------
# Step 6: Source URI helper
# ---------------------------------------------------------------------------


def _get_paper_source_uri(conn: sqlite3.Connection, paper_id: int) -> str | None:
    """Resolve the source_uri for a paper via its abstract chunk.

    Query: SELECT source_uri FROM chunks WHERE id =
           (SELECT abstract_chunk_id FROM papers WHERE id = ?)
    """
    row = conn.execute(
        "SELECT source_uri FROM chunks WHERE id = "
        "(SELECT abstract_chunk_id FROM papers WHERE id = ?)",
        (paper_id,),
    ).fetchone()
    return row["source_uri"] if row else None


# ---------------------------------------------------------------------------
# Step 7: Orchestrator
# ---------------------------------------------------------------------------


def extract_figures(
    conn: sqlite3.Connection,
    paper_id: int,
    pages: list[int] | None = None,
    confirmed: bool = False,
) -> dict:
    """Extract figures from a paper's PDF using vision models.

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

    # 3. Determine candidate pages
    doc = fitz.open(str(pdf_path))
    total_pages = len(doc)
    doc.close()

    if pages is not None:
        # Bounds-check
        for p in pages:
            if p < 0 or p >= total_pages:
                return {
                    "error": f"Page {p} out of range (document has {total_pages} pages)"
                }
        candidate_pages = pages
    else:
        candidate_pages = _heuristic_filter(str(pdf_path))

    # 4. ETA gate
    estimated = len(candidate_pages) * 4
    if estimated > 120 and not confirmed:
        return {
            "confirm_required": True,
            "estimated_seconds": estimated,
            "candidate_pages": len(candidate_pages),
        }

    # 5. Render all candidate pages to PNG bytes (main thread)
    rendered: dict[int, bytes] = {}
    for page_num in candidate_pages:
        rendered[page_num] = _render_page(str(pdf_path), page_num)

    # 6. Read vision config once (main thread)
    config = _get_vision_config(conn)
    base_url = config["base_url"]
    model = config["model"]

    # 7. Dispatch vision calls in thread pool (no conn access)
    page_results: dict[int, list[dict]] = {}
    errors: list[str] = []
    pages_failed = 0

    with ThreadPoolExecutor(max_workers=4) as executor:
        future_to_page = {}
        for page_num, png_bytes in rendered.items():
            b64 = base64.b64encode(png_bytes).decode("ascii")
            future = executor.submit(
                _vision_call, b64, _VISION_PROMPT, base_url=base_url, model=model
            )
            future_to_page[future] = page_num

        for future in as_completed(future_to_page):
            page_num = future_to_page[future]
            try:
                figures = future.result()
                page_results[page_num] = figures
            except Exception as exc:
                pages_failed += 1
                errors.append(f"Page {page_num}: {exc}")
                logger.warning("Vision call failed for page %d: %s", page_num, exc)

    # 8. Collect all figure descriptions for batch embedding
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
    if all_figures:
        conn.execute(
            "DELETE FROM chunks_vec WHERE chunk_id IN "
            "(SELECT id FROM chunks WHERE source_uri = ? AND source_type = 'figure')",
            (source_uri,),
        )
        conn.execute(
            "DELETE FROM chunks WHERE source_uri = ? AND source_type = 'figure'",
            (source_uri,),
        )

        for i, (page_num, fig_idx, figure) in enumerate(all_figures):
            content = figure["description"]
            content_hash = _content_hash(content)

            # Check for content_hash collision
            existing = conn.execute(
                "SELECT id FROM chunks WHERE content_hash = ?", (content_hash,)
            ).fetchone()
            if existing:
                continue

            chunk_index = 1_000_000 + page_num * 100 + fig_idx
            metadata = json.dumps(
                {
                    "page": page_num,
                    "figure_type": figure["figure_type"],
                    "title": figure["title"],
                    "entities_mentioned": figure["entities_mentioned"],
                    "vision_model": model,
                }
            )

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

    # 11. Save PNGs to disk (best effort, outside transaction)
    figures_dir = Path.home() / ".local" / "share" / "research-index" / "figures" / str(paper_id)
    try:
        figures_dir.mkdir(parents=True, exist_ok=True)
        for page_num, png_bytes in rendered.items():
            (figures_dir / f"page_{page_num}.png").write_bytes(png_bytes)
    except OSError as exc:
        logger.warning("Failed to save figure PNGs: %s", exc)

    return {
        "pages_processed": len(page_results),
        "pages_failed": pages_failed,
        "figures_found": len(all_figures),
        "chunks_created": chunks_created,
        "errors": errors,
    }
