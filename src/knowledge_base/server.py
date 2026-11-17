"""FastMCP server exposing knowledge-base tools."""

from __future__ import annotations

import sys

import uvicorn
from fastmcp import FastMCP

from .auth import RequireMachine, TokenTable
from .db import open_declared_db
from .exceptions import KnowledgeBaseError

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


def build_http_app(tokens: TokenTable) -> RequireMachine:
    """What ``--http`` serves: the hub's stateless JSON streamable-HTTP app behind the machine-token gate.

    Every option is explicit, so no ``FASTMCP_*`` variable changes the routes or the session mode.
    """
    return RequireMachine(mcp.http_app(path="/mcp", stateless_http=True, json_response=True), tokens)


def uvicorn_config(app: RequireMachine, host: str, port: int) -> uvicorn.Config:
    """The uvicorn configuration ``--http`` and the test harness share."""
    return uvicorn.Config(
        app,
        host=host,
        port=port,
        lifespan="on",
        # Under "auto", an upgrade request becomes a websocket scope that ends in uvicorn's own 403;
        # with "none" it is a plain request that meets the gate's 401.
        ws="none",
        # uvicorn's dictConfig would replace the process's handlers, and its per-request warnings
        # (upgrade attempts, malformed requests) let any client write to the journal; errors still show.
        access_log=False,
        log_config=None,
        log_level="error",
        server_header=False,
        # timeout_graceful_shutdown stays unset (wait): a cut call's tool thread runs to its end anyway,
        # so a deadline would only drop its answer and invite a retried write.
    )


def main():
    # The startup gate (#569): serve only with an explicit embedding declaration the
    # database agrees with. 78 is EX_CONFIG, so a unit can set RestartPreventExitStatus=78
    # instead of restart-looping on a configuration error.
    try:
        open_declared_db().close()
    except KnowledgeBaseError as exc:
        print(f"knowledge-base: {exc}", file=sys.stderr)
        sys.exit(78)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
