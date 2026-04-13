# ADR: LongTracer as Phase 2 ingestion quality gate (KB-P0-A)

- **Status**: Proposed
- **Date**: 2026-04-13
- **Supersedes**: _n/a_
- **Related**: KB-P0-B (benchmark pivot), Phase 3 candidate LettuceDetect (answer-time UX)
- **Gap ID**: KB-P0-A
- **Source**: `autonomous-agent-project/raw/docs/summaries/04-results-and-roadmap.md` §11.2
- **Corroborating landscape review**: `autonomous-agent-project/raw/landscape/32-memory-knowledge-landscape-april-week2-2026.md` §17.2.8 and §12

## Context

The April 2026 landscape review surfaced a material correction to an
earlier recommendation. Prior reviews had named **LettuceDetect** as the
Phase 2 ingestion quality-gate candidate for knowledge-base. Review #32
(§17.2.8) documented that the actual referent of the Reddit post that
surfaced the tool was **ENDEVSOLS/LongTracer**, not LettuceDetect, and
that LongTracer is the better fit for a pre-indexing gate while
LettuceDetect remains the stronger candidate for answer-time span
highlighting at UX time.

Quoting §11.2 verbatim:

> **KB-P0-A (LongTracer integration as Phase 2 ingestion quality gate).**
> Integrate LongTracer (endevsols2026longtracer) as a pre-indexing
> quality gate. The pipeline: for each chunk and its extracted summary,
> run the LongTracer STS + NLI two-stage classifier; chunks whose
> summary ↔ source NLI is contradictory (or where claims are
> unsupported) go to a review queue rather than the index. LongTracer is
> MIT-licensed, framework-agnostic (LangChain / LlamaIndex / Haystack /
> LangGraph adapters), ships with pluggable storage (SQLite default plus
> Mongo / Postgres / Redis), and runs CPU-only with zero LLM API calls.
> This replaces the prior (agent-inferred) LettuceDetect suggestion for
> Phase 2. LettuceDetect remains a Phase 3 candidate for answer-time
> span highlighting in UX.

And from review #32 §17.2.8 (direct verdict table):

> | Tool           | Stage                     | Why                                                                                                                                                      |
> | -------------- | ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
> | **LongTracer** | KB Phase 2 (ingestion)    | Framework-agnostic, zero fine-tuning, MIT, pluggable storage, CPU-friendly, drops into existing pipelines                                                |
> | **LettuceDetect** | KB Phase 3 (answer-time) | Token-level span highlighting is superior for surfacing hallucinations to users in retrieved-answer UX. Requires model hosting + RAGTruth-shaped inputs. |

### Why a gate is needed now

Knowledge-base's map-reduce extraction (see `extraction.py`) produces
per-chunk summaries and entity mentions via LLM calls that are then
stored alongside the raw chunk content. Today there is **no automated
check that a summary is faithful to the chunk it claims to summarize**.
A summary that contradicts its source will still enter the index, be
embedded, be surfaced by hybrid search, and be treated as ground truth
by downstream consumers. This is the same failure mode the LongTracer
pipeline was designed to detect — with a cheap, deterministic classifier
that runs CPU-only.

## Decision

Add an **opt-in, default-off Phase 2 ingestion quality gate** that
applies LongTracer's STS + NLI pipeline to each `(chunk, extracted
summary)` pair. When the gate is enabled, chunks whose summary ↔ source
NLI verdict is `contradiction` or `neutral` are routed to a new
**review queue** (a new SQLite table, `review_queue`) instead of being
indexed.

Specifically:

1. **New dependency (proposed, not installed by this ADR)**:
   `longtracer` on PyPI, MIT-licensed, `pip install longtracer` (we will
   add it to `pyproject.toml` as a soft dependency under an
   `[project.optional-dependencies].quality-gate` extra, so users who do
   not enable the gate do not pay the install cost). Installed via
   `uv pip install 'knowledge-base[quality-gate]'` — not via `pip`.

2. **New module**: `src/knowledge_base/quality_gate.py` wrapping the
   direct programmatic API (no framework adapter). Minimal shape:

   ```python
   from longtracer import check
   from longtracer.guard.verifier import CitationVerifier

   _verifier = CitationVerifier(cache=True)  # module-level, reused

   def gate_chunk(chunk_text: str, summary: str) -> GateVerdict:
       """Return PASS / REVIEW based on NLI verdict on summary↔chunk.

       PASS   → chunk + summary proceed to the index
       REVIEW → chunk is stored but marked pending; summary is quarantined
       """
   ```

   We deliberately use the direct API (`CitationVerifier`), not the
   LangChain/LlamaIndex/Haystack adapters — knowledge-base has no
   dependency on those frameworks and the direct API is three lines.

3. **New schema (proposed, not applied by this ADR)**: a `review_queue`
   table with columns `chunk_id`, `summary`, `verdict`,
   `contradiction_spans` (JSON), `trust_score`, `created_at`,
   `resolved_at`, `resolver` (nullable). This is the quarantine target.

4. **New MCP tool**: `list_review_queue` / `resolve_review_item` so the
   consumer (Claude, a human reviewer) can inspect, approve, or reject
   quarantined items and re-submit them to the index.

5. **New config key**: `quality_gate` — one of `off` (default),
   `advisory` (log only, still index), or `strict` (divert to review
   queue). Default is `off` so the gate does not silently change
   existing ingestion behavior for users who upgrade.

6. **Integration point**: `ingest.py` after chunk creation and summary
   extraction, before FTS/vector insert. Gate runs per-chunk,
   parallelizable with `ThreadPoolExecutor` (LongTracer is CPU-bound,
   GIL-releasing). Measured on the hot path.

7. **Trace storage backend**: LongTracer supports pluggable storage. We
   will use LongTracer's **in-memory** backend (not SQLite) for this
   integration because we are persisting verdicts into our own
   `review_queue` table. LongTracer's own storage is for its internal
   tracing, which we do not need alongside knowledge-base's `jobs` and
   `prediction_errors` tables.

### What this decision is not

- Not a UX hallucination highlighter at answer time — that is Phase 3
  and should evaluate **LettuceDetect** (token-level span classifier
  on RAGTruth), not LongTracer. LongTracer's output is verdict-level
  (PASS/FAIL + trust score), not span-level.
- Not a replacement for human review of low-confidence extractions —
  the review queue is a surface, not an auto-approver.
- Not a benchmark-gated accept threshold — that tuning should follow
  KB-P0-B (MemArch-Bench-first) once we have property-level ground truth.

## Consequences

### Positive

- **Fidelity wall**: extracted summaries that contradict their source
  no longer silently enter the index. This is a falsifiable, testable
  property.
- **CPU-only, no API keys**: LongTracer ships `all-MiniLM-L6-v2` +
  `nli-deberta-v3-xsmall` (~50M params total), runs without an LLM
  call, and does not require Ollama to be available. This matches
  knowledge-base's local-first stance.
- **Reversible**: the gate is default-off and keyed on a config
  setting. Turning it off restores pre-ADR behavior bit-for-bit.
- **Integrates cleanly with existing map-reduce extraction**: we
  already produce `(chunk, summary)` pairs in `extraction.py` —
  LongTracer operates on that exact shape.
- **No framework lock-in**: direct-API integration keeps
  knowledge-base free of LangChain/LlamaIndex/Haystack dependencies.

### Negative / risks

- **Model cold-start cost**: first ingestion after startup pays a
  one-time model-load latency (~500ms–2s depending on CPU). Mitigation:
  module-level singleton verifier, warmed on startup when the config
  key is non-`off`.
- **False-positive rate unknown on scientific prose**: LongTracer
  reports 91.64% NLI accuracy on SNLI; scientific abstracts, code
  comments, and formulas are out-of-distribution. The `advisory` mode
  is explicitly to measure the false-positive rate before committing
  to `strict` mode on real corpora.
- **New table, new MCP tool surface**: any schema change has a
  migration cost and tests to write. Estimate: one SQL migration plus
  `tests/test_quality_gate.py`.
- **LongTracer version drift**: we pin a specific version in
  `pyproject.toml` and track upstream releases via a new line in
  `ROADMAP.md` Phase 3B.

### Neutral

- Default-off: users who do nothing see no behavior change.
- The ADR does not bind us to LongTracer forever — if a better NLI
  gate appears (LettuceDetect moving upstream, or a new model), the
  `quality_gate.py` wrapper is the only integration surface to swap.

## Alternatives considered

1. **LettuceDetect at ingestion time (prior recommendation)** —
   rejected per the §11.2 correction: LettuceDetect requires RAGTruth-
   shaped inputs and model hosting, is slower per claim, and its
   span-level output is over-powered for a gate that only needs
   PASS/FAIL. Preferred for Phase 3 answer-time UX instead.
2. **LLM-as-judge NLI via Ollama/OpenAI** — rejected because it
   requires an LLM call per chunk, is non-deterministic, and
   contradicts knowledge-base's explicit local-first + LLM-free-engine
   alignment with the memory-engine design philosophy.
3. **Hand-rolled entailment heuristic (token overlap, TF-IDF cosine)** —
   rejected because it has known adversarial failure modes (paraphrase
   hallucinations) that the DeBERTa cross-encoder handles correctly.
4. **Defer to Phase 3** — rejected because the gap (summaries silently
   contradicting chunks) already exists in the current code and
   compounds with every ingestion.

## References

- [ENDEVSOLS/LongTracer on GitHub](https://github.com/ENDEVSOLS/LongTracer) (MIT)
- `autonomous-agent-project/raw/docs/summaries/04-results-and-roadmap.md` §11.2 KB-P0-A
- `autonomous-agent-project/raw/landscape/32-memory-knowledge-landscape-april-week2-2026.md` §17.2.8 (project profile), §12 (RAG observability stratification)
- Knowledge-base modules touched by the proposed integration: [src/knowledge_base/ingest.py](../../src/knowledge_base/ingest.py), [src/knowledge_base/extraction.py](../../src/knowledge_base/extraction.py), [src/knowledge_base/db.py](../../src/knowledge_base/db.py)

## Open questions for implementation

1. **Advisory vs strict default**: should new installs default to
   `advisory` so users see the telemetry without risking review-queue
   overflow, or stay `off` until the user opts in? (Current ADR: `off`.)
2. **Per-chunk-type thresholds**: code chunks, figure captions, and
   prose chunks likely need different NLI thresholds. Should the gate
   expose a `gate_threshold_per_chunk_type` config, or keep a single
   global threshold until evidence accumulates?
3. **Interaction with `reingest`**: on `reingest`, should quarantined
   items from the prior ingestion be re-tested against the new source,
   or require explicit review resolution?
