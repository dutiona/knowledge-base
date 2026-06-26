"""Search route: search_index, co_occurrence, status."""

from __future__ import annotations

import json

from fastmcp import FastMCP

from .._conn import _get_conn
from ..db import co_occurrence_pairs, get_index_stats
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

    # All inline count/aggregation SQL now lives in db.get_index_stats (#217);
    # the cross-module figures below (prediction errors, embedding config) stay
    # here because they are already function calls, not inline SQL.
    stats = get_index_stats(conn)

    embed_cfg = get_embed_config(conn)
    embed_cfg.pop("api_key", None)  # never expose the key (or env: spec) over the tool surface

    return json.dumps(
        {
            "total_chunks": stats["total_chunks"],
            "by_type": stats["by_type"],
            "papers": stats["papers"],
            "conclusions": stats["conclusions"],
            "relationships": stats["relationships"],
            "folder_summaries": stats["folder_summaries"],
            "methods": stats["methods"],
            "datasets": stats["datasets"],
            "metrics": stats["metrics"],
            "prediction_errors": get_prediction_error_count(conn),
            "jobs": stats["jobs"],
            "embed_spaces": stats["embed_spaces"],
            "embed_config": embed_cfg,
            "chunk_strategy": stats["chunk_strategy"],
            "recent_ingestions": stats["recent_ingestions"],
            "db_size_mb": round(stats["db_size_bytes"] / (1024 * 1024), 2),
            "db_path": stats["db_path"],
        }
    )
