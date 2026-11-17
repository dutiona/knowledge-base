Reviewer: Codex gpt-6.1-terra (config default) via codex exec (headless round, read-only)

~~~~
Michaël, the plan needs revision before implementation. Confidence: high in the findings below. I found no Blocker-severity findings. The production embedding call sites I inspected do pass through `declared_provider_config`; I found no bypass of the proposed provider bound.

1. **High — Decision 3, lines 62–67: binary search cannot guarantee the longest fitting prefix.** Token counts can decrease when a prefix is extended, as the plan itself acknowledges. Binary search can therefore skip a longer fitting prefix, and can report that nothing fits despite an unexamined position fitting. The plan also searches character positions only when *no whitespace position* fits, so it cannot find the longest prefix when a fitting word boundary is followed by part of a long word. Use an algorithm whose stated guarantees hold for non-monotone counts, or explicitly relax the longest-prefix requirement while still verifying every emitted piece and forward progress. Test a non-monotone counter and a long word following a fitting boundary.

2. **High — Decision 3 and item 3, lines 62–65 and 142–146: splitting can discard text.** The emitted piece is `text[:c].rstrip()`, but the plan says pieces preserve all text. Advancing past `c` discards the stripped whitespace; advancing by the emitted length instead requires explicit handling to avoid stalled or whitespace-only iterations. This affects meaningful whitespace in code and Markdown, while the proposed word-only assertion cannot detect it. Define lossless offsets and assert exact reconstruction of each original chunk, including newlines, indentation and trailing whitespace, while checking each emitted piece’s count.

3. **High — Item 3, lines 132–145: the prescribed red test cannot fail for the stated reason after item 2.** Item 2 already truncates over-length inputs in the provider, so the section ingest should no longer receive HTTP 400. It should fail because stored chunks remain over the limit or because their full text was not split before embedding. Make that the explicit red assertion, and separately establish the 400 baseline on master before item 2 if desired.

4. **High — Decision 1 and verification step 3, lines 31–36 and 183–185: the key tokenizer equivalence remains unverified at merge.** The source examined is not the deployed b12048, and a check scheduled after deployment cannot establish the acceptance claim for this PR. Moreover, an *over-length* embedding is expected to return 400, so it cannot supply the successful `usage.prompt_tokens` comparison described. Verify the deployed version or its exact source before claiming equivalence; compare `/tokenize` with successful embedding usage at and below the boundary, and separately verify rejection above it. If unavailable, record the acceptance claim as unverified.

5. **Medium — Item 3, lines 144–145: copied metadata can misdescribe split pieces.** PDF metadata is computed from the parent’s pages and image references before splitting; every child would inherit images and pages it may not contain. Python children likewise inherit the entire parent’s line range. Recalculate piece-specific metadata where the necessary offsets exist, or explicitly define and document it as parent-level provenance. Add coverage for a split containing an image reference and for Python line metadata.

6. **Medium — Decision 4 and item 3, lines 72–76 and 142–143: the #573 rebase is more than the stated one-line change.** The proposed `_bounded_chunks` passes a provider-family string, whereas the #573 branch’s `declared_provider_config` requires a space mapping and verifies its identity. Update the rebase instructions to obtain the active space for ingest and pass the target space for backfill and promotion; test that the selected space’s recorded limit governs each path.

7. **Medium — Verification, lines 176–181: the specified merge gate omits `uv sync`.** The repository instructions require it after `uv lock --check` and before lint and tests. Without it, those checks may use stale installed dependencies. Restore the full prescribed sequence, including `uv sync`, and retain the final clean-head requirement.

I found no Low-severity findings.

REVIEW COMPLETE
~~~~
