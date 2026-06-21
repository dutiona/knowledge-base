"""Embeddings route: embed spaces lifecycle + comparison tools."""

from __future__ import annotations

import json

from fastmcp import FastMCP

from .._conn import _get_conn
from ..comparison import batch_compare_spaces, compare_spaces
from ..db import get_active_space
from ..embed_swap import (
    backfill_space,
    cleanup_space,
    create_space,
    deprecate_space,
    get_embed_config,
    list_spaces,
    promote_space,
    re_embed,
)

mcp = FastMCP("embeddings-routes")


@mcp.tool()
def embed_config() -> str:
    """Get current embedding model configuration (model name and dimension)."""
    conn = _get_conn()
    config = get_embed_config(conn)
    active = get_active_space(conn)
    if active:
        config["active_space"] = active["name"]
        config["chunk_strategy"] = active["chunk_strategy"]
        config["element_type"] = active.get("element_type", "float32")
        if active.get("matryoshka_base_dim"):
            config["matryoshka_base_dim"] = active["matryoshka_base_dim"]
    return json.dumps(config)


@mcp.tool()
def re_embed_tool(model: str, dim: int, matryoshka_base_dim: int | None = None) -> str:
    """Re-embed all chunks with a new embedding model.

    Drops and recreates the vector table with new dimensions, then re-embeds
    all existing chunks. This is expensive — use only when switching models.

    Args:
        model: Ollama model name (e.g. 'mxbai-embed-large', 'nomic-embed-text').
        dim: Embedding dimension for the new model. For Matryoshka models,
            this is the truncated storage dimension.
        matryoshka_base_dim: Native embedding dimension when using Matryoshka
            truncation. Must be greater than ``dim``. See
            ``create_embed_space_tool`` for details.
    """
    conn = _get_conn()
    result = re_embed(conn, model, dim, matryoshka_base_dim=matryoshka_base_dim)

    # All "similar" relationships are invalid after embedding space change
    conn.execute("DELETE FROM relationships WHERE relation_type = 'similar'")
    conn.commit()
    result["note"] = (
        "All 'similar' relationships removed (embedding space changed). "
        "Run scan_relationships() to recompute. "
        "Use list_embed_spaces_tool() to see all spaces."
    )

    return json.dumps(result)


@mcp.tool()
def list_embed_spaces_tool() -> str:
    """List all embedding spaces with status, progress, and chunk strategy."""
    conn = _get_conn()
    return json.dumps(list_spaces(conn))


@mcp.tool()
def create_embed_space_tool(
    name: str,
    model: str,
    dim: int,
    provider: str,
    chunk_strategy: str = "mechanical",
    matryoshka_base_dim: int | None = None,
    element_type: str = "float32",
) -> str:
    """Create a new embedding space in 'populating' status.

    Args:
        name: Unique space name (alphanumeric + underscores only).
        model: Embedding model name (e.g. 'qwen3-embedding').
        dim: Embedding dimension (e.g. 768, 1024). For Matryoshka models,
            this is the truncated storage dimension.
        provider: Embedding provider ('ollama', 'openai', 'onnx').
        chunk_strategy: Which chunks to embed ('mechanical' or 'semantic').
        matryoshka_base_dim: Native embedding dimension of the model when using
            Matryoshka truncation. The provider embeds at this dimension, then
            the system truncates to ``dim`` and L2 re-normalizes before storage.
            Must be greater than ``dim``. Only useful with MRL-capable models
            (e.g. Qwen3-Embedding, nomic-embed-text-v2-moe).
        element_type: Vector element type for storage. ``'float32'`` (default)
            stores full-precision vectors. ``'int8'`` uses sqlite-vec's scalar
            quantization (4x storage reduction, typically <2% quality loss).
    """
    conn = _get_conn()
    try:
        result = create_space(
            conn,
            name,
            model,
            dim,
            provider,
            chunk_strategy,
            matryoshka_base_dim,
            element_type=element_type,
        )
        return json.dumps(result)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def backfill_embed_space_tool(name: str, batch_size: int = 32) -> str:
    """Backfill an embedding space with chunk embeddings. Resumable.

    Args:
        name: Name of the space to backfill (must be in 'populating' status).
        batch_size: Number of chunks per embedding batch (default 32).
    """
    conn = _get_conn()
    try:
        result = backfill_space(conn, name, batch_size)
        return json.dumps(result)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def promote_embed_space_tool(name: str) -> str:
    """Promote an embedding space to active. Deprecates the current active space.

    Also updates config (embed_model, embed_dim, embed_provider, chunk_strategy)
    and invalidates all 'similar' relationships.

    Args:
        name: Name of the space to promote.
    """
    conn = _get_conn()
    try:
        result = promote_space(conn, name)
        # Invalidate similarity relationships (same as re_embed)
        conn.execute("DELETE FROM relationships WHERE relation_type = 'similar'")
        conn.commit()
        result["note"] = (
            "All 'similar' relationships removed (embedding space changed). Run scan_relationships() to recompute."
        )
        return json.dumps(result)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def deprecate_embed_space_tool(name: str) -> str:
    """Mark an embedding space as deprecated.

    Args:
        name: Name of the space to deprecate (cannot be the active space).
    """
    conn = _get_conn()
    try:
        result = deprecate_space(conn, name)
        return json.dumps(result)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def cleanup_embed_space_tool(name: str) -> str:
    """Drop a deprecated space's vec table and remove its registry entry.

    Args:
        name: Name of the deprecated space to clean up.
    """
    conn = _get_conn()
    try:
        result = cleanup_space(conn, name)
        return json.dumps(result)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def compare_spaces_tool(
    query: str,
    space_a: str,
    space_b: str,
    top_k: int = 10,
    mode: str = "vec",
) -> str:
    """Compare search results for a query across two embedding spaces.

    Returns side-by-side results with overlap metrics and rank correlation.

    Args:
        query: Search query to compare.
        space_a: Name of the first embedding space.
        space_b: Name of the second embedding space.
        top_k: Number of results per space (default 10).
        mode: Search mode - 'vec' (default for comparison), 'hybrid', or 'fts'.
    """
    conn = _get_conn()
    try:
        result = compare_spaces(conn, query, space_a, space_b, top_k, mode)
        return json.dumps(result)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def batch_compare_spaces_tool(
    space_a: str,
    space_b: str,
    queries: list[str],
    top_k: int = 10,
    mode: str = "vec",
) -> str:
    """Batch-compare two embedding spaces with multiple queries.

    Returns aggregated overlap, Jaccard, and rank correlation metrics.

    Args:
        space_a: Name of the first embedding space.
        space_b: Name of the second embedding space.
        queries: List of search queries to compare.
        top_k: Number of results per space per query (default 10).
        mode: Search mode (default 'vec').
    """
    conn = _get_conn()
    try:
        result = batch_compare_spaces(conn, space_a, space_b, queries, top_k, mode)
        return json.dumps(result)
    except ValueError as e:
        return json.dumps({"error": str(e)})


# --- Storage size helpers ---------------------------------------------------

_BYTES_PER_ELEMENT: dict[str, int] = {
    "float32": 4,
    "int8": 1,
}


@mcp.tool()
def benchmark_spaces_tool(
    baseline_space: str | None = None,
    sample_queries: int = 50,
    top_k: int = 10,
) -> str:
    """Benchmark all non-deprecated spaces against a baseline.

    Samples random chunk content as queries, runs batch_compare_spaces
    for each non-baseline space, and reports aggregated quality metrics
    plus storage estimates.

    Args:
        baseline_space: Name of the baseline space. Defaults to the active space.
        sample_queries: Number of random chunk texts to use as queries (default 50).
        top_k: Number of results per space per query (default 10).
    """
    conn = _get_conn()

    spaces = list_spaces(conn)
    if not spaces:
        return json.dumps({"error": "No embedding spaces found"})

    # Resolve baseline
    baseline_name: str
    if baseline_space is not None:
        baseline_name = baseline_space
    else:
        active = get_active_space(conn)
        if active is None:
            return json.dumps({"error": "No active space and no baseline specified"})
        baseline_name = active["name"]

    baseline = next((s for s in spaces if s["name"] == baseline_name), None)
    if baseline is None:
        return json.dumps({"error": f"Baseline space {baseline_name!r} not found"})

    # Sample queries from chunks
    rows = conn.execute(
        "SELECT content FROM chunks ORDER BY RANDOM() LIMIT ?",
        (sample_queries,),
    ).fetchall()
    queries = [r["content"] for r in rows]
    if not queries:
        return json.dumps({"error": "No chunks in database to sample queries from"})

    # Compare each non-deprecated, non-baseline space
    results = []
    for space in spaces:
        if space["name"] == baseline_name:
            continue
        if space["status"] == "deprecated":
            continue

        try:
            comparison = batch_compare_spaces(conn, baseline_name, space["name"], queries, top_k, mode="vec")
        except (ValueError, RuntimeError, ImportError, OSError) as e:
            results.append({"space": space["name"], "error": str(e)})
            continue

        # Storage estimate: bytes per vector = bytes_per_element * dim
        bpe = _BYTES_PER_ELEMENT.get(space.get("element_type", "float32"), 4)
        baseline_bpe = _BYTES_PER_ELEMENT.get(baseline.get("element_type", "float32"), 4)
        vec_bytes = bpe * space["dim"]
        baseline_vec_bytes = baseline_bpe * baseline["dim"]
        storage_ratio = vec_bytes / baseline_vec_bytes if baseline_vec_bytes else 1.0

        results.append(
            {
                "space": space["name"],
                "element_type": space.get("element_type", "float32"),
                "dim": space["dim"],
                "status": space["status"],
                "chunk_count": space.get("chunk_count", 0),
                "storage_ratio_vs_baseline": round(storage_ratio, 4),
                "metrics": {
                    "overlap_at_k": comparison["overlap_at_k"],
                    "jaccard": comparison["jaccard"],
                    "rank_correlation": comparison["rank_correlation"],
                },
                "warnings": comparison.get("warnings", []),
            }
        )

    return json.dumps(
        {
            "baseline": baseline_name,
            "baseline_element_type": baseline.get("element_type", "float32"),
            "queries_sampled": len(queries),
            "top_k": top_k,
            "comparisons": results,
        }
    )
