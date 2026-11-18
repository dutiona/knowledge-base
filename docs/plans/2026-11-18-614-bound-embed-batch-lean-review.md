Reviewer: agy gemini-3.8-flash-high via agy-review-round.py (headless agy -p, conversation 736554a7-7b8b-4d12-8ad0-f24035b344f9)

~~~~
## Independent Implementation Plan Review: Issue #614

### Check 1: Acceptance Criteria & Measurement
- **Acceptance criteria:** The plan meets both acceptance criteria. Latency was measured first on a matched release b12048 `llama-server` rig under load before proposing configuration or bounds. The batch size is configurable (`KNOWLEDGE_BASE_EMBED_BATCH_SIZE`) and default `1` is directly justified: it is the only size that restricts query wait to a single input's duration (~428 ms p95 vs 219 s at 512) while preserving identical chunk throughput (2.11–2.33 chunks/s).
- **Measurement soundess & limits:** The measurement method is sound and its limits are explicitly documented (CPU build on Linux vs Mac Metal runtime; load average noise; residual wait for near-limit 2,048-token chunks).
- **Check 1 Verdict:** Nothing. No findings.

---

### Check 2: Correctness of Design
- **Settings parse & refusal:** `_positive_int("batch_size")` enforces positive integers (`"0"`, `"-1"`, `"x"`, `"1.5"` fail). Unset defaults to 1. Refusal is scoped to non-`_OPENAI_FAMILIES` only when the variable is set.
- **ProviderConfig as cache key:** `batch_size: int = 1` added to `ProviderConfig` ensures distinct batch sizes do not collide in `_provider_cache`.
- **Call-site audit:** Every embedding call site across the codebase (`src/knowledge_base/ingest.py`, `src/knowledge_base/search.py`, `src/knowledge_base/embed_swap.py`, `src/knowledge_base/folder_summaries.py`, `src/knowledge_base/redact_store.py`) retrieves its configuration via `declared_provider_config(conn, space)`, ensuring no site bypasses the declared batch size or gate.
- **Result order & dimensions:** Preserved via batch slicing and index sorting in `_call_api`. `expected_dim` checks remain intact.
- **Interaction with `_bounded` / `/tokenize`:** Token bounding occurs prior to batching; `/tokenize` calls remain outside the gate.

[Antigravity Review] [LOW] tests/test_settings.py:33 — `_EMBED_VARS` does not include `KNOWLEDGE_BASE_EMBED_BATCH_SIZE`, so the `declare` test fixture will not clear it between tests that declare custom settings — add `"KNOWLEDGE_BASE_EMBED_BATCH_SIZE"` to `_EMBED_VARS` in `tests/test_settings.py`.

---

### Check 3: Decision 6 (FIFO Gate for Concurrent Tool Calls)
- **Concurrency claim:** Accurate. Concurrent tool calls dispatched via FastMCP's sync-tool thread pool (`call_sync_fn_in_threadpool`) or HTTP requests across worker threads can execute `embed()` simultaneously.
- **Lock vs FIFO Gate vs Nothing:** "Nothing" violates the 1-input shared embedder budget. A plain `threading.Lock` is unfair under loops (a thread draining 50 chunks reacquires the lock immediately at loop boundaries, starving waiting queries). The FIFO gate ensures fair interleaving.
- **Queries through the gate:** Correct. A query arrives while chunk $K$ is in flight, acquires the next available ticket $K+1$, and is guaranteed to run before chunk $K+1$. Query latency remains bounded by at most one in-flight chunk.

[Antigravity Review] [HIGH] docs/plans/2026-11-18-614-bound-embed-batch.md:141 — In `_Turns.turn()`, if a thread waiting on `self._cv.wait()` is cancelled or interrupted (e.g. `KeyboardInterrupt`), `self._serving` will never advance past its ticket, permanently deadlocking all subsequent waiters — track cancelled tickets in `_Turns` and advance `self._serving` past any skipped tickets in `finally:`.

[Antigravity Review] [LOW] docs/plans/2026-11-18-614-bound-embed-batch.md:91 — Decision 6 states that the HTTP server exposes `ingest, reingest, ingest_text and ingest_url`, but `NETWORK_TOOLS` in `src/knowledge_base/server.py:54` restricts HTTP exposure to `(search_index, ingest_text, ingest_url)` (`ingest` and `reingest` are stdio/local only) — update Decision 6 to name `search_index`, `ingest_text`, and `ingest_url` as the concurrent HTTP tools.

---

### Check 4: Decision 5 (Kept-Alive Client)
- **Justification & thread-safety:** Reusing `self._http` avoids paying TCP and TLS handshakes on up to 512 requests per document. `httpx.Client`'s connection pool is thread-safe, and `_call_api` calls are additionally serialized by `_Turns`.
- **Test migration:** Existing `TestOpenAICompatProvider` tests currently patch `knowledge_base.embeddings.httpx.post`. They must migrate to `http_requests`.

[Antigravity Review] [LOW] docs/plans/2026-11-18-614-bound-embed-batch.md:152 — Sequence 3 states that migrated `TestOpenAICompatProvider` tests keep the `follow_redirects` assertion from `test_embeds_against_base_url`, but `self._http.post` delegates redirect policy to the client instance rather than passing `follow_redirects` per call or recording it on `httpx.Request` — assert `provider._http.follow_redirects is False` directly or assert that a 3xx response from `http_requests` is not followed.

---

### Check 5: Tests
- **Red tests:** Each red test fails on master for the right reason and properly pins behavior.
- **Determinism & failure modes:**

[Antigravity Review] [MEDIUM] docs/plans/2026-11-18-614-bound-embed-batch.md:138 — The fairness test in Sequence 2 risks flakiness if thread A1 is released before thread B has actually entered `turn()` and acquired its ticket — explicitly synchronize on `provider._turns._next == 2` before setting the event that unblocks A1.

[Antigravity Review] [MEDIUM] docs/plans/2026-11-18-614-bound-embed-batch.md:134 — Sequence 2 lacks a test verifying that an exception inside `_call_api` (such as HTTP 500 or connection failure) properly advances `self._serving` in `finally:` and does not block subsequent waiting threads — add a test in `tests/test_embeddings.py` asserting that a failed embedding call releases the turn and allows the next ticket holder to execute.

[Antigravity Review] [LOW] docs/plans/2026-11-18-614-bound-embed-batch.md:112 — Sequence 1 lacks a test verifying that `ProviderConfig` cache separation includes `batch_size` — add a test in `TestProviderConfigCache` asserting that two configs differing only by `batch_size` yield distinct cached provider instances.

---

### Check 6: Scope, Documentation & Hygiene
- **Scope:** Strictly bounded to issue #614.
- **Hygiene:** Public repository rules observed (no internal hostnames, tailnet IPs, or absolute home paths).
- **Check 6 Verdict:** Nothing. No findings outside the documentation note flagged in Check 3.

REVIEW COMPLETE
~~~~
