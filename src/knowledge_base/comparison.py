"""A/B comparison of embedding spaces."""

from __future__ import annotations

import sqlite3
from statistics import mean, stdev

from .embed_swap import get_space
from .search import search


def _spearman_rho(ranks_a: list[int], ranks_b: list[int]) -> float:
    """Compute Spearman's rank correlation coefficient."""
    n = len(ranks_a)
    if n < 2:
        return 0.0
    d_squared = sum((a - b) ** 2 for a, b in zip(ranks_a, ranks_b))
    return 1 - (6 * d_squared) / (n * (n**2 - 1))


def compare_spaces(
    conn: sqlite3.Connection,
    query: str,
    space_a: str,
    space_b: str,
    top_k: int = 10,
    mode: str = "vec",
) -> dict:
    """Compare search results for the same query across two embedding spaces."""
    info_a = get_space(conn, space_a)
    info_b = get_space(conn, space_b)

    results_a = search(conn, query, top_k=top_k, mode=mode, space_name=space_a)
    results_b = search(conn, query, top_k=top_k, mode=mode, space_name=space_b)

    ids_a = [r.chunk_id for r in results_a]
    ids_b = [r.chunk_id for r in results_b]
    set_a = set(ids_a)
    set_b = set(ids_b)
    common = set_a & set_b
    union = set_a | set_b

    # Overlap@K — denominator avoids understating on small result sets
    denom = min(top_k, len(results_a), len(results_b))
    if denom == 0:
        overlap_at_k = 1.0 if len(results_a) == 0 and len(results_b) == 0 else 0.0
    else:
        overlap_at_k = len(common) / denom

    # Jaccard
    jaccard = len(common) / len(union) if union else 0.0

    # Rank correlation (Spearman's rho on common results)
    rank_correlation = None
    if len(common) >= 5:
        rank_map_a = {cid: rank for rank, cid in enumerate(ids_a)}
        rank_map_b = {cid: rank for rank, cid in enumerate(ids_b)}
        common_ordered = sorted(common)
        ranks_a = [rank_map_a[cid] for cid in common_ordered]
        ranks_b = [rank_map_b[cid] for cid in common_ordered]
        rank_correlation = round(_spearman_rho(ranks_a, ranks_b), 4)

    # Warnings
    warnings = []
    if info_a["chunk_strategy"] != info_b["chunk_strategy"]:
        warnings.append(
            f"Cross-strategy comparison ({info_a['chunk_strategy']} vs "
            f"{info_b['chunk_strategy']}): metrics measure corpus overlap, "
            f"not embedding quality."
        )

    return {
        "query": query,
        "space_a": {
            "name": space_a,
            "model": info_a["model"],
            "dim": info_a["dim"],
            "result_count": len(results_a),
            "results": [
                {
                    "chunk_id": r.chunk_id,
                    "content": r.content[:200],
                    "score": round(r.score, 6),
                }
                for r in results_a
            ],
        },
        "space_b": {
            "name": space_b,
            "model": info_b["model"],
            "dim": info_b["dim"],
            "result_count": len(results_b),
            "results": [
                {
                    "chunk_id": r.chunk_id,
                    "content": r.content[:200],
                    "score": round(r.score, 6),
                }
                for r in results_b
            ],
        },
        "metrics": {
            "overlap_count": len(common),
            "overlap_at_k": round(overlap_at_k, 4),
            "jaccard": round(jaccard, 4),
            "rank_correlation": rank_correlation,
        },
        "warnings": warnings,
    }


def batch_compare_spaces(
    conn: sqlite3.Connection,
    space_a: str,
    space_b: str,
    queries: list[str],
    top_k: int = 10,
    mode: str = "vec",
) -> dict:
    """Run multiple queries against two spaces, return aggregated metrics."""
    overlaps = []
    jaccards = []
    correlations = []
    all_warnings: set[str] = set()

    for query in queries:
        result = compare_spaces(conn, query, space_a, space_b, top_k, mode)
        m = result["metrics"]
        overlaps.append(m["overlap_at_k"])
        jaccards.append(m["jaccard"])
        if m["rank_correlation"] is not None:
            correlations.append(m["rank_correlation"])
        all_warnings.update(result.get("warnings", []))

    def _stats(values: list[float]) -> dict:
        if not values:
            return {"mean": None, "std": None, "min": None, "max": None}
        return {
            "mean": round(mean(values), 4),
            "std": round(stdev(values), 4) if len(values) > 1 else 0.0,
            "min": round(min(values), 4),
            "max": round(max(values), 4),
        }

    return {
        "space_a": space_a,
        "space_b": space_b,
        "queries_analyzed": len(queries),
        "overlap_at_k": _stats(overlaps),
        "jaccard": _stats(jaccards),
        "rank_correlation": {
            **_stats(correlations),
            "valid_count": len(correlations),
        },
        "warnings": sorted(all_warnings),
    }
