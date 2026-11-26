Reviewer: Claude Sonnet — Codex-seat stand-in, clean slate, run by the orchestrator of the dutiona/infra#37 lane (the
worker's own stand-in path through agy was denied headless). It reviewed plan sha256 068fb954…2fcc (revision r10), which
the reviewer verified itself.
Altitude report SHA-256: a612d8d92955ca4f2ba0e566407236d0b21f43a3b7dc1a9259591d3cf36e9cca
(the report bound to those exact plan bytes; the reviewer's inputs were the plan, the issues and the code, so this line
records the artifact pair, not a sentence the reviewer wrote.)
The reviewer's own closing line is `VERDICT: LGTM`; its prompt did not use this skill's `REVIEW COMPLETE` sentinel. The
marker at the end of this file is the worker's manifest line, labelled as such, not a reviewer sentence.

~~~~
Reviewer: Claude Sonnet (Codex stand-in), plan sha256 068fb954… (round 2; round 1 was 805bcda6…)

Verified: `sha256sum` of docs/plans/2026-11-26-kb-config-tail.md = 068fb954f2337555fe26437a8a2ac65831233637f0db5533a631c48979252fcc (matches), 389 lines.
Checked the worker's resolution claims against the plan text and the code (routes/extraction.py, llm.py, tests). Re-measured the #664 quadratic double (below). Nothing edited except this file.

## Status of round-1 findings

- F1 HIGH (scope, removal layer): RESOLVED. `without_secret*`, `_mask`, `_free_key`, `_without_sent_key`, `configured_key` and their twelve tests are gone; Step 3 is now `error_text` plus its call sites. Residual 2 (endpoint echo) is stated with its reasoning, in the plan and Testing "Excluded".
- F2 HIGH (placeholder-key corruption): RESOLVED by deletion. The plan now names the placeholder-key failure as the reason the layer was dropped.
- F3 MEDIUM (fail-open in removal): RESOLVED, moot with the layer gone.
- F4 MEDIUM (MCP route not a sink): RESOLVED. Table row 7, Decision 6, and `test_the_inline_extraction_tool_names_no_endpoint` driving `extract_structure_tool`; the protection is genuinely the rendering at :750, which the test exercises with the real composition (only `_llm_call` patched). See N1 for the route edit itself.
- F5 MEDIUM (old rows): RESOLVED as an explicit accepted residual (residual 1, row 10) with the operator step promised in the PR body. Rationale (no blanket UPDATE on evidence) is reasonable and it is no longer silent.
- F6 MEDIUM (connectivity warning rewrite / httpx INFO log): RESOLVED. Rewrite dropped, test 6 dropped, httpx INFO log recorded as residual 3 and the Outcome no longer claims "no endpoint in a log".
- F7 MEDIUM (re-serialisation of every answer): RESOLVED, moot.
- F8 LOW (#664): RESOLVED. Plain `< 2.8`, no retry, no linear control, quadratic double kept. My probe data (idle 1.72-2.30, loaded 1.89-2.15, 42 pairs) gives 22 % headroom below 2.8; the #592 regression at 3.52 gives 25 % above it, so the bound sits near the middle. I also re-ran the `_rescan` double at 2**13 / 2**14, 8 times: ratios 3.81-3.97, about 12 ms at N, so the "must raise" test is stable with ~36 % margin over 2.8.
- F9 LOW (`import json`, `llm.__all__`): RESOLVED, moot.
- F10 LOW (Step 1 no failing test): unchanged, acceptable for a pure refactor with #660's suite as proof.

## New findings from the revision

### N1 LOW — a deliberate no-op line in the route
Section: Step 3, `routes/extraction.py`.
The plan calls `result = {"error": error_text(e)}` "a no-op for an ExtractionError" yet adds it plus an import "so the caller-facing JSON is built by the same rule". `e` is always an `ExtractionError`, so the call cannot change behaviour, it adds a dependency edge, and the test passes without it (it turns green from :750). By the repo's own "no defensive code for states that can't happen", drop the route edit and keep row 7 as "covered at its producer, pinned by the test". Optional; harmless if kept.

### N2 LOW — red-count wording
Step 3 says "those six cases -> 6 failed". Test 1 is parametrised (2 cases) plus tests 2-5 = 6, consistent. No change; noted only that test 4 (CLI stderr) depends on `stub_document_embedder.down = 503` producing an httpx status error today, which the worker should confirm when writing it (it is the one red not visible from the sources I read).

### N3 LOW — bound without retry on a shared runner
A single-sample 2.8 bound has no tail protection; my 84 samples max 2.30, and the 40-way contention run did not raise it, so I judge the flake risk acceptable. The plan's own wording ("measured headroom, not a proof") is the honest claim; keep it in the PR body.

No security regression found: a malformed key is refused at configure, at every config read and at the three header constructions before any header exists, and every named sink renders httpx errors by class. The remaining exposures are the three stated residuals.

VERDICT: LGTM
~~~~

## Resolution

Reviewed bytes: plan r10, sha256 `068fb954f2337555fe26437a8a2ac65831233637f0db5533a631c48979252fcc`; altitude report
`a612d8d9…` (both verified). The reviewer's round-1 findings F1–F9 are all RESOLVED by r10, F10 stands unchanged, and it
found no security regression. Its three new notes are LOW and, per Michaël's relay, are handled at implementation time
rather than by changing the plan now:

- **N1 (the route's no-op `error_text(e)` line)** — decided at Step 3: the line is dropped if it stays a no-op (it will,
  since the handler only ever catches `ExtractionError`), and path-table row 7 keeps its real meaning — "covered at its
  producer, pinned by the test". The test and the table row stand either way.
- **N2 (the CLI red)** — confirmed at Step 3 when the test is written: `stub_document_embedder.down = 503` raises
  `embedder_error(503)` (`tests/conftest.py:260-267`), an `httpx.HTTPStatusError`, so the new assertion is red at the
  parent and green after `error_text` reaches `indexer.py`.
- **N3 (single-sample 2.8 bound)** — accepted as the reviewer judged it (84 samples, max 2.30, unchanged under 40-way
  contention). The "measured headroom, not a proof" wording stays in the PR body.

Roster context for this artifact: the Full gate's Codex seat was degraded twice — Codex's CLI hit a usage limit at
round 8 (rounds 1–7 collected), and the worker's Claude Opus 5.5 stand-in through agy was denied the `command` tool on
three attempts with no review produced. The orchestrator therefore ran this clean-slate Sonnet review itself, and this
file is the Codex seat's actual review of the final plan bytes. agy `gemini-4-hydrogen-medium` reviewed every revision
including r10 (LGTM), in `…-external-agy.md`.

Manifest marker for `state.sh record-super-evidence` (the reviewer's own last line, `VERDICT: LGTM`, is reproduced inside
the fence above):
REVIEW COMPLETE
