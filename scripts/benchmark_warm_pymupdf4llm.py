#!/usr/bin/env python3
"""Warm-process benchmark for pymupdf4llm extraction.

Measures:
  - Cold-start time (first call, includes ONNX model load)
  - Warm-start time (subsequent calls, model already loaded)
  - Per-page marginal cost
  - RSS after model load vs after N papers

Usage:
    python scripts/benchmark_warm_pymupdf4llm.py <paper1.pdf> [paper2.pdf ...]

Not run in CI — manual validation only.
"""

from __future__ import annotations

import argparse
import resource
import sys
import time
from pathlib import Path


def _get_rss_mb() -> float:
    """Current process peak RSS in MB (Linux: ru_maxrss is KB)."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_maxrss / 1024  # KB → MB


def benchmark(pdf_paths: list[Path], warm_iterations: int = 5) -> None:
    import fitz

    rss_before = _get_rss_mb()
    print(f"RSS before import: {rss_before:.1f} MB")

    # --- Cold start (includes ONNX model load) ---
    import pymupdf4llm

    rss_after_import = _get_rss_mb()
    print(
        f"RSS after import:  {rss_after_import:.1f} MB (+{rss_after_import - rss_before:.1f})"
    )
    print(f"pymupdf4llm version: {pymupdf4llm.__version__}")
    print()

    kwargs = {
        "write_images": False,
        "page_chunks": True,
        "force_text": True,
    }

    for pdf_path in pdf_paths:
        doc = fitz.open(str(pdf_path))
        num_pages = len(doc)
        doc.close()

        print(f"{'=' * 60}")
        print(f"File: {pdf_path.name} ({num_pages} pages)")
        print(f"{'=' * 60}")

        # Cold-start (first call for this file, but model may be warm)
        t0 = time.perf_counter()
        pymupdf4llm.to_markdown(str(pdf_path), **kwargs)
        cold_time = time.perf_counter() - t0
        rss_after_cold = _get_rss_mb()

        print(
            f"  Cold call:       {cold_time:.3f}s  ({cold_time / num_pages:.3f}s/page)"
        )
        print(f"  RSS after cold:  {rss_after_cold:.1f} MB")

        # Warm calls
        warm_times = []
        for i in range(warm_iterations):
            t0 = time.perf_counter()
            pymupdf4llm.to_markdown(str(pdf_path), **kwargs)
            warm_times.append(time.perf_counter() - t0)

        avg_warm = sum(warm_times) / len(warm_times)
        min_warm = min(warm_times)
        max_warm = max(warm_times)

        print(f"  Warm calls ({warm_iterations}x):")
        print(f"    avg: {avg_warm:.3f}s  ({avg_warm / num_pages:.3f}s/page)")
        print(f"    min: {min_warm:.3f}s  max: {max_warm:.3f}s")

        # Compare with flat extraction
        t0 = time.perf_counter()
        doc = fitz.open(str(pdf_path))
        for page in doc:
            page.get_text()
        doc.close()
        flat_time = time.perf_counter() - t0

        print(f"  Flat get_text(): {flat_time:.3f}s")
        print(
            f"  Warm ratio:      {avg_warm / flat_time:.1f}x  (cold: {cold_time / flat_time:.1f}x)"
        )
        print()

    rss_final = _get_rss_mb()
    print(f"Final RSS: {rss_final:.1f} MB (total delta: +{rss_final - rss_before:.1f})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Warm-process pymupdf4llm benchmark")
    parser.add_argument("pdfs", nargs="+", type=Path, help="PDF files to benchmark")
    parser.add_argument(
        "-n", "--iterations", type=int, default=5, help="Warm iterations per file"
    )
    args = parser.parse_args()

    missing = [p for p in args.pdfs if not p.exists()]
    if missing:
        print(f"Error: files not found: {missing}", file=sys.stderr)
        sys.exit(1)

    benchmark(args.pdfs, args.iterations)


if __name__ == "__main__":
    main()
