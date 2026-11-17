"""``knowledge-base-ingest redact``: find, and with the server stopped rewrite, stored rows that hold a secret (#592).

Rows stored before #592 were never redacted. The report reads them and names the rows a rewrite would change,
never their text. The rewrite stores them as ingest now would, merges rows that become identical, re-embeds
what changed, rebuilds the folder summaries and purges the old bytes from the file.
"""

from __future__ import annotations

import json
import posixpath
import sqlite3
from collections import Counter
from collections.abc import Iterator
from itertools import chain
from typing import Any

from . import migrate as _migrate
from .db import (
    _batched_select,
    delete_chunk_vecs,
    get_vec_table_name,
    held_exclusively,
    insert_chunk_vec,
    purge_deleted_content,
)
from .folder_summaries import update_folder_summary
from .chunking import CHUNK_OVERLAP
from .ingest import _WHOLE_URL, _embed_with_config, redact_metadata
from .redact import findings, redact_in_context, scrub
from .utils import content_hash, without_userinfo

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


#: How much of a neighbouring chunk is read as a chunk's context: past any secret a rule matches.
_CONTEXT = 4096


def _contents(conn: sqlite3.Connection) -> dict[int, tuple[str, list[str]]]:
    """Each chunk whose content a rewrite changes: its new content, and the rules that change it.

    Before #592 the fixed-window chunker cut a secret where a window ended, so one row can hold a fragment no rule
    matches alone. A row is read between its neighbours when they overlap it as that chunker's windows do, which
    gives the rules the whole secret.
    """
    changed: dict[int, tuple[str, list[str]]] = {}

    def settle(row: sqlite3.Row, before: str, after: str) -> None:
        edged, rules = redact_in_context(row["content"], before, after)
        content = scrub(edged)
        if content != row["content"]:
            changed[row["id"]] = (content, rules + findings(edged))

    def windowed(a: sqlite3.Row, b: sqlite3.Row) -> bool:
        return (
            a["source_uri"] == b["source_uri"]
            and a["chunk_index"] + 1 == b["chunk_index"]
            and len(a["content"]) > CHUNK_OVERLAP
            and a["content"][-CHUNK_OVERLAP:] == b["content"][:CHUNK_OVERLAP]
        )

    rows = conn.execute("SELECT id, content, source_uri, chunk_index FROM chunks ORDER BY source_uri, chunk_index, id")
    prev = cur = None
    for nxt in chain(rows, [None]):
        if cur is not None:
            before = prev["content"][:-CHUNK_OVERLAP][-_CONTEXT:] if prev is not None and windowed(prev, cur) else ""
            after = nxt["content"][CHUNK_OVERLAP:][:_CONTEXT] if nxt is not None and windowed(cur, nxt) else ""
            settle(cur, before, after)
        prev, cur = cur, nxt
    return changed


def report(conn: sqlite3.Connection) -> dict:
    """What a rewrite would change: counts and chunk ids only, never stored text."""
    to_rewrite = Counter[str]()
    spans = Counter[str]()
    ids: list[int] = []
    scanned = 0
    contents = _contents(conn)
    for row in conn.execute("SELECT id, metadata, source_uri FROM chunks ORDER BY id"):
        scanned += 1
        dirty = False
        if row["id"] in contents:
            to_rewrite["content"] += 1
            spans.update(contents[row["id"]][1])
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


def _merge_into(conn: sqlite3.Connection, dup: int, keep: int) -> None:
    """Move every reference from chunk ``dup`` to ``keep``, then delete ``dup``: ingest-time dedup's end state."""
    refs = conn.execute(
        'SELECT m.name AS t, p."from" AS c FROM sqlite_master AS m, pragma_foreign_key_list(m.name) AS p '
        "WHERE m.type = 'table' AND p.\"table\" = 'chunks'"
    ).fetchall()
    for table, column in refs:
        conn.execute(f"UPDATE OR IGNORE [{table}] SET [{column}] = ? WHERE [{column}] = ?", (keep, dup))  # noqa: S608  # names from sqlite_master
        conn.execute(f"DELETE FROM [{table}] WHERE [{column}] = ?", (dup,))  # noqa: S608  # rows a UNIQUE constraint held back
    for row in conn.execute(
        "SELECT DISTINCT c.id, c.source_chunk_ids FROM conclusions c, json_each(c.source_chunk_ids) j"
        " WHERE j.value = ?",
        (dup,),
    ).fetchall():
        moved = [keep if cid == dup else cid for cid in json.loads(row["source_chunk_ids"])]
        conn.execute(
            "UPDATE conclusions SET source_chunk_ids = ? WHERE id = ?",
            (json.dumps(list(dict.fromkeys(moved))), row["id"]),
        )
    conn.execute("DELETE FROM chunks WHERE id = ?", (dup,))


def _plan(conn: sqlite3.Connection) -> tuple[list[dict], list[tuple[int, int]]]:
    """Each row's new values, in id order (``content`` None when it stays); a row whose new hash another row holds
    merges into that row."""
    contents = _contents(conn)
    owner = dict(conn.execute("SELECT content_hash, id FROM chunks").fetchall())
    changes: list[dict] = []
    merges: list[tuple[int, int]] = []
    for r in conn.execute("SELECT id, content_hash, metadata, source_uri FROM chunks ORDER BY id"):
        content = contents.get(r["id"], (None,))[0]
        metadata = redact_metadata(r["metadata"]) if r["metadata"] is not None else None
        uri = without_userinfo(r["source_uri"])
        if (content, metadata, uri) == (None, r["metadata"], r["source_uri"]):
            continue
        new_hash = r["content_hash"]
        if content is not None:
            new_hash = content_hash(content)
            del owner[r["content_hash"]]
            if new_hash in owner:
                merges.append((r["id"], owner[new_hash]))
                continue
            owner[new_hash] = r["id"]
        changes.append(
            {
                "id": r["id"],
                "content": content,
                "hash": new_hash,
                "metadata": metadata,
                "uri": uri,
                "reembed": content is not None,
            }
        )
    return changes, merges


def _apply(conn: sqlite3.Connection, changes: list[dict], merges: list[tuple[int, int]], embedded: dict) -> dict:
    """One transaction: rows, merges, vectors in every space, stale summaries, and the pending marker."""
    tables = [r[0] for r in conn.execute("SELECT table_name FROM embed_spaces").fetchall()]
    active = get_vec_table_name(conn)
    dropped = [c["id"] for c in changes if c["reembed"]] + [dup for dup, _ in merges]
    removed: dict[str, int] = {}
    conn.execute("BEGIN IMMEDIATE")
    try:
        for table in tables:
            removed[table] = sum(
                r[0]
                for r in _batched_select(conn, f"SELECT COUNT(*) FROM [{table}] WHERE chunk_id IN ({{ph}})", dropped)  # noqa: S608
            )
            delete_chunk_vecs(conn, dropped, table_name=table)
        for dup, keep in merges:
            _merge_into(conn, dup, keep)
        for c in changes:
            conn.execute(
                "UPDATE chunks SET content = coalesce(?, content), content_hash = ?, metadata = ?, source_uri = ?"
                " WHERE id = ?",
                (c["content"], c["hash"], c["metadata"], c["uri"], c["id"]),
            )
            if c["reembed"] and embedded.get(c["id"]) is not None:
                insert_chunk_vec(conn, c["id"], embedded[c["id"]], table_name=active)
        stale = [r[0] for r in conn.execute("SELECT folder_path, summary FROM folder_summaries") if scrub(r[1]) != r[1]]
        for path in stale:
            conn.execute("DELETE FROM folder_summaries WHERE folder_path = ?", (path,))
            conn.execute("DELETE FROM folder_summaries_vec WHERE folder_path = ?", (path,))
        conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, '1')", (PENDING_KEY,))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return removed


def _rebuild_summaries(conn: sqlite3.Connection) -> int:
    """Rebuild every file folder's summary that is missing or stale; each one commits on its own."""
    folders = {
        posixpath.dirname(u)
        for (u,) in conn.execute("SELECT DISTINCT source_uri FROM chunks WHERE source_uri LIKE '/%'")
    }
    folders |= {p for (p,) in conn.execute("SELECT folder_path FROM folder_summaries")}
    return sum(update_folder_summary(conn, folder) for folder in sorted(folders))


def rewrite(conn: sqlite3.Connection, batch_size: int = 32) -> dict:
    """Store every row as ingest now would, with the server stopped; a rerun finishes an interrupted run."""
    with held_exclusively(conn):
        changes, merges = _plan(conn)
        reembed = [c for c in changes if c["reembed"]]
        embedded: dict[int, list[float] | None] = {}
        for i in range(0, len(reembed), batch_size):  # every vector before the first write
            batch = reembed[i : i + batch_size]
            embedded.update(
                zip((c["id"] for c in batch), _embed_with_config(conn, [c["content"] for c in batch]), strict=True)
            )
        removed = _apply(conn, changes, merges, embedded)
        rebuilt = _rebuild_summaries(conn)
        purge_deleted_content(conn)
        conn.execute("DELETE FROM config WHERE key = ?", (PENDING_KEY,))
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    result = report(conn)
    result.update(rewritten=len(changes), merged=len(merges), vectors_removed=removed, folders_rebuilt=rebuilt)
    return result
