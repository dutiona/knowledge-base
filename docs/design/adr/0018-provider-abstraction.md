# ADR 0018: Embedding & LLM provider abstraction (standard-API provider portability)

**Status:** Accepted (2026-06-26)
**Date:** 2026-06-26
**Scope:** Knowledge layer — `embeddings.py`, `llm.py`, `config` table, `embed_spaces` registry
**Roadmap:** KB delta 14 — epic #474 (embedding & quantization) primary, #480 (infra & scale) for the shared-port surface
**Related:** ADR 0015 (cross-layer embedding identity) · ADR 0017 (faithful-metric / E0 whitening substrate)

## Context

KB is the Knowledge layer of a four-layer cognitive architecture (Knowledge → Memory
→ Wisdom → Intelligence) and a companion to the Memory layer (ME). Both layers
produce vectors with an embedding model and call an LLM for structured extraction.
Today both capabilities are effectively **bound to Ollama** as the default, with a
half-built escape hatch toward other backends. The owner's end-state is the opposite
of this binding: **any provider conforming to a standard API must be a first-class
provider**, with no single vendor privileged — local Ollama, local ONNX, an
OpenAI-compatible HTTP endpoint (OpenAI itself, vLLM, LM Studio, OpenRouter, Ollama
Cloud, HuggingFace TEI), or an Anthropic-compatible endpoint. The cloud end-state
(KB running against a hosted embedder + hosted chat model) must be a configuration
change, not a code change.

This ADR is the **portability decision** only. It is deliberately orthogonal to the
_operators_ that ride on top of the vectors (ADR 0017 / E0 whitening, CSLS, diffusion
ranker, …) and to the _identity_ policy that governs which vectors are comparable
(ADR 0015). Those two ADRs assume a vector exists and is faithful to a declared model;
this ADR decides **how that vector and that LLM call are produced and routed**, and
how the producer's identity feeds 0015.

### Current state (verified against the code, axis-3 gap analysis)

The abstraction exists but is **two disconnected half-systems**, each anchored to a
literal vendor.

**Embeddings (`embeddings.py`).** There is a real `Protocol`:

```python
@runtime_checkable
class EmbeddingProvider(Protocol):
    def embed(self, texts, model, expected_dim=None) -> list[list[float] | None]: ...
```

backed by a registry `_PROVIDERS = {"ollama", "openai", "onnx"}` and dispatched
through module-level `embed()` / `embed_single()`. Three observations matter:

1. `OpenAIProvider` is **hardcoded** to the literal endpoint — there is no
   `base_url`:

   ```python
   resp = httpx.post(
       "https://api.openai.com/v1/embeddings",
       headers={"Authorization": f"Bearer {api_key}"}, ...)
   ```

   So vLLM / LM Studio / OpenRouter / Ollama Cloud / TEI — all of which speak
   `/v1/embeddings` — are **unreachable** even though the wire protocol is identical.
   The only OpenAI-compatible embedding endpoint KB can hit is OpenAI's own.

2. The provider selector is an **env var read inside dispatch**
   (`EMBED_PROVIDER`, plus `OPENAI_API_KEY`, `ONNX_EMBED_MODEL_PATH`), _not_ the
   `config` table. Embedding provider choice is therefore invisible to the DB,
   un-auditable, and divorced from the `embed_spaces` row that records what the
   vectors actually are.
3. There is **no SSRF guard** on the embedding path at all — it never needed one
   because the only HTTP target was a hardcoded public host.

**LLM (`llm.py`).** Here the design is the inverse — config-driven and SSRF-guarded,
but vendor-narrow:

- `_get_llm_config(conn)` reads `llm_provider`, `llm_model`, `llm_base_url`,
  `llm_api_key` from the `config` table. `provider ∈ {ollama, openai_compat}`.
- `openai_compat` POSTs to `{base_url}/v1/chat/completions` with a `Bearer` header
  and is gated by `_ssrf_check_openai_compat(base_url)`.
- There is **no `anthropic_compat` path** — Anthropic's Messages API
  (`/v1/messages`, `x-api-key` + `anthropic-version`, different request/response
  shape) cannot be reached.

The two systems **do not share** a provider notion, a registry, a config schema, or a
security guard. Embeddings key on `EMBED_PROVIDER` (env) with vendor names; LLM keys
on `llm_provider` (config) with API-family names. The word "provider" means two
different things in two files.

**Security state (verified).** There are in fact **two** SSRF guards, and the
embedding path uses neither:

- `utils.validate_base_url(url)` — the canonical guard, already shared by `web.py`
  and `vision.py`: checks scheme ∈ {http, https}, requires a hostname, and rejects
  any non-global IP via `is_private_ip` (which resolves **all** A/AAAA records to
  defeat DNS-rebinding such as `127.0.0.1.nip.io`).
- `llm._ssrf_check_openai_compat(base_url)` — a second, llm-local guard that calls
  `is_private_ip` but is otherwise a near-duplicate of the canonical one.

Both block **loopback** (`is_private_ip` returns true for `127.0.0.1`/`localhost`),
which has a sharp consequence the next section must resolve: **a locally served
vLLM / LM Studio / TEI on `localhost` is currently rejected by the `openai_compat`
path** — the existing guard's comment even tells the user to "use the 'ollama'
provider for local endpoints," which is wrong for an OpenAI-compatible local server.

### Why now / why this is the right altitude

Leading by the ideal: the provider is the _outermost_ seam of the system — every
embedding write, every query vector, every extraction call passes through it. Building
the E0 faithful-metric substrate (ADR 0017), the identity-mismatch enforcement (ADR
0015, #468/#469), and the cloud end-state all assume a clean answer to "who produced
this vector / this completion, and under what identity." Fixing the seam _after_
those land would mean retrofitting identity and whitening through a vendor-bound,
env-var-driven producer. The cost of doing it first is one config-schema change and a
provider-class generalization; the cost of doing it last is touching every downstream
consumer twice. So portability is a **prerequisite**, not a feature bolt-on.

## Decision

Adopt a **single, config-driven Provider model** spanning **both** embeddings and
chat/LLM, organized by **API family, not vendor**. A "provider" is a `(family,
base_url, api_key, model)` configuration that selects one of four wire protocols. The
same four families serve both capabilities; the capability (embed vs chat) selects the
endpoint and request shape within a family.

### 1. The four API families

| Family             | Embedding endpoint                  | Chat/LLM endpoint                | Auth                                     | Covers (non-exhaustive)                                |
| ------------------ | ----------------------------------- | -------------------------------- | ---------------------------------------- | ------------------------------------------------------ |
| `openai_compat`    | `{base_url}/v1/embeddings`          | `{base_url}/v1/chat/completions` | `Authorization: Bearer <key>`            | OpenAI, vLLM, LM Studio, OpenRouter, Ollama Cloud, TEI |
| `anthropic_compat` | — (Anthropic has no embeddings API) | `{base_url}/v1/messages`         | `x-api-key: <key>` + `anthropic-version` | Anthropic, Anthropic-compatible gateways               |
| `ollama`           | `{base_url}/api/embed`              | `{base_url}/api/generate`        | none (local)                             | native Ollama (auto-detected URL today)                |
| `onnx`             | local `InferenceSession`            | — (no LLM)                       | none (local file)                        | local ONNX embedding models                            |

Notes that fall out of the families, not vendors:

- **`openai_compat` is the workhorse.** Generalizing `OpenAIProvider` to take a
  `base_url` immediately unlocks six embedding backends and (already, for chat) the
  same six chat backends — _with no per-vendor code_. OpenAI literal becomes the
  degenerate case `base_url = https://api.openai.com`.
- **`anthropic_compat` is chat-only.** Anthropic exposes no embeddings endpoint, so
  the embedding side of this family is intentionally absent; an embedding config that
  names `anthropic_compat` is a configuration error, rejected at validation.
- **`ollama` stays native, not folded into `openai_compat`.** Ollama's own
  `/api/embed` and `/api/generate` differ from the OpenAI shape (batch `input`,
  `format: json`, response key `response`), and the WSL2 host auto-detection in
  `_get_ollama_url()` is load-bearing for the local-default experience. It remains its
  own family; users who run Ollama's OpenAI-compatibility shim may instead point
  `openai_compat` at `http://localhost:11434/v1`.
- **`onnx` is local-inference, embed-only.** Unchanged in behavior; folded into the
  unified registry only so the selector vocabulary is uniform.

### 2. Unified provider classes

Embeddings: generalize the vendor-bound class into a family-bound one.

```python
class OpenAICompatProvider:
    def __init__(self, base_url: str, api_key: str | None) -> None:
        # Normalize FIRST, then the same normalized value is what gets validated
        # (at config-write, §4) and stored — no /v1-strip can smuggle a host past
        # the guard, because validation runs on the post-strip string.
        self._base_url = base_url.rstrip("/").removesuffix("/v1")
        self._api_key = api_key  # never logged

    def embed(self, texts, model, expected_dim=None):
        validate_base_url(self._base_url)          # defense-in-depth (see §4)
        # POST {base_url}/v1/embeddings, dimensions=expected_dim, Bearer key
        ...
```

The SSRF check is shown here at call time as **defense-in-depth**, but the
**primary gate is at config-write** (`configure_embeddings()` / `configure_llm()`,
§4): a malicious `base_url` is rejected before it is ever persisted, so it is never
stored and re-validated on use. Both boundaries validate the **post-normalization**
URL — the `/v1` strip happens in `__init__` and at config-read (matching today's
`llm.py` `.removesuffix("/v1")`), and `validate_base_url` is applied to that already-
stripped value, closing any "smuggle a private host through the suffix-strip" gap.

`OpenAIProvider` becomes a thin alias: `OpenAICompatProvider(base_url="https://api.openai.com")`.
`OllamaProvider` and `ONNXProvider` are unchanged except for construction from config
(below). The `EmbeddingProvider` Protocol and the `embed()` / `embed_single()`
module-level dispatch are **unchanged** — mocks that `@patch("knowledge_base.ingest.embed")`
keep working, preserving the testing convention.

LLM: `llm.py` gains the `anthropic_compat` branch alongside `ollama` and
`openai_compat`, dispatching on `cfg["provider"]` exactly as today. The request/response
adapters for the three chat families live behind one `_llm_call()` so callers
(extraction, keywords) are untouched.

### 3. One registry, one config schema

The selector vocabulary becomes the **four family names** for both capabilities. The
`config` K/V table carries two parallel, independent provider configs (embeddings and
LLM are deliberately separable — a deployment may embed locally on ONNX and extract via
a hosted `openai_compat` chat model):

| Embedding key    | LLM key        | Meaning                                                           |
| ---------------- | -------------- | ----------------------------------------------------------------- |
| `embed_provider` | `llm_provider` | family ∈ {openai_compat, anthropic_compat, ollama, onnx}          |
| `embed_base_url` | `llm_base_url` | required for `openai_compat` / `anthropic_compat`                 |
| `embed_api_key`  | `llm_api_key`  | bearer/`x-api-key`; may be `env:VARNAME` indirection (see §4)     |
| `embed_model`    | `llm_model`    | model slug — **the `model` field of the ADR-0015 identity tuple** |

This **migrates embedding provider selection out of `EMBED_PROVIDER`/`OPENAI_API_KEY`
env vars into the `config` table**, making it auditable and putting it in the same
place LLM config already lives. Env vars are retained only as the _value source_ for
secrets (§4) and for the existing local conveniences (`OLLAMA_HOST`,
`ONNX_EMBED_MODEL_PATH`). A `configure_embeddings()` tool mirrors the existing
`configure_llm()` — same validation, same connectivity probe, same redaction of the
key from the returned dict.

### 4. Security — one SSRF guard, secrets never persisted in the clear

This is a hard requirement, not advice.

- **Single guard, applied everywhere.** Collapse `llm._ssrf_check_openai_compat` into
  the canonical `utils.validate_base_url`, and call it on **every user-supplied
  `base_url`** at _both_ the embedding and LLM boundaries (and at config-write time in
  the `configure_*` tools). The embedding path, which has no guard today, gains one as
  a consequence of this ADR — that is the point.
- **The SSRF guard MUST stay for any non-loopback base_url.** A hosted endpoint can be
  attacker-influenced (an open-redirect, a typo'd host, a DNS-rebind); blocking
  non-global targets prevents the classic cloud-metadata / internal-service pivot.
- **Loopback is the deliberate exception, scoped tightly.** To make local `openai_compat`
  servers (Ollama, vLLM, LM Studio, TEI) first-class — the current code wrongly rejects
  them — the guard permits a `base_url` whose host is a **loopback IP literal**
  (`127.0.0.0/8`, `::1`) **or the RFC-6761 reserved name `localhost`** (which a conformant
  resolver MUST map to loopback and MUST NOT send to the network, so it is _not_ a
  DNS-rebinding vector), **only when an explicit opt-in config flag
  `allow_loopback_base_url=true` is set**. The reserved-name allowance is what makes the
  recommended `http://localhost:11434/v1` local config first-class — without it the guard
  would reject the very URL this ADR tells operators to use. The exception is restricted to
  those two forms: it does **not** extend to any _other_ `hostname` that merely resolves to
  loopback (e.g. `127.0.0.1.nip.io`, or a name whose A/AAAA record points at `127.0.0.1`).
  That resolve-to-loopback case is exactly the DNS-rebinding vector `is_private_ip` was
  built to defeat (it resolves all A/AAAA records and rejects if any is non-global), so the
  flag is checked **on the parsed host being either a loopback IP literal or the exact
  reserved label `localhost`**, before any DNS resolution — never by "did this resolve to
  loopback." The default remains fail-closed: a non-flagged private/loopback URL is
  rejected, and the non-loopback SSRF floor (private, link-local, metadata, rebinding) is
  **always** mandatory regardless of the flag.
- **Redirects are a per-request invariant, not a one-time check.** Validating only the
  configured `base_url` is insufficient: a permitted host can open-redirect (or DNS-rebind
  between validation and connect) to an internal target. The HTTP client MUST either
  **disable cross-host redirect-following** or **re-run the guard on every redirect target**
  before issuing the next hop. The guard binds the _actual connect target_ of each request,
  before and after any redirect — never just the configured string.
- **API keys: env-first, never logged, never returned.** A key may be given inline or,
  preferred, as an `env:VARNAME` indirection that is resolved at call time and **never
  written to the DB**. Inline keys are stored in the `config` table (documented as
  plaintext-at-rest, acceptable for the local-only single-user posture, flagged for
  keyring hardening — same caveat `configure_llm` already carries). In **no** code path
  is a key, or a URL containing userinfo, written to a log: reuse `_sanitize_url()` for
  any URL that reaches a log line, and keep keys out of `repr`/error strings. This ties
  directly to the project's absolute no-secrets rule.

### 5. Identity coupling (ties to ADR 0015 and ADR 0017)

The provider is **not** identity-neutral. Per ADR 0015, an embedding store's identity
tuple includes both `provider` and `model`, and two tuples are compatible **iff
field-by-field equal**. This ADR's job is to make `provider` a first-class, swappable
field _without weakening that rule_:

- **`embed_provider` / `embed_model` populate the ADR-0015 identity tuple** written to
  the `embed_spaces` row on first embedding write. The family name (`openai_compat`,
  `ollama`, …) is the `provider` field; `embed_model` is the `model` field.
- **Swapping provider for the same model slug is permitted but is still a tuple
  change.** Serving `Qwen/Qwen3-Embedding-0.6B` via `tei` vs via `vllm` (both
  `openai_compat`, both the same `model` slug) yields _numerically_ comparable vectors
  **only if the server actually serves that model**. The ADR-0015 mismatch rule still
  governs: a configured provider whose identity ≠ the store's recorded identity is
  **hard-rejected**. This ADR widens the set of _legitimate_ providers; it does **not**
  loosen the equality check that protects against silent space-mixing.
- **The residual risk ADR 0015 names is exactly this ADR's residual risk.** `model` is
  operator-declared, slug-level — a provider that silently serves a _different_ model
  under the requested slug (a misconfigured TEI, an OpenRouter route that quietly
  substitutes) passes the tuple check and corrupts retrieval. Provider portability
  _increases_ the surface for this (more backends, more ways to misconfigure), so the
  weight-hash hardening that 0015 defers becomes more valuable, not less. No new
  guarantee is offered here beyond 0015's; the risk is named and inherited.
- **Whitening is provider-agnostic once the model is fixed; it is NOT model-agnostic
  (ADR 0017).** The E0 whitening transform `W, μ` is computed from the corpus geometry
  of a _specific embedding space_. Switching the _serving provider_ for the _same_
  model and the _same_ vectors leaves `W, μ` valid (the metric is provider-independent
  once whitened). Switching to a provider that serves a _different_ model is, by
  definition, a new space → a reconstruction → `W, μ` **must be recomputed** and the
  RMT/CSLS caches invalidated, exactly as ADR 0017's content fingerprint
  (`hash{epoch, W_version, csls_k, row_count_bucket}`) requires. The provider field
  thus participates in space identity but, alone, does not invalidate the metric.

### 6. No hard SDK dependencies

All HTTP families are implemented with **`httpx` only** — no `openai`, no `anthropic`,
no vendor SDK is added to the dependency tree. This is already the pattern
(`OpenAIProvider` deliberately uses raw `httpx` "to avoid hard dependency on the openai
package"); this ADR generalizes it rather than reversing it. The wire formats are
small and stable; the cost of three request/response adapters is far below the cost of
two SDKs' transitive dependencies, version pins, and auth abstractions. `onnx` keeps
its existing optional-group import.

## Consequences

**Positive**

- **Cloud end-state is a config change.** Pointing KB at OpenAI, an OpenRouter route,
  Ollama Cloud, or a self-hosted vLLM/TEI cluster — for embeddings, chat, or both — is
  `configure_embeddings(...)` / `configure_llm(...)`, no code edit. The owner's "not
  tied to Ollama" requirement is met by construction.
- **Six backends per family for free.** Generalizing one class (`base_url`) makes
  every OpenAI-compatible server reachable; the marginal cost of the seventh is zero.
- **Local OpenAI-compatible servers stop being second-class.** The loopback opt-in
  fixes the current bug where a `localhost` vLLM/LM Studio/TEI is rejected.
- **Embedding provider choice becomes auditable.** Moving it from env to the `config`
  table puts it next to the `embed_spaces` identity it determines, closing the gap
  where the recorded `provider` and the actually-used provider could silently diverge.
- **Testability is preserved and improved.** The `EmbeddingProvider` Protocol and the
  `_llm_call` seam mean every family mocks at the boundary with `httpx`-level fakes; no
  network, no SDK, no real key in tests. The 1.35x test/source ratio is maintainable
  because the new code is three thin adapters behind existing seams.
- **One SSRF guard to reason about.** Collapsing two near-duplicate checks into
  `validate_base_url` removes a drift hazard and extends coverage to the embedding path
  that had none.

**Negative / costs**

- **Config-schema migration.** `EMBED_PROVIDER`/`OPENAI_API_KEY` → `embed_*` config
  keys is a one-time migration with a back-compat read of the old env vars during a
  deprecation window. Small, but it is a behavior change for existing local setups.
- **Residual silent-model-substitution risk (inherited from 0015).** More providers =
  more ways a server can serve the wrong model under the right slug. This ADR does not
  close that gap; it raises the priority of the deferred weight-hash hardening.
- **Three chat-format adapters to maintain.** Ollama, OpenAI, and Anthropic differ in
  request/response shape and `think`-tag handling; the Anthropic Messages format (system
  as top-level field, `content` blocks) is the most divergent and adds the most adapter
  surface. Bounded and stable, but non-zero.
- **Plaintext keys at rest remain** for the inline path until keyring hardening lands;
  the `env:VARNAME` indirection is the recommended mitigation, not a default.

**Neutral / explicitly out of scope**

- This ADR does **not** add streaming, tool-calling, or multi-turn chat — KB's LLM use
  is single-shot JSON extraction; the families are implemented only to that surface.
- It does **not** change any _operator_ (whitening, CSLS, ranker) — those are ADR 0017.
  The metric consumes whatever faithful vector the provider yields.
- It does **not** add a `StorageBackend` port (KB delta 9 / epic #480) — that is a
  separate seam; this ADR is the _producer_ seam, not the _store_ seam, though both are
  "pluggable port" work and may share the #480 home.

## References

- Current code: `src/knowledge_base/embeddings.py` (`EmbeddingProvider` Protocol,
  `OpenAIProvider` hardcoded endpoint, `_PROVIDERS` registry, env-var selector),
  `src/knowledge_base/llm.py` (`_get_llm_config`, `openai_compat` branch,
  `_ssrf_check_openai_compat`, `configure_llm`), `src/knowledge_base/utils.py`
  (`validate_base_url`, `is_private_ip` — the canonical SSRF guard).
- ADR 0015 — Cross-layer embedding-identity & mismatch parity policy
  (`docs/design/adr/0015-cross-layer-embedding-identity-policy.md`): the identity tuple
  `(model, provider, dim, matryoshka_base_dim, element_type)` this ADR populates and
  whose mismatch rule it must not loosen.
- ADR 0017 — faithful-metric / E0 whitening substrate (planned, KB delta 1, epic #482):
  the metric layer that rides above the provider; whitening recomputation is gated on
  _model_ change, not _provider_ change.
- Roadmap: KB delta 14 (this ADR), under epic #474 (embedding & quantization) with the
  shared-port surface tracked in #480 (infra & scale). Cross-pollination context:
  `docs/design/kb-me-roadmap-parity.md`, `docs/design/kb-to-me-cross-pollination.md`.
- Provider wire formats (httpx-only, no SDK): OpenAI `/v1/embeddings` &
  `/v1/chat/completions`; Anthropic Messages `/v1/messages`
  (`x-api-key` + `anthropic-version`); Ollama `/api/embed` & `/api/generate`;
  HuggingFace TEI (OpenAI-compatible `/v1/embeddings`).
