"""FastMCP server exposing knowledge-base tools."""

from __future__ import annotations

from fastmcp import FastMCP

from .routes.embeddings import mcp as _embeddings
from .routes.extraction import mcp as _extraction
from .routes.ingestion import mcp as _ingestion
from .routes.operations import mcp as _operations
from .routes.papers import mcp as _papers
from .routes.search import mcp as _search

mcp = FastMCP(
    "knowledge-base",
    instructions=(
        "Hybrid semantic search over research papers, code, and notes. "
        "Use 'search' to find relevant content by concept or keyword. "
        "Use 'ingest' to add new files. Use 'status' for index statistics. "
        "Use 'register_paper_tool' and 'get_paper_tool' to manage paper metadata. "
        "Use 'add_relationship_tool' to create typed edges between papers. "
        "Use 'record_conclusion_tool' to record evidence-chained claims. "
        "Use 'export_bibtex_tool' to export papers for Typst bibliography. "
        "Use 'suggest_relationships_tool' to auto-detect citations."
    ),
)

mcp.mount(_ingestion)
mcp.mount(_search)
mcp.mount(_embeddings)
mcp.mount(_papers)
mcp.mount(_extraction)
mcp.mount(_operations)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
