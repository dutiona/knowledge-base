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


def _load_embeddings(conn: sqlite3.Connection, space: str | None) -> tuple[np.ndarray, str]:
    """Load all vectors of the active (or named) space as a float32 matrix."""
    if space:
        row = conn.execute("SELECT name, dim, table_name FROM embed_spaces WHERE name = ?", (space,)).fetchone()
    else:
        row = conn.execute("SELECT name, dim, table_name FROM embed_spaces WHERE status = 'active'").fetchone()
    if row is None:
        sys.exit(f"no {'space named ' + space if space else 'active space'} in {conn}")
    name, dim, table = row
    blobs = conn.execute(f"SELECT embedding FROM {table}").fetchall()  # noqa: S608 — table name from embed_spaces registry
    if not blobs:
        sys.exit(f"space {name!r} has no vectors")
    mat = np.array([np.array(struct.unpack(f"{dim}f", b[0]), dtype=np.float32) for b in blobs])
    return mat, name


def anisotropy_report(mat: np.ndarray, pairs: int, rng: np.random.Generator) -> dict:
    """Random-pair cosine statistics + spectral concentration."""
    n = len(mat)
    unit = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-12)
    i, j = rng.integers(0, n, pairs), rng.integers(0, n, pairs)
    keep = i != j
    cos = np.einsum("ij,ij->i", unit[i[keep]], unit[j[keep]])
    centered = mat - mat.mean(axis=0)
    # Spectrum of the covariance: how much variance the top PCs hoard
    s = np.linalg.svd(centered, compute_uv=False)
    var = s**2 / (s**2).sum()
    return {
        "n_vectors": int(n),
        "dim": int(mat.shape[1]),
        "random_pair_cosine_mean": float(cos.mean()),
        "random_pair_cosine_std": float(cos.std()),
        "top1_pc_variance_share": float(var[0]),
        "top10_pc_variance_share": float(var[:10].sum()),
        "effective_rank_participation": float(np.exp(-(var * np.log(var + 1e-12)).sum())),
    }


def abt(mat: np.ndarray, p: int) -> np.ndarray:
    """Mean-center + remove the top-p principal components (All-but-the-Top)."""
    centered = mat - mat.mean(axis=0)
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    return centered - (centered @ vt[:p].T) @ vt[:p]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("db_path", type=Path)
    ap.add_argument("--space", default=None)
    ap.add_argument("--pairs", type=int, default=20_000)
    ap.add_argument("--max-p", type=int, default=10)
    ap.add_argument("--allow-small", action="store_true", help="run ABT-only sweep below the 10K-chunk gate")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{args.db_path}?mode=ro", uri=True)
    mat, space_name = _load_embeddings(conn, args.space)
    rng = np.random.default_rng(RNG_SEED)

    # --- Gate box (#495) ---
    if len(mat) < MIN_CHUNKS and not args.allow_small:
        sys.exit(
            f"GATE: corpus has {len(mat)} vectors < {MIN_CHUNKS} (gamma = d/N argument, #494). "
            "Grow the corpus via T0 (#528), or pass --allow-small for an ABT-only sweep "
            "(no full-whitening verdict)."
        )
    golden_n = sum(1 for line in GOLDEN_SET.open() if line.strip()) if GOLDEN_SET.is_file() else 0
    golden_ok = golden_n >= MIN_GOLDEN

    report: dict = {
        "poc": "E0-PoC-1 anisotropy audit",
        "space": space_name,
        "gates": {"chunks": len(mat), "chunks_gate_met": len(mat) >= MIN_CHUNKS, "golden_set_queries": golden_n},
        "baseline": anisotropy_report(mat, args.pairs, rng),
        "abt_sweep": [],
    }
    for p in range(1, args.max_p + 1):
        report["abt_sweep"].append({"p": p, **anisotropy_report(abt(mat, p), args.pairs, rng)})

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

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
