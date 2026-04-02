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
    """
    conn = _get_conn()
    try:
        result = create_space(
            conn, name, model, dim, provider, chunk_strategy, matryoshka_base_dim
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
            "All 'similar' relationships removed (embedding space changed). "
            "Run scan_relationships() to recompute."
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
