"""The lean HTTP surface (#543): what each surface serves, and the reference that documents both."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from knowledge_base import server

_ROOT = Path(__file__).resolve().parent.parent


def _names(hub) -> set[str]:
    return {tool.name for tool in asyncio.run(hub.list_tools())}


def test_the_reference_documents_every_stdio_tool():
    reference = (_ROOT / "docs/reference/mcp-tools.md").read_text()
    assert set(re.findall(r"^### (\S+)$", reference, re.MULTILINE)) == _names(server.mcp)
