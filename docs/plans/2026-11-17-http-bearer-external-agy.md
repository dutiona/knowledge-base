# External plan review: agy (headless agy, gemini-4-argon-high, resume)

Verbatim final messages of each round; the only edit is the local home directory in file links, written `~/`.

## Round 1

**Conclusion:** The plan in [plan.md](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/plan.md) is ready for implementation as written. **Confidence:** High.

Altitude report SHA-256: 8aaa4742ce04ffb9ce0a44fb0449a95ec1c1b37cd604d84b6c81454674ce66ed

### Altitude and Duplication Assessment

- **Calibration (`proceed`):** At 417 lines (only 7 lines of fenced shell commands in `## Verification`) against an estimated 820–1,120 lines of implementation and tests across 9 files, [plan.md](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/plan.md) stays at design and specification altitude. It quotes the binding clauses of [adr-0020.md](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/inputs/adr-0020.md) once under Constraints and aligns parser error reasons and test tables with [me1018-auth.rs](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/inputs/me1018-auth.rs) and [me1018-http.rs](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/inputs/me1018-http.rs) without embedding full source files or duplicating contract ownership. No altitude override is needed.

### Structural Completeness

- `## Documentation` ([plan.md](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/plan.md#L335-L366)), `## Testing` ([plan.md](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/plan.md#L368-L390)), and `## Verification` ([plan.md](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/plan.md#L391-L417)) are all present and complete.

### Technical & Security Rationale

- **Decisions a–b & Items 4–5 (Auth boundary, fail-closed parsing, no token leakage):** Wrapping `mcp.http_app(path="/mcp", stateless_http=True, json_response=True)` in the pure-ASGI `RequireMachine` callable rather than FastMCP's `BearerAuthBackend` enforces ADR-0020 §3–§4 across every path and method (exact `Bearer ` prefix, single `Authorization` header, `me_[0-9A-Za-z]{32}` shape check prior to SHA-256, non-short-circuiting `hmac.compare_digest` across all entries, 401 with `WWW-Authenticate: Bearer` and empty body, and `Authorization` stripped from the inner scope). Setting `ws="none"` in `uvicorn.Config` prevents WebSocket upgrade requests from bypassing the HTTP gate into uvicorn's 403 handler. `TokenTable.parse` splits on `b"\n"` only, rejects unterminated final lines and malformed entries with line numbers, and never quotes raw line bytes in error messages.
- **Decisions c, h, i & Item 7 (Startup order and exit codes):** Parsing CLI flags, loading `--token-file` into `TokenTable`, and running `open_declared_db().close()` before binding the socket guarantees that neither a bad CLI invocation nor a bad token file touches [db.py](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/src/knowledge_base/db.py) or restart-loops a unit with `RestartPreventExitStatus=78`. Passing `[]` to `server.main([])` in existing in-process tests in [test_server.py](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/tests/test_server.py) cleanly isolates `argparse` from pytest's `sys.argv`.
- **Decision d & Item 8 (Per-call machine logging and defense in depth):** `CallingMachine.on_call_tool` logs `machine %s called %r` at `INFO` on `knowledge_base.auth`, passes through when `get_http_request()` raises `RuntimeError` (preserving stdio and in-memory `fastmcp.Client` runs), and raises `ToolError` if an HTTP request ever reaches the hub without `state.machine`.
- **Decisions e–g & Item 6 (Harness, graceful drain, and mutation-checked tests):** Leaving `timeout_graceful_shutdown` at uvicorn's default allows in-flight synchronous tool calls in worker threads to deliver their response on stop, while `sock.dup()` on a held `127.0.0.1:0` socket in [conftest.py](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/tests/conftest.py) eliminates port-reuse races across server restarts for Michaël's test suite.

[Antigravity Review] LGTM

## Round 2

**Conclusion:** The round-2 revisions in [plan.md](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/plan.md) cleanly resolve all round-1 findings without introducing gaps, bad assumptions, or security regressions. **Confidence:** High.

Altitude report SHA-256: a675e4fbd71410b4cd914419f70d84ace5d6a8da12d3dfe231e854cb30edf487

### Altitude and Duplication Assessment

- **Calibration (`proceed`):** The +37-line delta in [plan-delta-r2.diff](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/plan-delta-r2.diff) (417 to 454 lines in [plan.md](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/plan.md)) consists strictly of design decisions, edge-case specifications, and test cases addressing the round-1 review items, with no added code blocks or duplicated contract material.

### Delta Review Rationale

- **Token redaction in tool-call logs (Decision d, Item 8):** Redacting any `me_[0-9A-Za-z]{32}` substring in `context.message.name` to `me_[redacted]` before logging in `CallingMachine.on_call_tool` (and testing it in `test_a_token_named_as_the_tool_is_not_logged`) closes the log-injection/token-leak vector when a client passes a token as the tool name prior to FastMCP's tool lookup.
- **Uvicorn logging and response hardening (Decisions d, f; Items 5, 6):** Passing `log_config=None, log_level="error", server_header=False` to `uvicorn.Config` prevents uvicorn's `dictConfig` from mutating root/test logging handlers and silences per-request warning noise from malformed or upgrade probes. Adding `connection: close` to the 401 response in `RequireMachine` ensures uvicorn/h11 closes unauthenticated connections immediately rather than keeping a connection open on an unread request body.
- **Startup failure and IP-literal `--http` validation (Decisions f, i; Items 6, 7):** Requiring `HOST` in `--http HOST:PORT` to be an IPv4 or bracketed IPv6 literal (`ipaddress`) catches host typos like `127.0.0.l` at CLI parse time with exit 78 (`EX_CONFIG`) instead of failing DNS resolution inside uvicorn and restart-looping Michaël's systemd unit. Checking `if not server.started: sys.exit(1)` after `server.run()` (and checking thread termination before `started` in `KbHttp.start()`) ensures lifespan or bind failures surface as non-zero exits.
- **Test, documentation, and verification hygiene (Items 3, 6, 8, 9, Verification):** Sharing `declared_db` across HTTP and in-memory tests, comparing tool name sets in `test_tools_list_is_served`, not asserting a zero return code after `SIGTERM` (since uvicorn re-raises the signal), using `git grep` so untracked files under `docs/plans/` do not trip doc checks, documenting container `0.0.0.0` bind with loopback `PublishPort=127.0.0.1:<port>:<port>`, and isolating the verification smoke test with `KNOWLEDGE_BASE_DB` and `KNOWLEDGE_BASE_DATA_DIR` in scratch are all sound improvements.

[Antigravity Review] LGTM

## Round 3

**Conclusion:** Decision d2 (`auth.RedactingFormatter` + `auth.log_to_stderr()`) in [plan.md](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/plan.md) is sound and closes the SDK and FastMCP logging leak and log-forgery paths; there is one minor test-assertion mismatch in Item 8's subprocess test where `status` is called twice while the assertion expects one `called 'status'` line. **Confidence:** High.

Altitude report SHA-256: b433a3f65ff5098a1dec43052c1ef905ae6314b7cad0a5d81cfd3a80e25c1c50

### Altitude and Duplication Assessment

- **Calibration (`proceed`):** The +26-line delta in [plan-delta-r3.diff](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/plan-delta-r3.diff) (454 to 480 lines in [plan.md](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/plan.md)) specifies `RedactingFormatter` and `log_to_stderr()` by their invariants and test cases without embedding implementation code or duplicating contracts.

### Verification of Round-3 Changes

- **Library logging coverage (Decision d2, Item 8):** The MCP SDK (`mcp.server.lowlevel.server`) uses standard `logging.getLogger(__name__)` with `propagate=True`, so its `WARNING` on an unknown tool name propagates to the root logger's `StreamHandler(sys.stderr)`. FastMCP (`fastmcp`) attaches its own handler and sets `propagate=False` at import time; clearing `logging.getLogger("fastmcp").handlers` and setting `propagate=True` in `log_to_stderr()` routes FastMCP's `ERROR` validation tracebacks through the same root handler and `RedactingFormatter`.
- **Continuation-line indentation vs. log forgery (Decision d2, Item 8):** Rewriting `\r` to `\r` and `\n` to `\n  ` across the entire `super().format(record)` output ensures every continuation line (whether from a traceback or an unescaped newline in an SDK warning) starts with two spaces at column 0, while real log records start with `<LEVEL> <logger>:`.
- **Regex type split, `git grep` pathspec, and `uvicorn.Server.run` mock (Decisions d2, Item 3, Item 4, Item 7, Item 9):** Compiling `TOKEN_SHAPE` separately as `str` and `bytes`, excluding `':!docs/plans'` in `git grep`, and setting `started = True` via `side_effect` on the `autospec=True` mock of `uvicorn.Server.run` cleanly resolve all other round-2 findings for Michaël.

[Antigravity Review] [LOW] Item 8 (`test_the_http_process_logs_each_call_and_never_the_token`, [plan.md](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/plan.md#L360-L366)): the test sends `tools/call status` (200) and then a second `tools/call status` with an unexpected argument holding the token, then asserts that "exactly one stderr line starts `INFO knowledge_base.auth: machine laptop called 'status'`". Because `CallingMachine.on_call_tool` logs before `call_next` and FastMCP validates tool arguments inside the tool handler after middleware (whereas the MCP SDK's `inputSchema` for `status` has no `additionalProperties: false` and passes the extra argument through to FastMCP, as shown in [spike-log-leak.py](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/inputs/spike-log-leak.py)), both `status` calls log `INFO knowledge_base.auth: machine laptop called 'status'`, producing two such lines instead of one. Either send the unexpected-argument call to a different tool (e.g. `co_occurrence`) or assert two `called 'status'` lines.

## Round 4

**Conclusion:** The round-4 revisions in [plan.md](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/plan.md) resolve the round-3 findings and harden `RedactingFormatter` against control characters and overlong lines, but combining the new per-call `me_[redacted]` stderr check in Item 8 with the Pydantic `input_value` truncation noted in the new residual creates a failing test case unless the token is passed in an unshortened traceback field (such as the unexpected argument's key). **Confidence:** High.

Altitude report SHA-256: 57cd845a03cc79abac4cd665a1305f700903d0de96b5c5f9b249d2d57d0d56c8

### Altitude and Duplication Assessment

- **Calibration (`proceed`):** The +13-line delta in [plan-delta-r4.diff](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/plan-delta-r4.diff) (480 to 493 lines in [plan.md](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/plan.md)) stays at design and test-specification altitude with no added code blocks or duplicated contracts.

### Verification of Round-4 Changes

- **Formatter pipeline order (Decision d2, Item 8):** Redacting `me_[0-9A-Za-z]{32}` first, escaping C0 controls (except `\n` and `\t`) and DEL (`\x7f`) to `\xNN` second, splitting physical lines over 2,000 characters third, and indenting every `\n` with two spaces fourth guarantees that tokens straddling a 2,000-character boundary are still redacted and no continuation line starts at column 0 or exceeds 2,002 characters.
- **In-process logging isolation and `status` call count (Items 7, 8):** Patching `server.log_to_stderr` to a no-op in Item 7's in-process `main()` tests and asserting two `INFO knowledge_base.auth: machine laptop called 'status'` lines in Item 8 cleanly resolve the round-3 issues for Michaël.

[Antigravity Review] [MEDIUM] Item 8 (`test_the_http_process_logs_each_call_and_never_the_token`, [plan.md](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/plan.md#L370-L374)): the test now polls the stderr bytes added by each leak call for `me_[redacted]`, including "`tools/call status` with an unexpected argument holding the token (`result.isError` true; FastMCP's validation traceback: the added bytes hold `me_[redacted]`)" (matching `{"x": tok}` in [spike-log-leak.py](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/inputs/spike-log-leak.py#L17)). However, as documented in the new residual at [plan.md](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/plan.md#L149-L150), `pydantic_core` truncates string `input_value` in `ValidationError` to `me_` plus 21 characters (`'me_000000000000000000000...'`), which is shorter than 35 characters and does not match `TOKEN_SHAPE` (`me_[0-9A-Za-z]{32}`)—so `CallingMachine` logs `'status'` and FastMCP's traceback logs only the truncated `input_value`, producing no `me_[redacted]` in the bytes added by that call and causing the 5 s poll to time out. Pass the token as the unexpected argument's *name* (`{token: 1}`) (or in a field Pydantic prints in full in the traceback, such as the error `loc`) so the full 35-character token appears in FastMCP's traceback and is redacted to `me_[redacted]`.

## Round 5

**Conclusion:** The round-5 revisions in [plan.md](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/plan.md) resolve all remaining items cleanly and are ready for implementation. **Confidence:** High.

Altitude report SHA-256: 4ba0933cd4e386eea3704e3b40a8ef7b2bb602256cde0449d6ad534a67107a65

### Altitude and Duplication Assessment

- **Calibration (`proceed`):** The +6-line delta in [plan-delta-r5.diff](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/plan-delta-r5.diff) (493 to 499 lines in [plan.md](~/.local/state/super-plan/tmp/2026-11-17-http-bearer.bf3dfa94/.review/plan.md)) refines wording and two test assertions without adding code blocks or duplicating contracts.

### Delta Review Rationale

- **Item 8 subprocess test (`{token: token}` and line-length bound):** Passing `{token: token}` as the unexpected argument to `status` guarantees the full 35-character token appears in Pydantic's error location (and in `input_value` at 35 characters) so `RedactingFormatter` rewrites it to `me_[redacted]` and neither copy leaks, while asserting that no stderr line exceeds 2,002 characters verifies the long-line breaker in the child process.
- **`server.log_to_stderr` import and byte-bound wording (Decision d2, Item 8):** Importing `from .auth import log_to_stderr` in `server.py` aligns with `monkeypatch.setattr(server, "log_to_stderr", lambda: None)`, and the 8,002-byte bound and conmon partial-message residual are accurately stated for Michaël.

[Antigravity Review] LGTM

REVIEW COMPLETE
