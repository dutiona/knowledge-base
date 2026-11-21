"""Ingestion route: ingest, reingest, ingest_text, ingest_url, configure_chunking, configure_browser."""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from pathlib import Path

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from .._conn import _get_conn
from ..auth import calling_machine
from ..exceptions import NotFoundError, ValidationError
from ..ingest import ingest_directory, ingest_file, ingest_text as _ingest_text, reingest_file
from ..web import configure_browser, ingest_url as _ingest_url
from . import RACE, embedder_failure, tool

mcp = FastMCP("ingestion-routes")


@tool(mcp)
def ingest(
    path: str,
    source_type: str | None = None,
    session_id: str | None = None,
) -> str:
    """Ingest a file or directory into the knowledge base.

    Args:
        path: Absolute path to a file or directory.
        source_type: Override auto-detection. One of: pdf, markdown, code, web, note, figure.
        session_id: Optional session ID to group co-ingested documents.
            For directories, a shared session ID is auto-generated.
            For files, pass the same session_id across calls to mark co-occurrence.
    """
    conn = _get_conn()
    p = Path(path).expanduser().resolve()

    if not p.exists():
        raise NotFoundError(f"Path does not exist: {p}")

    if p.is_dir():
        results = ingest_directory(conn, p)
        total_added = sum(r["chunks_added"] for r in results)
        total_skipped = sum(r["chunks_skipped"] for r in results)
        return json.dumps(
            {
                "files_processed": len(results),
                "chunks_added": total_added,
                "chunks_skipped": total_skipped,
                "details": results,
            }
        )
    else:
        result = ingest_file(conn, p, source_type, session_id=session_id)
        return json.dumps(result)


@tool(mcp)
def reingest(
    path: str,
    source_type: str | None = None,
    session_id: str | None = None,
) -> str:
    """Force re-ingest of a previously ingested file. Deletes old chunks and inserts new ones.

    Cleans up FK references in papers, relationships, and conclusions.

    Args:
        path: Absolute path to the file to re-ingest.
        source_type: Override auto-detection. One of: pdf, markdown, code, web, note, figure.
        session_id: Optional session ID for co-occurrence tracking.
    """
    conn = _get_conn()
    p = Path(path).expanduser().resolve()
    result = reingest_file(conn, p, source_type, session_id=session_id)

    # Invalidate stale "similar" relationships for all papers linked to this file
    source_uri = p.as_posix()
    affected = conn.execute("SELECT paper_id FROM paper_paths WHERE path = ?", (source_uri,)).fetchall()
    if affected:
        from ..jobs import submit_job

        for row in affected:
            pid = row["paper_id"]
            conn.execute(
                "DELETE FROM relationships WHERE relation_type = 'similar' "
                "AND (source_paper_id = ? OR target_paper_id = ?)",
                (pid, pid),
            )
            submit_job(conn, pid, "auto_relate", {"paper_id": pid})
        conn.commit()
    return json.dumps(result)


#: Controls, format characters (bidi overrides, zero-width), lone surrogates, line and paragraph separators: a name
#: is shown in other machines' results, and a lone surrogate cannot be stored.
_REFUSED_NAME_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})
_NAME_RULE = (
    "name must be 1 to 255 characters, with no control, format or surrogate characters and no line or paragraph "
    "separators, not starting with /"
)


@tool(mcp)
def ingest_text(name: str, content: str) -> str:
    """Ingest a text or markdown document you pass in, under a name search results will cite.

    The document is stored as ``content://<machine>/<name>``, the machine being the caller's.
    Ingest only adds: identical text is skipped, and re-ingesting changed text under the same
    name keeps the earlier version searchable beside the new one, so give each revision its own
    name. A failed ingest stored nothing. Token-shaped secrets and URL passwords are stored as
    ``[REDACTED:<rule>]``: KB is no place to keep a credential.

    Args:
        name: The document's name, for example 'notes/attention.md'. An opaque label: KB never
            opens it as a path.
        content: The text or markdown to store.
    """
    name = name.strip()
    if (
        not 1 <= len(name) <= 255
        or name.startswith("/")
        or any(unicodedata.category(c) in _REFUSED_NAME_CATEGORIES for c in name)
    ):
        raise ToolError(_NAME_RULE)
    if not content.strip():
        raise ToolError("content is empty: there is nothing to ingest")
    source_uri = f"content://{calling_machine() or ''}/{name}"
    conn = _get_conn()
    try:
        stored = _ingest_text(conn, content, source_uri=source_uri, source_type="markdown")
    except sqlite3.IntegrityError as e:
        raise ToolError(f"{RACE}; nothing was stored, retry") from e
    except httpx.HTTPError as e:
        kind, retryable = embedder_failure(e)
        raise ToolError(f"{kind}; nothing was stored" + (", retry later" if retryable else "")) from e
    return json.dumps({"source_uri": source_uri, "source_type": "markdown", **stored})


@tool(mcp)
def ingest_url(url: str, session_id: str | None = None) -> str:
    """Ingest a web page by URL. Fetches the page, extracts main content, and indexes it.

    Uses content-hash dedup — unchanged pages are skipped on re-ingest.
    Re-ingesting a changed page adds its new text passages beside the earlier ones; no tool replaces
    or removes a page's text. Token-shaped secrets and URL passwords are stored as
    ``[REDACTED:<rule>]``, and the page under its URL without its userinfo.

    Args:
        url: The URL to fetch and ingest.
        session_id: Optional session ID for co-occurrence tracking.
    """
    conn = _get_conn()
    try:
        return json.dumps(_ingest_url(conn, url, session_id=session_id))
    except sqlite3.IntegrityError as e:
        raise ToolError(f"{RACE}; the page's text was not stored, retry") from e
    except httpx.HTTPError as e:  # ingest_url reports every page-fetch failure as ValidationError: this is the embedder
        kind, retryable = embedder_failure(e)
        raise ToolError(f"{kind}; the page's text was not stored" + (", retry later" if retryable else "")) from e


@tool(mcp)
def configure_chunking(strategy: str | None = None) -> str:
    """Configure the chunking strategy for PDF ingestion.

    With 32K-context embedding models, 'semantic' chunking splits PDFs at
    section boundaries (3-5 chunks per paper). The default 'mechanical' uses
    fixed-size splitting (1000 chars + overlap, ~15 chunks per paper).

    Non-PDF content (markdown, code, web) always uses mechanical chunking
    regardless of this setting.

    Args:
        strategy: 'mechanical' or 'semantic'. Omit to query current strategy.
    """
    conn = _get_conn()
    if strategy is None:
        row = conn.execute("SELECT value FROM config WHERE key = 'chunk_strategy'").fetchone()
        return json.dumps({"chunk_strategy": row["value"] if row else "mechanical"})
    if strategy not in ("mechanical", "semantic"):
        raise ValidationError(f"Invalid strategy: {strategy!r}. Must be 'mechanical' or 'semantic'.")
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('chunk_strategy', ?)",
        (strategy,),
    )
    conn.commit()
    return json.dumps({"chunk_strategy": strategy})


@tool(mcp)
def configure_browser_tool(
    cdp_endpoint: str | None = None,
    venv_path: str | None = None,
) -> str:
    """Configure browser rendering for JS-heavy web pages.

    Enables fallback rendering when trafilatura extracts insufficient content
    from a URL (< 200 chars). Also captures screenshots for figure extraction
    via the vision pipeline. Only http/https URLs are rendered.

    Note: browser rendering executes page JavaScript, which can issue secondary
    requests beyond the original URL. This is an accepted trade-off for an
    optional feature. Each render uses an ephemeral browser context (no shared state).

    Two modes (both require a venv with ``playwright`` Python client installed):
    - **CDP:** Connect to a running Playwright browser. Provide both cdp_endpoint and venv_path.
      The endpoint keeps the SSRF floor: a public IP address, or ``ws://localhost:<port>`` with
      ``allow_loopback_base_url`` (set through configure_llm_tool). A host name is refused, since
      the browser would resolve it again after KB's check.
    - **Local:** Launch headless Chromium from the venv.
      Provide venv_path only.

    Local venv setup::

        python -m venv /path/to/venv
        /path/to/venv/bin/pip install playwright
        /path/to/venv/bin/playwright install --with-deps chromium

    CDP venv setup (Chromium not needed locally)::

        python -m venv /path/to/venv
        /path/to/venv/bin/pip install playwright

    Args:
        cdp_endpoint: WebSocket CDP endpoint (ws:// or wss://, an IP address or localhost). Requires venv_path too.
        venv_path: Absolute path to Python venv with playwright installed.
            Pass both as empty string to disable browser rendering.
    """
    conn = _get_conn()
    return json.dumps(configure_browser(conn, cdp_endpoint=cdp_endpoint, venv_path=venv_path))
