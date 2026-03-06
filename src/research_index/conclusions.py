"""Evidence-chained conclusions with provenance tracking."""

from __future__ import annotations

import json
import sqlite3


def record_conclusion(
    conn: sqlite3.Connection,
    claim: str,
    confidence: float = 1.0,
    source_chunk_ids: list[int] | None = None,
    session_context: str | None = None,
) -> dict:
    """Record a conclusion with evidence links to source chunks."""
    chunk_ids = source_chunk_ids or []

    # Validate that all chunk IDs exist
    if chunk_ids:
        placeholders = ",".join("?" * len(chunk_ids))
        existing = conn.execute(
            f"SELECT id FROM chunks WHERE id IN ({placeholders})", chunk_ids
        ).fetchall()
        existing_ids = {row["id"] for row in existing}
        missing = [cid for cid in chunk_ids if cid not in existing_ids]
        if missing:
            return {"error": f"Chunk IDs not found: {missing}"}

    cursor = conn.execute(
        """INSERT INTO conclusions (claim, confidence, source_chunk_ids, session_context)
           VALUES (?, ?, ?, ?)""",
        (claim, confidence, json.dumps(chunk_ids), session_context),
    )
    conn.commit()
    return {"conclusion_id": cursor.lastrowid}


def get_conclusions(
    conn: sqlite3.Connection,
    keyword: str | None = None,
    min_confidence: float = 0.0,
    include_superseded: bool = False,
) -> list[dict]:
    """Search conclusions by keyword and confidence threshold."""
    conditions = ["confidence >= ?"]
    params: list = [min_confidence]

    if not include_superseded:
        conditions.append("superseded_by IS NULL")

    if keyword:
        conditions.append("claim LIKE ?")
        params.append(f"%{keyword}%")

    where = " AND ".join(conditions)
    rows = conn.execute(
        f"SELECT * FROM conclusions WHERE {where} ORDER BY created_at DESC",
        params,
    ).fetchall()

    results = []
    for row in rows:
        chunk_ids = json.loads(row["source_chunk_ids"])
        entry = {
            "id": row["id"],
            "claim": row["claim"],
            "confidence": row["confidence"],
            "source_chunk_ids": chunk_ids,
            "session_context": row["session_context"],
            "created_at": row["created_at"],
            "superseded_by": row["superseded_by"],
        }

        # Resolve source chunks
        if chunk_ids:
            placeholders = ",".join("?" * len(chunk_ids))
            chunks = conn.execute(
                f"SELECT id, content, source_uri FROM chunks WHERE id IN ({placeholders})",
                chunk_ids,
            ).fetchall()
            entry["source_chunks"] = [
                {"id": c["id"], "content": c["content"], "source_uri": c["source_uri"]}
                for c in chunks
            ]
        else:
            entry["source_chunks"] = []

        results.append(entry)
    return results


def supersede_conclusion(
    conn: sqlite3.Connection,
    old_conclusion_id: int,
    new_claim: str,
    confidence: float = 1.0,
    source_chunk_ids: list[int] | None = None,
    session_context: str | None = None,
) -> dict:
    """Supersede an old conclusion with a new one."""
    old = conn.execute(
        "SELECT id FROM conclusions WHERE id = ?", (old_conclusion_id,)
    ).fetchone()
    if not old:
        return {"error": f"Conclusion {old_conclusion_id} not found"}

    # Record the new conclusion
    result = record_conclusion(conn, new_claim, confidence, source_chunk_ids, session_context)
    if "error" in result:
        return result

    new_id = result["conclusion_id"]
    conn.execute(
        "UPDATE conclusions SET superseded_by = ? WHERE id = ?",
        (new_id, old_conclusion_id),
    )
    conn.commit()
    return {"old_conclusion_id": old_conclusion_id, "new_conclusion_id": new_id}


def get_conclusion_chain(
    conn: sqlite3.Connection,
    conclusion_id: int,
) -> list[dict]:
    """Follow the supersession chain for a conclusion (oldest first)."""
    # Walk backwards to find the root
    current_id = conclusion_id
    chain_ids = [current_id]

    # Find predecessors (conclusions that were superseded to reach this one)
    while True:
        prev = conn.execute(
            "SELECT id FROM conclusions WHERE superseded_by = ?", (current_id,)
        ).fetchone()
        if not prev:
            break
        current_id = prev["id"]
        chain_ids.insert(0, current_id)

    # Walk forward from conclusion_id
    current_id = conclusion_id
    while True:
        row = conn.execute(
            "SELECT superseded_by FROM conclusions WHERE id = ?", (current_id,)
        ).fetchone()
        if not row or not row["superseded_by"]:
            break
        current_id = row["superseded_by"]
        chain_ids.append(current_id)

    # Fetch all conclusions in the chain
    placeholders = ",".join("?" * len(chain_ids))
    rows = conn.execute(
        f"SELECT * FROM conclusions WHERE id IN ({placeholders})", chain_ids
    ).fetchall()

    # Maintain order
    row_map = {r["id"]: r for r in rows}
    return [
        {
            "id": row_map[cid]["id"],
            "claim": row_map[cid]["claim"],
            "confidence": row_map[cid]["confidence"],
            "source_chunk_ids": json.loads(row_map[cid]["source_chunk_ids"]),
            "session_context": row_map[cid]["session_context"],
            "created_at": row_map[cid]["created_at"],
            "superseded_by": row_map[cid]["superseded_by"],
        }
        for cid in chain_ids
        if cid in row_map
    ]
