# fix(embeddings): don't let a 512-input embedding batch starve the shared embedder's queries (#614)

## Outcome

KB sends one input per `/v1/embeddings` request by default and keeps at most one such request in flight per
embedder, even when several tool calls embed at once, so a query that reaches the shared embedder while KB ingests or
re-embeds waits for at most one KB input instead of KB's whole request. The number of inputs per request
is configurable with `KNOWLEDGE_BASE_EMBED_BATCH_SIZE` (a whole number of at least 1, default `1`), validated at
startup with the rest of the declaration, and documented beside the other embedding variables. The default comes
from the measurement below.

## Measurement (done before this plan, as the issue's first criterion asks)

Rig, as #604's live check ran it: a throwaway llama-server, release b12048 for Linux x64 (CPU build, commit
`2c93f0b6e`, the Mac's build), on loopback with a throwaway key, serving the pinned GGUF
`Qwen3-Embedding-0.6B-Q8_0.gguf` (sha256 `06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439`), with the
deployed embedder's flags minus Metal: `--embedding --pooling last --parallel 4 --kv-unified-per-slot 2049
--batch-size 2048 --ubatch-size 2048 --no-slots --log-verbosity 2`. The server ran under the heavy-command lock; the
binaries, the model and the key were deleted afterwards.

Load, shaped like KB: one client sends a document's chunks sequentially, `batch` inputs per request, as
`OpenAICompatProvider.embed` does; chunks are this repository's own `docs/` prose. Meanwhile an ME-recall-sized
query (KB's query instruction plus a short question, 1 input, about 25 tokens) is sent closed-loop every 200 ms and
timed. The desktop was shared with other work (load average 18 to 41 on 32 threads), so absolute numbers are noisy;
the relative effect is what the measurement shows. CPU numbers are not the Mac's Metal numbers.

Chunks of 489–762 tokens (mean 600):

| Inputs per request | Query p50 / p95 / max (ms) | Drain (chunks/s) | One KB request p50 (ms) |
| --- | --- | --- | --- |
| none (query alone) | 32.3 / 47.1 / 144.5 | — | — |
| 512 (today) | 32.2 / 219,398.5 / 219,398.5 | 2.33 | 219,982.9 |
| 64 | 29.6 / 29,954.6 / 29,954.6 | 2.21 | 28,777.6 |
| 16 | 6,640.8 / 8,280.4 / 8,526.7 | 2.16 | 7,261.5 |
| 4 | 1,714.6 / 2,157.7 / 2,202.0 | 2.11 | 1,927.9 |
| 1 | 252.4 / 428.1 / 547.9 | 2.14 | 453.8 |
| 1, kept-alive connection | 239.9 / 437.7 / 545.7 | 2.16 | 440.6 |
| none (query alone, after) | 31.4 / 36.3 / 41.9 | — | — |

Chunks of 1,614–2,048 tokens (mean 1,852; the served limit is 2,048):

| Inputs per request | Query p50 / p95 / max (ms) | Drain (chunks/s) |
| --- | --- | --- |
| none | 28.6 / 36.4 / 44.2 | — |
| 1 | 1,112.7 / 1,539.2 / 1,705.4 | 0.72 |
| 4 | 2,383.6 / 3,053.0 / 3,077.4 | 0.73 |

What it shows:

- **A query waits for KB's whole in-flight request**, not for one slot: with 4 slots free, a query arriving during a
  64-input request still waited 30 s. llama-server queues a request's inputs ahead of a later request's.
- **Fewer inputs per request cost no throughput**: 2.11–2.33 chunks/s at every size, within the run's noise. This is
  the runbook's finding on the Mac (one input in flight drained as fast as three).
- **At one input per request, a query waits for one input**: p95 428 ms for 600-token chunks on this CPU, against
  320.9 ms the runbook recorded on the Mac for 541-token items under the 27B. One is the only size whose query p95
  stayed under a second here.
- **The residual is one input's time**, which grows with its length: 1.5 s p95 on this CPU for near-limit
  2,048-token inputs. KB cannot make it shorter by batching; only the input length (ADR-0015's settled
  `max_input_tokens` of 2,048) or the server could. The Mac has not been measured at that length (its recorded runs
  stop at 541 tokens); see Collateral below.
- **A kept-alive connection changed nothing on loopback**, as expected without TLS.

## Constraints

- Measure first (Michaël's ruling: no cap from a guess): ME-recall-sized query latency while KB ingests a large document at today's batch size, against the shared embedder or an equivalent llama-server.
- Then bound KB's in-flight embedding work so a concurrent query never waits for seconds; the batch size is configurable and its default is chosen from the measurement.
- The shared embedder's budget (dutiona/infra#77): one embedding input in flight, shared by ME and KB; each element of an `input` array counts.
- Latency rules (Michaël, 2026-11-17): no latency cap, timeout or gate from a guess; what must never happen is a tool call hanging for seconds.
- KB is public: no host names, tailnet addresses or home paths in code, docs, commits or the PR.
- ADR-0015's identity tuple is unchanged: the batch size is not part of a space's identity.

## Decisions

1. **Default `1`.** The measurement above: the only size that keeps a query to one input's wait, at no throughput
   cost. It is also the shared embedder's recorded budget.
2. **Scope: the OpenAI-compatible family** (`openai_compat`, `openai`, `llama.cpp`), the wire the shared embedder
   speaks and the only one measured. `ollama` keeps its 32 and `onnx` (in-process) its 32. Setting the variable
   with `ollama` or `onnx` is refused at startup, as `KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS` is under a provider
   that cannot honour it, so it never silently does nothing.
3. **One default for the whole family**, `openai` included. An operator of a remote API with request-rate limits
   raises it; the deployed case is the shared embedder.
4. **Where it lives**: `EmbedSettings.batch_size` (parsed in `settings.load_embed_settings`, so `check_environment`
   covers it), carried by `ProviderConfig.batch_size` (part of the provider cache key) from
   `embed_swap.declared_provider_config`, consumed by `OpenAICompatProvider(batch_size=...)`. Every embedding call
   site already takes its provider from `declared_provider_config` (#573, #604), so nothing bypasses it.
5. **A kept-alive connection for `/v1/embeddings`** (`_call_api` posts through the provider's existing `self._http`
   instead of a new `httpx.post` per request). One input per request multiplies the requests by up to 512, and
   against the deployed HTTPS endpoint each `httpx.post` pays a new TCP and TLS handshake. Loopback cannot show
   that cost (above), so this is a separate commit with its own reason, reviewable and revertible on its own.
6. **One request in flight per embedder, in arrival order.** The batch size alone does not bound KB: FastMCP 3.1
   runs every sync tool in a thread pool (`fastmcp/tools/function_tool.py`, `call_sync_fn_in_threadpool`) on either
   transport: over stdio every ingest tool, and over HTTP `ingest_text` and `ingest_url` (`server.NETWORK_TOOLS`,
   beside `search_index`), so parallel tool calls (an agent issuing several at once) each put an input in flight; the runbook measured three in flight tripling a query's
   p95. `OpenAICompatProvider` therefore serializes its `/v1/embeddings` requests through a FIFO gate (a
   `threading.Condition` over a queue of waiters, about a dozen lines): a plain `threading.Lock` is not fair, and a thread
   that releases and re-acquires it in a loop can keep a waiter out for its whole drain. Queries take their turn
   too, so a KB query waits behind at most one input per caller already waiting, which is what the server's own
   FIFO queue would make it wait anyway; a query from another client (ME) waits for at most one KB input. The gate
   is per provider instance; `get_provider` caches one per declared configuration, so a process has one per
   embedder. The thread pools in `vision.py` and `extraction.py` call the vision and LLM endpoints, not the
   embedder, and are untouched.

## Implementation sequence

- [ ] **1. Configurable batch size, default 1.**
  Files: `src/knowledge_base/settings.py`, `src/knowledge_base/embeddings.py`, `src/knowledge_base/embed_swap.py`,
  `tests/conftest.py`, `tests/test_settings.py`, `tests/test_embeddings.py`, `tests/test_embed_limits.py`.
  - Red first:
    - `tests/test_settings.py`: unset gives `batch_size == 1`; `"8"` gives 8; `"0"`, `"-1"`, `"x"`, `"1.5"` raise
      `ValidationError` whose message starts with `KNOWLEDGE_BASE_EMBED_BATCH_SIZE`; set with provider `ollama` or
      `onnx` raises.
    - `tests/test_embeddings.py` (through the `http_requests` fixture, AGENTS.md rule 6): an
      `OpenAICompatProvider` built without `batch_size` sends 5 texts as 5 requests of one input each and returns
      the vectors in input order; with `batch_size=3`, as requests of 3 and 2. In `TestProviderConfigCache`: two
      configs differing only in `batch_size` give two provider instances.
    - `tests/test_embed_limits.py` (its transport stub records every request's `input`): ingesting a markdown file
      of several sections sends no `/v1/embeddings` request with more than one input; with
      `KNOWLEDGE_BASE_EMBED_BATCH_SIZE=2`, none with more than two and at least one with two.
    - Red: `uv run pytest tests/test_settings.py tests/test_embeddings.py tests/test_embed_limits.py -q -k "batch"`
      fails (no field, no variable; master sends all inputs in one request).
  - Change:
    - `settings.VARIABLES["batch_size"] = "KNOWLEDGE_BASE_EMBED_BATCH_SIZE"`; `EmbedSettings.batch_size: int = 1`;
      `load_embed_settings` parses it with `_positive_int("batch_size")`, refuses it when the provider is not in
      `_OPENAI_FAMILIES` (message: `KNOWLEDGE_BASE_EMBED_BATCH_SIZE applies to openai_compat, openai and
      llama.cpp; got <provider>`), and defaults it to 1.
    - `ProviderConfig.batch_size: int = 1` (in `__repr__` too); `_build_provider` passes it to
      `OpenAICompatProvider(..., batch_size=cfg.batch_size)`; `OpenAICompatProvider.__init__` takes
      `batch_size: int = 1`; `embed` slices by it. The `512` constant and its comment go.
    - `declared_provider_config` passes `batch_size=declared.batch_size`.
    - `tests/conftest.py` clears `KNOWLEDGE_BASE_EMBED_BATCH_SIZE` with the other declaration variables, and
      `tests/test_settings.py` adds it to `_EMBED_VARS`, so its `declare` fixture clears it too.
  - Green: the same command passes; then `uv run pytest tests/ -q -m "not slow"` passes.
  - Commit: `fix(embeddings): send one input per embedding request by default (KNOWLEDGE_BASE_EMBED_BATCH_SIZE)`.
- [ ] **2. One `/v1/embeddings` request in flight per embedder, in arrival order.**
  Files: `src/knowledge_base/embeddings.py`, `tests/test_embeddings.py`.
  - Red first (through `http_requests`, with a handler that blocks on a `threading.Event` and records how many
    requests are in flight and their order):
    - three threads each embedding 2 texts at the default batch size: never more than one request in flight, and
      all six inputs embedded;
    - fairness: thread A embeds 3 texts; while A's first request is held, thread B asks for 1 text; once B is
      queued (the test waits until `len(provider._turns._queue) == 2`), the held request is released. Requests
      arrive as A1, B1, A2, A3;
    - a failed request releases its turn: thread A's held request is answered 500 while B is queued; A raises
      `httpx.HTTPStatusError`, and B's request is then served;
    - a waiter that leaves the queue (an exception raised while it waits, simulated by making the condition's
      `wait_for` raise for it) is removed, and the next waiter is still served.
    - Red: `uv run pytest tests/test_embeddings.py -q -k "in_flight"` (master sends concurrently and out of order).
  - Change: a private `_Turns` class: a `threading.Condition` and a `collections.deque` of waiter tokens, and a
    `turn()` context manager that appends a fresh token, waits until it is at the head, and pops it in `finally`
    then `notify_all`s; a waiter whose wait raises (any `BaseException`) removes its own token, notifies and
    re-raises, so no abandoned token can block the queue. `OpenAICompatProvider.__init__` creates one; `embed` wraps each `_call_api` in `with self._turns.turn():`. `/tokenize` counts stay outside the
    gate: they do not occupy a slot (#604 measured them under 4 ms during an embedding).
  - Green: the same command passes; then `uv run pytest tests/test_embeddings.py tests/test_embed_limits.py -q`.
  - Commit: `fix(embeddings): keep one embedding request in flight per embedder across concurrent tool calls`.
- [ ] **3. One kept-alive connection for `/v1/embeddings`.**
  Files: `src/knowledge_base/embeddings.py`, `tests/test_embeddings.py`.
  - Red first: through `http_requests`, with `knowledge_base.embeddings.httpx.post` replaced by a function that
    fails the test ("a new connection per request"), embedding 3 texts at the default batch size succeeds with 3
    `/v1/embeddings` requests. The redirect rule keeps a behavioural test instead of a keyword assertion: a 307
    answer to `/v1/embeddings` raises `httpx.HTTPStatusError` and exactly one request is recorded (the redirect is
    not followed). Red: `uv run pytest tests/test_embeddings.py -q -k "kept_alive"`.
  - Change: `_call_api` posts with `self._http.post(...)` (its `timeout=120` and `follow_redirects=False` are the
    client's). The `TestOpenAICompatProvider` tests that patch `knowledge_base.embeddings.httpx.post` move to the
    `http_requests` fixture (AGENTS.md rule 6), keeping every assertion (URL, `Authorization`, no redirects,
    `dimensions`, normalization, the call-time private-host guard, the loopback opt-in).
  - Green: `uv run pytest tests/test_embeddings.py -q` passes.
  - Commit: `perf(embeddings): reuse the provider's kept-alive connection for /v1/embeddings`.
- [ ] **4. Documentation.** Files: `README.md`, `docs/usage/ingesting-documents.md`.
  - No executable behaviour; `uv run pytest tests/ -q -m "not slow"` stays green (the repository's docs checks run
    in the suite).
  - Commit: `docs(embeddings): document KNOWLEDGE_BASE_EMBED_BATCH_SIZE and why its default is 1`.

## Documentation

- `README.md`, Configuration table: a row `KNOWLEDGE_BASE_EMBED_BATCH_SIZE` | optional; only for `openai_compat`,
  `openai`, `llama.cpp` | inputs per `/v1/embeddings` request (default `1`). One input per request lets a query
  from another client wait for one input rather than a whole batch; raising it adds no throughput against a
  llama-server.
- `docs/usage/ingesting-documents.md`, after "Inputs longer than the embedder takes": a short section "How much KB
  sends at once" with the rule (one input per request, one request in flight per embedder, in arrival order),
  the variable, the default and its reason, and the measured comparison in two lines (CPU rig, relative numbers),
  with no host names.

## Testing

- Unit: the settings parse and refusals; the provider's request grouping and result order at 1 and 3.
- Integration: ingest through the transport stub, at the default and at 2, asserting the inputs per request.
- Concurrency: one request in flight across threads, served in arrival order.
- The kept-alive connection: one client for every request of a provider.
- Not tested: query latency itself. It is a property of the server, measured above; the tests pin what KB
  controls, the inputs per request.

## Verification

1. The merge gate at the final clean head, in `AGENTS.md`'s order: `uv lock --check`, `uv sync`,
   `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`,
   `uv run pytest tests/ -q -m "not slow"` (under the heavy-command lock), `uv sync --all-groups`,
   `uv run basedpyright src/ tests/`.
2. Baseline: the new tests of item 1, copied into a throwaway worktree at master `492afdf`, fail there.
3. Live re-check with KB's own code: the same rig rebuilt (same release, GGUF, flags, lock; deleted afterwards),
   draining 256 chunks through `OpenAICompatProvider.embed` built by `get_provider(cfg=...)` with the default
   batch size while the query loop runs, then two such drains on two threads at once. Expected: query p95 of the
   same order as the "1" row above in both, every `/v1/embeddings` request one input.

## Collateral (for the PR body and the worker report)

- **The residual wait is one input's time, and the Mac has not been measured at the served limit.** The recorded
  runs stop at 541-token items (320.9 ms p95 under the 27B). KB's inputs reach 2,048 tokens, and on this CPU a
  near-limit input made a query wait 1.5 s p95. Proposed for the infra runbook: one `embed-bench load --queries 100
  --drain 1 --doc-tokens 2048` run on the Mac under the 27B (it needs the deployed key, so after infra#79).

## Resolution

One review: agy `gemini-3.8-flash-high`, headless round (`-lean-review.md` beside this plan). Planning-review
severity: Default, so every finding is resolved by a revision.

| # | Severity | Finding | Resolution |
| --- | --- | --- | --- |
| 1 | HIGH | An interrupted waiter never advances the ticket counter and deadlocks the gate | Revised item 2: the gate is a queue of waiter tokens, not tickets; a waiter whose wait raises removes its own token and notifies; a test covers it |
| 2 | MEDIUM | The fairness test can release A's request before B has queued | Revised item 2: the test waits until `len(provider._turns._queue) == 2` before releasing |
| 3 | MEDIUM | No test that a failed request releases its turn | Revised item 2: a 500 on A's held request; A raises, B is served |
| 4 | LOW | `tests/test_settings.py` `_EMBED_VARS` would not clear the new variable | Revised item 1: added to `_EMBED_VARS` |
| 5 | LOW | Decision 6 named `ingest` and `reingest` as HTTP tools; `server.NETWORK_TOOLS` serves only `search_index`, `ingest_text`, `ingest_url` | Revised Decision 6: stdio serves every ingest tool, HTTP `ingest_text` and `ingest_url`; both run sync tools in the thread pool |
| 6 | LOW | `follow_redirects` is no longer a per-call keyword once `_call_api` uses the client | Revised item 3: a behavioural test, a 307 is not followed (one request, `HTTPStatusError`) |
| 7 | LOW | No test that configs differing only in `batch_size` get distinct providers | Revised item 1: a `TestProviderConfigCache` case |

