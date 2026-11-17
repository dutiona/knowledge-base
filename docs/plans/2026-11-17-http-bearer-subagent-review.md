# Clean-slate subagent review (round 1, general-purpose on Opus)

Michaël, here is the review of the #571 HTTP Bearer plan.

**Verdict: approve after fixes. There is no BLOCKER and no HIGH, but six MEDIUM findings should be fixed in the plan before implementation starts.** I have high confidence in the FastMCP, mcp and uvicorn claims, because I checked each one against the installed sources. I have medium confidence in M4, because I did not check how rootless podman forwards ports to a server bound to loopback.

Altitude report SHA-256: 8aaa4742ce04ffb9ce0a44fb0449a95ec1c1b37cd604d84b6c81454674ce66ed

## Altitude and duplication (assessed first)

- **Digests.** The plan's SHA-256 is `c110bd30…83be4`, the digest the altitude report is bound to. All seven review inputs match the digests in its table.
- **Disposition "proceed": I agree.**
  - The plan settles decisions, signatures, exact strings and the failing test of each item. It contains no full product file and no second copy of a validator.
  - The ADR-0020 grammar is quoted once as a constraint. Item 4 adds only what is needed to match ME byte for byte.
  - The test tables in item 4 are ME's `auth.rs` table, not new inventions.
- **Duplication.** It is mild and justified:
  - The Outcome restates the decisions.
  - The README subsection repeats ME's status table, mint recipe and curl check. KB's operator docs have to stand on their own, and the token-file format is referenced, not restated.
- **Overrides I accept explicitly:**
  - **a.** A pure ASGI gate instead of FastMCP's `auth=`. The SDK accepts the scheme in any case and reads only the first header (`mcp/server/auth/middleware/bearer_auth.py:32-35`). `RequireAuthMiddleware` sits on the `/mcp` route alone (`fastmcp/server/http.py:334-344`).
  - **b.** No 403, unlike ME's Host check. ADR-0020 §3 says "403 is never sent by the server", and the contract wins over the brief.
  - **c.** No environment variable. It is defaulted as question 1 and can be added later.
  - **e.** Unbounded grace instead of FastMCP's 0 and ME's 5 s connection grace. FastMCP runs sync tools through `anyio.to_thread.run_sync` (`fastmcp/utilities/async_utils.py:13-21`), whose default waits for the worker thread when cancelled. So a finite grace only throws away the answer; it does not shorten the stop.
  - **h.** Exit 78 instead of argparse's 2.
  - **i.** No IPv6 literal.

## API claims checked against the installed sources (all correct)

- `http_app(path, middleware, json_response, stateless_http, …)` exists. Its lifespan enters `server._lifespan_manager()` and `session_manager.run()` (`http.py:366-369`), so running the bare app under uvicorn is complete.
- The session manager is built without `security_settings` (`http.py:301-307`). DNS-rebinding protection is therefore off (`transport_security.py:43,114`).
- `run_http_async` uses `port or settings.port` (`transport.py:241`) and defaults `timeout_graceful_shutdown` to 0 (`transport.py:257`).
- In JSON mode, a POST only needs `Accept: application/json` (`streamable_http.py:422-440`).
  - A wrong Content-Type gets 400 from `validate_request`, not 415. The README's "other 4xx" wording covers that.
  - A stateless DELETE gets 405 (`streamable_http.py:749-758`).
  - A stateless GET gets 405 from Starlette, because the route only allows POST and DELETE (`http.py:347`).
- `get_http_request()` raises `RuntimeError` when there is no request (`dependencies.py:392-409`).
- `FastMCP(middleware=…)` exists, and `on_call_tool` gives `context.message.name`.
- A `ToolError` raised in middleware becomes `isError: true` inside an HTTP 200 (`mcp/server/lowlevel/server.py:583-584`).
- `websockets` 16.0 is installed, so uvicorn's default `ws="auto"` would turn an upgrade into a websocket scope, and the gate's close would become uvicorn's 403. `ws="none"` is needed.
- uvicorn closes the duplicated socket it was given when it shuts down (`server.py:275-278`). The held original keeps listening, so decision g works.
- The ADR test vector checks out: `f7ca4ebc…13aa`.

## Findings

**[MEDIUM] M1 — Decision d says "a probe cannot fill the journal", and the planned uvicorn configuration does not deliver that.**
- With `ws="none"`, every unauthenticated request carrying `Connection: upgrade` makes uvicorn log two WARNING lines on `uvicorn.error` (`h11_impl.py:155-167`).
  - One of them is "No supported WebSocket library detected. Please use pip install 'uvicorn[standard]'…". That invites an operator to install a WebSocket library or switch back to `ws="auto"`, which reopens the 403 path decision b closed.
- Malformed requests log "Invalid HTTP request received." (`h11_impl.py:179-182`).
- uvicorn's default logging configuration prints `uvicorn.error` at INFO to stderr.
- `uvicorn.Config.__init__` also runs `logging.config.dictConfig`, which closes every existing logging handler in the process, the test process included (`config.py:364-372`).
- **Fix:** pass `log_config=None, log_level="error"` in `uvicorn_config`.
  - uvicorn's loggers then go through main()'s single `basicConfig`.
  - Bind failures, startup failures and application exceptions (ERROR) still show; the per-request warnings do not.
  - The refusal-matrix test can then assert, through caplog, that no uvicorn record appears for refused requests.
- If you prefer, keep the configuration but correct the claim and add it to the accepted residuals.

**[MEDIUM] M2 — A startup failure after the gate exits 0.**
- When the lifespan fails, `uvicorn.Server.run()` simply returns (`server.py:104-107`, `_serve` 95-97).
- uvicorn's own command line turns `not server.started` into exit 3 (`main.py:613-614`). Item 7's `uvicorn.Server(...).run()` has no such check, so KB exits 0. `Restart=on-failure` then does nothing and the unit looks like it stopped cleanly.
- **Fix:** keep the `Server` object and add `if not server.started: sys.exit(1)` (or 3).
- The harness has the same blind spot. "Re-raises the thread's error if it dies first" raises nothing in this case, so `start()` must also fail when the thread ends without `started`.

**[MEDIUM] M3 — Decision i lets a host typo restart-loop the unit, the failure decision h exists to prevent.**
- Any non-empty HOST without `:` reaches `loop.create_server`. For example, `127.0.0.l` fails name resolution, and uvicorn logs it and calls `sys.exit(1)` (`server.py:170-182`).
- ME's `--listen` is likely a Rust socket address, which only accepts an IP literal. I did not check this: `me1018-main.rs` is not among the review inputs.
- **Fix:** check `ipaddress.IPv4Address(host)` in `_host_port`, and add `127.0.0.l:80` and `localhost:80` to the table in `test_a_bad_command_line_exits_78`.
- The bind errors that remain (port in use, address not on the host) are transient, so exit 1 is right for them.

**[MEDIUM] M4 — The container text in the Documentation section leaves out the bind address inside the container (likely).**
- It says to publish the port on the host's loopback only. But a server bound to 127.0.0.1 inside a bridge or slirp4netns container is generally not reachable through a published port.
- dutiona/infra's `docs/runbooks/memory-services.md` shows KB running as a rootless quadlet with `Network=slirp4netns:…`, not host networking.
- The README's "bind loopback" advice and the residual "a non-loopback `--http` sends tokens in clear" would steer an operator away from the bind the container actually needs.
- **Fix:** one sentence in `installation.md`: inside the container use `--http 0.0.0.0:<port>`, with `PublishPort=127.0.0.1:<port>:<port>`; the clear-text residual then covers only the hop inside the host.
- For a container, also name podman's stop timeout (`StopTimeout=`, default 10 s) as what bounds a stop under decision e.

**[MEDIUM] M5 — The Verification smoke test can write to the developer's real database.**
- `uv run knowledge-base --http …` with no `KNOWLEDGE_BASE_DB` or `KNOWLEDGE_BASE_DATA_DIR` runs the #569 gate against `~/.local/share/knowledge-base/knowledge.db`. It may seed a blank database or record a legacy URL.
- `installation.md` already warns that a KB entry point "can start the job worker, whose crash recovery resets running jobs".
- **Fix:** run the smoke test with both variables in the scratch directory and a declaration exported.

**[MEDIUM] M6 — The checks in items 3 and 9 can never pass.**
- `rg -n 'manages thread-local database connections' docs/` and `rg -n "Until the server gains an HTTP transport" docs` both match this plan and its drafts under `docs/plans/`, which are untracked but not git-ignored. I ran both: they list `docs/plans/2026-11-17-http-bearer.md` and the drafts.
- **Fix:** add `--glob '!docs/plans/**'`, or use `git grep` so only tracked files are searched.

**[LOW] L1 — The Decisions preamble describes the spikes inaccurately.**
- Both spikes put the gate in `middleware=[Middleware(Gate)]`, which runs inside FastMCP's `RequestContextMiddleware`. Spike 1 ran over httpx's in-process ASGI transport, not uvicorn.
- That is not "an ASGI layer around `mcp.http_app`". The outermost wrap the plan chooses is strictly stronger, and item 6 tests it for real; only the sentence needs correcting.

**[LOW] L2 — Some wording in decisions d and f overstates or is ambiguous.**
- `run_http_async` does not "force" grace 0: `uvicorn_config={"timeout_graceful_shutdown": None}` overrides it (`transport.py:257-261`). The other two reasons still stand.
- "No `FASTMCP_*` variable changes the served surface" is not quite true. `FASTMCP_DEBUG` still turns on Starlette's debug mode (`http.py:375`), which puts tracebacks in 500 bodies, for token holders only.
- Decision d's "No HTTP request (stdio, in-memory) runs the call unlogged" reads as the opposite of what is meant.

**[LOW] L3 — The `_conn._local` monkeypatch in `kb_http` rests on a wrong mechanism.**
- anyio's thread pool is tied to one event loop, and every uvicorn run is a fresh loop. A server's worker threads therefore never carry a cached connection from an earlier test.
- Drop the patch, or give it a correct reason.
- Separately, the gate-less test needs the same database setup `kb_http` does. Put that setup in a fixture both tests use.

**[LOW] L4 — The real-process test should not expect exit code 0.**
- After a graceful stop, uvicorn restores the default handler and re-raises the SIGTERM it caught (`capture_signals` in `server.py`). The child ends with returncode -15, where ME exits 0.
- Asserting only "ended", as the plan does, is right; keep it that way. systemd treats a SIGTERM death as clean, and today's stdio server already ends that way.

**[LOW] L5 — Optional hardening of the 401.**
- The residual says "a refused request's body is never read", but h11 reads and discards it to keep the connection alive. Each chunk received also cancels the keep-alive timer, so a client trickling bytes holds the connection open.
- A `connection: close` header on the gate's 401 makes uvicorn close the connection right after the response.
- `server_header=False` removes the `server: uvicorn` header from unauthenticated answers. ME's axum sends no such header.

**[LOW] L6 — The README's "Limitations: Single-user, single-process" goes stale.**
- Several machines now share one process. Update the line next to the new HTTP subsection, and say there that every token holder reaches every tool until #543.

**[LOW] L7 — Item 0 says to "run Verification once" at baseline.**
- That includes the smoke test and the leak guards, which cannot run before `--http` exists. It should say "the gate command list".

## Structural completeness

The Documentation, Testing and Verification sections are all present and concrete. They are complete once M4, M5, M6 and L6 are fixed.

## Feasibility

- Items 4 to 8 can be done in the stated order, and each red step is real.
- Every mutation in item 6 goes red for the reason the plan gives. Under `timeout_graceful_shutdown=0`, uvicorn cancels the task and the client gets 500 (`run_asgi` calls `send_500_response`), not the expected 200.
- I found no security bypass:
  - The gate runs before any FastMCP code; only uvicorn's own proxy-header middleware runs ahead of it.
  - The request state is rebuilt for every request.
  - `Authorization` is removed before FastMCP sees the request.
  - The check before hashing and the constant-time comparison match ME.

## Resolution

Severity mode Default: every finding addressed in the plan.

- [MEDIUM] M1 "uvicorn logging contradicts decision d" → `uvicorn_config` adds `log_config=None, log_level="error"`;
  decision d explains why (per-request WARNINGs, the WebSocket-library advice, `dictConfig` replacing handlers). The
  caplog assertion on uvicorn records was not added: it would test a dependency's incidental output.
- [MEDIUM] M2 "a startup failure exits 0" → decision f and item 7: `sys.exit(1)` when `not server.started`, tested by
  `test_a_server_that_never_starts_exits_1`; `KbHttp.start()` fails when the thread ends without `started`.
- [MEDIUM] M3 "a host typo restart-loops" → decision i: HOST is an IP literal (IPv4, or bracketed IPv6), checked with
  `ipaddress`; `127.0.0.l:80`, `localhost:80` and `[::1` join the refusal table, `[::1]:8000` is accepted.
- [MEDIUM] M4 "container bind address" → Documentation: `--http 0.0.0.0:<port>` inside the container,
  `PublishPort=127.0.0.1:<port>:<port>`, podman's `StopTimeout=`; the plain-HTTP residual and README wording now say
  "reachable from the host only" instead of "bind loopback".
- [MEDIUM] M5 "the smoke can write the developer's database" → Verification: `KNOWLEDGE_BASE_DB` and
  `KNOWLEDGE_BASE_DATA_DIR` in scratch, declaration exported.
- [MEDIUM] M6 "the doc checks match the plan itself" → items 3 and 9 use `git grep … -- docs/` (tracked files only).
- [LOW] L1 → the spike paragraph now says spike 1 used a `middleware=` entry over httpx's ASGI transport and the plan's
  outermost wrap is stronger.
- [LOW] L2 → "forces grace 0" became "defaults the grace to 0"; `FASTMCP_DEBUG` named as an accepted residual;
  decision d's stdio sentence reworded.
- [LOW] L3 → the `_conn._local` patch is dropped with the reviewer's reason; a `declared_db` fixture serves `kb_http`,
  the gate-less test and the in-memory test.
- [LOW] L4 → item 8 states why the child's exit status is not asserted.
- [LOW] L5 → adopted: the 401 carries `connection: close`, uvicorn runs with `server_header=False`; the body residual
  is corrected (h11 discards it; no KB or FastMCP code reads it).
- [LOW] L6 → Documentation: README "Limitations" updated (one process shared by every token holder, every tool until
  #543).
- [LOW] L7 → item 0 runs the gate command list only.

# Round 2 (delta review, same subagent)

Michaël, here is the round-2 review of the #571 HTTP Bearer plan, delta only.

**Verdict: not LGTM yet. One HIGH remains: the fix for Codex's token-in-log finding is incomplete.** One round-1 MEDIUM (M6) is only partly fixed. All my other round-1 findings are fixed and add no regression. I have high confidence: every point below was checked against the installed mcp 1.26.0, FastMCP 3.1.0 and uvicorn 0.41.0 sources, h11 and Python 3.12's asyncio, plus your local finish-pr and address-issue skills.

Altitude report SHA-256: a675e4fbd71410b4cd914419f70d84ace5d6a8da12d3dfe231e854cb30edf487

## Altitude and duplication (assessed first)

- **Digests.** The plan's SHA-256 is `e3ceb6b4…c1e`, the digest the new altitude report is bound to. The report's own digest is the line above.
- **Disposition "proceed": I agree.** The plan grew by 37 lines. All of it is decision text and test rows the findings asked for: no new literal, file or catalogue.
- **Duplication.** Decision d is now long. Its logging rationale repeats in item 6's "one comment each" line, which is acceptable.
- **Override I accept explicitly:** decision i now also accepts a bracketed IPv6 host. That is new scope ("not needed today" in round 1), justified as parity with ME's `--listen`. The cost is one branch and two test rows. ME's flag type is still unverified: `me1018-main.rs` is not among the review inputs.

## Round-1 resolutions checked

| Finding | Status | Note |
|---|---|---|
| M1 | Fixed | `log_config=None` skips `dictConfig`, and `log_level="error"` raises `uvicorn.error` to ERROR (`uvicorn/config.py:364-400`). The upgrade and malformed-request WARNINGs (`h11_impl.py:155-182`) stay quiet. Bind, startup and application errors go to root and still show. Not adding a test on uvicorn's own records is a fair call. |
| M2 | Fixed | Exit 1 when `not server.started`, with a test for it; `KbHttp.start()` also fails when the thread ends without `started`. |
| M3 | Fixed | `127.0.0.l:80` and `localhost:80` give 78. For `[::1` (no closing bracket), splitting at the last `:` gives host `[:`, which is refused. |
| M4 | Fixed | `0.0.0.0` inside the container and `PublishPort=127.0.0.1:…` is correct. Even if rootless port forwarding does reach a loopback bind, `0.0.0.0` covers loopback, so the instruction is safe either way. |
| M5 | Fixed | The smoke test now points both variables at scratch. |
| M6 | Partly fixed | See MEDIUM below. |
| L1, L2, L3, L4, L6, L7 | Fixed | The claim that anyio's thread pool is tied to one event loop is right. |
| L5 | Fixed | h11 honours `connection: close` (`h11/_connection.py:84-90`), so uvicorn closes the connection after the 401 (`h11_impl.py:523-525`). `server_header=False` exists. One side effect: asyncio closes without lingering, so a refused client still sending a body may get a reset after the 401. httpcore reads an early response after a write error, so this is acceptable; no change needed. |
| Codex tools/list check | Fixed | It now compares sets of names. |
| Codex token in the log | **Not fixed** | See HIGH below. |

## Findings

**[HIGH] The SDK still writes the raw tool name to stderr, so a token sent as the tool name still reaches the journal, and a newline in the name can still forge lines.**

The mcp SDK logs an unknown tool's name at WARNING before any FastMCP middleware runs:
- In mcp 1.26's call-tool handler, `_get_cached_tool_definition(tool_name)` is called at `mcp/server/lowlevel/server.py:525`. It runs `logger.warning("Tool '%s' not listed, no validation will be performed", tool_name)` (line 488), before the FastMCP handler is called at line 535.
- FastMCP registers its handler through this SDK decorator (`fastmcp/server/mixins/mcp_operations.py:123`). Its `LowLevelServer` does not override the method.
- The `mcp.server.lowlevel.server` logger passes its records up to the root logger. Nothing silences it; FastMCP only stops propagation for its own `fastmcp` logger. Under `--http`, main()'s `basicConfig()` prints it to stderr, and Python's fallback stderr handler would print a WARNING even without that.

The consequences:
- KB's own log line is redacted, but the journal still gets the token, written with `%s`.
- A JSON tool name containing `\n` forges whole lines, for example `INFO:knowledge_base.auth:machine desktop called 'status'`. That breaks decision d's claim that `%r` prevents forgery, and with it the per-call attribution record.
- `test_a_token_named_as_the_tool_is_not_logged` would only catch this if it checked every captured record. Its "one record" wording suggests it filters to `knowledge_base.auth`, in which case it passes with the leak in place.

The ideal fix:
- Install one handler in main() (`basicConfig(handlers=[h])`) whose formatter or filter redacts the token shape and escapes CR and LF in every message that reaches stderr, whichever library logged it.
- Delete the call-site `_TOKEN_RE.sub` in `CallingMachine`.
- Move the token-as-tool-name check into the real-child test: call a tool named with laptop's token, and one named with a newline. Assert that neither output file contains the token and that the forged line does not appear on its own. Keep the existing "neither file contains the token" assertion.
- This has to be tested in the real child. pytest's caplog captures records before KB's handler runs, so an in-process test cannot see the redaction.

A cheaper alternative is `logging.getLogger("mcp").setLevel(logging.ERROR)` in the HTTP branch. It drops all SDK warnings and does not cover future SDK messages at ERROR. Either way, setting `FASTMCP_LOG_LEVEL=DEBUG` would log tool arguments through FastMCP's own handler; that is the operator's opt-in, and worth listing as a residual.

**[MEDIUM] M6 is only partly fixed: `git grep … -- docs/` breaks again once the plan is committed to the branch.**
- The plan's own note says it relies on the plan being untracked ("tracked files only: this plan quotes the phrase").
- Your workflow says otherwise. finish-pr item 5: "A plan lives on its work branch while the work runs", and its working files "are deleted in one commit". address-issue's `state-and-resume.md` likewise "deletes the PR's `docs/plans/` files".
- If the plan, its drafts and the review files are committed before item 3 or item 9, both checks print matches.
- **Fix:** exclude the directory in both checks: `git grep -n '…' -- docs/ ':!docs/plans'`.

**[LOW] Item 7's serving tests now hit the new `sys.exit(1)`.**
- `test_main_serves_http_through_the_gate`, and item 8's in-process `--http` tests, patch `uvicorn.Server.run` with a mock that never sets `started`. As written, `main()` raises `SystemExit(1)` in them.
- The patch has to set `started = True`, for example with `autospec=True` and `side_effect=lambda self: setattr(self, "started", True)`.
- Only `test_a_server_that_never_starts_exits_1` should leave it unset.

**[LOW] The `_TOKEN_RE` "shared with `authenticate`" claim does not work, and the pattern differs from ADR-0020's redaction rule.**
- `authenticate` takes bytes, and a regex compiled from a str pattern raises `TypeError` on bytes. Share the pattern string and compile it twice, or drop the claim; the HIGH fix moves redaction elsewhere anyway.
- ADR-0020 §4 says redaction matches the 35 characters "with no `[0-9A-Za-z_]` on either side". The plan's unbounded pattern redacts more than that. Over-redacting is the right choice for a log, but state the deviation and its reason in one line, since the contract wins over a restatement.

## Resolution (round 2)

- [HIGH] "the SDK still writes the raw tool name; a newline forges lines" → new decision d2: `auth.RedactingFormatter`
  on the root's one stderr handler (`auth.log_to_stderr()`), FastMCP's logger routed through it; it redacts every
  token shape in the whole formatted text (traceback included) and indents every continuation line by two spaces, so
  no injected line starts at column 0. The call-site redaction in `CallingMachine` is deleted. The real-child test now
  sends a token-named tool, a token argument and a newline-forging tool name and checks the whole output; a unit test
  covers the formatter. `FASTMCP_LOG_LEVEL=DEBUG` and non-`logging` writes are listed as residuals.
- [MEDIUM] "M6 breaks once the plan is committed" → items 3 and 9 use `git grep … -- docs/ ':!docs/plans'`.
- [LOW] "item 7's serving tests hit `sys.exit(1)`" → item 7 patches `uvicorn.Server.run` with `autospec=True` and a
  side effect setting `started`, except in the never-starts test.
- [LOW] "the str/bytes regex cannot be shared; the pattern differs from ADR-0020's boundary rule" → one
  `TOKEN_SHAPE` string compiled twice (str for the formatter, bytes for `authenticate`); decision d2 states the
  deliberate over-redaction against ADR-0020 §4's boundary rule.
- Override acknowledged by the reviewer: decision i's bracketed IPv6 (ME `--listen` parity).

# Round 3 (delta review, same subagent)

Michaël, here is the round-3 review of the #571 HTTP Bearer plan, delta only.

**Verdict: not LGTM yet, but close. Two MEDIUM and two LOW findings remain; there is no BLOCKER or HIGH.** Decision d2 closes the round-2 HIGH on the paths it names; I checked each against the installed sources. Two gaps are left: the real-child test, as written, fails on correct code, and the line-safety claim misses two ways the journal starts a new record. I have high confidence in both MEDIUMs; the container log-driver detail in M2 is unverified.

Altitude report SHA-256: b433a3f65ff5098a1dec43052c1ef905ae6314b7cad0a5d81cfd3a80e25c1c50

## Altitude and duplication (assessed first)

- **Digests.** The plan is `8a2c381e…cfdc`, the same in the review tree and the worktree, and it is the digest the report is bound to. Spike 3's input digest (`7bfcc70e…`) matches its table row.
- **Disposition "proceed": I agree.** The plan grew by 26 lines, which are decision d2's rules and item 8's test rows. The formatter is described by its rules, not written out.
- **Duplication.** d2 is restated in the Outcome, item 8 and the README. That is modest and acceptable.

## Round-2 resolutions checked

**HIGH (the SDK's raw unknown-tool name): fixed for the paths d2 names.**
- **SDK warning.** The `Tool '%s' not listed` warning (`mcp/server/lowlevel/server.py:488`) comes from the SDK's logger, which nothing stops from reaching the root handler.
- **FastMCP's traceback.** FastMCP's own setup gives its `fastmcp` logger two Rich handlers and turns propagation off (`fastmcp/utilities/logging.py:57,106-107`). That setup runs once at import (`fastmcp/__init__.py:16`); the only other caller is `temporary_log_level`, used by `run`/`run_http_async`, which KB's HTTP path never calls. So removing those handlers and turning propagation back on holds.
- **Argument values.** They reach the log through `logger.exception(f"Error validating tool …")` in `server.py`. I ran a one-off pydantic snippet that wrote no files: pydantic's message carries `input_value='me_…'`, which the redaction catches.
- **Levels.** FastMCP's logger stays at INFO, and no per-request INFO line exists on these paths.
- **Line breaks.** Indenting after every LF plus escaping CR does stop a newline from forging a line.
- **Writes outside `logging`.** KB's own `print`s are the startup error line, which holds no client data, and the Playwright renderer, which runs as a subprocess with `capture_output=True`. So the residual "KB's server writes none" holds.
- **Real child.** It can observe what d2 changes. caplog sees records before any handler, so the real child is indeed the only place to test it. Records reach the file: non-interactive stderr is line-buffered and the handler flushes each record. Records written before the re-raised SIGTERM survive.

**Other round-2 findings:**
- **MEDIUM (M6), fixed.** `git grep … -- docs/ ':!docs/plans'` is correct.
- **LOW (patched `run`), fixed.** The `autospec=True` side effect that sets `started` works.
- **LOW (str/bytes regex), fixed.** One `TOKEN_SHAPE` string compiled twice works, and the over-redaction against ADR-0020 §4's boundary rule is now stated.

## Findings

**[MEDIUM] M1. The real-child test's line count is wrong: correct code fails it.**
- The test asserts "exactly one stderr line starts `INFO knowledge_base.auth: machine laptop called 'status'`", but laptop makes two `status` calls: the plain one and the one with an extra argument holding the token.
- FastMCP's `strict_input_validation` defaults to False (`fastmcp/settings.py:257`), so the SDK skips schema checks.
- The arguments are only checked inside `FunctionTool.run` (`function_tool.py:285-293`), which runs after the middleware has already logged the call. So both calls write that line.
- **Fix:** assert exactly two such lines, or send the token argument to a different tool, for example `co_occurrence`.

**[MEDIUM] M2. Client data can still start a new journal record at column 0 in two ways, so "cannot forge a line" (Outcome and d2) is not yet true.**

`journald.conf(5)` here (systemd 259) says a unit's stderr stream is split into records at LF and at NUL, and that it inserts a hard record boundary after `LineMax` bytes (48K by default).
- **NUL.** A tool name containing `\u0000` (valid JSON, accepted as a str) followed by `INFO knowledge_base.auth: machine desktop called 'status'` is written raw by the SDK's `%s` warning. journald then starts a record at column 0. KB's own line is safe here, because `%r` escapes NUL.
- **Overlong lines.** A tool name of about 48 KiB, with the forged text placed at the boundary, is split by journald with the forged part at column 0. Both the SDK's `%s` line and KB's own `%r` line carry the whole name, and token holders face no body limit.
- **Container path.** I believe podman's conmon (KB runs as a quadlet) splits long container output into partial entries at its read-buffer size. I have not verified this.

The fix:
- In `RedactingFormatter`, escape NUL and every other C0 control except LF (already handled by the indent) and tab, plus DEL, as `\xNN`, the same way CR is escaped.
- Either cap a formatted record below the smallest sink limit (about 8,000 bytes, with a truncation marker; this covers journald and conmon, at the cost of the tail of very long tracebacks), or break physical lines over about 2,000 characters with the same two-space continuation (this covers journald's `LineMax` only). Do either one after redaction, so a token is never split before it is matched.
- Add a NUL case and an overlong name to the formatter unit test and to the child test.
- If you keep neither length guard, word the claim as what holds: "no control character in client data starts a record line". Then list "a sink that splits overlong output can" as a residual.

**[LOW] L1. The in-process restore fixture in items 7 and 8 is fragile.**
- pytest adds and removes its own root handlers in each phase. A fixture that snapshots `root.handlers` during setup and puts that list back at teardown re-attaches pytest's setup-phase capture handlers for the rest of the session. The handler that `log_to_stderr()` installed may also stay bound to a capture stream pytest has since closed.
- **Fix:** in the in-process `main()` tests, patch `log_to_stderr` to a no-op (`monkeypatch.setattr(server, "log_to_stderr", lambda: None)`). The formatter unit test and the real child already cover it, so the restore fixture can be deleted.

**[LOW] L2. Shape-based redaction misses a token that a library shortens; add it to the residuals.**
- I ran a one-off pydantic snippet (no files written). For a value longer than its limit, pydantic prints `input_value='me_SSSSSSSSSSSSSSSSSSSSS...XXXX'`. That is `me_` plus 21 characters of the token, which the 32-character shape cannot match.
- So a token holder that sends its token followed by more text in an argument with the wrong type or name puts two thirds of its token in the journal.
- That is the client's own data, so no code change is needed. Add one residual line: "only the exact 35-character shape is redacted; a value a library shortens (pydantic's `input_value`) or encodes can still show part of a token."

## Resolution (round 3)

- [MEDIUM] M1 "the child test's line count is wrong" (Codex and agy found it too) → item 8 asserts exactly two
  `machine laptop called 'status'` lines, explains why, checks the validation call's `result.isError`, and checks each
  leak call on the stderr bytes it added, so the unknown-tool path cannot satisfy the traceback path's assertion.
- [MEDIUM] M2 "NUL and overlong lines still start journal records" → decision d2: every C0 control but LF and tab,
  and DEL, written `\xNN`; physical lines broken at 2,000 characters after redaction and escaping; both with
  continuation indent. The formatter unit test and the child test gain a NUL case and an overlong name; "a sink that
  splits below 2,000 characters" is a residual.
- [LOW] L1 "the restore fixture is fragile" → item 8: in-process `main()` tests patch `log_to_stderr` to a no-op; the
  restore fixture is gone.
- [LOW] L2 "library-shortened tokens escape the shape" → added to the residuals.

# Round 4 (delta review, same subagent)

Michaël, here is the round-4 review of the #571 HTTP Bearer plan, delta only.

**Verdict: LGTM. There is no BLOCKER, HIGH or MEDIUM; the four LOW findings below can be fixed while implementing.** All four round-3 findings are resolved and I found no regression. I have high confidence: I checked the formatter's rules and the real-process test (item 8) against the paths verified in earlier rounds (`mcp/server/lowlevel/server.py:488,525,535`; `fastmcp/tools/function_tool.py:285-293`; journald.conf(5) on this machine). The only exception is podman's log behaviour in L2, which I have not checked.

Altitude report SHA-256: 57cd845a03cc79abac4cd665a1305f700903d0de96b5c5f9b249d2d57d0d56c8

## Altitude and duplication (assessed first)

- **Digests.** The plan's SHA-256 is `3682a0f3…42a2`, the same in the review tree and the worktree, and it is the digest the altitude report is bound to.
- **Disposition "proceed": I agree.** The plan grew by 13 lines: two formatter rules, test rows and residuals. The formatter is still specified by its rules, not written out in code.
- **Duplication.** None new.

## Round-3 resolutions checked

**M1 (the line count): fixed.**
- Two lines starting with `machine laptop called 'status'` is right. I checked the other calls' log lines:
  - the token-named call logs `'me_[redacted]'`;
  - the NUL name logs `\x00`, because Python's repr escapes it;
  - the newline name is repr-quoted with the newline escaped;
  - the long name starts with `'aaaa…`.

  None of those match, so the count is exactly two.
- Checking `result.isError` on the call with the bad argument is right too: the SDK turns the validation error into an error result.
- Reading only the stderr bytes each call added makes the check of the validation traceback depend on that traceback alone. That call's own KB line, `called 'status'`, contains no `me_[redacted]`, so only the traceback can supply it.

**M2 (NUL and overlong lines): fixed for journald's stream.**
- The order of steps holds: redact, then escape, then break lines, then indent.
  - Redacting first catches a token that straddles character 2,000.
  - Escaping a control character produces `\xNN`, whose backslash can never complete a token shape.
- A 2,000-character piece is at most 8,000 UTF-8 bytes, and 8,002 with the indent. That is far below journald's `LineMax` default of 48K.
- With NUL escaped, no byte from a client can end a journald record.

**L1 (the restore fixture): fixed** by patching `log_to_stderr` to a no-op. See L3 for the patch target.

**L2 (library-shortened tokens): fixed** with a new residual. Its figure, `me_` plus 21 characters, matches what I measured from pydantic.

## Findings

**[LOW] L1. The long-name case in the real-process test cannot go red for the rule it is meant to check.**
- A file is not a sink that splits lines. So without the 2,000-character break, the 2,000 `a`s plus the forged text are still one long line in the stderr file, and "no line starts `INFO knowledge_base.auth: machine desktop`" still passes. The formatter's unit test is what covers the break.
- **Fix:** either drop the case, or add the assertion the break makes observable: "no stderr line is longer than 2,002 characters". That fails without the rule, because KB's own `%r` line and the SDK's `%s` line both carry the whole name.
- A related note that needs no change: in the token-named call's window, KB's own line (`called 'me_[redacted]'`) already supplies `me_[redacted]`. So that window does not prove the SDK's warning went through the formatter. The SDK's path is still covered, by the newline-name case (the SDK line is the one that would otherwise forge a record) and by the whole-file "no token" assertion.

**[LOW] L2. The new residual about splitting sinks does not name the sink KB actually runs behind.**
- It covers "a sink that splits lines below 2,000 characters". KB runs as a rootless podman quadlet, where container output goes through conmon.
- I believe conmon's journald log driver reads container output in fixed chunks of about 8 KiB and writes a chunk boundary that falls mid-line as an entry marked `CONTAINER_PARTIAL_MESSAGE=true`. If so, it can cut a record at a byte offset the client controls, whatever the line lengths. I have not verified this.
- **Fix:** word the residual as "a sink that splits output at its own boundaries (a line limit under 2,000 characters, or fixed-size reads such as podman's conmon, I believe)". Note that readers can recognise such pieces by `CONTAINER_PARTIAL_MESSAGE`.

**[LOW] L3. The no-op patch may target a name `server.py` does not have.**
- Decision d writes the call as `auth.log_to_stderr()`, while item 8 patches `server.log_to_stderr`.
- If `server.py` calls the function through the `auth` module, `monkeypatch.setattr(server, "log_to_stderr", …)` raises, because that attribute does not exist on `server`.
- **Fix:** pick one: import it by name in `server.py` (`from .auth import log_to_stderr`), or patch `knowledge_base.auth.log_to_stderr`.

**[LOW] L4. One rationale figure is slightly off.**
- d2 says escaping first "keeps a piece under 8,000 bytes". The exact bound is at most 8,000 bytes per piece, and 8,002 with the two-space indent. It is still below conmon's buffer, if that buffer is 8,192 bytes as I believe.

## Resolution (round 4)

LGTM with four LOW findings, all applied to the plan:

- [LOW] L1 "the long-name case cannot go red in a file" → item 8 also asserts no stderr line is longer than 2,002
  characters.
- [LOW] L2 "the splitting-sink residual should name conmon" → reworded (fixed-size reads, likely conmon,
  `CONTAINER_PARTIAL_MESSAGE`).
- [LOW] L3 "the no-op patch target" → `server.py` imports `log_to_stderr` by name; the patch targets
  `server.log_to_stderr`.
- [LOW] L4 "the byte bound" → "at most 8,000 bytes, 8,002 with the indent".

# Round 5 (confirmation, same subagent)

Michaël, here is the round-5 review of the #571 HTTP Bearer plan. It is a confirmation round, delta only.

**Verdict: LGTM. There is no BLOCKER, HIGH, MEDIUM or LOW finding.** I have high confidence: I checked the four applied LOWs against the plan text, and I tested agy's hardening against the installed pydantic.

Altitude report SHA-256: 4ba0933cd4e386eea3704e3b40a8ef7b2bb602256cde0449d6ad534a67107a65

## Altitude and duplication (assessed first)

- **Digests.** The plan's SHA-256 is `4555291c…4893`, the same in the review tree and the worktree, and it is the digest the altitude report is bound to.
- **Disposition "proceed": I agree.** The plan grew by 6 lines, which are wording and two test assertions. There is no new literal and no new duplication.

## Round-4 LOWs checked

- **L1 (fixed).** The new assertion "no stderr line is longer than 2,002 characters" can now fail when it should.
  - Without the 2,000-character break, KB's own log line for the 2,000-`a` name would exceed that length, and so would the SDK's warning for it.
  - Every other line on KB's stderr also goes through the formatter, so the bound can't be broken by an unrelated line.
- **L2 (fixed).** The residual now names fixed-size reads, says podman's conmon "likely" does them, and points to `CONTAINER_PARTIAL_MESSAGE`. That wording matches what I could not verify.
- **L3 (fixed).** With `from .auth import log_to_stderr` in `server.py`, the test patch `monkeypatch.setattr(server, "log_to_stderr", …)` targets a name that exists.
- **L4 (fixed).** The text now says at most 8,000 bytes per piece, 8,002 with the indent.

## agy's hardening checked

The child test's unexpected argument is now `{token: token}`. I measured it with the installed pydantic, in a one-off snippet that wrote no files:
- The argument's name is printed in full as the error's location line, whatever its value.
- A 35-character value prints in full, while a 55-character value is shortened (`me_` plus 21 characters, then `...`), as the plan says.

So that call's stderr always carries a full token shape that the formatter must redact. KB's own log line for the call (`called 'status'`) holds no `me_[redacted]`, so the check on that call's stderr can only pass through FastMCP's validation traceback, as intended. Redaction runs on the whole formatted text before the indent is added, so the location line is written `  me_[redacted]`.

## Resolution (round 5)

LGTM, no finding: no change required.

# Round 6 (binding confirmation, same subagent)

Michaël, here is the round-6 review of the #571 HTTP Bearer plan, a binding confirmation of the one-phrase change.

**Verdict: LGTM. There are no findings.** I have high confidence: I checked the change against the full plan, not only the diff.

Altitude report SHA-256: 0a1fde183dc0452cd6981313ad25037efab001d9379224ca044641179635fc5c

## Altitude (assessed first)

- **Digests.** The plan's SHA-256 is `61434ebe…428e`, the same in the review tree and the worktree, and it is the digest the altitude report is bound to.
- **Disposition "proceed": I agree.** The change is 24 bytes on one line and is recorded as "round 6 one phrase". There is no new content and no new duplication.

## The change

- **Only one line changed.** Comparing the full plan against the round-5 version shows a single difference, at line 380.
- **The meaning is kept.** That line in item 8's real-process test now says "a NUL character (U+0000) in place of the newline" instead of the literal `\u0000` escape. It still asks for the same tool name: `x`, a NUL, then the forged `INFO knowledge_base.auth: …` text.
- **The checks that depend on it are unchanged.** "Neither file contains … a NUL byte" still proves that decision d2 escapes NUL to `\x00`, and the formatter's unit test still covers NUL separately.
- **The file is clean for publication.** The plan contains no NUL byte, no other control character, and no `\uNNNN` escape left for GitHub to alter on read-back.

## Resolution (round 6)

LGTM, no finding: no change required.
