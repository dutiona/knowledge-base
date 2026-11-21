Reviewer: Codex gpt-6.1-terra (medium) via codex-headless-round.sh, resumed thread 01a1ff78-d8f1-7c21-a3e9-046b710fdfe8

~~~
LGTM, Michaël. The single-line publication change introduces no new problem in the reviewed delta. Confidence is high. The altitude report’s **proceed** disposition remains justified.

Altitude report SHA-256: 9fc37f524a3ef1e69f2d63c22409e24600fbf055a0ff8f701faef8e6a1a048f9

REVIEW COMPLETE
~~~

## Resolution

Reviewer ran headless on `gpt-6.1-terra` at `medium` effort (Michaël, 2026-11-21: "if you need codex, gpt 6.1 terra only"), resuming one thread across five rounds. No degradation. Every finding of every round, with its disposition:

Round 1 (plan `b316c8b2…c20b`):
1. [HIGH] read-only commands could initialize a store while a writer holds the lock (`status`/`redact` report run `init_schema`). Accepted: D2 adds the read-only guard (refuse a missing store and any store `peek_schema_version` does not report current) before `init_schema`; the false "a held store is always stamped" bullet was deleted; item 1 adds `test_a_read_only_command_never_initializes_a_store`.
2. [HIGH] the lock could be released while the job worker still writes. Accepted: D2 adds `jobs.shutdown_worker()` (stop flag, join without timeout) inside the lock scope on every exit path; item 1 adds `test_the_lock_outlives_a_running_job`.
3. [HIGH] a deprecated space could become unpromotable with impossible advice. Accepted as a remedy fix: the pending refusal for a deprecated space names `knowledge-base-ingest re-embed`, the documented rollback; `backfill_space` stays populating-only (no new write path). Item 7 adds `test_a_deprecated_space_with_pending_chunks_names_re_embed`.
4. [HIGH] a refusal or vector could describe obsolete content. Accepted: each backfill batch writes in one `BEGIN IMMEDIATE` transaction that re-reads `(id, content_hash)` and writes only for unchanged chunks; item 7 adds `test_backfill_writes_nothing_for_a_chunk_changed_while_it_embeds`.
5. [MEDIUM] `remove` landed before the every-space vector fix. Accepted: the fix is item 3, before `remove` (item 4), whose URL test backfills a populating space and checks both spaces; D8 explains the order.
6. [MEDIUM] the hygiene check matched itself. Accepted: added lines only, patterns `/h[o]me/|\.ts\.n[e]t`.
7. [MEDIUM] the container check raced startup. Accepted: bounded readiness wait, running check, cleanup trap.

Round 2 (plan `c101049b…f3ee7`):
8. [HIGH] the cleanup trap could delete a pre-existing container and volume. Accepted: per-run names and an explicit volume create (completed in round 4, finding 11).
9. [MEDIUM] the report connection's `PRAGMA journal_mode=WAL` can still write. Arbitrated, not changed (Codex accepted the arbitration in round 3): every KB writer opens through `get_connection`, WAL persists in the file, so the PRAGMA is a no-op on any store a KB process holds and never meets a lock holder; a second read-only connection helper buys nothing reachable. D2's promise was narrowed to "never create, bootstrap or migrate a store", with the reason stated.
10. [MEDIUM] readiness and expected results were not enforced. Accepted: tracked readiness that fails on timeout, an `expect` helper asserting each exit code and holder text, a final `container check: PASS`.

Round 3 (plan `fef61c81…6b6f`):
11. [MEDIUM] unique-looking names do not prove ownership. Accepted: an unpredictable `secrets.token_hex(6)` name, and the trap removes the container and volume only when this run created them (`made_ctr`, `made_vol`).

Round 4 (plan `59d8cf92…3328`): LGTM, no finding.

Round 5 (plan `93a3fc1a…488f`, the final bytes): LGTM, no finding. The one changed line makes item 0 name "this plan, under `docs/plans/` on the branch" instead of its own local path, which the plan-issue archiver refuses.

Altitude: Codex acknowledged the `proceed` disposition on every round's report (rounds 1 to 5, the last `9fc37f52…48f9`).
