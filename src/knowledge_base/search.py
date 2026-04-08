"""Hybrid search: FTS5 BM25 + sqlite-vec cosine similarity, merged via RRF."""

from __future__ import annotations

import logging
import posixpath
import sqlite3
from dataclasses import dataclass

from .db import (
    _batched_select,
    get_active_space,
    get_space_element_type,
    get_vec_table_name,
)
from .embed_swap import get_embed_config
from .embeddings import embed_single, truncate_embedding
from .exceptions import ValidationError
from .keywords import build_fts_query, extract_keywords
from .utils import ELEMENT_QUERY_EXPR, serialize_f32 as _serialize_f32

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    chunk_id: int
    content: str
    source_type: str
    source_uri: str
    chunk_index: int
    score: float
    match_type: str  # 'fts', 'vec', 'hybrid', 'reranked'


__all__ = [
    "SearchResult",
    "search",
]

# Default parameters for Reciprocal Rank Fusion merge
RRF_K = 60

# Folder-boost defaults: multiply scores for chunks in semantically relevant folders
FOLDER_BOOST_FACTOR = 1.15
FOLDER_BOOST_TOP_N = 5

# Over-fetch multiplier: fetch more candidates than top_k for RRF merge quality
SEARCH_OVERFETCH_MULTIPLIER = 3

# Strategy-filtered searches need even more over-fetching
STRATEGY_OVERFETCH_MULTIPLIER = 5

# Folder boost window: consider this many top results for re-ranking
BOOST_WINDOW_MULTIPLIER = 2

# Default search parameters
DEFAULT_TOP_K = 10
DEFAULT_RERANK_TOP_N = 20
MAX_TOP_K = 500

VALID_MODES = frozenset({"hybrid", "fts", "vec"})
VALID_SOURCE_TYPES = frozenset({"pdf", "markdown", "code", "web", "note", "figure"})
VALID_CHUNK_STRATEGIES = frozenset({"mechanical", "semantic"})


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
            "SELECT c.id AS rowid, bm25(chunks_fts) AS score"
            " FROM chunks_fts"
            " JOIN chunks c ON c.id = chunks_fts.rowid"
            " WHERE chunks_fts MATCH ? AND c.chunk_strategy = ?"
            " ORDER BY score LIMIT ?",
            (query, chunk_strategy, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT rowid, bm25(chunks_fts) AS score FROM chunks_fts"
            " WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?",
            (query, limit),
        ).fetchall()
    return [(row["rowid"], row["score"]) for row in rows]


def _vec_search(
    conn: sqlite3.Connection,
    query_embedding: list[float],
    limit: int,
    table_name: str | None = None,
) -> list[tuple[int, float]]:
    """Vector similarity search. Returns (chunk_id, distance) pairs."""
    vec_table = table_name or get_vec_table_name(conn)
    element_type = get_space_element_type(conn, vec_table)
    query_expr = ELEMENT_QUERY_EXPR[element_type]
    rows = conn.execute(
        f"SELECT chunk_id, distance FROM [{vec_table}]"
        f" WHERE embedding MATCH {query_expr} ORDER BY distance LIMIT ?",
        (_serialize_f32(query_embedding), limit),
    ).fetchall()
    return [(row["chunk_id"], row["distance"]) for row in rows]


def _rrf_merge(
    fts_results: list[tuple[int, float]],
    vec_results: list[tuple[int, float]],
    k: int = RRF_K,
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
    boost_factor: float = FOLDER_BOOST_FACTOR,
    top_folders: int = FOLDER_BOOST_TOP_N,
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
        "SELECT folder_path, distance FROM folder_summaries_vec"
        " WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
        (_serialize_f32(query_embedding), top_folders),
    ).fetchall()
    if not folder_rows:
        return scores

    # Use the top folder's distance as threshold — boost folders within 2x of best
    best_distance = folder_rows[0]["distance"]
    threshold = best_distance * 2 if best_distance > 0 else 1e-6
    boosted_folders = set()
    for row in folder_rows:
        if row["distance"] <= threshold:
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


def _fetch_chunk_contents(
    conn: sqlite3.Connection, chunk_ids: list[int]
) -> dict[int, str]:
    """Fetch content for chunk IDs. Returns {chunk_id: content} dict."""
    rows = _batched_select(
        conn, "SELECT id, content FROM chunks WHERE id IN ({ph})", chunk_ids
    )
    return {row["id"]: row["content"] for row in rows}


def search(
    conn: sqlite3.Connection,
    query: str,
    top_k: int = DEFAULT_TOP_K,
    source_type: str | None = None,
    mode: str = "hybrid",
    keyword_prefilter: bool = False,
    chunk_strategy: str | None = None,
    space_name: str | None = None,
    rerank: bool = False,
    rerank_top_n: int = DEFAULT_RERANK_TOP_N,
) -> list[SearchResult]:
    """
    Hybrid search over indexed chunks.

    Args:
        query: Natural language search query.
        top_k: Number of results to return.
        source_type: Filter by source type (pdf, markdown, code, web, note, figure).
        mode: 'hybrid' (default), 'fts' (keyword only), 'vec' (semantic only).
        keyword_prefilter: Extract intent keywords for FTS leg instead of
            using the raw query. Reduces noise from stopwords and context-
            specific filler. Only affects hybrid and fts modes.
        chunk_strategy: Filter by chunk strategy ('mechanical' or 'semantic').
            None (default) returns all chunks regardless of strategy.
        space_name: Search against a specific embedding space instead of the
            active one. The space's own model/dim/provider are used for query
            embedding. Folder boost is disabled for non-active spaces.
        rerank: When True, apply cross-encoder reranking to the top
            candidates before final selection.
        rerank_top_n: Number of candidates to feed into the reranker.
            Larger values improve recall at the cost of latency.

    Raises:
        ValidationError: If mode, source_type, or chunk_strategy are invalid,
            or top_k is not in [1, MAX_TOP_K].
    """
    if mode not in VALID_MODES:
        raise ValidationError(
            f"mode must be one of {sorted(VALID_MODES)}, got {mode!r}"
        )
    if top_k < 1 or top_k > MAX_TOP_K:
        raise ValidationError(f"top_k must be between 1 and {MAX_TOP_K}, got {top_k}")
    if source_type and source_type not in VALID_SOURCE_TYPES:
        raise ValidationError(
            f"source_type must be one of {sorted(VALID_SOURCE_TYPES)}, got {source_type!r}"
        )
    if chunk_strategy and chunk_strategy not in VALID_CHUNK_STRATEGIES:
        raise ValidationError(
            f"chunk_strategy must be one of {sorted(VALID_CHUNK_STRATEGIES)}, got {chunk_strategy!r}"
        )

    fetch_limit = top_k * SEARCH_OVERFETCH_MULTIPLIER

    # Resolve space configuration — either specific space or active
    if space_name:
        from .embed_swap import get_space

        space = get_space(conn, space_name)
        space_cfg = {
            "model": space["model"],
            "dim": space["dim"],
            "provider": space["provider"],
        }
        space_base_dim = space.get("matryoshka_base_dim")
        vec_table: str | None = space["table_name"]
        # Only skip folder boost if the space differs from the active one
        # (folder_summaries_vec is always at the active space's dim)
        active = get_active_space(conn)
        skip_folder_boost = not active or space["name"] != active["name"]
    else:
        space_cfg = get_embed_config(conn)
        active = get_active_space(conn)
        space_base_dim = active.get("matryoshka_base_dim") if active else None
        vec_table = None  # use active space default
        skip_folder_boost = False

    # Strategy filtering: only apply when the caller explicitly requests it.
    strategy_fetch_limit = (
        fetch_limit * STRATEGY_OVERFETCH_MULTIPLIER if chunk_strategy else fetch_limit
    )

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
            except sqlite3.OperationalError:
                # FTS query syntax error — skip FTS leg
                pass

    if mode in ("hybrid", "vec"):
        # Use space-specific config for query embedding
        if space_base_dim:
            query_embedding = embed_single(
                query,
                model=space_cfg["model"],
                expected_dim=space_base_dim,
                _provider_name=space_cfg["provider"],
            )
            if query_embedding is not None:
                query_embedding = truncate_embedding(query_embedding, space_cfg["dim"])
        else:
            query_embedding = embed_single(
                query,
                model=space_cfg["model"],
                expected_dim=space_cfg["dim"],
                _provider_name=space_cfg["provider"],
            )
        if query_embedding is not None:
            vec_results = _vec_search(
                conn, query_embedding, strategy_fetch_limit, table_name=vec_table
            )

    # --- chunk_strategy filter (pre-RRF) ---
    if chunk_strategy:
        # Filter both result sets by joining against chunks table (batched
        # to stay under SQLite's 999 variable limit for large candidate sets)
        all_candidate_ids = list(
            {cid for cid, _ in fts_results} | {cid for cid, _ in vec_results}
        )
        if all_candidate_ids:
            valid_rows = _batched_select(
                conn,
                "SELECT id FROM chunks WHERE id IN ({ph}) AND chunk_strategy = ?",
                all_candidate_ids,
                extra_params=[chunk_strategy],
            )
            valid_ids = {row["id"] for row in valid_rows}
            fts_results = [(cid, s) for cid, s in fts_results if cid in valid_ids]
            vec_results = [(cid, s) for cid, s in vec_results if cid in valid_ids]

    # Merge
    if mode == "hybrid" and fts_results and vec_results:
        merged = _rrf_merge(fts_results, vec_results)
        match_type = "hybrid"
    elif fts_results:
        merged = [
            (cid, 1.0 / (RRF_K + rank + 1)) for rank, (cid, _) in enumerate(fts_results)
        ]
        match_type = "fts"
    elif vec_results:
        merged = [
            (cid, 1.0 / (RRF_K + rank + 1)) for rank, (cid, _) in enumerate(vec_results)
        ]
        match_type = "vec"
    else:
        return []

    # --- Folder boost (#126) ---
    # Over-fetch slightly so folder boost can re-rank within a larger window
    boost_window = min(len(merged), top_k * BOOST_WINDOW_MULTIPLIER)
    pre_boost_ids = [cid for cid, _ in merged[:boost_window]]
    score_map = dict(merged[:boost_window])

    if (
        mode in ("hybrid", "vec")
        and query_embedding is not None
        and not skip_folder_boost
    ):
        score_map = _folder_boost(conn, query_embedding, pre_boost_ids, score_map)

    # Re-sort after boost and take top_k
    ranked = sorted(score_map.items(), key=lambda x: x[1], reverse=True)

    # --- Cross-encoder reranking (#106) ---
    reranked_ids: set[int] = set()
    if rerank:
        try:
            from .reranker import rerank as rerank_fn

            # Pre-filter rerank pool by source_type and chunk_strategy so
            # we don't waste reranker budget on candidates that will be
            # excluded in the final fetch.  Filter in Python using the
            # chunk metadata we already need to fetch for content anyway.
            pool_ids = [cid for cid, _ in ranked]
            if (source_type or chunk_strategy) and pool_ids:
                rows = _batched_select(
                    conn,
                    "SELECT id, source_type, chunk_strategy"
                    " FROM chunks WHERE id IN ({ph})",
                    pool_ids,
                )
                valid_ids: set[int] = set()
                for row in rows:
                    if source_type and row["source_type"] != source_type:
                        continue
                    if chunk_strategy and row["chunk_strategy"] != chunk_strategy:
                        continue
                    valid_ids.add(row["id"])
                rerank_candidates = [(cid, s) for cid, s in ranked if cid in valid_ids][
                    :rerank_top_n
                ]
            else:
                rerank_candidates = ranked[:rerank_top_n]

            rerank_cids = [cid for cid, _ in rerank_candidates]
            if rerank_cids:
                contents = _fetch_chunk_contents(conn, rerank_cids)
                fetchable_cids = [cid for cid in rerank_cids if cid in contents]

                if fetchable_cids:
                    texts = [contents[cid] for cid in fetchable_cids]
                    rerank_scores = rerank_fn(query, texts)
                    # Replace RRF scores with reranker scores
                    for cid, rs in zip(fetchable_cids, rerank_scores):
                        score_map[cid] = rs
                        reranked_ids.add(cid)

                # Re-sort with updated scores
                ranked = sorted(score_map.items(), key=lambda x: x[1], reverse=True)
        except (ImportError, RuntimeError, ValueError, OSError):
            # Graceful degradation: if reranker fails (missing deps,
            # bad model path, inference error), fall back to RRF ordering.
            logger.warning(
                "Reranker failed, falling back to RRF ordering", exc_info=True
            )

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
        f"SELECT id, content, source_type, source_uri, chunk_index"
        f" FROM chunks WHERE id IN ({placeholders}){type_filter}{strategy_filter}",
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
                match_type="reranked" if cid in reranked_ids else match_type,
            )
        )

    return results
