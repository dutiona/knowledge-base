"""Tests for the server hub: route mounting and the stdio entry point.

``server.py`` is a thin hub — its only logic is mounting the six route
sub-modules and exposing ``main()``. These tests guard that wiring: if a route
fails to mount (bad import, renamed ``mcp`` export, forgotten ``mcp.mount``
call), the tool set shrinks and the assertions below catch it.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from fastmcp import FastMCP

from knowledge_base import server

# The full tool surface, grouped by the route module that registers it. Keeping
# the groups explicit makes a dropped mount point obvious: a missing route shows
# up as exactly that route's block disappearing from the diff.
_INGESTION_TOOLS = {
    "ingest",
    "reingest",
    "ingest_url",
    "configure_chunking",
    "configure_browser_tool",
}
_SEARCH_TOOLS = {"search_index", "co_occurrence", "status"}
_EMBEDDING_TOOLS = {
    "embed_config",
    "re_embed_tool",
    "list_embed_spaces_tool",
    "create_embed_space_tool",
    "backfill_embed_space_tool",
    "promote_embed_space_tool",
    "deprecate_embed_space_tool",
    "cleanup_embed_space_tool",
    "compare_spaces_tool",
    "batch_compare_spaces_tool",
    "benchmark_spaces_tool",
}
_PAPER_TOOLS = {
    "register_paper_tool",
    "get_paper_tool",
    "add_relationship_tool",
    "get_relationships_tool",
    "record_conclusion_tool",
    "get_conclusions_tool",
    "supersede_conclusion_tool",
    "get_conclusion_chain_tool",
    "export_bibtex_tool",
    "sync_bibtex_tool",
    "suggest_relationships_tool",
    "scan_relationships",
    "relocate_paper_tool",
    "get_paper_paths_tool",
}
_EXTRACTION_TOOLS = {
    "record_method_tool",
    "record_dataset_tool",
    "record_metric_tool",
    "compare_papers_tool",
    "extract_structure_tool",
    "configure_llm_tool",
    "get_entities_tool",
    "extract_figures_tool",
    "configure_vision_tool",
    "configure_omniparser_tool",
}
_OPERATIONS_TOOLS = {
    "get_job_status_tool",
    "list_jobs_tool",
    "list_prediction_errors_tool",
    "resolve_prediction_error_tool",
}

_ALL_EXPECTED_TOOLS = (
    _INGESTION_TOOLS
    | _SEARCH_TOOLS
    | _EMBEDDING_TOOLS
    | _PAPER_TOOLS
    | _EXTRACTION_TOOLS
    | _OPERATIONS_TOOLS
)


def _registered_tool_names() -> set[str]:
    """Resolve the live tool names from the mounted FastMCP hub."""
    tools = asyncio.run(server.mcp.list_tools())
    return {t.name for t in tools}


def test_mcp_is_fastmcp_instance():
    assert isinstance(server.mcp, FastMCP)


def test_instructions_present_and_mentions_core_tools():
    """The hub advertises its core tools in the instructions string."""
    instructions = server.mcp.instructions
    assert instructions is not None and instructions.strip()
    for phrase in ("search", "ingest", "status"):
        assert phrase in instructions


def test_all_routes_are_mounted():
    """Every tool from all six route modules is exposed by the hub."""
    assert _registered_tool_names() == _ALL_EXPECTED_TOOLS


def test_tool_count_matches_route_inventory():
    """Sanity: the six route blocks sum to the registered tool count (no dupes)."""
    block_total = (
        len(_INGESTION_TOOLS)
        + len(_SEARCH_TOOLS)
        + len(_EMBEDDING_TOOLS)
        + len(_PAPER_TOOLS)
        + len(_EXTRACTION_TOOLS)
        + len(_OPERATIONS_TOOLS)
    )
    assert block_total == len(_ALL_EXPECTED_TOOLS)  # no name collisions across routes
    assert len(_registered_tool_names()) == len(_ALL_EXPECTED_TOOLS)


def test_each_route_block_is_fully_mounted():
    """Per-route assertion: pinpoints which route dropped if one fails to mount."""
    registered = _registered_tool_names()
    assert _INGESTION_TOOLS <= registered
    assert _SEARCH_TOOLS <= registered
    assert _EMBEDDING_TOOLS <= registered
    assert _PAPER_TOOLS <= registered
    assert _EXTRACTION_TOOLS <= registered
    assert _OPERATIONS_TOOLS <= registered


def test_main_runs_stdio_transport():
    """main() starts the server over the stdio transport."""
    with patch.object(server.mcp, "run") as mock_run:
        server.main()
    mock_run.assert_called_once_with(transport="stdio")
