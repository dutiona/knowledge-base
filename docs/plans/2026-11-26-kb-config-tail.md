# KB config tail — a refused key, a renderer that never quotes httpx, and a redaction test that measures the algorithm

Closes #666, #662, #664. Two commits, one per member issue.

## Outcome

A *malformed* LLM API key cannot leave the process at all. The key must be one Bearer token of visible ASCII, checked
where it is resolved, so a padded, multi-line or non-ASCII value is refused — naming the field, never echoing it —
before any header exists. Every surface that could have shown it therefore cannot: `jobs.error`, `jobs.result`, a log
record, the CLI's stderr, an MCP tool result. Those surfaces render an `httpx` error as its class and status only, so
the endpoint URL and the refused header value go with it; #662's third criterion is met by the same structural rule the
tool routes already use. The redaction suite stops measuring a shared runner's speed and measures the algorithm:
t(2N)/t(N) on the same shape.

Two residuals are stated rather than hidden, because neither is closable inside this change: rows an older build
already wrote, and an endpoint that echoes a key it already holds.

## Constraints

- Issue acceptance (verbatim):
  - #662: a failing test first; an LLM key with trailing whitespace never reaches `jobs.error`, a log line or stderr.
    The LLM key gets the same validation as the embedder key since #660. Errors stored in `jobs.error` or logged by
    `jobs.py` and `indexer.py` are redacted the same way the tool routes already redact them.
  - #664: the linearity check no longer depends on the machine's absolute speed; it still fails on a quadratic
    implementation (shown red first).
- Owner decisions: the malformed key must never reach `jobs.error` or the logs (h11 echoes bad header values; httpx
  error text carries them); mirror #660's K1 rule (no key and no path in errors); #664 becomes a relative check that
  cannot flake on slow runners.
- **Owner ruling of 2026-11-26, after review:** drop the exact-key removal layer drafted earlier (it is outside both
  issues' acceptance and corrupts real data for short or placeholder keys); keep the refusal rule and the structural
  renderer; add the MCP tool result as a sink; leave the connectivity warning alone. Record the two residuals below.
- Repository rules (AGENTS.md): TDD, failing test first; fake HTTP with `http_requests`, never patch `httpx` inside a
  module; test keys from `secrets.token_hex(16)`, never a secret-shaped literal; the AGENTS.md Verify list is the merge
  gate; branch commits carry no co-author line; one `Closes #N` per line in the PR body.
- knowledge-base is PUBLIC: generic wording everywhere; no real key, endpoint or host in code, tests, docs or commits.
- Two commits, conventional and imperative: `fix(llm): …` (#662), `test(redact): …` (#664). No new dependency.
  Smallest change that fully solves both.

### Accepted residuals

1. **Rows an older build already wrote.** `jobs.error` / `jobs.result` persisted by a pre-fix build can still hold a key
   from the very bug #662 describes. No migration: those values are operator diagnostics, a blanket `UPDATE` would
   rewrite evidence, and a `LIKE '%Illegal header value%'` match cannot be validated as "this row holds the key". The PR
   body names the one-off operator step (`DELETE`/`UPDATE` the failed job rows, or re-run the job) for anyone who needs
   the old rows clean.
2. **A key the endpoint echoes back.** The endpoint receives the key by design. KB never reads a non-2xx body
   (`raise_for_status`, then `error_text`) and never puts the key in a prompt, so an echo can only reach
   `jobs.result["raw"]` or an extracted field when the endpoint manufactures one. Detecting it would need a
   text-scrubbing layer that cannot recognise an arbitrary key shape — a `secrets.token_hex(16)` key is 4.0 bits/char,
   below `redact.HIGH_ENTROPY_BITS = 4.3` (`src/knowledge_base/redact.py:75-76`), and the reviewed draft of that layer
   showed it corrupts real data when a local server uses a placeholder key (`ollama`, `EMPTY`, `x`). The endpoint
   already holds the key, so this is the same exposure as the key's transmission.
3. **httpx's own INFO log.** With INFO enabled, httpx logs `HTTP Request: POST <url>` on the `httpx` logger; it names
   the endpoint (never the key) and exists today. Out of scope: this change claims no key on a log line, not the
   absence of an endpoint from a third party's log.

### Every path a key can take, and its guard

| # | Path | Guard | If the guard fails |
| - | ---- | ----- | ------------------ |
| 1 | Header values: `x-api-key` (`src/knowledge_base/llm.py:226-228`), `Authorization: Bearer` (`:246-248`), probe (`:289-291`) | `_key_to_send(spec)` → `settings.require_key(var, raw)` on the **resolved** bytes, at header construction | `ValidationError` naming the field or variable, before `post()`; zero requests |
| 2 | `h11`'s `Illegal header value b'…'` | unreachable once (1) holds | — the class of failure no longer exists for KB's keys |
| 3 | `jobs.error`, `jobs.result["error"]` (`src/knowledge_base/jobs.py:190-225`) | `utils.error_text(exc)` in both failure branches | `HTTPStatusError (HTTP 503)`; the URL is never rendered |
| 4 | `jobs.result["errors"]`, per chunk (`extraction.py:937`) and its FAILED log (`:938-948`), caller-visible through `routes/operations.py:22-44` | `error_text` at the producer | class + status per chunk |
| 5 | `ExtractionError` composed at `extraction.py:750` (and `raw` at `:754`), the resolution warning `:984` | `error_text` at composition | class + status, no URL |
| 6 | CLI stderr: `src/knowledge_base/indexer.py:470-475` | `error_text` in both branches | no endpoint on stderr |
| 7 | MCP tool result: `extract_structure_tool`'s handler (`routes/extraction.py:133-140`) returns `str(e)`, `e.errors`, `e.raw` as a `ToolError` JSON | `error_text(e)` at the handler; the text is rendered at its producer (:750, :937) | no URL and no key in the caller's ToolError |
| 8 | Refusal messages (`llm.py:47-50`, `settings.py:186-189`, `:212`) | name the variable only; `{spec!r}` removed | a path or a key-shaped token in an error |
| 9 | **Residual**: an endpoint's echo in `raw` or an extracted field | none, by ruling (residual 2) | the endpoint already holds the key |
| 10 | **Residual**: rows written by an older build (row 3/4 paths) | none, no migration (residual 1) | the PR body names the operator step |

### Decisions

1. **One home for the key rule.** `settings.require_key(var, raw) -> str` is the single implementation of #660's rule
   (`_KEY_RE` stays private); `_key_source` is refactored onto it with the same message and behaviour, so #660's own
   tests are the regression proof of the move. `llm.py` owns the **policy**: both `_HTTP_LLM_FAMILIES` send the key
   (`ollama` builds no header), the reported name is the variable for an `env:` spec and `llm_api_key` for an inline
   value, and `configure_llm` still stores a supplied key under `ollama` (only an *omitted* key is cleared) — which is
   why the rule runs on the **prospective** provider at configure time and the **stored** provider at every read.
2. **Refused, not trimmed.** A padded inline or `env:` key is refused, not silently stripped: the stored row is what
   the operator sees, and quietly sending a different secret than the row shows is worse than a refusal naming the
   field. (The `file:` source trims, per #660, because a file is not a value anyone reads back.)
3. **An unset variable stays keyless.** `env:KNOWLEDGE_BASE_LLM_API_KEY` with the variable absent resolves to no key,
   so a local keyless `llama-server` keeps working; pinned by a test so no later hardening breaks it.
4. **Structural rendering only.** `utils.error_text(exc)`: an `httpx.HTTPStatusError` reads as `<Class> (HTTP <status>)`,
   any other `httpx.HTTPError` as its class name, everything else unchanged. This is the rule the tool routes already
   apply (`embedder_failure`, `routes/__init__.py:25-35`), and #662's third criterion asks the sinks to match it.
   **No text-scrubbing layer** (owner ruling): an exact-key removal was drafted and reviewed over nine rounds; it is
   outside the acceptance, cannot recognise a key of arbitrary shape anyway, and rewrites real output when a local
   server's key is a word (`ollama`, `none`, `x`). The rule that holds is the refusal in decision 1.
5. **`extraction.py` is in scope** because it composes the leak: `ExtractionError(f"LLM extraction failed: {e}")`
   (`:750`) turns an `httpx` failure into a non-`httpx` exception, so no downstream sink could strip the URL; the
   per-chunk `errors` entry and its FAILED log (`:937-948`) and the resolution warning (`:984`) are the same text on a
   caller-visible `jobs.result`.
6. **The MCP tool result is a sink.** `extract_structure_tool` returns a failed inline extraction's error, its per-chunk
   errors and its `raw` to the caller as JSON (`routes/extraction.py:133-140`); nothing pinned that. It is covered by
   the same rendering plus a test that drives the tool.
7. **#664: a plain ratio bound, no retry.** `time.process_time`, N = 2**19 and 2N, `t(2N)/t(N) < 2.8`. The measured
   evidence decided it: 42 pairs (idle, and pinned to one core with 40 competing busy loops) gave linear ratios of
   1.72–2.30, so a 2.8 bound keeps 22 % headroom and a retry threshold below it never fires; the retry's minimum of two
   samples can also let a noisy quadratic pass, so it was removed rather than tuned. The pure-Python "linear control"
   is dropped too — the seven linear shapes are the positive case, and a separate ~30 ms timing assertion would carry
   the very flake class #664 removes. The quadratic double stays: it is what proves the check fails on a quadratic
   implementation.
8. **Docs**: the reference docs state the refusal, not an allowance. ADR-0018 is a dated decision record, left alone.

## Implementation sequence

- [ ] **Step 0 — baseline, worktree, plan issue (operational).**
  - Worktree: the locked worktree on `fix/666-kb-config-tail`, cut from `origin/master` ce3afc9; `uv sync` run.
  - Baseline: `uv run pytest tests/ -q -m "not slow"` → all passed, so any later red is this branch's.
  - Plan issue: #667 exists and currently carries the **r9** text. One plan has one plan issue: after the owner approves
    this revision, its **body is replaced** with this file's text (`gh issue edit --body-file`), never a second issue.
    Until then it is not touched.
  - Commit: none.

- [ ] **Step 1 — one home for the key rule** (`src/knowledge_base/settings.py`)
  - Files: `src/knowledge_base/settings.py` (`require_key` added; `_key_source` refactored onto it; `__all__` entry).
  - Failing test: none, deliberately — this step must not change behaviour. Its regression assertion is #660's own
    suite: `uv run pytest tests/test_settings.py -q -k "key"` shows the same pass count as on `origin/master`
    (including `test_an_embedder_key_the_http_client_would_echo_is_refused` and
    `test_a_padded_env_key_still_starts_a_provider_that_never_sends_it`).
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
    `_key_source`: `if provider in _OPENAI_FAMILIES: require_key(API_KEY_VAR, os.environ[API_KEY_VAR])` — identical
    message, so no existing test moves.
  - Green: `uv run pytest tests/test_settings.py -q` → all pass; `uv run ruff check src/knowledge_base/settings.py`.
  - Commit: with steps 2–4.

- [ ] **Step 2 — the LLM key gets the rule at configure time and at use time** (`src/knowledge_base/llm.py`,
  `tests/test_llm.py`)
  - Files: `src/knowledge_base/llm.py`, `tests/test_llm.py` (`import secrets` added).
  - Failing tests first:
    1. `test_a_padded_llm_key_is_refused_naming_the_field` — parametrized over `("openai_compat", "anthropic_compat")`
       × `["{k} ", " {k}", "{k}\r", "{k}\nmore", "{k}\u00e9", "{k}\x01"]`, `k = secrets.token_hex(16)`; asserts
       `pytest.raises(ValidationError, match=r"^llm_api_key must")`, `k not in str(refused.value)`,
       `http_requests == []`, `_config_rows(kb_conn) == before`.
    2. `test_a_padded_env_llm_key_is_refused_before_any_request` — `monkeypatch.setenv("KNOWLEDGE_BASE_LLM_API_KEY",
       f"{k} ")`, a `cfg` dict carrying `"api_key": "env:KNOWLEDGE_BASE_LLM_API_KEY"`, `_llm_call("p", cfg=cfg)` →
       `ValidationError` matching `^KNOWLEDGE_BASE_LLM_API_KEY must`, `k` absent, `http_requests == []`.
    3. `test_an_unset_llm_key_variable_stays_keyless` — the variable deleted, the same `env:` spec: the POST goes out
       and `"Authorization" not in http_requests[0].headers`. Green on arrival: it pins decision 3.
    4. `test_a_stored_padded_key_is_refused_when_the_provider_switches_to_an_http_family` — store a padded key under
       `provider="ollama"` (allowed there: it is never sent, and `configure_llm` stores a supplied key even for ollama),
       then `configure_llm(conn, provider="openai_compat", base_url=…)` with no `api_key` → refused, `_config_rows`
       unchanged, `http_requests == []`.
  - Red: `uv run pytest tests/test_llm.py -q -k "padded or keyless"` → **14 failed, 1 passed** (12 cases from test 1,
    tests 2 and 4 one each; test 3 passes on arrival).
  - Change, exact:
    ```python
    def _key_to_send(spec: str | None) -> str | None:
        """The key *spec* resolves to now, refused unless it is one Bearer token of visible ASCII, or None.

        The last gate before a header value: ``env:`` reads its variable at call time, and a ``cfg`` read earlier can
        be stale. An unset variable is no key, so a keyless endpoint stays usable (#662).
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
    Call sites: `_get_llm_config` passes `prov` (`:62`); `configure_llm` passes the **prospective** provider at `:363`
    and `:366` (what makes test 4 refuse before the write); the three header sites (`:226`, `:246`, `:289`) replace
    `_resolve_api_key(...)` with `_key_to_send(...)`. Import `require_key` from `.settings`. Removing `{spec!r}` keeps
    the existing tests' `match="KNOWLEDGE_BASE_LLM_API_KEY"`.
  - Green: `uv run pytest tests/test_llm.py -q` → all pass, including `test_llm_key_spec_allowlist_*` and
    `test_a_stored_foreign_key_spec_is_refused_before_any_request`.
  - Commit: with steps 1, 3, 4.

- [ ] **Step 3 — no sink renders an httpx error's own text, and the tool result is covered** (`src/knowledge_base/utils.py`,
  `jobs.py`, `extraction.py`, `indexer.py`, `routes/extraction.py`; their tests)
  - Failing tests first:
    1. `tests/test_jobs.py::test_a_failed_job_stores_no_endpoint_and_no_header_value` — parametrized
       `("status", "protocol")` through the `_run_once` helper (`tests/test_jobs.py:287-301`): a fake dispatch raising
       `httpx.HTTPStatusError(f"Server error '503' for url '{url}'", request=…, response=…)` built on
       `url = "http://llm.example.test:1234/v1/chat/completions"`, and `httpx.LocalProtocolError(f"Illegal header value
       b'Bearer {key}'")`, `key = secrets.token_hex(16)`. Asserts `job["status"] == "failed"`,
       `job["error"] == "HTTPStatusError (HTTP 503)"` for the status case, and for both: `"llm.example.test" not in
       job["error"]`, `key not in job["error"]`, the same two absences in `caplog.text` at `ERROR` on
       `knowledge_base.jobs`, and `key not in json.dumps(job["result"])`.
    2. `tests/test_extraction.py::test_a_failed_single_pass_call_names_no_endpoint` — `_extract_single_pass` with
       `knowledge_base.extraction._llm_call` patched to raise that `HTTPStatusError`: the `ExtractionError` message holds
       `HTTPStatusError (HTTP 503)` and not the host.
    3. `tests/test_extraction.py::test_a_failed_chunk_is_recorded_without_the_endpoint` — the map-reduce path with every
       chunk failing (scaffolding of `test_map_reduce_logs_failed_chunk`, `tests/test_extraction.py:1242`): the host
       appears in neither `json.dumps(exc.errors)` nor `caplog.text`, and `errors[0]["error"] == "HTTPStatusError (HTTP
       503)"`.
    4. `tests/test_indexer.py::test_cli_stderr_never_names_the_embedder` — `stub_document_embedder.down = 503`,
       `_exit_of(["--db", str(db), "ingest", str(doc)])` (`:686`) → exit 1, `names_the_embedder(err)` false,
       `HTTPStatusError (HTTP 503)` present (`tests/conftest.py:260-277`).
    5. `tests/test_routes_extraction.py::test_the_inline_extraction_tool_names_no_endpoint` — the tool path (row 7):
       modeled on `test_extract_structure_tool_short_doc_passthrough` (`tests/test_routes_extraction.py:296-317`) but with
       `knowledge_base.extraction._llm_call` patched to raise that `HTTPStatusError` on a one-chunk paper, so the real
       composition at `:750` runs; asserts `pytest.raises(ToolError)`, the ToolError's JSON holds `error` starting with
       `LLM extraction failed: HTTPStatusError (HTTP 503)`, no `llm.example.test`, and no key.
  - Red: those six cases → 6 failed (today the stored, printed and returned text carries the URL; the protocol case
    carries the key verbatim).
  - Change, exact — `utils.py`, after `_resolve_api_key`, plus `"error_text"` in `__all__`:
    ```python
    def error_text(exc: BaseException) -> str:
        """What *exc* says where a caller or a log reads it: an httpx error's class, never its own text.

        httpx puts the endpoint URL in a status error's message, and h11's refusal reaches us as
        ``httpx.LocalProtocolError`` with the refused header value quoted in its text, so neither is rendered. Every
        other error is KB's own text, which names a variable rather than a value (#660, #662), and stays whole: a
        job's ``error`` is the operator's only diagnosis.
        """
        if isinstance(exc, httpx.HTTPStatusError):
            return f"{type(exc).__name__} (HTTP {exc.response.status_code})"
        if isinstance(exc, httpx.HTTPError):
            return type(exc).__name__
        return str(exc)
    ```
    `jobs.py`: in both `except` branches `detail = error_text(exc)`, used for `jobs.error`, `result_data["error"]` and
    `logger.error("Job %d failed: %s", job_id, detail)`. `indexer.py`: `error_text(exc)` in both branches of `main`'s
    handler (byte-identical for KB's own errors). `extraction.py`: `error_text(e)` at `:750`, `:937`, in the FAILED log
    at `:938-948` and the resolution warning at `:984`; extend the existing `from .utils import …` line.
    `routes/extraction.py`, in `extract_structure_tool`'s `except ExtractionError` handler: `result = {"error":
    error_text(e)}` — a no-op for an `ExtractionError` (its text is already rendered at `:750`, `:937`), applied at the
    sink so the caller-facing JSON is built by the same rule; `errors` and `raw` are passed as produced (residual 2). Add
    `error_text` to that module's imports.
  - Green: `uv run pytest tests/test_jobs.py tests/test_extraction.py tests/test_indexer.py tests/test_routes_extraction.py
    -q` → all pass, and the diagnostic value is kept where it is real:
    `test_a_job_s_uncommitted_writes_land_only_with_its_completion` still finds `"boom"` in `job["error"]`,
    `tests/test_extraction.py:544` still finds `"empty response"` in `errors[0]["error"]`, and
    `test_extract_structure_tool_short_doc_extraction_error` still sees `error`, `errors` and `raw`.
  - Commit: with steps 1, 2, 4.

- [ ] **Step 4 — the docs state a refusal, not an allowance** (part of commit 1)
  - Files and exact additions are under `## Documentation`. Green: `uv run --group docs sphinx-build -W -E -q -b html
    docs docs/_build` → exit 0, no output.
  - Commit: with steps 1–3.

- [ ] **Step 5 — #664: linearity as a ratio** (`tests/test_redact.py` only)
  - Failing test first, written against a helper that does not exist yet:
    ```python
    def test_the_linearity_check_rejects_a_quadratic_shape():
        """The check is the deliverable: a quadratic detector must raise, not merely be slow."""
        with pytest.raises(AssertionError, match=r"t\(2N\)/t\(N\)"):
            _assert_linear(_rescan, lambda n: "x" * n, small=2**13)
    ```
  - Red: `uv run pytest tests/test_redact.py -q -k quadratic` → 1 failed (`NameError: _assert_linear`); the honest red
    for a test-hardening issue. The one-off demonstration for the PR body: patch `tests.test_redact.scrub` (the name the
    test measures) with a quadratic double and run the parametrized test → red on every shape.
  - Change, exact — replace `tests/test_redact.py:223-241`:
    ```python
    #: The smaller size the linearity check reads. N + 2N is less scrub work than the single 2 MiB run it replaces.
    _SMALL = 2**19
    #: t(2N)/t(N) = 2 + 2Q/(Q+L) for a quadratic term Q and a linear one L at N, so this bound fires exactly when Q > L.
    #: Measured: linear shapes 1.72-2.30 over 42 pairs (idle, and 40 competing loops on one core); the #592
    #: regression, run at this N from the pre-fix module, 3.52; a pure quadratic double 3.85+.
    _MAX_GROWTH = 2.8

    _HOSTILE = {  # the seven hostile shapes, each a builder of n bytes; the ids are the contract
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

    def _assert_linear(run, make, *, small: int = _SMALL) -> None:
        """Assert *run* over *make*'s shape grows about linearly: t(2N)/t(N) < ``_MAX_GROWTH``.

        A ratio of the same work at two sizes is a property of the algorithm; the absolute bound this replaces was a
        property of the runner (8.2 s against 6 s on a shared runner, #664). CPU time keeps a noisy neighbour's
        descheduling out, and each shape is built outside the clock.
        """
        t_small = _cpu_seconds(run, make(small))
        t_large = _cpu_seconds(run, make(small * 2))
        ratio = t_large / max(t_small, 1e-6)
        assert ratio < _MAX_GROWTH, f"t(2N)/t(N) = {ratio:.2f} at N = {small} bytes: a linear pass doubles"

    def _rescan(text: str) -> None:
        """A deliberately quadratic detector: one full scan per character."""
        for _ in range(len(text)):
            text.count("x")

    scrub("warm")  # the regex module builds its caches lazily; one warm-up keeps them out of the first timed run

    @pytest.mark.parametrize("make", list(_HOSTILE.values()), ids=list(_HOSTILE))
    def test_redaction_is_linear_on_hostile_input(make):
        """The ids are #664's contract: ``[url-authority]`` was the shape that failed on a shared runner."""
        _assert_linear(scrub, make)
    ```
  - Green: `uv run pytest tests/test_redact.py -q` → all pass (7 shapes + the double); the file's scrub time is ~4.9 s
    against today's ~6.5 s (measured), and the double costs 0.13 s.
  - Commit: `test(redact): measure linearity as a ratio, not a wall-clock bound`.

- [ ] **Step 6 — gate, PR, review (operational).** Run `## Verification` at the final head with a clean tree; open one PR
  to `master` whose body carries `Closes #666`, `Closes #662`, `Closes #664`, `Classification: Bug fix
  (user-confirmed)`, `Execution path: Standard`, `Planning tier: Super`, `Post-implementation review: Full (Default)`,
  Refs dutiona/infra#37, every gate result, the two accepted residuals, and the `Collateral findings` section. Then
  `/super-review` at the Full gate and `/finish-pr` to READY.
- [ ] **Step 7 — merge (owner only).** Michaël squash-merges; the squash commit carries exactly one co-author line. This
  plan is archived on plan issue #667 at `/finish-pr` and its files leave the branch there.

## Documentation

- `README.md:138` — the `KNOWLEDGE_BASE_LLM_API_KEY` row: "the only `env:` source an LLM key may name; one Bearer token
  of visible ASCII (as the embedder key since #660)".
- `docs/usage/structured-extraction.md:155-156` — after the inline/`env:` sentence: "For `openai_compat` or
  `anthropic_compat` the key is one token of visible ASCII: a padded, multi-line or non-ASCII value is refused naming
  the field, and never echoed, instead of failing inside the HTTP client."
- `docs/usage/structured-extraction.md:198-200` — in the plaintext-at-rest paragraph: "A stored value that is not one
  token is refused at every read, so re-run `configure_llm` if an older build accepted a padded key."
- `docs/reference/mcp-tools.md:683` — append to the `configure_llm_tool` paragraph: "; a key for an HTTP family is one
  Bearer token of visible ASCII, refused naming the field otherwise."
- `docs/reference/mcp-tools.md:996-1008` — after `get_job_status_tool`'s **Returns**: "A failed job's `error` names the
  failure class and, for an HTTP failure, its status (`HTTPStatusError (HTTP 503)`); it never carries the endpoint's URL
  or an httpx error's own text. `result.errors` holds one such message per failed chunk."
- `docs/reference/mcp-tools.md`, `extract_structure_tool`'s section — one sentence: a failed inline extraction's
  `ToolError` carries the same rendered class and status, never the endpoint or an httpx text.
- `docs/reference/schema.md:281-283` — append: "An LLM key for an HTTP family holds one Bearer token of visible ASCII,
  refused at configure time and again before each request."
- Docstrings: `configure_llm` (`src/knowledge_base/llm.py:345-358`) gains one sentence for the visible-ASCII rule.
- Not changed, deliberately: `docs/design/adr/0018-provider-abstraction.md:363` is a dated decision record.

## Testing

| Criterion | Tests (step) |
| --- | --- |
| #662 a padded key never reaches a job error, a log or stderr | `test_a_padded_llm_key_is_refused_naming_the_field`, `test_a_padded_env_llm_key_is_refused_before_any_request` (2), `test_a_failed_job_stores_no_endpoint_and_no_header_value`, `test_cli_stderr_never_names_the_embedder` (3) |
| #662 the LLM key gets #660's validation | the same refusals (2); #660's own `test_settings.py -k key` unchanged (1) |
| #662 sinks redacted as the tool routes do | `test_a_failed_single_pass_call_names_no_endpoint`, `test_a_failed_chunk_is_recorded_without_the_endpoint`, `test_the_inline_extraction_tool_names_no_endpoint` (3) |
| #662 a stored padded key fails closed | `test_a_stored_padded_key_is_refused_when_the_provider_switches_to_an_http_family` (2) |
| #662 a keyless endpoint still works | `test_an_unset_llm_key_variable_stays_keyless` (2) |
| #664 no absolute bound | the ratio helper replaces `time.monotonic() < 6` (5) |
| #664 still red on a quadratic shape | `test_the_linearity_check_rejects_a_quadratic_shape` (5) |
| #664 the known regression is caught at this size | the pre-fix module (`git show a87f6c8^`) run in a probe at N = 2**19: 3.52 against 2.03; recorded in the plan and the PR body, not committed as a fixture |

Excluded, with reasons: an endpoint's echo of its own key (residual 2 — accepted, not testable by a rule KB can hold);
rows written by an older build (residual 1 — an operator step named in the PR body); the httpx INFO log line (residual
3); the connectivity warning, which keeps naming the caller's own endpoint by ruling (it is the caller's argument, and
`_sanitize_url` already strips userinfo).

## Verification

Run at the PR's final head, clean tree, in this order (AGENTS.md's merge gate), quoting each result in the PR:

```
uv lock --check
uv sync
uv run ruff check src/ tests/            # zero warnings
uv run ruff format --check src/ tests/   # no reformats
uv run pytest tests/ -q -m "not slow"    # all pass
uv sync --all-groups
uv run basedpyright src/ tests/          # 0 errors
uv run --group docs sphinx-build -W -E -q -b html docs docs/_build   # exit 0, no output
```

- Every heavy step (pytest, sphinx) runs under the host's heavy lock and memory cap, as the lane requires.
- The image step is N/A: `git diff --quiet origin/master...HEAD -- Containerfile .containerignore pyproject.toml
  uv.lock .python-version` exits 0 — nothing this PR adds is a build input.
- CI status is not evidence (`.github/workflows/ci.yml` does not run for this repository).
- Quote in the PR, besides the eight results: `uv run pytest tests/test_llm.py -q -k "padded"` failing at the parent
  commit and passing at head; `uv run pytest tests/test_redact.py -q -k quadratic` failing before step 5 and passing
  after; the one-off quadratic-`scrub` run (every hostile shape red); and the #664 ratio measurements above.
- tesseract-ocr is installed on this host; the OCR tests run rather than skip.
