# Implementation audit (post-review): #592 content redaction

- Approved plan: `docs/plans/2026-11-17-kb-592-content-redaction-v2.md`, SHA-256
  `581603829a68b79a47867b0066e7b8cd58c287439fdffd7e176b20a91c787732` (approved on #618, comment 6956497236).
- Implementation head audited: `3074d8f29d1007e573b7a4a61605c7bd855f74bc` (base 48c97a1, after the rebase over #615).
- Scope: post-review. The pre-review audit (`-implementation-audit.md`, head c0aac3e) stays as written; this file
  records what the review changed.

## Item status after review

The thirteen items keep their pre-review status: Implemented 11, Modified 2, Dropped 0, Incomplete 0, Added 0;
divergence 15%. Review changed how items 1, 4, 7, 10 and 11 meet their intent, never the intent itself:

| # | Item | Review change |
| --- | --- | --- |
| 1 | Port the rules, shared fixture | The link exemption was narrowed so no secret hides behind it: a link's userinfo is excluded from the link span (every rule still reads it), a machine token is redacted inside a link, and a `-----BEGIN` span stops where a link starts. The scans became linear (pending spans merged on flush, bisect overlap checks, the context-word tail length-checked). The fixture grew to 99 cases; its SHA-256 is now `adc8e496cef3e495fbc57c1c9f9133d8e17553d02cffb710b454103bb69bf7e9`, outputs still from the patched Rust oracle. |
| 4 | Redact a document before chunking | Chunking never cuts inside a link (a link stretches a window at most to four windows), and every stored chunk, and every piece the #615 input-limit split makes, is scrubbed again so it is a fixpoint. File ingest now scrubs before that split, so its stored-chunk lookup and token count see the stored text. The split moves a cut out of a link; only a link that alone counts more than the limit, or one over 4,000 characters, is still cut, as the usage docs say. |
| 7 | Fetched URL without userinfo | The fetch error text, the malformed-URL error, the image log lines, a failed image download's and a failed browser render's log lines (the subprocess error repeats its argv) and the HTML-parse warning name the URL without its userinfo. |
| 10 | Report | It refuses a missing database instead of creating one. It reads each legacy row between the windows that overlap it as the old 1000/200 chunker's did, paired per revision by their detected overlap (at least 100 characters, so windows stored stripped pair too) and read in every revision's context (bounded at 64 pairs, then one side at a time), so a fragment the old chunker cut is found; input-limit pieces cut before this release, and PDF windows an image-reference rewrite changed across the cut, are read alone, as the docs say. It reads chunks and folder summaries; the other tables are #610's, as the docs say. Its plan streams rows instead of loading the table. |
| 11 | Rewrite | The same context read decides each row's new content, so a fragment is redacted with the rest of its secret. |

The machine-token-inside-a-link rule goes beyond the literal link ruling ("we don't redact stuff from link that are
in a paper"); it is named for Michaël in the PR body, since a machine token is never part of a published link.

## Review record

- Plan review: Codex (`gpt-6.1-terra`) and the Opus clean-slate lens; agy quota-blocked.
- Code review (Full gate, Default severity): the Opus clean-slate lens, thirteen rounds (round 1: 2 HIGH, 2 MEDIUM,
  4 LOW; rounds 2, 3, 5, 6 and 8 to 12: LOWs; rounds 4, 7 and 13: LGTM, 13 at the audited head), and two external voices standing in for Codex
  (usage limit until 2026-11-18): OpenCode GLM 5.3 (2 MEDIUM, 5 LOW) and Vibe Mistral Large 4 (round 1: 3 MEDIUM, 3 LOW; round 2 on the fixes: 1 MEDIUM, 3 LOW).
  Every finding was fixed, or refuted with evidence on the PR (the PDF page break, the 8-pass cap, `SG.` parity with
  memory-engine, the locking pragma, non-JSON metadata, one source in memory, the docs' link sentence), or placed in its tracked issue (#610), except round 1 #5
  (memory-engine's half of AC2, on memory-engine#1024), left to Michaël. agy Gemini and agy Opus were unavailable
  (429).

## Verification at the audited head

Merge gate (AGENTS.md "Verify"): `uv lock --check` 0, `uv sync` 0, `ruff check` clean, `ruff format --check` 104
files, `pytest -m "not slow"` 1844 passed / 4 skipped / 10 deselected, `uv sync --all-groups` 0, `basedpyright` 0
errors. An end-to-end CLI run against a loopback stub embedder (ingest, `redact`, a legacy row, `redact --rewrite`,
a byte scan of the database files) passed every check; the PR's Test plan lists them.
