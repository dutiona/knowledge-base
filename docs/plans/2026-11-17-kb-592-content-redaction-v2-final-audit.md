# Implementation audit (post-review): #592 content redaction

- Approved plan: `docs/plans/2026-11-17-kb-592-content-redaction-v2.md`, SHA-256
  `581603829a68b79a47867b0066e7b8cd58c287439fdffd7e176b20a91c787732` (approved on #618, comment 6956497236).
- Implementation head audited: `5473f1ef2a87d0358d7f1466677cc093a4b0523b` (base 48c97a1, after the rebase over #615).
- Scope: post-review. The pre-review audit (`-implementation-audit.md`, head c0aac3e) stays as written; this file
  records what the review changed.

## Item status after review

The thirteen items keep their pre-review status: Implemented 11, Modified 2, Dropped 0, Incomplete 0, Added 0;
divergence 15%. Review changed how items 1, 4 and 7 meet their intent, never the intent itself:

| # | Item | Review change |
| --- | --- | --- |
| 1 | Port the rules, shared fixture | The link exemption was narrowed so no secret hides behind it: a link's userinfo is excluded from the link span (every rule still reads it), a machine token is redacted inside a link, and a `-----BEGIN` span stops where a link starts. The scans became linear (pending spans merged on flush, bisect overlap checks, the context-word tail length-checked). The fixture grew to 99 cases; its SHA-256 is now `adc8e496cef3e495fbc57c1c9f9133d8e17553d02cffb710b454103bb69bf7e9`, outputs still from the patched Rust oracle. |
| 4 | Redact a document before chunking | Chunking never cuts inside a link (a link stretches a window at most to four windows), and every stored chunk, and every piece the #615 input-limit split makes, is scrubbed again so it is a fixpoint. File ingest now scrubs before that split, so its stored-chunk lookup and token count see the stored text. The split moves a cut out of a link; only a link that alone counts more than the limit, or one over 4,000 characters, is still cut, as the usage docs say. |
| 7 | Fetched URL without userinfo | The fetch error text, the image log lines and a failed image download's log line name the URL without its userinfo. |

The machine-token-inside-a-link rule goes beyond the literal link ruling ("we don't redact stuff from link that are
in a paper"); it is named for Michaël in the PR body, since a machine token is never part of a published link.

## Review record

- Plan review: Codex (`gpt-6.1-terra`) and the Opus clean-slate lens; agy quota-blocked.
- Code review (Full gate, Default severity): the Opus clean-slate lens alone, seven rounds (round 1: 2 HIGH, 2
  MEDIUM, 4 LOW; rounds 2, 3, 5 and 6: LOWs; rounds 4 and 7: LGTM). Every finding was fixed except round 1 #5
  (memory-engine's half of AC2, on memory-engine#1024), left to Michaël. Codex (usage limit), agy Gemini and agy
  Opus (429), OpenCode GLM 5.3 (server error on the review task) and Vibe (provider error 3000) were unavailable.

## Verification at the audited head

Merge gate (AGENTS.md "Verify"): `uv lock --check` 0, `uv sync` 0, `ruff check` clean, `ruff format --check` 104
files, `pytest -m "not slow"` 1831 passed / 4 skipped / 10 deselected, `uv sync --all-groups` 0, `basedpyright` 0
errors. An end-to-end CLI run against a loopback stub embedder (ingest, `redact`, a legacy row, `redact --rewrite`,
a byte scan of the database files) passed every check; the PR's Test plan lists them.
