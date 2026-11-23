# feat(config): read the embedder API key from a file, like memory-engine (#660)

## Outcome

`KNOWLEDGE_BASE_EMBED_API_KEY_FILE` names a file holding the embedder's key. KB reads it once, at start-up, trims
surrounding whitespace, and refuses to start (exit 78 for the server, naming the variable, never the file's content or
path) when the file is unreadable, empty, not one token of visible ASCII, or set beside `KNOWLEDGE_BASE_EMBED_API_KEY`.
`config.embed_api_key` records `file:KNOWLEDGE_BASE_EMBED_API_KEY_FILE`. Two guards land before the feature: no LLM
key spec can name a file, and an environment key that the HTTP client would echo in an error is refused at start.

## Constraints

Issue acceptance (verbatim):
- A key-file setting mirrors memory-engine's: the server reads the key from the named file at start-up, with surrounding whitespace trimmed.
  - An unreadable or empty file is a start-up error that names the setting, never the file's content.
  - Setting both the key variable and the file variable is refused.
- The key never appears in logs, errors or `--help` output (tests).
- The configuration reference documents the setting beside the existing key variable.
- Tests, failing first: a key read from the file is used; a missing or empty file refuses to start; both settings set is refused.

Owner decisions (settled):
- Purpose: a deployment keeps the key in a mounted file so no shell sits in front of KB and the key is not in KB's process environment (children inherit the environment).
- Mirror memory-engine's option: read once at start; unreadable/empty -> start-up error naming the setting; both set -> refused. `KNOWLEDGE_BASE_EMBED_API_KEY` -> `KNOWLEDGE_BASE_EMBED_API_KEY_FILE`.
- Small and localized: settings.py, utils.py (and llm.py if a guard needs it), docs, tests.

Repository rules: TDD; fake HTTP with `http_requests` at the transport, never patch httpx; no secret-shaped literal in a
test (keys come from `secrets.token_hex(16)`); the AGENTS.md "Verify" list is the merge gate.

## Trust boundaries

| Input (who sets it) | What it could reach without a guard | Guard (item) | Fails as |
| --- | --- | --- | --- |
| `configure_llm(api_key=…)` (any stdio MCP client), `llm.py:362-363` | once the shared `utils._resolve_api_key` (`utils.py:396-408`) learns `file:`, the embedder key, sent to an LLM URL the client chose | `_check_llm_key_spec` (`llm.py:47-50`) refuses every `file:` spec (1) | `ValidationError`, nothing stored, no request |
| `config.llm_api_key` row (any process that writes the database) | same | the same check on every read, `llm.py:59-62` (1) | `ValidationError` before any request |
| `config.embed_api_key` row (same) | nothing: the provider takes the process's declaration, `embed_swap.py:76-88` | existing (#569), pinned by `tests/test_embeddings.py:529-554` | row rewritten at start, `db.py:1452-1455` |
| key value, file or env (operator accident: CRLF env file, trailing space) | h11 rejects the header and raises `Illegal header value b'Bearer <key>'` (h11 `_abnf.py:55-57`, `_headers.py:166`); httpx encodes headers as ASCII (`_models.py:82`), so a non-ASCII key raises a `UnicodeEncodeError` naming the character. Sinks: `indexer.py:473-474` prints `{exc}`; `jobs.py:215-222` stores `str(exc)` in `jobs.error` and logs it. Tool routes already show only the class name (`routes/__init__.py:25-35`) | the rule in decision 1, checked at start for both sources (2, 3) | start-up refusal naming the variable |
| `KNOWLEDGE_BASE_EMBED_API_KEY_FILE` holding the key itself (operator swaps the two variables) | `OSError`'s text, which holds the filename, i.e. the key | the error carries only the `OSError` class name and is raised `from None` (3) | start-up refusal naming the variable |
| file bytes that are not UTF-8 | a `UnicodeDecodeError`, whose text names the byte | no decode can fail: bytes are stripped, decoded as latin-1 (total), then the rule refuses anything past 0x7E (3) | start-up refusal naming the variable |

Accepted residuals: the path stays in the environment, so a child process running as KB's user inherits the path and
could read the file. Before, it inherited the key itself, so this is strictly less exposure. The key stays in process
memory for the life of the process, as an environment key does in `os.environ`.

## Decisions

1. **Content rule:** strip ASCII whitespace from both ends (the issue's rule), then require `[!-~]+` (one token of
   visible ASCII). memory-engine (`crates/memory-engine-embed/src/key_file.rs:28-48`) strips one `\n` and refuses
   control characters. Its rule lets `k3y ` through, and the issue's rule alone lets `k3y\nmore` through, and h11
   echoes both. RFC 6750's `b64token` fits inside `[!-~]+`.
2. **Same rule for the env key, where the key is sent:** applied to the raw value, which is what `_resolve_api_key`
   sends (no trimming), and only for the providers that send it, `openai_compat`, `openai` and `llama.cpp`
   (`_OPENAI_FAMILIES`, `settings.py:93`; `OpenAICompatProvider`, `embeddings.py:505-530`). Today a CRLF env file puts
   the key in `jobs.error` and on the CLI's stderr. For those providers such a key already fails every request, so
   refusing it at start breaks no working deployment; the one exception is a key with an inner space, and no real
   bearer token has one. `ollama` and `onnx` never send the key, so a padded env key there keeps starting (review
   round 1). The file rule (decision 1) applies under every provider: the setting is new, so it breaks nothing.
3. **Cache:** `functools.cache` on `settings._read_key_file(path: str) -> str`, keyed by the path. The key source is
   the last rule `load_embed_settings` checks (after the batch size), so a bad declaration is reported first and a
   key problem only once everything else is valid.
   `check_environment` does the one read. Every later `load_embed_settings` (one per embedding call,
   `embed_swap.py:76`) and every request's resolve hits the cache. Failures are not cached. Tests vary the path with
   `tmp_path`, which never repeats, so no cache-clearing fixture is needed.
4. **Spec:** `file:KNOWLEDGE_BASE_EMBED_API_KEY_FILE`. Like the `env:` spec, it names the variable, so the row, the
   bind warning and every repr hold neither the path nor the secret. The resolver matches this exact string, not a
   prefix.
5. **LLM guard:** refuse `spec.startswith(("env:", "file:")) and spec != "env:KNOWLEDGE_BASE_LLM_API_KEY"`. This check
   already stands in front of every LLM key: it is what stops `env:KNOWLEDGE_BASE_EMBED_API_KEY` today. Refusing the
   whole prefix fails closed. A second mechanism for `file:` alone (resolving only in `embeddings.py`) would add a
   module, not protection.
6. **Errors name the variable, never the path:** this deviates from memory-engine, which names the path, because of
   the swapped-variable case above. Every message starts with the variable, following `load_embed_settings`'s
   contract (`settings.py:168`).
7. **Empty values:** blank counts as unset (`settings.py:122-124`, README "Empty values count as unset"). So a blank
   `KNOWLEDGE_BASE_EMBED_API_KEY` beside a file is not "both set". A compose or quadlet template that leaves `KEY=`
   blank must start.
8. **onnx / ollama:** a declared file is read, checked by decision 1's rule and recorded, as the env key is recorded
   today (`settings.py:221`); neither provider sends a key, so decision 2's env-key rule does not apply to them.
9. **Rejected:**
   - a size cap: an endless file is the operator's own start-up failure, not a disclosure;
   - re-read or rotation: the owner settled read once; a new key needs a restart;
   - a generic `file:` scheme: it would let any spec read any file;
   - an LLM key file: not asked for;
   - popping `_FILE` from `os.environ`: every reader looks at `os.environ` at call time (`settings.py:5-8`), and the
     path is not the secret.
10. **ADR-0018:** add one `#660:` sentence to the #569 amendment's "One key source (§4)" bullet (`:360-362`), whose
    "read only from `KNOWLEDGE_BASE_EMBED_API_KEY`" becomes false. Leave the rest of the ADR alone.

## Implementation sequence

- [ ] **0. Worktree, plan issue, baseline (operational).**
  - Worktree: the existing locked worktree on `feat/660-embed-key-file`, cut from `origin/master`; it has no `.venv` yet.
  - Plan issue: exactly one, published from this file by `finish-pr`'s `archive-plan-issue.sh` before the approval is
    asked; `/finish-pr` reuses it. Never a second one.
  - Baseline: `uv sync && uv run pytest tests/ -q -m "not slow"`, expecting all passed, so that any later red is this
    branch's. Branch commits carry no co-author line.

- [ ] **1. Guard: no LLM key spec names a file.** Files: `src/knowledge_base/llm.py`, `tests/test_llm.py`.
  - Failing tests:
    - Rename `test_llm_key_spec_allowlist_refuses_other_env_specs` (`:788`) to `…_refuses_other_indirect_specs` and add
      `file:KNOWLEDGE_BASE_EMBED_API_KEY_FILE` and `file:/etc/hostname` to its specs.
    - Parametrize `test_a_stored_foreign_key_spec_is_refused_before_any_request` (`:810`) over
      `env:KNOWLEDGE_BASE_EMBED_API_KEY` and `file:KNOWLEDGE_BASE_EMBED_API_KEY_FILE`.
  - Red: `uv run pytest tests/test_llm.py -q -k key_spec` shows the `file:` cases failing (DID NOT RAISE, or a probe
    recorded in `http_requests`).
  - Change: decision 5's rule in `_check_llm_key_spec`; the message is unchanged; the docstring adds "and no file".
  - Green: the same command, all passed.
  - Commit: `feat(llm): refuse a file: key spec before any resolver reads one`.

- [ ] **2. Guard: refuse an env key the HTTP client would echo.** Files: `src/knowledge_base/settings.py`,
  `tests/test_settings.py`.
  - Failing tests:
    - `test_an_embedder_key_the_http_client_would_echo_is_refused`, under the suite's `openai_compat` declaration,
      parametrized over `{k} `, ` {k}`, `{k}\r`, `{k}\nmore`, `{k}é` and `{k}\x01`, with
      `k = secrets.token_hex(16)`. It asserts that `load_embed_settings()` raises `ValidationError` matching
      `KNOWLEDGE_BASE_EMBED_API_KEY must` and that `k` is not in its text.
    - `test_a_padded_env_key_still_starts_a_provider_that_never_sends_it`: an `ollama` declaration with `{k} ` loads,
      `api_key_source == "env:KNOWLEDGE_BASE_EMBED_API_KEY"` (green on arrival; it pins decision 2's scope).
  - Red: `uv run pytest tests/test_settings.py -q -k would_echo` shows 6 failed.
  - Change:
    - add `_KEY_RE = re.compile(r"[!-~]+")`;
    - add `_key_source() -> str | None`, which `load_embed_settings` uses for `api_key_source`;
    - when `_get(API_KEY_VAR)` is set, the provider is in `_OPENAI_FAMILIES` and
      `_KEY_RE.fullmatch(os.environ[API_KEY_VAR])` fails, raise
      `ValidationError(f"{API_KEY_VAR} must hold the key alone: visible ASCII characters, no whitespace")`;
    - `load_embed_settings` computes `api_key_source=_key_source(provider)` after the batch-size rule (decision 3).
  - Green: `uv run pytest tests/test_settings.py -q -k "would_echo or never_sends"`, 7 passed. Then `uv run pytest tests/test_settings.py tests/test_embeddings.py
    tests/test_embed_swap.py tests/test_db.py -q`, all passed: every existing test key is visible ASCII.
  - Commit: `fix(settings): refuse an embedder key the HTTP client would echo in an error`.

- [ ] **3. Feature: `KNOWLEDGE_BASE_EMBED_API_KEY_FILE`.**
  - Files: `src/knowledge_base/settings.py`, `src/knowledge_base/utils.py`; docstrings only in
    `src/knowledge_base/embed_swap.py` (`:36-39`) and `src/knowledge_base/embeddings.py` (`:137-138`, `:319-320`).
    Tests: `tests/conftest.py`, `tests/test_settings.py`, `tests/test_utils.py`, `tests/test_embeddings.py`,
    `tests/test_server.py`, `tests/test_db.py`. Tests spell the variable literally, because the name is the contract.
  - Failing tests (every name contains `key_file`):
    - `conftest.py:102-116`: the variable joins the unset list, so a developer's shell cannot leak a file in.
    - `test_settings.py`: add the variable to `_EMBED_VARS` (`:32`).
      - `test_a_key_file_declares_the_file_source`: files `{k}\n` and ` {k}\r\n` give `api_key_source ==
        "file:KNOWLEDGE_BASE_EMBED_API_KEY_FILE"`, and neither `k` nor the path appears in `repr`.
      - `test_a_bad_key_file_refuses_naming_the_variable`, over: missing; a directory; empty; whitespace only;
        `{k}\nmore`; `{k} {k}`; `k` + `b"\xff"`; a UTF-8 BOM before `k`; and an `ollama` declaration with an empty
        file (decision 8). Each case gives `check_environment()` raising `ValidationError`
        matching the variable. Neither `k`, `more` nor the path is in the message, and no chained exception shows:
        `__cause__ is None`, and `__context__` is either `None` or suppressed.
      - `test_a_key_file_set_to_the_key_never_echoes_it`: the variable holds `k` itself.
      - `test_the_key_and_the_key_file_together_are_refused`: both set is refused, naming both variables. A blank
        `KNOWLEDGE_BASE_EMBED_API_KEY` beside a file is accepted.
    - `test_utils.py`: `test_resolve_api_key_reads_the_key_file_once`. The file holds key A; resolve; overwrite the
      file with B; resolving the exact spec still gives A. `_resolve_api_key(f"file:{path}")` returns that string as
      is, so no file is read.
    - `test_embeddings.py`: `test_a_key_file_key_is_sent_and_never_logged`, beside `:529`. Use `open_declared_db` and
      then `_embed_with_config` under `caplog.at_level("DEBUG")`. Assert
      `http_requests[0].headers["Authorization"] == f"Bearer {k}"`, and that `k` is in neither `caplog.text` nor any
      `config` value.
    - `test_server.py`:
      - `test_a_bad_key_file_setting_exits_78_before_the_database`, modeled on `:146`, over: missing file; empty
        file; both set. Expect exit 78, stderr naming the variable and without `k`, `mcp.run` not called, and no
        database or lock file.
      - `test_help_never_shows_the_key_file_key`: with a key file declared, `server.main(["--help"])` and
        `indexer.main(["--help"])` exit 0, and `k` is absent from stdout and stderr. This test is green on arrival:
        argparse reads no environment. It pins the `--help` criterion.
    - `test_db.py`: `test_bind_records_the_key_file_source_never_its_path`. Call
      `_seeded(tmp_path, monkeypatch, api_key=None, api_key_file=str(path))`, then `bind_embed_config` under caplog.
      The row becomes the file spec and the warning names `embed_api_key`. Neither `k` nor the path is in caplog or
      any `config` value.
  - Red: `uv run pytest tests/test_settings.py tests/test_utils.py tests/test_embeddings.py tests/test_server.py
    tests/test_db.py -q -k key_file` shows every new test failing except the `--help` one.
  - Change in `settings.py`:
    - `API_KEY_FILE_VAR = "KNOWLEDGE_BASE_EMBED_API_KEY_FILE"`, added to `__all__`.
    - `@functools.cache _read_key_file(path: str) -> str`:
      - read the bytes with `Path(path).read_bytes()`;
      - on `OSError` raise `ValidationError(f"{API_KEY_FILE_VAR} names a file KB cannot read
        ({type(exc).__name__})") from None`;
      - set `key = raw.strip().decode("latin-1")`;
      - if `key` is empty, raise `"{API_KEY_FILE_VAR} names an empty file"`;
      - if `_KEY_RE` does not match, raise `"{API_KEY_FILE_VAR} names a file that does not hold the key alone: one
        line of visible ASCII characters"`.
    - `embed_key_from_file() -> str`, added to `__all__`: `_read_key_file(path)` for `_get(API_KEY_FILE_VAR)`; when
      the variable is unset it raises `ValidationError(f"{API_KEY_FILE_VAR} is not set but the declared key source
      names it")`, so a request never silently goes out without the key (fails closed). Test in `test_utils.py`:
      `test_resolve_api_key_refuses_the_key_file_spec_without_the_variable`.
    - `_key_source(provider: str)`:
      - if both variables are set, raise `"{API_KEY_FILE_VAR} and {API_KEY_VAR} are both set; set one"`;
      - if only the file is set, read it and return `f"file:{API_KEY_FILE_VAR}"`;
      - if only the env key is set, apply item 2's check (sending providers only) and return `f"env:{API_KEY_VAR}"`;
      - otherwise return `None`.
    - Update the `EmbedSettings` docstring to "the `env:` or `file:` spec naming the variable, never the path or the
      secret".
  - Change in `utils.py`: import `API_KEY_FILE_VAR` and `embed_key_from_file` beside `host_networks` from
    `knowledge_base.settings` (`utils.py:26`; settings imports nothing from utils, so no cycle). `_resolve_api_key`'s
    first branch is `raw == f"file:{API_KEY_FILE_VAR}"`, which returns `embed_key_from_file()`. The docstring says that this one spec reads a file, and that any other `file:` string is
    an inline value, which the LLM guard refuses.
  - Green: the same command, all passed. Then `uv run pytest tests/ -q -m "not slow"`, all passed.
  - Commit: `feat(settings): read the embedder key from KNOWLEDGE_BASE_EMBED_API_KEY_FILE`.

- [ ] **4. Documentation.** Files: `README.md`, `docs/getting-started/installation.md`, `docs/reference/schema.md`,
  `docs/usage/ingesting-documents.md`, `docs/design/adr/0018-provider-abstraction.md`. The text is listed under
  Documentation below.
  - Red: N/A, prose; sphinx `-W` is the check.
  - Green: `uv run --group docs sphinx-build -W -E -q -b html docs docs/_build` exits 0 with no output. Then
    `rg -c KNOWLEDGE_BASE_EMBED_API_KEY_FILE README.md docs/` shows a count in each of the five files, and
    `rg -n "only ever the declared .env:|read only from|Always .env:KNOWLEDGE_BASE_EMBED_API_KEY" src docs README.md`
    prints nothing (no stale "the env var is the only key source" claim survives; `README.md:227`'s upgrade note,
    which moves the key from `OPENAI_API_KEY`, stays true and is left alone).
  - Commit: `docs: document KNOWLEDGE_BASE_EMBED_API_KEY_FILE beside the embedder key`.

- [ ] **5. Gate and PR (operational).** Run Verification at the final head with a clean tree. Open one PR to `master`
  titled `feat(config): read the embedder API key from a file, like memory-engine`. The body says `Closes #660`,
  quotes every gate result, and lists decisions 1, 2, 6 and 10 for review.

- [ ] **6. `/super-review` (operational)** on the PR. Fix the findings in new commits, then rerun the gate at the new
  head.

- [ ] **7. Merge (owner only).** The owner squash-merges. The squash commit carries exactly one co-author line.

## Documentation

- `README.md:135`: the `_API_KEY` row adds "visible ASCII, no whitespace; refused beside `_API_KEY_FILE`". Add a new
  row after it:
  - Variable: `KNOWLEDGE_BASE_EMBED_API_KEY_FILE`.
  - Required: optional; refused beside `_API_KEY`.
  - Value: a file holding the embedder's key, read once at start with surrounding whitespace trimmed. It must hold
    one token of visible ASCII; an unreadable or empty file, or any other content, stops the start naming the
    variable. `config` records only `file:KNOWLEDGE_BASE_EMBED_API_KEY_FILE`. A new key takes a restart.
- `docs/getting-started/installation.md:161-164`: rewrite the sentence "the key `KNOWLEDGE_BASE_EMBED_API_KEY`
  through `--env-file` or a secret" (`:163`) to prefer mounting the key as a file (a podman secret, `Secret=` in a
  quadlet), readable by UID 10001, and pointing `KNOWLEDGE_BASE_EMBED_API_KEY_FILE` at its in-container path. That
  keeps the key out of the unit file and out of the container's environment, which children inherit. `--env-file`
  with `_API_KEY` still works.
- `docs/reference/schema.md:269`: "`env:KNOWLEDGE_BASE_EMBED_API_KEY` or `file:KNOWLEDGE_BASE_EMBED_API_KEY_FILE`,
  whichever declares the key; never the path or the secret".
- `docs/usage/ingesting-documents.md:351`: add the line `# or: export KNOWLEDGE_BASE_EMBED_API_KEY_FILE=/run/secrets/embed-key  # read once at start; never both`.
- ADR-0018: the one sentence from decision 10. Docstrings are covered in item 3.

## Testing

| Criterion | Tests (item) |
| --- | --- |
| key from the file is used | `…_key_file_key_is_sent_and_never_logged`, `…_declares_the_file_source`, `…_reads_the_key_file_once` (3) |
| missing or empty refuses to start | `…_bad_key_file_refuses_naming_the_variable`, `…_bad_key_file_setting_exits_78…` (3) |
| both set refused | `…_together_are_refused`, the server's both-set case (3) |
| never in logs | caplog at DEBUG over a real request; bind warning (3) |
| never in errors | every refusal asserts the key, the content and the path are absent, with no chained cause (2, 3) |
| never in `--help` | `test_help_never_shows_the_key_file_key` (3) |
| LLM cannot reach it | configure-time and stored-row refusals with `http_requests == []` (1) |

No test asserts h11's or httpx's message text, because that is a dependency's output format. The guard is tested at
KB's boundary. Before each commit run `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`.

## Verification

Run the AGENTS.md gate in order at the PR's final head with a clean tree, and quote each result in the PR:

1. `uv lock --check`
2. `uv sync`
3. `uv run ruff check src/ tests/`: zero warnings.
4. `uv run ruff format --check src/ tests/`: no reformats.
5. `uv run pytest tests/ -q -m "not slow"`: all passed. On a shared host, run it inside that host's memory lane.
6. `uv sync --all-groups`
7. `uv run basedpyright src/ tests/`: 0 errors.
8. `uv run --group docs sphinx-build -W -E -q -b html docs docs/_build`: exit 0.

`git diff --quiet origin/master...HEAD -- Containerfile .containerignore pyproject.toml uv.lock .python-version` exits
0, so the image step does not run.

Hand smoke against a real process's stderr. It uses no network, because every case fails before the database opens:

1. Declare `openai_compat` with `KNOWLEDGE_BASE_DB` pointing into a `mktemp -d` directory, and set
   `k=$(python3 -c 'import secrets; print(secrets.token_hex(16))')`.
2. Run `uv run knowledge-base` three ways:
   - `KNOWLEDGE_BASE_EMBED_API_KEY_FILE="$k"` (the swapped variable);
   - an empty file;
   - `KNOWLEDGE_BASE_EMBED_API_KEY="$k"` set beside a valid file.
3. Each run must exit 78, and `grep -c "$k"` on its stderr must print `0`.

## Open for the owner

- **Sibling leak, outside #660's acceptance.** The LLM key reaches the same sinks: an env key or an inline key with a
  trailing CR goes through `llm.py:246-248` into h11's error, then `jobs.py:215-222`. More broadly, `jobs.py` and
  `indexer.py:473-474` render httpx's own text, which `routes/__init__.py:28` says no caller may learn because it
  names the endpoint. Recommendation: a separate issue that renders those two sinks through `embedder_failure`-style
  text, plus the same rule for the LLM key. It changes `configure_llm`'s accepted input, which deserves its own
  review. Your call.
- **Decision 2's behavior change.** Under `openai_compat`, `openai` or `llama.cpp`, an env key with an inner space,
  which works today, is refused at start. My view is to accept this, since no bearer token contains a space.
- **Decision 1 deviates from memory-engine's exact rule** (one trailing newline stripped, control characters refused,
  path named in the error): KB trims surrounding whitespace (the issue's wording), accepts only visible ASCII, and never
  names the path. A file memory-engine accepts with an inner space or a non-ASCII character is refused here; a file
  with a trailing space or CRLF, which memory-engine would send as is, is trimmed here.
