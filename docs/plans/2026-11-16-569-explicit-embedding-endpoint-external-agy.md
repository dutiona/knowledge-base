# agy external review (resume, one conversation), all rounds; the final round is last

## Round 1

Michaël, the implementation plan for #569 is comprehensive, structurally complete, and sound in its security architecture. Confidence: high.

Altitude report SHA-256: aca6edb96f6be734f2e2323385bf2c096f372a0b5ecb73f6f89dbadf8e8660c9

### Assessment & Altitude
The plan ([.review/plan.md](.review/plan.md)) is well-calibrated (375 physical lines, 19 tasks). It defines clear architectural boundaries (D1–D7), strictly respects the contract of issue #569 ([.review/issue-569.md](.review/issue-569.md)), and avoids over-engineering or premature abstraction. The documentation, testing, and verification sections are all present and detailed.

Security verification confirms that:
1. No document or API key can reach undeclared endpoints: startup halts at status 78 without explicit declaration, runtime writers (`configure_embeddings`) are removed, and key resolution is strictly per-request via `env:`.
2. The SSRF exemption cannot be abused by tool-supplied URLs: `exempt_host` is scoped strictly to exact-match on `OpenAICompatProvider.embed`, while `ingest_url`, `_validate_image_url`, and `configure_llm` continue to enforce the un-exempted `is_private_ip` floor.
3. Test sequencing ensures red tests genuinely fail against 8fa69bd, and the autouse declaration keeps the full suite green across intermediate commits.

Three low-severity clarity points are noted below:

[Antigravity Review] [LOW] Plan item 4 and Table D1 ([.review/plan.md#L56](.review/plan.md#L56)): `KNOWLEDGE_BASE_EMBED_URL` grammar states "trailing `/` dropped, then `/v1` for the two OpenAI families", which is ambiguous as to whether `/v1` is stripped or appended. To match `OpenAICompatProvider` ([src/knowledge_base/embeddings.py#L190](src/knowledge_base/embeddings.py#L190)) and avoid false mismatches during startup binding against existing databases (which record `embed_base_url` without `/v1`), the implementer should explicitly specify stripping via `.rstrip("/").removesuffix("/v1")`.
[Antigravity Review] [LOW] Plan item 7 and D7: Legacy `get_provider` in [src/knowledge_base/embeddings.py#L374](src/knowledge_base/embeddings.py#L374) still reads `os.environ.get("EMBED_PROVIDER", resolved)` when called without `cfg` and with `allow_env_override=True`. While `settings.check_environment()` rejects `EMBED_PROVIDER` at startup, cleaning up this lingering read in `embeddings.py` guarantees that deprecated variable is completely dead code across the repository.
[Antigravity Review] [LOW] Plan item 10 and D6: `configure_vision` ([src/knowledge_base/vision.py#L146](src/knowledge_base/vision.py#L146)) ends with `return _get_vision_config(conn)`. Because D6 makes `_get_vision_config` raise `ValidationError` when `KNOWLEDGE_BASE_VISION` is off, calling `configure_vision` while the switch is off (e.g. via `configure_vision_tool`) will write rows but raise an error before returning. If `configure_vision` is intended to record settings while inert (owner call d), it should return the config mapping directly without calling `_get_vision_config`.


## Round 2

Michaël,

### Conclusion & Confidence
**LGTM.** Candidate 2 fully resolves all Round 1 blockers and observations without introducing regressions. The plan establishes clean, fail-closed boundaries for the embedding endpoint, eliminates tool-driven identity drift via runtime promote guards, and introduces explicit operator maintenance transitions (`re-embed` and `rebind`) for endpoint and model migrations.
Confidence: **High (95%)**.

---

### Altitude Assessment

Altitude report SHA-256: 547c687adcf94a0757ab1233eb036fd9a1f70abd261421d933f532fcad84ee80

The physical line count expanded from 375 to 471 lines (+96 lines, +20 checkbox items). This expansion is completely justified by the concrete architectural gaps identified in Round 1:
1. **D3 Maintenance Transitions (Item 14, +18 lines):** Explicitly specifies the mechanics and failure modes for `knowledge-base-ingest re-embed` and `rebind` using `open_declared_db(transition=...)` with the unit stopped.
2. **Provider Builder & Space Guard (Item 8 & 14, +22 lines):** Unifies provider construction through `embed_swap.declared_provider_config(conn, family)` across all 5 embed sites, blocking foreign provider family construction, and adds guards in `promote_space` and `re_embed` so tool-created spaces cannot switch the runtime model identity.
3. **Secret Hygiene (Item 5, +8 lines):** Updates `bind_embed_config` to derive and persist `embed_api_key` at each bind, removing inline secret exposures.
4. **Tool Switch Refusal Guards (Items 10–11, +12 lines):** `configure_vision`, `configure_omniparser`, and `configure_browser` now explicitly refuse before mutating state when their respective environment switches are disabled.
5. **URL Normalization Grammar (Item 4, +6 lines):** Formally specifies `rstrip("/")` followed by `removesuffix("/v1")`.

The plan avoids file inlining, speculative abstractions, and superfluous catalogs.

---

### Delta Verification Against Repository Sources

- **URL Normalization Grammar (Item 4):** Standardized to `rstrip("/")` followed by `removesuffix("/v1")`. This aligns with `OpenAICompatProvider._embeddings_url` and prevents malformed URLs (e.g., `https://embedder.example.test/v1/v1/embeddings`).
- **Dead Fallback Elimination (Item 9):** `os.environ.get("EMBED_PROVIDER")` in `src/knowledge_base/embeddings.py:374` is removed, leaving no back-compat leakage.
- **Provider Family Integrity (Item 8 & 14):** `embed_swap.declared_provider_config(conn, family)` guards against disparate families (e.g. promoting an `ollama` space on an `openai_compat` declaration raises `ValidationError` before hitting external networks).
- **SSRF Floor Exemption (Item 15):** The exemption remains strictly limited to `OpenAICompatProvider.embed` using exact host matching via `utils.validate_base_url(..., exempt_host=...)`. Tool paths (`ingest_url`, `web._validate_image_url`, `configure_llm`) remain unconditionally bound to `is_private_ip`.
- **Subprocess Harness Discipline (Item 13 & Verification):** The CLI subprocess test specifies `stdin=subprocess.DEVNULL` and `timeout=30`, preventing stdio hangs during automated test runs.

Candidate 2 is ready for implementation.

[Antigravity Review] LGTM

## Round 3

Michaël,

### Conclusion & Confidence
**LGTM.** Candidate 3 is completely sound and ready for implementation. It resolves all Round 2 feedback with high precision, leaves zero paths to undeclared endpoints or identities, prevents secret exfiltration via the LLM configuration path, and establishes deterministic resume and rollback behaviors for re-embedding.
Confidence: **High (98%)**.

---

### Altitude Assessment

Altitude report SHA-256: 3fbb853794ed9d51a3ec7bff5fc9469bc4d2e58cea205649cb987636093b948b

The plan grew by 35 physical lines (from 471 to 506 lines, 20 top-level tasks), with literal volume strictly confined to a 10-line verification block. This growth is entirely driven by four necessary security and operational hardenings:
1. **Active-space bind check (D2, Item 6):** Validates the active space in `embed_spaces` against the environment declaration during `bind_embed_config`, catching drifted or partially-migrated legacy databases before serving.
2. **Re-embed reuse, resume, and rollback rules (D3, Item 14):** Formalizes idempotency and failure recovery for `knowledge-base-ingest re-embed` by reusing `populating` (interrupted resume) and `deprecated` (rollback) spaces, preventing orphan space accumulation and service lock-out.
3. **LLM key allowlist enforcement (D1, D6, Item 12):** Restricts LLM key specifications to inline strings or strictly `env:KNOWLEDGE_BASE_LLM_API_KEY`, blocking foreign environment key leakage across both `configure_llm` and legacy database rows in `_get_llm_config`.
4. **Owner decisions (f) and (g):** Preserves same-provider comparison spaces while binding their HTTP traffic strictly to the declared endpoint, and properly surfaces the pre-existing folder summary commit ordering as collateral for a follow-up issue.

No complete files, validator tables, or synthetic catalogs are inlined.

---

### Focus Area Verification Against Repository Sources

1. **Active-Space Bind Check (`src/knowledge_base/db.py`, D2, Item 6):**
   - In candidate 2, `bind_embed_config` compared the `config` table rows against the environment declaration, but did not cross-check `get_active_space(conn)`.
   - Candidate 3 checks `(active.provider, active.model, active.dim) == declaration`. If the active space drifted or an interrupted migration left an old space active, `open_declared_db()` hard-refuses on startup with instructions to run `re-embed`.
   - Feasible and verified: `get_active_space(conn)` is already implemented in `db.py:617-624` and returns the active `EmbedSpace` row.

2. **Re-Embed Reuse, Resume, and Rollback (`src/knowledge_base/embed_swap.py`, D3, Item 14):**
   - `knowledge-base-ingest re-embed` opens with `transition="re-embed"` and refuses if the active space already matches the declaration ("use rebind").
   - If an existing non-active space matches the declaration:
     - `status == "populating"`: Resumes backfilling. In `embed_swap.py:384-396`, the backfill query already uses `LEFT JOIN ... WHERE v.chunk_id IS NULL`, so resuming automatically skips chunks already embedded.
     - `status == "deprecated"`: Reuses the space for rollback. `backfill_space` now accepts `deprecated` spaces to embed only chunks ingested since deprecation, then `promote_space` atomically activates it without a full corpus re-calculation.
   - An interrupted run leaves the old space active; a plain start refuses because the active space does not match the declaration, preventing half-embedded spaces from serving.

3. **LLM Key Allowlist (`src/knowledge_base/llm.py`, D6, Item 12):**
   - Tool calls to `configure_llm` cannot supply arbitrary environment references (such as `env:KNOWLEDGE_BASE_EMBED_API_KEY` or `env:AWS_SECRET_ACCESS_KEY`). Any `env:*` spec other than `env:KNOWLEDGE_BASE_LLM_API_KEY` raises `ValidationError` immediately.
   - For pre-#569 databases, `_get_llm_config` validates the stored `llm_api_key` before any probe or LLM invocation, preventing exfiltration through stored legacy rows.

4. **Owner Calls (f) and (g):**
   - **(f) Comparison Spaces:** Comparison spaces of another model within the same provider family can still be created and evaluated (supporting A/B testing). Because `declared_provider_config(conn, space["provider"])` enforces the declared base URL and API key, and `promote_space` refuses any space whose identity differs from the declaration, comparison spaces cannot bypass the declared endpoint or become active.
   - **(g) Folder Summary Ordering:** In `embed_swap.py:490-545`, `promote_space` commits the active status swap before running `_re_embed_folder_summaries`. This pre-existing behavior is accurately cataloged as collateral without inflating the scope of #569.

Candidate 3 satisfies all constraints and design requirements.

[Antigravity Review] LGTM

## Round 4

Michaël,

### Conclusion & Confidence
**LGTM.** Candidate 4 is exceptionally clean, robust, and ready for execution. It eliminates the subtle edge cases identified in Round 3 by removing the speculative resume/rollback heuristic, enforcing write-free refusals on all bind and transition checks, making `promote_space` fully all-or-nothing, and explicitly stating the model rollback contract under owner call (g).
Confidence: **High (99%)**.

---

### Altitude Assessment

Altitude report SHA-256: 5f5f969cc2ef21eadc732100939622a8ca04d0974ab60846e4771f306cd7507c

The plan grew by only 14 physical lines (from 506 to 520 lines, 20 top-level tasks), while actually simplifying implementation mechanics:
1. **Write-free refusals (D2, Items 12, 14):** Ensures that all validation checks in `bind_embed_config` (including the transition's "use rebind" check) and `configure_llm` execute before any SQL mutation, committing once at the end.
2. **Simplified re-embed transition (D3, Item 14):** Drops the multi-parameter matching heuristic for space reuse and resume, reverting to clean, dedicated space creation. This eliminates vector pollution across differing endpoints and prevents parameter mismatch bugs.
3. **All-or-nothing `promote_space` (D3, Item 14):** Replaces the previous "pre-existing collateral" label with an atomic sequence: folder summaries are embedded first in memory, followed by the status swap, config synchronization, and `folder_summaries_vec` update in a single transaction.
4. **Owner call (g):** Accurately documents that rolling back a model requires a full re-embed, maintaining simplicity and database integrity.

---

### Focus Area Verification Against Repository Sources

1. **Write-Free Refusal (`src/knowledge_base/db.py`, D2, Item 14):**
   - In candidate 3, a re-embed transition on an already-matching identity would record the new endpoint before reporting "use rebind", mutating the database during a failed operation.
   - Candidate 4 structures `bind_embed_config` so all checks run first. If `transition == "re-embed"` and the active space's `(provider, model, dim)` matches the declaration, `ValidationError("use rebind")` raises before any `INSERT` or `DELETE` executes. Mutations commit once at the end, guaranteeing no partial writes on error.
   - Similarly, in `src/knowledge_base/llm.py`, `configure_llm` validates the effective API key (argument or stored row) before touching the `config` table.

2. **Simplified Re-Embed Transition (`src/knowledge_base/embed_swap.py`, D3, Item 14):**
   - Dropping the resume and rollback reuse logic directly adheres to "Less is more". Resuming a populating space across modified environment endpoints risked mixing vectors from different models/hosts, while deprecated rollbacks required complex validation across `matryoshka_base_dim`, `element_type`, and `chunk_strategy`.
   - By creating a fresh space via `re_embed` (which already generates unique collision-resistant names in `embed_swap.py:631-638`), every re-embed is clean, deterministic, and isolated. Interrupted runs never serve because the active space remains untouched; cleanups can be run via `cleanup_embed_space_tool`.
   - Catching `httpx.HTTPError` in the CLI wrapper provides a clear operator error message ("re-embed failed; rerun it") without raw tracebacks.

3. **All-or-Nothing `promote_space` (`src/knowledge_base/embed_swap.py`, D3, Item 14):**
   - In the existing implementation (`embed_swap.py:490-545`), `promote_space` commits the active status swap and config sync before calling `_re_embed_folder_summaries`. A network failure during summary embedding left a broken, half-promoted state.
   - Candidate 4 embeds folder summaries first through `declared_provider_config`. Once vectors are returned, the status swap, config updates, table replacement, and relationship invalidation commit in a single atomic transaction. A failure leaves the populating space intact and the active space unchanged.

4. **Owner Call (g) — Rollback Contract:**
   - Clearly documented under Decisions and Documentation: rolling back an embedding model costs a full re-embed. This avoids maintaining complex heuristics for resurrecting deprecated spaces and ensures the active space is always verified end-to-end against the current corpus.

No remaining paths exist to undeclared endpoints or identities. Candidate 4 is approved.

[Antigravity Review] LGTM

## Round 5

Michaël,

### Conclusion & Confidence
**LGTM.** Candidate 5 is production-ready. The plan addresses all remaining operational and transactional edge cases: it guarantees that `promote_space` commits atomically via an explicit `BEGIN IMMEDIATE` transaction, automatically cleans up failed temporary spaces during `re_embed` without leaving orphans, and allows `rebind` to repair drifted `config` identity rows when the active space matches the environment declaration.
Confidence: **High (99%)**.

---

### Altitude Assessment

Altitude report SHA-256: a63dc38e7ed6879c0aaf73b00772e8e9de864460d5fc7670a2394ee4a0a348ba

The physical line count increased by only 13 lines (from 520 to 533 lines, 20 top-level tasks), with literal volume strictly limited to the unchanged 10-line verification block. This growth directly translates into precise transactional guarantees:
1. **Explicit transaction in `promote_space` (D3, Item 14):** Formalizes atomicity using `BEGIN IMMEDIATE` … `COMMIT` with `ROLLBACK` on any exception, accounting for Python `sqlite3`'s behavior around DDL statements.
2. **Failed `re_embed` auto-cleanup (D3, Item 14):** Removes the created space on backfill or promotion failure via `deprecate_space` then `cleanup_space`, leaving no orphan spaces in `embed_spaces`.
3. **`rebind` repair capability (D2, Item 14):** Restores drifted `config` rows to match the active space and the environment declaration.

---

### Focus Area Verification Against Repository Sources

1. **Explicit `promote_space` Transaction (`src/knowledge_base/embed_swap.py:441-550`, D3, Item 14):**
   - In Python's standard `sqlite3` library, statements like `DROP TABLE` and `CREATE VIRTUAL TABLE` implicitly commit active transactions unless managed explicitly with `isolation_level=None` and `BEGIN IMMEDIATE` (the proven pattern in `src/knowledge_base/migrate.py:206-233`).
   - Candidate 5 splits `_re_embed_folder_summaries` into an embed step (which calls `declared_provider_config` before opening the transaction) and a write step with no internal commit.
   - The entire mutation—status swap, `config` update, virtual table recreation, vector insertion, and relationship deletion—runs inside an explicit `BEGIN IMMEDIATE` … `COMMIT` block with `ROLLBACK` on any error.
   - `test_promote_is_all_or_nothing` is parametrized over failures occurring both before any write and during vector insertion after table recreation, confirming full rollback across both the current and fresh connections.

2. **Failed `re_embed` Cleanup (`src/knowledge_base/embed_swap.py:606-660`, D3, Item 14):**
   - If `backfill_space` or `promote_space` fails, `re_embed` intercepts the exception, marks the new space deprecated, and calls `cleanup_space` before re-raising.
   - This eliminates orphan populating spaces in `embed_spaces` and avoids deadlocking on subsequent runs with unique naming constraints. Tested by `test_failed_re_embed_leaves_no_orphan_space`.

3. **`rebind` Repair Rule (`src/knowledge_base/db.py`, D2, Item 14):**
   - If a legacy database has a `config` row that drifted from its active space (e.g. `config.embed_model` differs from `active_space.model`), but the active space itself matches the declared model, `rebind` verifies `(active.provider, active.model, active.dim) == declaration`, records `embed_base_url`, and rewrites the `config` identity rows to match.
   - This resolves the operational deadlock where neither plain start nor `re-embed` could proceed. Tested by `test_cli_rebind_adopts_a_moved_endpoint`.

No remaining paths exist to undeclared endpoints or identities. Candidate 5 is ready for implementation.

[Antigravity Review] LGTM

## Round 6

Michaël,

### Conclusion & Confidence
**LGTM.** Candidate 6 provides rock-solid transaction semantics and clean fault recovery. By pulling the completeness check, summary read, summary embed, and schema updates into a single `BEGIN IMMEDIATE` transaction, it eliminates race conditions against concurrent ingests. Handling `BaseException` ensures clean space teardown on manual interruption (Ctrl-C) while truthfully documenting the unpreventable SIGKILL residual.
Confidence: **High (99%)**.

---

### Altitude Assessment

Altitude report SHA-256: 7c44bfaf5e95e89f93454875b5fbe7f816b749ae4a2f3ac0e4b033117b23a88a

The plan expanded by just 6 physical lines (from 533 to 539 lines, 20 top-level tasks), maintaining the unchanged 10-line verification block. This compact addition addresses critical operational edge cases:
1. **Single-transaction `promote_space` (D3, Item 14):** Encloses the entire promotion workflow under an explicit write lock (`BEGIN IMMEDIATE`), matching the `migrate.py:230-271` pattern.
2. **`BaseException` cleanup (D3, Item 14):** Traps both standard exceptions and `KeyboardInterrupt` to delete temporary spaces while strictly preserving and re-raising the original error.
3. **Killed-run residual documentation (D3, Documentation):** Replaces unrealistic claims of universal self-cleanup with an accurate operational procedure for ungraceful kills (SIGKILL/OOM).

---

### Focus Area Verification Against Repository Sources

1. **Single-Transaction `promote_space` (`src/knowledge_base/embed_swap.py`, D3, Item 14):**
   - In candidate 5, embedding folder summaries before acquiring the write lock created a window where concurrent chunk ingestion could invalidate the backfill completeness check, or folder summary edits could leave stale vectors committed.
   - Candidate 6 executes the full sequence—completeness assertion, reading summaries, embedding them via `declared_provider_config`, status swap, config update, virtual table recreation, and relationship cleanup—inside a single `BEGIN IMMEDIATE` … `COMMIT` block.
   - Managing this under `isolation_level = None` (and restoring the original level in a `finally` block) avoids implicit commits before DDL and prevents SQLite errors on nested transactions. Because folder summaries are small (typically a small set of paths), the brief write lock easily stays within standard SQLite busy timeouts.

2. **`BaseException` Cleanup Handling (`src/knowledge_base/embed_swap.py`, D3, Item 14):**
   - Intercepting `BaseException` ensures that interactive interrupts (Ctrl-C / `KeyboardInterrupt`) trigger `deprecate_space` and `cleanup_space`.
   - Running the cleanup within its own `try...except` block and logging failures prevents a cleanup glitch from masking the original root cause.

3. **Killed-Run Residual Handling (Documentation, D3):**
   - Ungraceful process termination (SIGKILL, host failure, OOM) cannot execute Python `finally` blocks.
   - Documenting that orphaned `populating` spaces are inert (they are never searched, never promoted without a complete backfill, and can be cleaned up with `deprecate_embed_space_tool` followed by `cleanup_embed_space_tool`) provides clear and realistic guidance for operators.

No path remains to undeclared endpoints or identities. Candidate 6 is approved.

[Antigravity Review] LGTM

## Round 7

Michaël,

### Conclusion & Confidence
**LGTM.** Candidate 7 elegantly resolves the concurrency and transaction boundary challenges raised in Round 6. The optimistic snapshot embedding avoids blocking concurrent database workers while keeping the lock-holding phase atomic, the `conn.in_transaction` guard prevents premature commit of caller state, and the pre-existing lifecycle race is bounded cleanly under D3 without unbounded scope creep.
Confidence: **High (99%)**.

---

### Altitude Assessment

Altitude report SHA-256: 3207898e6138026ff5d5994c93a2288290eb196150e4223e2a6789415fb824d0

The plan grew by only 7 physical lines (from 539 to 546 lines, 20 top-level tasks), maintaining the unchanged 10-line verification block. This small delta delivers full SQLite transaction and concurrency safety:
1. **Pending-transaction guard (D3, Item 14):** Refuses `promote_space` if `conn.in_transaction` is active before touching `isolation_level`, preventing unintentional commits of caller writes.
2. **Optimistic promotion (D3, Item 14):** Embeds folder summaries snapshot outside the lock, acquires `BEGIN IMMEDIATE`, re-validates completeness and re-reads summaries, embeds only changed rows if any, and commits atomically.
3. **Lifecycle race scoping (D3, Decisions):** Formally bounds the space-swap race under D3 (identity swaps happen only offline; online swaps share the declared model/dim), recording cross-writer revalidation as collateral rather than entangling this issue with full-codebase refactoring.

---

### Focus Area Verification Against Repository Sources

1. **Optimistic `promote_space` (`src/knowledge_base/embed_swap.py`, D3, Item 14):**
   - Holding `BEGIN IMMEDIATE` across network I/O could block concurrent background jobs or ingests past the busy timeout.
   - Candidate 7 snapshots and embeds folder summaries *prior* to locking. Upon acquiring `BEGIN IMMEDIATE`, it re-checks corpus completeness and inspects `folder_summaries`. If rows were modified or added during the snapshot window, it embeds only the delta before writing. In the typical case, zero network calls occur while holding the lock.
   - Tested by `test_promote_re_embeds_a_summary_changed_after_the_snapshot`.

2. **Pending-Transaction Refusal (`src/knowledge_base/embed_swap.py`, D3, Item 14):**
   - Setting `conn.isolation_level = None` in Python's `sqlite3` driver implicitly commits any active transaction.
   - Candidate 7 asserts `not conn.in_transaction` before modifying `isolation_level`. If uncommitted caller writes exist, it refuses immediately with an error, leaving the caller's transaction open and unaffected.
   - `isolation_level` is restored in a `finally` block, and any `BaseException` (including `KeyboardInterrupt`) triggers an immediate `ROLLBACK`.
   - Tested by `test_promote_refuses_a_pending_transaction` and the parametrized `test_promote_is_all_or_nothing`.

3. **Arbitration of the Space-Lifecycle Race (D3, Constraints):**
   - The scenario where an in-flight writer embeds against space A and commits after space B is promoted is a pre-existing property of the multi-space architecture.
   - Under #569, model or provider identity changes are prohibited in a served process and can only be performed via `knowledge-base-ingest re-embed` with the unit stopped (single-writer).
   - In a running server, promotion is strictly constrained to spaces matching the declared, active `(provider, model, dim)` (e.g. Matryoshka dimension truncation or chunking strategy variations). Thus, vectors written during any online transition are guaranteed to originate from the declared provider and model.
   - Appropriately documented as collateral rather than rewriting transaction boundaries across `ingest.py`, `web.py`, `vision.py`, and `folder_summaries.py`.

No remaining paths exist to undeclared endpoints or identities. Candidate 7 is fully approved.

[Antigravity Review] LGTM

## Round 8

Michaël,

### Conclusion & Confidence
**LGTM.** Candidate 8 cleanly integrates the three confirmation-round refinements. The folder-summary diffing specification is mathematically complete across all edge cases (new, modified, and vanished rows), the "unit stopped" invariant is properly contextualized with an advisory lock in the collateral ledger, and the redundant route-level relationship deletions are eliminated in favor of single-transaction atomicity in `promote_space`.
Confidence: **High (99%)**.

---

### Altitude Assessment

Altitude report SHA-256: 26ed18e6eb4b66b711db7bc58a90861d3a6ce7ff990bfc9fb5dc976b031d1235

The physical line count increased by only 5 lines (from 546 to 551 lines, 20 top-level tasks), maintaining the unchanged 10-line verification block. This small confirmation delta refines:
1. **Summary diff specification (D3, Item 14):** Formulates exact vector reuse and re-embed rules during the optimistic promotion check.
2. **Unit-stopped operational boundary (D3):** Documents that idle SQLite connections do not block CLI execution and records a server/CLI advisory lock as collateral.
3. **Route deduplication (D3, Item 14):** Removes uncoordinated `DELETE FROM relationships` statements from `src/knowledge_base/routes/embeddings.py`.

---

### Confirmation of Delta Edits Against Repository Sources

1. **Folder-Summary Diff Specification (`src/knowledge_base/embed_swap.py`, D3, Item 14):**
   - The optimistic promote rule now explicitly accounts for all set-difference permutations between the snapshot and lock acquisition:
     - Unchanged `(folder_path, summary)`: Reuses the snapshot vector without network I/O.
     - New or modified rows: Embeds using `declared_provider_config`.
     - Vanished rows: Skipped and excluded from insertion into `folder_summaries_vec`.
   - Guarantees `folder_summaries_vec` remains 100% congruent with `folder_summaries` when the transaction commits.

2. **Unit-Stopped Assumption and Advisory Lock (D3, Constraints):**
   - In SQLite WAL mode, an idle server holds no exclusive locks. The assumption that CLI transitions (`re-embed`, `rebind`) run without concurrent writers is therefore an operational contract.
   - Transparently stated in the README, while recording a cross-process advisory lock (e.g. `fcntl` or DB marker) in the collateral finding for future hardening.

3. **Route-Level Invalidation Deduplication (`src/knowledge_base/routes/embeddings.py:93-95, 186-188`):**
   - Both `re_embed_tool` and `promote_embed_space_tool` previously executed separate, uncoordinated `DELETE FROM relationships WHERE relation_type = 'similar'` queries after invoking `promote_space`.
   - Because `promote_space` now executes this invalidation inside its atomic `BEGIN IMMEDIATE` … `COMMIT` block, removing these route-level deletes prevents redundant operations and guarantees transactional consistency.

No remaining paths exist to undeclared endpoints or identities. Candidate 8 is fully verified and ready to build.

[Antigravity Review] LGTM

REVIEW COMPLETE
