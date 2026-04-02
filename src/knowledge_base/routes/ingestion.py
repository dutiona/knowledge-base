"""Ingestion route: ingest, reingest, ingest_url, configure_chunking, configure_browser."""

from __future__ import annotations

import json
from pathlib import Path

from fastmcp import FastMCP

from .._conn import _get_conn
from ..exceptions import KnowledgeBaseError
from ..ingest import ingest_directory, ingest_file, reingest_file
from ..web import configure_browser, ingest_url as _ingest_url

mcp = FastMCP("ingestion-routes")


@mcp.tool()
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
        return json.dumps({"error": f"Path does not exist: {p}"})

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


@mcp.tool()
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

    if not p.exists():
        return json.dumps({"error": f"Path does not exist: {p}"})

    try:
        result = reingest_file(conn, p, source_type, session_id=session_id)
    except KnowledgeBaseError as e:
        err = {"error": str(e), **e.details}
        return json.dumps(err)

    # Invalidate stale "similar" relationships for all papers linked to this file
    source_uri = p.as_posix()
    affected = conn.execute(
        "SELECT paper_id FROM paper_paths WHERE path = ?", (source_uri,)
    ).fetchall()
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


@mcp.tool()
def ingest_url(url: str, session_id: str | None = None) -> str:
    """Ingest a web page by URL. Fetches the page, extracts main content, and indexes it.

    Args:
        url: URL to ingest (http or https only).
        session_id: Optional session ID for co-occurrence tracking.
    """
    conn = _get_conn()
    return json.dumps(_ingest_url(conn, url, session_id=session_id))


@mcp.tool()
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
        row = conn.execute(
            "SELECT value FROM config WHERE key = 'chunk_strategy'"
        ).fetchone()
        return json.dumps({"chunk_strategy": row["value"] if row else "mechanical"})
    if strategy not in ("mechanical", "semantic"):
        return json.dumps(
            {
                "error": f"Invalid strategy: {strategy!r}. Must be 'mechanical' or 'semantic'."
            }
        )
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('chunk_strategy', ?)",
        (strategy,),
    )
    conn.commit()
    return json.dumps({"chunk_strategy": strategy})


@mcp.tool()
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
    - **CDP (recommended for Docker):** Connect to a running Playwright container.
      Provide both cdp_endpoint and venv_path.
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
        cdp_endpoint: WebSocket CDP endpoint (ws:// or wss://). Requires venv_path too.
        venv_path: Absolute path to Python venv with playwright installed.
            Pass both as empty string to disable browser rendering.
    """
    conn = _get_conn()
    try:
        return json.dumps(
            configure_browser(conn, cdp_endpoint=cdp_endpoint, venv_path=venv_path)
        )
    except KnowledgeBaseError as e:
        err = {"error": str(e), **e.details}
        return json.dumps(err)
