"""Route sub-modules for the knowledge-base MCP server."""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import TypeVar

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from .. import _conn
from ..exceptions import KnowledgeBaseError

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., object])

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


def _end_open_transaction() -> bool:
    """Roll back a transaction the calling thread's connection still holds; whether there was one."""
    conn = getattr(_conn._local, "conn", None)
    if conn is None or not conn.in_transaction:
        return False
    conn.rollback()
    return True


def tool(mcp: FastMCP) -> Callable[[F], F]:
    """Register a tool on *mcp* through the call boundary: every call ends with no transaction open (#597).

    A ``KnowledgeBaseError`` the tool lets through becomes a tool error carrying its message (#598), so no tool
    returns a failure as a result.

    The boundary runs on the tool's own thread, where its connection lives. A call that raises leaves no partial
    write for that thread's next commit and no write lock against other threads; a call that returns with a
    transaction open forgot to commit, which is logged and rolled back rather than stored by accident later.
    """

    def register(fn: F) -> F:
        @functools.wraps(fn)
        def call(*args: object, **kwargs: object) -> object:
            try:
                result = fn(*args, **kwargs)
            except KnowledgeBaseError as e:  # a domain refusal reads as its own text on both hubs (#543 D6, #598)
                _end_open_transaction()
                raise ToolError(str(e)) from e
            except BaseException:
                _end_open_transaction()
                raise
            if _end_open_transaction():
                logger.warning("tool %s returned with a transaction open; it was rolled back", fn.__name__)
            return result

        return mcp.tool()(call)  # type: ignore[return-value]

    return register
