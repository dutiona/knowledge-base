# KB#662 + KB#664 — security-first draft

## Outcome

After these two commits, an LLM API key can leave the process in exactly one way: as the value of an HTTP header,
and only when the whole value is one Bearer token of visible ASCII (`[!-~]+`). Every other path — a stored
`jobs.error`, `jobs.result`, a log record, the CLI's stderr — carries neither the key nor the endpoint it was
meant for. The redaction suite stops measuring a shared runner's speed and measures the algorithm instead.

Two commits, one per issue:

1. `fix(llm): refuse a bad LLM key and keep httpx text out of job errors` (#662)
2. `test(redact): measure linearity as a ratio, not a wall-clock bound` (#664)

The mechanism that holds is not a text scrub. It is a *positive* rule applied to the exact bytes that would
become a header value, before `httpx`/`h11` ever see them, plus a renderer that never calls `str()` on an
`httpx` error. `redact.scrub` is not the guard: a 32-hex-digit key has at most 4.0 bits/char, below
`HIGH_ENTROPY_BITS = 4.3` (`src/knowledge_base/redact.py:73-76`), so `scrub` cannot see the very key shape the
suite generates; and `scrub` never removes a link's URL at all (`_links` exempts a link's body from every rule
but url-credential, `src/knowledge_base/redact.py:166-201`). Neither property matters once the key is refused
before a header and the endpoint is never rendered.

## Constraints

- Two commits, conventional, imperative, body explains why, no `Co-Authored-By` on branch commits; one PR closing
  #666, #662, #664. No new dependency. Smallest change that fully solves both issues.
- AGENTS.md applies unchanged: TDD (failing test first), TDD fakes via `http_requests` / `fake_dns` / `public_net`
  (`tests/conftest.py:152-174`), never patch `httpx` inside a module, test keys from `secrets.token_hex(16)`, no
  secret-shaped literal, ruff `format`/`check` clean, basedpyright clean, Sphinx `-W` clean.
- knowledge-base is public: generic wording, no real endpoint, no real key in code, tests or docs.
- The owner's settled decisions are taken as given: the key must never reach `jobs.error` or the logs (h11 echoes
  bad header values; httpx error text carries them); mirror KB#660's K1 rule (no key and no path in errors); #664
  becomes a relative check.
- Scope is the LLM path. `web.py:1065/1078` composes an `httpx` failure into a `ValidationError` while keeping the
  caller's own URL (by #592's "a stored URL is a reference" ruling) — see the residuals table; unchanged.

### Every path a key can take, and its guard

| # | Path | Guard | If the guard fails |
| - | ---- | ----- | ------------------ |
| 1 | Header value: `x-api-key` (`src/knowledge_base/llm.py:226-228`), `Authorization: Bearer` (`:246-248`), probe (`:289-291`) | `_key_to_send(spec)` → `settings.require_key(var, raw)` on the *resolved* bytes, at header-construction time | `ValidationError` naming the field/variable, raised before `post()` is called. Zero requests |
| 2 | `h11` `Illegal header value b'…'` | Unreachable once (1) holds: `h11` refuses what `[!-~]+` refuses, and that value can no longer arrive | n/a — the class of failure no longer exists for our keys |
| 3 | `jobs.error` and `jobs.result["error"]` (`src/knowledge_base/jobs.py:204-222`) | `utils.error_text(exc)` at the sink | `HTTPStatusError (HTTP 503)`; the URL never rendered |
| 4 | `jobs.result["errors"]` per-chunk list (caller-visible via `routes/operations.py:21-44`) | `error_text` at the producer, `src/knowledge_base/extraction.py:937` | rendered class+status per chunk |
| 5 | Log records: `jobs.py:214/222`, `extraction.py:938-945`, `:984` | same renderer, same value | no endpoint, no key in the record |
| 6 | CLI stderr: `src/knowledge_base/indexer.py:470-475` | same renderer in both branches (byte-identical for KB's own errors) | no endpoint on stderr |
| 7 | Refusal messages (`llm.py:47-50`, `settings.py:186-189`, `:212`) | name the variable only; `{spec!r}` is removed (it echoed `file:/etc/hostname`) | a path or a key-shaped token in an error |
| 8 | `jobs.result["raw"]` (model output) | none, deliberately | the model's own text; the prompt carries no key and stored chunks are scrubbed at ingest |
| 9 | Plaintext at rest, `config.llm_api_key` | none, deliberately (documented, `docs/reference/schema.md:282`) | operator-controlled local posture, unchanged by this PR |
| 10 | A padded or CR/LF key already in `config` | refused at every config read (`llm.py:62`) with `llm_api_key must hold the key alone: …` | fail closed: extraction refuses until the operator reconfigures |

### The five open questions, answered

1. **Where the rule lives.** `settings.require_key(var, raw) -> str` is the single implementation of #660's rule;
   `_KEY_RE` stays private to `settings`. `_key_source` is refactored onto it (same message, same behaviour);
   `llm.py` owns the *policy* — which providers (both `_HTTP_LLM_FAMILIES`, mirroring `_key_source`'s
   `_OPENAI_FAMILIES` gate, since `ollama` builds no headers and `configure_llm:396-398` drops key material for
   it) and which name to report (`llm_api_key` for an inline value, the variable for an `env:` spec). A key that
   is not one token is **refused, not stripped**: the stored value is what the operator sees, and silently
   trimming a secret would send a different one than the row shows. An `env:` variable that is *unset* resolves
   to nothing and stays keyless — a local `llama-server` without auth keeps working (pinned by test 3).
2. **How the sinks render.** One shared helper, `utils.error_text(exc) -> str`: `httpx.HTTPStatusError` →
   `f"{type(exc).__name__} (HTTP {status})"`, any other `httpx.HTTPError` → its class name, everything else →
   `str(exc)` unchanged. No `scrub`: the routes' mechanism (`routes/__init__.py:25-35`) is structural, not
   textual, and `scrub` cannot fail closed (see Outcome). `jobs.py` and `indexer.py` call it directly — no new
   module, and `routes` is not imported from `jobs` (wrong direction).
3. **Are `extraction.py`'s sinks in scope?** Yes, and they are the reason criterion 3 can be met at all:
   `ExtractionError(f"LLM extraction failed: {e}")` (`:750`) turns an `httpx` failure into a non-`httpx`
   exception, so no downstream sink can recognise it and strip the URL. The three sites (`:750`, `:937`, the log
   at `:938-945`, plus `:984`) render with `error_text` where the foreign text enters.
4. **The #664 measurement.** `time.process_time` (our CPU, not the runner's scheduling), sizes `N = 2**19` and
   `2N`, one warm-up at `N`, and the assertion `t(2N)/t(N) < 3.0`: a linear pass is 2.0, a quadratic one 4.0.
   The test still fails on a quadratic implementation *by construction*: a committed test drives the same helper
   with a deliberately quadratic double at small sizes and requires it to reject (see step 5).
5. **Docs.** Four wording fixes, listed under `## Documentation`. ADR-0018 is a decision record and stays
   untouched; the reference docs state current behaviour.

## Implementation sequence

Landing order is guard-first: the shared rule, then the LLM's use of it, then the sinks, then the docs that now
describe a refusal, then #664.

- [ ] **Step 1 — one home for the key rule** (`src/knowledge_base/settings.py`)
  - Files: `src/knowledge_base/settings.py` (`require_key` added, `_key_source` refactored onto it, `__all__`
    entry).
  - Failing test: none, and that is the point — this must not change behaviour. Its regression assertion is the
    existing parametrized test that already encodes #660, which must stay green after the move:
    `uv run pytest tests/test_settings.py -q -k "key"` → expect the same pass count as on `origin/master`.
  - Change:
    ```python
    def require_key(var: str, raw: str) -> str:
        """*raw* as the key it holds alone: one Bearer token of visible ASCII, or a refusal naming *var*.

        h11 echoes a header value it refuses and httpx cannot encode a non-ASCII one, so a value that is not one
        token must not reach a request (#660 for the embedder key, #662 for the LLM key). The refusal names *var*
        and never quotes *raw*.
        """
        if not _KEY_RE.fullmatch(raw):
            raise ValidationError(f"{var} must hold the key alone: visible ASCII characters, no whitespace")
        return raw
    ```
    and in `_key_source`: `if provider in _OPENAI_FAMILIES: require_key(API_KEY_VAR, os.environ[API_KEY_VAR])`
    (identical message text, so `test_an_embedder_key_the_http_client_would_echo_is_refused` and
    `test_a_padded_env_key_still_starts_a_provider_that_never_sends_it` are untouched).
  - Green: `uv run pytest tests/test_settings.py -q` → all pass; `uv run ruff check src/knowledge_base/settings.py`
    → clean.
  - Commit: (with steps 2-4) `fix(llm): refuse a bad LLM key and keep httpx text out of job errors`

- [ ] **Step 2 — the LLM key gets the rule at configure time and at use time** (`src/knowledge_base/llm.py`, `tests/test_llm.py`)
  - Files: `src/knowledge_base/llm.py`, `tests/test_llm.py`.
  - Failing tests first (4 new, one parametrized; `import secrets` added to the test module):
    1. `test_a_padded_llm_key_is_refused_naming_the_field` — `@pytest.mark.parametrize` over
       `("openai_compat", "anthropic_compat")` × `["{k} ", " {k}", "{k}\r", "{k}\nmore", "{k}\u00e9", "{k}\x01"]`;
       asserts `pytest.raises(ValidationError, match=r"^llm_api_key must")`, `key not in str(refused.value)`,
       `http_requests == []`, and `_config_rows(conn) == before` (nothing stored).
    2. `test_a_padded_env_llm_key_is_refused_before_any_request` — `monkeypatch.setenv("KNOWLEDGE_BASE_LLM_API_KEY",
       f"{key} ")`, `cfg = {..., "api_key": "env:KNOWLEDGE_BASE_LLM_API_KEY"}`, `_llm_call("p", cfg=cfg)` →
       `ValidationError` matching `^KNOWLEDGE_BASE_LLM_API_KEY must`, `key not in` the message, `http_requests == []`.
    3. `test_an_unset_llm_key_variable_stays_keyless` — the variable deleted, same `env:` spec: the POST goes out
       and `"Authorization" not in http_requests[0].headers`. This pins the answer to open question 1 so no later
       "hardening" breaks a keyless local server.
    4. `test_a_stored_padded_key_is_refused_when_the_provider_switches_to_an_http_family` — store a padded key
       under `provider="ollama"` (allowed, as #660 allows a padded key for a provider that never sends it), then
       `configure_llm(conn, provider="openai_compat", base_url=…)` with no `api_key` → refused, config unchanged.
    Red: `uv run pytest tests/test_llm.py -q -k "padded or keyless"` → 4 failed (the first two record a request;
    the fourth rewrites the provider row).
  - Change, exact:
    ```python
    def _key_to_send(spec: str | None) -> str | None:
        """The key *spec* resolves to now, refused unless it is one Bearer token of visible ASCII, or None.

        The last gate before a header value: ``env:`` reads its variable at call time and a ``cfg`` read earlier
        can be stale. An unset variable is no key, so a keyless endpoint stays usable (#662).
        """
        key = _resolve_api_key(spec)
        if key is None:
            return None
        return require_key(spec[4:] if spec and spec.startswith("env:") else "llm_api_key", key)
    ```
    ```python
    def _check_llm_key_spec(spec: str | None, provider: str) -> None:
        """An LLM key is inline or ``env:KNOWLEDGE_BASE_LLM_API_KEY``, and for a provider that sends it one token."""
        if spec and spec.startswith(("env:", "file:")) and spec != f"env:{LLM_API_KEY_VAR}":
            raise ValidationError(
                f"an LLM api_key is an inline value or env:{LLM_API_KEY_VAR}: no other variable of this process, "
                "and no file"
            )
        if provider in _HTTP_LLM_FAMILIES:
            _key_to_send(spec)
    ```
    Call sites: `_get_llm_config` passes `prov` (`:62`); `configure_llm` passes the **prospective** provider at
    `:363` and `:366` (that is what makes test 4 refuse before the write); the three header sites
    (`:226`, `:246`, `:289`) replace `_resolve_api_key(...)` with `_key_to_send(...)`. `_test_llm_connectivity`
    gains `except ValidationError as exc: warning = str(exc)` before its generic branch, so a padded key is named
    in the probe's warning instead of hiding behind `Connectivity test failed: ValidationError`.
    Import: `from .settings import LLM_API_KEY_VAR, LLM_VAR, require_key, switch_on`.
  - Green: `uv run pytest tests/test_llm.py tests/test_settings.py -q` → all pass, including the four new tests
    (12 cases for the parametrized one).
  - Commit: same as step 1.

- [ ] **Step 3 — no sink renders an httpx error's own text** (`src/knowledge_base/utils.py`, `jobs.py`, `extraction.py`, `indexer.py`)
  - Files: source `src/knowledge_base/utils.py`, `src/knowledge_base/jobs.py`, `src/knowledge_base/extraction.py`,
    `src/knowledge_base/indexer.py`; tests `tests/test_jobs.py`, `tests/test_extraction.py`, `tests/test_indexer.py`.
  - Failing tests first (5 cases):
    1. `tests/test_jobs.py::test_a_failed_job_stores_no_endpoint_and_no_header_value` — parametrized
       `("status", "protocol")`, using the `_run_once` helper (`tests/test_jobs.py:287-301`): a fake dispatch
       raising `httpx.HTTPStatusError(f"Server error '503' for url '{url}'", request=…, response=…)` built on
       `url = "http://llm.example.test:1234/v1/chat/completions"`, and `httpx.HTTPError(f"Illegal header value
       b'Bearer {key}'")` (h11's own shape, the key from `secrets.token_hex(16)`). Asserts `job["status"] ==
       "failed"`, `job["error"] == "HTTPStatusError (HTTP 503)"` for the status case, and for both:
       `"llm.example.test" not in job["error"]`, `key not in job["error"]`, `"llm.example.test" not in
       caplog.text`, `key not in caplog.text` under `caplog.at_level(logging.ERROR, logger="knowledge_base.jobs")`.
    2. `tests/test_extraction.py::test_a_failed_llm_call_names_no_endpoint` — `patch("knowledge_base.extraction.\
       _llm_call", ...)` raising the same `HTTPStatusError`, `_extract_single_pass(conn, paper_id,
       [{"id": 1, "content": "text"}])` → the `ExtractionError` message contains `HTTPStatusError (HTTP 503)` and
       not the host.
    3. `tests/test_extraction.py::test_a_failed_chunk_is_recorded_without_the_endpoint` — the map-reduce path with
       every chunk failing (the scaffolding of `test_map_reduce_logs_failed_chunk`, `tests/test_extraction.py:1241`)
       → `ExtractionError.errors[0]["error"]` carries class+status, the host appears in neither
       `json.dumps(exc.errors)` nor `caplog.text`.
    4. `tests/test_indexer.py::test_cli_stderr_never_names_the_embedder` — `stub_document_embedder.down = 503`,
       `_exit_of(["--db", str(db), "ingest", str(doc)])` → exit 1, `names_the_embedder(err)` false, and
       `HTTPStatusError (HTTP 503)` present (helpers `embedder_error`/`names_the_embedder`, `tests/conftest.py:260-277`).
    Red: `uv run pytest tests/test_jobs.py::test_a_failed_job_stores_no_endpoint_and_no_header_value tests/test_extraction.py::test_a_failed_llm_call_names_no_endpoint tests/test_extraction.py::test_a_failed_chunk_is_recorded_without_the_endpoint tests/test_indexer.py::test_cli_stderr_never_names_the_embedder -q`
    → 5 failed (today each stored text holds the URL, and one holds the key verbatim).
  - Change, exact — `utils.py`, after `_resolve_api_key`, plus `"error_text"` in `__all__`:
    ```python
    def error_text(exc: BaseException) -> str:
        """What *exc* says where a caller or a log reads it: an httpx error's class, never its own text.

        httpx puts the endpoint URL in a status error's message and h11 echoes the header value it refused, so
        neither is rendered: a status error reads as its class and status, any other httpx error as its class
        alone. Every other error is KB's own text, which names a variable rather than a value (#660, #662); it
        stays whole, since a job's ``error`` is the operator's only diagnosis.
        """
        if isinstance(exc, httpx.HTTPStatusError):
            return f"{type(exc).__name__} (HTTP {exc.response.status_code})"
        if isinstance(exc, httpx.HTTPError):
            return type(exc).__name__
        return str(exc)
    ```
    `jobs.py`: in the `ExtractionError` branch `detail = error_text(exc)`, used for `result_data["error"]`, for the
    `error` column and for `logger.error("Job %d failed: %s", job_id, detail)`; the generic branch likewise. Leave
    `exc.errors` / `exc.raw` as produced — the producer renders them (next). `indexer.py`: `error_text(exc)` in both
    branches of `main`'s handler (byte-identical output for domain errors, so no CLI test moves). `extraction.py`:
    `error_text(e)` at `:750`, `:937`, the FAILED log at `:938-945` and the resolution warning at `:984`; add
    `error_text` to the existing `from .utils import egress_client` line.
  - Green: `uv run pytest tests/test_jobs.py tests/test_extraction.py tests/test_indexer.py -q` → all pass; in
    particular `test_a_job_s_uncommitted_writes_land_only_with_its_completion` still finds `"boom"` in
    `job["error"]` and `test_extraction.py:544-546` still finds `"empty response"` in `errors[0]["error"]`, which
    is the diagnostic value the rule must keep.
  - Commit: same as step 1.

- [ ] **Step 4 — the docs state a refusal, not an allowance** (docs; part of commit 1)
  - Files and exact edits are in `## Documentation`. Green: `uv run --group docs sphinx-build -W -E -q -b html docs
    docs/_build` → exit 0, no warning (an orphan page or a broken link fails it).
  - Commit: same as step 1.

- [ ] **Step 5 — #664: linearity as a ratio** (`tests/test_redact.py`)
  - Files: `tests/test_redact.py` (only).
  - Failing test first: `test_the_linearity_check_rejects_a_quadratic_shape`, written against a helper that does
    not exist yet:
    ```python
    def test_the_linearity_check_rejects_a_quadratic_shape():
        with pytest.raises(AssertionError, match=r"t\(2N\)/t\(N\)"):
            _assert_linear(_rescan, lambda n: "x" * n, small=2**12)
    ```
    Red: `uv run pytest tests/test_redact.py -q -k "quadratic"` → 1 failed (`NameError: _assert_linear`). This is
    the honest red for a test-hardening issue: the discriminator is the deliverable, so it is proven by a test
    that stays in the suite rather than by a wall-clock bound no one can reproduce. The one-off demonstration to
    record in the commit body: patch `knowledge_base.redact.scrub` with a quadratic double and run the
    parametrized test → red on every shape.
  - Change, exact — replace the parametrized strings (`tests/test_redact.py:223-241`) with builders and the helper:
    ```python
    #: The smaller size the linearity check reads; the pair plus its warm-up costs what the old 2 MiB run did.
    _SMALL = 2**19
    #: t(2N)/t(N) is 2.0 for a linear pass and 4.0 for a quadratic one; a linear shape cannot reach 3.0 anywhere.
    _MAX_GROWTH = 3.0

    _HOSTILE = {  # the seven shapes #592/#664 guard, each as a builder of n bytes
        "jwt-prefix": lambda n: "=eyJ" * (n // 4),
        "url-authority": lambda n: "://u:p@" * (n // 7),
        "hex-context": lambda n: "token=a3f1b2c4d5e6f7a8b9c0d1e2f3a4b5c6 " * (n // 39),
        "pem-line": lambda n: "-----BEGIN " * (n // 11),
        "hex-words": lambda n: "_a3f1b2c4d5e6f7a8b9c0d1e2f3a4b5c6" * (n // 33),
        # One word, one underscore: a copied context-word tail made this quadratic (15 s at 8 MiB, now 0.6 s).
        "one-long-word": lambda n: "_" + ("g" + "0" * 32) * (n // 33),
        "interleaved": lambda n: ("me_" + "0" * 32 + " Ab3dE5fG7hI9jK1lM2nO ") * (n // 57),
    }

    def _cpu_seconds(run, text: str) -> float:
        started = time.process_time()
        run(text)
        return time.process_time() - started

    def _assert_linear(run, make, small: int = _SMALL) -> None:
        """Assert *run* over *make*'s shape grows about linearly: t(2N)/t(N) < ``_MAX_GROWTH``.

        A ratio of the same work at two sizes is a property of the algorithm; the absolute bound this replaces was
        a property of the runner (8.2 s against 6 s on a shared runner, #664). CPU time keeps a noisy neighbour's
        descheduling out, and each shape is built outside the clock.
        """
        shorter = make(small)
        run(shorter)  # warm: the allocator, and the regex module's own caches
        t_small = _cpu_seconds(run, shorter)
        t_large = _cpu_seconds(run, make(small * 2))
        ratio = t_large / max(t_small, 1e-6)
        assert ratio < _MAX_GROWTH, f"t(2N)/t(N) = {ratio:.2f} at N = {small} bytes: a linear pass doubles"

    def _rescan(text: str) -> None:
        """A deliberately quadratic detector: one full scan per character. No rule may ever look like this."""
        for _ in range(len(text)):
            text.count("x")

    @pytest.mark.parametrize("make", list(_HOSTILE.values()), ids=list(_HOSTILE))
    def test_redaction_is_linear_on_hostile_input(make):
        """The ids are #664's contract: ``[url-authority]`` was the shape that failed on a shared runner."""
        _assert_linear(scrub, make)
    ```
    `import time` stays; `time.monotonic` is replaced by `time.process_time`. Per-shape cost is unchanged in
    total (warm-up + N + 2N = 2 MiB, as today's single 2 MiB run), and the 8 MiB `one-long-word` point is dropped
    deliberately: the ratio needs no large input, and the shape is caught at 1 MiB.
  - Green: `uv run pytest tests/test_redact.py -q` → all pass, 7 parametrized shapes + the quadrative-double
    rejection; `uv run pytest tests/ -q -m "not slow"` shows no change in total suite time (±2 s).
  - Commit: `test(redact): measure linearity as a ratio, not a wall-clock bound`

## Documentation

- `docs/usage/structured-extraction.md:155-156` — after the sentence that limits an LLM `api_key` to an inline
  value or `env:KNOWLEDGE_BASE_LLM_API_KEY`, add: "A key for `openai_compat` or `anthropic_compat` is one Bearer
  token of visible ASCII, the rule the embedder key has followed since KB#660: a padded, multi-line or non-ASCII
  value is refused naming the field, never echoed, instead of failing inside the HTTP client."
- `docs/usage/structured-extraction.md:198-200` — in the plaintext-at-rest paragraph, add: "A stored value that is
  not one token is refused at every read, so re-run `configure_llm` if an older build accepted a padded key."
- `docs/reference/mcp-tools.md:683` — append to the `configure_llm_tool` paragraph: "; a key for an HTTP family is
  one Bearer token of visible ASCII, refused naming the field otherwise."
- `docs/reference/mcp-tools.md`, Jobs section (after `get_job_status_tool`'s **Returns**, `:996-1008`) — add: "A
  failed job's `error` names the failure class and, for an HTTP failure, its status (`HTTPStatusError (HTTP 503)`);
  it never carries the endpoint's URL or an httpx error's own text. `result.errors` holds one such message per
  failed chunk."
- `docs/reference/schema.md:282-283` — append: "A key for an HTTP LLM family holds one Bearer token of visible
  ASCII, refused at configure time and again before each request."
- Inline docstrings: `src/knowledge_base/llm.py:355-357` (`configure_llm`) gains one sentence for the visible-ASCII
  rule; the `settings.py` module docstring's issue ledger gains "``#660`` the key rule and ``#662`` the LLM key's
  use of it".
- Not changed, deliberately: `docs/design/adr/0018-provider-abstraction.md:363` is a decision record of its date;
  `README.md:375`'s one-line tool row says only "Check status of a background operation" and needs nothing.

## Testing

- `tests/test_settings.py` — unchanged tests are the regression proof for step 1: `test_an_embedder_key_the_http_
  client_would_echo_is_refused` (6 shapes) and `test_a_padded_env_key_still_starts_a_provider_that_never_sends_it`
  pin that the rule moved without changing.
- `tests/test_llm.py` — the boundary rule: refused at configure (both HTTP families, 6 shapes each, nothing
  stored, zero requests), refused at use for a padded `env:` value (zero requests), allowed for an unset variable
  (keyless request, no `Authorization`), and refused when an HTTP provider inherits a stored padded key. Together
  they are #662's acceptance 1 and 2.
- `tests/test_jobs.py` — the caller-visible sink: neither the endpoint nor the key in `jobs.error` or in the log
  record, for both a status error and h11's own text. This is acceptance 3 at the sink that `get_job_status_tool`
  returns (`routes/operations.py:21-44`).
- `tests/test_extraction.py` — the two producers that compose text before the sink sees it: the single-pass wrapper
  and the per-chunk `errors` list; they are why acceptance 3 can hold at all.
- `tests/test_indexer.py` — the CLI's stderr, through the existing `embedder_error`/`names_the_embedder` helpers.
- `tests/test_redact.py` — the 7 shapes keep their ids (the issue names `[url-authority]`), the ratio replaces the
  bound, and the quadratic double proves the check still fails on a quadratic implementation (acceptance 2 of #664).
- Not tested, with reasons: the probe's warning wording (advisory, covered by `test_llm_connectivity_*`), the
  plaintext-at-rest posture (documented, unchanged), and `web.py`'s composed URL (residual 8/#10 above).

## Verification

Run at the PR's final head, clean tree, in this order (AGENTS.md's merge gate):

```
uv lock --check
uv sync
uv run ruff check src/ tests/            # zero warnings
uv run ruff format --check src/ tests/   # no reformats
uv run pytest tests/ -q -m "not slow"    # all pass; the redaction test's total time is unchanged
uv sync --all-groups
uv run basedpyright src/ tests/          # zero errors
uv run --group docs sphinx-build -W -E -q -b html docs docs/_build
```

- The image step (#617) is N/A: no change to `Containerfile`, `.containerignore`, `pyproject.toml`, `uv.lock` or
  `.python-version`, so its `git diff --quiet origin/master...HEAD -- …` guard skips it; nothing this PR adds is a
  build input.
- CI status is not evidence: `.github/workflows/ci.yml` does not run for this repo.
- What to quote in the PR: the eight command results, plus the two demo lines — `uv run pytest tests/test_llm.py -q
  -k "padded"` failing on the parent commit and passing at head, and the one-off quadratic-`scrub` run that turns
  every shape red.
