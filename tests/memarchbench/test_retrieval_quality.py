"""Retrieval-quality baseline (#360/#250): Coverage@{5,10,20}, Recall@k,
nDCG@10, MRR against the golden set, on the real ingested corpus.

Corpus-gated: skips unless MEMARCH_CORPUS_DB points at an ingested corpus and
tests/memarchbench/fixtures/golden_queries.jsonl has curated queries (#250).
Once #250 lands, #529 files the CI wiring with a +/-2pp regression threshold
against the recorded baseline.

Judgment anchoring (locked on #250): judgments carry a verbatim ``quote`` from
the answering chunk plus optionally that chunk's ``content_hash``. Resolution
order: content_hash exact match, else quote-containment search within the
judged ``doc`` — so a re-ingested or re-chunked corpus does not orphan the
golden set.
"""

from __future__ import annotations

import pytest


@pytest.mark.skip(reason="stub — implementation gated on #250 golden set (>=100 judged queries)")
def test_retrieval_quality_baseline(corpus_conn, golden_set):
    """For each golden query: run search (retrieval only), resolve judgments to
    live chunk ids (content_hash, else quote containment), compute
    Coverage@{5,10,20} / Recall@k / nDCG@10 / MRR, compare to the recorded
    baseline within +/-2pp."""
    raise NotImplementedError
