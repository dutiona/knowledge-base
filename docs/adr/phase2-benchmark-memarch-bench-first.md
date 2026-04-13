# ADR: MemArch-Bench-first verification strategy (KB-P0-B)

- **Status**: Proposed
- **Date**: 2026-04-13
- **Supersedes**: _n/a_ — this establishes an explicit verification plan that the repository previously lacked (ROADMAP.md has no "Verification" section today).
- **Related**: Issue #250 (retrieval coverage golden set, 100+ questions), Phase 3A golden set work, Phase 3H Bayesian pipeline optimization (#261)
- **Gap ID**: KB-P0-B
- **Source**: `autonomous-agent-project/raw/docs/summaries/04-results-and-roadmap.md` §11.2
- **Corroborating landscape review**: `autonomous-agent-project/raw/landscape/32-memory-knowledge-landscape-april-week2-2026.md` §18.2.1 (MemPalace drama)

## Context

Knowledge-base currently has no documented verification plan beyond
unit tests, a roadmap entry for a retrieval golden set (#250,
unfinished), and scattered `@pytest.mark.slow` LLM integration tests.
Any future evaluation claim — "KB retrieves X% of the time", "KB
outperforms Y on Z benchmark" — will implicitly have to choose a
benchmark. The public defaults (LoCoMo, LongMemEval, AMB) have been
shown in April 2026 to be **untrustworthy for cross-system comparison**.

### The MemPalace drama (§18.2.1 of landscape #32)

> The MemPalace drama: A project called MemPalace reported **96.6% on
> LongMemEval and 100% on LoCoMo**. The 96.6% turned out to be
> retrieval recall@5 placed in the same table as end-to-end QA
> accuracy from other systems. The 100% LoCoMo result used
> `top_k=50`, which bypasses embedding retrieval entirely. When
> independent researchers ran actual end-to-end QA with calibrated
> judges, the numbers collapsed to **66.8% (raw) and 53.2% (with the
> project's own "lossless" compression)**.

Independent audit findings from §18.2.1:

- **LoCoMo: 6.4% of the answer key is factually wrong**
- **The LLM judge accepts 63% of intentionally incorrect answers**
- **56% of per-category comparisons are statistically indistinguishable
  from noise**
- **LongMemEval-S's per-question context (~115K tokens) fits in a
  single context window**, allowing retrieval bypass — a system can
  stuff the context and produce what looks like a memory score but is
  actually zero-shot long-context reasoning

Direct consequence for knowledge-base (quoting §11.2):

> **KB-P0-B (Benchmark strategy pivot — MemArch-Bench-first,
> LoCoMo/LongMemEval-second).** The MemPalace drama
> (mempalace2026drama) invalidates direct comparability of LoCoMo and
> LongMemEval scores across systems. KB's verification plan should
> prioritize architectural-property benchmarking against supersession
> correctness, bi-temporal point-in-time accuracy, type-appropriate
> decay behavior, and hybrid retrieval quality. LoCoMo/LongMemEval
> remain as secondary references with explicit caveats. Update
> knowledge-base/ROADMAP.md verification section to reflect this
> pivot. This also aligns KB's evaluation strategy with paper #2
> (MemArch-Bench) publication opportunity.

### What "architectural property" means for knowledge-base specifically

§11.2 lists four properties. Two of them — **bi-temporal point-in-time
accuracy** and **type-appropriate decay** — are primarily
memory-engine concerns; knowledge-base's schema does not implement
four-timestamp bi-temporal validity or Ebbinghaus decay, and should
not pretend to benchmark properties it does not implement. This ADR
narrows the KB-side slice of MemArch-Bench to the properties KB
actually owns:

1. **Supersession correctness via `reingest`** — When a chunk is
   re-ingested from a newer source that contradicts the prior
   ingestion, subsequent search results should reflect the newer
   content, not the older. Stale chunks should be FK-cleaned.
2. **Hybrid retrieval quality on KB's specific hybrid** — BM25
   (FTS5) + cosine (sqlite-vec) + RRF + stage-2 reranking, measured
   on a KB-owned golden set with paper/code/markdown/web content
   mixed.
3. **Prediction-error detection correctness** — Issue #127 (done)
   already implements stale-result detection; MemArch-Bench-shape
   property tests should measure whether prediction errors fire
   when they should, and **only** when they should (precision AND
   recall).
4. **Entity-resolution stability** — When the map-reduce extraction
   re-runs over the same content with different chunk boundaries,
   entity IDs should remain stable. This is a falsifiable property
   of `extraction.py` that no existing LoCoMo/LongMemEval question
   tests.

## Decision

### Primary verification instrument: MemArch-Bench (KB slice)

Adopt MemArch-Bench as the **primary** verification framework for
knowledge-base. The KB slice of MemArch-Bench consists of four
property-test suites, each with 20+ test cases, living under
`tests/memarchbench/`. Each suite is an architectural-property
invariant rather than a QA accuracy number.

| Suite                         | Property under test                                               | Pass criterion                                                        |
| ----------------------------- | ----------------------------------------------------------------- | --------------------------------------------------------------------- |
| `test_supersession.py`        | `reingest(newer)` ⇒ search returns newer, not older               | 100% on hand-written fixtures; 95% on generated paraphrase variants   |
| `test_retrieval_quality.py`   | Recall@k, nDCG@k, MRR on KB-owned golden set (built from #250)    | Baseline recorded, regression threshold ±2pp                          |
| `test_prediction_errors.py`   | Prediction-error fires iff stale, `precision` and `recall` ≥ 0.9  | Both metrics tracked in CI                                            |
| `test_entity_stability.py`    | Same paper → same entity IDs across chunking strategy swap        | 100% ID stability on identical content                                |

These are **invariant tests**, not score-maximization tests. A passing
suite does not establish "KB is SOTA"; a failing suite establishes "KB
lost a property it previously had."

### Secondary reference: LoCoMo/LongMemEval with explicit caveats

Keep LoCoMo and LongMemEval as **secondary** reference benchmarks
only, and only with the following stipulations published alongside
any number we report:

1. We run them with our own independently-audited subset, not the
   full benchmark, until the community resolves the MemPalace issues.
2. We report recall@k and end-to-end QA accuracy in **separate tables
   with explicit column labels**, so the MemPalace confounding of the
   two cannot recur in our own reports.
3. We cite the MemPalace drama (landscape #32 §18.2.1) as motivation
   in every paper/README/post that reports a number on these
   benchmarks.
4. We **do not** claim cross-system comparison against any published
   LoCoMo/LongMemEval number until the 6.4% answer-key error and
   63% wrong-answer-accept judge are resolved upstream.

### ROADMAP.md update

This ADR proposes adding a new top-level section to `ROADMAP.md`
titled **"Verification"**, positioned immediately after "Phase 3
Summary" and before "Phase 4 — New Frontends & Scale". The section
will:

- Name MemArch-Bench as the primary instrument
- Point to this ADR for the full rationale
- Cite landscape #32 §18.2.1 for the MemPalace drama
- Cross-reference issue #250 as the prerequisite for `test_retrieval_quality.py`
- Explicitly park LoCoMo/LongMemEval as secondary references with caveats

The update is proposed alongside this ADR in the same change set.

### Alignment with paper #2

Per §11.2:

> This also aligns KB's evaluation strategy with paper #2 (MemArch-Bench)
> publication opportunity.

and per landscape #32 §18.2.1 action item:

> Paper #3 needs a §Benchmarking methodology section: when we
> eventually evaluate memory-engine, we cannot claim numbers against
> LoCoMo/LongMemEval without running our own audit.

The MemArch-Bench KB slice described above is the KB-side input to
paper #2. Test cases written under `tests/memarchbench/` are directly
reusable as the empirical section of that paper.

## Consequences

### Positive

- **Trustworthy CI signal**: property-test invariants do not rot when
  upstream benchmarks do. A failing `test_supersession.py` is a real
  regression; a changing LoCoMo score is not necessarily.
- **Publication alignment**: paper #2 (MemArch-Bench) gets its KB
  empirical section for free as tests accumulate.
- **Cheaper to run than LoCoMo/LongMemEval**: property tests are
  pytest fixtures, not multi-hour LLM-judge runs.
- **Honest scope**: we only claim to test properties the code owns.
  Bi-temporal and decay properties are explicitly flagged as ME's
  responsibility, not KB's.

### Negative / risks

- **No apples-to-apples against competitors**: we lose the ability to
  claim "X% on LongMemEval" as a marketing number. This is the
  intended consequence, not a bug.
- **Golden set is a prerequisite**: `test_retrieval_quality.py`
  depends on #250 landing. Until then, that suite is a stub.
- **MemArch-Bench is not yet a published artifact**: we are adopting
  a framework from an unpublished-but-forthcoming paper. If paper #2
  changes shape during peer review, the KB slice may need to be
  re-aligned.

### Neutral

- Existing `@pytest.mark.slow` LLM integration tests are unaffected
  and remain the only way to test end-to-end LLM-powered flows.
- Existing 338+ unit tests are unaffected.
- The ADR does not mandate any schema change or new dependency.

## Alternatives considered

1. **Keep LoCoMo/LongMemEval as primary, add caveats only** — rejected
   because the MemPalace audit shows the caveats need to be so heavy
   (6.4% wrong answer key, 63% wrong-answer-accept judge, 56%
   noise-indistinguishable categories) that the benchmark is no longer
   informative. Half-measures here would be dishonest.
2. **Wait for the community to resolve the MemPalace crisis** —
   rejected because the resolution timeline is external to us and
   the gap (no verification plan) already exists.
3. **Ragas-only** — rejected because Ragas is LLM-judge-based and
   therefore inherits the same 63% wrong-answer-accept failure mode.
4. **Hand-written golden set only, no MemArch-Bench framing** —
   rejected because the MemArch-Bench framing is what aligns the KB
   test suites with paper #2 and with memory-engine's parallel
   verification work.

## References

- `autonomous-agent-project/raw/docs/summaries/04-results-and-roadmap.md` §11.2 KB-P0-B, §10.3 (benchmark governance crisis), §3.4 (MemArch-Bench verification plan)
- `autonomous-agent-project/raw/landscape/32-memory-knowledge-landscape-april-week2-2026.md` §18.2.1 (MemPalace drama, direct quotes), §17.3 (community-voice validation)
- Existing KB modules implicated: [src/knowledge_base/ingest.py](../../src/knowledge_base/ingest.py), [src/knowledge_base/search.py](../../src/knowledge_base/search.py), [src/knowledge_base/extraction.py](../../src/knowledge_base/extraction.py), [src/knowledge_base/prediction_errors.py](../../src/knowledge_base/prediction_errors.py)
- Related issue: #250 (retrieval coverage golden set)

## Open questions for implementation

1. **Where does the golden set live?**: `tests/memarchbench/fixtures/`
   versus `docs/benchmark/golden-set/` versus a separate git submodule.
   Favor `tests/memarchbench/fixtures/` for now — the benchmark is a
   test, not an artifact.
2. **Paraphrase generation**: the supersession suite needs controlled
   paraphrase variants. Hand-written or LLM-generated? Hand-written
   for the first 20 cases; revisit after we have a baseline.
3. **How strict is the "regression threshold ±2pp" on retrieval
   quality?**: 2pp is a guess; first 10 CI runs should establish a
   real noise floor.
4. **Prediction-error precision/recall targets**: 0.9 each is a guess
   until we measure what #127's current implementation actually
   achieves.
