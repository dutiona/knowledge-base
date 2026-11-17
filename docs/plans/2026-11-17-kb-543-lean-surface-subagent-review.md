# Clean-slate subagent review: 2026-11-17-kb-543-lean-surface

Reviewer: a fresh Claude subagent (Opus, general-purpose, no prior context), read-only. Four rounds: revision 1 in
full, then the deltas of revisions 3, 4 and 5. Rounds 1 and 4 are recorded verbatim; rounds 2 and 3 are condensed
to their findings, each kept with its severity and substance. Earlier rounds' digest lines are labelled by round; the
unlabelled one belongs to the last round, bound to the final altitude report.

## Round 1 (revision 1, altitude report 7c993831...)

The plan is not ready for approval yet. It has 1 HIGH and 4 MEDIUM findings, and none is a blocker. The design is sound and most of its claims check out against the code at 91e6a76. Confidence: high on M1 to M3, which I checked directly in the code. Medium on how severe H1 is: it depends on how the deployment ticket is sequenced, which I couldn't see.

Altitude report SHA-256 (round 1): 7c993831a077f9f1a2f3cb0411dd13106cffd3103a5ead5bdd7dbebc0f6d03b5

### Altitude and duplication (assessed first)

- **The report matches this plan.** The plan's SHA-256 (`de26ab32…e82`) equals the one the report records. Its measurements are right: 511 lines, 42,714 bytes, 3 fences, and the file line counts for `server.py`, `auth.py`, `routes/ingestion.py`, `ingest.py`, `web.py`, `tests/test_ingest.py` and `mcp-tools.md` all match.
- **The override is justified.** The plan is both #543's design and its implementation plan. That is acceptable because one approval marker covers both. The 48-row table is the issue's required deliverable. The D7 strings are model-facing text that the instructions test checks exactly. The plan describes code; it does not paste it.
- **Duplication is minor.**
  - The file-by-file list in §Testing repeats each item's Red list.
  - §"Why no HTTP tool deletes holds structurally" overlaps D5 and item 8. That section is the argument for the acceptance criterion, so keeping it is fine. Trimming §Testing down to the seam rules and "Not tested" is optional [LOW].

### Claims I checked and found correct

- The stdio hub registers 47 tools today.
- `@mcp.tool()` returns a plain `function`.
- `FastMCP(tools=[fn])` calls `Tool.from_function`.
- With `mask_error_details`, FastMCP masks every other exception but passes `ToolError` through. It also special-cases 429 and timeouts, which D6 makes irrelevant.
- The deletion sites at `web.py:399`, `502`, `871` and `1020` are all behind the vision or browser switch.
- The reference really lacks `benchmark_spaces_tool` and `search_index`'s `keyword_prefilter`.
- Item 3's defect is real: the web text path has no in-batch dedup of repeated chunks and no rollback.
- There is no UNIQUE on `(source_uri, chunk_index)`, so D4's append cannot violate a constraint.
- `chunk_strategy` defaults to `mechanical`.
- No test patches a name that item 2 removes from `web.py`.
- Contextvars reach sync tools (anyio `run_sync`), so `calling_machine()` works inside a tool.

### Findings

**[HIGH] H1. Nothing says what must land before KB goes on the tailnet (G1, G3, #588, #592).**
- §Verification "After the merge" names only this merge commit as the precondition for publishing.
- Once this merge is published, a token holder can:
  - store a pasted secret unredacted, because #592 comes later;
  - leave it impossible to remove, because of G1 and D4;
  - send content of any size (G3);
  - exhaust memory through `ingest_url`, because #588 comes later.
- I read both issues: neither #588 nor #592 says it gates publication.
- G3's options don't actually bound memory. The plan itself says the SDK reads the request whole, so a cap on the argument only limits embedding. A body cap in `RequireMachine` would bound memory.
- A large ingest also delays a graceful stop: `timeout_graceful_shutdown` is unset, and a cut call's thread runs to its end.
- Recommendation:
  - State the publication preconditions explicitly: this merge plus #588 and #592. For secrets, cite #592's maintenance command, which rewrites matching stored chunks.
  - Either make G1's remove command a precondition too, or accept the gap explicitly.
  - Choose G3's "Else" (cap `ingest_text` in this PR, one constant and one test), or say which ticket bounds the request body.

**[MEDIUM] M1. D4 understates what an append-only revision costs.**
- `chunk_text` cuts fixed 1000-character windows with 200 of overlap (`chunking.py:89-106`). Any insertion or deletion shifts every later window.
- So a revised document does not add "its changed passages". It re-adds almost everything after the first edit.
- Search then returns near-duplicates of both versions, crowding `top_k`, and `chunk_index` values repeat under one URI.
- The same misleading phrase is in the D7 network instructions and in the new tool description.
- Recommendation: state the real consequence in D4 so it is approved knowingly, and correct the text the model sees.

**[MEDIUM] M2. Item 8's test-environment change is incomplete.**
- The suite's default environment, set by the autouse `_declared_environment` fixture, switches both on: `KNOWLEDGE_BASE_VISION=on` and `KNOWLEDGE_BASE_BROWSER=on` (`tests/declared_env.py:21-22`).
- Besides `kb_http` and the subprocess test, these `served`-fixture tests in `tests/test_server.py` call `main(["--http", ...])`:
  - `test_main_serves_http_through_the_gate`, `test_a_bracketed_ipv6_host_is_served_without_its_brackets` and `test_a_server_that_never_starts_exits_1` will now exit 78.
  - `test_the_declaration_gate_runs_before_http_is_served` will exit 78 naming the vision variable instead of `KNOWLEDGE_BASE_EMBED_MODEL`, because item 8 builds the app before `open_declared_db()`.
- Recommendation: switch both off in `served`.
- Item 7's edit to the subprocess test also has to change `call("status", {token: token})` and the two `'status'` log-line assertions, not only the first call.

**[MEDIUM] M3. `test_no_http_tool_deletes` cannot pass as specified.**
- The plan says "every row of every regular table … unchanged".
- But every insert updates `embed_spaces.chunk_count` (through `bump_chunk_count`, `db.py:662`) and `sqlite_sequence` (AUTOINCREMENT).
- Recommendation: specify that rows present before are present after, unchanged apart from those counters. Otherwise the implementer weakens the assertion ad hoc.

**[MEDIUM] M4. The same failure class (#554) exists in file ingest and the plan doesn't mention it.**
- `ingest_file` and `_produce_and_insert_chunks` (`ingest.py:311-486`) have no rollback. The web figure writers don't either.
- A failed insert midway leaves rows in the thread's open transaction, and the next commit stores them. This is exactly what item 3 fixes for text.
- Under the no-silent-deferral rule it belongs in "Collateral findings" with a recommended fix. `ingest.py` is #573's file, so either a follow-up after #573 or one try/rollback now.
- Also worth saying: two machines ingesting overlapping text at the same time race between the existence check and the insert, because embedding runs in between. The loser gets an `IntegrityError` that reads as a generic masked error, and a retry succeeds. That fits "retry later", but the plan should state it.

**[LOW]**
- **L1.** Item 4's new docstring says "nothing replaces or removes a page". That is false on stdio with vision on, where `web.py:399` and `502` replace a page's figure chunks. One description serves both hubs, so say "a page's text".
- **L2.** `search_index`'s docstring is now network-facing. Its operator hints ("install with: uv sync --group reranker", and the `space_name` A/B hint) should be trimmed, as item 4 does for `ingest_url`.
  - The "path" class counts "reveals a server path" as a reason to leave HTTP (row 33), yet search results carry file `source_uri`s, which are server paths. Say that paths in results are accepted.
  - `relocate_paper_tool`, `supersede_conclusion_tool` and `add_relationship_tool` rewrite stored data but aren't labelled destructive. This is cosmetic, since all three leave HTTP.
- **L3.** The structural no-delete argument leaves out the job worker. Job handlers delete (`auto_relate.py:138`, `extraction.py:541-548`), and the worker starts only from `submit_job` (`jobs.py:45,56`), which none of the three tools calls. Add that sentence.
- **L4.** `ingest_text` input checks:
  - Whitespace-only content passes the `""` check and succeeds with 0 added. Refuse when `not content.strip()`.
  - Names: consider refusing C1 controls and bidi overrides, since names are shown in other machines' results.
  - `#` or `?` in a name breaks `urlparse` for consumers such as memory-engine#1028. Either document the identifier as opaque or restrict the characters.
- **L5.** `calling_machine()` returns `None` both when there is no HTTP request and when an HTTP request has no machine. `CallingMachine` must keep telling the two apart. `ingest_text` should refuse, not store `content:///`, when a request is present without a machine.
- **L6.** D6 says "retry later" for every `HTTPStatusError`. A 4xx is not transient (bad key, oversized input). Say "retry later" only for 5xx, 429 and transport errors.
- **L7.** The Green commands for items 2 and 3 leave out `tests/test_indexer.py` (`cmd_ingest_url`) and `tests/test_embeddings.py` (the floor test), which both exercise the moved code.
- **L8.** The Verification grep checks `.ts.net` names and token shapes but not tailnet addresses (the CGNAT range and the tailnet IPv6 prefix). Also confirm whether `docs/plans/*-drafts/` and `*-advisor.md` get committed.
- **L9.** Two deferrals have no tracked follow-up: converging the other 45 tools' error results, and moving file ingest onto `ingest_text`. List them as a G5 or link an issue.
- **L10.** The network instructions contain apostrophes ("page's", "document's"). The instructions test needs a regex like `'([a-z_]+)'`, not `'([^']+)'`.
- **L11.** Issue #543 asks for a design comment on #543 approved before any code. The plan replaces that with the plan-issue marker plus a pointer after the merge, and presents it as the same thing. Call it an override and give the reason.

### Structure and feasibility

- Documentation, Testing and Verification sections are all present and concrete.
- The commit order is sound: the failure contract (5) comes before the new tool (6), the HTTP cut (7) and the refusal (8).
- The only places where a stated Green won't pass as written are M2 and M3.
- I found no over-engineering beyond the optional trims in L11 and the §Testing list.

## Round 2 (revision 3 delta, altitude report efeecd07...)

The revision fixes every finding from my first review. One new MEDIUM remains, plus several LOWs. Nothing blocks approving the design. My verdict is LGTM once M1 is fixed, and the LOWs can be fixed in the same edit.

Altitude report SHA-256 (round 2): efeecd07f95bcb95ee19cba60649f352d42eecafe7055b5219477c6b503c818d

- The binding is valid (plan `2fa8a59e…455c4b`; 591 lines, 51,100 bytes; fences 64-71, 149-159, 566-574; table 199-246; 10 items). Keeping "proceed" is justified.
- [LOW] Some of the report's estimates are now out of date (source "+120/-60" vs components summing to about 173; Design ~250 lines; sequence ~190 lines).
- All round-1 findings resolved (H1 via G5 and item 9; M1 to M4; L1 to L11).
- I checked that the race test can work: the embedding step runs before the main connection's first write, so a second connection can commit the duplicate chunk first.
- **[MEDIUM] M1.** Item 7's edit to the subprocess test, done as written, either fails or loses token-redaction coverage: the second `status {token: token}` call must stay invalid (it asserts `isError is True` and its traceback carries the token the redaction check inspects). Fix: `search_index {"query": "q", "mode": "fts", <token>: <token>}`.
- **[LOW] L1.** Item 4's new `rerank` text is not true: `search.py:417-423` falls back to fused (RRF) ordering instead of failing.
- **[LOW] L2.** The race mapping is only on `ingest_text`; map it in `ingest_url` too ("the page's text was not stored").
- **[LOW] L3.** Item 9's 413 should not carry `connection: close` (a reset could destroy the 413); uvicorn discards the rest of the body itself (`h11_impl.py:261-272`). After replaying the buffered body, hand later `receive()` calls to the real `receive`.
- **[LOW] L4.** "G1 to G4" in the PR step; the after-merge line should name #588/#592 if G5 is accepted; item 9's Files lack `README.md`.
- **[LOW] L5.** "None of them holds … a home-directory path" is wrong (`~/...` paths); the grep matches its own `fd7a:115c:a1e0` text.
- **[LOW] L6 (optional).** `Zl`, `Zp` and `Cs` still pass the name check.
- Structure and feasibility: sections present; item 9 fits the order; apart from M1, every Green step can pass as written.

## Round 3 (revision 4 delta, altitude report ed45439a...)

LGTM, with three LOW nits for the same edit.

Altitude report SHA-256 (round 3): ed45439ae7c4da984b719aef4791336603e0c28af976ef42e28e76ad4d63b1d6

- The report is bound to the plan (`c3e4f171…95b2b`); figures correct; "proceed" justified.
- All round-2 findings resolved (M1: pydantic still rejects the unknown argument; L1 matches the fallback; L2: the only `IntegrityError` reaching `ingest_url` comes from the text step; L3 accepted: the server reads an oversized body to its end, but only token holders pass the gate; L4 to L6 fixed).
- **[LOW] L1.** The grep matches two plan lines, not one.
- **[LOW] L2.** Item 6's refusal message and test names do not cover the new `Zl`, `Zp`, `Cs` categories.
- **[LOW] L3.** §Documentation does not say where `source_uri` is documented as opaque.
- No BLOCKER, HIGH or MEDIUM remains.

## Round 4 (revision 5 delta, the final altitude report)

LGTM. The three LOW wording fixes from round 4 are applied correctly, and I found nothing new at BLOCKER, HIGH, MEDIUM or LOW.

Altitude report SHA-256: 0c28f11a87d4c778fe12854d645efa49f8b3571a817c878074b5fdd09b784b02

**Altitude report.** It is bound to the current plan: the plan's SHA-256 (`720899d4…5e17da3`) is the one the report records. Its measurements are all correct (605 lines, 52,595 bytes, the fence, table and section line ranges). Its "proceed" verdict is justified, since this revision only adds two lines of wording.

**The three fixes:**
- **Item 6, refused name characters.** The refusal message now names control, format, surrogate and separator characters. The test now covers a line separator (`"a b"`) and a lone surrogate (`"a\ud800b"`).
  - The surrogate case can reach the tool: the MCP SDK parses request bodies with Python's `json.loads` (`mcp/server/streamable_http.py:467`), which accepts the escape. The parameters are typed `Any`, so I expect pydantic not to reject it before the tool's own check.
  - Keep the name out of the refusal text, as the plan already does. If the error echoed a lone surrogate, serialising the response would likely fail.
- **Leak grep.** Run over `docs/plans/*.md`, it now matches only line 589, the command itself. The new wording ("the plan's own Verification lines that quote its patterns") is accurate.
- **Opaque `source_uri`.** The Documentation section now puts this statement in the identity table of the Surfaces section in `docs/reference/mcp-tools.md`.

## Resolution

- [HIGH] H1 (publication preconditions; G3 does not bound memory) → G5 added (this merge plus #588 and #592 gate the
  deployment tag, for Michaël); G3 recommends a 4 MiB request-body cap at the HTTP boundary in this PR (item 9), with
  its scope stated (Codex round 3).
- [MEDIUM] M1 (append-only revision cost) → D4, the network instructions and the tool description state the
  shifting-window cost and ask for a new name per revision.
- [MEDIUM] M2 (served fixture, subprocess test) → item 8 switches both off in `served`; item 7 rewrites both calls,
  keeping the rejected-argument call rejected (round 2 M1).
- [MEDIUM] M3 (impossible no-delete assertion) → `embed_spaces.chunk_count` and `sqlite_sequence` allowed (also Codex
  round 1).
- [MEDIUM] M4 (file-ingest rollback; concurrent race) → G6; D6 and items 5/6 map the race in both ingest tools, with a
  test.
- [LOW] L1 to L11 (round 1), L1 to L6 (round 2), L1 to L3 (round 3) → all applied in revisions 3 to 5.
- Round 4: LGTM, nothing to change.
