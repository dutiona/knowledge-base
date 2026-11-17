# Advisor capability unavailable

- Reason: `advisor()` is not exposed in this worker's harness.
- Gate: Full, advisor-less form. Reviewers that ran on the successor: Codex (`gpt-6.1-terra`, headless, the
  predecessor's thread resumed on a fresh a518ab9 snapshot) and the clean-slate Opus subagent (resumed). agy is
  quota-blocked (RESOURCE_EXHAUSTED 429 observed 2026-11-17T16:23Z) and was not dispatched; no substitute model.
- No finding here is attributed to an advisor. Findings and resolutions: `2026-11-17-kb-592-content-redaction-v2-external-codex.md`
  and `2026-11-17-kb-592-content-redaction-v2-subagent-review.md`.

## Resolution

Altitude report SHA-256: d4e46e9aedfe4d15ec8cf8ca82d6bba76e9f4427e675b6b17909a0d1da694711

- Codex: round 1 1 HIGH (URL-valued metadata), round 2 2 HIGH (shared metadata rule, scheme case), round 3 1 MEDIUM
  (test split), rounds 4 and 5 LGTM. All addressed in the successor.
- Subagent: round 1 1 MEDIUM, 4 LOW; round 2 1 HIGH, 1 LOW; round 3 2 LOW; round 4 1 LOW; round 5 LGTM. All addressed.
