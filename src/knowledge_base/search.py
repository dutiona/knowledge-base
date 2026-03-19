"""Hybrid search: FTS5 BM25 + sqlite-vec cosine similarity, merged via RRF."""

from __future__ import annotations

import sqlite3
import struct
from dataclasses import dataclass

from .embed_swap import get_embed_config
from .embeddings import embed_single
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
    conn: sqlite3.Connection, query: str, limit: int
) -> list[tuple[int, float]]:
    """BM25 full-text search. Returns (chunk_id, bm25_score) pairs."""
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
    rows = conn.execute(
        """
        SELECT chunk_id, distance
        FROM chunks_vec
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


def search(
    conn: sqlite3.Connection,
    query: str,
    top_k: int = 10,
    source_type: str | None = None,
    mode: str = "hybrid",
    keyword_prefilter: bool = False,
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
    """
    fetch_limit = top_k * 3  # over-fetch for RRF merge

    fts_results: list[tuple[int, float]] = []
    vec_results: list[tuple[int, float]] = []

    if mode in ("hybrid", "fts"):
        if keyword_prefilter:
            keywords = extract_keywords(query)
            fts_query = build_fts_query(keywords)
        else:
            fts_query = query
        if fts_query:
            try:
                fts_results = _fts_search(conn, fts_query, fetch_limit)
            except Exception:
                # FTS query syntax error — skip FTS leg
                pass

    if mode in ("hybrid", "vec"):
        cfg = get_embed_config(conn)
        query_embedding = embed_single(
            query,
            model=cfg["model"],
            expected_dim=cfg["dim"],
            _provider_name=cfg["provider"],
        )
        vec_results = _vec_search(conn, query_embedding, fetch_limit)

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

    # Fetch chunk details
    chunk_ids = [cid for cid, _ in merged[:top_k]]
    if not chunk_ids:
        return []

    placeholders = ",".join("?" * len(chunk_ids))
    type_filter = ""
    params: list = list(chunk_ids)
    if source_type:
        type_filter = " AND source_type = ?"
        params.append(source_type)

    rows = conn.execute(
        f"""
        SELECT id, content, source_type, source_uri, chunk_index
        FROM chunks
        WHERE id IN ({placeholders}){type_filter}
        """,
        params,
    ).fetchall()

    # Build lookup
    chunk_map = {row["id"]: row for row in rows}
    score_map = dict(merged[:top_k])

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
