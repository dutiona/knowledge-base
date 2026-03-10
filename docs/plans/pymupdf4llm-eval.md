# pymupdf4llm Evaluation — Phase 1 Findings

**Date:** 2026-03-10
**Issue:** #60
**pymupdf4llm version:** 1.27.2.1 (layout-based, uses ONNX model)

## Executive Summary

pymupdf4llm significantly improves structure extraction (headings, tables, lists) over bare `page.get_text()`. However, the ONNX layout model makes it **71-108x slower** and adds **~300MB RSS**, far exceeding the ≤2x thresholds. Image references are not produced in default mode. The library is a strong candidate for Phase 2 if performance is addressed (lazy loading, caching, or optional use).

## Test Corpus

| Paper                   | Pages | Images | Drawings | Category     |
| ----------------------- | ----- | ------ | -------- | ------------ |
| graphiti-2501.13956.pdf | 12    | 0      | 0        | Pure text    |
| memento-2508.16153.pdf  | 28    | 13     | 925      | Vector-heavy |
| coala-2309.02427.pdf    | 32    | 21     | 292      | Mixed        |
| mem0-2504.19413.pdf     | 23    | 28     | 106      | Raster-heavy |

## Results

### Structure Extraction

| Paper    | Headings (flat → md) | List Items (flat → md) | Table Rows (flat → md) |
| -------- | -------------------- | ---------------------- | ---------------------- |
| graphiti | 0 → 29               | 36 → 59                | 0 → 30                 |
| memento  | 4 → 32               | 17 → 85                | 0 → 64                 |
| coala    | 0 → 26               | 53 → 259               | 0 → 19                 |
| mem0     | 4 → 26               | 40 → 34                | 0 → 47                 |

**Headings detected in 4/4 papers (100%)** — exceeds ≥80% threshold.

**Tables detected in 4/4 papers** — pipe-delimited markdown tables produced. Table integrity varies:

- graphiti: 66.7% of table blocks fit within single chunks
- memento: 16.7% (large tables split across chunks)
- coala: 100%
- mem0: 0% (tables too large for 1000-char chunks)

### Figure/Image Extraction

**0 image references** produced across all 4 papers (0/4 = 0%).

pymupdf4llm with `write_images=False` produces no `![...]` references. With `write_images=True`, it may extract raster images to disk, but this was not the default behavior tested. The synthetic test with `write_images=True` and `image_path` did produce references (test passed).

**Vector drawings:** On the memento paper (925 drawings), pymupdf4llm outputs `**==> picture [W x H] intentionally omitted <==**` markers — it detects the drawing region but doesn't extract it. The vision pipeline remains necessary for vector figure description.

**Does NOT meet ≥80% threshold** with default settings. Needs `write_images=True` + `image_path` configuration for Phase 2.

### Reading Order (Two-Column)

Synthetic two-column test: **passed** — Column A text precedes Column B, no interleaving.

Real papers (memento page 0): Title correctly extracted as `# **Memento: Fine-tuning LLM Agents without Fine-tuning LLMs**` with proper heading markup. Author affiliations correctly grouped.

### Performance

| Paper          | get_text() | to_markdown() | Ratio  | RSS delta (MB) |
| -------------- | ---------- | ------------- | ------ | -------------- |
| graphiti (12p) | 0.06s      | 4.79s         | 78.7x  | 271            |
| memento (28p)  | 0.13s      | 9.37s         | 71.4x  | 313            |
| coala (32p)    | 0.11s      | 10.52s        | 96.7x  | 307            |
| mem0 (23p)     | 0.07s      | 8.07s         | 108.0x | 328            |

**Does NOT meet ≤2x wall-clock threshold.** The ONNX layout model dominates. ~300MB RSS overhead from onnxruntime + model weights.

Note: The ONNX model loads once per process invocation. In production (MCP server), this would be a one-time cost amortized across multiple ingestions. The per-page marginal cost (~0.3s/page) is more relevant than the total including model load.

### Chunk Quality

| Paper    | Chunks (flat → md) | Heading-at-start (flat → md) | Orphaned heading-at-end |
| -------- | ------------------ | ---------------------------- | ----------------------- |
| graphiti | 52 → 53            | 0.0% → 1.9%                  | 3.8%                    |
| memento  | 107 → 113          | 0.0% → 0.9%                  | 0.0%                    |
| coala    | 153 → 158          | 0.0% → 0.0%                  | 1.3%                    |
| mem0     | 87 → 93            | 1.1% → 1.1%                  | 1.1%                    |

Heading-at-start improvement is modest because `_chunk_text()` uses fixed-size 1000-char chunks with no markdown awareness. A markdown-aware chunker (Phase 2 consideration) would substantially improve this metric.

## Transitive Dependency Footprint

pymupdf4llm 1.27.2.1 brings:

- `pymupdf-layout` (1.27.2) — ONNX-based layout detection model
- `onnxruntime` (1.24.3) — ML inference runtime
- `numpy` (2.4.3)
- `sympy`, `networkx`, `protobuf`, `flatbuffers` — onnxruntime deps
- `tabulate` (0.10.0) — table formatting

Total: 11 new packages. Significant footprint due to onnxruntime.

## Go/No-Go Assessment

| Criterion            | Threshold             | Result              | Status |
| -------------------- | --------------------- | ------------------- | ------ |
| Structure (headings) | ≥80% papers           | 100% (4/4)          | PASS   |
| Tables               | ≥60% papers           | 100% (4/4 detected) | PASS   |
| Figures (raster)     | ≥80% papers w/ raster | 0% (default config) | FAIL   |
| Stability            | No crashes            | 0 crashes           | PASS   |
| Performance (time)   | ≤2x wall-clock        | 71-108x             | FAIL   |
| Performance (RSS)    | ≤2x RSS               | ~30x                | FAIL   |

**Overall: CONDITIONAL GO** — Structure extraction value is clear and significant. Performance thresholds were set for a drop-in replacement, but pymupdf4llm's ONNX model changes the cost model. Recommend proceeding to Phase 2 with these adjustments:

1. **Performance:** Accept higher cost at ingest time (one-time operation per paper). The MCP server loads the model once; per-page marginal cost (~0.3s) is acceptable for batch ingestion.
2. **Figures:** Configure `write_images=True` with a managed `image_path` directory. This should satisfy the figure criterion.
3. **Chunking:** Investigate markdown-aware chunking to fully leverage the structure improvement.
4. **Dependency:** Promote pymupdf4llm from dev to production dependency; accept the onnxruntime footprint.

## Recommendations for Phase 2

1. Replace `_extract_pdf_text()` with `pymupdf4llm.to_markdown()` using pinned kwargs
2. Add `write_images=True` + managed image directory for figure extraction
3. Evaluate markdown-aware chunking (split on headings, keep tables intact)
4. Lazy-load pymupdf4llm to avoid ONNX model load on non-PDF operations
5. Benchmark amortized cost in the MCP server context (model loaded once, multiple papers)
