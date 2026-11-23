# Implementation audit (pre-review): #660

- Approved plan: docs/plans/2026-11-23-embed-key-file.md, SHA-256 f44e73583bdcc497d3a83dc0d0d7dfb5d67eaa836b96b7b59e0cc23292162e8f (approved on #661)
- Implementation head audited: 44deb009a1d2c1ec9af16517a153c5be115b2e2d
- Scope: pre-review

| Item | Status | Evidence |
| --- | --- | --- |
| 0. Worktree, plan issue, baseline | Implemented | plan issue #661; baseline `pytest -m "not slow"`: 2048 passed, 4 skipped |
| 1. Guard: no LLM key spec names a file | Implemented | 7bfb1d8; red 3 failed, green `tests/test_llm.py` 78 passed |
| 2. Guard: refuse an env key the HTTP client would echo | Implemented | 107263b; red 6 failed, green 7 passed |
| 3. Feature: KNOWLEDGE_BASE_EMBED_API_KEY_FILE | Implemented | 15d39ac; red 21 failed + 1 green-on-arrival (`--help`), green 22 passed; full suite 2080 passed, 4 skipped |
| 4. Documentation | Implemented | 44deb00; sphinx `-W` exit 0; `rg -c` names all five files; stale-claim `rg` prints nothing |
| 5. Gate and PR | Incomplete (next step) | runs at the final head |
| 6. /super-review | Incomplete (next step) | after the PR |
| 7. Merge | Owner only | not this worker's step |

Counts: Implemented 5, Modified 0, Dropped 0, Added 0, Incomplete 2 (operational, next in sequence), owner-only 1.
Divergence: 0 of 8 (0%).

Intent-preserving variations (not Modified):
- Item 4 / decision 10: the plan said "add one `#660:` sentence" to ADR-0018's key-source bullet and also required the
  stale-claim `rg` check to print nothing. Appending a sentence left "read only from" in place, so the bullet's first
  sentence was rewritten to name both sources, with the `(#660)` marker; the rest of the ADR is unchanged. The `rg`
  check excludes `docs/plans/`, whose working files quote the phrase and are removed at `/finish-pr`.
- Item 3 tests: the both-set message is pinned as "... and KNOWLEDGE_BASE_EMBED_API_KEY are both set", and a blank key
  beside a file is its own test (`test_a_blank_key_beside_a_key_file_is_not_both`), as decision 7 describes.
- Hand smoke (Verification): swapped variable, empty file and both set each exit 78 with the key absent from stderr
  and stdout (`grep -c` 0), and no database file created.
