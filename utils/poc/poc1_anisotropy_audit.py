"""E0 PoC-1 — anisotropy audit + before/after ABT sweep (#495, gate box).

Measures how anisotropic the active embedding space is (Ethayarajh 2019:
random-pair cosine should be ~0 in an isotropic space) and whether
mean-centering + All-but-the-Top (removing the top-p principal components)
restores isotropy. Optionally scores recall@k on the #250 golden set before
and after, which is the actual pass/fail axis.

GATES (from #495 — enforced, not advisory):
- corpus >= MIN_CHUNKS chunks before a full-whitening verdict (gamma = d/N);
  smaller corpora may run with --allow-small for the ABT-only sweep.
- recall@k comparison requires the golden set (tests/memarchbench/fixtures/
  golden_queries.jsonl with >= 100 judged queries); without it the script
  reports geometry only and says so loudly.

Read-only against the DB. Never touches the live DB unless explicitly given
its path. Output: JSON report to stdout (redirect to a file for the record).

Usage:
    uv run python utils/poc/poc1_anisotropy_audit.py <db_path> [--space NAME]
        [--pairs 20000] [--max-p 10] [--allow-small]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import struct
import sys
from pathlib import Path

import numpy as np

MIN_CHUNKS = 10_000  # gamma = d/N gate (#494/#495): full-W verdict needs N >> d
GOLDEN_SET = Path(__file__).resolve().parents[2] / "tests" / "memarchbench" / "fixtures" / "golden_queries.jsonl"
MIN_GOLDEN = 100
RNG_SEED = 20260706  # fixed: the PoC must be reproducible run-to-run


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
    """Load all vectors of the active (or named) space as a float32 matrix."""
    if space:
        row = conn.execute(
            "SELECT name, dim, table_name, element_type FROM embed_spaces WHERE name = ?", (space,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT name, dim, table_name, element_type FROM embed_spaces WHERE status = 'active'"
        ).fetchone()
    if row is None:
        sys.exit(f"no {'space named ' + space if space else 'active space'} in the database")
    name, dim, table, element_type = row
    if element_type != "float32":
        sys.exit(f"space {name!r} has element_type={element_type!r}; only float32 spaces are supported here")
    blobs = conn.execute(f"SELECT embedding FROM {table}").fetchall()  # noqa: S608 — table name from embed_spaces registry
    if not blobs:
        sys.exit(f"space {name!r} has no vectors")
    mat = np.array([np.array(struct.unpack(f"{dim}f", b[0]), dtype=np.float32) for b in blobs])
    return mat, name


def random_pair_cosine(mat: np.ndarray, pair_i: np.ndarray, pair_j: np.ndarray) -> dict:
    """Cosine statistics over a FIXED set of random pairs (paired comparison —
    the same pairs are reused across the baseline and every ABT level)."""
    unit = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-12)
    cos = np.einsum("ij,ij->i", unit[pair_i], unit[pair_j])
    return {"random_pair_cosine_mean": float(cos.mean()), "random_pair_cosine_std": float(cos.std())}


def spectrum_stats(s: np.ndarray) -> dict:
    """Variance concentration from a singular-value slice."""
    var = s**2 / (s**2).sum()
    return {
        "top1_pc_variance_share": float(var[0]),
        "top10_pc_variance_share": float(var[:10].sum()),
        "effective_rank_participation": float(np.exp(-(var * np.log(var + 1e-12)).sum())),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("db_path", type=Path)
    ap.add_argument("--space", default=None)
    ap.add_argument("--pairs", type=int, default=20_000)
    ap.add_argument("--max-p", type=int, default=10)
    ap.add_argument("--allow-small", action="store_true", help="run ABT-only sweep below the 10K-chunk gate")
    args = ap.parse_args()

    if args.pairs < 1:
        ap.error("--pairs must be >= 1")
    if args.max_p < 1:
        ap.error("--max-p must be >= 1")

    conn = _open_ro(args.db_path)
    mat, space_name = _load_embeddings(conn, args.space)
    rng = np.random.default_rng(RNG_SEED)

    # --- Gate box (#495) ---
    if len(mat) < MIN_CHUNKS and not args.allow_small:
        sys.exit(
            f"GATE: corpus has {len(mat)} vectors < {MIN_CHUNKS} (gamma = d/N argument, #494). "
            "Grow the corpus via T0 (#528), or pass --allow-small for an ABT-only sweep "
            "(no full-whitening verdict)."
        )
    golden_n = 0
    if GOLDEN_SET.is_file():
        with GOLDEN_SET.open() as fh:
            golden_n = sum(1 for line in fh if line.strip())
    golden_ok = golden_n >= MIN_GOLDEN

    n = len(mat)
    # Fixed pair sample, drawn once: baseline vs every ABT level is a PAIRED
    # comparison on identical pairs, not fresh draws per level.
    pair_i, pair_j = rng.integers(0, n, args.pairs), rng.integers(0, n, args.pairs)
    keep = pair_i != pair_j
    pair_i, pair_j = pair_i[keep], pair_j[keep]
    if len(pair_i) == 0:
        sys.exit(f"corpus of {n} vector(s) has no distinct random pairs; need >= 2 vectors for the cosine audit")

    # ONE SVD for everything: removing the top-p principal components zeroes
    # exactly those singular values, so each level's spectrum is s[p:], and
    # each level's projection is a cheap slice of vt.
    centered = mat - mat.mean(axis=0)
    _, s, vt = np.linalg.svd(centered, full_matrices=False)

    # Clamp the ABT sweep to the available spectrum: removing p >= len(s) principal
    # components leaves s[p:] empty and spectrum_stats would index var[0] out of range
    # (reachable under --allow-small on a corpus whose rank <= --max-p).
    max_p = min(args.max_p, len(s) - 1)

    report: dict = {
        "poc": "E0-PoC-1 anisotropy audit",
        "space": space_name,
        "gates": {"chunks": n, "chunks_gate_met": n >= MIN_CHUNKS, "golden_set_queries": golden_n},
        "baseline": {
            "n_vectors": n,
            "dim": int(mat.shape[1]),
            **random_pair_cosine(mat, pair_i, pair_j),
            **spectrum_stats(s),
        },
        "abt_sweep": [],
    }
    for p in range(1, max_p + 1):
        abt_mat = centered - (centered @ vt[:p].T) @ vt[:p]
        report["abt_sweep"].append({"p": p, **random_pair_cosine(abt_mat, pair_i, pair_j), **spectrum_stats(s[p:])})

    if not golden_ok:
        report["recall_comparison"] = (
            f"SKIPPED — golden set has {golden_n} < {MIN_GOLDEN} judged queries (#250). "
            "Geometry-only report: NOT sufficient for the E0 pass/fail verdict."
        )
    else:
        # Pass/fail axis: recall@k on the golden set, raw vs ABT space. Wire via
        # tests/memarchbench resolution helpers once #250 lands (quote/content_hash
        # anchoring). Deliberately unimplemented until then.
        report["recall_comparison"] = "TODO(#250): recall@k before/after — implement when the golden set lands"

    # allow_nan=False: a degenerate stat must fail loudly, not emit NaN (invalid JSON
    # per RFC 8259) into a report that feeds the owner+Fable verdict.
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
