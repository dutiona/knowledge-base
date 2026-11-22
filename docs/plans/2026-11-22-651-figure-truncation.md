# #651 — follow-up group P: figure-description truncation record, vacuous vector-drawing test

## Outcome

Every chunk a store path embeds cut to the embedder's `max_input_tokens` gets its `embed_truncations` row (#620),
whichever path embedded it first: figure descriptions stored by the PDF vision pipeline and by the three web figure
writers now record the cut exactly as `backfill_space` and `redact --rewrite` do — chunk id, space, tokens counted,
tokens kept, never the text (#648). `test_vector_drawing_output_documented` stops being vacuous: it asserts what
the production PDF extraction does with a vector-only page and is shown red on a build where that changes, and the
figure-extraction guide stops claiming the opposite (#649). One PR, `Closes #648` and `Closes #649`, each member in
its own commit.

## Constraints

- #648: "A failing test first: storing an over-long figure description records the truncation like backfill does, without content."
- #648: "Figure descriptions are cut to the embedder's `max_input_tokens` when stored, as designed" — the stored chunk keeps its whole text; only the embedded input is cut. This plan does not split figure descriptions.
- #648: "Same defect class as #638": reuse the `embed_truncations` recording #635/#650 built (owner, brief).
- #649: "The test asserts the documented behaviour for vector drawings and is shown failing against a build where it changes, or it is deleted with the reason in the commit."
- #651: "Each member's own acceptance, unchanged. The PR carries one `Closes #N` line per member."
- knowledge-base is public: no host name, tailnet address or token in any commit, PR or test.
- Tests check behaviour, never a dependency's incidental output format (the pymupdf4llm placeholder text is not asserted).
- No schema change: `embed_truncations` (v4) already has every column this needs.

## Design

The cut happens inside `OpenAICompatProvider._bounded`, the only provider that counts tokens. `embed_with_cuts`
surfaces it, but every document and figure store embeds through `ingest._embed_with_config` → `embeddings.embed`,
which drops it. About 250 tests fake the embedder by patching the name `knowledge_base.ingest.embed` (one
`stub_document_embedder` fixture plus decorators in 16 files) and every fake already takes `**kwargs`.

- **Chosen:** `embeddings.embed` gains a keyword-only `cuts: Cuts | None = None`; when given, it is filled from
  `embed_with_cuts`. That out-parameter is the one place a dict is mutated, and it keeps the `ingest.embed` seam
  every fake uses. `_embed_with_config` then returns `(vectors, cuts)`, so no caller can drop a cut silently and
  basedpyright flags any caller that does not unpack.
- **Ideal, not chosen:** `_embed_with_config` built on #650's `space_embedder` (it duplicates that helper's provider
  and Matryoshka logic), with the fakes moved to one provider-level seam. That rewrites ~250 test patch sites for
  no behaviour change; it is a test-seam refactor of its own, reported as a follow-up, not folded in here.

## Implementation sequence

- [ ] **1. One writer for `embed_truncations` rows.** Files: `src/knowledge_base/embed_swap.py`,
  `src/knowledge_base/redact_store.py`. Add `record_truncation(conn, chunk_id: int, space_name: str,
  cut: tuple[int, int]) -> None` to `embed_swap.py` (the `INSERT OR REPLACE … (chunk_id, space_name, tokens_before,
  tokens_after)` both sites hold today) and call it from `backfill_space` and `redact_store._apply`. No behaviour
  change, so no new test: the existing #620/#638 tests are the guard.
  Green: `uv run pytest tests/test_embed_limits.py tests/test_redact_store.py -q` all pass.
  Commit: `refactor(embed): one writer for embed_truncations rows`.

- [ ] **2. Figure stores record their cuts (#648).** Files: `src/knowledge_base/embeddings.py`,
  `src/knowledge_base/ingest.py`, `src/knowledge_base/vision.py`, `src/knowledge_base/web.py`,
  `tests/test_embed_limits.py`, and the tests that mock or call `_embed_with_config` (`tests/test_vision.py`,
  `tests/test_multi_figure.py`, `tests/test_vision_omniparser_integration.py`, `tests/test_embeddings.py`,
  `tests/test_embed_swap.py`, `tests/test_redact_store.py`).
  - Red first, in `tests/test_embed_limits.py` under the existing `bounded_embedder` (llama-server at the httpx
    transport, `LIMIT = 64`):
    - `test_a_pdf_figure_stored_cut_records_its_truncation`: `vision._persist_figures` with one page whose figure
      description is `LONG`; asserts the `embed_truncations` rows are exactly
      `[(figure_chunk_id, active_space, count(stored_content), count(sent))]` — the shape
      `test_a_backfill_reports_each_stored_chunk_it_truncates` checks — that the stored chunk keeps its whole text,
      and that no `zqxsentinel` reaches the captured log.
    - `test_a_web_figure_stored_cut_records_its_truncation`, parametrized over the three web writers
      (`_extract_web_figures`, `_extract_element_captures`, `_extract_html_images`), each
      with `knowledge_base.vision._vision_call` patched to return one `LONG` description: same assertion. Each
      writer builds its own figure tuple, so each one can drop the cut on its own.
    - Red: `uv run pytest tests/test_embed_limits.py -q -k "figure_stored_cut"` → 4 failures, rows `[]`.
  - Change:
    - `embeddings.embed(texts, model, expected_dim=None, *, _provider_cfg, cuts: Cuts | None = None)`: when `cuts`
      is given, embed through `embed_with_cuts(get_provider(cfg=_provider_cfg), …)` and `cuts.update(found)`.
    - `ingest._embed_with_config(conn, texts) -> tuple[list[list[float] | None], Cuts]`, passing `cuts=` to
      `embed` (both the Matryoshka and the plain branch).
    - `ingest._insert_chunk(…, cut: tuple[int, int] | None = None)`: after `insert_chunk_vec`, when the vector was
      stored and `cut` is given, `record_truncation` against the space that owns the vec table it wrote. A chunk
      without a vector gets no row, as in backfill.
    - Callers pass `cut=cuts.get(i)` by embedding position: `ingest._insert_chunks` (documents are pre-split by
      `_bounded_chunks`, so this records nothing today but can no longer drop one), `vision._persist_figures`, and
      the three web writers through a fifth element `cut` in `_replace_figures`' figure tuple. The key is always
      the text's position in the embedded batch, never a chunk index: `_extract_element_captures` keeps
      `_WEB_ELEMENT_CAPTURE_CHUNK_INDEX_START + cap_idx` for `chunk_index` (its `cap_idx` has gaps where a capture
      was skipped) and takes the cut from `enumerate(zip(collected, embeddings, strict=True))`. The parametrized
      element-capture test therefore skips the first capture (an unreadable file), so a cut keyed by `cap_idx`
      lands on no figure and the test fails.
    - Test fakes of `_embed_with_config` return `(vectors, {})`; direct callers unpack. Mechanical; candidate local
      bundle per `references/local-bundles.md`, diff read and tests run by me.
  - Green: the four new tests pass; `uv run pytest tests/ -q -m "not slow"` passes.
  - Commit: `fix(vision): record when a figure description is cut to the embedder's input limit` with
    `Closes #648`.

- [ ] **3. The vector-drawing test asserts what production extraction does (#649).** Files:
  `tests/test_pymupdf4llm_eval.py`, `docs/usage/figure-extraction.md`.
  - Observed on the pinned pymupdf4llm 1.27.2.1 (probe, 2026-11-22): `_extract_pdf_markdown(pdf, image_dir)` — the
    only production call, `write_images=True` — rasterises the synthetic vector-only page's drawing cluster into one
    PNG and emits one `![](…)` reference to it, next to the heading text. The guide says the opposite ("pymupdf4llm
    cannot extract these as images"), so the docs are corrected rather than the behaviour asserted against them.
  - Replace `test_vector_drawing_output_documented` with `test_a_vector_only_page_yields_an_extracted_image`: the
    page text keeps "Architecture Overview", `IMAGE_REF_RE` finds at least one reference, and each one is a file
    written to `image_dir` (the shape of `test_real_pdf_image_refs`). No print, no placeholder-string assertion.
    It stays unmarked (about 1 s), so the merge gate runs it.
  - Red on a build where it changes, both shown and quoted in the PR:
    1. pymupdf4llm 1.27.2.2 (drops image references, #465) in a scratch copy under
       `~/.local/state/agent-work/…/knowledge-base-651/scratch/`, never the worktree's lock;
    2. a mutant of `_extract_pdf_markdown` passing `write_images=False`.
    Each: `uv run pytest tests/test_pymupdf4llm_eval.py -q -k vector_only` → 1 failed.
  - Docs: the "Fallback path" paragraph says pymupdf4llm extracts a vector drawing as an image when it groups it
    into a picture, and that the full-page render covers the vector pages where it extracted none (the >10
    drawings rule, unchanged).
  - Green: `uv run pytest tests/test_pymupdf4llm_eval.py -q` passes.
  - Commit: `test(pdf): assert what extraction does with a vector-only page` with `Closes #649`; its body says what
    the old test could not catch and where the red was shown.

## Documentation

- `docs/usage/figure-extraction.md`: the vector-figure sentence corrected (item 3).
- `docs/reference/schema.md` § `embed_truncations` (lines ~392-396) says ingest "cuts a long figure, image or
  element-capture description by design and records nothing": rewrite it to say the cut is recorded with the
  vector, by ingest as by backfill and `redact --rewrite` (item 2's commit).
- `docs/usage/ingesting-documents.md` § "Inputs longer than the embedder takes", the figure row: add "the cut
  recorded with its vector" (item 2's commit).
- No public API changes: `embed`'s new keyword is internal and `_embed_with_config` is private.

## Testing

- Unit/integration: the four new #648 tests at the httpx transport (real provider, real `/tokenize` counting,
  real SQLite), and the rewritten #649 test against real pymupdf4llm. Red shown before each green.
- Existing #620/#638 truncation tests guard item 1's refactor.
- E2E: N/A: no server or MCP surface changes; the record is read by the existing `truncated_chunks` reports.

## Verification

At the final clean HEAD, unpiped, pytest under `flock /run/user/1000/memlane-heavy.lock`:

- `uv lock --check`
- `uv run ruff check src/ tests/`
- `uv run ruff format --check src/ tests/`
- `flock /run/user/1000/memlane-heavy.lock uv run pytest tests/ -q -m "not slow"`
- `uv sync --all-groups && uv run basedpyright src/ tests/`
- `uv run --group docs sphinx-build -W -E -q -b html docs docs/_build` (AGENTS.md's gate; three docs pages change)
- `flock /run/user/1000/memlane-heavy.lock uv run pytest tests/test_pymupdf4llm_integration.py -q` (slow, touched
  neighbour of item 3).

## Self-check

- #648's acceptance → item 2's red tests; #649's acceptance → item 3 (assert + two reds); #651's → one commit and
  one `Closes` per member.
- Follow-up to report, not fold in: migrate the ~250 `ingest.embed` fakes to one provider seam so
  `_embed_with_config` can reuse `space_embedder`.

## Resolution

Lean review: agy `gemini-4-hydrogen-medium` (one-shot, `--mode plan`), planning-review severity Default. A first
attempt ended with no review (agy's headless mode denied a shell command); the second, told to use file tools only,
completed. No BLOCKER or HIGH.

- **LOW — `_extract_element_captures` indexes captures with gaps (`cap_idx`), the cuts by batch position.**
  Accepted and revised: item 2 now keys every cut by embedded-batch position, names that rule for element captures,
  and makes the element-capture test skip its first capture so a cut keyed by `cap_idx` fails it.
- **MEDIUM — `## Verification` omits AGENTS.md's sphinx gate while three docs pages change.** Accepted and
  revised: the `sphinx-build -W` step is added. `docs/plans` is in `docs/conf.py`'s `exclude_patterns`, so the plan
  file itself does not trip it.
