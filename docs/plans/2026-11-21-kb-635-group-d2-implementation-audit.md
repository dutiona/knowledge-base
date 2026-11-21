# Implementation audit (pre-review): 2026-11-21-kb-635-group-d2

- Scope: pre-review
- Approved plan: `docs/plans/2026-11-21-kb-635-group-d2.md`, SHA-256
  141d6db124319ad71d00fc5b1632cec08be87f6e6efd5d233c49b2b81bf74a60 (approved on plan issue #636)
- Implementation audited: branch `chore/635-docs-release-embed` at 35e761a (base 528d170)

| Item | Status | Evidence |
| --- | --- | --- |
| 0. Worktree, plan issue, sibling check | Implemented | Plan issue #636; `git fetch origin` before items 1 and 6: no #634 branch or PR pushed yet, `origin/master` still 528d170 |
| 1. #576 toctrees and `-W` | Implemented | df6f16a; the docs line exits 0, and a new orphan page fails it (exit 1, `toc.not_included`) |
| 2. Boy-scout: the `ci.yml` mirror sentence | Implemented | 3643f77; `grep -c 'It mirrors'` gives 0 in all three files |
| 3. #617 image step | Implemented | 6a9e4bf; forced rehearsal under the heavy lock: success exits 0 under zsh, a failed smoke exits 1, a failed removal exits 1, no image or container left; the guard gives 0 on this branch and 1 on the last `uv.lock` change |
| 4. #586 release gate | Modified | 853250a. Per Michaël's ruling after approval ("ok, if and only if they are actually useful tests"), the slow-test line deselects two of the ten slow tests judged not useful (`test_fallback_on_import_error` duplicates a fast test; `test_real_pdf_image_refs` passes whether or not image references come out). Intent unchanged: Step 2 refers to the Verify list and keeps the useful slow tests |
| 5. #605 removal | Implemented | 25c4298; the signature check prints `ok`, basedpyright reports 0 errors, and the removed-name grep prints nothing. The three converted tests take names that say what they now check |
| 6. #620 record and report | Implemented | 35e761a; the five new tests went red (3 `KeyError: 'truncated_chunks'`, 2 missing table) then green; the ten affected test files pass (362) |
| 7. PR, review, finish | Pending | This audit precedes the PR |

Added outside the items: a branch commit 5d2d9b3 carrying the plan artifacts (removed by `/finish-pr`), placed after
item 1 so every commit passes `-W`.

Counts: 6 Implemented, 1 Modified, 0 Dropped, 0 Incomplete, 0 Added, 1 Pending, out of 8. Divergence (Modified +
Dropped + Added) is 1/8 = 12.5%, under 30%. The one modification is the owner's own post-approval direction, recorded as
a deviation in the PR body. No successor plan.
