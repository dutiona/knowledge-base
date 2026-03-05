"""FastMCP server exposing research-index tools."""

from __future__ import annotations

import json
from pathlib import Path

from fastmcp import FastMCP

from .db import DEFAULT_DB_PATH, get_connection, init_schema
from .ingest import ingest_directory, ingest_file
from .search import search

mcp = FastMCP(
    "research-index",
    instructions=(
        "Hybrid semantic search over research papers, code, and notes. "
        "Use 'search' to find relevant content by concept or keyword. "
        "Use 'ingest' to add new files. Use 'status' for index statistics."
    ),
)

_conn = None


def _get_conn():
    global _conn
    if _conn is None:
        _conn = get_connection()
        init_schema(_conn)
    return _conn


@mcp.tool()
def ingest(path: str, source_type: str | None = None) -> str:
    """Ingest a file or directory into the research index.

    Args:
        path: Absolute path to a file or directory.
        source_type: Override auto-detection. One of: pdf, markdown, code, web, note.
    """
    conn = _get_conn()
    p = Path(path).expanduser().resolve()

    if not p.exists():
        return json.dumps({"error": f"Path does not exist: {p}"})

    if p.is_dir():
        results = ingest_directory(conn, p)
        total_added = sum(r["chunks_added"] for r in results)
        total_skipped = sum(r["chunks_skipped"] for r in results)
        return json.dumps({
            "files_processed": len(results),
            "chunks_added": total_added,
            "chunks_skipped": total_skipped,
            "details": results,
        })
    else:
        result = ingest_file(conn, p, source_type)
        return json.dumps(result)


@mcp.tool()
def search_index(
    query: str,
    top_k: int = 10,
    source_type: str | None = None,
    mode: str = "hybrid",
) -> str:
    """Search the research index using hybrid semantic + keyword search.

    Args:
        query: Natural language search query.
        top_k: Number of results to return (default 10).
        source_type: Filter results by type (pdf, markdown, code, web, note).
        mode: Search mode - 'hybrid' (default), 'fts' (keyword only), 'vec' (semantic only).
    """
    conn = _get_conn()
    results = search(conn, query, top_k=top_k, source_type=source_type, mode=mode)

    return json.dumps([
        {
            "chunk_id": r.chunk_id,
            "content": r.content,
            "source_type": r.source_type,
            "source_uri": r.source_uri,
            "chunk_index": r.chunk_index,
            "score": round(r.score, 6),
            "match_type": r.match_type,
        }
        for r in results
    ])


@mcp.tool()
def status() -> str:
    """Get index statistics: chunk counts by type, recent ingestions, DB size."""
    conn = _get_conn()

    type_counts = conn.execute(
        "SELECT source_type, COUNT(*) as count FROM chunks GROUP BY source_type"
    ).fetchall()

    total = conn.execute("SELECT COUNT(*) as count FROM chunks").fetchone()["count"]

    paper_count = conn.execute("SELECT COUNT(*) as count FROM papers").fetchone()["count"]
    conclusion_count = conn.execute("SELECT COUNT(*) as count FROM conclusions").fetchone()["count"]
    relationship_count = conn.execute("SELECT COUNT(*) as count FROM relationships").fetchone()["count"]

    recent = conn.execute(
        """SELECT source_uri, source_type, COUNT(*) as chunks, created_at
           FROM chunks GROUP BY source_uri
           ORDER BY created_at DESC LIMIT 5"""
    ).fetchall()

    db_size_bytes = DEFAULT_DB_PATH.stat().st_size if DEFAULT_DB_PATH.exists() else 0

    return json.dumps({
        "total_chunks": total,
        "by_type": {row["source_type"]: row["count"] for row in type_counts},
        "papers": paper_count,
        "conclusions": conclusion_count,
        "relationships": relationship_count,
        "recent_ingestions": [
            {
                "source_uri": row["source_uri"],
                "source_type": row["source_type"],
                "chunks": row["chunks"],
                "created_at": row["created_at"],
            }
            for row in recent
        ],
        "db_size_mb": round(db_size_bytes / (1024 * 1024), 2),
        "db_path": str(DEFAULT_DB_PATH),
    })


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
