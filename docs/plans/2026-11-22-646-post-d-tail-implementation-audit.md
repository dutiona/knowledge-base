# Implementation audit (pre-review): #646

- Scope: pre-review
- Approved plan: `2026-11-22-646-post-d-tail.md`, SHA-256 581dba179e63066dcb7c6bae5a7e028691f7a25226db8b52aca2ee0a536cded7
  (approved by Michaël's marker on #647, 2026-11-22T00:23:29Z)
- Audited head: daa6a7656c92db85c32d249ff22e21f662a1437f

| Item | Status | Commit | Note |
| --- | --- | --- | --- |
| 0. The plan | Implemented | 4fd1186 | |
| 1. #644 image-reference test | Implemented | 07caaf2 | Red on the #465 reproduction (1.27.2.3): control + old test 1 failed, 1 passed; control + rewritten test 2 failed. Green on 1.27.2.1: 4 passed, three runs, none skipped. |
| 2. Boy-scout: integration docstring | Implemented | ddd6d9e | |
| 3. Boy-scout: schema.md sentence | Implemented | b1da717 | The commit names #648, the figure-record follow-up Michaël filed. |
| 4. #638 rewrite records and reports cuts | Implemented | 0aa3900 | Red 2 failed; both mutation checks red; 303 passed across the listed modules. |
| 5. #643 failed-job rollback | Implemented | 15ea229 | Red 5 failed; rollbacks only 2 failed; the progress-commit mutation turns the visibility guard red; 60 passed. |
| 6. Boy-scout: core concepts | Implemented | 322b301 | |
| 7. #639 Verify copies test | Implemented | 8e46e78 | |
| 8. #640 dead defaults | Implemented | daa6a76 | basedpyright 0 errors. |
| 9. Ship | In progress | | PR, /super-review, /finish-pr follow this audit. |

Counts: Implemented 9, Modified 0, Dropped 0, Added 0, Incomplete 0 (item 9 in progress) of 10. Divergence 0%.

Intent-preserving variations (not Modified):

- Item 7: the plan expected the "bodies emptied in all three" red to give 3 failed, 2 passed. It gives 5 failed,
  because the comparison test reads each section through the same helper, which refuses an empty one. Stricter, same
  intent.
- Item 5: the fakes take `on_progress` keyword-only with no default, so basedpyright accepts calling it; the worker
  always passes it.
- Item 4: `space_embedder` takes `Mapping[str, Any]`, as `backfill_space`'s row dict and the active-space TypedDict
  both are.
- Item 1: each reference must name a file whose parent is the image directory, as the plan's "a file in image_dir".
