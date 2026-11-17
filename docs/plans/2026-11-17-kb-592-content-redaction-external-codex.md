# [Codex Review] plan review rounds 1-4 (gpt-6.1-terra, headless, thread 01a1ea52-3a46-7562-aa21-d2a593909631)

## Round 1

Michaël, the plan needs revision before implementation. Confidence: high.

The altitude report’s **proceed** disposition is justified: the plan’s detail addresses distinct porting, ingestion, and repair decisions. I found no duplicate implementation that warrants a lower-altitude alternative. Documentation, Testing, and Verification sections are present.

Altitude report SHA-256: afdd9300c8d2b5f66e764aec05aa12b73aa1bfe1d4ca0ae15af52ce625597f39

- **[BLOCKER] Interrupted rewrites can report clean while old secret bytes remain.** Maintenance command steps 3–5 and item 11 commit redacted rows and delete summaries before rebuilding summaries and purging. If the process stops after the commit, the next report can find no matching rows and exit 0; a rerun also has no durable record of which summaries need rebuilding. Old bytes can remain in free pages, FTS segments or the WAL, despite the promised clean gate. Persist recoverable work or otherwise make completion and purge detectable across interruption; test interruption after each committed stage. See [plan](.review/plan.md:103).

- **[HIGH] Folder summaries can reintroduce and embed a secret from a filename.** Items 4, 11 and the identity exception leave file paths unchanged, while [`_build_folder_summary`](src/knowledge_base/folder_summaries.py:69) incorporates the filename and [`update_folder_summary`](src/knowledge_base/folder_summaries.py:106) embeds and stores that summary. A token-shaped filename therefore reaches the embedder and survives a rebuild; the report will flag it again. Keep the identity unchanged, but scrub the assembled summary before embedding and storage, with an ingest and rewrite regression case.

- **[HIGH] Rewrite does not maintain embedding-space counts.** Maintenance command step 3 deletes vectors and merges chunks without specifying corresponding `embed_spaces.chunk_count` adjustments. [`backfill_space`](src/knowledge_base/embed_swap.py:268) increments that count, and [`_check_promotable`](src/knowledge_base/embed_swap.py:387) relies on it. A populating space can consequently appear complete with missing vectors. Item 11 should reconcile counts transactionally and test promotion before and after backfill.

- **[HIGH] The stated Unicode divergence can leave a provider token unredacted.** Item 1 uses Python `str.isalnum()` while Verification V2 says the divergence only moves span edges and never affects whether an ASCII token is caught. The Rust detector includes other alphabetic characters in its body and applies its minimum to the entire captured UTF-8 byte length ([source](.review/inputs/me-redact-97c8e77.rs:315)). If one such character occurs before the Python capture reaches 20 bytes, Python rejects the whole prefixed candidate while Rust can accept it. Port Rust’s character classification faithfully and add a threshold case; a finite differential sample cannot establish equivalence.

- **[MEDIUM] Reingest’s proposed deduplication loses associations.** Item 5 changes the dedup probe and flushes deferred session links, but reingest subsequently finds replacement chunks by the original `source_uri` ([ingest.py](src/knowledge_base/ingest.py:744)). A shared chunk retains the other document’s URI, so papers and extracted entities can remain unlinked. Deferred links also receive only the supplied `session_id`, not all historical sessions. Carry the deduplicated IDs into relinking and preserve all relevant session associations, with assertions for both.

No tests, builds, gates, or mutations were run, as required by the review instructions.

REVIEW COMPLETE
## Round 2

Michaël, the delta addresses the main round 1 risks, but three details need correction before the plan is ready. Confidence: high.

The refreshed altitude report’s **proceed** disposition remains justified: the added detail resolves concrete review findings without introducing a duplicate artifact.

Altitude report SHA-256: 04abf141de00dd60f8bb0b67d6d03c1935f6069a8bee7ca6033e67c7538249e5

- **[HIGH] Item 5 cannot relink from the proposed deferred IDs as described.** The plan says those IDs already carry document positions, but [`_produce_and_insert_chunks`](src/knowledge_base/ingest.py:404) returns only a list of IDs, populated only when `session_id` is non-null ([line 416](src/knowledge_base/ingest.py:416)). A normal reingest without a new session can therefore have no deferred IDs even when chunks deduplicate. Return the deduplicated IDs with their document positions independently of session linking, and test reingest with `session_id=None`.

- **[HIGH] The rewrite specifies contradictory treatment of metadata-only changes.** Maintenance command step 2 says those rows keep their vectors, while step 3 deletes vectors for *every changed row* ([plan](.review/plan.md:125)). Since step 2 produces no replacement embedding for them, following step 3 removes their active vectors. Restrict deletion to content-changed or merged rows and test metadata-only and URI-only rewrites in all spaces.

- **[MEDIUM] The new checkpoint preflight does not establish exclusive access.** Step 0 equates a successful `wal_checkpoint(TRUNCATE)` with “no other connection” ([plan](.review/plan.md:121)). Another connection can be idle, or start work immediately after the checkpoint; the check does not hold exclusion through the rewrite. Also, `open_declared_db` can write during configuration binding before this step ([db.py](src/knowledge_base/db.py:1280)). The server-stopped instruction remains necessary; remove the stronger guarantee or define an exclusion mechanism that covers opening through completion.

No tests, builds, gates, or mutations were run, as required by the review instructions.

REVIEW COMPLETE
## Round 3

Michaël, **LGTM**. The round 3 delta addresses the three round 2 findings, and I found no new gap in the changes. The refreshed altitude report’s proceed disposition remains justified.

Altitude report SHA-256: b0ed09eb3afca86dd7ba0647732a806cd63fd66bd7339d8f895591d5a5c25d6a

No tests, builds, gates, or mutations were run, as required by the review instructions.

REVIEW COMPLETE
## Round 4 (final)

Michaël, **LGTM**. I found no new gap in the round 4 delta. The refreshed altitude report’s proceed disposition remains justified.

Altitude report SHA-256: 9cd3ca320eb812f9827e175f0521cd7839d357522edbadefa753ed1669e3e1b4

No tests, builds, gates, or mutations were run, as required by the review instructions.

REVIEW COMPLETE
