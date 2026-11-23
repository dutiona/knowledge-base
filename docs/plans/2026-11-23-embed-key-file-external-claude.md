Reviewer: Claude Sonnet (clean-slate subagent, medium effort) via the Agent tool, standing in for Codex (degraded: quota out until about 2026-11-25); rounds 1 to 2, one resumed context

~~~~
## Round 1

Altitude report SHA-256: 8207cbcca8f80ff55c4ffdbe83777137f9dd778b6bd366927df028df95876cfc

I read the plan and altitude report and checked the code facts against the snapshot. No blockers.

## Verdict
The plan is sound and the structure is complete. Documentation, Testing and Verification are all present, with a criterion-to-test table and a no-network smoke test. I found one over-reach in Decision 2, one doc-coverage gap, and a few small items.

## Altitude and duplication
The altitude report's proceed disposition holds. The plan's length comes from the trust-boundary table and the decision list, and each row names a sink and its guard. The code change is about 50 lines across `settings.py`, `utils.py` and `llm.py`. No second mechanism, no new module, no copied validator. I accept the single shared `_KEY_RE` and the exact-string resolver branch. Decision 5's claim also holds: `_check_llm_key_spec` guards every route to `_resolve_api_key` in `llm.py` (`_get_llm_config` at `llm.py:62`, `configure_llm` at `llm.py:362-366`, then `_probe` and `_llm_call`).

## Findings

**[MEDIUM] Decision 2 / item 2 breaks working deployments for providers that never send a key.**
- The plan says an env key that fails the rule "already fails every request, so refusing it at start breaks no working deployment."
- `_build_provider` (`embeddings.py:505-530`) shows this is false for `ollama` and `onnx`. They never receive `cfg.api_key`, so a stray trailing space or CRLF in `KNOWLEDGE_BASE_EMBED_API_KEY` works today and would now stop the start with exit 78. Decision 8 says as much for those providers, which contradicts Decision 2.
- Fix: apply the env-key rule only when the provider sends the key (`openai_compat`, `openai`, `llama.cpp`, i.e. the `OpenAICompatProvider` families). Alternatively, rewrite the justification and list the ollama/onnx case under "Open for the owner" next to the inner-space case. The first option is better. It costs one condition and removes the false claim.
- Add a test for that case: an ollama declaration with a padded env key still starts.

**[MEDIUM] Documentation misses a place where the `env:` spec is written as the only source.**
- Beyond the five listed files, `README.md:227` mentions the key. `README.md:95` is an MCP `env` example, which is fine. The ADR §4 bullet is covered by Decision 10.
- Stale wording remains in `docs/getting-started/installation.md:163`: "the key `KNOWLEDGE_BASE_EMBED_API_KEY` through `--env-file` or a secret". The plan rewrites lines 161-164, so confirm the new text covers this sentence.
- The `embed_swap.py:36-39` docstring says "only ever the declared `env:` source". `embeddings.py:135-138` and `:319-320` say "inline or `env:VARNAME`". The plan lists these as docstring edits, which is correct.
- Add a check to item 4's green step: `rg "only ever the declared .env:" src` and `rg "read only from" docs` return nothing stale. The `rg -c` count only proves the new name appears, not that old claims are gone.

**[LOW] The `_key_source` placement and error priority are unspecified.**
- `load_embed_settings` raises "the first failing rule", in a defined order (provider, model, dim, url, and so on). The plan doesn't say where `_key_source()` runs.
- If it is evaluated inline in the `EmbedSettings(...)` call at `settings.py:221`, a key-file error preempts nothing, but a bad provider is reported first. That is the right order. State it, and say whether the server smoke test's "swapped variable" case depends on it.
- A `KNOWLEDGE_BASE_EMBED_API_KEY_FILE` under `onnx` also gets read and can refuse the start (Decision 8). That is consistent, but add one test line for it, or drop the "read and recorded" wording for onnx and ollama.

**[LOW] The `utils._resolve_api_key` coupling is slightly asymmetric.**
- The generic resolver now knows the embedder's variable. `embed_key_from_file()` returns `None` if `API_KEY_FILE_VAR` is unset at resolve time. That can't happen in a process, but a test that clears the variable after binding would silently send no `Authorization` header instead of failing.
- Cheap hardening: have the spec branch raise `ValidationError` when it resolves to `None`. The code stays tiny. Otherwise record it as accepted.

**[LOW] BOM and encoding.**
- A UTF-8 BOM or UTF-16 file decodes to non-visible latin-1 and is refused with the generic message. That is correct.
- The message text "one line of visible ASCII characters" is enough. Optionally add a BOM case to the `test_a_bad_key_file_refuses_naming_the_variable` parameters.

**[LOW] Test and fixture details I verified.**
- `conftest.py:102-116`, `test_settings.py:32` (`_EMBED_VARS`) and `test_llm.py:788`/`:810` match.
- The `_seeded(..., api_key_file=...)` helper builds `KNOWLEDGE_BASE_EMBED_API_KEY_FILE` from `api_key_file` and supports `None`.
- The server-test model at `test_server.py:146` matches. It asserts exit 78, no lock file and no database file, which is consistent with `check_environment` running before `hold` (`server.py:220`).
- `ValidationError` subclasses `KnowledgeBaseError`, so the server prints `str(exc)` and exits 78. The indexer prints `str(exc)` and exits 1.
- Neither path reaches a traceback, so `from None` plus a static message is sufficient. No key text reaches `str(exc)`.

## Security assessment
- **Key to log, error, traceback or repr:** I found no path.
  - The spec carries the variable name only. `ProviderConfig.__repr__` omits the key. `OpenAICompatProvider.__repr__` shows a sanitized URL only. `EmbedSettings` holds the spec only.
  - Messages are static. `OSError` is reduced to its class name with `from None`. Bytes are decoded as latin-1, so no `UnicodeDecodeError` can echo a byte.
  - The swapped-variable case is handled.
- **Key to config row or MCP result:** the row records `file:KNOWLEDGE_BASE_EMBED_API_KEY_FILE` only. `bind_embed_config` rewrites a foreign row (`db.py:1452-1455`).
- **MCP client redirects the key:** the LLM guard covers the new route. Decision 5's whole-prefix refusal is correct, and the stored-row and `configure_llm` paths are both tested. The embedder provider takes the process declaration, not rows. Redirects are off in the embedder client (`follow_redirects=False`).
- **Residuals:** the child-process path exposure and in-memory key are accepted and correctly stated. `functools.cache` keeps the key in the process for its life, the same as `os.environ`.
- **Sibling leak:** the LLM-key h11 echo in "Open for the owner" is real, since `llm.py:246-248` and `jobs.py:215-222` render httpx text. A separate issue is right. Deferring it is not silent, because the plan surfaces it for the owner.

REVIEW COMPLETE

## Round 2

Altitude report SHA-256: 4827ce22fafb76062419ce0044cce29e060accadb1f812af35214e6c14ecff94

LGTM on the delta. All earlier findings are resolved and I found nothing new above LOW.

**Round-1 findings**
- **[MEDIUM] Padded env key under ollama/onnx: holds.**
  - `_OPENAI_FAMILIES` at `settings.py:93` is exactly `("openai_compat", "openai", "llama.cpp")`. That matches `_build_provider`, where only those families reach `OpenAICompatProvider` with the key.
  - The new test `test_a_padded_env_key_still_starts_a_provider_that_never_sends_it` pins the scope. The "7 passed" count is consistent: 6 parametrized cases plus 1.
  - Decision 8 now says a file under ollama/onnx is read and checked by Decision 1, which is consistent with the new "ollama + empty file" case.
- **[MEDIUM] Stale "only key source" wording: holds.**
  - The new `rg` check must print nothing. It targets the three actual phrases I saw: "only ever the declared `env:`" in the `embed_swap.py` docstring, "read only from" in ADR-0018, and "Always `env:KNOWLEDGE_BASE_EMBED_API_KEY`" in `schema.md:269`. Item 4 also rewrites `installation.md:163` explicitly.
  - The `embeddings.py` docstrings ("inline or `env:VARNAME`") are not in the `rg` pattern, but item 3 already edits them.
- **[LOW] Rule order: holds.** The key source is now the last rule, after batch size. The both-set refusal therefore comes only once the rest of the declaration is valid. The server both-set test uses a valid declaration, so it is unaffected.
- **[LOW] File spec resolving to None: holds.** `embed_key_from_file() -> str` now raises `ValidationError` when the variable is unset, and a named test covers it. No new leak path, since the message is static.
- **[LOW] BOM: holds.** The BOM and ollama + empty-file cases were added to the bad-file parametrization.

**New from the delta**
- **[LOW] `_key_source(provider)` runs last, so an invalid provider never reaches it.** That is fine. In the `ollama`/`onnx` bad-file test, `check_environment()` must still reach `_key_source`, which it does because the declaration is otherwise valid. Ensure that test's `onnx` or `ollama` declaration is complete (`ONNX_EMBED_MODEL_PATH` must name a real file for onnx), otherwise it fails earlier for an unrelated reason. The plan names ollama only, which avoids this.
- **[LOW] The owner note on Decision 1's deviation from memory-engine is accurate.** The trimmed trailing space or CRLF case is the behavioral difference, and it is stated correctly.

REVIEW COMPLETE
~~~~

## Resolution

Round 1 (revision 1, plan SHA-256 3a9b8245…):
- [MEDIUM] Decision 2 refuses a padded env key under ollama/onnx, which never send it -> accepted. Decision 2 and
  item 2 now apply the env-key rule only to `openai_compat`, `openai` and `llama.cpp`; new green-on-arrival test
  `test_a_padded_env_key_still_starts_a_provider_that_never_sends_it`.
- [MEDIUM] Stale "the env var is the only key source" wording could survive -> accepted. Item 4's green step adds an
  `rg` check that must print nothing; the Documentation section names `installation.md:163`'s sentence. `README.md:227`
  checked: it stays true and is left alone.
- [LOW] `_key_source` placement and error priority unspecified -> accepted. Decision 3: the key source is the last
  rule, after the batch size; item 2 says where `load_embed_settings` computes it.
- [LOW] The file spec resolving to None sends no Authorization header -> accepted. `embed_key_from_file() -> str`
  raises `ValidationError` when the variable is unset (fails closed), with a named test.
- [LOW] BOM and encoding -> accepted: a UTF-8 BOM case and an ollama + empty-file case (decision 8) join the bad-file
  parameters.
- [LOW] Test and fixture details verified -> no change needed.
- The owner note on decision 1's deviation from memory-engine was added in the same revision.

Round 2 (revision 2, plan SHA-256 f44e7358…, the final bytes): LGTM.
- [LOW] the onnx/ollama bad-file test needs a complete declaration -> no change: the plan names ollama only, which
  needs no model file, as the reviewer notes.
- [LOW] the memory-engine deviation note is accurate -> no change.

Degradations: Codex (the gate's named voice) did not run: quota out until about 2026-11-25; this Sonnet subagent
stood in per the owner's 2026-11-22 roster.
