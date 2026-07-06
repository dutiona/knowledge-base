"""MemArch-Bench KB slice (#360): invariant test suites for memory-architecture properties.

Four suites (see docs/adr/phase2-benchmark-memarch-bench-first.md):

- test_supersession.py       — reingest(newer) => search returns newer, not older
- test_retrieval_quality.py  — Recall@k / nDCG@10 / MRR / Coverage@k on the golden set (#250)
- test_prediction_errors.py  — prediction-error fires iff stale (precision/recall >= 0.9)
- test_entity_stability.py   — same paper => same entity IDs across chunking swaps

LoCoMo/LongMemEval are deliberately NOT run here (upstream answer-key and
retrieval-bypass defects — ADR + #360). Bi-temporal accuracy and decay are
memory-engine properties, out of KB scope.
"""
