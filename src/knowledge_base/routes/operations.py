"""Operations route: jobs and prediction errors."""

from __future__ import annotations

import json

from fastmcp import FastMCP

from .._conn import _get_conn
from ..exceptions import KnowledgeBaseError
from ..jobs import get_job, list_jobs as _list_jobs
from ..prediction_errors import (
    list_prediction_errors as _list_prediction_errors,
    resolve_prediction_error,
)

mcp = FastMCP("operations-routes")


@mcp.tool()
def get_job_status_tool(job_id: int) -> str:
    """Get the status and progress of a background extraction job.

    Args:
        job_id: Job ID returned by extract_structure_tool or extract_figures_tool.
    """
    conn = _get_conn()
    job = get_job(conn, job_id)
    if job is None:
        return json.dumps({"error": f"Job {job_id} not found"})
    return json.dumps(job)


@mcp.tool()
def list_jobs_tool(status: str | None = None, paper_id: int | None = None) -> str:
    """List background extraction jobs.

    Args:
        status: Filter by status: pending, running, completed, failed.
        paper_id: Filter by paper ID.
    """
    conn = _get_conn()
    return json.dumps(_list_jobs(conn, status=status, paper_id=paper_id))


@mcp.tool()
def list_prediction_errors_tool(
    since: str | None = None,
    unresolved_only: bool = True,
) -> str:
    """List prediction errors (queries with low-confidence or missing results).

    Prediction errors are logged automatically when search returns poor results.
    Use this to identify gaps in the knowledge base — queries that need better coverage.

    Args:
        since: ISO 8601 timestamp to filter errors after (e.g. '2025-01-01').
        unresolved_only: Only show unresolved errors (default true).
    """
    conn = _get_conn()
    return json.dumps(
        _list_prediction_errors(conn, since=since, unresolved_only=unresolved_only)
    )


@mcp.tool()
def resolve_prediction_error_tool(error_id: int) -> str:
    """Mark a prediction error as resolved (e.g. after ingesting content that fills the gap).

    Args:
        error_id: ID of the prediction error to resolve.
    """
    conn = _get_conn()
    try:
        return json.dumps(resolve_prediction_error(conn, error_id))
    except KnowledgeBaseError as e:
        err = {"error": str(e), **e.details}
        return json.dumps(err)
