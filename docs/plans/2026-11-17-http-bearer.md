# feat(server): serve MCP over HTTP behind per-machine Bearer auth (#571)

## Outcome

`knowledge-base --http HOST:PORT --token-file PATH` serves the hub through FastMCP's `http` (streamable-HTTP)
transport on `POST /mcp`, stateless, with JSON responses, under a uvicorn server KB configures itself. A pure-ASGI
application, `auth.RequireMachine`, is the callable uvicorn serves and wraps everything FastMCP builds: a request on any
path and method without exactly one `Authorization: Bearer <token>` whose SHA-256 is in the ADR-0020 §4 token-hash file
gets 401, `WWW-Authenticate: Bearer` and an empty body, and no FastMCP code runs for it. An admitted request reaches
FastMCP without its `Authorization` header and with its machine in the request state. No route is unauthenticated,
every `Host` is served, KB never sends 403. The hub's `auth.CallingMachine` middleware logs one line per `tools/call`
naming the machine and the tool, and refuses a tool call that arrives over HTTP without a machine; in HTTP mode every
log line the process writes, libraries' included, has token shapes redacted and cannot forge a line. Startup parses the
command line, reads the token file and runs the #569 gate, in that order and before the port is bound; any failure
prints the reason and exits 78 with no database written by a bad token file. A restart needs no client session
recovery, and a stop lets calls already started finish. Plain `knowledge-base` serves stdio, unchanged.
`tests/conftest.py` holds the `kb_http` harness: the production app and uvicorn configuration on an ephemeral loopback
port, with a stub query embedder.

## Constraints

From #571, verbatim:

- KB answers MCP over HTTP, and every request without a valid machine token is refused before a tool runs.
- `knowledge-base --http <host:port>` serves FastMCP's `http` transport. The endpoint gate (dutiona/knowledge-base#569) runs before the port is bound. The stdio entry point stays: it is the local and maintenance surface, run against the database only with the unit stopped.
- The HTTP transport is stateless (FastMCP's stateless-HTTP option), like dutiona/memory-engine#1018, so a KB restart (deploy, maintenance, token change) needs no client session recovery. Tested: a call after a server restart succeeds without re-initialization.
- A token verifier reads the same hashed token file as ME, in ADR-0020's format. With no token or an invalid one, the MCP route returns 401 and no tool runs (asserted); with a valid token, `tools/list` works. The calling machine is logged per call.
- Any unauthenticated route FastMCP adds (for example OAuth discovery metadata) exposes no tool and no data (asserted). A request with the tailnet `Host` is served.
- The pytest HTTP harness (uvicorn on an ephemeral port, stub embedder) lives in `tests/conftest.py`.
- Contracts win over restatements: a merged ADR-0020/ADR-0015 overrides any restatement here. PRs reference the umbrella as `Refs dutiona/infra#37`, never a bare `#37`.

From memory-engine ADR-0020 (merged), verbatim:

- **One token per machine**, and the machine's identity is taken from the token. The same token serves memory-engine and knowledge-base.
- **Shape.** `me_` followed by 32 random base62 characters (`[0-9A-Za-z]`, from a CSPRNG, about 190 bits).
- The request header is exactly `Authorization: Bearer` followed by one space and the token.
- **Check.** A presented value off the token shape gets a 401 without being hashed. Otherwise its hash is compared in constant time.
- One `<machine> <sha256-hex>` line per machine, each ended by a line feed, the last one included.
- `<machine>` is a DNS label: `[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?`.
- `<sha256-hex>` is 64 lowercase hex digits: the SHA-256 of the token's 35 ASCII bytes, with no line feed.
- Exactly one space between the two. No comment, no blank line, no carriage return, no other whitespace.
- **Fail closed.** A missing or unreadable file, a malformed line, a repeated machine or a repeated hash makes the service refuse to start and name the line number. An empty file is valid and admits no one.
- The file is read at startup only; a change needs a restart.
- **Test vector.** The token made of `me_` followed by 32 `0` characters hashes to: `f7ca4ebc8eb419b37dea09411716d9aa5506b9e5572f1a5e8b3170e815ee13aa`
- Every implementation must reject a line with uppercase hex, an uppercase machine name, two spaces, a carriage return before the line feed, a last line without a line feed, and a second line for the same machine.
- §3 statuses: 401 "No token, a malformed one, or one whose hash is not in the file"; 403 "Never sent by the server; an intermediary may send it".

From the fleet brief, verbatim (its deployment sentence names a private port and is left out):

- Mirror memory-engine#1018 (ME's HTTP + Bearer, in flight): the same `me_` + 32 base62 token shape, the same `<machine> <sha256-hex>` token-hash file, the same status codes. FastMCP 3.1.0 `stateless_http`.
- Since #569, a configuration error exits 78 (EX_CONFIG); keep that for a missing or unreadable token-hash file.
- The repository is PUBLIC: no tailnet host name, tailnet address, token or dutiona port mapping in any committed file. Use placeholders such as `kb.example-tailnet.ts.net`.

From AGENTS.md, verbatim:

- **TDD** — write the failing test first, then implement
- **Atomic commits** — one logical change per commit
- **Mock external services** — tests must not call Ollama or any LLM. Use `unittest.mock.patch` on `knowledge_base.embeddings.embed` (or the caller's `embed` name), or the `http_requests` fixture, which records every httpx request at the transport.

### Decisions

Verified by two spikes in this worktree (scratch, not committed). Spike 1, over httpx's in-process ASGI transport,
put a gate in `mcp.http_app(path="/mcp", stateless_http=True, json_response=True, middleware=[Middleware(Gate)])`:
401 with an empty body on `/mcp`, `/.well-known/oauth-protected-resource/mcp` and `/nope`; with the token, a
`tools/call` sent with no `initialize` answers 200, a non-loopback `Host` is served, GET and DELETE `/mcp` answer 405.
Spike 2, under uvicorn: a second server on the same port answers a `tools/call` after the first served `initialize`
(no `Mcp-Session-Id` is ever issued), and a hub middleware reads the machine through `get_http_request().state`. The
plan's outermost wrap (decision a) is stronger than spike 1's `middleware=` entry; item 6 tests it under uvicorn.

- **a. A pure-ASGI gate, not FastMCP's `auth=` `TokenVerifier`.** With `auth=`, the SDK's `BearerAuthBackend` accepts
  the scheme in any case (`auth_header.lower().startswith("bearer ")`), reads only the first `Authorization` header, and
  `RequireAuthMiddleware` covers the `/mcp` route alone (other paths answer 404). ADR-0020 and ME refuse all three, so
  one token would get different answers from the two servers. The gate is the outermost callable, not a `middleware=`
  entry, so it covers every route FastMCP has or adds in a later release, Starlette's 404 and 405 answers, and
  FastMCP's own middleware. It sends the 401 with two raw ASGI messages, so `auth.py` imports no Starlette. The
  acceptance's "token verifier" is `TokenTable` plus `RequireMachine`.
- **b. No Host allowlist; never 403.** FastMCP builds the SDK's session manager without `security_settings`, so
  DNS-rebinding protection is off and every Host is served. A Host check defends an unauthenticated local server
  against rebinding; a rebinding page cannot attach a Bearer token, so the gate already answers it 401. ME needed
  `--allowed-host` only because rmcp admits loopback alone. uvicorn runs with `ws="none"`: under its default, a
  WebSocket upgrade becomes a websocket scope that ends in uvicorn's 403; with `none` it is a plain GET that meets the
  gate. The tailnet-Host test fails if a later SDK turns the check on.
- **c. `--token-file PATH`, ME's flag name, beside `--http`; no environment variable.** Both are HTTP-only parameters
  of the unit's command line; one spelling means one doc entry and one test path, and a maintenance shell that sources
  the unit's environment still gets plain stdio. An environment fallback is additive later (defaulted question 1).
- **d. A call is a `tools/call`.** `CallingMachine.on_call_tool` logs `machine %s called %r` on the
  `knowledge_base.auth` logger at INFO, before the tool runs, never its arguments. A call with no HTTP request behind
  it (stdio, in-memory) is not logged and runs. An
  HTTP request without a machine raises `ToolError` before `call_next`, so serving the hub any other way (`fastmcp run
  … --transport http`, a refactor that drops the gate) runs no tool. The gate logs no refusal; uvicorn's access log is
  off and its own logger runs at ERROR (`access_log=False`, `log_config=None`, `log_level="error"`), so a probe cannot
  fill the journal (ME parity) and the per-request WARNING lines uvicorn writes for an upgrade attempt or a malformed
  request (one of which advises installing a WebSocket library, which would reopen decision b) stay quiet, while bind,
  startup and application errors still show. `log_config=None` also keeps uvicorn's `dictConfig` from replacing the
  process's handlers (the test process's included). HTTP mode makes the line visible: `auth.log_to_stderr()` installs
  the root's one handler (stderr, root stays WARNING) and sets `knowledge_base.auth` to INFO; without that, the root's
  WARNING drops the line while `caplog` still passes.
- **d2. One line-safe, redacting formatter for everything the process logs.** Client data reaches the log through
  libraries KB does not control: the mcp SDK logs an unknown tool's raw name at WARNING (`Tool '%s' not listed`)
  before any FastMCP middleware runs, and FastMCP's own handler logs argument values in validation tracebacks (both
  seen in a spike). So a client could put its own token in the journal, or forge a line with a newline in a tool
  name. `auth.RedactingFormatter` (format `%(levelname)s %(name)s: %(message)s`) rewrites the whole formatted text,
  traceback included, in this order: every `me_[0-9A-Za-z]{32}` becomes `me_[redacted]`; every C0 control but LF and
  tab, and DEL, becomes `\xNN` (journald also starts a record at NUL); every physical line longer than 2,000 characters
  is broken into 2,000-character pieces (journald forces a record boundary after `LineMax`, 48K by default, and
  escaping first keeps a piece at most 8,000 bytes, 8,002 with the indent); and every `\n` is followed by two spaces. So a record's
  continuation lines (a traceback, an injected newline, a broken long line) never start at column 0 where a real
  record line does. `log_to_stderr()` also routes FastMCP's logger through it (its handlers
  removed, `propagate` back on; FastMCP's level is unchanged). The shape is matched without ADR-0020 §4's boundary
  rule ("no `[0-9A-Za-z_]` on either side"): in a log, over-redaction is the safe side. `TOKEN_SHAPE =
  "me_[0-9A-Za-z]{32}"` is one string, compiled as `str` here and as `bytes` for `authenticate`.
- **e. Shutdown grace: uvicorn's default (`None`), not FastMCP's 0.** `run_http_async` passes
  `timeout_graceful_shutdown=0`, which cuts every call in flight on each deploy or token restart. KB's tools are
  synchronous and their worker thread runs to the end either way, so a finite grace never shortens a stop; it only
  drops the answer, and a dropped answer invites a retry of a non-idempotent write (`register_paper_tool`). The unit's
  stop timeout bounds the stop. Tested.
- **f. One builder for `main()` and the harness.** `build_http_app(tokens)` and `uvicorn_config(app, host, port)` are
  everything `--http` serves; `main()` runs `uvicorn.Server(uvicorn_config(...)).run()` and exits 1 when the server
  never started (`run()` returns normally after a failed lifespan; uvicorn's own CLI checks `server.started` the same
  way), so `Restart=on-failure` sees the failure. `mcp.run(transport="http")` cannot be that builder: it builds the
  app internally, cannot wrap it, reads port 0 as unset (`port or settings.port`) and defaults the grace to 0. `path`,
  `stateless_http` and `json_response` are explicit so no `FASTMCP_*` variable changes the served routes or session
  mode (`FASTMCP_DEBUG` still reaches Starlette's debug flag: an accepted residual). JSON responses: no KB tool streams
  progress; ME parity. The 401 carries `connection: close` and uvicorn runs with `server_header=False`: a refused
  client cannot hold the connection open by trickling a body, and an unauthenticated answer names no server software.
- **g. Restart mechanics.** The harness holds one listening socket on `127.0.0.1:0` for the whole test and gives each
  uvicorn server `sock.dup()`: no free-port window across a restart. A restart is a fresh `build_http_app` (a new
  session manager, the token file read again) and a fresh server, which is what a new process gives a client.
- **h. Startup order and exit codes.** Parse, token file, #569 gate, bind. A bad token file never touches the
  database. Every command-line error exits 78 too (the parser's `error()` is overridden): argparse's 2 would
  restart-loop a unit that sets `RestartPreventExitStatus=78` on a typo. `--help` exits 0.
- **i. `HOST:PORT`, an IP socket address like ME's `--listen`.** Split at the last `:`; HOST an IPv4 address, or an
  IPv6 address in brackets (passed to uvicorn without them), checked with `ipaddress`; PORT 1 to 5 ASCII digits within
  1–65535. A host name is refused: `127.0.0.l` would otherwise fail name resolution inside uvicorn, exit 1 and
  restart-loop the unit, the failure decision h exists to prevent. Anything else is a command-line error (78). The bind
  errors left (port in use, address not on the host) are transient and exit 1.

### Accepted residuals (repeated in the PR body)

- KB speaks plain HTTP: it must sit behind a TLS proxy such as `tailscale serve`, reachable only from the host (a
  loopback bind, or a container's port published on the host's loopback); any other exposure sends tokens in clear.
- No body-size limit on `POST /mcp` for token holders (the SDK reads a whole body); a refused request's body reaches
  no KB or FastMCP code (h11 discards it, and `connection: close` ends the connection after the 401).
- `FASTMCP_DEBUG=true` in the unit's environment would put tracebacks in 500 bodies, to token holders only.
- `FASTMCP_LOG_LEVEL=DEBUG` would log tool arguments (FastMCP's `Handler called: call_tool … with …`), token shapes
  redacted by decision d2 but other content in clear: an operator's opt-in.
- Anything written to stderr outside `logging` (a `print`, a `warnings` line, an uncaught thread exception) bypasses
  the formatter; KB's server writes none on these paths.
- Only the exact 35-character token shape is redacted: a value a library shortens (pydantic's `input_value` keeps
  `me_` plus 21 characters of a long string) or encodes can still show part of a token the client itself sent.
- A log sink that splits output at its own boundaries (a line limit under 2,000 characters, or fixed-size reads, as
  podman's conmon likely does, marking such pieces `CONTAINER_PARTIAL_MESSAGE`) can still cut a record at a point a
  client influences.
- A token holder's request stalled mid-body holds a stop until the unit's stop timeout (the cost of decision e).
- Every token holder reaches every tool until the lean tool map (#543): this issue is authentication, not
  authorization.

### Questions for Michaël (defaulted; the plan proceeds on the default)

1. **Token-file naming.** Default: `--token-file` only. Alternative: ME's pair, `--token-file` plus
   `KNOWLEDGE_BASE_TOKEN_FILE`, at the cost of a precedence rule and two tests; additive later.
2. **stdio against a live database.** The acceptance keeps "only with the unit stopped" a rule, and nothing enforces
   it, as the README already says of `re-embed` and `rebind`. Default: documented, not enforced in this PR.

## Implementation sequence

Branch commits carry no co-author line. Each test is seen red before its change.

- [ ] **0. Worktree and baseline (no commit).** `.worktrees/feat-571-http-bearer`, branch `feat/571-http-bearer` at
  `origin/master` 69ce65f, exists and is locked. `command -v tesseract` prints a path (else the OCR tests skip; say so
  in the PR). Run Verification's gate command list once and record the pytest pass and skip counts.

- [ ] **1. Plan issue (no commit).** Before approval is asked: the leak guards (Verification) over this plan, then
  `~/.claude/skills/finish-pr/scripts/archive-plan-issue.sh --repo dutiona/knowledge-base --plan-file
  docs/plans/2026-11-17-http-bearer.md --title "#571 — feat(server): serve MCP over HTTP behind per-machine Bearer auth"`
  (no `--pr`; one issue per plan; `/finish-pr` links and reuses it). Implementation starts on Michaël's approval.

- [ ] **2. Boy-scout: the errors' boundary.** `src/knowledge_base/exceptions.py` says `server.py` catches
  `KnowledgeBaseError` and merges `details`; the `routes/` modules do, and `server.py` catches only the startup gate's.
  Docstrings only, no executable behaviour. Commit: `docs(exceptions): name the route modules as the boundary that
  serializes errors`.

- [ ] **3. Boy-scout: the architecture overview's `server.py` paragraph.** `docs/design/architecture-overview.md`
  says `server.py` manages thread-local connections, schema locking, argument validation and JSON; those live in
  `_conn.py` and `routes/`. Rewrite it as the hub that mounts the six `routes/` modules, whose `main()` runs the #569
  gate then serves stdio (item 9 adds HTTP). Check: `git grep -n 'manages thread-local database connections' -- docs/
  ':!docs/plans'` prints nothing (the plan and its review files quote the phrase). Commit: `docs(architecture):
  describe server.py as the route hub it is`.

- [ ] **4. Read the token-hash file and check a Bearer value.**
  - Files: `src/knowledge_base/auth.py` (new), `tests/test_auth.py` (new), `tests/conftest.py`.
  - Red: `uv run pytest tests/test_auth.py -q` fails at collection (`No module named 'knowledge_base.auth'`).
  - Change, `@dataclass(frozen=True, slots=True) class TokenTable` with `entries: tuple[tuple[bytes, str], ...]`
    (digest, machine):
    - `parse(data: bytes) -> TokenTable` (classmethod): empty input is an empty table; split on `b"\n"` only (never
      `splitlines`, which also splits on a lone CR); a non-empty remainder after the last LF fails at its line; lines
      count from 1; machine and hash by `re.fullmatch` on bytes. The first offending line raises
      `ValidationError(f"token-hash file line {n}: {reason}")` with ME's reasons: `no line feed at its end`, `not two
      fields separated by one space`, `the machine is not a lowercase DNS label`, `the hash is not 64 lowercase hex
      digits`, `a repeated machine`, `a repeated hash`. No message quotes a line's bytes (a pasted token).
    - `load(path: Path) -> TokenTable` (classmethod): `path.read_bytes()`; an `OSError` becomes
      `ValidationError(f"cannot read the token-hash file {path}: {exc.strerror}")`; a line error is re-raised prefixed
      with the path. `ValidationError` is what `settings.py` raises for configuration, so `main()`'s existing
      `except KnowledgeBaseError` gives 78.
    - `authenticate(self, authorization: bytes) -> str | None`: strip exactly `b"Bearer "`; `fullmatch` of
      `TOKEN_SHAPE.encode()` (decision d2) before any hashing; `hashlib.sha256(token).digest()` compared by `hmac.compare_digest` with every entry, no
      early exit; returns the machine.
    - `tests/conftest.py`: `machine_token(machine: str) -> str` = `"me_" + machine.rjust(32, "0")` and
      `token_line(machine: str) -> str`, built at run time so no token literal is committed.
  - Tests: the ADR vector (`sha256(("me_" + "0" * 32).encode())` equals it; a one-line table admits it as `laptop`);
    two machines each named by its own token, a well-shaped unlisted token refused; an empty file admits no one; a
    missing file names its path; after a good line 1, each bad line 2 fails naming `line 2`: uppercase hex, uppercase
    machine, two spaces, CR before LF, no final LF, repeated machine, repeated hash, blank line, `#` comment, tab,
    leading `-`, trailing `-`, 64-character label, 63- and 65-digit hash, trailing space, a non-ASCII byte; a
    63-character label is accepted; a raw token pasted as the hash is refused and absent from the message; refused
    values: `bearer t`, `Basic t`, `Bearer  t`, `Bearer t ` (trailing space), 34 and 36 characters, `ME_` prefix,
    non-ASCII, a `-`, the bare token, empty; an off-shape value whose own SHA-256 is listed is still refused.
  - Green: `uv run pytest tests/test_auth.py -q` passes; `uv run ruff check src/ tests/` is clean.
  - Commit: `feat(auth): read the machine token-hash file and check Bearer tokens`.

- [ ] **5. The Bearer gate.**
  - Files: `src/knowledge_base/auth.py`, `tests/test_auth.py`.
  - Red: `uv run pytest tests/test_auth.py -q -k gate` fails (`ImportError: RequireMachine`).
  - Change: `class RequireMachine`, `__init__(self, app, tokens: TokenTable)`, `async __call__(scope, receive, send)`.
    A `lifespan` scope goes to `app` untouched. Any other scope passes only with exactly one `authorization` header
    that `tokens.authenticate` maps to a machine; `app` then gets a shallow copy of the scope whose `headers` lack
    `authorization` and whose `state` is a copy of the caller's plus `"machine"`. Otherwise, for an `http` scope, the
    gate sends `http.response.start` (401, `www-authenticate: Bearer`, `content-length: 0`, `connection: close`) and an
    empty `http.response.body`; any other scope type is closed (`websocket.close`). `app` is never called on a
    refusal.
  - Tests (`asyncio.run`, a recording inner app): refused (no header, off-shape, two identical valid headers, laptop
    plus desktop, non-ASCII bytes) gives that 401 and no inner call; accepted gives one inner call whose scope lacks
    `authorization`, keeps the other headers in order and holds `state["machine"] == "laptop"`, while the caller's
    scope and state are unchanged; a lifespan scope is forwarded as is.
  - Green: `uv run pytest tests/test_auth.py -q`.
  - Commit: `feat(auth): refuse every request without exactly one valid machine token`.

- [ ] **6. Serve stateless HTTP behind the gate; the harness.**
  - Files: `src/knowledge_base/server.py`, `pyproject.toml`, `uv.lock`, `tests/conftest.py`, `tests/test_http_auth.py`
    (new).
  - Red: `uv run pytest tests/test_http_auth.py -q` fails (`ImportError: build_http_app`). Then three mutations, each
    run, seen red and reverted before the commit (a test that stays green under its mutation is fixed first):
    `build_http_app` returns the bare FastMCP app (the refusal matrix sees 200, 404, 405 and the canaries move);
    `stateless_http=False` (the first-call and restart tests get 400); `timeout_graceful_shutdown=0` (the in-flight
    call loses its answer).
  - Change:
    - `build_http_app(tokens: TokenTable) -> RequireMachine` =
      `RequireMachine(mcp.http_app(path="/mcp", stateless_http=True, json_response=True), tokens)`.
    - `uvicorn_config(app, host: str, port: int) -> uvicorn.Config` = `uvicorn.Config(app, host=host, port=port,
      lifespan="on", ws="none", access_log=False, log_config=None, log_level="error", server_header=False)`,
      `timeout_graceful_shutdown` left at its default; one comment each for `ws` (decision b), the logging (decision d)
      and the grace (decision e).
    - `pyproject.toml`: `"uvicorn>=0.41.0"` in `dependencies` (now a direct import; the floor is the locked version,
      as for the other entries); `uv lock` changes only the project's own requirement lines.
  - Harness, `tests/conftest.py`:
    - `token_file` fixture: `token_line("laptop") + token_line("desktop")` in `tmp_path / "token-hashes"`.
    - `stub_embedder` fixture: patches `knowledge_base.search.embed_single` (the caller's name, per AGENTS.md rule 6)
      with a recorder answering `[0.1] * TEST_EMBED_DIM`; the fixture is the list of texts it received.
    - `KbHttp(app_factory)`: owns one listening socket on `127.0.0.1:0`. `start()` runs
      `uvicorn.Server(uvicorn_config(app_factory(), "127.0.0.1", port)).run(sockets=[sock.dup()])` in a daemon thread,
      waits for `started` (10 s), and fails if the thread ends first, re-raising its error if it has one (a failed
      lifespan returns from `run()` without raising). `stop()` sets `should_exit` and
      joins (10 s; fails if the thread lives on; a no-op when stopped). `restart()`. `rpc(method, params=None, *,
      machine="laptop", headers=None) -> httpx.Response` and `request(method, path, *, authorization=None,
      headers=None, content=None)`, one fresh `httpx` client per request (no pooled connection outlives a restart).
      Its docstring warns against combining it with `http_requests`, which patches the transport this client uses.
    - `declared_db` fixture: `open_declared_db().close()` as `main()` does, on the per-test database the autouse
      fixtures already point at. (No `_conn._local` patch: anyio's worker threads belong to one event loop, and each
      harness server runs a fresh loop, so no worker carries a connection from an earlier test.)
    - `kb_http` (uses `declared_db`, `token_file`, `stub_embedder`): `KbHttp(lambda:
      build_http_app(TokenTable.load(token_file)))`, started, stopped at teardown.
  - Tests, `tests/test_http_auth.py`:
    - `test_every_request_without_a_valid_token_is_refused`: credentials {none, `Bearer not-a-token`,
      `bearer <laptop>`, `Basic <laptop>`, `Bearer <stranger>` (well-shaped, unlisted), laptop twice, laptop plus
      desktop} × requests {POST `/mcp` with `initialize`, `tools/list`, `tools/call search_index`, `tools/call
      register_paper_tool`; GET and DELETE `/mcp`; GET `/mcp` shaped as a WebSocket upgrade; GET and POST on `/`,
      `/nope`, `/mcp/`, `/.well-known/oauth-protected-resource`, `/.well-known/oauth-protected-resource/mcp`,
      `/.well-known/oauth-authorization-server` and every path in the inner app's `routes`}: each 401,
      `WWW-Authenticate: Bearer`, empty body. Then `stub_embedder == []` and the `papers` table is empty. Then the same
      two tool calls with laptop's token: 200, one embedder text, one `papers` row (each canary can move).
    - `test_tools_list_is_served`: laptop, 200 `application/json`; the set of `result.tools[].name` equals
      `{t.name for t in asyncio.run(server.mcp.list_tools())}`.
    - `test_the_first_request_may_be_a_tool_call`: desktop, `tools/call search_index`, no `initialize`: 200,
      `result.isError` false, no `mcp-session-id` header, the stub saw the query.
    - `test_the_tailnet_host_is_served`: `Host: kb.example-tailnet.ts.net` with `X-Forwarded-For` and
      `X-Forwarded-Proto: https`, as a proxy hop sends them: `tools/list` 200.
    - `test_statuses_with_a_token`: GET and DELETE `/mcp` 405; GET `/nope` 404.
    - `test_a_restart_needs_no_initialize`: `initialize` 200; append a `phone` line to the file, `phone` gets 401
      (read at startup only); `restart()`; `tools/call status` from `laptop` and from `phone`, no `initialize`: 200.
    - `test_a_stop_answers_the_call_in_flight`: the stub blocks on an event; `tools/call search_index` in a thread;
      once the stub is entered, `stop()` in a thread, 0.5 s, release: the call gets 200 and the server thread ends
      within 10 s.
  - Green: `uv run pytest tests/test_auth.py tests/test_http_auth.py -q`; `uv lock --check`.
  - Commit: `feat(server): serve MCP over stateless HTTP behind the machine-token gate`.

- [ ] **7. `--http` and `--token-file` on the entry point.**
  - Files: `src/knowledge_base/server.py`, `tests/test_server.py`.
  - Red: the in-process `server.main()` calls become `server.main([])` (pytest's argv must not reach argparse) and the
    tests below are added; `uv run pytest tests/test_server.py -q` fails (`main()` takes no argument).
  - Change:
    - `main(argv: Sequence[str] | None = None) -> None`, argparse `prog="knowledge-base"` whose `error()` prints the
      usage and the message to stderr and exits 78 (decision h); `--http HOST:PORT` (type `_host_port`, decision i;
      its message names `--http`) and `--token-file PATH` (`type=Path`, help names ADR-0020's token-hash file).
    - Order (decision h): parse; with `--http`, no `--token-file` is the command-line error `--http needs
      --token-file`, else `TokenTable.load`; `open_declared_db().close()`; then
      `server = uvicorn.Server(uvicorn_config(build_http_app(tokens), host, port))`, `server.run()`, and
      `sys.exit(1)` if `not server.started` (decision f); or `mcp.run(transport="stdio")`. Every `KnowledgeBaseError`
      keeps the `knowledge-base: <reason>` line and exit 78. `--token-file` without `--http` is a command-line error
      too.
    - The module docstrings of `server.py` and `tests/test_server.py` name both modes.
  - Tests (`mcp.run` patched; `uvicorn.Server.run` patched with `autospec=True` and
    `side_effect=lambda self, *a, **k: setattr(self, "started", True)`, except in the never-starts test; no port is
    bound):
    - `test_main_serves_http_through_the_gate`: `main(["--http", "127.0.0.1:65000", "--token-file", f])` runs one
      server whose config has a `RequireMachine` app, host `127.0.0.1`, port 65000; `mcp.run` not called.
    - `test_http_needs_a_token_file` and `test_a_token_file_needs_http`: 78, stderr names the flag, nothing served, no
      database file.
    - `test_a_bad_token_file_exits_78_before_the_database` (missing: stderr names its path; line 2 in uppercase hex:
      `line 2`): 78, nothing served, the database file does not exist.
    - `test_the_declaration_gate_runs_before_http_is_served`: `KNOWLEDGE_BASE_EMBED_MODEL` unset: 78 naming it,
      nothing served.
    - `test_a_bad_command_line_exits_78` (`--http nope`, `127.0.0.1:`, `:80`, `127.0.0.1:0`, `127.0.0.1:65536`,
      `127.0.0.1:+80`, `::1:80`, `[::1`, `127.0.0.l:80`, `localhost:80`, full-width digits, `--bogus`): 78, nothing
      served, stderr names `--http` or the flag. `[::1]:8000` is accepted and reaches the server as host `::1`.
    - `test_a_server_that_never_starts_exits_1`: `uvicorn.Server.run` patched to return without setting `started`:
      exit 1.
  - Green: `uv run pytest tests/test_server.py -q`; the old tests keep their meaning (`main([])` is stdio).
  - Commit: `feat(server): add --http and --token-file to the entry point`.

- [ ] **8. Log the calling machine per call, safely; refuse a gate-less HTTP call.**
  - Files: `src/knowledge_base/auth.py`, `src/knowledge_base/server.py`, `tests/test_auth.py`,
    `tests/test_http_auth.py`, `tests/test_server.py`.
  - Red: `uv run pytest tests/test_auth.py tests/test_http_auth.py -q -k "logs or gate_less or formatter"`: no record,
    the gate-less app writes a `papers` row, `RedactingFormatter` does not import. The in-memory test is green before
    and after: it guards stdio against the new middleware.
  - Change:
    - `class CallingMachine(fastmcp.server.middleware.Middleware)`, only `on_call_tool`, per decision d:
      `get_http_request()` raising `RuntimeError` calls through; `state.machine` present: `logger.info("machine %s
      called %r", machine, context.message.name)` then `call_next`; absent: `raise ToolError("refused: this call
      reached KB over HTTP without a machine token")`.
    - The hub: `FastMCP("knowledge-base", instructions=..., middleware=[CallingMachine()])`.
    - `class RedactingFormatter(logging.Formatter)` and `log_to_stderr() -> None`, per decision d2: a
      `StreamHandler(sys.stderr)` with that formatter becomes the root's handler; `knowledge_base.auth` at INFO; the
      `fastmcp` logger's handlers removed and `propagate = True`. `server.py` imports it by name (`from .auth import
      log_to_stderr`) and `main()`'s HTTP branch calls it before the server runs. Item 7's in-process `--http` tests
      patch it to a no-op (`monkeypatch.setattr(server, "log_to_stderr",
      lambda: None)`): restoring logging state around pytest's own capture handlers is fragile, and the formatter unit
      test and the real child cover the function.
    - Within the item: the middleware first, in-process tests green; then the process test without the
      `log_to_stderr()` call, seen red (no machine line on stderr, the SDK's warning holds the raw token); then the
      call.
  - Tests:
    - `test_each_tool_call_logs_its_machine` (caplog INFO on `knowledge_base.auth`): laptop calls `search_index`,
      desktop calls `status`: exactly two records, each naming its machine and its tool; `initialize`, `tools/list` and
      a refused `tools/call` add none.
    - `test_the_formatter_redacts_and_keeps_one_record_per_line` (`tests/test_auth.py`): a token in the message, in
      the `args`, and in an exception's text under `exc_info` comes out as `me_[redacted]` in each; a message holding
      `\n` plus a would-be record line, a CR, a NUL, an ESC and DEL comes out with every line after the first
      starting with two spaces and those four as `\x0d`, `\x00`, `\x1b` and `\x7f`, a tab kept;
      a 4,500-character message comes out as physical lines of at most 2,002 characters, every one after the first
      starting with two spaces; a token straddling the 2,000th character is still redacted (redaction runs first).
    - `test_a_gate_less_http_app_runs_no_tool` (uses `declared_db`): `KbHttp(lambda: mcp.http_app(path="/mcp",
      stateless_http=True, json_response=True))`, no token: `tools/call register_paper_tool` answers 200 with
      `result.isError` true, and `papers` stays empty.
    - `test_an_in_memory_call_still_runs` (uses `declared_db`): `fastmcp.Client(server.mcp)` calls `status`: a result,
      no record (the stdio path has no HTTP request either).
    - `test_the_http_process_logs_each_call_and_never_the_token` (`tests/test_server.py`, a real child, the only
      place the formatter's effect on library records is observable: caplog sees records before any handler): a port
      from a probe bind on `127.0.0.1:0`, closed just before the spawn (the reuse race is accepted); `[sys.executable,
      "-m", "knowledge_base.server", "--http", f"127.0.0.1:{port}", "--token-file", f]` with the suite's declaration
      and `KNOWLEDGE_BASE_DB` in `tmp_path`, `# noqa: S603` as `_run_server` has; stdout and stderr to files; poll an
      unauthenticated POST `/mcp` until 401 (30 s; fail at once, quoting the child's stderr, if it exits). As laptop,
      no `initialize`, each leak call checked on the stderr bytes it added (the file's size before the call; poll up
      to 5 s for `me_[redacted]` in what follows): `tools/call status` (200); a `tools/call` named with laptop's token
      (the SDK's unknown-tool warning: the added bytes hold `me_[redacted]`); `tools/call status` with an unexpected
      argument whose name and value are the token (`{token: token}`; the key is printed in the error's location
      whatever pydantic's length limit for values: measured, a 35-character value prints in full and a 55-character
      one is shortened; `result.isError` true;
      FastMCP's validation traceback: the added bytes hold `me_[redacted]`); tools named `"x\nINFO knowledge_base.auth: machine desktop called 'status'"`, the same with
      `\u0000` instead of `\n`, and the same text preceded by 2,000 `a` characters. SIGTERM, ended within 15 s (its
      exit status is not asserted: uvicorn re-raises the SIGTERM it caught). Then: exactly two stderr lines start
      `INFO knowledge_base.auth: machine laptop called 'status'` (the validation call is logged too: FastMCP validates
      arguments after the middleware); no line starts `INFO knowledge_base.auth: machine desktop`; no stderr line is
      longer than 2,002 characters (the long name would otherwise make one, in KB's line and the SDK's); neither file
      contains the token or a NUL byte.
  - Green: `uv run pytest tests/test_auth.py tests/test_http_auth.py tests/test_server.py -q`.
  - Commit: `feat(auth): log the calling machine of every tool call, redacted and line-safe`.

- [ ] **9. Documentation** (below). No executable behaviour; check `git grep -n "Until the server gains an HTTP
  transport" -- docs/ ':!docs/plans'` prints nothing, and the leak guards print nothing. Commit: `docs: document the
  HTTP transport and machine tokens`.

- [ ] **10. Gate and PR (no commit).** Rebase on `origin/master` if it moved. Verification at the final clean HEAD,
  unpiped. Push; open `feat(server): serve MCP over HTTP behind per-machine Bearer auth` against `master`, labels
  `type:feature` and `area:mcp`. Body: `Closes #571`, `Refs dutiona/infra#37` (no other closing keyword, negated or
  not), `Classification: Feature (researched) (user-confirmed)` and the other three route lines, the plan issue,
  decisions a to i (with d2) one line each, the accepted residuals, the questions, the deploy note (the infra unit passes
  `--http` and `--token-file`; nothing deploy-specific is in the repository), the baseline and final gate output, and
  `Collateral findings`. Leak guards over the body before posting.

- [ ] **11. Review.** `/super-review` at the confirmed gate on the PR, adopting the warm plan reviewers; each valid
  finding fixed test-first in its own commit, Verification re-run. Then `/finish-pr` to READY with a fresh zero
  unresolved-thread count; it archives this plan and removes `docs/plans/` before merge.

- [ ] **12. Merge.** Michaël alone, through `/squash-merge` on his OK; the squash commit carries exactly one co-author
  line.

## Documentation

- `README.md`, "Run the server": after the stdio block, an HTTP block
  `uv run knowledge-base --http 127.0.0.1:<port> --token-file /path/to/token-hashes` (declaration exported as above),
  and: plain `knowledge-base` serves stdio, the local and maintenance surface; run it against the service's database
  only with the HTTP server stopped (nothing enforces it).
- `README.md`, "Configuration": the exit-78 paragraph adds a bad command line, `--http` without `--token-file`, and a
  missing, unreadable or malformed token-hash file (named by path and line), all before the database is opened.
- `README.md`, a new "HTTP transport and machine tokens" subsection: stateless `POST /mcp` with JSON responses (a
  restart needs no client recovery); the exact header and the file format by reference to memory-engine's ADR-0020 §4,
  one example line `laptop <64 lowercase hex>`, and that memory-engine's file and token serve KB too; the mint recipe,
  which keeps the token off every argv and hashes no line feed:
  `printf '%s %s\n' laptop "$(printf '%s' "$token" | sha256sum | cut -d' ' -f1)" >> token-hashes`; read at startup
  only; statuses (401 any request, method or path without exactly one valid token; 404 an unknown path and 405 GET or
  DELETE on `/mcp` with one; other 4xx for a POST the MCP SDK refuses; never 403); every Host is served and the
  transport is plain HTTP, so keep the port reachable from the host only (a loopback bind, or for a container the
  Installation guide's published-port setup) behind a TLS proxy such as `tailscale serve`; no unauthenticated route,
  so a liveness probe expects 401; one
  stderr line per tool call names the machine and the tool, and any token shape in any log line is written
  `me_[redacted]`; a stop lets calls already started finish, bounded by the
  unit's stop timeout; only `knowledge-base --http` serves HTTP, and any other HTTP server of the hub runs no tool; a
  client sends the header itself (the Claude Code plugin's `headersHelper`, ADR-0020 §12), never in `.mcp.json` or on a
  command line; a manual check that reads the header from standard input:
  `printf 'Authorization: Bearer %s\n' "$(cat ~/.config/memory-engine/secrets/token)" | curl -sS -H @- -H 'Accept: application/json' -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' https://kb.example-tailnet.ts.net/mcp`.
- `docs/getting-started/installation.md`: the fastmcp row reads "(stdio and HTTP transports)" and a `uvicorn` row is
  added; "The server uses FastMCP's stdio transport." becomes stdio by default, or stateless HTTP with `--http` (README);
  the "Until the server gains an HTTP transport (#571)…" sentence becomes: under `--http` the container needs no `-i`;
  inside it the server binds `--http 0.0.0.0:<port>` (a loopback bind inside a bridge or slirp4netns container is not
  reachable through a published port), the unit publishes that port on the host's loopback only
  (`PublishPort=127.0.0.1:<port>:<port>`), so plain HTTP crosses only the hop inside the host, and mounts the
  token-hash file read-only; podman's stop timeout (`StopTimeout=`, 10 s by default) bounds how long a stop waits for
  calls in flight; over stdio the container still exits when its standard input closes.
- `README.md`, "Limitations": "Single-user, single-process" becomes: one process, shared by every machine holding a
  token, each of which reaches every tool until the lean tool map (#543).
- `docs/design/architecture-overview.md`: the diagram's client edge reads `stdio or HTTP + Bearer`; the `server.py`
  paragraph (item 3) adds `--http`; a new `auth.py` paragraph (the token-hash file, the gate, the calling machine).
- `AGENTS.md`, Project Layout: `auth.py  # machine tokens: the token-hash file, the Bearer gate, the per-call log`;
  `server.py  # entry point: stdio, or stateless HTTP behind the gate with --http`.
- Unchanged: `Containerfile` (`CMD` stays stdio; the unit's `Exec=` passes `--http`, and no port belongs in this
  repository), the ADRs, `docs/reference/mcp-tools.md` (the tool surface does not change).

## Testing

- Unit: `tests/test_auth.py` (`TokenTable`; `RequireMachine` at its ASGI seam, where header removal is observable).
- Integration: `tests/test_http_auth.py` through `kb_http`, the production `build_http_app` and `uvicorn_config` on a
  real socket.
- Entry point: `tests/test_server.py`, in-process `main([...])` with the server run patched (arguments, order, 78), plus
  one real child for what only a process shows: the console wiring, the visible log line, SIGTERM, no token in output.
- "No tool runs" rests on three canaries, each shown to move under a valid token in the same test: an embed
  (`search_index`, the stub), a write (`register_paper_tool`, a `papers` row), and the per-call record.
- Each composition test is shown able to fail: item 6's three mutations, item 8's visibility red.
- Assertions check statuses, headers, bodies, counts, stored rows and KB's own text (the line number, the path, the
  flag, the log line); never a dependency's wording or version (uvicorn's logs, argparse's usage text, the SDK's 4xx
  bodies).
- No token literal in any file: tokens are built at run time; the ADR vector's hash is the only literal, and ADR-0020
  publishes it.
- Flake guards: every wait has a deadline; one held socket per in-process test; one connection per request; the child
  writes to files; the in-flight test's 0.5 s only decides how reliably its mutation goes red.
- Not tested: compare timing (reviewed: `hmac.compare_digest` over every entry); a real `tailscale serve`; the unit
  itself (infra's smoke).
- Acceptance map: `--http`, the #569 gate before the bind, stdio stays → 6, 7, 8. Stateless, restart without
  re-initialization → 6. The ADR-0020 file, 401 with no tool run, `tools/list`, the machine logged per call → 4, 5, 6,
  8. FastMCP routes expose nothing, the tailnet Host is served → 6. The harness in `tests/conftest.py` → 6.

## Verification

At the final clean HEAD (`git status --porcelain` empty), unpiped, results quoted in the PR:

```bash
uv lock --check
uv sync
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest tests/ -q -m "not slow"
uv sync --all-groups
uv run basedpyright src/ tests/
```

Then:

- `git diff origin/master -- uv.lock` only adds `uvicorn` to the project's own requirements; no version moves.
- Generic leak guard, prints nothing:
  `rg -n -e '\b100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.' -e 'me_[0-9A-Za-z]{32}' -e '\.ts\.net\b' $(git diff --name-only origin/master...HEAD) | rg -v 'kb\.example-tailnet\.ts\.net'`,
  and the same over `git log --format=%B origin/master..HEAD` and the PR body. The deployment patterns (tailnet name,
  addresses, KB's port) live outside the repository in
  `~/.local/state/agent-work/2026-11-02-me-kb-claude-code/fleet/artifacts/knowledge-base-571/private-leak-patterns.txt`:
  `rg -n -f` with it over the same files, messages and body.
- Smoke with a throwaway token file in scratch, never against the developer's database: export a declaration and
  `KNOWLEDGE_BASE_DB=<scratch>/kb.db KNOWLEDGE_BASE_DATA_DIR=<scratch>/kb-data`, then
  `uv run knowledge-base --http 127.0.0.1:<free port> --token-file <scratch>/token-hashes`; `curl` without a token
  prints 401; with it (`-H @-`), `tools/call status` answers 200 `application/json` and stderr shows
  `machine <name> called 'status'`; SIGTERM stops it.
