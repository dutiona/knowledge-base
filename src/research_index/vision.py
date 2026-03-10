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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import fitz
import httpx

from .embeddings import _get_ollama_url
from .ingest import _content_hash, _embed_with_config, _serialize_f32

logger = logging.getLogger(__name__)

_CAPTION_RE = re.compile(r"(?:Figure|Fig\.|Table)\s+\d+", re.IGNORECASE)


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


def _run_omniparser(
    png_path: Path, omniparser_path: str, timeout: int = 120
) -> dict | None:
    """Invoke OmniParser as a subprocess. Returns parsed JSON or None on failure."""
    venv_python = str(Path(omniparser_path) / ".venv" / "bin" / "python")
    parse_script = str(Path(omniparser_path) / "parse.py")

    json_fd, json_out = tempfile.mkstemp(suffix=".json")
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
            return json.load(f)
    except (
        subprocess.TimeoutExpired,
        subprocess.CalledProcessError,
        json.JSONDecodeError,
        OSError,
    ) as exc:
        logger.warning("OmniParser failed for %s: %s", png_path, exc)
        return None
    finally:
        Path(json_out).unlink(missing_ok=True)


_OMNIPARSER_MAX_APPEND = 500
_ETA_SECS_PER_PAGE_BASE = 4
_ETA_SECS_PER_PAGE_OMNIPARSER = 40


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
        timeout=120,
    )
    resp.raise_for_status()

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
    if pages is not None:
        doc = fitz.open(str(pdf_path))
        total_pages = len(doc)
        doc.close()
        # Bounds-check
        for p in pages:
            if p < 0 or p >= total_pages:
                return {
                    "error": f"Page {p} out of range (document has {total_pages} pages)"
                }
        candidate_pages = pages
    else:
        candidate_pages = _heuristic_filter(str(pdf_path))

    # 3b. Read omniparser config (needed for ETA adjustment)
    omniparser_path = _get_omniparser_config(conn)
    omniparser_enriched = 0

    # 4. ETA gate
    per_page = _ETA_SECS_PER_PAGE_BASE + (
        _ETA_SECS_PER_PAGE_OMNIPARSER if omniparser_path else 0
    )
    estimated = len(candidate_pages) * per_page
    if estimated > 120 and not confirmed:
        return {
            "confirm_required": True,
            "estimated_seconds": estimated,
            "candidate_pages": len(candidate_pages),
        }

    # 5. Render all candidate pages to PNG bytes (main thread, single doc open)
    rendered: dict[int, bytes] = {}
    doc = fitz.open(str(pdf_path))
    try:
        for page_num in candidate_pages:
            page = doc[page_num]
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            rendered[page_num] = pix.tobytes("png")
    finally:
        doc.close()

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

    # 7b. Optional: enrich with OmniParser
    if omniparser_path:
        for page_num, png_bytes in rendered.items():
            if page_num not in page_results or not page_results[page_num]:
                continue

            # Write PNG to tempfile for omniparser
            png_fd, png_tmp = tempfile.mkstemp(suffix=".png")
            try:
                os.close(png_fd)
                Path(png_tmp).write_bytes(png_bytes)
                omni_result = _run_omniparser(Path(png_tmp), omniparser_path)
            finally:
                Path(png_tmp).unlink(missing_ok=True)

            if not omni_result or not omni_result.get("elements"):
                continue

            figures_on_page = page_results[page_num]
            omni_elements = omni_result["elements"]

            if len(figures_on_page) == 1:
                # Single-figure gate: safe to merge into description
                enriched = _merge_omniparser_elements(figures_on_page[0], omni_elements)
                if enriched is not figures_on_page[0]:
                    omniparser_enriched += 1
                page_results[page_num] = [enriched]
            else:
                # Multi-figure: store in metadata only
                for i, fig in enumerate(figures_on_page):
                    fig_with_meta = {
                        **fig,
                        "_omniparser_elements": omni_elements,
                    }
                    figures_on_page[i] = fig_with_meta

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
    # Always delete old figure chunks (even if no new figures found — ensures idempotency)
    chunks_created = 0
    fig_chunk_subquery = (
        "(SELECT id FROM chunks WHERE source_uri = ? AND source_type = 'figure')"
    )
    try:
        # Clean up FK references before deleting figure chunks (#53)
        conn.execute(
            f"DELETE FROM entity_mentions WHERE chunk_id IN {fig_chunk_subquery}",
            (source_uri,),
        )
        conn.execute(
            f"UPDATE methods SET chunk_id = NULL WHERE chunk_id IN {fig_chunk_subquery}",
            (source_uri,),
        )
        conn.execute(
            f"UPDATE datasets SET chunk_id = NULL WHERE chunk_id IN {fig_chunk_subquery}",
            (source_uri,),
        )
        conn.execute(
            f"DELETE FROM chunks_vec WHERE chunk_id IN {fig_chunk_subquery}",
            (source_uri,),
        )
        conn.execute(
            "DELETE FROM chunks WHERE source_uri = ? AND source_type = 'figure'",
            (source_uri,),
        )

        if all_figures:
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
                meta_dict = {
                    "page": page_num,
                    "figure_type": figure["figure_type"],
                    "title": figure["title"],
                    "entities_mentioned": figure["entities_mentioned"],
                    "vision_model": model,
                }
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

    # 11. Save PNGs to disk (best effort, outside transaction)
    figures_dir = (
        Path.home() / ".local" / "share" / "research-index" / "figures" / str(paper_id)
    )
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
        "omniparser_enriched": omniparser_enriched,
        "errors": errors,
    }
