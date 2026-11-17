# [Codex Review] External plan review: 2026-11-17-kb-543-lean-surface

Codex, headless, one resumed thread, config default model `gpt-6.1-terra`. Rounds 1 to 4 are condensed here (the advisor
artifact lists the same findings with their resolutions); round 5, bound to the final altitude report, is verbatim.

- Round 1 (revision 1): [HIGH] `ingest_url`'s "nothing was stored" is false on stdio (figure phases at
  `src/knowledge_base/web.py:1177` can commit, line 1058, before the text embedding at line 1262 fails). [HIGH] Item
  8's "every row unchanged" cannot pass: inserts update `embed_spaces.chunk_count` (`src/knowledge_base/db.py:662`).
- Round 2 (revision 2): LGTM.
- Round 3 (revision 3): [MEDIUM] G3 overstates what the request cap protects. [MEDIUM] The race contract covers only
  `ingest_text`.
- Round 4 (revision 4): LGTM.

## Round 5 (verbatim)

Michaël, the altitude report’s **proceed** disposition remains justified. Confidence is high.

- **[LOW] Item 6’s refusal message is broader than D3’s rule.** “No … separator characters” also describes Unicode space separators, which D3 permits within a name. Say “line or paragraph separators” so the message matches the accepted input.

Altitude report SHA-256: 0c28f11a87d4c778fe12854d645efa49f8b3571a817c878074b5fdd09b784b02

REVIEW COMPLETE
