# Codex external review (headless, thread resumed across rounds), all rounds; the final round is last

## Round 1

Michaël, the plan needs revision before implementation. Confidence is high in the findings below; this was a read-only review, so the implementation owner must verify the proposed fixes and test outcomes.

The altitude report’s **proceed** disposition is reasonable: the security boundary crosses several existing modules, and the plan assigns concrete work without embedding implementation-sized payloads. Its LLM switch is an explicit, justified scope override for the stated egress outcome, subject to your approval. The plan contains Documentation, Testing, and Verification sections.

Altitude report SHA-256: aca6edb96f6be734f2e2323385bf2c096f372a0b5ecb73f6f89dbadf8e8660c9

- **[BLOCKER] Tool-controlled embedding spaces can defeat the declared endpoint boundary.** Items 7–8 and D3–D4 leave `create_embed_space_tool`, `backfill_embed_space_tool`, and `promote_embed_space_tool` available. A caller can create a space with provider `ollama`; `backfill_space` combines that tool-selected family with the declared URL ([embed_swap.py](src/knowledge_base/embed_swap.py:370)). Ollama skips the SSRF floor and posts to `/api/embed` ([embeddings.py](src/knowledge_base/embeddings.py:157)), even if the declaration selected OpenAI-compatible `/v1/embeddings`. Promotion also changes the recorded provider, model, and dimension during the running process ([embed_swap.py](src/knowledge_base/embed_swap.py:500)). Enforce the declaration across space backfill and promotion, with tests exercising the MCP tools and asserting both request destination and identity.

- **[HIGH] Tool-supplied URLs do not consistently pass the SSRF floor.** Item 14 tests direct calls on a private declared host, but `ingest_url` follows redirects before checking the final host; a public URL can therefore cause a request to a private address ([web.py](src/knowledge_base/web.py:1083)). Item 12 also permits `configure_llm` with an Ollama URL when the switch is on, and that path skips validation ([llm.py](src/knowledge_base/llm.py:362)). Owner call (d) likewise leaves vision and OmniParser URLs unfloored. These conflict with the acceptance criterion that **any URL a tool supplies** goes through the floor. Cover each request path, including redirects and enabled switches, rather than only direct requests with defaults off.

- **[HIGH] The documented re-embedding procedure cannot change provider under the gate.** D2 and item 13 require the CLI’s `re-embed` command to bind the existing database to the declaration before running; the procedure then changes identity variables only after re-embedding. Yet `re_embed` accepts a new provider and uses the current connection details for backfill ([indexer.py](src/knowledge_base/indexer.py:145), [embed_swap.py](src/knowledge_base/embed_swap.py:622)). A provider or endpoint migration either runs using the old declaration or is rejected before it starts. Define and test an authorized stopped-service transition that preserves the endpoint boundary throughout.

- **[HIGH] Existing configured endpoints can be rejected despite being equivalent.** D1 records OpenAI-family URLs with `/v1`, while the existing writer strips that suffix before storing it ([embed_swap.py](src/knowledge_base/embed_swap.py:227)). Item 6 compares recorded fields for equality, so an existing `https://host` row disagrees with the declaration normalized to `https://host/v1`. Normalize legacy values consistently before comparison and test a database created through the old configuration path.

- **[HIGH] ONNX contradicts the endpoint acceptance criterion.** D1 explicitly refuses a URL for `onnx`, while D2 allows it to pass the startup gate. The issue requires KB to exit nonzero with no explicit endpoint. The plan needs an explicit contract decision for local ONNX before treating this as an accepted exception; its current acceptance-to-proof mapping does not address it.

- **[HIGH] The final verification omits the required full test suite.** The project instructions require `uv run pytest tests/ -q` after every change; the plan’s Verification section runs only `-m "not slow"`. Add the full suite and the required lint and format checks to the commit-level verification sequence. The focused tests described per item do not establish the claim that each commit leaves the suite green.

- **[MEDIUM] Several specified red results do not follow from the tests.** In item 7, constructing `OpenAICompatProvider(None)` does not make an HTTP request; its request occurs only in `embed` ([embeddings.py](src/knowledge_base/embeddings.py:189)). Thus the proposed exception assertion fails without the claimed recorded POST. Item 14’s new `exempt_host` keyword also fails before it can prove the end-to-end behavior. Split API-existence failures from behavioral red tests and specify the observed failure for each.


## Round 2

Michaël, the revised plan still needs changes before implementation. Confidence is high in the findings below.

The refreshed altitude report’s **proceed** disposition is reasonable: the growth is attributed to concrete changes to the provider builder and maintenance transitions, with no implementation-sized payload embedded in the plan. The proposed interpretation of the tool-URL SSRF criterion remains an owner call, not an established acceptance of that criterion.

Altitude report SHA-256: 547c687adcf94a0757ab1233eb036fd9a1f70abd261421d933f532fcad84ee80

- **[HIGH] Item 12’s new key guard can be bypassed by an existing LLM configuration.** It rejects the protected key spec only when passed to `configure_llm`. An existing `llm_api_key=env:KNOWLEDGE_BASE_EMBED_API_KEY` remains readable by `_get_llm_config` ([llm.py](src/knowledge_base/llm.py:40)). A tool call can change an HTTP LLM endpoint while omitting `api_key`; the old key row is retained and used in the connectivity probe ([llm.py](src/knowledge_base/llm.py:385)). Validate the effective stored key before any probe or request, and test this upgrade case.

- **[HIGH] Item 14 can leave a failed re-embed appearing fully bound.** The transition writes the new endpoint before backfill. If provider, model, and dimension remain the same but the endpoint changes, a failed backfill leaves the old active vectors and the new endpoint recorded; a normal bind can then pass. The current lifecycle commits space creation and each batch independently ([embed_swap.py](src/knowledge_base/embed_swap.py:328), [embed_swap.py](src/knowledge_base/embed_swap.py:427)). Specify a durable transition state and recovery behavior, and test interruption and failed backfill before allowing normal startup.

- **[HIGH] Item 14 does not specify recovery when promotion’s summary rebuild fails.** `promote_space` commits the active-space and identity changes before rebuilding folder-summary vectors ([embed_swap.py](src/knowledge_base/embed_swap.py:500)). A request failure during that rebuild can leave the new identity bound with incomplete or stale summaries. Define a recoverable completion boundary and test failure after promotion.

- **[HIGH] D3 allows an undeclared model and dimension to be used for embedding.** The new builder checks only the provider, and D3 explicitly permits backfilling a same-provider comparison space. A tool can therefore choose another model and dimension, and `backfill_space` sends those values with stored document text ([embed_swap.py](src/knowledge_base/embed_swap.py:358), [embed_swap.py](src/knowledge_base/embed_swap.py:412)). This contradicts the revised outcome that the declaration is the only embedding identity. Either enforce the full identity before backfill or obtain an explicit contract decision for comparison spaces.

- **[HIGH] The newly introduced ONNX endpoint is not bound to the database.** D1 requires `ONNX_EMBED_MODEL_PATH` but explicitly does not record it. Changing that path to a different existing model file therefore passes the planned database comparison while the provider loads the new file ([embeddings.py](src/knowledge_base/embeddings.py:299)). Bind the local endpoint to the recorded configuration and define its transition, or resolve this exception explicitly.

- **[MEDIUM] Per-commit verification still excludes slow tests.** The delta adds the full suite to final Verification, but the implementation sequence explicitly defines each item’s green as `pytest ... -m "not slow"` and claims that establishes a green suite. Slow tests exist, including [test_pymupdf4llm_integration.py](tests/test_pymupdf4llm_integration.py:130). The project instructions require the full suite, lint, and format checks after every change.


## Round 3

Michaël, the revised transition rules still have failure and recovery gaps. Confidence is high in the findings below.

The altitude report’s **proceed** disposition is reasonable: the added detail assigns concrete recovery and validation work, and the comparison-space exception is now an explicit owner call. The plan remains reviewable at its current size.

Altitude report SHA-256: 3fbb853794ed9d51a3ec7bff5fc9469bc4d2e58cea205649cb987636093b948b

- **[HIGH] A refused re-embed can still adopt the new endpoint.** D2 says `transition="re-embed"` records the declared URL before item 14 checks whether the active identity already matches and refuses with “use rebind.” If that write is committed, a changed URL with the same identity is adopted by a command that reports failure, and the next plain bind can pass. Require the refusal before any persistent endpoint mutation, and test the database and subsequent plain open after this failure. The existing CLI opens the connection before invoking `re_embed` ([indexer.py](src/knowledge_base/indexer.py:145)).

- **[HIGH] The new resume rule can mix vectors from different endpoints.** Item 14 reuses a populating space based only on `(provider, model, dim)`, while the transition may overwrite the endpoint. If a run writes one batch at endpoint A, fails, and is retried with endpoint B under the same identity, backfill skips existing vectors and writes the remainder from B ([embed_swap.py](src/knowledge_base/embed_swap.py:399)). The space can then be promoted with mixed vectors. Bind resumable work to its transition endpoint and relevant space parameters, or discard and rebuild it when they differ; test a retry after an endpoint change.

- **[HIGH] The new reuse rule does not preserve the requested space configuration.** Item 14 selects a populating or deprecated space solely by provider, model, and dimension, although spaces also carry `chunk_strategy`, `matryoshka_base_dim`, and `element_type` ([embed_swap.py](src/knowledge_base/embed_swap.py:273)). A matching old space can be promoted with a different strategy or Matryoshka dimension than the requested transition. Define matching and ambiguity rules for all parameters that affect the resulting space, with tests for multiple matching identities.

- **[HIGH] Owner call (g) leaves the new CLI without a recovery path after a failed summary rebuild.** This is new evidence beyond the recorded pre-existing ordering: after `promote_space` commits the target identity and the summary rebuild fails ([embed_swap.py](src/knowledge_base/embed_swap.py:525)), the revised CLI refuses a retry because the active identity already matches. A plain bind also passes, despite incomplete summary work. Make the transition detectably incomplete until summaries finish, and provide a retry that completes them.

- **[MEDIUM] Item 14’s focused red command omits its rollback test.** The `-k` expression contains `re_embed_moves` but not `rolls_back`, so `test_cli_re_embed_rolls_back_to_a_deprecated_space` is not selected. Include it in the focused red and observe its failure before implementation; the later full-suite run establishes only the green side.


## Round 4

Michaël, the simplified transition resolves the space-reuse ambiguity, but two recovery details still need to be specified. Confidence is high.

The altitude report’s **proceed** disposition is reasonable: the delta replaces the reuse rules with a smaller transition and explains the cost of a full re-embed on rollback. Owner call (g) makes that cost explicit.

Altitude report SHA-256: 5f5f969cc2ef21eadc732100939622a8ca04d0974ab60846e4771f306cd7507c

- **[HIGH] Item 14’s all-or-nothing test does not exercise a failure during the transaction.** It makes summary embedding fail *before* the status and vector-table changes. The current summary helper drops and recreates the virtual table, inserts rows, and commits ([embed_swap.py](src/knowledge_base/embed_swap.py:663)). The revised plan must remove the helper’s independent commits and explicitly roll back on a failure after writes begin; otherwise a reused connection can retain partial changes. Add a test that fails during table recreation or insertion and verifies the old state from both the same connection and a fresh connection.

- **[MEDIUM] The documented abandoned-space cleanup cannot work as written.** Item 14 says to remove an abandoned `populating` space with `cleanup_embed_space_tool`, but `cleanup_space` accepts only `deprecated` spaces ([embed_swap.py](src/knowledge_base/embed_swap.py:569)). The MCP server also cannot serve that tool while the interrupted transition’s declaration disagrees with the active space. Specify a workable recovery sequence, including deprecation after successful startup, or provide an appropriate stopped-service cleanup path and test it.


## Round 5

Michaël, the revised transaction addresses the earlier rollback gap, but the plan still needs two recovery and concurrency cases. Confidence is high.

The altitude report’s **proceed** disposition is reasonable: the added detail specifies the transaction boundary and failure tests without embedding implementation code.

Altitude report SHA-256: a63dc38e7ed6879c0aaf73b00772e8e9de864460d5fc7670a2394ee4a0a348ba

- **[HIGH] Item 14 prepares summaries before taking the write lock, so promotion can commit stale vectors.** `promote_space` remains callable through the MCP tool ([routes/embeddings.py](src/knowledge_base/routes/embeddings.py:173)). Another connection can update or remove a folder summary after the proposed embed step but before `BEGIN IMMEDIATE`; promotion then commits vectors for the old snapshot. Folder-summary updates write both the summary and its vector ([folder_summaries.py](src/knowledge_base/folder_summaries.py:144)). Revalidate the prepared input under the transaction or otherwise serialize it, and test an interleaved update. The live-corpus completeness check also needs to remain valid at the commit boundary ([embed_swap.py](src/knowledge_base/embed_swap.py:452)).

- **[MEDIUM] Item 14’s exception cleanup does not handle process interruption.** Space creation and backfill batches commit independently ([embed_swap.py](src/knowledge_base/embed_swap.py:328), [embed_swap.py](src/knowledge_base/embed_swap.py:427)). A killed process or machine restart cannot run the proposed exception handler, leaving a populating space. The new no-orphan test covers a raised backfill failure, not interruption. Define and test recovery of that durable orphan, and qualify the documentation’s claim that a failed run removes its space.


## Round 6

Michaël, the revised promotion lock closes the race during the transaction, but two transaction-boundary issues remain. Confidence is high.

The altitude report’s **proceed** disposition is reasonable: the delta explains the added lock and cleanup behavior, and the plan remains a work assignment rather than embedded implementation.

Altitude report SHA-256: 7c44bfaf5e95e89f93454875b5fbe7f816b749ae4a2f3ac0e4b033117b23a88a

- **[HIGH] Item 14’s write lock does not protect against a writer that prepared an old vector before promotion.** A concurrent folder-summary update reads the active identity and embeds before its first write ([folder_summaries.py](src/knowledge_base/folder_summaries.py:107)). If promotion acquires the lock while that embedding is in flight, the update can wait and then write its old-model vector after promotion commits. Equal dimensions can make that corruption silent. Revalidate the active identity at the writer’s commit boundary or serialize the entire operation, and test this interleaving.

- **[HIGH] Setting `isolation_level = None` can commit a caller’s pending transaction.** Item 14 explicitly proposes this to handle a connection that already has an implicit transaction. Switching Python’s SQLite connection into autocommit commits that pending transaction before `BEGIN IMMEDIATE`; a subsequent promotion failure cannot roll those writes back. The cited migration pattern ([migrate.py](src/knowledge_base/migrate.py:229)) does not establish that this is safe for a shared promotion connection. Define transaction ownership: reject a connection already in a transaction, or use an approach that preserves the caller’s transaction, and test pending writes plus a failed promotion.


## Round 7

Michaël, LGTM for the round-7 delta. Confidence is moderate: this is a read-only plan review, and implementation tests remain the owner’s responsibility.

The altitude report’s **proceed** disposition is reasonable; the growth is explained by the transaction and concurrency decisions. The remaining lifecycle race is explicitly recorded for your decision as collateral.

Altitude report SHA-256: 3207898e6138026ff5d5994c93a2288290eb196150e4223e2a6789415fb824d0


## Round 8

Michaël, LGTM for the round-8 delta. The three edits are feasible against the current source; the unit-stopped requirement remains an explicitly documented owner decision. This was a read-only review.

The altitude report’s **proceed** disposition is reasonable, and the five-line growth is explained by the three changes.

Altitude report SHA-256: 26ed18e6eb4b66b711db7bc58a90861d3a6ce7ff990bfc9fb5dc976b031d1235


REVIEW COMPLETE
