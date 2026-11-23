Reviewer: agy gemini-4-hydrogen-medium via agy-review-round.py (headless print mode, one resumed conversation; rounds 1 to 2). Local file:// link targets rewritten to their tree-relative paths and the salutation dropped for the public repository; no other byte changed.

~~~~
## Round 1

Here is my review of [.review/plan.md](.review/plan.md).

Altitude report SHA-256: 8207cbcca8f80ff55c4ffdbe83777137f9dd778b6bd366927df028df95876cfc

**Altitude and duplication:** Disposition `proceed` in [.review/altitude.md](.review/altitude.md) is justified — 0 fenced code blocks, no duplicated validator or product file, and the trust-boundary table plus decision list are the security-first lens's deliverable.

**Completeness:** Documentation, Testing, and Verification sections are all present and map directly to the acceptance criteria and the `AGENTS.md` merge gate.

[Antigravity Review] LGTM — All cited `file:line` references match the codebase (`settings.py`, `utils.py`, `llm.py`, `embed_swap.py`, `embeddings.py`, `db.py`, `indexer.py`, `jobs.py`, `routes/__init__.py`, docs, and tests). The trust boundaries close every leak and redirection path: `_check_llm_key_spec` (`llm.py:47-50`) blocks `file:` specs at both `configure_llm` and `_get_llm_config`; `_resolve_api_key` (`utils.py:396-408`) matches only the exact `file:KNOWLEDGE_BASE_EMBED_API_KEY_FILE` spec; `EmbedSettings`, `ProviderConfig`, `OpenAICompatProvider`, `config.embed_api_key`, and tool outputs (`embed_config`, `cmd_status`) hold or expose only the spec or omit it; `_KEY_RE` (`[!-~]+`) prevents h11/httpx header errors from echoing malformed keys; and `OSError` / latin-1 decoding avoids leaking swapped-variable values or raw bytes. The `functools.cache` on `_read_key_file(path)` satisfies read-once-at-start (`check_environment()` -> `load_embed_settings()`) while keeping `os.environ` lookup at call time.

REVIEW COMPLETE

## Round 2

Here is my Round 2 review of the delta in [.review/plan-delta-r2.diff](.review/plan-delta-r2.diff).

Altitude report SHA-256: 4827ce22fafb76062419ce0044cce29e060accadb1f812af35214e6c14ecff94

**Altitude:** [.review/altitude.md](.review/altitude.md) remains at 0 code fences with no duplicated validator or product file; the growth (268 → 289 lines) is strictly review resolution, and `proceed` remains justified.

[Antigravity Review] LGTM — Every change in the revision 2 delta is accurate against the codebase and introduces no new leak or fail-open path:
- **Decision 2 & Item 2:** Scoping the raw env-key rule to `_OPENAI_FAMILIES` (`settings.py:93`) matches `embeddings.py:507-530`, where only `OpenAICompatProvider` accepts `cfg.api_key`, while `ollama` and `onnx` never send it.
- **Decision 3 & Item 3:** Placing `_key_source(provider)` after the batch-size check (`settings.py:210-215`) preserves existing declaration-error precedence, and changing `embed_key_from_file() -> str` to raise `ValidationError` when `KNOWLEDGE_BASE_EMBED_API_KEY_FILE` is unset fails closed instead of sending an unauthenticated request.
- **Tests & Docs:** The UTF-8 BOM and `ollama` empty-file cases cover the remaining edge cases without echoing secrets; the `rg` stale-claim check and explicit `installation.md:163` / `README.md:227` citations match the files; and the `memory-engine` deviation is surfaced to the owner.

REVIEW COMPLETE
~~~~

## Resolution

- Round 1: LGTM, no findings. Revision 2 changed the plan for the stand-in reviewer's round-1 findings (see
  `2026-11-23-embed-key-file-external-claude.md`).
- Round 2: LGTM on revision 2 (plan SHA-256 f44e73583bdcc497d3a83dc0d0d7dfb5d67eaa836b96b7b59e0cc23292162e8f), the
  final plan bytes; it acknowledges the refreshed altitude report.
- Degradations: none for agy. The gate's Codex voice is degraded (Codex out of quota until about 2026-11-25); a
  Claude Sonnet clean-slate subagent stood in, recorded in its own file.
