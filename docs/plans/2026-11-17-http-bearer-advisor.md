# Advisor capability unavailable

Altitude report SHA-256: 8aaa4742ce04ffb9ce0a44fb0449a95ec1c1b37cd604d84b6c81454674ce66ed

- Reason: `advisor()` is not exposed to this fleet-worker session (no such tool in its tool list).
- Gate: Full. Its advisor-less form ran instead: Codex (headless) + agy (`gemini-4-argon-high`, resume) + one
  clean-slate Opus subagent. No advisor attribution is made anywhere in this cycle.
- Findings from the fallback reviewers are recorded, attributed, in
  `docs/plans/2026-11-17-http-bearer-subagent-review.md`, `docs/plans/2026-11-17-http-bearer-external-codex.md` and
  `docs/plans/2026-11-17-http-bearer-external-agy.md`; their resolutions are below.

## Resolution

Round 1 of the fallback reviewers (no advisor attribution):

- Codex [HIGH] "the per-call log can record a token a client sends as the tool name" → decision d and item 8: every
  `me_[0-9A-Za-z]{32}` in the logged name becomes `me_[redacted]`; new test
  `test_a_token_named_as_the_tool_is_not_logged`.
- Codex [MEDIUM] "item 6 compares `Tool` objects with names" → the assertion compares the two sets of names.
- agy: LGTM, no finding.
- Clean-slate subagent: 6 MEDIUM, 7 LOW, all addressed (its own `## Resolution`).

## Resolution (round 2)

Altitude report assessed by the fallback reviewers this round: `a675e4fbd71410b4cd914419f70d84ace5d6a8da12d3dfe231e854cb30edf487`.

- Codex [HIGH] "the SDK logs an unknown tool's raw name before the middleware's redaction" → decision d2: a redacting,
  line-safe formatter on the process's one stderr handler, FastMCP's logger routed through it; verified first in a
  scratch spike, which also showed FastMCP logging argument values in validation tracebacks (now covered too).
- Codex [HIGH] "a str regex shared with the bytes `authenticate`" → one `TOKEN_SHAPE` string compiled twice.
- agy: LGTM.
- Clean-slate subagent: 1 HIGH (the same SDK log path, plus newline forgery), 1 MEDIUM, 2 LOW, all addressed in its
  own `## Resolution (round 2)`.

## Resolution (round 3)

Altitude report assessed by the fallback reviewers this round: `b433a3f65ff5098a1dec43052c1ef905ae6314b7cad0a5d81cfd3a80e25c1c50`.

- Codex [MEDIUM] "item 8's child test expects one `status` line; both `status` calls are logged, and one
  `me_[redacted]` can be satisfied by the unknown-tool path alone" → two lines asserted; each leak call checked on the
  stderr bytes it added; the validation call's `result.isError` checked.
- agy [LOW]: the same line-count mismatch → the same fix.
- Clean-slate subagent: 2 MEDIUM, 2 LOW, addressed in its `## Resolution (round 3)` (NUL and overlong-line record
  splits now escaped and broken by the formatter).

## Resolution (round 4)

Altitude report assessed by the fallback reviewers this round: `57cd845a03cc79abac4cd665a1305f700903d0de96b5c5f9b249d2d57d0d56c8`.

- Codex: LGTM.
- agy [MEDIUM] "pydantic shortens the 35-character token in `input_value`, so the validation window never holds
  `me_[redacted]`" → refuted by measurement in the worktree's venv (pydantic 2.12): a 35-character value prints in
  full (`input_value='<token>'`), a 55-character one is shortened; spike 3 shows the full token too. Its suggested
  hardening is adopted anyway: the unexpected argument's name is the token as well, and the error's location prints
  the key in full whatever the value limit.
- Clean-slate subagent: LGTM with four LOW, all applied (its `## Resolution (round 4)`).

## Resolution (round 5)

Altitude report SHA-256: 4ba0933cd4e386eea3704e3b40a8ef7b2bb602256cde0449d6ad534a67107a65

Confirmation round on the final plan bytes (`4555291c4acbf5a61bb91941848461599e8c9d4e1c6766983bbdddd2769a4893`): Codex LGTM, agy LGTM,
clean-slate subagent LGTM. No change required.

## Resolution (round 6)

Altitude report SHA-256: 0a1fde183dc0452cd6981313ad25037efab001d9379224ca044641179635fc5c

Publishing the round-5 plan failed its byte-for-byte read-back: GitHub altered the literal backslash-u escape for NUL in
item 8 (the incomplete archive, issue #590, is closed as superseded). Item 8 now names the character in words; nothing
else changed (plan `61434ebe86cf44c91eebe73cba3466f666403f615771284218e9bd3de1b5428e`). Binding confirmation: Codex
LGTM; clean-slate subagent LGTM; agy LGTM on `gemini-3.8-flash-high`, because `gemini-4-argon-high` returned the account
quota error (429) and its round ended incomplete with verified cleanup. No change required.
