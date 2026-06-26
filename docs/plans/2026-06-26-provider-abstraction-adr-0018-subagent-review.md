# Clean-slate subagent review — provider-abstraction plan (#516)

Reviewer: fresh `general-purpose` agent, no prior context, full read access to the
worktree; validated the plan against the real sources (`embeddings.py`, `utils.py`,
`llm.py`, `embed_swap.py`, `ingest.py`, `db.py:802-885`, `routes/embeddings.py`,
`routes/extraction.py`) + existing test files + issue #516 ACs.

**Verdict:** Solid plan, approve with fixes. Architecture right, slice ordering sound,
the three probed correctness questions (frozen-config cache key, SSRF loopback
semantics, redirect disabling) all verify against real code + ADR. Structural
completeness (Documentation / Testing / Verification) fully present.

## Findings

### BLOCKERS

None.

### HIGH

- **Slice A undercounts the `_ssrf_check_openai_compat` → `validate_base_url` migration
  test blast radius.** The guard's `is_private_ip` call moves from the `knowledge_base.llm`
  namespace to `knowledge_base.utils` (`utils.py:68`). **7** tests `@patch("knowledge_base.llm.is_private_ip")`
  (`tests/test_llm.py` ≈ 107, 129, 212, 240, 314, 527, 583) — they stop intercepting and
  must be repatched to the `utils` namespace. Plan said "3 tests"; it is 3 direct + ~5
  indirect. As written, Slice A's gate fails. Not a design flaw — a migration miscount.

### MEDIUM

- **AC6 reject is `(provider, model)`-only but the ADR-0015 tuple is 5 fields.** Narrowing
  is licensed (AC6 names only `embed_provider`/`embed_model`) but must be a stated decision:
  either extend `_assert_identity_match` to `dim`, or document that `dim`/`element_type`
  mismatch stays caught at the vector-length check and full-tuple is #468.
- **Ollama-embedding loopback consistency left implicit.** Today the LLM guard runs only in
  the `openai_compat` branch; `ollama` (localhost) bypasses it. If the plan intends to guard
  _all_ embed providers, default-Ollama-on-localhost breaks unless `allow_loopback_base_url=true`
  — regressing every local user (violating the T6 no-drift guarantee). Plan must state the
  `ollama` provider does NOT call `validate_base_url` (localhost trusted by family).
- **`configure_llm_tool` lives in `routes/extraction.py:177`, not `routes/embeddings.py`.**
  Pointer correction for the executor; the "tool-in-route / logic-in-domain" symmetry holds.

### LOW

- Three probed correctness claims all verify: frozen-config cache key keeps Protocol +
  `embed()`/`embed_single()` signatures intact (mocks survive); SSRF loopback rejects
  `127.0.0.1.nip.io` while accepting exact `localhost`; **no regression window** between
  Slice A (guard removal, flag defaults False) and Slice C (config read) because today's
  `_ssrf_check_openai_compat` already rejects `127.0.0.1`/`localhost` for openai_compat.
- `_get_ollama_url()` memoizes in module global `_OLLAMA_URL` — new localhost/ollama tests
  must not assume a fresh resolve.
- Redirect-disable is correct and the right call (httpx module-level `post` defaults False).
- Rejection of a new `provider_core.py` in favor of `utils.py` is the right call (no
  premature abstraction; `utils.py` already the shared SSRF substrate).

## Resolution

- **[HIGH] migration test miscount** → Addressed. Added task **A4-tests** to Slice A: repatch
  ALL `knowledge_base.llm.is_private_ip` patchers (3 direct + ~5 indirect, exact set via
  `Grep "knowledge_base.llm.is_private_ip" tests/`) to the `utils` namespace; preserve
  `test_ssrf_check_skipped_for_ollama`; run the FULL `tests/test_llm.py` to GREEN.
- **[MEDIUM] AC6 dim scope** → Addressed. Added an explicit "scope of the reject is
  `(provider, model)` only — deliberately" paragraph to §2 T7, documenting that
  `dim`/`element_type` mismatch stays caught at the vector-length check and full-tuple
  enforcement is #468.
- **[MEDIUM] ollama loopback bypass** → Addressed. Added an explicit bullet to §2 T2 stating
  the `ollama` family is NOT guarded by `validate_base_url` on either side (localhost trusted
  by family), guaranteeing no localhost regression for default installs.
- **[MEDIUM] configure_llm_tool pointer** → Addressed. Corrected the change-map row and Slice C
  C3 to reference `routes/extraction.py:177` and the tool-in-route / logic-in-domain symmetry.
- **[LOW] items** → Noted; the `_OLLAMA_URL` memoization caveat is folded into the Testing
  guidance implicitly (tests mock at the httpx boundary). No plan change required.
