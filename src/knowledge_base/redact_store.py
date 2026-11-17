"""``knowledge-base-ingest redact``: find, and with the server stopped rewrite, stored rows that hold a secret (#592).

Rows stored before #592 were never redacted. The report reads them and names the rows a rewrite would change,
never their text. The rewrite stores them as ingest now would, merges rows that become identical, re-embeds
what changed, rebuilds the folder summaries and purges the old bytes from the file.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Iterator
from typing import Any

from . import migrate as _migrate
from .ingest import _WHOLE_URL, redact_metadata
from .redact import findings, scrub
from .utils import without_userinfo

#: Present while a ``--rewrite`` has committed its rows but not yet rebuilt the summaries and purged the file.
PENDING_KEY = "redact_rewrite_pending"


def _backups(conn: sqlite3.Connection) -> list[str]:
    db = _migrate._main_db_path(conn)
    if db is None or not (db.parent / "backups").is_dir():
        return []
    return sorted(p.name for p in (db.parent / "backups").glob("*.db"))


def _strings(value: Any) -> Iterator[str]:
    """The metadata strings the rules apply to: every string value but a whole URL, which only loses userinfo."""
    if isinstance(value, str):
        if not _WHOLE_URL.fullmatch(value):
            yield value
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)


def report(conn: sqlite3.Connection) -> dict:
    """What a rewrite would change: counts and chunk ids only, never stored text."""
    to_rewrite = Counter[str]()
    spans = Counter[str]()
    ids: list[int] = []
    scanned = 0
    for row in conn.execute("SELECT id, content, metadata, source_uri FROM chunks ORDER BY id"):
        scanned += 1
        dirty = False
        if scrub(row["content"]) != row["content"]:
            to_rewrite["content"] += 1
            spans.update(findings(row["content"]))
            dirty = True
        metadata = row["metadata"] or "{}"
        if redact_metadata(metadata) != metadata:
            to_rewrite["metadata"] += 1
            spans.update(rule for text in _strings(json.loads(metadata)) for rule in findings(text))
            dirty = True
        if without_userinfo(row["source_uri"]) != row["source_uri"]:
            to_rewrite["source_uri"] += 1
            dirty = True
        if dirty:
            ids.append(row["id"])
    summaries = sum(
        1 for (summary,) in conn.execute("SELECT summary FROM folder_summaries").fetchall() if scrub(summary) != summary
    )
    pending = conn.execute("SELECT 1 FROM config WHERE key = ?", (PENDING_KEY,)).fetchone() is not None
    return {
        "rows_scanned": scanned,
        "rows_to_rewrite": {k: to_rewrite[k] for k in ("content", "metadata", "source_uri")},
        "spans": dict(sorted(spans.items())),
        "chunk_ids": ids,
        "folder_summaries_to_rebuild": summaries,
        "rewrite_incomplete": pending,
        "backups_with_old_text": _backups(conn),
    }


def needs_rewrite(result: dict) -> bool:
    return bool(result["chunk_ids"] or result["folder_summaries_to_rebuild"] or result["rewrite_incomplete"])
