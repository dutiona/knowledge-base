"""Route sub-modules for the knowledge-base MCP server."""

from __future__ import annotations

import httpx

#: The error a lost race on ``chunks.content_hash`` becomes: the winner stored the passage, so a retry skips it.
RACE = "another ingest stored some of the same passages meanwhile"


def embedder_failure(exc: httpx.HTTPError) -> tuple[str, bool]:
    """What an embedder failure says to a caller, and whether a retry can help.

    Never httpx's own text: it names the embedder's endpoint, which no caller may learn.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if 400 <= status < 500 and status != 429:
            return f"the embedding service refused the request (HTTP {status})", False
        return f"the embedding service failed (HTTP {status})", True
    return f"the embedding service failed ({type(exc).__name__})", True
