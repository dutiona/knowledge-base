"""Web ingestion: URL fetching, SSRF protection, browser rendering, and inline image extraction."""

from __future__ import annotations

import html.parser
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura

from .db import (
    _batched_execute,
    delete_chunk_vecs,
    delete_chunks_cascade,
)
from .exceptions import ValidationError
from .utils import is_private_ip
from .chunking import chunk_text as _chunk_text
from .ingest import (
    _cleanup_conclusion_refs,
    _embed_with_config,
    _flush_deferred_session_links,
    _insert_chunk,
)
from .utils import content_hash as _content_hash

logger = logging.getLogger(__name__)

__all__ = [
    "configure_browser",
    "ingest_url",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ALLOWED_URL_SCHEMES = {"http", "https"}

_BROWSER_FALLBACK_MIN_CHARS = 200
"""Below this character count, trafilatura output is likely boilerplate/nav-only."""

_WEB_FIGURE_CHUNK_INDEX_START = 1_000_000
"""Chunk index offset for web figure chunks (avoids collision with text chunks)."""

_WEB_IMAGE_CHUNK_INDEX_START = 2_000_000
"""Chunk index offset for inline web image figures (avoids collision with screenshot figures)."""

_WEB_ELEMENT_CAPTURE_CHUNK_INDEX_START = 3_000_000
"""Chunk index offset for per-element canvas/SVG captures (avoids collision with inline images)."""

_MIN_IMAGE_DIMENSION = 100
"""Minimum width/height in pixels for an image to be considered non-decorative."""

_MAX_IMAGES_PER_PAGE = 10
"""Maximum number of inline images to extract per web page."""

_MAX_IMAGE_DOWNLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
"""Maximum size of a single image download."""

_DECORATIVE_URL_PATTERNS = re.compile(
    r"[/\-_](logo|favicon|avatar|banner|sprite|spacer|badge)[/\-_.]"
    r"|[/\-_]ads?[/\-_]"
    r"|[/\-_](tracking[_\-]?pixel|1x1)[/\-_.]",
    re.IGNORECASE,
)

_DECORATIVE_ALT_PATTERNS = re.compile(
    r"\b(logo|icon|avatar|banner|advertisement|ad|spacer)\b",
    re.IGNORECASE,
)

_RENDER_SCRIPT = Path(__file__).parent / "browser" / "render_page.py"


# ---------------------------------------------------------------------------
# SSRF protection
# ---------------------------------------------------------------------------


def _parse_srcset(srcset: str, *, current_src: str = "") -> str | None:
    """Pick the highest-resolution URL from an ``srcset`` attribute value.

    Applies fail-soft parsing: malformed entries, data-URI entries, and SVG
    URLs are silently skipped.  *current_src* participates as an implicit
    ``1x`` candidate when the set uses pixel-density descriptors.

    Returns the best URL, or ``None`` when no valid candidate survives.
    """
    candidates: list[tuple[str, float, str]] = []  # (url, value, kind)
    for raw in srcset.split(","):
        parts = raw.strip().split()
        if not parts:
            continue
        url = parts[0]
        if not url or url.startswith("data:"):
            continue
        if url.lower().endswith((".svg", ".svgz")):
            continue
        if len(parts) >= 2:
            desc = parts[1]
            if desc.endswith("w"):
                try:
                    candidates.append((url, float(desc[:-1]), "w"))
                except ValueError:
                    continue
            elif desc.endswith("x"):
                try:
                    candidates.append((url, float(desc[:-1]), "x"))
                except ValueError:
                    continue
            else:
                # Unknown descriptor — treat as no-descriptor
                candidates.append((url, 0.0, "none"))
        else:
            candidates.append((url, 0.0, "none"))

    if not candidates:
        return None

    # Determine dominant descriptor kind
    kinds = {k for _, _, k in candidates}
    if "w" in kinds:
        w_candidates = [(u, v) for u, v, k in candidates if k == "w"]
        return max(w_candidates, key=lambda t: t[1])[0]
    if "x" in kinds:
        x_candidates = [(u, v) for u, v, k in candidates if k == "x"]
        best_url, best_x = max(x_candidates, key=lambda t: t[1])
        # src is implicit 1x — keep it if it ties or beats srcset
        if current_src and best_x <= 1.0:
            return current_src
        return best_url
    # No descriptors — pick last (convention: ascending quality)
    return candidates[-1][0]


class _ImgTagParser(html.parser.HTMLParser):
    """Extract ``<img>`` (and ``<picture>``) tag attributes from HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.images: list[dict[str, str]] = []
        self._in_picture: bool = False
        self._picture_best_src: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "picture":
            self._in_picture = True
            self._picture_best_src = None
            return

        if tag == "source" and self._in_picture and self._picture_best_src is None:
            d = {k: v for k, v in attrs if v is not None}
            # Skip SVG sources entirely
            if d.get("type", "").lower() == "image/svg+xml":
                return
            srcset = d.get("srcset", "")
            if srcset:
                best = _parse_srcset(srcset)
                if best:
                    self._picture_best_src = best
            return

        if tag == "img":
            d = {k: v for k, v in attrs if v is not None}
            src = d.get("src", "")
            if self._in_picture:
                # Use picture source if available, else try img srcset, else img src
                if self._picture_best_src:
                    d["src"] = self._picture_best_src
                elif "srcset" in d:
                    best = _parse_srcset(d["srcset"], current_src=src)
                    if best:
                        d["src"] = best
                if d.get("src"):
                    self.images.append(d)
            else:
                # Standalone <img> — resolve srcset if present
                if "srcset" in d and src:
                    best = _parse_srcset(d["srcset"], current_src=src)
                    if best:
                        d["src"] = best
                if d.get("src"):
                    self.images.append(d)

    def handle_endtag(self, tag: str) -> None:
        if tag == "picture":
            self._in_picture = False
            self._picture_best_src = None


def _validate_image_url(url: str) -> bool:
    """Validate an image URL is safe to fetch (scheme + SSRF check)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.hostname:
        return False
    return not is_private_ip(parsed.hostname)


# ---------------------------------------------------------------------------
# Figure FK cleanup
# ---------------------------------------------------------------------------


def _cleanup_figure_fk_refs(conn: sqlite3.Connection, chunk_ids: list[int]) -> None:
    """Clean FK references to figure chunks before deletion.

    Mirrors the FK cleanup in ``reingest_file`` (papers, relationships,
    conclusions, methods, datasets, metrics, entity_mentions).
    """
    if not chunk_ids:
        return

    _batched_execute(
        conn,
        "UPDATE papers SET abstract_chunk_id = NULL WHERE abstract_chunk_id IN ({ph})",
        chunk_ids,
    )
    _batched_execute(
        conn,
        "UPDATE relationships SET evidence_chunk_id = NULL "
        "WHERE evidence_chunk_id IN ({ph})",
        chunk_ids,
    )

    _cleanup_conclusion_refs(conn, chunk_ids)

    for table in ("methods", "datasets", "metrics"):
        _batched_execute(
            conn,
            f"UPDATE {table} SET chunk_id = NULL WHERE chunk_id IN ({{ph}})",
            chunk_ids,
        )
    _batched_execute(
        conn,
        "DELETE FROM entity_mentions WHERE chunk_id IN ({ph})",
        chunk_ids,
    )


# ---------------------------------------------------------------------------
# HTML image extraction
# ---------------------------------------------------------------------------


def _parse_image_candidates(
    html_content: str,
    base_url: str,
    *,
    exclude_urls: set[str] | frozenset[str] = frozenset(),
) -> list[tuple[str, str]] | None:
    """Parse ``<img>`` tags from *html_content* and return qualifying ``(url, alt)`` pairs.

    Applies all filtering: data URIs, SVGs, decorative patterns,
    HTML dimension pre-filter, and URL dedup.  *exclude_urls* skips images
    already collected from another HTML source (Phase 2 cross-source dedup).

    Returns ``None`` on parse failure (caller should not trigger stale cleanup),
    or an empty list when parsing succeeded but no images qualify.
    """
    parser = _ImgTagParser()
    try:
        parser.feed(html_content)
    except Exception:
        logger.warning("HTML parsing failed for %s", base_url, exc_info=True)
        return None

    if not parser.images:
        return []

    seen_urls: set[str] = set(exclude_urls)
    candidates: list[tuple[str, str]] = []

    for img in parser.images:
        src = img["src"]

        if src.startswith("data:"):
            continue
        if src.lower().endswith((".svg", ".svgz")):
            continue

        resolved = urljoin(base_url, src)

        if not _validate_image_url(resolved):
            continue

        if _DECORATIVE_URL_PATTERNS.search(resolved):
            continue

        alt = img.get("alt", "")
        if alt and _DECORATIVE_ALT_PATTERNS.search(alt):
            continue

        w_str = img.get("width", "")
        h_str = img.get("height", "")
        try:
            w = int(w_str) if w_str else None
            h = int(h_str) if h_str else None
        except ValueError:
            w, h = None, None
        if w is not None and h is not None:
            if w < _MIN_IMAGE_DIMENSION or h < _MIN_IMAGE_DIMENSION:
                continue

        if resolved in seen_urls:
            continue
        seen_urls.add(resolved)

        candidates.append((resolved, alt))

    return candidates


def _extract_html_images(
    conn: sqlite3.Connection,
    html_content: str,
    source_url: str,
    base_url: str | None = None,
    *,
    extra_html_sources: list[tuple[str, str]] | None = None,
) -> int:
    """Extract inline ``<img>`` tags from HTML, describe via vision, store as figure chunks.

    *source_url* is used as ``source_uri`` for storage and stale cleanup (must
    match the key used for text chunks — typically the original requested URL).

    *base_url* is used for resolving relative ``<img src>`` attributes via
    ``urljoin``.  Defaults to *source_url* when not provided.  Pass
    ``str(response.url)`` when the page redirected so relative paths resolve
    against the final location.

    *extra_html_sources* is an optional list of ``(html, base_url)`` pairs
    (e.g. rendered DOM HTML from Playwright).  Images from these sources are
    merged with the primary HTML candidates and URL-deduplicated — images
    already found in the primary HTML are not re-downloaded.

    Returns the number of figure chunks added.  Returns 0 if vision is not
    configured or no qualifying images are found.
    """
    if base_url is None:
        base_url = source_url
    import base64
    import io

    from PIL import Image

    from .vision import _get_vision_config, _vision_call

    try:
        vision_config = _get_vision_config(conn)
    except Exception:
        return 0

    vis_base_url = vision_config["base_url"]
    vis_model = vision_config["model"]

    # --- Parse <img> tags ---
    candidates = _parse_image_candidates(html_content, base_url)

    if candidates is None:
        # Parse failure — do NOT trigger stale cleanup (non-destructive)
        return 0

    # Phase 2 (#131): merge candidates from rendered DOM (or other extra sources)
    any_parse_failed = False
    if extra_html_sources:
        seen_urls: set[str] = {url for url, _alt in candidates}
        for extra_html, extra_base in extra_html_sources:
            extra = _parse_image_candidates(
                extra_html, extra_base, exclude_urls=seen_urls
            )
            if extra is None:
                any_parse_failed = True
                break
            elif extra:
                candidates.extend(extra)
                seen_urls.update(url for url, _alt in extra)

    if any_parse_failed:
        # Abort: proceeding with partial candidates would delete-then-reinsert,
        # losing figures previously extracted from the failed source.
        return 0

    if not candidates:
        _cleanup_stale_inline_images(conn, source_url)
        return 0

    # Cap
    candidates = candidates[:_MAX_IMAGES_PER_PAGE]

    # --- Download, convert, describe ---
    collected: list[tuple[str, dict]] = []  # (description, metadata_dict)

    for image_url, alt_text in candidates:
        try:
            with httpx.stream(
                "GET", image_url, timeout=15.0, follow_redirects=True
            ) as resp:
                resp.raise_for_status()

                # Post-redirect SSRF check
                final_url = str(resp.url)
                if not _validate_image_url(final_url):
                    logger.warning(
                        "SSRF: image redirected to private address %s", final_url
                    )
                    continue

                # Stream with byte counter
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_bytes(chunk_size=65536):
                    total += len(chunk)
                    if total > _MAX_IMAGE_DOWNLOAD_BYTES:
                        logger.warning(
                            "Image too large (>%d bytes): %s",
                            _MAX_IMAGE_DOWNLOAD_BYTES,
                            image_url,
                        )
                        break
                    chunks.append(chunk)
                else:
                    # Loop completed without break — download OK
                    pass

                if total > _MAX_IMAGE_DOWNLOAD_BYTES:
                    continue

                image_bytes = b"".join(chunks)
        except Exception:
            logger.warning("Image download failed: %s", image_url, exc_info=True)
            continue

        # Open with Pillow, check dimensions, convert to PNG
        try:
            img_obj = Image.open(io.BytesIO(image_bytes))
            w, h = img_obj.size
            if w < _MIN_IMAGE_DIMENSION or h < _MIN_IMAGE_DIMENSION:
                continue

            buf = io.BytesIO()
            img_obj.convert("RGB").save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:
            logger.warning("Image decode failed: %s", image_url, exc_info=True)
            continue

        # Vision call
        prompt = (
            "Describe this image from a web page. Identify what it shows — "
            "diagrams, charts, schematics, photographs, or other visual content. "
            "Respond with a JSON list of objects with keys: "
            '"description", "figure_type", "title".'
        )
        try:
            figures = _vision_call(b64, prompt, base_url=vis_base_url, model=vis_model)
        except Exception:
            logger.warning("Vision call failed for image %s", image_url, exc_info=True)
            continue

        for fig in figures:
            desc = fig.get("description", "")
            if not desc:
                continue
            meta = {
                "figure_type": "web_image",
                "image_url": image_url,
                "alt_text": alt_text,
                "original_source_type": "web",
                "source_url": source_url,
                "vision_model": vis_model,
                "title": fig.get("title", ""),
            }
            collected.append((desc, meta))

    if not collected:
        return 0

    # --- Compute embeddings (last fallible step) ---
    texts = [desc for desc, _ in collected]
    embeddings = _embed_with_config(conn, texts)

    # --- Delete stale inline image chunks (only after embeddings succeed) ---
    # Scoped to [_WEB_IMAGE_CHUNK_INDEX_START, _WEB_ELEMENT_CAPTURE_CHUNK_INDEX_START)
    # to avoid deleting element captures from Phase 3.
    old_ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM chunks WHERE source_uri = ? "
            "AND source_type = 'figure' AND chunk_index >= ? AND chunk_index < ?",
            (
                source_url,
                _WEB_IMAGE_CHUNK_INDEX_START,
                _WEB_ELEMENT_CAPTURE_CHUNK_INDEX_START,
            ),
        ).fetchall()
    ]
    if old_ids:
        _cleanup_figure_fk_refs(conn, old_ids)
        delete_chunks_cascade(conn, old_ids)

    # --- Insert new figure chunks ---
    figures_added = 0
    for idx, ((desc, meta), emb_vec) in enumerate(zip(collected, embeddings)):
        chunk_hash = _content_hash(desc)
        existing = conn.execute(
            "SELECT id FROM chunks WHERE content_hash = ?", (chunk_hash,)
        ).fetchone()
        if existing:
            continue

        meta_json = json.dumps(meta)
        chunk_index = _WEB_IMAGE_CHUNK_INDEX_START + idx
        _insert_chunk(
            conn,
            content_hash=chunk_hash,
            content=desc,
            source_type="figure",
            source_uri=source_url,
            chunk_index=chunk_index,
            embedding=emb_vec,
            metadata=meta_json,
        )
        figures_added += 1

    if figures_added or old_ids:
        conn.commit()
    return figures_added


# ---------------------------------------------------------------------------
# Browser rendering configuration
# ---------------------------------------------------------------------------


def _find_venv_python(venv_path: str | Path) -> Path | None:
    """Locate the Python executable in a venv (cross-platform)."""
    venv = Path(venv_path)
    for candidate in (venv / "bin" / "python", venv / "Scripts" / "python.exe"):
        if candidate.is_file():
            return candidate
    return None


def _get_browser_config(conn: sqlite3.Connection) -> dict | None:
    """Read browser rendering configuration from config table.

    Returns a dict with mode/endpoint/venv keys, or None when unconfigured.
    """
    rows = conn.execute(
        "SELECT key, value FROM config "
        "WHERE key IN ('browser_mode', 'browser_venv', 'browser_endpoint')"
    ).fetchall()
    config_map = {row["key"]: row["value"] for row in rows}

    mode = config_map.get("browser_mode")
    venv = config_map.get("browser_venv")
    if not mode or not venv:
        return None

    config: dict = {"mode": mode, "venv": venv}
    if mode == "cdp":
        endpoint = config_map.get("browser_endpoint")
        if endpoint:
            config["endpoint"] = endpoint

    return config


def configure_browser(
    conn: sqlite3.Connection,
    cdp_endpoint: str | None = None,
    venv_path: str | None = None,
) -> dict:
    """Configure browser rendering for JS-heavy web pages.

    Args:
        cdp_endpoint: WebSocket CDP endpoint (ws:// or wss://).
                      Requires venv_path too.
        venv_path: Absolute path to Python venv with playwright installed.
        Pass both as empty string to disable.  Both None to query.
    """
    # Query mode
    if cdp_endpoint is None and venv_path is None:
        cfg = _get_browser_config(conn)
        return {"browser": cfg}

    # Disable mode
    if cdp_endpoint == "" and venv_path == "":
        for key in ("browser_mode", "browser_endpoint", "browser_venv"):
            conn.execute("DELETE FROM config WHERE key = ?", (key,))
        conn.commit()
        return {"browser": None}

    # CDP without venv is an error
    if cdp_endpoint and not venv_path:
        raise ValidationError(
            "venv_path is required (playwright Python client must be installed)"
        )

    # Validate venv
    if venv_path:
        resolved = Path(venv_path).resolve()
        if not resolved.is_absolute():
            raise ValidationError("venv_path must be an absolute path")
        venv_python = _find_venv_python(resolved)
        if not venv_python:
            raise ValidationError(f"Python executable not found in venv at {venv_path}")

    # Determine mode
    if cdp_endpoint:
        parsed = urlparse(cdp_endpoint)
        if parsed.scheme not in ("ws", "wss"):
            raise ValidationError(
                f"CDP endpoint must use ws:// or wss://, got {parsed.scheme}://"
            )
        mode = "cdp"
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES ('browser_endpoint', ?)",
            (cdp_endpoint,),
        )
    else:
        mode = "local"
        # Clear any stale CDP endpoint
        conn.execute("DELETE FROM config WHERE key = 'browser_endpoint'")

    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('browser_mode', ?)",
        (mode,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('browser_venv', ?)",
        (venv_path,),
    )
    conn.commit()
    return {"browser": _get_browser_config(conn)}


def _render_with_browser(
    url: str,
    browser_config: dict,
    timeout: int = 60,
) -> dict | None:
    """Render a URL via Playwright subprocess.

    Returns ``{"html": str, "screenshot_path": Path, "tmpdir": Path}``
    on success, or ``None`` on failure.  Caller owns tmpdir cleanup on
    success; tmpdir is cleaned on failure.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="ri-browser-"))
    venv_python_path = _find_venv_python(browser_config["venv"])
    if not venv_python_path:
        logger.warning(
            "Python executable not found in configured venv: %s",
            browser_config["venv"],
        )
        shutil.rmtree(tmpdir, ignore_errors=True)
        return None
    venv_python = str(venv_python_path)

    cmd = [venv_python, str(_RENDER_SCRIPT), url, str(tmpdir)]
    if browser_config.get("mode") == "cdp" and browser_config.get("endpoint"):
        cmd.extend(["--cdp", browser_config["endpoint"]])

    try:
        subprocess.run(
            cmd,
            timeout=timeout,
            capture_output=True,
            check=True,
        )
    except (
        subprocess.TimeoutExpired,
        subprocess.CalledProcessError,
        OSError,
    ) as exc:
        logger.warning("Browser rendering failed for %s: %s", url, exc)
        shutil.rmtree(tmpdir, ignore_errors=True)
        return None

    html_path = tmpdir / "page.html"
    screenshot_path = tmpdir / "screenshot.png"

    if not html_path.exists():
        logger.warning("Browser produced no HTML for %s", url)
        shutil.rmtree(tmpdir, ignore_errors=True)
        return None

    html = html_path.read_text(encoding="utf-8")
    # Final URL may differ from input after redirects or client-side navigation
    final_url_path = tmpdir / "final_url.txt"
    final_url = (
        final_url_path.read_text(encoding="utf-8").strip()
        if final_url_path.exists()
        else None
    )
    # Per-element captures (Phase 3, #132)
    element_captures: list[dict] = []
    elements_json_path = tmpdir / "elements.json"
    if elements_json_path.exists():
        try:
            raw = json.loads(elements_json_path.read_text(encoding="utf-8"))
            for entry in raw:
                png_path = tmpdir / entry["file"]
                if png_path.exists():
                    element_captures.append(
                        {
                            "path": png_path,
                            "tag": entry["tag"],
                            "width": entry.get("width", 0),
                            "height": entry.get("height", 0),
                        }
                    )
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning("Failed to parse elements.json for %s", url)

    return {
        "html": html,
        "screenshot_path": screenshot_path if screenshot_path.exists() else None,
        "final_url": final_url,
        "element_captures": element_captures,
        "tmpdir": tmpdir,
    }


# ---------------------------------------------------------------------------
# Web figure extraction (screenshot-based)
# ---------------------------------------------------------------------------


def _extract_web_figures(
    conn: sqlite3.Connection,
    source_url: str,
    screenshot_path: Path,
) -> int:
    """Extract figures from a browser-rendered web page screenshot.

    Feeds the screenshot through the existing vision pipeline:
    - Vision model describes the page screenshot
    - OmniParser segments into text/icon regions (if configured)
    Stores as figure chunks with ``source_type='figure'`` and metadata
    indicating web origin.  Returns number of figures added.

    Returns 0 if vision is not configured or on any failure.
    """
    import base64

    from .vision import (
        _get_omniparser_config,
        _get_vision_config,
        _merge_omniparser_elements,
        _resolve_omniparser_server_url,
        _run_omniparser,
        _vision_call,
    )

    try:
        vision_config = _get_vision_config(conn)
    except Exception:
        return 0  # Vision not configured or misconfigured

    base_url = vision_config["base_url"]
    model = vision_config["model"]

    # Describe the screenshot via the vision model
    png_bytes = screenshot_path.read_bytes()
    b64 = base64.b64encode(png_bytes).decode("ascii")

    prompt = (
        "Describe this web page screenshot. Identify any figures, diagrams, "
        "charts, or schematics visible. Respond with a JSON list of objects "
        'with keys: "description", "figure_type", "title".'
    )

    try:
        figures = _vision_call(b64, prompt, base_url=base_url, model=model)
    except Exception:
        logger.warning(
            "Vision call failed for web screenshot %s", source_url, exc_info=True
        )
        return 0

    if not figures:
        return 0

    # Optional: OmniParser enrichment (uses server mode when available, #334)
    omniparser_path = _get_omniparser_config(conn)
    omni_elements: list[dict] | None = None
    if omniparser_path:
        server_url = _resolve_omniparser_server_url(conn, omniparser_path)
        omni_result = _run_omniparser(
            screenshot_path, omniparser_path, server_url=server_url
        )
        if omni_result and omni_result.get("elements"):
            omni_elements = omni_result["elements"]

    # Embed and store figure chunks
    texts: list[str] = []
    valid_figures: list[dict] = []
    for fig in figures:
        desc = fig.get("description", "")
        if not desc:
            continue
        # Enrich with OmniParser if available and single figure
        if omni_elements and len(figures) == 1:
            enriched = _merge_omniparser_elements(fig, omni_elements)
            desc = enriched.get("description", desc)
        texts.append(desc)
        valid_figures.append(fig)

    if not texts:
        return 0

    embeddings = _embed_with_config(conn, texts)

    # Remove stale screenshot figure chunks only (scope to < _WEB_IMAGE_CHUNK_INDEX_START
    # to avoid deleting inline image figures managed by _extract_html_images)
    old_fig_ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM chunks WHERE source_uri = ? AND source_type = 'figure' "
            "AND chunk_index < ?",
            (source_url, _WEB_IMAGE_CHUNK_INDEX_START),
        ).fetchall()
    ]
    if old_fig_ids:
        _cleanup_figure_fk_refs(conn, old_fig_ids)
        delete_chunk_vecs(conn, old_fig_ids)
        _batched_execute(conn, "DELETE FROM chunks WHERE id IN ({ph})", old_fig_ids)

    figures_added = 0

    for idx, (fig, desc, emb_vec) in enumerate(zip(valid_figures, texts, embeddings)):
        chunk_hash = _content_hash(desc)
        existing = conn.execute(
            "SELECT id FROM chunks WHERE content_hash = ?", (chunk_hash,)
        ).fetchone()
        if existing:
            continue

        meta_json = json.dumps(
            {
                "figure_type": fig.get("figure_type", "web_screenshot"),
                "title": fig.get("title", ""),
                "original_source_type": "web",
                "source_url": source_url,
                "vision_model": model,
            }
        )

        chunk_index = _WEB_FIGURE_CHUNK_INDEX_START + idx
        _insert_chunk(
            conn,
            content_hash=chunk_hash,
            content=desc,
            source_type="figure",
            source_uri=source_url,
            chunk_index=chunk_index,
            embedding=emb_vec,
            metadata=meta_json,
        )
        figures_added += 1

    if figures_added:
        conn.commit()
    return figures_added


# ---------------------------------------------------------------------------
# Per-element canvas/SVG capture extraction (Phase 3, #132)
# ---------------------------------------------------------------------------


def _extract_element_captures(
    conn: sqlite3.Connection,
    source_url: str,
    captures: list[dict],
) -> int:
    """Extract figures from per-element browser captures.

    Each capture dict has ``{"path": Path, "tag": str, "width": int, "height": int}``.
    Sends each PNG to the vision model and stores as a figure chunk with
    ``chunk_index >= _WEB_ELEMENT_CAPTURE_CHUNK_INDEX_START``.

    Returns number of figure chunks added.
    """
    import base64

    from .vision import _get_vision_config, _vision_call

    if not captures:
        return 0

    try:
        vision_config = _get_vision_config(conn)
    except Exception:
        return 0

    base_url = vision_config["base_url"]
    model = vision_config["model"]

    # --- Phase 1: Collect descriptions (fallible: vision calls) ---
    collected: list[tuple[str, dict, int]] = []  # (desc, metadata_dict, capture_idx)
    for idx, capture in enumerate(captures):
        png_path: Path = capture["path"]
        tag: str = capture["tag"]
        if not png_path.exists():
            continue

        b64 = base64.b64encode(png_path.read_bytes()).decode("ascii")
        prompt = (
            f"Describe this {tag} element captured from a web page. "
            "What does it show? Respond with a JSON list of objects "
            'with keys: "description", "figure_type", "title".'
        )

        try:
            figures = _vision_call(b64, prompt, base_url=base_url, model=model)
        except Exception:
            logger.warning(
                "Vision call failed for element capture %s/%s",
                source_url,
                png_path.name,
                exc_info=True,
            )
            continue

        if not figures:
            continue

        fig = figures[0]
        desc = fig.get("description", "")
        if not desc:
            continue

        fig_type_raw = fig.get("figure_type", "unknown")
        fig_type = f"{tag}_capture" if fig_type_raw in ("unknown", "") else fig_type_raw

        meta = {
            "figure_type": fig_type,
            "element_tag": tag,
            "element_width": capture.get("width", 0),
            "element_height": capture.get("height", 0),
            "title": fig.get("title", ""),
            "original_source_type": "web",
            "source_url": source_url,
            "vision_model": model,
        }
        collected.append((desc, meta, idx))

    if not collected:
        return 0

    # --- Phase 2: Batch embed (fallible: embedding calls) ---
    texts = [desc for desc, _, _ in collected]
    embeddings = _embed_with_config(conn, texts)

    # --- Phase 3: Atomic swap — delete stale, insert new (infallible) ---
    old_ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM chunks WHERE source_uri = ? AND source_type = 'figure' "
            "AND chunk_index >= ?",
            (source_url, _WEB_ELEMENT_CAPTURE_CHUNK_INDEX_START),
        ).fetchall()
    ]
    if old_ids:
        _cleanup_figure_fk_refs(conn, old_ids)
        delete_chunk_vecs(conn, old_ids)
        _batched_execute(conn, "DELETE FROM chunks WHERE id IN ({ph})", old_ids)

    figures_added = 0
    for (desc, meta, cap_idx), emb_vec in zip(collected, embeddings):
        chunk_hash = _content_hash(desc)
        existing = conn.execute(
            "SELECT id FROM chunks WHERE content_hash = ?", (chunk_hash,)
        ).fetchone()
        if existing:
            continue

        meta_json = json.dumps(meta)
        chunk_index = _WEB_ELEMENT_CAPTURE_CHUNK_INDEX_START + cap_idx
        _insert_chunk(
            conn,
            content_hash=chunk_hash,
            content=desc,
            source_type="figure",
            source_uri=source_url,
            chunk_index=chunk_index,
            embedding=emb_vec,
            metadata=meta_json,
        )
        figures_added += 1

    if figures_added:
        conn.commit()
    return figures_added


# ---------------------------------------------------------------------------
# Stale inline image cleanup
# ---------------------------------------------------------------------------


def _cleanup_stale_inline_images(conn: sqlite3.Connection, source_uri: str) -> int:
    """Delete orphaned inline image chunks for *source_uri*.

    Called when ``_extract_html_images`` returns 0 for a page that may have had
    images during a previous ingestion.  Without this, stale figure chunks with
    ``chunk_index >= _WEB_IMAGE_CHUNK_INDEX_START`` would persist indefinitely.

    Returns the number of chunks deleted.
    """
    # Scoped to [_WEB_IMAGE_CHUNK_INDEX_START, _WEB_ELEMENT_CAPTURE_CHUNK_INDEX_START)
    # to avoid deleting element captures from Phase 3.
    old_ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM chunks WHERE source_uri = ? "
            "AND source_type = 'figure' AND chunk_index >= ? AND chunk_index < ?",
            (
                source_uri,
                _WEB_IMAGE_CHUNK_INDEX_START,
                _WEB_ELEMENT_CAPTURE_CHUNK_INDEX_START,
            ),
        ).fetchall()
    ]
    if not old_ids:
        return 0
    _cleanup_figure_fk_refs(conn, old_ids)
    delete_chunk_vecs(conn, old_ids)
    _batched_execute(conn, "DELETE FROM chunks WHERE id IN ({ph})", old_ids)
    conn.commit()
    logger.info("Cleaned %d stale inline image chunks for %s", len(old_ids), source_uri)
    return len(old_ids)


# ---------------------------------------------------------------------------
# URL ingestion
# ---------------------------------------------------------------------------


def ingest_url(
    conn: sqlite3.Connection,
    url: str,
    session_id: str | None = None,
) -> dict:
    """Fetch a web page, extract content, and ingest as chunks.

    Uses trafilatura for content extraction (strips boilerplate, extracts main content).
    Falls back to browser rendering when trafilatura extracts insufficient content.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_URL_SCHEMES:
        raise ValidationError(
            f"URL scheme must be http or https, got: {parsed.scheme!r}"
        )
    if not parsed.hostname:
        raise ValidationError("URL must include a hostname")
    if is_private_ip(parsed.hostname):
        raise ValidationError(
            f"URL points to a private/internal address: {parsed.hostname}"
        )

    # SSRF defense: pre-fetch check blocks direct requests to private IPs.
    # Post-redirect check below prevents processing data from redirect-based SSRF.
    # Note: httpx still follows the redirect (the request reaches the target).
    # Full per-hop validation requires a custom transport — tracked in #232.
    try:
        response = httpx.get(url, follow_redirects=True, timeout=30.0)
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise ValidationError(f"Failed to fetch {url}: {e}") from e

    # Validate post-redirect URL — prevents processing data from internal hosts
    final_host = urlparse(str(response.url)).hostname
    if final_host and is_private_ip(final_host):
        raise ValidationError(
            f"URL redirected to a private/internal address: {final_host}"
        )

    html = response.text
    text = trafilatura.extract(html, include_links=False, include_tables=True) or ""
    extracted_title = None
    metadata = trafilatura.extract_metadata(html)
    if metadata and metadata.title:
        extracted_title = metadata.title

    browser_rendered = False
    figures_extracted = 0
    rendered_html_for_phase2: str | None = None
    rendered_base_url: str = str(response.url)

    # Browser fallback: if trafilatura got insufficient content, try rendering
    if len(text.strip()) < _BROWSER_FALLBACK_MIN_CHARS:
        browser_config = _get_browser_config(conn)
        if browser_config:
            render_result = _render_with_browser(url, browser_config)
            if render_result:
                try:
                    rendered_text = (
                        trafilatura.extract(
                            render_result["html"],
                            include_links=False,
                            include_tables=True,
                        )
                        or ""
                    )
                    meta2 = trafilatura.extract_metadata(render_result["html"])
                    if meta2 and meta2.title and not extracted_title:
                        extracted_title = meta2.title
                    # Only use rendered content if it's actually better
                    if len(rendered_text.strip()) > len(text.strip()):
                        text = rendered_text
                        browser_rendered = True

                    # Capture rendered HTML for Phase 2 image extraction (#131).
                    # Intentionally unconditional: the rendered DOM may contain
                    # JS-injected images even when the rendered *text* was not
                    # better than static (browser_rendered stays False).
                    rendered_html_for_phase2 = render_result["html"]
                    # Use browser's final URL for resolving relative <img src>
                    # (may differ from httpx response.url after client-side nav).
                    final = render_result.get("final_url") or ""
                    if final and urlparse(final).scheme in ("http", "https"):
                        rendered_base_url = final
                    else:
                        rendered_base_url = str(response.url)

                    # Extract figures from screenshot (isolated from text ingest)
                    screenshot = render_result.get("screenshot_path")
                    if screenshot and screenshot.exists():
                        try:
                            figures_extracted = _extract_web_figures(
                                conn, url, screenshot
                            )
                        except Exception:
                            logger.warning(
                                "Figure extraction failed for %s",
                                url,
                                exc_info=True,
                            )
                            figures_extracted = 0

                    # Extract per-element captures (Phase 3, #132)
                    element_captures = render_result.get("element_captures")
                    if element_captures:
                        try:
                            figures_extracted += _extract_element_captures(
                                conn, url, element_captures
                            )
                        except Exception:
                            logger.warning(
                                "Element capture extraction failed for %s",
                                url,
                                exc_info=True,
                            )
                finally:
                    tmpdir = render_result.get("tmpdir")
                    if tmpdir:
                        shutil.rmtree(tmpdir, ignore_errors=True)

    # Extract inline images from HTML.
    # Phase 1: always parse static HTML.
    # Phase 2 (#131): when browser fallback fired, also parse rendered DOM.
    extra_sources: list[tuple[str, str]] | None = None
    if rendered_html_for_phase2 is not None:
        extra_sources = [(rendered_html_for_phase2, rendered_base_url)]

    try:
        inline_figures = _extract_html_images(
            conn,
            html,
            source_url=url,
            base_url=str(response.url),
            extra_html_sources=extra_sources,
        )
        figures_extracted += inline_figures
    except Exception:
        logger.warning("Inline image extraction failed for %s", url, exc_info=True)

    _base_result: dict = {
        "url": url,
        "source_uri": url,
        "source_type": "web",
        "browser_rendered": browser_rendered,
        "figures_extracted": figures_extracted,
    }

    if not text.strip():
        return {**_base_result, "chunks_added": 0, "chunks_skipped": 0}

    chunks = _chunk_text(text)
    if not chunks:
        return {**_base_result, "chunks_added": 0, "chunks_skipped": 0}

    # Compute content hashes, skip duplicates.
    # Defer chunk_sessions writes until after embeddings succeed (#180).
    new_chunks = []
    skipped = 0
    deferred_session_links: list[int] = []
    meta_json = json.dumps({"title": extracted_title} if extracted_title else {})
    for i, chunk in enumerate(chunks):
        h = _content_hash(chunk)
        existing = conn.execute(
            "SELECT id FROM chunks WHERE content_hash = ?", (h,)
        ).fetchone()
        if existing:
            if session_id is not None:
                deferred_session_links.append(existing["id"])
            skipped += 1
            continue
        new_chunks.append((i, chunk, h))

    if not new_chunks:
        _flush_deferred_session_links(conn, deferred_session_links, session_id)
        conn.commit()
        return {**_base_result, "chunks_added": 0, "chunks_skipped": skipped}

    texts_to_embed = [c[1] for c in new_chunks]
    embeddings = _embed_with_config(conn, texts_to_embed)

    for (idx, chunk_text, chunk_hash), emb_vec in zip(new_chunks, embeddings):
        _insert_chunk(
            conn,
            content_hash=chunk_hash,
            content=chunk_text,
            source_type="web",
            source_uri=url,
            chunk_index=idx,
            embedding=emb_vec,
            session_id=session_id,
            metadata=meta_json,
        )

    # Embeddings succeeded — flush deferred session links for deduped chunks
    _flush_deferred_session_links(conn, deferred_session_links, session_id)

    conn.commit()
    return {
        **_base_result,
        "chunks_added": len(new_chunks),
        "chunks_skipped": skipped,
        "title": extracted_title,
    }
