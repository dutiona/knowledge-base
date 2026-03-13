#!/usr/bin/env python3
"""Evaluate pymupdf4llm.to_markdown() vs page.get_text() on real PDFs.

Trusted local PDFs only — no URL fetching.

Usage:
    python scripts/eval_pymupdf4llm.py paper1.pdf [paper2.pdf ...]
    python scripts/eval_pymupdf4llm.py paper.pdf --page 0
    python scripts/eval_pymupdf4llm.py paper.pdf --save-output
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import re
import resource
import sys
import time
from pathlib import Path

import fitz
import pymupdf4llm

# Reuse chunking from the main codebase
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from knowledge_base.ingest import _chunk_text  # noqa: E402

# ---------------------------------------------------------------------------
# Fixed extraction config
# ---------------------------------------------------------------------------

_TO_MARKDOWN_KWARGS = {
    "write_images": False,
    "page_chunks": False,
}


def _extract_flat(path: str) -> str:
    doc = fitz.open(path)
    pages = [page.get_text() for page in doc]
    doc.close()
    return "\n\n".join(pages)


def _extract_markdown(path: str) -> str:
    return pymupdf4llm.to_markdown(path, **_TO_MARKDOWN_KWARGS)


# ---------------------------------------------------------------------------
# Memory measurement via subprocess isolation
# ---------------------------------------------------------------------------


def _measure_in_child(func_name: str, path: str) -> dict:
    """Run extraction in a child process and measure peak RSS.

    Returns dict with 'text', 'elapsed_s', 'peak_rss_kb'.
    """

    import json
    import tempfile

    def worker(func_name: str, path: str, result_file: str) -> None:
        rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        t0 = time.monotonic()
        if func_name == "flat":
            text = _extract_flat(path)
        else:
            text = _extract_markdown(path)
        elapsed = time.monotonic() - t0
        rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Write to file instead of Queue to avoid pipe buffer deadlock
        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "text": text,
                    "elapsed_s": elapsed,
                    "peak_rss_kb": rss_after - rss_before,
                },
                f,
            )

    fd, result_path = tempfile.mkstemp(suffix=".json", prefix="eval-")
    os.close(fd)
    try:
        p = multiprocessing.Process(target=worker, args=(func_name, path, result_path))
        p.start()
        p.join(timeout=600)
        if p.is_alive():
            # Timed out — kill the zombie
            p.terminate()
            p.join(timeout=5)
            if p.is_alive():
                p.kill()
                p.join(timeout=5)
            print(
                f"  WARNING: {func_name} child timed out and was killed",
                file=sys.stderr,
            )
            return {"text": "", "elapsed_s": 0, "peak_rss_kb": 0}
        if p.exitcode != 0:
            print(
                f"  WARNING: {func_name} child exited with code {p.exitcode}",
                file=sys.stderr,
            )
            return {"text": "", "elapsed_s": 0, "peak_rss_kb": 0}
        with open(result_path, encoding="utf-8") as f:
            return json.load(f)
    finally:
        Path(result_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)
_LIST_RE = re.compile(r"^\s*[-*\u2022]\s|^\s*\d+\.\s", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\|.+\|$", re.MULTILINE)
_IMG_REF_RE = re.compile(r"!\[")


def _compute_metrics(text: str) -> dict:
    return {
        "chars": len(text),
        "lines": text.count("\n"),
        "headings": len(_HEADING_RE.findall(text)),
        "list_items": len(_LIST_RE.findall(text)),
        "table_rows": len(_TABLE_ROW_RE.findall(text)),
        "image_refs": len(_IMG_REF_RE.findall(text)),
    }


def _chunk_metrics(text: str) -> dict:
    chunks = _chunk_text(text)
    if not chunks:
        return {
            "num_chunks": 0,
            "avg_len": 0,
            "heading_start_pct": 0,
            "orphaned_heading_end_pct": 0,
        }

    heading_starts = sum(1 for c in chunks if _HEADING_RE.match(c))
    orphaned_ends = 0
    for c in chunks:
        lines = c.rstrip().splitlines()
        if lines and _HEADING_RE.match(lines[-1]):
            orphaned_ends += 1

    return {
        "num_chunks": len(chunks),
        "avg_len": sum(len(c) for c in chunks) / len(chunks),
        "heading_start_pct": heading_starts / len(chunks),
        "orphaned_heading_end_pct": orphaned_ends / len(chunks),
    }


def _table_integrity(text: str) -> float:
    """Measure what % of tables survive chunking intact.

    Finds contiguous table blocks in the full text (runs of consecutive
    pipe-delimited lines). Then checks if each block fits entirely
    within a single chunk. Returns fraction of blocks that are intact.
    """
    # Find table blocks: consecutive pipe-delimited lines
    lines = text.splitlines()
    blocks: list[list[str]] = []
    current_block: list[str] = []
    for line in lines:
        if _TABLE_ROW_RE.match(line):
            current_block.append(line)
        else:
            if current_block:
                blocks.append(current_block)
                current_block = []
    if current_block:
        blocks.append(current_block)

    if not blocks:
        return 1.0  # no tables → perfect by default

    chunks = _chunk_text(text)
    if not chunks:
        return 0.0

    # Check each block: is its full text found contiguously in any chunk?
    intact = 0
    for block in blocks:
        block_text = "\n".join(block)
        if any(block_text in chunk for chunk in chunks):
            intact += 1

    return intact / len(blocks)


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def _print_comparison(label: str, flat_val: object, md_val: object) -> None:
    print(f"  {label:<30} {str(flat_val):>12}  {str(md_val):>12}")


def evaluate_pdf(path: str, page: int | None = None, save_output: bool = False) -> dict:
    """Run full evaluation on a single PDF. Returns metrics dict."""
    print(f"\n{'=' * 70}")
    print(f"  {Path(path).name}")
    print(f"{'=' * 70}")

    # Basic PDF info
    doc = fitz.open(path)
    num_pages = len(doc)
    total_images = sum(len(p.get_images()) for p in doc)
    total_drawings = sum(len(p.get_drawings()) for p in doc)
    doc.close()

    print(f"\n  Pages: {num_pages}, Images: {total_images}, Drawings: {total_drawings}")

    # Extract both ways (with memory measurement)
    flat_result = _measure_in_child("flat", path)
    md_result = _measure_in_child("markdown", path)

    flat_text = flat_result["text"]
    md_text = md_result["text"]

    # Metrics
    flat_m = _compute_metrics(flat_text)
    md_m = _compute_metrics(md_text)

    print(f"\n  {'Metric':<30} {'get_text()':>12}  {'to_markdown()':>12}")
    print(f"  {'-' * 56}")
    _print_comparison("Characters", flat_m["chars"], md_m["chars"])
    _print_comparison("Lines", flat_m["lines"], md_m["lines"])
    _print_comparison("Headings", flat_m["headings"], md_m["headings"])
    _print_comparison("List items", flat_m["list_items"], md_m["list_items"])
    _print_comparison("Table rows", flat_m["table_rows"], md_m["table_rows"])
    _print_comparison("Image refs", flat_m["image_refs"], md_m["image_refs"])

    # Timing
    print(f"\n  {'Timing':<30} {'get_text()':>12}  {'to_markdown()':>12}")
    print(f"  {'-' * 56}")
    _print_comparison(
        "Wall-clock (s)",
        f"{flat_result['elapsed_s']:.2f}",
        f"{md_result['elapsed_s']:.2f}",
    )
    ratio = (
        md_result["elapsed_s"] / flat_result["elapsed_s"]
        if flat_result["elapsed_s"] > 0
        else float("inf")
    )
    _print_comparison("Ratio", "1.00x", f"{ratio:.2f}x")

    # Memory
    print(f"\n  {'Memory':<30} {'get_text()':>12}  {'to_markdown()':>12}")
    print(f"  {'-' * 56}")
    _print_comparison(
        "Peak RSS delta (KB)",
        flat_result["peak_rss_kb"],
        md_result["peak_rss_kb"],
    )

    # Chunk analysis
    flat_c = _chunk_metrics(flat_text)
    md_c = _chunk_metrics(md_text)

    print(f"\n  {'Chunk analysis':<30} {'get_text()':>12}  {'to_markdown()':>12}")
    print(f"  {'-' * 56}")
    _print_comparison("Num chunks", flat_c["num_chunks"], md_c["num_chunks"])
    _print_comparison(
        "Avg length",
        f"{flat_c['avg_len']:.0f}",
        f"{md_c['avg_len']:.0f}",
    )
    _print_comparison(
        "Heading-at-start %",
        f"{flat_c['heading_start_pct']:.1%}",
        f"{md_c['heading_start_pct']:.1%}",
    )
    _print_comparison(
        "Orphaned heading-at-end %",
        f"{flat_c['orphaned_heading_end_pct']:.1%}",
        f"{md_c['orphaned_heading_end_pct']:.1%}",
    )

    # Table integrity
    md_table_int = _table_integrity(md_text)
    print(f"\n  Table integrity (markdown): {md_table_int:.1%}")

    # Per-page deep dive
    if page is not None:
        doc = fitz.open(path)
        if page < num_pages:
            p = doc[page]
            flat_page = p.get_text()
            doc.close()
            # Get markdown for single page
            md_page = pymupdf4llm.to_markdown(path, pages=[page], **_TO_MARKDOWN_KWARGS)

            print(f"\n  --- Page {page}: flat text (first 500 chars) ---")
            print(f"  {flat_page[:500]}")
            print(f"\n  --- Page {page}: markdown (first 500 chars) ---")
            print(f"  {md_page[:500]}")
        else:
            doc.close()
            print(f"\n  Page {page} out of range (0-{num_pages - 1})")

    # Save output
    if save_output:
        out_dir = Path("tmp/eval_output")
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(path).stem
        (out_dir / f"{stem}.txt").write_text(flat_text, encoding="utf-8")
        (out_dir / f"{stem}.md").write_text(md_text, encoding="utf-8")
        print(f"\n  Saved to {out_dir / stem}.{{txt,md}}")

    return {
        "path": path,
        "pages": num_pages,
        "images": total_images,
        "drawings": total_drawings,
        "flat_metrics": flat_m,
        "md_metrics": md_m,
        "flat_timing_s": flat_result["elapsed_s"],
        "md_timing_s": md_result["elapsed_s"],
        "timing_ratio": ratio,
        "flat_rss_kb": flat_result["peak_rss_kb"],
        "md_rss_kb": md_result["peak_rss_kb"],
        "flat_chunks": flat_c,
        "md_chunks": md_c,
        "md_table_integrity": md_table_int,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate pymupdf4llm vs page.get_text() on PDF files"
    )
    parser.add_argument("pdfs", nargs="+", help="PDF files to evaluate")
    parser.add_argument("--page", type=int, default=None, help="Single-page deep dive")
    parser.add_argument(
        "--save-output",
        action="store_true",
        help="Save .md and .txt to tmp/eval_output/",
    )
    args = parser.parse_args()

    results = []
    for pdf_path in args.pdfs:
        if not Path(pdf_path).exists():
            print(f"SKIP: {pdf_path} not found", file=sys.stderr)
            continue
        result = evaluate_pdf(pdf_path, page=args.page, save_output=args.save_output)
        results.append(result)

    # Summary
    if len(results) > 1:
        print(f"\n{'=' * 70}")
        print("  SUMMARY")
        print(f"{'=' * 70}")
        for r in results:
            name = Path(r["path"]).name
            print(
                f"  {name:<40} "
                f"time={r['timing_ratio']:.1f}x  "
                f"headings={r['md_metrics']['headings']:>3}  "
                f"tables={r['md_metrics']['table_rows']:>3}  "
                f"imgs={r['md_metrics']['image_refs']:>3}"
            )


if __name__ == "__main__":
    main()
