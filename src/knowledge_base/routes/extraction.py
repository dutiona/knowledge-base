"""Extraction route: LLM extraction, vision, entity recording."""

from __future__ import annotations

import json

from fastmcp import FastMCP

from .._conn import _get_conn
from ..exceptions import ExtractionError, KnowledgeBaseError
from ..extraction import (
    _MAX_WORKERS_LIMIT,
    compare_papers,
    estimate_extraction_time,
    extract_structure,
    get_entities,
    record_dataset,
    record_method,
    record_metric,
)
from ..jobs import submit_job
from ..llm import configure_llm
from ..vision import configure_omniparser, configure_vision, estimate_figures_time

mcp = FastMCP("extraction-routes")


@mcp.tool()
def record_method_tool(
    name: str,
    paper_id: int,
    description: str | None = None,
) -> str:
    """Record a method used in a paper.

    Args:
        name: Method name (e.g. 'Transformer', 'ResNet-50').
        paper_id: Paper that uses this method.
        description: Brief description of the method.
    """
    conn = _get_conn()
    return json.dumps(record_method(conn, name, paper_id, description))


@mcp.tool()
def record_dataset_tool(
    name: str,
    paper_id: int,
    description: str | None = None,
) -> str:
    """Record a dataset used in a paper.

    Args:
        name: Dataset name (e.g. 'ImageNet', 'GLUE').
        paper_id: Paper that uses this dataset.
        description: Brief description of the dataset.
    """
    conn = _get_conn()
    return json.dumps(record_dataset(conn, name, paper_id, description))


@mcp.tool()
def record_metric_tool(
    name: str,
    value: float,
    paper_id: int,
    method_id: int | None = None,
    dataset_id: int | None = None,
    unit: str | None = None,
) -> str:
    """Record a metric value from a paper.

    Args:
        name: Metric name (e.g. 'accuracy', 'F1', 'BLEU').
        value: Numeric value of the metric.
        paper_id: Paper reporting this metric.
        method_id: Method that achieved this metric.
        dataset_id: Dataset the metric was measured on.
        unit: Unit of measurement (e.g. '%', 'ms').
    """
    conn = _get_conn()
    return json.dumps(record_metric(conn, name, value, paper_id, method_id, dataset_id, unit))


@mcp.tool()
def compare_papers_tool(paper_ids: list[int]) -> str:
    """Compare metrics across papers on shared datasets.

    Shows side-by-side results for papers that report metrics on the same datasets.

    Args:
        paper_ids: List of 2+ paper IDs to compare.
    """
    conn = _get_conn()
    return json.dumps(compare_papers(conn, paper_ids))


@mcp.tool()
def extract_structure_tool(
    paper_id: int,
    confirmed: bool = False,
    max_workers: int = 1,
) -> str:
    """Extract methods, datasets, and metrics from a paper using LLM.

    For short papers, runs inline. For long papers (>2min estimated),
    returns a warning with ETA — call again with confirmed=True to queue
    a background job. Use get_job_status(job_id) to poll progress.

    Args:
        paper_id: Paper ID to extract structure from.
        confirmed: Set True to skip the ETA warning for long documents.
        max_workers: Number of concurrent LLM calls for the map phase (default 1).
            Increase to match your LLM server's parallel capacity.
    """
    conn = _get_conn()
    try:
        est = estimate_extraction_time(conn, paper_id)
    except KnowledgeBaseError as e:
        err = {"error": str(e), **e.details}
        return json.dumps(err)

    # Short doc: run inline (fast path) — reuse chunks from estimate
    if not est["is_long"]:
        try:
            return json.dumps(
                extract_structure(
                    conn,
                    paper_id,
                    confirmed=True,
                    max_workers=max_workers,
                    _prefetched_chunks=est["chunks"],
                )
            )
        except ExtractionError as e:
            result: dict[str, object] = {"error": str(e)}
            if e.errors:
                result["errors"] = e.errors
            if e.raw:
                result["raw"] = e.raw
            return json.dumps(result)

    # Long doc, not confirmed: ETA warning (adjust for parallelism)
    effective_workers = min(max(max_workers, 1), est["chunk_count"], _MAX_WORKERS_LIMIT)
    wall_estimate = est["estimated_seconds"] // effective_workers
    if wall_estimate > 120 and not confirmed:
        return json.dumps(
            {
                "warning": (
                    f"Extraction will take ~{wall_estimate // 60}min "
                    f"for {est['chunk_count']} chunks"
                    + (f" ({effective_workers} workers)" if effective_workers > 1 else "")
                ),
                "estimated_seconds": wall_estimate,
                "chunk_count": est["chunk_count"],
                "max_workers": effective_workers,
                "confirm_required": True,
            }
        )

    # Long doc, confirmed: submit background job
    # Normalize max_workers to effective value so dedup key is stable
    # (e.g., max_workers=10 and max_workers=20 on a 3-chunk paper both clamp to 3)
    params = {"max_workers": effective_workers} if effective_workers > 1 else None
    job_id = submit_job(conn, paper_id, "extract_structure", params=params)
    return json.dumps(
        {
            "deferred": True,
            "job_id": job_id,
            "status": "pending",
            "message": "Use get_job_status(job_id) to poll progress.",
        }
    )


@mcp.tool()
def configure_llm_tool(
    provider: str = "ollama",
    base_url: str | None = None,
    model: str = "qwen3.5:27b",
    api_key: str | None = None,
) -> str:
    """Configure the LLM used for structured extraction.

    Args:
        provider: 'ollama' (native API) or 'openai_compat' (OpenAI-compatible API).
        base_url: Base URL (e.g. 'http://192.168.1.41:1234'). Required for openai_compat.
        model: Model name (e.g. 'qwen3.5:27b', 'qwen/qwen3.5-35b-a3b').
        api_key: Optional API key for authenticated endpoints.
    """
    conn = _get_conn()
    try:
        return json.dumps(configure_llm(conn, provider, base_url, model, api_key))
    except KnowledgeBaseError as e:
        err = {"error": str(e), **e.details}
        return json.dumps(err)


@mcp.tool()
def get_entities_tool(paper_id: int) -> str:
    """List resolved entities for a paper with their surface forms and chunk mentions.

    Args:
        paper_id: Paper ID to get entities for.
    """
    conn = _get_conn()
    return json.dumps(get_entities(conn, paper_id))


@mcp.tool()
def extract_figures_tool(paper_id: int, pages: list[int] | None = None, confirmed: bool = False) -> str:
    """Extract figures from a paper's PDF using a vision model.

    Renders candidate pages as images, sends them to the configured vision model,
    and stores figure descriptions as searchable 'figure' chunks.

    Always queues a background job (figure extraction involves PDF rendering +
    vision API calls). Returns an ETA warning first; call with confirmed=True
    to submit the job. Use get_job_status(job_id) to poll progress.

    Args:
        paper_id: Paper ID.
        pages: 1-based page numbers to process (optional, auto-detects if omitted).
        confirmed: Skip ETA warning for long documents.
    """
    conn = _get_conn()
    # Convert 1-based (user-facing) to 0-based (internal)
    pages_0 = None
    if pages is not None:
        invalid = [p for p in pages if p <= 0]
        if invalid:
            return json.dumps({"error": f"Pages must be >= 1 (got {invalid})"})
        pages_0 = [p - 1 for p in pages]

    try:
        est = estimate_figures_time(conn, paper_id, pages=pages_0)
    except KnowledgeBaseError as e:
        err = {"error": str(e), **e.details}
        return json.dumps(err)

    # ETA gate
    if est["estimated_seconds"] > 120 and not confirmed:
        return json.dumps(
            {
                "confirm_required": True,
                "estimated_seconds": est["estimated_seconds"],
                "extracted_images": est["extracted_images"],
                "vector_pages": est["vector_pages"],
            }
        )

    # Submit background job
    job_id = submit_job(conn, paper_id, "extract_figures", {"pages": pages_0})
    return json.dumps(
        {
            "deferred": True,
            "job_id": job_id,
            "status": "pending",
            "message": "Use get_job_status(job_id) to poll progress.",
        }
    )


@mcp.tool()
def configure_vision_tool(
    model: str | None = None,
    base_url: str | None = None,
) -> str:
    """Configure the vision model used for figure extraction.

    Args:
        model: Vision model name (e.g. 'gemma3:27b', 'llava:13b').
        base_url: Base URL for the vision API (e.g. 'http://localhost:11434').
    """
    conn = _get_conn()
    return json.dumps(configure_vision(conn, model=model, base_url=base_url))


@mcp.tool()
def configure_omniparser_tool(path: str | None = None, server_url: str | None = None) -> str:
    """Configure OmniParser for figure enrichment.

    OmniParser adds OCR text and icon detection to figure descriptions.
    Requires a local OmniParser installation with its own venv.

    Args:
        path: Absolute path to OmniParser directory (None to query, empty string to disable).
        server_url: Optional HTTP server URL for persistent OmniParser server mode.
            None to leave unchanged, empty string to clear (reverts to auto-start),
            or a URL like "http://gpu-node:7862" for a remote server.
    """
    conn = _get_conn()
    try:
        return json.dumps(configure_omniparser(conn, path, server_url=server_url))
    except KnowledgeBaseError as e:
        err = {"error": str(e), **e.details}
        return json.dumps(err)
