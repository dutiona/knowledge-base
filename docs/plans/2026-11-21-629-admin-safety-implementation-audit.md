# Implementation audit (pre-review): 2026-11-21-629-admin-safety

- Approved plan: `docs/plans/2026-11-21-629-admin-safety.md`, sha256 `93a3fc1aa8b55a7090a9ae7d754f5a341f3ed64152634c9cfc2cb7a0c0d8488f`
  (approved on plan issue #630, 2026-11-21T19:14:57Z; owner calls O1 exclusive, O2 the schema-v3 table).
- Audited head: `4ba39cb338857d289c2fbcf5b84eb573c3bc6140` (scope: pre-review).

| Item | Status | Evidence |
| --- | --- | --- |
| 0. Plan on the branch | Implemented | `ccd44a0` |
| 1. #596 the store lock | Implemented | `8ac7868`: `store_lock.hold`, `server.main` order, `indexer._read_only`, `jobs.shutdown_worker`, the read-only guard; tests in `test_store_lock.py`, `test_server.py`, `test_indexer.py` |
| 2. #578 holder record, both orders | Implemented | `d01fc41` |
| 3. every-space vector deletion | Implemented | `640fdad` |
| 4. #595 remove | Implemented | `4cf0284` |
| 5. figure sites through the owner | Implemented | `7ee1ef1` (figure tests: 484 passed before and after) |
| 6. dead chunk_strategy branch | Implemented | `daf8ea8` |
| 7. #579 refusals and the pending rule | Implemented | `4ba39cb` |
| 8. merge gate and container check | Implemented at the final head (results in the PR) | — |
| 9. PR, review, finish | In progress after this audit | — |

Counts: Implemented 9 (items 0-8), in progress 1 (item 9, the review lifecycle), Modified 0, Dropped 0, Added 0.
Divergence: 0% (Modified + Dropped + Added = 0 of 10).

Intent-preserving variations, recorded, not Modified:
- Item 7's trap test is `test_a_space_whose_only_gaps_are_refusals_is_promoted` through `promote_space` (the step of
  `re_embed` that the trap breaks), not a `re_embed` call; the planned
  `test_a_rewritten_chunk_loses_its_refusals` is the content-update case of
  `test_a_refusal_ends_with_its_chunk_its_content_or_its_space`.
- Item 7 also returns `chunks_refused` from `re_embed` (D7 names it for the backfill result only).
- Item 2's server-during-transition test starts the stdio server inside the transition (the lock refusal comes
  before the transport, so the transport does not change the case).
- Item 1's `test_the_lock_creates_the_database_directory` was added beside the planned lock tests.
