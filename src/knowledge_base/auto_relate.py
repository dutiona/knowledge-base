"""Auto-relationship discovery via embedding similarity."""

from __future__ import annotations

import heapq
import sqlite3
from collections.abc import Callable

import numpy as np

from .db import get_vec_table_name
from .papers import add_relationship


def _get_paper_embeddings(conn: sqlite3.Connection, paper_id: int) -> list[sqlite3.Row]:
    """Return [(chunk_id, embedding_blob), ...] for a paper's non-figure chunks.

    Primary path: join through paper_paths to find chunks by source_uri.
    Fallback: if no paper_paths row exists (e.g. duplicate source_uri conflict),
    resolve source_uri via papers.abstract_chunk_id → chunks.source_uri.
    """
    vec_table = get_vec_table_name(conn)
    rows = conn.execute(
        f"SELECT cv.chunk_id, cv.embedding FROM [{vec_table}] cv"  # noqa: S608  # trusted internal identifier, not user input
        " JOIN chunks c ON c.id = cv.chunk_id"
        " JOIN paper_paths pp ON pp.path = c.source_uri"
        " WHERE pp.paper_id = ? AND c.source_type != 'figure'",
        (paper_id,),
    ).fetchall()
    if rows:
        return rows

    # Fallback: resolve source_uri via abstract_chunk_id
    uri_row = conn.execute(
        "SELECT source_uri FROM chunks WHERE id = (SELECT abstract_chunk_id FROM papers WHERE id = ?)",
        (paper_id,),
    ).fetchone()
    if not uri_row:
        return []
    return conn.execute(
        f"SELECT cv.chunk_id, cv.embedding FROM [{vec_table}] cv"  # noqa: S608  # trusted internal identifier, not user input
        " JOIN chunks c ON c.id = cv.chunk_id"
        " WHERE c.source_uri = ? AND c.source_type != 'figure'",
        (uri_row["source_uri"],),
    ).fetchall()


def auto_relate(
    conn: sqlite3.Connection,
    paper_id: int,
    on_progress: Callable[[str], object] | None = None,
    *,
    only_compare_higher: bool = False,
) -> dict:
    """Discover 'similar' relationships by comparing chunk embeddings."""
    _TOP_K = 3

    # Read thresholds from config
    propose_row = conn.execute("SELECT value FROM config WHERE key = 'auto_relate_propose_threshold'").fetchone()
    propose_threshold = float(propose_row["value"]) if propose_row else 0.82

    # Fetch source paper embeddings
    source_rows = _get_paper_embeddings(conn, paper_id)
    if not source_rows:
        return {"skipped": "no embeddings", "relationships_created": 0}

    source_chunk_ids = {row["chunk_id"] for row in source_rows}
    source_vecs = [(row["chunk_id"], np.frombuffer(bytes(row["embedding"]), dtype=np.float32)) for row in source_rows]
    # Pre-normalize source vectors
    source_normed = []
    for cid, vec in source_vecs:
        norm = np.linalg.norm(vec)
        if norm > 0:
            source_normed.append((cid, vec / norm))

    if not source_normed:
        return {"skipped": "no valid embeddings", "relationships_created": 0}

    # Fetch candidate paper IDs.  When only_compare_higher is set (full-scan
    # mode), restrict to id > paper_id so each pair is compared exactly once.
    query = "SELECT id FROM papers WHERE id > ?" if only_compare_higher else "SELECT id FROM papers WHERE id != ?"
    other_papers = conn.execute(query, (paper_id,)).fetchall()

    if not other_papers:
        return {"skipped": "no other papers", "relationships_created": 0}

    created = 0
    compared = 0
    skipped = 0

    for other_row in other_papers:
        other_id = other_row["id"]

        # Direction normalization: always source < target for "similar"
        lo, hi = min(paper_id, other_id), max(paper_id, other_id)

        # Fetch other paper embeddings
        other_rows = _get_paper_embeddings(conn, other_id)
        if not other_rows:
            skipped += 1
            continue

        # Skip papers that share chunks (e.g. duplicate source_uri registrations)
        other_chunk_ids = {row["chunk_id"] for row in other_rows}
        if source_chunk_ids & other_chunk_ids:
            skipped += 1
            continue

        other_vecs = []
        for row in other_rows:
            vec = np.frombuffer(bytes(row["embedding"]), dtype=np.float32)
            norm = np.linalg.norm(vec)
            if norm > 0:
                other_vecs.append((row["chunk_id"], vec / norm))

        if not other_vecs:
            skipped += 1
            continue

        compared += 1

        # Stream top-k via bounded heap — O(_TOP_K) memory, not O(n×m)
        top_k: list[tuple[float, int, int]] = []
        for s_cid, s_vec in source_normed:
            for o_cid, o_vec in other_vecs:
                sim = float(np.dot(s_vec, o_vec))
                if len(top_k) < _TOP_K:
                    heapq.heappush(top_k, (sim, s_cid, o_cid))
                elif sim > top_k[0][0]:
                    heapq.heapreplace(top_k, (sim, s_cid, o_cid))

        avg_score = sum(s for s, _, _ in top_k) / len(top_k)

        if avg_score < propose_threshold:
            # Below threshold: delete any stale "similar" edge from a
            # previous run with a lower threshold
            conn.execute(
                "DELETE FROM relationships WHERE relation_type = 'similar' "
                "AND source_paper_id = ? AND target_paper_id = ?",
                (lo, hi),
            )
            continue

        # Best-matching chunk for evidence (max of the heap)
        _best_sim, best_s_cid, _best_o_cid = max(top_k)
        evidence_chunk_id = best_s_cid

        # Upsert: creates new edge or updates confidence/evidence on re-run
        confidence = avg_score
        add_relationship(conn, lo, hi, "similar", confidence, evidence_chunk_id)
        created += 1

        if on_progress is not None:
            on_progress(f"Compared paper {paper_id} vs {other_id}: score={avg_score:.3f}")

    return {
        "relationships_created": created,
        "papers_compared": compared,
        "papers_skipped": skipped,
    }
