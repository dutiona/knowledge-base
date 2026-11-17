"""Web ingestion: URL fetching, SSRF protection, browser rendering, and inline image extraction."""

from __future__ import annotations

import codecs
import html.parser
import ipaddress
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
import trafilatura

from .db import (
    _batched_execute,
    bump_chunk_count,
    delete_chunk_vecs,
    delete_chunks_cascade,
    get_vec_table_name,
)
from .exceptions import ValidationError
from .settings import (
    BROWSER_VAR,
    FETCH_DEADLINE_VAR,
    FETCH_MAX_BYTES_VAR,
    FETCH_MAX_REDIRECTS_VAR,
    fetch_limits,
    switch_on,
)
from .utils import (
    DeadlineExceeded,
    _is_allowed_loopback,
    deadline_left,
    egress_client,
    egress_deadline,
    is_private_ip,
    without_userinfo,
    loopback_opt_in,
    validate_base_url,
)
from .ingest import (
    _cleanup_conclusion_refs,
    _embed_with_config,
    _insert_chunk,
    ingest_text,
)
from .redact import scrub
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
            # Skip sources with media queries — we can't evaluate them
            # server-side (no viewport context). Only unconditional sources
            # are reliable for a content indexer.
            if "media" in d:
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
                if "srcset" in d:
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
        "UPDATE relationships SET evidence_chunk_id = NULL WHERE evidence_chunk_id IN ({ph})",
        chunk_ids,
    )

    _cleanup_conclusion_refs(conn, chunk_ids)

    for table in ("methods", "datasets", "metrics"):
        _batched_execute(
            conn,
            f"UPDATE {table} SET chunk_id = NULL WHERE chunk_id IN ({{ph}})",  # noqa: S608  # trusted internal identifier, not user input
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
        if w is not None and h is not None and (w < _MIN_IMAGE_DIMENSION or h < _MIN_IMAGE_DIMENSION):
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
    the fetched page's final URL when it redirected so relative paths resolve
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
            extra = _parse_image_candidates(extra_html, extra_base, exclude_urls=seen_urls)
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

    # Every request and redirect hop is floored and pinned by the client (#572), and each download keeps the
    # page's fetch limits (#588).
    with egress_client(floor=True, timeout=15.0) as image_client:
        for image_url, alt_text in candidates:
            try:
                image_bytes = bytes(_fetch(image_client, image_url).body)  # BytesIO would copy a bytearray again
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
                figures = _vision_call(b64, prompt, **vision_config)
            except Exception:
                logger.warning("Vision call failed for image %s", image_url, exc_info=True)
                continue

            for fig in figures:
                desc = scrub(fig.get("description", ""))
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
    # Resolve the active vec table once so the per-chunk inserts and the single
    # batched chunk_count bump below both target it; suppress the per-row UPDATE
    # (bump_count=False) and bump once with the count of embeddings actually
    # inserted (#392). Not every figure embeds, so tally non-None separately.
    vec_table = get_vec_table_name(conn)
    figures_added = 0
    n_inserted = 0
    for idx, ((desc, meta), emb_vec) in enumerate(zip(collected, embeddings, strict=True)):
        chunk_hash = _content_hash(desc)
        existing = conn.execute("SELECT id FROM chunks WHERE content_hash = ?", (chunk_hash,)).fetchone()
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
            vec_table=vec_table,
            bump_count=False,
        )
        figures_added += 1
        if emb_vec is not None:
            n_inserted += 1

    if n_inserted:
        bump_chunk_count(conn, vec_table, n_inserted)

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


def _check_cdp_endpoint(endpoint: str, *, allow_loopback: bool) -> str:
    """The CDP endpoint keeps the SSRF floor (#572); returns the canonical form to store and use.

    Playwright connects to it out of process and would resolve a name again after this check, so the host
    must be an IP literal, or ``localhost`` under the loopback opt-in: there is then no lookup to rebind.
    The browser also parses the URL itself (WHATWG rules), so any form two parsers could read differently
    (userinfo, a backslash, whitespace) is refused, and the endpoint is rebuilt from the checked parts.
    """
    if "\\" in endpoint or any(c.isspace() or not c.isprintable() for c in endpoint):
        raise ValidationError("the CDP endpoint must not contain backslashes, whitespace or control characters")
    parsed = urlparse(endpoint)
    scheme = parsed.scheme.lower()
    if scheme not in ("ws", "wss"):
        raise ValidationError(f"CDP endpoint must use ws:// or wss://, got {parsed.scheme}://")
    if "@" in parsed.netloc:
        raise ValidationError("the CDP endpoint must not carry userinfo")
    host = parsed.hostname or ""
    if "%" in host:  # an IPv6 zone id: Python accepts it, a browser's URL parser does not
        raise ValidationError("the CDP endpoint must not carry an IPv6 zone id")
    if not (allow_loopback and _is_allowed_loopback(host)):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            raise ValidationError(
                "the CDP endpoint must be an IP address (or localhost with allow_loopback_base_url)"
            ) from None
    try:
        port = parsed.port
    except ValueError:
        raise ValidationError("the CDP endpoint has an invalid port") from None
    authority = (f"[{host}]" if ":" in host else host) + (f":{port}" if port is not None else "")
    validate_base_url(f"{'https' if scheme == 'wss' else 'http'}://{authority}", allow_loopback=allow_loopback)
    return urlunparse((scheme, authority, parsed.path, parsed.params, parsed.query, ""))


def _get_browser_config(conn: sqlite3.Connection) -> dict | None:
    """Read browser rendering configuration from config table.

    Returns a dict with mode/endpoint/venv keys, or None when unconfigured or while the
    browser fallback is off: Playwright runs page JavaScript, so it runs only when switched
    on (#569).
    """
    if not switch_on(BROWSER_VAR):
        return None
    rows = conn.execute(
        "SELECT key, value FROM config WHERE key IN ('browser_mode', 'browser_venv', 'browser_endpoint')"
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
            try:
                config["endpoint"] = _check_cdp_endpoint(endpoint, allow_loopback=loopback_opt_in(conn))
            except ValidationError as exc:
                logger.warning("Stored CDP endpoint refused, browser fallback disabled: %s", exc)
                return None

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

    Refuses while the browser fallback is off.
    """
    if not switch_on(BROWSER_VAR):
        raise ValidationError(f"the browser fallback is off; set {BROWSER_VAR}=on to enable it")
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
        raise ValidationError("venv_path is required (playwright Python client must be installed)")

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
        cdp_endpoint = _check_cdp_endpoint(cdp_endpoint, allow_loopback=loopback_opt_in(conn))
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
        subprocess.run(  # noqa: S603  # trusted argv, no shell; url passed as single argv element, scheme validated in render script
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
    final_url = final_url_path.read_text(encoding="utf-8").strip() if final_url_path.exists() else None
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
        figures = _vision_call(b64, prompt, **vision_config)
    except Exception:
        logger.warning("Vision call failed for web screenshot %s", source_url, exc_info=True)
        return 0

    if not figures:
        return 0

    # Optional: OmniParser enrichment (uses server mode when available, #334)
    omniparser_path = _get_omniparser_config(conn)
    omni_elements: list[dict] | None = None
    if omniparser_path:
        server = _resolve_omniparser_server_url(conn, omniparser_path)
        omni_result = _run_omniparser(screenshot_path, omniparser_path, server=server)
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
        texts.append(scrub(desc))
        valid_figures.append(fig)

    if not texts:
        return 0

    embeddings = _embed_with_config(conn, texts)

    # Remove stale screenshot figure chunks only (scope to < _WEB_IMAGE_CHUNK_INDEX_START
    # to avoid deleting inline image figures managed by _extract_html_images)
    old_fig_ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM chunks WHERE source_uri = ? AND source_type = 'figure' AND chunk_index < ?",
            (source_url, _WEB_IMAGE_CHUNK_INDEX_START),
        ).fetchall()
    ]
    if old_fig_ids:
        _cleanup_figure_fk_refs(conn, old_fig_ids)
        delete_chunk_vecs(conn, old_fig_ids)
        _batched_execute(conn, "DELETE FROM chunks WHERE id IN ({ph})", old_fig_ids)

    # Resolve the active vec table once for the per-chunk inserts and the single
    # batched chunk_count bump (#392); suppress the per-row UPDATE and bump once
    # with the count of embeddings actually inserted (some figures may not embed).
    vec_table = get_vec_table_name(conn)
    figures_added = 0
    n_inserted = 0

    for idx, (fig, desc, emb_vec) in enumerate(zip(valid_figures, texts, embeddings, strict=True)):
        chunk_hash = _content_hash(desc)
        existing = conn.execute("SELECT id FROM chunks WHERE content_hash = ?", (chunk_hash,)).fetchone()
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
            vec_table=vec_table,
            bump_count=False,
        )
        figures_added += 1
        if emb_vec is not None:
            n_inserted += 1

    if n_inserted:
        bump_chunk_count(conn, vec_table, n_inserted)

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
            figures = _vision_call(b64, prompt, **vision_config)
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
        desc = scrub(fig.get("description", ""))
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
            "SELECT id FROM chunks WHERE source_uri = ? AND source_type = 'figure' AND chunk_index >= ?",
            (source_url, _WEB_ELEMENT_CAPTURE_CHUNK_INDEX_START),
        ).fetchall()
    ]
    if old_ids:
        _cleanup_figure_fk_refs(conn, old_ids)
        delete_chunk_vecs(conn, old_ids)
        _batched_execute(conn, "DELETE FROM chunks WHERE id IN ({ph})", old_ids)

    # Resolve the active vec table once for the per-chunk inserts and the single
    # batched chunk_count bump (#392); suppress the per-row UPDATE and bump once
    # with the count of embeddings actually inserted (some captures may not embed).
    vec_table = get_vec_table_name(conn)
    figures_added = 0
    n_inserted = 0
    for (desc, meta, cap_idx), emb_vec in zip(collected, embeddings, strict=True):
        chunk_hash = _content_hash(desc)
        existing = conn.execute("SELECT id FROM chunks WHERE content_hash = ?", (chunk_hash,)).fetchone()
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
            vec_table=vec_table,
            bump_count=False,
        )
        figures_added += 1
        if emb_vec is not None:
            n_inserted += 1

    if n_inserted:
        bump_chunk_count(conn, vec_table, n_inserted)

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
# Fetching a tool-supplied URL under the fetch limits (#588)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Fetched:
    body: bytearray
    url: httpx.URL
    encoding: str


#: The userinfo of an http(s) URL inside a message, up to the authority's last "@".
_USERINFO = re.compile(r"(?i)(https?://)[^\s/?#]*@")


def _fetch(client: httpx.Client, url: str) -> _Fetched:
    """GET *url* with *client* (a floored client) under the fetch limits.

    Redirects are followed here, so a redirect's body is never read: httpx's own follower reads each one in full.
    The body is asked for uncompressed and counted as it streams; a server that compresses anyway is decoded
    (gzip or deflate, one layer) and its decoded bytes count. The deadline covers every hop and socket operation.
    A limit raises ``ValidationError``; any other failure is the ``httpx.HTTPError`` it was.
    """
    shown = without_userinfo(url)  # an error is returned and logged, never with the URL's credentials
    limits = fetch_limits()
    request = client.build_request("GET", url, headers={"Accept-Encoding": "identity"})
    redirects = 0
    try:
        with egress_deadline(limits.deadline):
            while True:
                response = client.send(request, stream=True, follow_redirects=False)
                try:
                    if response.next_request is not None:
                        redirects += 1
                        if redirects > limits.max_redirects:
                            raise ValidationError(
                                f"Failed to fetch {shown}: more than {limits.max_redirects} redirects "
                                f"({FETCH_MAX_REDIRECTS_VAR})"
                            )
                        request = response.next_request
                        continue
                    response.raise_for_status()
                    encoding = response.headers.get("content-encoding", "")
                    codings = [c for c in (part.strip().lower() for part in encoding.split(",")) if c]
                    if codings not in ([], ["identity"], ["gzip"], ["deflate"]):
                        raise ValidationError(f"Failed to fetch {shown}: unsupported Content-Encoding {encoding!r}")
                    body = bytearray()  # one buffer: a list of many tiny chunks costs far more than their bytes
                    for chunk in response.iter_bytes():
                        if len(body) + len(chunk) > limits.max_bytes:
                            raise ValidationError(
                                f"Failed to fetch {shown}: the response is over the {limits.max_bytes}-byte cap "
                                f"({FETCH_MAX_BYTES_VAR})"
                            )
                        body += chunk
                        deadline_left()
                    deadline_left()
                    return _Fetched(body, response.url, response.encoding or "utf-8")
                finally:
                    response.close()
    except DeadlineExceeded:
        raise ValidationError(
            f"Failed to fetch {shown}: not done within the {limits.deadline} s deadline ({FETCH_DEADLINE_VAR})"
        ) from None


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
    try:
        parsed = urlparse(url)
    except ValueError as e:  # an unclosed IPv6 bracket
        raise ValidationError(f"Invalid URL {url!r}: {e}") from e
    if parsed.scheme not in _ALLOWED_URL_SCHEMES:
        raise ValidationError(f"URL scheme must be http or https, got: {parsed.scheme!r}")
    if not parsed.hostname:
        raise ValidationError("URL must include a hostname")
    # The page is stored and logged under its URL without userinfo; it is fetched with all of it (#592).
    source_uri = without_userinfo(url)
    # The client floors and pins the first request and every redirect hop, so a refused hop is never sent and
    # raises ValidationError (#572); _fetch bounds the fetch's size, redirects and time (#588).
    try:
        with egress_client(floor=True, timeout=30.0) as client:
            fetched = _fetch(client, url)
    except (httpx.HTTPError, httpx.InvalidURL) as e:  # InvalidURL (a bad port, a control character) is no HTTPError
        raise ValidationError(f"Failed to fetch {source_uri}: {_USERINFO.sub(r'\1', str(e))}") from e

    # punycode is a hostname codec that decodes a run of digits in quadratic time, after the fetch's deadline
    encoding = "utf-8" if codecs.lookup(fetched.encoding).name == "punycode" else fetched.encoding
    try:
        html = fetched.body.decode(encoding, errors="replace")  # httpx's own .text rule
    except (LookupError, UnicodeError):  # no text encoding (base64, rot13), or one refusing "replace" (idna, undefined)
        html = fetched.body.decode("utf-8", errors="replace")
    text = trafilatura.extract(html, include_links=False, include_tables=True) or ""
    extracted_title = None
    metadata = trafilatura.extract_metadata(html)
    if metadata and metadata.title:
        extracted_title = metadata.title

    browser_rendered = False
    figures_extracted = 0
    rendered_html_for_phase2: str | None = None
    rendered_base_url: str = str(fetched.url)

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
                    # (may differ from the fetched URL after client-side nav).
                    final = render_result.get("final_url") or ""
                    if final and urlparse(final).scheme in ("http", "https"):
                        rendered_base_url = final
                    else:
                        rendered_base_url = str(fetched.url)

                    # Extract figures from screenshot (isolated from text ingest)
                    screenshot = render_result.get("screenshot_path")
                    if screenshot and screenshot.exists():
                        try:
                            figures_extracted = _extract_web_figures(conn, source_uri, screenshot)
                        except Exception:
                            logger.warning(
                                "Figure extraction failed for %s",
                                source_uri,
                                exc_info=True,
                            )
                            figures_extracted = 0

                    # Extract per-element captures (Phase 3, #132)
                    element_captures = render_result.get("element_captures")
                    if element_captures:
                        try:
                            figures_extracted += _extract_element_captures(conn, source_uri, element_captures)
                        except Exception:
                            logger.warning(
                                "Element capture extraction failed for %s",
                                source_uri,
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
            source_url=source_uri,
            base_url=str(fetched.url),
            extra_html_sources=extra_sources,
        )
        figures_extracted += inline_figures
    except Exception:
        logger.warning("Inline image extraction failed for %s", source_uri, exc_info=True)

    _base_result: dict = {
        "url": url,
        "source_uri": source_uri,
        "source_type": "web",
        "browser_rendered": browser_rendered,
        "figures_extracted": figures_extracted,
    }

    if not text.strip():
        return {**_base_result, "chunks_added": 0, "chunks_skipped": 0}

    stored = ingest_text(
        conn,
        text,
        source_uri=source_uri,
        source_type="web",
        session_id=session_id,
        metadata={"title": extracted_title} if extracted_title else None,
    )
    if not stored["chunks_added"]:
        return {**_base_result, **stored}
    return {**_base_result, **stored, "title": extracted_title}
