"""Folder-level semantic embeddings for search context boosting.

Computes and stores per-folder summaries with embedding vectors.
Used at search time to boost results from semantically relevant directories.
"""

from __future__ import annotations

import hashlib
import sqlite3
import struct

from .embed_swap import get_embed_config
from .embeddings import embed


def _serialize_f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def compute_folder_hash(conn: sqlite3.Connection, folder_path: str) -> str:
    """Compute a content hash for a folder from its chunks' content hashes.

    The hash is derived from the sorted set of content_hash values for
    all chunks whose source_uri starts with the folder path. If no
    documents changed, the hash stays the same.  Returns empty string
    when the folder contains no indexed chunks.
    """
    prefix = folder_path.rstrip("/") + "/"
    rows = conn.execute(
        """SELECT DISTINCT content_hash FROM chunks
           WHERE source_uri LIKE ? || '%'
             AND source_uri NOT LIKE ? || '%/%'
           ORDER BY content_hash""",
        (prefix, prefix),
    ).fetchall()
    if not rows:
        return ""
    combined = "|".join(row["content_hash"] for row in rows)
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


def _build_folder_summary(conn: sqlite3.Connection, folder_path: str) -> str:
    """Build a summary string for a folder from its documents.

    Concatenates the first chunk of each unique source_uri in the folder
    (truncated to 200 chars each), separated by newlines.
    """
    prefix = folder_path.rstrip("/") + "/"
    rows = conn.execute(
        """SELECT source_uri, content FROM chunks
           WHERE source_uri LIKE ? || '%'
             AND source_uri NOT LIKE ? || '%/%'
             AND chunk_index = 0
           ORDER BY source_uri""",
        (prefix, prefix),
    ).fetchall()
    parts = []
    for row in rows:
        filename = row["source_uri"].rsplit("/", 1)[-1]
        snippet = row["content"][:200].replace("\n", " ").strip()
        parts.append(f"{filename}: {snippet}")
    return "\n".join(parts)


def update_folder_summary(
    conn: sqlite3.Connection,
    folder_path: str,
) -> bool:
    """Recompute folder summary and embedding if content changed.

    Returns True if the summary was created or updated, False if skipped
    (content unchanged or folder empty).
    """
    folder_path = folder_path.rstrip("/")
    current_hash = compute_folder_hash(conn, folder_path)

    if not current_hash:
        # No chunks in this folder — clean up stale entry if present
        conn.execute(
            "DELETE FROM folder_summaries WHERE folder_path = ?", (folder_path,)
        )
        conn.execute(
            "DELETE FROM folder_summaries_vec WHERE folder_path = ?",
            (folder_path,),
        )
        conn.commit()
        return False

    # Check for staleness
    existing = conn.execute(
        "SELECT content_hash FROM folder_summaries WHERE folder_path = ?",
        (folder_path,),
    ).fetchone()
    if existing and existing["content_hash"] == current_hash:
        return False

    # Build summary and embed
    summary = _build_folder_summary(conn, folder_path)
    if not summary:
        return False

    cfg = get_embed_config(conn)
    embedding = embed([summary], model=cfg["model"], expected_dim=cfg["dim"])[0]

    # Upsert folder_summaries
    conn.execute(
        """INSERT INTO folder_summaries (folder_path, summary, content_hash, updated_at)
           VALUES (?, ?, ?, datetime('now'))
           ON CONFLICT(folder_path) DO UPDATE SET
               summary = excluded.summary,
               content_hash = excluded.content_hash,
               updated_at = excluded.updated_at""",
        (folder_path, summary, current_hash),
    )

    # Upsert folder_summaries_vec (delete + insert since vec0 has no ON CONFLICT)
    conn.execute(
        "DELETE FROM folder_summaries_vec WHERE folder_path = ?",
        (folder_path,),
    )
    conn.execute(
        "INSERT INTO folder_summaries_vec (embedding, folder_path) VALUES (?, ?)",
        (_serialize_f32(embedding), folder_path),
    )

    conn.commit()
    return True
