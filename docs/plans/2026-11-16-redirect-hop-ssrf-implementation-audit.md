# Implementation audit (pre-review): 2026-11-16-redirect-hop-ssrf

- Approved plan: `docs/plans/2026-11-16-redirect-hop-ssrf.md`, SHA-256
  `7f2a54eeb798c4bdd06d5a3fbd52817e378af744bf0c84d5e678089ee508d8f4`. Approved on #587.
- Audited head: the commit adding this file's final revision (branch `fix/572-redirect-hop-ssrf`); the gate below
  was re-run there.
- Scope: pre-review.

## Items

| Item | Status | Commit(s) | Note |
| --- | --- | --- | --- |
| 0 Worktree and plan | Implemented | 9a8fecc | |
| 1 Host addresses | Implemented | ea729bc | |
| 2 Floor refuses host addresses | Implemented | 77143bf | |
| 3 Pinned client | Implemented | 24eb7c3 | |
| 4 Web test migration | Implemented | 9e57363 | |
| 5 Pin URL ingest and images | Implemented | 1fde2b3 | |
| 6 LLM/vision test migration | Implemented | a987b16 | |
| 7 LLM floor and pin | Implemented | 4e09c3a | |
| 8 Vision and OmniParser floor and pin | Implemented | 52e9848 | |
| 9 CDP floor | Implemented | 6ccb6e1 | |
| 10 Docs and ADR amendment | Implemented | 1e53bda | |
| 11 Boy-scout docs | Implemented | 5ffa945, 1a7af44, 1daac8e | |
| 12 Gates and PR | Implemented | b54eeba | The gate run found one basedpyright error in a new test, fixed in its own commit. |
| 13 `/super-review` | Pending (lifecycle) | | |
| 14 `/finish-pr` | Pending (lifecycle) | | |

## Implementation variations

Each of these keeps the approved intent, so none is counted as Modified.

- **Item 3:** `_pinned` passes `content=request.read()` rather than the plan's `stream=request.stream`.
  - Why: the body is then in memory, so a retry on the next accepted address can resend it, and recorded
    requests are readable.
  - Found in item 7, when JSON-body tests read `request.content`.
- **Item 3:** the fallback across addresses is written without an `assert`, as earlier addresses plus a final
  one. Same behaviour.
- **Item 5:** `test_extract_html_images_rejects_redirect_to_private` was kept, not deleted. The item-4 migration
  turned it into a real 302 to `169.254.169.254`, so it now tests the transport's refusal rather than a mock.
- **Item 8:** `_ensure_omniparser_server(path, port)` keeps its port-parameterized URL, passing
  `floor=False` (KB's own child). The two ETA f-strings now go through `_omniparser_endpoint`.
- **Item 3 (recorder):** the plan's round-4 nit was adopted. The recorder takes its key and fallback from
  `raw_host`, so both are in ASCII form.
  - The planned self-test was first missed. This audit found the gap and closed it:
    `test_the_recorder_keys_a_request_by_its_host_header` in `tests/test_utils.py`.
  - It covers `name:port`, `[IPv6]:port`, a portless name and an IDNA name.

## Counts

| Status | Count |
| --- | --- |
| Implemented | 13 |
| Modified | 0 |
| Dropped | 0 |
| Added | 0 |
| Incomplete | 0 |
| Pending lifecycle | 2 |

Divergence is (M + D + A) / 15 = 0%, under the 30% threshold. No successor plan is needed.

## Verification at `b54eeba`

| Check | Result |
| --- | --- |
| `uv lock --check` | rc 0 |
| `ruff check src/ tests/` | All checks passed! |
| `ruff format --check src/ tests/` | 90 files already formatted |
| `uv run pytest tests/ -q -m "not slow"` | 1311 passed, 4 skipped, 10 deselected |
| `uv sync --all-groups && uv run basedpyright src/ tests/` | 0 errors, 0 warnings, 0 notes |
| `unshare -rn ... uv run --offline pytest tests/ -q -m "not slow"` (hermetic) | 1311 passed, 4 skipped |
| `rg 'knowledge_base\.(web\|llm\|vision)\.httpx' tests/` and `rg 'httpx\.(get\|post\|stream)\('` in web/llm/vision | no output |
| Startup refusal with `KNOWLEDGE_BASE_HOST_ADDRESSES=198.51.100.7/24` | `knowledge-base: KNOWLEDGE_BASE_HOST_ADDRESSES must list ...`, exit 78 |
| Real-network smoke | `200 https://github.com/ 1`; `http://localhost/` refused with the floor's message |
| Weaken-and-restore on the transport (SNI, `check_hostname=False`, default limits) | each red, restore proven by `cmp` |
