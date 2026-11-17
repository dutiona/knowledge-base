# Advisor capability unavailable

Reason: `advisor()` is not exposed in this fleet worker's tool set (Claude Code agent session, 2026-11-17); no
advisor call was possible, for review, triage, tie-break or the pre-completion check.

The confirmed gate is Full. Its advisor-less form (debate-defaults) ran Codex + agy + the clean-slate subagent:

- Codex, headless, config default model `gpt-6.1-terra` (the round script cannot select `gpt-6.1-sol`,
  my-dotfiles#1346; accepted by Michaël 2026-11-17), one resumed thread, five rounds;
- agy, `gemini-3.8-flash-high`, one resumed conversation, five rounds;
- the clean-slate Claude subagent (Opus), four rounds, recorded in `2026-11-17-kb-543-lean-surface-subagent-review.md`.

The orchestrating worker arbitrated severities itself where an advisor would have. Findings below are attributed to
the reviewer that produced them; none is attributed to an advisor.

## Codex findings, by round

- Round 1 (revision 1): [HIGH] D6/item 5 promised "nothing was stored" for `ingest_url` on stdio, where figure phases
  can commit before the text embedding fails. [HIGH] Item 8's "every row unchanged" cannot pass: a successful insert
  bumps `embed_spaces.chunk_count`. Altitude "proceed" justified.
- Round 2 (revision 2): LGTM.
- Round 3 (revision 3): [MEDIUM] G3 overstated what the request cap protects (a short URL can fetch a large page).
  [MEDIUM] The concurrent-duplicate mapping covered only `ingest_text`, not `ingest_url`.
- Round 4 (revision 4): LGTM.
- Round 5 (revision 5): [LOW] Item 6's refusal message says "separator characters", broader than D3's rule, which
  permits space separators; say "line or paragraph separators".

## agy findings, by round

- Round 1: [LOW] a name starting with `/` gives a double-slash URI. [LOW] `CallingMachine` must keep telling "no HTTP
  request" from "HTTP request without a machine" when `calling_machine()` is introduced. Design judged sound.
- Rounds 2 to 5: LGTM.

## Resolution

- [HIGH] Codex r1 `ingest_url` message → it now says "the page's text was not stored"; `ingest_text` keeps "nothing was
  stored" (revision 2).
- [HIGH] Codex r1 no-delete assertion → `chunk_count` may grow, `sqlite_sequence` may advance, only seeded rows must
  be unchanged and no table may lose a row (revisions 2 and 3).
- [MEDIUM] Codex r3 G3 scope → G3 states the cap bounds the request, and #588 the URL fetch (revision 4).
- [MEDIUM] Codex r3 race on `ingest_url` → item 5 maps `sqlite3.IntegrityError` for `ingest_url` too (revision 4).
- [LOW] agy r1 leading slash → refused (revision 2). [LOW] agy r1 `CallingMachine` → it keeps its own check, and only
  the tool uses `calling_machine()` (revision 2).
- [LOW] Codex r5 message wording → accepted and arbitrated without another round (exit condition: only a LOW remained
  and the round raised nothing load-bearing): item 6's refusal message is implemented as "name must be 1 to 255
  characters, with no control, format or surrogate characters and no line or paragraph separators, not starting with
  /". The plan bytes stay as reviewed; the plan audit records this one wording deviation.

Altitude report SHA-256: 0c28f11a87d4c778fe12854d645efa49f8b3571a817c878074b5fdd09b784b02
