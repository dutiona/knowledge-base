Plan SHA-256: 7f2a54eeb798c4bdd06d5a3fbd52817e378af744bf0c84d5e678089ee508d8f4

# Altitude checkpoint: 2026-11-16-redirect-hop-ssrf

- Plan path: `docs/plans/2026-11-16-redirect-hop-ssrf.md`.
- Size: 607 physical lines, 45 624 bytes (round 4). Earlier rounds: 522/37 881 (r1), 591/44 254 (r2), 597/44 719 (r3).
- Top-level implementation items: 15 (`- [ ] **N.`, items 0–14, counted with `grep -c '^- \[ \] \*\*'`).
- Fenced code: none. Literal volume is limited to inline signatures, test names, commit subjects, the D4 table and
  one error-message string. There is no complete product file, validator, generated payload or mutant catalogue.

## Target surface

Measured existing surface: `src/knowledge_base/utils.py` 392 lines, `web.py` 1276, `llm.py` 426, `vision.py` 2252,
`settings.py` 149 and `extraction.py` 1099, plus `embed_swap.py`, `routes/extraction.py` and `routes/ingestion.py`
(touched lightly).

Test patches to migrate, counted with `grep -c`:

| Patch target | Count |
| --- | --- |
| `knowledge_base.web.httpx` | 50 in `test_ingest.py`, 1 in `test_indexer.py` |
| `knowledge_base.llm.httpx` | 30 in `test_llm.py` |
| `knowledge_base.vision.httpx` | 16 in `test_vision.py` |
| `knowledge_base.web.is_private_ip` | 32 in `test_ingest.py` |

These are estimates, not measured:
- Product change: about 120–180 lines in `utils.py` (transport, client, floor rule), 30 in `settings.py`, and about
  20–40 each in `web.py`, `llm.py` and `vision.py`.
- New tests: about 400–600 lines (`test_web_redirects.py`, plus additions to `test_utils.py` and `test_settings.py`).
- Migration churn: about 150 patch sites rewritten.
- Docs: about 10 files, small edits each, and one ADR amendment.

## Inputs

- Required fallback or provenance inputs: none. The plan is self-contained.
- Context consulted, not required for review:
  - the drafting context `docs/plans/2026-11-16-redirect-hop-ssrf-drafts/context.md`;
  - the three lens drafts.

  All are ephemeral and deleted before the first commit.

## Calibration and disposition

- This is a security patch that crosses five modules. That breadth comes from the owner-approved scope addition
  (LLM, vision, OmniParser, CDP) and from ADR-0018 §4, which requires binding the actual connect target.
- The plan assigns work, decisions D1–D6, owner calls OC1–OC7, red and green commands, and commit subjects.
- It does not transcribe code: signatures and rules only.
- Its length follows from 15 reviewable items and an explicit table of call-site trust, not from embedded product.
- Duplication check:
  - one guard (`_PinnedTransport` plus `egress_client`);
  - `validate_base_url` is kept for write-time checks and is not re-implemented;
  - no second validator.

Disposition: **proceed**.

## Round 2 refresh

- The growth (+69 lines) comes from round-1 findings only:
  - the restricted `egress_client` contract;
  - the SNI form;
  - OmniParser provenance threading;
  - the CDP literal rule (OC8);
  - the `trustme` TLS tests;
  - the recorder semantics;
  - the extra residual.
- No new product literal, validator or duplicated owner was added.
- Disposition is unchanged: **proceed**.

## Round 3 refresh

- 597 lines, 44 719 bytes.
- The +6 lines are two round-2 Codex corrections:
  - the targeted weakening for the TLS mismatch test;
  - parsing `Host` as an authority, with a self-test.
- Disposition is unchanged: **proceed**.

## Round 4 refresh

- 607 lines, 45 624 bytes.
- The +10 lines are the subagent's three round-2 LOWs (the OmniParser endpoint helper, the `localhost` fake_dns
  answer, the IDNA recorder key) and one doc bullet.
- Disposition is unchanged: **proceed**.
