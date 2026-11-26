Reviewer: agy (gemini-4-hydrogen-medium) via agy review helper, resumed conversation 3d283668-5d01-48fd-848b-68d04a959b80, round 10 (final revision r10)

~~~~
Altitude report SHA-256: a612d8d92955ca4f2ba0e566407236d0b21f43a3b7dc1a9259591d3cf36e9cca

[Antigravity Review] LGTM — the reduced r10 plan satisfies both `#662` and `#664` without leaving any named path (rows 1–8) unguarded: `settings.require_key` / `_key_to_send` refuse malformed keys before any header is built, `utils.error_text` strips `httpx` text across `jobs.py`, `extraction.py`, `indexer.py`, and `routes/extraction.py` (pinned by `test_the_inline_extraction_tool_names_no_endpoint`), `#664`'s plain `2.8` ratio bound without retry or sub-second control is well-supported by the `1.72–2.30` loaded measurements and `3.52` pre-fix probe, and the accepted residuals (pre-existing DB rows, endpoint echoes, and `httpx` INFO logs) are stated honestly.

REVIEW COMPLETE

~~~~

## Resolution

Rounds: ten. Codex `gpt-6.1-terra` (headless) reviewed r1–r7 and then its CLI refused on a usage limit; agy
`gemini-4-hydrogen-medium` (resumed conversation) reviewed r1–r10, LGTM on r10; the Codex seat was then covered by a
Claude Sonnet clean-slate review run by the orchestrator (CHANGES REQUESTED, plan sha `805bcda6…`), whose F1–F10 and
Michaël's ruling produced r10: the exact-key removal layer is deleted.

| # | Round | Reviewer | Severity | Finding | Disposition |
| --- | --- | --- | --- | --- | --- |
| 1 | 1 | Codex | HIGH | a valid key echoed by the endpoint can reach a sink | r3 added an exact-key removal layer; **r10 removes it by owner ruling** and records the echo as an accepted residual (row 9), with the endpoint's own possession as the reason |
| 2 | 1 | Codex | HIGH | the connectivity warning names the endpoint | r3 changed it; **r10 reverts it by owner ruling** — the caller supplied the URL, `_sanitize_url` strips userinfo, and dropping the host costs diagnosis (Sonnet F6) |
| 3 | 1 | Codex | HIGH | the ratio check's reliability and detection | r3–r8 hardened it; **r10 simplifies to a plain 2.8 bound, no retry** (Sonnet F8's 42-pair evidence: 1.72–2.30 idle and under load, so the retry never fires); the sensitivity law and the measured #592 run (3.52 vs 2.03) stay |
| 4 | 1 | Codex | MEDIUM | the quadratic demonstration patches the wrong binding | r2: patch `tests.test_redact.scrub`, the name the test measures |
| 5 | 1 | Codex | MEDIUM | step 2's red count and the ollama-storage claim | r3: 14 failed / 1 passed cases; ollama stores a supplied key, so the rule runs on the prospective provider |
| 6 | 1 | agy | MEDIUM | the positive control (`str.count`) is sub-millisecond and sits in timer noise | r2 introduced `_sum_chars`; **r10 drops the control entirely** — the seven linear shapes are the positive case (Sonnet F8a) |
| 7 | 3 | Codex | NEW HIGH | successful jobs bypass the key removal | r4 added a completed-result walk; **r10 removes the walk with the layer** and records the success payload as an endpoint-echo residual (row 9) |
| 8 | 3 | agy | MEDIUM | the retry only fired below the bound (a no-op on failure) | r4 fixed the condition; **r10 removes the retry** |
| 9 | 4 | Codex | NEW HIGH | object keys bypass the walk | r5 walked keys; **r10 removes the walk** |
| 10 | 4 | Codex | NEW MEDIUM | `redact_json` also scrubs unrelated keys/values | r5 replaced it with a dedicated walk; **r10 removes the walk** |
| 11 | 4 | Codex | HIGH | the key sent can differ from a re-read key | r5 moved the scrub to the request site; **r10 removes the scrub** |
| 12 | 5–7 | Codex | HIGH ×4, MEDIUM ×2 | escape spellings, a dropped duplicate, a replacement boundary, a marker or suffix reproducing the key, a broad byte-identity promise, a collision dropping data | r6–r8 each answered one; **all moot in r10** — the owner's ruling ends the escape-spelling arms race by deleting the layer instead of matching it |
| 13 | — | Sonnet (Codex seat) | F1 HIGH | the removal layer is out of scope, ~110 lines, and incoherent with its own residual | **r10 takes it**: layer deleted, ~70 production lines remain, `require_key` + `_key_to_send` + `error_text` kept |
| 14 | — | Sonnet | F2 HIGH | the removal corrupts data for placeholder keys (`ollama`, `x`) that `_KEY_RE` accepts | part of the r10 ruling; the corruption argument is recorded in decision 4 |
| 15 | — | Sonnet | F3 MEDIUM | fail-open points in the removal path (tuples, a failed key read, a rotated key) | moot in r10 |
| 16 | — | Sonnet | F4 MEDIUM | `routes/extraction.py` returns `str(e)`, `errors`, `raw` to the MCP caller; no test drives the tool | **r10 adds it**: path table row 7, `error_text(e)` at the handler, `test_the_inline_extraction_tool_names_no_endpoint` |
| 17 | — | Sonnet | F5 MEDIUM | rows an older build already wrote are not addressed | **accepted residual 1**, with the operator step named in the PR body |
| 18 | — | Sonnet | F6 MEDIUM | the connectivity-warning rewrite is unrequested and httpx logs the URL itself at INFO | **r10 reverts the rewrite** and records the httpx INFO line as out-of-scope residual 3 |
| 19 | — | Sonnet | F7 MEDIUM | `_without_sent_key` re-encodes most real answers | moot in r10 |
| 20 | — | Sonnet | F8 LOW | #664 is sound; simplify (no retry, reconsider the control) | **r10 takes it**: plain 2.8, no retry, no control |
| 21 | — | Sonnet | F9 LOW | `utils.py` has no `json` import; `llm.__all__` mixes purposes | moot in r10 (both artefacts of the deleted code) |
| 22 | — | Sonnet | F10 LOW | Step 1 has no failing test | unchanged, and stated in Step 1 |
| 23 | 10 | agy | — | LGTM on r10: rows 1–8 guarded, the tool-result sink pinned, 2.8 supported by 1.72–2.30, residuals honest | final |

Degradations, named:
- Codex's round-8 invocation was refused by its CLI: "You've hit your usage limit … try again at 9:39 PM". Its seven
  rounds stand as review input; no Codex verdict claims the final bytes.
- The Claude Opus 5.5 stand-in through agy could not run headless in this worker (the `command` tool was denied on
  three attempts, no review produced); no permission rule was added, as the member requires. The orchestrator ran the
  Claude Sonnet clean-slate review itself instead, and its findings are rows 13–22 above.
