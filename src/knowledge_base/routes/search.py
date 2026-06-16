"""Search route: search_index, co_occurrence, status."""

from __future__ import annotations

import json
from pathlib import Path

from fastmcp import FastMCP

from .._conn import _get_conn
from ..db import co_occurrence_pairs
from ..embed_swap import get_embed_config
from ..exceptions import KnowledgeBaseError
from ..prediction_errors import detect_and_log, get_prediction_error_count
from ..search import search

mcp = FastMCP("search-routes")


@mcp.tool()
def search_index(
    query: str,
    top_k: int = 10,
    source_type: str | None = None,
    mode: str = "hybrid",
    keyword_prefilter: bool = False,
    chunk_strategy: str | None = None,
    space_name: str | None = None,
    rerank: bool = False,
) -> str:
    """Search the knowledge base using hybrid semantic + keyword search.

    Args:
        query: Natural language search query.
        top_k: Number of results to return (default 10).
        source_type: Filter results by type (pdf, markdown, code, web, note, figure).
        mode: Search mode - 'hybrid' (default), 'fts' (keyword only), 'vec' (semantic only).
        keyword_prefilter: Extract intent keywords for FTS matching instead of
            using the raw query. Improves precision for verbose natural language
            queries by stripping stopwords and filler. Default false.
        chunk_strategy: Filter by chunking strategy ('mechanical' or 'semantic').
            None (default) returns all chunks regardless of strategy.
        space_name: Search a specific embedding space instead of the active one.
            Useful for A/B comparison before promoting a new space.
        rerank: Enable cross-encoder reranking for improved relevance. Requires
            onnxruntime (install with: uv sync --group reranker). Default false.
    """
    conn = _get_conn()
    try:
        results = search(
            conn,
            query,
            top_k=top_k,
            source_type=source_type,
            mode=mode,
            keyword_prefilter=keyword_prefilter,
            chunk_strategy=chunk_strategy,
            space_name=space_name,
            rerank=rerank,
        )
    except KnowledgeBaseError as e:
        return json.dumps({"error": str(e), **e.details})

    detect_and_log(conn, query, results, source_type_filter=source_type, mode=mode)

    return json.dumps(
        [
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
        ]
    )


@mcp.tool()
def co_occurrence(min_sessions: int = 1) -> str:
    """Find document pairs that were ingested together in the same session.

    Co-ingestion is a behavioral signal: documents ingested together share
    research context at ingestion time. This complements embedding similarity
    by capturing relationships that no query could surface via BM25 or cosine.

    Args:
        min_sessions: Minimum number of shared sessions to include a pair (default 1).
    """
    conn = _get_conn()
    return json.dumps(co_occurrence_pairs(conn, min_sessions))


@mcp.tool()
def status() -> str:
    """Get index statistics: chunk counts by type, recent ingestions, DB size."""
    conn = _get_conn()

    type_counts = conn.execute(
        "SELECT source_type, COUNT(*) as count FROM chunks GROUP BY source_type"
    ).fetchall()

    total = conn.execute("SELECT COUNT(*) as count FROM chunks").fetchone()["count"]

    paper_count = conn.execute("SELECT COUNT(*) as count FROM papers").fetchone()[
        "count"
    ]
    conclusion_count = conn.execute(
        "SELECT COUNT(*) as count FROM conclusions"
    ).fetchone()["count"]
    relationship_count = conn.execute(
        "SELECT COUNT(*) as count FROM relationships"
    ).fetchone()["count"]
    folder_summary_count = conn.execute(
        "SELECT COUNT(*) as count FROM folder_summaries"
    ).fetchone()["count"]
    method_count = conn.execute("SELECT COUNT(*) as count FROM methods").fetchone()[
        "count"
    ]
    dataset_count = conn.execute("SELECT COUNT(*) as count FROM datasets").fetchone()[
        "count"
    ]
    metric_count = conn.execute("SELECT COUNT(*) as count FROM metrics").fetchone()[
        "count"
    ]

    recent = conn.execute(
        "SELECT source_uri, source_type, COUNT(*) as chunks, created_at"
        " FROM chunks GROUP BY source_uri ORDER BY created_at DESC LIMIT 5"
    ).fetchall()

    # Report the DB the connection actually opened (env/CLI-resolved, #449), not
    # the hardcoded default — they differ when KNOWLEDGE_BASE_DB is set.
    db_main = next(
        (r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main"), ""
    )
    db_path = Path(db_main) if db_main else None
    db_size_bytes = db_path.stat().st_size if db_path and db_path.exists() else 0

    job_counts = {}
    for row in conn.execute(
        "SELECT status, COUNT(*) as count FROM jobs GROUP BY status"
    ).fetchall():
        job_counts[row["status"]] = row["count"]

    space_counts = {}
    for row in conn.execute(
        "SELECT status, COUNT(*) as count FROM embed_spaces GROUP BY status"
    ).fetchall():
        space_counts[row["status"]] = row["count"]

    return json.dumps(
        {
            "total_chunks": total,
            "by_type": {row["source_type"]: row["count"] for row in type_counts},
            "papers": paper_count,
            "conclusions": conclusion_count,
            "relationships": relationship_count,
            "folder_summaries": folder_summary_count,
            "methods": method_count,
            "datasets": dataset_count,
            "metrics": metric_count,
            "prediction_errors": get_prediction_error_count(conn),
            "jobs": job_counts,
            "embed_spaces": space_counts,
            "embed_config": get_embed_config(conn),
            "chunk_strategy": (lambda r: r["value"] if r else "mechanical")(
                conn.execute(
                    "SELECT value FROM config WHERE key = 'chunk_strategy'"
                ).fetchone()
            ),
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
            "db_path": str(db_path) if db_path else "",
        }
    )
