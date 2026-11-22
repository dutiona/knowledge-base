Reviewer: agy gemini-4-hydrogen-medium via agy -p --mode plan (one-shot)

~~~~
Plan reviewed: [2026-11-22-651-figure-truncation.md](file:///home/michael-roynard/dev/knowledge-base/.worktrees/fix-651-figure-truncation/docs/plans/2026-11-22-651-figure-truncation.md).

**Conclusion:** Sound plan; ready to implement after adding the missing Sphinx gate to `## Verification`. No `BLOCKER` or `HIGH` defects found.

---

### 1. Correctness (writers, positions, space, trigger, dedup, Matryoshka, transactions)

- **All four figure writers covered:**
  - [vision.py:_persist_figures](file:///home/michael-roynard/dev/knowledge-base/.worktrees/fix-651-figure-truncation/src/knowledge_base/vision.py#L1935-L2072) builds `all_figures` and `texts` in lockstep, embeds once via `_embed_with_config`, and passes `cut=cuts.get(i)` to `_insert_chunk`.
  - [web.py:_extract_html_images](file:///home/michael-roynard/dev/knowledge-base/.worktrees/fix-651-figure-truncation/src/knowledge_base/web.py#L439-L451) and [web.py:_extract_web_figures](file:///home/michael-roynard/dev/knowledge-base/.worktrees/fix-651-figure-truncation/src/knowledge_base/web.py#L755-L793) pass `cuts.get(idx)` as the 5th element of each figure tuple to `_replace_figures`.
  - [ingest.py:_insert_chunks](file:///home/michael-roynard/dev/knowledge-base/.worktrees/fix-651-figure-truncation/src/knowledge_base/ingest.py#L473-L493) also unpacks `(embeddings, cuts)` and forwards `cut=cuts.get(i)` to `_insert_chunk`.
  - No other `_embed_with_config` or `_insert_chunk` callers exist in `src/`.
- **LOW — Index subtlety in `_extract_element_captures`:**
  - **Where:** [web.py:_extract_element_captures](file:///home/michael-roynard/dev/knowledge-base/.worktrees/fix-651-figure-truncation/src/knowledge_base/web.py#L830-L894)
  - **Evidence:** `collected` holds `(desc, meta, cap_idx)` where `cap_idx` is the index in `captures` (which can have gaps when captures are missing or vision fails), whereas `texts` and `cuts` are indexed by `0..len(collected)-1`.
  - **Fix:** When building the 5-tuples for `_replace_figures`, index `cuts.get(idx)` with `enumerate(zip(collected, embeddings, strict=True))` while keeping `_WEB_ELEMENT_CAPTURE_CHUNK_INDEX_START + cap_idx` for `chunk_index`.
- **Space, vector-only gating, Matryoshka, dedup, triggers, transactions:**
  - Checking `embedding is not None` in `_insert_chunk` before calling `insert_chunk_vec` and `record_truncation` ensures a chunk whose Matryoshka prefix (or whole vector) has zero norm gets no `embed_truncations` row, matching `backfill_space`.
  - Resolving the space from `vec_table or get_vec_table_name(conn)` against `embed_spaces.table_name` records against the space that owns the written vec table.
  - Dedup skips (`SELECT id FROM chunks WHERE content_hash = ?`) continue before `_insert_chunk`, so existing chunks are not overwritten.
  - `delete_chunks_cascade` in `_persist_figures` and `_replace_figures` removes old figure chunks, and `embed_truncations.chunk_id` (`ON DELETE CASCADE`, [migrate.py](file:///home/michael-roynard/dev/knowledge-base/.worktrees/fix-651-figure-truncation/src/knowledge_base/migrate.py#L110-L115)) clears their old truncation rows in the same transaction.
  - `chunks_au_truncations` ([migrate.py](file:///home/michael-roynard/dev/knowledge-base/.worktrees/fix-651-figure-truncation/src/knowledge_base/migrate.py#L118-L120)) only fires on `UPDATE OF content`, not `INSERT`.
  - All writers wrap `delete_chunks_cascade` + `_insert_chunk` + `record_truncation` in one transaction with `rollback()` on failure.

---

### 2. Design (`embeddings.embed(..., cuts=)` vs alternatives)

- **Cost claim verified:** Searching `tests/` shows ~207 `@patch`/`monkeypatch` sites targeting `knowledge_base.ingest.embed` across 16 test files, plus [stub_document_embedder](file:///home/michael-roynard/dev/knowledge-base/.worktrees/fix-651-figure-truncation/tests/conftest.py#L300-L313) used across ~35+ tests. Spot-checked fakes (`_embed` in `conftest.py`, `_fake_embed` in `test_ingest.py` and `test_vision.py`) all accept `**_kwargs`.
- **Justification:** Adding `cuts: Cuts | None = None` to `embeddings.embed` keeps the `ingest.embed` patch seam intact while `_embed_with_config -> tuple[vectors, cuts]` makes dropping cuts a `basedpyright` type error. All test callers/mocks of `_embed_with_config` are accounted for (`test_ingest.py:3664` raises `RuntimeError` so it needs no edit; `test_redact_store.py:597`, `test_embeddings.py:508`, `test_embed_swap.py:363`, `test_vision.py`, `test_multi_figure.py`, `test_vision_omniparser_integration.py` are all listed in the plan).

---

### 3. Tests & #649 Acceptance

- **#648 tests:** Both `test_a_pdf_figure_stored_cut_records_its_truncation` and the 3-way parametrized `test_a_web_figure_stored_cut_records_its_truncation` will be genuinely red (`embed_truncations` rows `[]`) before the change, and exercise real `OpenAICompatProvider._bounded` + `/tokenize` via `bounded_embedder`.
- **#649 test and red proofs:** Replacing `test_vector_drawing_output_documented` ([test_pymupdf4llm_eval.py](file:///home/michael-roynard/dev/knowledge-base/.worktrees/fix-651-figure-truncation/tests/test_pymupdf4llm_eval.py#L325-L343)) with `test_a_vector_only_page_yields_an_extracted_image` calling production `_extract_pdf_markdown(pdf, image_dir)` and asserting `"Architecture Overview"` + `IMAGE_REF_RE` + existing files in `image_dir` tests behavior rather than incidental placeholder text. Both planned red demonstrations (`pymupdf4llm 1.27.2.2` in scratch and `write_images=False` mutant) are valid.

---

### 4. Missing items, docs, verification gaps, scope creep

- **MEDIUM — Missing Sphinx build in `## Verification`:**
  - **Where:** [2026-11-22-651-figure-truncation.md](file:///home/michael-roynard/dev/knowledge-base/.worktrees/fix-651-figure-truncation/docs/plans/2026-11-22-651-figure-truncation.md#L122-L132) vs [AGENTS.md](file:///home/michael-roynard/dev/knowledge-base/.worktrees/fix-651-figure-truncation/AGENTS.md#L27)
  - **Evidence:** The plan edits three documentation files (`docs/usage/figure-extraction.md`, `docs/reference/schema.md`, `docs/usage/ingesting-documents.md`), but `## Verification` omits the `AGENTS.md` merge-gate step `uv run --group docs sphinx-build -W -E -q -b html docs docs/_build`.
  - **Fix:** Add `uv run --group docs sphinx-build -W -E -q -b html docs docs/_build` to `## Verification`.
- **Docs & scope:** All three stale doc locations ([figure-extraction.md](file:///home/michael-roynard/dev/knowledge-base/.worktrees/fix-651-figure-truncation/docs/usage/figure-extraction.md#L15), [schema.md](file:///home/michael-roynard/dev/knowledge-base/.worktrees/fix-651-figure-truncation/docs/reference/schema.md#L392-L396), [ingesting-documents.md](file:///home/michael-roynard/dev/knowledge-base/.worktrees/fix-651-figure-truncation/docs/usage/ingesting-documents.md#L398)) are identified and updated in the right commits; no scope creep.

REVIEW COMPLETE
~~~~
