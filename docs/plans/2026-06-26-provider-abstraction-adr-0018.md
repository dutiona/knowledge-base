# Implementation Plan — Provider abstraction (ADR-0018, issue #516)

**Status:** awaiting approval
**Issue:** [#516](https://github.com/dutiona/knowledge-base/issues/516) — `feat(embeddings): provider abstraction (ADR-0018) — openai_compat/anthropic_compat/ollama/onnx`
**Spec:** `docs/design/adr/0018-provider-abstraction.md` (Accepted 2026-06-26 — referenced, **not** restated/amended here)
**Worktree:** `/home/mroynard/dev/knowledge-base/.worktrees/feat-516-provider-abstraction` (branch `feat/516-provider-abstraction`, off `master`, exists)
**Engagement / severity:** Thorough / Default (address all findings)

---

## 0. BLUF

Unify the two vendor-anchored half-systems (env-driven embeddings, config-driven LLM)
into **one config-driven provider model organized by API family** —
`openai_compat` / `anthropic_compat` / `ollama` / `onnx` — spanning both embeddings and
chat, behind **one** SSRF guard (`utils.validate_base_url`), with the provider feeding
the embedding-identity tuple.

**Six TDD slices, risk-retirement ordered** (security floor proven by tests before any
feature code rides on it), each an atomic commit, tree green after every slice:

| Slice | Retires / delivers                                                                                                                 | ACs                     |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------- | ----------------------- |
| **A** | One SSRF guard: loopback opt-in + `localhost` label + disabled redirects; **delete** `_ssrf_check_openai_compat`                   | AC4, AC5, owner-comment |
| **B** | `OpenAICompatProvider(base_url, api_key)` + frozen-config cache key (kills the name-collision bug) + `OpenAIProvider` alias        | AC1                     |
| **C** | `configure_embeddings()` mirroring `configure_llm()` + env→config migration with back-compat + `env:VAR` indirection + log hygiene | AC3, AC6-secrets        |
| **D** | Narrow producer-side identity hard-reject at the write boundary                                                                    | AC6-identity            |
| **E** | `anthropic_compat` chat branch behind `_llm_call`                                                                                  | AC2                     |
| **F** | Final audit: dup-guard zero refs, secrets-in-logs grep, ratio check                                                                | AC4-final               |

### Synthesis rationale (three-lens panel → this plan)

- **From the risk-first lens:** the slice _ordering_ (SSRF → embedding family/cache →
  migration → identity → chat → audit) so the externally-exploitable security surface is
  test-proven first, and the explicit **required** security-test matrix (rebind, loopback
  literal vs resolved, exact-`localhost` vs look-alikes, metadata floor, redirect).
- **From the mvp-first lens:** minimal mechanisms — frozen `ProviderConfig` threaded
  through the existing private dispatch channel (signatures frozen), `follow_redirects=False`
  one-liner, `anthropic_compat` as a third `if`-arm (no adapter hierarchy), `OpenAIProvider`
  kept as a thin subclass so the legacy `TestOpenAIProvider` suite survives unmodified.
- **From the architecture-first lens:** the frozen value object **as the cache key** (the
  precise fix for the collision bug) with a **redacting `__repr__`**, and the explicit
  `web.py`/`vision.py` non-regression fences — **but its new `provider_core.py` module is
  rejected**: shared helpers consolidate into the existing `utils.py` substrate, matching
  ADR-0018 §2's in-place-generalization framing and the thin-hub architecture, for a
  smaller blast radius and no new import edge.
- **User decision (feature altitude):** AC6 hard-reject = **narrow producer-side
  assertion** (configured `(provider, model)` == active space's recorded tuple, raise on
  mismatch). #516 does **not** build the full cross-layer mismatch engine (#468) or write
  the ADR-0015 doc (#469) — it _gates_ them.

---

## 1. Verified ground truth (do not re-derive)

- `validate_base_url` is **regression-safe to extend**: `web.py` calls `is_private_ip`
  _directly_ (`web.py:214, 1080, 1095`); `vision.py` calls _neither_ `validate_base_url`
  nor `is_private_ip`. A new `allow_loopback=False` kwarg cannot regress any existing caller.
- **ADR-0015 does not exist in the repo** (only `0017`, `0018` under `docs/design/adr/`).
  It is future work: **#469** writes the ADR doc, **#468** implements mismatch detection;
  **#516 gates both**. No active-space identity assertion exists today (`embed_swap` reads
  `space["provider"]`/`space["model"]` but never compares-and-rejects). ⇒ AC6 = _add_ the
  narrow producer-side reject (per user decision); there is no prior rule to "keep unchanged"
  beyond ADR-0018 §5's stated field-equality semantics, which we honor.
- `embeddings.py`: `EmbeddingProvider` Protocol; `_PROVIDERS={ollama,openai,onnx}`;
  `OpenAIProvider._call_api` hardcodes `https://api.openai.com/v1/embeddings`;
  `get_provider(name)` reads `EMBED_PROVIDER` env + **name-keyed `_provider_cache`**
  (the collision bug); providers constructed argless `cls()`.
- `llm.py`: `_get_llm_config` reads `llm_provider/model/base_url/api_key`;
  `provider∈{ollama,openai_compat}`; `_ssrf_check_openai_compat` (near-dup) at `llm.py:131`;
  `_sanitize_url`, `_test_llm_connectivity`, `configure_llm` (validate+probe+redact).
- Embed config read path: `embed_swap.get_embed_config` (lines 24-40) reads **only**
  `embed_model/embed_dim/embed_provider` — no `base_url`/`api_key`. Consumers:
  `ingest._embed_with_config`, `search` (2×), `folder_summaries` via `embed(_provider_name=…)`;
  `embed_swap.{backfill_space,promote_space,_re_embed_folder_summaries}` via
  `get_provider(name, allow_env_override=False).embed(...)`. `db._seed_default_config`
  (~855) seeds `embed_*`; `db._bootstrap_embed_spaces` (~802) writes `(name,model,provider,dim,…)`.

---

## 2. Design decisions — the contract (7 tensions resolved)

### T1 — thread `base_url`/`api_key` without touching `embed()` / the Protocol

- `EmbeddingProvider.embed(texts, model, expected_dim)` and module-level
  `embed()`/`embed_single()` stay **byte-identical** (mocks `@patch(...embed)` survive).
- New **frozen** `ProviderConfig` (`@dataclass(frozen=True, slots=True)`) in `embeddings.py`,
  fields `family: str`, `base_url: str | None`, `api_key: str | None` (the **raw** config
  spec — inline value or `env:VAR` indirection string, **never** a resolved secret), with a
  `__repr__` that routes `base_url` through `_sanitize_url` and omits `api_key`.
- `get_provider(name, *, cfg: ProviderConfig | None = None, allow_env_override=True)`
  caches on the **frozen `ProviderConfig`** (or a sentinel for the bare/legacy path), not
  the family name — **the concrete fix for two `openai_compat` configs colliding**.
- `embed()`/`embed_single()` gain one **private keyword-only** `_provider_cfg: ProviderConfig | None = None`
  (purely additive; default `None` → today's Ollama default). Callers
  (`ingest._embed_with_config`, `embed_swap.*`) build a `ProviderConfig` from active space +
  config and pass it through.

### T2 — loopback opt-in, parsed-host-literal-only, pre-DNS

- `utils.validate_base_url(url, *, allow_loopback: bool = False)`. New helper
  `_is_allowed_loopback(host)` returns True iff `host == "localhost"` (exact, case-insensitive,
  RFC-6761) **or** `ipaddress.ip_address(host).is_loopback` (covers `127.0.0.0/8`, `::1`).
- Order: scheme/hostname checks → if `allow_loopback and _is_allowed_loopback(host): return`
  (**before** any DNS) → else `is_private_ip(host)` raises. So `127.0.0.1.nip.io` (not a
  literal, not the label) falls through to `is_private_ip`, which resolves it to `127.0.0.1`
  and **rejects** — the rebind floor is preserved. `is_private_ip` is **untouched**.
- **Param, not global.** Default `False` ⇒ `web.py`/`vision.py` strict path unchanged. Only
  the embed/LLM call-time guard + connectivity probe + config-write pass `allow_loopback=`
  the value of the `allow_loopback_base_url` config flag.
- **`ollama` family is NOT guarded by `validate_base_url`** — on either the embed or chat
  side. Today the LLM guard runs only in the `openai_compat` branch (`llm.py:193-194`); the
  `ollama` branch bypasses it because `localhost`/the WSL2 gateway is trusted by family
  (`_get_ollama_url`). The new `OllamaProvider.embed` likewise does **not** call
  `validate_base_url`. This is the explicit no-regression guarantee: a default local install
  (Ollama on `localhost:11434`) keeps working **without** setting `allow_loopback_base_url`.
  The guard binds only the `openai_compat`/`anthropic_compat` user-supplied `base_url`.

### T3 — redirects

- **Disable cross-host redirect following**: explicit `follow_redirects=False` on every
  provider `httpx.post` (embed + all three chat families). httpx's module-level `post`
  defaults to `False`; we set it explicitly so a future switch to a `Client` cannot silently
  re-enable it. No second hop ⇒ no DNS-rebind-on-redirect; keeps the per-hop `#232`/web.py
  story out of scope. Documented with a "do not harmonize with `ingest_url`" comment.

### T4 — `anthropic_compat` (chat-only)

- Third arm in `_llm_call`, dispatched on `cfg["provider"]`: POST `{base_url}/v1/messages`;
  headers `x-api-key: <key>` + `anthropic-version` (pinned constant `_ANTHROPIC_VERSION = "2023-06-01"`);
  body `{"model", "max_tokens": _ANTHROPIC_MAX_TOKENS (8192), "system": _SYSTEM_JSON_DIRECTIVE,
"messages": [{"role":"user","content":prompt}]}` (system **top-level**); parse
  `resp.json()["content"]` → concat `block["text"]` for `block.get("type")=="text"`; then
  `_strip_think_tags`. `configure_llm` provider set widens to
  `{ollama, openai_compat, anthropic_compat}`; `_get_llm_config` `/v1`-strip applies to both
  HTTP families. **Embedding** config naming `anthropic_compat` → `ValidationError` (it has no
  embeddings endpoint); absent from the embeddings `_PROVIDERS` registry.

### T5 — validate the post-normalization URL

- Normalize first (`.rstrip("/").removesuffix("/v1")`) in `OpenAICompatProvider.__init__`,
  `_get_llm_config`, and both `configure_*`; validate the **normalized** value; persist the
  **normalized** value. The string validated == stored == used. `httpx` only, **no vendor SDK**.

### T6 — config migration + secrets

- New config keys: `embed_base_url`, `embed_api_key` (+ existing `embed_provider`,
  `embed_model`), and a shared `allow_loopback_base_url` flag read by both guards. K/V table
  ⇒ **no DDL migration**; absence is meaningful (fail-closed / auto-detect).
- `get_embed_config` back-compat: if `embed_base_url`/`embed_api_key` config keys absent,
  fall back to `EMBED_PROVIDER`/`OPENAI_API_KEY` env (today's behavior) + a **one-time**
  deprecation `logger.warning`. **Config wins over env.** Default-when-both-absent stays
  `ollama` (the no-drift guarantee for existing local users).
- `env:VARNAME` indirection: stored **verbatim**, resolved at **call time** via shared
  `utils._resolve_api_key(raw)` (`raw.startswith("env:")` → `os.environ.get(raw[4:])`, else
  `raw`); the resolved secret is **never** persisted.
- Never log key/userinfo: move `_sanitize_url` to `utils.py` (re-export from `llm.py` for
  back-compat) and route every logged URL through it; key never enters `repr`/error/return
  (the `configure_*` pop it before returning).

### T7 — identity coupling (per user decision: narrow producer-side reject)

- `embed_provider`/`embed_model` populate the `embed_spaces` identity tuple (columns
  `provider`,`model` already exist and are written by `create_space`/`_bootstrap_embed_spaces`).
- New `_assert_identity_match(cfg_provider, cfg_model, space_provider, space_model)` raises
  `ValidationError` on mismatch, called once per **write** path (`ingest._embed_with_config`,
  `embed_swap.backfill_space`). Provider swap for the same model slug is a legitimate tuple
  change handled by the space lifecycle (not loosened). **No** whitening/operator code is
  touched; provider-only swap does **not** invalidate any metric (KB ships zero whitening —
  satisfied by inaction + an explanatory comment, ADR-0017). Full cross-layer enforcement is
  #468; the ADR-0015 doc is #469 — **both out of scope** here.
- **Scope of the reject is `(provider, model)` only** — deliberately, not the full ADR-0015
  5-tuple `(model, provider, dim, matryoshka_base_dim, element_type)`. The issue's AC6 names
  only `embed_provider`/`embed_model`, so this is the licensed scope; the new fields this PR
  introduces are exactly `provider`(family) and `model`(slug). A `dim`/`element_type` mismatch
  is still caught — at the existing per-provider vector-length check (`Expected N dims, got M`,
  `embeddings.py`) — just at a later, deeper point than the producer seam. Promoting `dim`/
  `matryoshka_base_dim`/`element_type` into a producer-seam tuple equality is **#468's job**
  (full-tuple enforcement), explicitly deferred. Stated here so the narrowing is a decision,
  not an omission.

### Structural decision (no user-facing consequence — decided, not asked)

Shared logic lands in **`utils.py`** (the existing substrate beneath `web.py`/`vision.py`/`llm.py`):
`validate_base_url` (extended), `_is_allowed_loopback`, `_sanitize_url` (moved), `_resolve_api_key`
(new). **No** new `provider_core.py` module — ADR-0018 §2 frames the work as in-place class
generalization + an `llm.py` branch, and `utils.py` already kills the two-guards drift hazard.
`ProviderConfig` lives in `embeddings.py` (its only consumer-of-record for the cache key);
`configure_embeddings` lives in `embed_swap.py` (its domain, alongside `get_embed_config`).

---

## 3. File-by-file change map

| File                                      | Change                                                                                                                                                                                                                                                         | Slice   |
| ----------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------- |
| `src/knowledge_base/utils.py`             | `validate_base_url(url, *, allow_loopback=False)` + `_is_allowed_loopback`; host `_sanitize_url` (moved) + `_resolve_api_key` (new); update `__all__`                                                                                                          | A, C    |
| `src/knowledge_base/llm.py`               | delete `_ssrf_check_openai_compat`, route 2 call sites → `validate_base_url(..., allow_loopback=)`; `follow_redirects=False`; `anthropic_compat` arm + constants; widen `configure_llm`/`_get_llm_config`; re-export `_sanitize_url`; resolve key at call time | A, E    |
| `src/knowledge_base/embeddings.py`        | `ProviderConfig`; `OpenAICompatProvider`; `OpenAIProvider`→thin subclass; `_PROVIDERS` vocabulary (`openai_compat`, `openai` alias); `get_provider(cfg=)` frozen-config cache; `embed(_provider_cfg=)`; `follow_redirects=False`                               | B       |
| `src/knowledge_base/embed_swap.py`        | `get_embed_config` reads `embed_base_url`/`embed_api_key` + env back-compat + builds `ProviderConfig`; `configure_embeddings()` + `_test_embedding_connectivity`; `backfill_space` passes `cfg=` + identity reject                                             | B, C, D |
| `src/knowledge_base/ingest.py`            | `_embed_with_config` builds+threads `ProviderConfig` + identity reject                                                                                                                                                                                         | B, D    |
| `src/knowledge_base/routes/embeddings.py` | `configure_embeddings_tool` MCP tool (mirror `configure_llm_tool` @ `routes/extraction.py:177`)                                                                                                                                                                | C       |
| `docs/usage/ingesting-documents.md`       | embedding providers, loopback flag, `env:` indirection                                                                                                                                                                                                         | docs    |
| `docs/usage/structured-extraction.md`     | `anthropic_compat` LLM family                                                                                                                                                                                                                                  | docs    |
| `docs/reference/schema.md`                | new config keys; reaffirm `embed_spaces.provider/.model` as identity-tuple fields                                                                                                                                                                              | docs    |
| `CLAUDE.md`                               | embeddings.py row note: `openai_compat` (not `openai`)                                                                                                                                                                                                         | docs    |

No change to `db.py` DDL (K/V config). No new dependency (httpx-only).

---

## 4. Slice A — one SSRF guard (AC4, AC5, owner-comment)

- [ ] **A1 (test-first)** `tests/test_utils.py` SSRF matrix (each a test fn), run → RED:
  - rejects loopback by default: `validate_base_url("http://127.0.0.1:11434")` raises.
  - accepts loopback IP literal on opt-in: `127.0.0.1:11434` and `[::1]:11434` pass with `allow_loopback=True`; `127.5.6.7` too (whole `/8`).
  - accepts exact `localhost` on opt-in: `http://localhost:11434/v1` passes with the flag.
  - **rejects rebind even on opt-in**: `http://127.0.0.1.nip.io` raises with `allow_loopback=True` (mock `socket.getaddrinfo`→`127.0.0.1`, offline).
  - rejects `localhost` look-alikes on opt-in: `localhost.attacker.com`, `notlocalhost`, `sub.localhost` raise.
  - **metadata/private floor always**: `169.254.169.254`, `10.0.0.1`, `192.168.1.41`, `[fd00::1]` raise even with `allow_loopback=True`.
  - scheme/hostname unchanged: `ftp://…`, `file://…`, `http://` (no host) raise regardless of flag.
- [ ] **A2** implement `_is_allowed_loopback` + `allow_loopback` in `utils.validate_base_url`
      (pre-DNS literal/`localhost` check; `is_private_ip` untouched). Move `_sanitize_url` →
      `utils.py`, re-export from `llm.py`. Add `_resolve_api_key`. Run A1 → GREEN.
- [ ] **A3 (test-first)** `tests/test_llm.py::test_llm_call_disables_redirects` — chat POST
      called with `follow_redirects=False` (capture kwargs). RED.
- [ ] **A4** delete `_ssrf_check_openai_compat`; replace its 2 call sites (`_llm_call`,
      `_test_llm_connectivity`) with `validate_base_url(base_url, allow_loopback=<flag>)`
      (flag threaded as a param defaulting `False`; config read lands in Slice C). Add
      `follow_redirects=False` to all `llm.py` provider POSTs.
- [ ] **A4-tests (test-migration — DO NOT undercount).** The guard's `is_private_ip` call
      moves from the `knowledge_base.llm` namespace into `knowledge_base.utils`
      (`validate_base_url` calls `utils.is_private_ip` — `utils.py:68`). Therefore **every**
      `@patch("knowledge_base.llm.is_private_ip", …)` stops intercepting and must be repatched
      to `@patch("knowledge_base.utils.is_private_ip", …)`. This is **not 3 tests — it is the
      3 direct `_ssrf_check_openai_compat` unit tests PLUS ~5 indirect `llm.is_private_ip`
      patchers** (`tests/test_llm.py` ≈ lines 107, 129, 212, 240, 314, 527, 583 — verify the
      exact set with `Grep "knowledge_base.llm.is_private_ip" tests/`). Convert the 3 direct
      tests to `validate_base_url` assertions; repatch the indirect set to the `utils`
      namespace. **Also preserve `test_ssrf_check_skipped_for_ollama` semantics** (the `ollama`
      branch must still bypass the guard). Run the FULL `tests/test_llm.py` → GREEN (not just
      the migrated subset — the indirect patchers are the ones that silently break).
- [ ] **A5** `ruff check`/`ruff format --check`/`basedpyright` clean for touched files.
- [ ] **A6** commit: `feat(security): unify SSRF guard with scoped loopback opt-in + disable cross-host redirects (ADR-0018 §4)`.

## 5. Slice B — OpenAICompatProvider + frozen-config cache (AC1)

- [ ] **B1 (test-first)** `tests/test_embeddings.py`, run → RED:
  - `OpenAICompatProvider(base_url="http://vllm.example.com", api_key="k")` posts to
    `http://vllm.example.com/v1/embeddings`, `Authorization: Bearer k`, `follow_redirects=False`,
    `dimensions=expected_dim`, result L2-normalized (mock `httpx.post`).
  - `/v1`-suffix strip: `base_url="http://host/v1"` → posts `http://host/v1/embeddings` (no `/v1/v1`).
  - OpenAI-literal alias: `OpenAIProvider()` posts to `https://api.openai.com/v1/embeddings`;
    `test_missing_api_key_raises` still holds (thin subclass reads `OPENAI_API_KEY`).
  - **cache no-collision**: `get_provider("openai_compat", cfg=A)` and `…cfg=B)` with
    different `base_url` return **different** instances; same `cfg` → same instance.
  - call-time guard: a private `base_url` without the loopback flag raises at `embed()`.
  - keep all existing `TestOllamaProvider`/`TestOpenAIProvider` green via the alias.
- [ ] **B2** implement `ProviderConfig` (frozen, redacting `__repr__`), `OpenAICompatProvider`
      (normalize-first, call-time `validate_base_url`, `follow_redirects=False`, resolve
      `api_key` via `_resolve_api_key` at request time), `OpenAIProvider` thin subclass,
      `_PROVIDERS` vocabulary (`openai_compat`; `openai`→alias), `get_provider(cfg=)` frozen-config
      cache (keep a `_clear_provider_cache`-style reset for tests). GREEN.
- [ ] **B3** thread `ProviderConfig` through `ingest._embed_with_config` and
      `embed_swap.{backfill_space,_re_embed_folder_summaries}`; add `_provider_cfg` to
      `embed()`/`embed_single()`. Existing ingest/embed_swap tests stay green (default `None`
      → ollama path unchanged).
- [ ] **B4** gates clean; commit: `feat(embeddings): OpenAICompatProvider(base_url) + frozen-config provider cache (ADR-0018 §2, AC1)`.

## 6. Slice C — configure_embeddings + migration + secrets (AC3, AC6-secrets)

- [ ] **C1 (test-first)** `tests/test_embed_swap.py`, run → RED:
  - `configure_embeddings` mirrors `configure_llm`: validates, probes (mocked), **redacts
    `api_key`** from the returned dict, persists `embed_provider`/`embed_base_url`/`embed_model`,
    stores the **normalized** URL.
  - rejects `anthropic_compat` for embeddings (`ValidationError`).
  - loopback requires the flag: `base_url="http://localhost:11434"` without
    `allow_loopback_base_url=True` → blocked/unreachable; with it → accepted.
  - env back-compat: no `embed_base_url` key + `EMBED_PROVIDER`/`OPENAI_API_KEY` set →
    config reflects env + one-time deprecation warning (`caplog`); config-present wins over env.
  - `env:VAR` not persisted: `api_key="env:MY_KEY"` stored verbatim (`SELECT` == `env:MY_KEY`);
    `_resolve_api_key` yields the secret only at call time; DB never contains the secret.
  - no key/userinfo in logs (`caplog`); `_sanitize_url` reused.
- [ ] **C2** implement `configure_embeddings` + `_test_embedding_connectivity` (advisory,
      never raises, mirrors `_test_llm_connectivity` incl. SSRF-caught-as-unreachable) in
      `embed_swap.py`; extend `get_embed_config` with config-key reads + env back-compat +
      deprecation warning; thread `allow_loopback_base_url` into the embed guard. GREEN.
- [ ] **C3** add `configure_embeddings_tool` in `routes/embeddings.py` (mirror the pattern of
      `configure_llm_tool`, which lives in `routes/extraction.py:177` — tool-in-route /
      logic-in-domain-module; the domain fn `configure_embeddings` is in `embed_swap.py`,
      as `configure_llm` is in `llm.py`); smoke-test it returns JSON.
- [ ] **C4** gates clean; commit: `feat(embeddings): configure_embeddings() + env→config migration with back-compat & env: indirection (AC3)`.

## 7. Slice D — producer-side identity hard-reject (AC6-identity)

- [ ] **D1 (test-first)** `tests/test_embed_swap.py` + `tests/test_ingest.py`, run → RED:
  - active space `(provider="ollama", model="bge-m3")`, config `provider="openai_compat"`
    → `backfill_space` raises `ValidationError`, **no vectors written**.
  - same provider, different model slug → raises.
  - matching tuple (new space `(openai_compat, bge-m3)`, config matches) → proceeds.
  - ingest path mismatch → `_embed_with_config` raises (mock embed boundary).
- [ ] **D2** add `_assert_identity_match`; call in `backfill_space` (after reading space row + cfg) and `ingest._embed_with_config` (after `get_embed_config` + `get_active_space`).
      Add the "provider-only swap does not invalidate whitening (ADR-0017)" comment. GREEN.
- [ ] **D3** gates clean; commit: `feat(embeddings): hard-reject configured-vs-store identity mismatch at the producer seam (AC6)`.

## 8. Slice E — anthropic_compat chat branch (AC2)

- [ ] **E1 (test-first)** `tests/test_llm.py`, run → RED:
  - `_llm_call` with `provider="anthropic_compat"` posts `{base_url}/v1/messages`, headers
    `x-api-key` + `anthropic-version: 2023-06-01`, body top-level `system` + `max_tokens` +
    `messages`, `follow_redirects=False`.
  - content-block parse: `{"content":[{"type":"text","text":"{\"ok\":true}"}]}` → `{"ok":true}`
    (post think-strip); multiple text blocks concatenated; non-text ignored.
  - private `base_url` raises via `validate_base_url`.
  - `configure_llm` accepts `anthropic_compat`; persisted.
- [ ] **E2** implement the `anthropic_compat` arm + `_ANTHROPIC_VERSION`/`_ANTHROPIC_MAX_TOKENS`;
      widen `configure_llm` provider set + `_get_llm_config` normalize/strip. GREEN.
- [ ] **E3** gates clean; commit: `feat(llm): anthropic_compat Messages-API chat branch behind _llm_call (AC2)`.

## 9. Slice F — final audit (AC4-final)

- [ ] **F1** `Grep "_ssrf_check_openai_compat"` → empty (zero refs).
- [ ] **F2** `Grep "validate_base_url"` in `web.py`/`vision.py` → still zero (strict path intact).
- [ ] **F3** secrets audit: review every `logger.*`/error/`repr` in `embeddings.py`/`llm.py`/
      `embed_swap.py` touching URLs/keys → key absent, every URL `_sanitize_url`-ed.
- [ ] **F4** test/source ratio ≥ 1.35× preserved (manual count of new tests vs new source).
- [ ] **F5** full gate (§ Verification); commit if any fixups: `test(embeddings): provider-abstraction audit — dup-guard removed, secrets-in-logs covered`.

---

## Documentation

- [ ] `docs/usage/ingesting-documents.md` — "Embedding providers" subsection:
      `configure_embeddings(provider, base_url, model, api_key, allow_loopback_base_url)`; the
      four families; `openai_compat` covering vLLM/TEI/LM-Studio/OpenRouter/Ollama-Cloud with
      one `base_url`; `http://localhost:11434/v1` local example **requiring**
      `allow_loopback_base_url=true`; `api_key="env:VARNAME"` (recommended) vs inline
      (plaintext-at-rest caveat). **Sample/example** block: a local-vLLM config and a hosted
      OpenAI-embedder config (satisfies the issue's "sample/example for `configure_embeddings()`").
      Reference ADR-0018 by path; do not restate it.
- [ ] `docs/usage/structured-extraction.md` — `anthropic_compat` LLM family (`/v1/messages`,
      `x-api-key`+`anthropic-version`, system-as-top-level, `max_tokens`); chat-only (no
      embeddings); note local `openai_compat` reachable via the loopback flag.
- [ ] `docs/reference/schema.md` — new `config` keys `embed_base_url`, `embed_api_key`,
      `allow_loopback_base_url`; note `embed_api_key`/`llm_api_key` may hold `env:VARNAME`
      (not persisted-as-secret) or inline plaintext; reaffirm `embed_spaces.provider`/`.model`
      as the identity-tuple fields the producer populates.
- [ ] `CLAUDE.md` — embeddings.py domain-row note: providers are now `openai_compat` (not `openai`).
- **N/A:** no ADR edit (0018 merged; 0015 is #469); no ROADMAP edit (issue is the tracker);
  no new dependency docs (httpx-only, no SDK).

## Testing

TDD per slice — **failing test first**, watch it fail, implement, green. All HTTP mocked at
the `httpx` boundary (`@patch("knowledge_base.<mod>.httpx.post"/".get"`); mock `socket.getaddrinfo`
for the rebind case to stay offline); `tmp_path` SQLite, never the real DB; no network/keys.
Files: `tests/test_utils.py` (SSRF matrix), `tests/test_embeddings.py` (provider + cache),
`tests/test_embed_swap.py` (`configure_embeddings`, migration, identity), `tests/test_ingest.py`
(identity on ingest path), `tests/test_llm.py` (`anthropic_compat`, guard migration).

**REQUIRED security tests (risk evidence, not optional):** the full A1 SSRF matrix (rebind
`127.0.0.1.nip.io`, loopback-literal vs resolved-loopback, exact-`localhost` vs look-alikes,
metadata/RFC-1918 floor, scheme), redirect-disabled on both paths, loopback-opt-in plumbed to
the embed path **and** config-write, key/userinfo never logged, `env:VAR` never persisted.
**REQUIRED contract tests:** `@patch(...embed)` mock seam still intercepts; Protocol satisfied;
cache no-collision (the B1 headline); identity hard-reject (D1); no-drift for existing local
Ollama. **Ratio:** the SSRF matrix alone is ~12 cases against modest source additions ⇒ the
1.35× test/source ratio is maintained or improved (audited in F4). No `@pytest.mark.slow` added.

## Verification

Run from the worktree root:

- [ ] `uv run pytest -m "not slow" -q` (full suite green)
- [ ] `ruff check src/ tests/` (strict select `E,F,W,B,SIM,UP,C4,PTH,RUF,S` — `S`=bandit:
      any `# noqa: S…` on the new SSRF/secret code must carry a justification, matching house style)
- [ ] `ruff format --check src/ tests/`
- [ ] `uv run basedpyright src/ tests/` (needs `uv sync --all-groups`, already synced)
- [ ] `Grep "_ssrf_check_openai_compat"` → empty; `Grep "validate_base_url"` in web.py/vision.py → empty
- [ ] test/source ratio ≥ 1.35× (F4)
- **N/A:** no perf/load benchmark (provider dispatch is the same single HTTP call); no
  migration-rollback test (K/V config, no DDL change).

## Git operational steps

Worktree + branch already exist.

- [ ] Commit per slice (A–F) — Conventional Commits, imperative, WHY in body referencing the
      AC + ADR-0018 section, atomic, **no co-author**.
- [ ] Post a comment on **#516** linking this committed plan file (the plan is published +
      referenced; no duplicate plan-issue since #516 is the issue of record).
- [ ] `git push -u origin feat/516-provider-abstraction`.
- [ ] `gh pr create` — title `feat(embeddings): provider abstraction (ADR-0018)`; body
      `Closes #516`, AC1–AC6 + owner-comment each mapped to the satisfying slice, the 7
      tension resolutions, and the OUT-OF-SCOPE list (streaming/tools/multi-turn, operator/
      whitening, #480 StorageBackend, keyring, full mismatch engine #468, ADR-0015 doc #469).
      Labels `type:feature`, `area:embeddings`. Direct reviewers at the SSRF matrix, the
      cache-key fix, and the ADR-0015 non-loosening — the three highest-risk diffs.
- [ ] `/super-review` (workflow adversarial reviewers + agy per roster pref); address findings;
      re-run all gates after any change.
- [ ] `finish-pr` pre-merge checklist; then squash-merge once green + approved; confirm #516 auto-closes.

---

## Risks / watch-items

- **Legacy `TestOpenAIProvider`** patches `OpenAIProvider._call_api` + asserts `OPENAI_API_KEY`.
  Keep `OpenAIProvider` a thin subclass defaulting `base_url="https://api.openai.com"`, reading
  `OPENAI_API_KEY` when constructed bare, preserving the `_call_api(api_key, texts, model, dimensions)`
  seam → those tests survive unmodified.
- **`_sanitize_url` move** must re-export from `llm.py` so `tests/test_llm.py` imports resolve;
  basedpyright catches a missed reference.
- **Embedding connectivity probe** must be advisory-only (never raise) so `configure_embeddings`
  saves config even when the endpoint is down — including SSRF-`ValidationError`-caught-as-unreachable
  (the loopback C1 case depends on this).
- **Cache-key `api_key`** holds the **raw spec** (`env:VAR`/inline/None), never a resolved
  secret → no secret retained in a long-lived dict key; redacting `__repr__` keeps it out of logs.
- **`anthropic-version` pin** `2023-06-01` documented as a constant; a future bump is one line.

## Acceptance-criteria traceability

| AC / owner-comment                                                     | Slice | Headline test                         |
| ---------------------------------------------------------------------- | ----- | ------------------------------------- |
| AC1 OpenAICompat base_url, alias, vLLM/TEI/OpenRouter no-vendor-code   | B     | cache no-collision + base_url honored |
| AC2 anthropic_compat `/v1/messages` + content-blocks                   | E     | posts messages + parses blocks        |
| AC3 `configure_embeddings` mirror + config selection + env back-compat | C     | mirror/redact + env back-compat       |
| AC4 single guard both paths + `_ssrf_check_openai_compat` removed      | A, F  | guard migration + zero-refs grep      |
| AC5 loopback IP-literal only-with-flag; rebind stays rejected          | A     | rebind raises on opt-in               |
| AC6 tuple populated + mismatch hard-reject + no key in logs            | C, D  | identity reject + no-key-in-logs      |
| owner: `localhost` reserved name                                       | A     | exact-`localhost` accepted on flag    |
| owner: redirect re-validation                                          | A     | redirects disabled both paths         |
