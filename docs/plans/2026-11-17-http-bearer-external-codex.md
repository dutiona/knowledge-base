# External plan review: codex (headless Codex, default model)

## Round 1

Michaël, the plan’s altitude is appropriate: the detail is concentrated in the shared token contract, HTTP boundary, and acceptance tests. I found no unjustified duplication. Confidence is high in the findings below.

- [HIGH] **Decision d and item 8 can log a bearer token.** The middleware logs the client-supplied tool name with `%r` before resolving the tool. A valid client can send a `tools/call` whose name is its token; FastMCP invokes middleware before tool lookup, so the token reaches stderr even though the tool does not exist. The ordinary-call test does not cover this. Redact token-shaped values from the logged name and test an unknown tool named with a token.

- [MEDIUM] **Item 6’s tool-list assertion compares different types.** `server.mcp.list_tools()` returns `Sequence[Tool]`, whereas the HTTP response contains tool names. Comparing those directly cannot pass. Compare the names extracted from both results instead.

The plan contains Documentation, Testing, and Verification sections. This was a read-only review; I did not run tests or gates.

Altitude report SHA-256: 8aaa4742ce04ffb9ce0a44fb0449a95ec1c1b37cd604d84b6c81454674ce66ed

## Round 2

Michaël, the round-two growth is justified by the review dispositions; I found no new duplication. The `tools/list` comparison resolves my earlier medium finding. The token-log finding is only partially resolved.

- [HIGH] **Decision d and item 8: the SDK logs the token before the new redaction runs.** For an unknown tool, mcp 1.26.0’s `_get_cached_tool_definition()` logs `Tool '%s' not listed` with the raw name before it calls FastMCP’s handler and middleware. A token used as the tool name therefore still reaches stderr. The new test checks the `knowledge_base.auth` record and can miss the SDK warning. Cover the complete process output for this request and prevent the SDK log from exposing the name.

- [HIGH] **Item 8: the shared regex has incompatible input types.** Item 4 specifies `authenticate(authorization: bytes)` and a bytes token shape; item 8 changes the shared pattern to `re.compile(r"…")`, a string regex, while also using it on the string tool name. Applying that pattern to the bytes token raises `TypeError`, so valid HTTP authentication fails. Use compatible patterns or explicitly convert at the boundary, and retain tests for both authentication and log redaction.

This was a read-only delta review; no tests or gates were run.

Altitude report SHA-256: a675e4fbd71410b4cd914419f70d84ace5d6a8da12d3dfe231e854cb30edf487

## Round 3

Michaël, the round-three additions are justified by the logging findings; I found no new plan duplication. The separate string and bytes patterns resolve the regex type mismatch, and routing the SDK and FastMCP loggers through the formatter addresses the previously identified warning path.

- [MEDIUM] **Item 8’s child-process test expects the wrong call count.** It sends `status` twice: once normally and once with an unexpected argument. With FastMCP’s default `strict_input_validation=False`, the second request reaches `CallingMachine` before tool validation, so it also logs `machine laptop called 'status'`. The assertion requiring exactly one such line will fail. Assert two calls, or use a different tool for the validation case. Also assert that the validation request produces the intended error and traceback: the single `me_[redacted]` assertion can currently be satisfied by the unknown-tool warning alone.

This was a read-only delta review; no tests or gates were run.

Altitude report SHA-256: b433a3f65ff5098a1dec43052c1ef905ae6314b7cad0a5d81cfd3a80e25c1c50

## Round 4

Michaël, **LGTM** for the round-four delta. Confidence is moderate: the prior test finding is addressed, and I found no new gap in the revised formatter rules or test plan. The added detail is justified by the logging findings and does not duplicate the implementation.

This was a read-only review; no tests or gates were run.

Altitude report SHA-256: 57cd845a03cc79abac4cd665a1305f700903d0de96b5c5f9b249d2d57d0d56c8

## Round 5

Michaël, **LGTM** for the round-five delta. The changes are appropriately scoped, and I found no new gap. Confidence is high for this delta.

This was a read-only review; no tests or gates were run.

Altitude report SHA-256: 4ba0933cd4e386eea3704e3b40a8ef7b2bb602256cde0449d6ad534a67107a65

REVIEW COMPLETE
