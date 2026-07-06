"""E0 PoC-2 — CSLS hub histogram (#495, gate box).

Quantifies hubness in the embedding space and whether CSLS
(csls(x,y) = 2 cos(x,y) - r_k(x) - r_k(y), Conneau & Lample et al. 2018)
flattens it. Hubness measure: N_k(x) = how many other points list x in their
top-k — its skewness is the standard hubness statistic; an absolute-cosine
cutoff (auto_relate's 0.82) is exactly what hubs game.

GATES (from #495 — enforced): corpus >= MIN_CHUNKS vectors (the N_k
distribution on a tiny corpus is noise). --allow-small produces an
exploratory report explicitly labeled non-verdict.

Read-only. Output: JSON report (histograms + skewness before/after CSLS).

Usage:
    uv run python utils/poc/poc2_csls_hub_histogram.py <db_path> [--space NAME]
        [--k 10] [--sample 20000] [--allow-small]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import struct
import sys
from pathlib import Path

import numpy as np

MIN_CHUNKS = 10_000
RNG_SEED = 20260706


def _open_ro(db_path: Path) -> sqlite3.Connection:
    """Read-only connection with the sqlite-vec extension loaded.

    vec0 virtual tables are unreadable without the module (mirrors
    db.py's connect()); loading an extension is per-connection state,
    not a DB write, so it coexists with mode=ro.
    """
    import sqlite_vec

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def _load_embeddings(conn: sqlite3.Connection, space: str | None) -> tuple[np.ndarray, str]:
    if space:
        row = conn.execute(
            "SELECT name, dim, table_name, element_type FROM embed_spaces WHERE name = ?", (space,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT name, dim, table_name, element_type FROM embed_spaces WHERE status = 'active'"
        ).fetchone()
    if row is None:
        sys.exit("no matching embed space")
    name, dim, table, element_type = row
    if element_type != "float32":
        sys.exit(f"space {name!r} has element_type={element_type!r}; only float32 spaces are supported here")
    blobs = conn.execute(f"SELECT embedding FROM {table}").fetchall()  # noqa: S608 — table name from embed_spaces registry
    if not blobs:
        sys.exit(f"space {name!r} has no vectors")
    return np.array([np.array(struct.unpack(f"{dim}f", b[0]), dtype=np.float32) for b in blobs]), name


def hub_stats(top_k_idx: np.ndarray, n: int) -> dict:
    """N_k occupancy distribution + skewness from a (n, k) neighbor-index matrix."""
    counts = np.bincount(top_k_idx.ravel(), minlength=n).astype(np.float64)
    mean, std = counts.mean(), counts.std()
    skew = float(((counts - mean) ** 3).mean() / (std**3 + 1e-12))
    hist, edges = np.histogram(counts, bins=[0, 1, 2, 5, 10, 20, 50, 100, np.inf])
    return {
        "nk_skewness": skew,
        "nk_max": int(counts.max()),
        "nk_zero_share": float((counts == 0).mean()),  # never-retrieved points
        "nk_histogram": {f"[{int(edges[i])},{edges[i + 1]})": int(hist[i]) for i in range(len(hist))},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("db_path", type=Path)
    ap.add_argument("--space", default=None)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--sample", type=int, default=20_000, help="subsample cap for the O(n^2) similarity matrix")
    ap.add_argument("--allow-small", action="store_true")
    args = ap.parse_args()

    # A non-positive --k silently corrupts results: k=0 makes r_k a NaN mean-of-empty,
    # and argpartition's negative-kth semantics return a wrong-shaped neighbor set with
    # no error. Reject both up front, along with a non-positive --sample.
    if args.k < 1:
        ap.error("--k must be >= 1")
    if args.sample < 1:
        ap.error("--sample must be >= 1")

    conn = _open_ro(args.db_path)
    mat, space_name = _load_embeddings(conn, args.space)

    if len(mat) < MIN_CHUNKS and not args.allow_small:
        sys.exit(
            f"GATE: corpus has {len(mat)} vectors < {MIN_CHUNKS} — N_k statistics on a tiny corpus "
            "are noise (#495 gate box). Grow via T0 (#528) or pass --allow-small for an "
            "exploratory, non-verdict report."
        )

    rng = np.random.default_rng(RNG_SEED)
    if len(mat) > args.sample:
        mat = mat[rng.choice(len(mat), args.sample, replace=False)]
    n = len(mat)
    if n <= args.k:
        sys.exit(f"corpus of {n} vectors cannot support k={args.k} neighborhoods (need n > k)")
    k = args.k

    unit = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-12)
    sim = unit @ unit.T  # n^2 float32 — the sample cap bounds this at ~1.6 GB
    np.fill_diagonal(sim, -np.inf)

    # Raw-cosine neighborhoods
    top_cos = np.argpartition(-sim, k, axis=1)[:, :k]
    # CSLS re-scoring: r_k = mean similarity to the k nearest neighbors
    r_k = np.take_along_axis(sim, top_cos, axis=1).mean(axis=1)
    # Reuse the sim buffer in place — sim is not needed once top_cos/r_k exist,
    # and a second n^2 allocation would double peak memory.
    sim *= 2
    sim -= r_k[None, :]
    sim -= r_k[:, None]
    csls = sim
    np.fill_diagonal(csls, -np.inf)
    top_csls = np.argpartition(-csls, k, axis=1)[:, :k]

    report = {
        "poc": "E0-PoC-2 CSLS hub histogram",
        "space": space_name,
        "gates": {"vectors_used": n, "chunks_gate_met": n >= MIN_CHUNKS, "k": k},
        "raw_cosine": hub_stats(top_cos, n),
        "csls": hub_stats(top_csls, n),
        "r_k": {"mean": float(r_k.mean()), "std": float(r_k.std()), "min": float(r_k.min()), "max": float(r_k.max())},
        "pass_axis": (
            "CSLS must reduce nk_skewness and nk_zero_share vs raw cosine; final verdict "
            "additionally requires recall@k non-degradation on the #250 golden set (owner+Fable checkpoint)."
        ),
    }
    # allow_nan=False: a degenerate stat must fail loudly, not emit NaN (invalid JSON
    # per RFC 8259) into a report that feeds the owner+Fable verdict.
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
