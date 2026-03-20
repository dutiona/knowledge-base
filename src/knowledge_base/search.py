"""Hybrid search: FTS5 BM25 + sqlite-vec cosine similarity, merged via RRF."""

from __future__ import annotations

import posixpath
import sqlite3
import struct
from dataclasses import dataclass

from .db import (
    _batched_select,
    get_active_chunk_strategy,
    get_active_space,
    get_vec_table_name,
)
from .embed_swap import get_embed_config
from .embeddings import embed_single, truncate_embedding
from .keywords import build_fts_query, extract_keywords


@dataclass
class SearchResult:
    chunk_id: int
    content: str
    source_type: str
    source_uri: str
    chunk_index: int
    score: float
    match_type: str  # 'fts', 'vec', 'hybrid'


def _serialize_f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _fts_search(
    conn: sqlite3.Connection,
    query: str,
    limit: int,
    chunk_strategy: str | None = None,
) -> list[tuple[int, float]]:
    """BM25 full-text search. Returns (chunk_id, bm25_score) pairs.

    When *chunk_strategy* is given, only chunks matching that strategy are
    returned (requires the ``chunk_strategy`` column on ``chunks``).
    """
    if chunk_strategy is not None:
        rows = conn.execute(
            """
            SELECT c.id AS rowid, bm25(chunks_fts) AS score
            FROM chunks_fts
            JOIN chunks c ON c.id = chunks_fts.rowid
            WHERE chunks_fts MATCH ?
              AND c.chunk_strategy = ?
            ORDER BY score
            LIMIT ?
            """,
            (query, chunk_strategy, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT rowid, bm25(chunks_fts) AS score
            FROM chunks_fts
            WHERE chunks_fts MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
    return [(row["rowid"], row["score"]) for row in rows]


def _vec_search(
    conn: sqlite3.Connection, query_embedding: list[float], limit: int
) -> list[tuple[int, float]]:
    """Vector similarity search. Returns (chunk_id, distance) pairs."""
    vec_table = get_vec_table_name(conn)
    rows = conn.execute(
        f"""
        SELECT chunk_id, distance
        FROM {vec_table}
        WHERE embedding MATCH ?
        ORDER BY distance
        LIMIT ?
        """,
        (_serialize_f32(query_embedding), limit),
    ).fetchall()
    return [(row["chunk_id"], row["distance"]) for row in rows]


def _rrf_merge(
    fts_results: list[tuple[int, float]],
    vec_results: list[tuple[int, float]],
    k: int = 60,
) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion. Returns (chunk_id, rrf_score) sorted descending."""
    scores: dict[int, float] = {}

    for rank, (chunk_id, _) in enumerate(fts_results):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)

    for rank, (chunk_id, _) in enumerate(vec_results):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)

    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def _folder_boost(
    conn: sqlite3.Connection,
    query_embedding: list[float],
    chunk_ids: list[int],
    scores: dict[int, float],
    boost_factor: float = 1.15,
    top_folders: int = 5,
) -> dict[int, float]:
    """Apply a score multiplier to chunks from semantically relevant folders.

    Compares query_embedding against folder_summaries_vec embeddings.
    Chunks whose source_uri parent folder matches a top-scoring folder
    get their RRF score multiplied by boost_factor.

    Returns a new scores dict with boosted values.
    """
    if not chunk_ids:
        return scores

    # Check if folder_summaries_vec has any rows
    has_folders = conn.execute("SELECT 1 FROM folder_summaries_vec LIMIT 1").fetchone()
    if not has_folders:
        return scores

    # Find top matching folders
    folder_rows = conn.execute(
        """SELECT folder_path, distance
           FROM folder_summaries_vec
           WHERE embedding MATCH ?
           ORDER BY distance
           LIMIT ?""",
        (_serialize_f32(query_embedding), top_folders),
    ).fetchall()
    if not folder_rows:
        return scores

    # Use the top folder's distance as threshold — boost folders within 2x of best
    best_distance = folder_rows[0]["distance"]
    boosted_folders = set()
    for row in folder_rows:
        if best_distance == 0 or row["distance"] <= best_distance * 2:
            boosted_folders.add(row["folder_path"])

    if not boosted_folders:
        return scores

    # Look up source_uri for each candidate chunk (batched for safety)
    uri_rows = _batched_select(
        conn, "SELECT id, source_uri FROM chunks WHERE id IN ({ph})", chunk_ids
    )

    boosted = dict(scores)
    for row in uri_rows:
        chunk_id = row["id"]
        source_uri = row["source_uri"]
        # Extract parent folder from source_uri
        parent = posixpath.dirname(source_uri)
        if parent in boosted_folders and chunk_id in boosted:
            boosted[chunk_id] *= boost_factor

    return boosted


def search(
    conn: sqlite3.Connection,
    query: str,
    top_k: int = 10,
    source_type: str | None = None,
    mode: str = "hybrid",
    keyword_prefilter: bool = False,
    chunk_strategy: str | None = None,
) -> list[SearchResult]:
    """
    Hybrid search over indexed chunks.

    Args:
        query: Natural language search query.
        top_k: Number of results to return.
        source_type: Filter by source type (pdf, markdown, code, web, note).
        mode: 'hybrid' (default), 'fts' (keyword only), 'vec' (semantic only).
        keyword_prefilter: Extract intent keywords for FTS leg instead of
            using the raw query. Reduces noise from stopwords and context-
            specific filler. Only affects hybrid and fts modes.
        chunk_strategy: Filter by chunk strategy ('mechanical' or 'semantic').
            None (default) returns all chunks regardless of strategy.
    """
    fetch_limit = top_k * 3  # over-fetch for RRF merge

    # Default to the active space's chunk_strategy when caller doesn't specify.
    # This ensures FTS and vec results come from the same granularity.
    if chunk_strategy is None:
        chunk_strategy = get_active_chunk_strategy(conn)

    # When filtering by chunk_strategy, over-fetch to compensate for
    # candidates that will be filtered out before RRF merge.
    strategy_fetch_limit = fetch_limit * 5 if chunk_strategy else fetch_limit

    fts_results: list[tuple[int, float]] = []
    vec_results: list[tuple[int, float]] = []
    query_embedding: list[float] | None = None

    if mode in ("hybrid", "fts"):
        if keyword_prefilter:
            keywords = extract_keywords(query)
            fts_query = build_fts_query(keywords)
        else:
            fts_query = query
        if fts_query:
            try:
                fts_results = _fts_search(
                    conn, fts_query, strategy_fetch_limit, chunk_strategy=chunk_strategy
                )
            except Exception:
                # FTS query syntax error — skip FTS leg
                pass

    if mode in ("hybrid", "vec"):
        cfg = get_embed_config(conn)
        active = get_active_space(conn)
        matryoshka_base_dim = active.get("matryoshka_base_dim") if active else None
        if matryoshka_base_dim:
            query_embedding = embed_single(
                query,
                model=cfg["model"],
                expected_dim=matryoshka_base_dim,
                _provider_name=cfg["provider"],
            )
            query_embedding = truncate_embedding(query_embedding, cfg["dim"])
        else:
            query_embedding = embed_single(
                query,
                model=cfg["model"],
                expected_dim=cfg["dim"],
                _provider_name=cfg["provider"],
            )
        vec_results = _vec_search(conn, query_embedding, strategy_fetch_limit)

    # --- chunk_strategy filter (pre-RRF) ---
    if chunk_strategy:
        # Filter both result sets by joining against chunks table
        all_candidate_ids = list(
            {cid for cid, _ in fts_results} | {cid for cid, _ in vec_results}
        )
        if all_candidate_ids:
            placeholders = ",".join("?" * len(all_candidate_ids))
            valid_ids = {
                row["id"]
                for row in conn.execute(
                    f"SELECT id FROM chunks WHERE id IN ({placeholders}) AND chunk_strategy = ?",
                    [*all_candidate_ids, chunk_strategy],
                ).fetchall()
            }
            fts_results = [(cid, s) for cid, s in fts_results if cid in valid_ids]
            vec_results = [(cid, s) for cid, s in vec_results if cid in valid_ids]

    # Merge
    if mode == "hybrid" and fts_results and vec_results:
        merged = _rrf_merge(fts_results, vec_results)
        match_type = "hybrid"
    elif fts_results:
        merged = [
            (cid, 1.0 / (60 + rank + 1)) for rank, (cid, _) in enumerate(fts_results)
        ]
        match_type = "fts"
    elif vec_results:
        merged = [
            (cid, 1.0 / (60 + rank + 1)) for rank, (cid, _) in enumerate(vec_results)
        ]
        match_type = "vec"
    else:
        return []

    # --- Folder boost (#126) ---
    # Over-fetch slightly so folder boost can re-rank within a larger window
    boost_window = min(len(merged), top_k * 2)
    pre_boost_ids = [cid for cid, _ in merged[:boost_window]]
    score_map = dict(merged[:boost_window])

    if mode in ("hybrid", "vec") and query_embedding is not None:
        score_map = _folder_boost(conn, query_embedding, pre_boost_ids, score_map)

    # Re-sort after boost and take top_k
    ranked = sorted(score_map.items(), key=lambda x: x[1], reverse=True)
    chunk_ids = [cid for cid, _ in ranked[:top_k]]
    if not chunk_ids:
        return []

    # Fetch chunk details
    placeholders = ",".join("?" * len(chunk_ids))
    type_filter = ""
    params: list = list(chunk_ids)
    if source_type:
        type_filter = " AND source_type = ?"
        params.append(source_type)
    strategy_filter = ""
    if chunk_strategy:
        strategy_filter = " AND chunk_strategy = ?"
        params.append(chunk_strategy)

    rows = conn.execute(
        f"""
        SELECT id, content, source_type, source_uri, chunk_index
        FROM chunks
        WHERE id IN ({placeholders}){type_filter}{strategy_filter}
        """,
        params,
    ).fetchall()

    # Build lookup
    chunk_map = {row["id"]: row for row in rows}
    score_map = dict(ranked[:top_k])

    results = []
    for cid in chunk_ids:
        if cid not in chunk_map:
            continue
        row = chunk_map[cid]
        results.append(
            SearchResult(
                chunk_id=cid,
                content=row["content"],
                source_type=row["source_type"],
                source_uri=row["source_uri"],
                chunk_index=row["chunk_index"],
                score=score_map[cid],
                match_type=match_type,
            )
        )

    return results
