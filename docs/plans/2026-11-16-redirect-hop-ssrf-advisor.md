# Advisor capability unavailable

Altitude report SHA-256: 062da951ae911af126207d7ecf80d0ceb4c90d30064d7f613511296bda308367

- **Reason:** `advisor()` is not exposed to this fleet-worker session; there is no such tool in its tool list.
- **Gate:** Full. Its advisor-less form is Codex, agy and a clean-slate subagent, per `debate-defaults`.
- **Fallback reviewers that ran on this altitude report:**
  - Codex (headless), on the configured default model. `codex-headless-round.sh` has no model override, so the
    preferred `gpt-6-astra` or `gpt-6.1-sol` could not be selected.
  - agy, on `gemini-4-argon-high`, per Michaël's 2026-11-16 review-model instruction.
  - A clean-slate Claude subagent on Opus. Michaël's instruction forbids Fable as a lens.
- **Findings:** recorded in the Codex shared file, the agy round's `review.md` and
  `2026-11-16-redirect-hop-ssrf-subagent-review.md`, each attributed to its reviewer. No advisor attribution is
  made here.

## Resolution

No advisor ran. The fallback reviewers' round-1 findings and their outcomes follow, each attributed to its
reviewer. The subagent's own resolution is in `-subagent-review.md`.

**Codex** (headless, thread `01a1e613-…`):
- [HIGH] CDP rebinding: fixed by OC8. A CDP endpoint must be an IP literal, or `localhost` under the opt-in, so
  Playwright has no lookup to rebind. It is tested, and the alternative (accepting a residual) is listed for the
  owner.
- [HIGH] OmniParser trust inferred by equality: fixed by provenance threading (D4, item 8).
- [HIGH] `ip.ipv4_mapped or ip` fails for IPv4: fixed. The code branches on the version, and tests classify both
  families (item 2).
- [MEDIUM] TLS hostname verification not gated: gated now with `trustme`, through positive and mismatched-name
  tests (item 3).
- [MEDIUM] Pool limits dropped: `limits` is no longer accepted; the executor bounds concurrency (D2, item 7).

**agy** (`gemini-4-argon-high`, round 1). Degraded: the receipt is `incomplete` because the final stream drain
exceeded 1 s and cleanup is unverified. No surviving agy process was observed. `review.md` is complete and ends
with `REVIEW COMPLETE`, and its findings were used:
- [HIGH] OmniParser `floor` and `allow_loopback` were not threaded through `_run_omniparser*` and
  `web._extract_web_figures`; also, there are three f-string sites and normalization was missing. Fixed (item 8).
- [MEDIUM] `hosts()` semantics, and `other.com` in `test_ingest.py`. Fixed (item 3, D5).
- [LOW] `close()` delegation: added as D2.7 and tested.
- [LOW] The startup-refusal output prefix: Verification is corrected.

## Final altitude binding (controller metadata, no advisor ran)

Altitude report SHA-256: 54baa26a1776daf85087bd84e0f1a19ac98e5008f6c5a0afc6d31782a01bee43

The final plan is `7f2a54eeb798c4bdd06d5a3fbd52817e378af744bf0c84d5e678089ee508d8f4`, after 4 rounds. All three
fallback reviewers acknowledged this report and gave LGTM in round 4:
- Codex: `codex-shared.md`;
- agy: `agy-round-1794861379-4/review.md`, receipt `complete`;
- the Opus subagent: `-subagent-review.md`.
