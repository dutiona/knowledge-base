# Implementation audit (pre-review): #592 content redaction

- Approved plan: `docs/plans/2026-11-17-kb-592-content-redaction-v2.md`, SHA-256
  `581603829a68b79a47867b0066e7b8cd58c287439fdffd7e176b20a91c787732` (approved on #618, comment 6956497236).
- Implementation head audited: `c0aac3e3eb2f4ff0e98e911656817dad4ea4fdae` (base a518ab9).
- Scope: pre-review.

## Owner amendment given with the approval

Michaël's answer to the plan's scope question, given with the approval (relayed verbatim by the orchestrator; posted on
memory-engine#1024, comment 6956673032): "I don't see why we should redact a token query that is published worldwide
from a paper. If a publish paper has an url with a token/key, we don't redact it. It's already public knowledge
(published paper) and would do no good. It would hinder us from repdocucing or accessing results. It's the own author
responsability to get its private keeys out of a public published paper before pushing it (and the editor's job too),
not ours. I'll introduce bug in our pipe that is already very tricky. So so, we don't redact stuff from link that are
in a paper."

So links are not redacted, inside document text too; only a URL's `user:pass@` userinfo is. The engine treats a
`scheme://` followed by RFC 3986 characters as a link; inside one, only url-credential redacts (the password). The
fixture's header says so and the `link-*` cases pin it. The orchestrator directed recording this bounded change here
as the owner's ruling instead of a third plan version; the address-issue rule for changed approved intent is a
reviewed successor, so this departure is named for Michaël.

## Items

| # | Item | Status | Notes |
| --- | --- | --- | --- |
| 0 | Pin "current", build the oracle | Implemented | main and 97c8e77 have the same blob (31b3adaf); memory-engine's 54 unit tests pass on the unmodified copy |
| 1 | Port the rules, shared fixture | Modified | owner amendment: link exemption added; the machine-token detector landed here too (item 2's fixture diff is therefore shown by V1's classes instead of a commit) |
| 2 | Machine-token shape | Implemented | detector in item 1's commit; `TOKEN_SHAPE` moved, `auth.py` imports it (3eaba81) |
| 3 | Prove the purge | Implemented | the byte test required the vec0 rebuild branch (sqlite-vec keeps a deleted vector through VACUUM); refusal is exclusive locking mode (`held_exclusively`), which also refuses an idle connection: stronger than the planned checkpoint check, same intent |
| 4 | Redact a document before chunking | Implemented | plus a separate typing fix for the flat-PDF stub (189e518) |
| 5 | Reingest links a shared chunk | Implemented | |
| 6 | Metadata at the insert | Modified | `_sanitize_url` not changed: it already drops userinfo by rebuilding the URL from `urlparse` (and lowercases the host, drops the query), so reuse would add code, not remove a duplicate |
| 7 | Fetched URL without userinfo | Implemented | the fetch-error text and three log lines also name the stored URL |
| 8 | Figure text; folder summaries | Implemented | two commits |
| 9 | Insert-site tripwire | Implemented | shown red by removing one map entry locally |
| 10 | Report | Implemented | `redact.findings()` added so the report skips a match that is already its own placeholder |
| 11 | Rewrite | Implemented | the transaction step always runs, so a summary that alone holds a secret is rebuilt (test added); `cmd_redact` closes its connection |
| 12 | Documentation | Implemented | CLAUDE.md and GEMINI.md got the same two facts as AGENTS.md |

Counts: Implemented 11, Modified 2, Dropped 0, Incomplete 0, Added 0. Divergence (Modified + Dropped + Added) 2 of 13
= 15%, under the 30% arbitration threshold.

## Verification evidence (scratch, not committed)

- V1: the patched oracle build (memory-engine 97c8e77 with the rulings applied) wrote every output but one; the
  unmodified build agrees with every case except 6 machine-token cases, 8 link cases (one of them memory-engine's
  `url_embedded_github_pat_redacted`, now `connection-string-password` instead of `github-pat`) and the one input
  both builds panic on (`url-authority-ends-at-multibyte-space`, hand-written as planned).
- V2: 200,000 generated inputs: 178,401 identical between the patched Rust build and `redact()`, 0 differences,
  21,599 Rust panics, all of them the multibyte-terminator panic (each disappears when multibyte whitespace is
  replaced by a space).
