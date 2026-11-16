# fix(server): refuse to start without an explicit embedding endpoint (#569)

Plan for dutiona/knowledge-base#569 (KB-1 of epic #568, wave 1 of the dutiona/infra#37 plan). Classification: Security
patch, Standard path, Super planning, Full post-implementation review (user-confirmed).

## Outcome

`knowledge-base` (stdio now, HTTP in KB-3) and the embedding commands of `knowledge-base-ingest` start only after the
`KNOWLEDGE_BASE_EMBED_*` declaration has been parsed and the database's recorded embedding configuration bound to it
(D1, D2); a missing or invalid variable, or a disagreeing database, exits non-zero naming it before any request is
sent or port bound. While KB runs, the declaration is the only embedding provider and endpoint, the active space's
identity, the only key source, and the only host exempt from the SSRF floor (D3–D5); the active identity and the
endpoint change only through two maintenance commands run with the unit stopped. Vision, the Playwright fallback and LLM extraction stay off unless switched on
(D6).

## Constraints

From the issue, verbatim:

- The environment is authoritative for the embedding configuration: provider, model, dimension, base URL and API-key source.
- The ticket names the variables.
- A blank database seeds its `config` table from them, never from the `ollama`/`bge-m3` defaults (`db.py:74-76`).
- An existing database whose `config` disagrees with the environment refuses to start and names the field.
- Setting the endpoint therefore never needs an admin write.
- With no explicit endpoint, KB exits non-zero before serving and names the missing variable. A transport mock records zero HTTP requests, so none reach `api.openai.com`.
- The `openai_compat` provider with no base URL raises instead of defaulting to `https://api.openai.com` (`embeddings.py:190`). The explicit `openai` provider is unchanged.
- The operator-declared endpoint is exempt from the SSRF floor, by exact host match. Without the exemption, `validate_base_url` (`utils.py:76-98`, through `is_private_ip`'s `not is_global`) rejects the Mac's tailnet name, which resolves into `100.64.0.0/10` and the `fd7a:` ULA range. Any URL a tool supplies still goes through the floor.
- Vision and the browser (Playwright) fallback are off by explicit configuration: `ingest_url` on a page with qualifying images makes no vision request, and a page the plain fetch can't read launches no browser.
- Contracts win over restatements: a merged ADR-0020/ADR-0015 overrides any restatement here. PRs reference the umbrella as `Refs dutiona/infra#37`, never a bare `#37`.

Settled by Michaël (spec and epic), not reopened:

- KB refuses to start without an explicit embedding endpoint. Vision is explicitly off (spec decision 10).
- KB fails loudly, and the caller retries.
- Admin writes run as a maintenance CLI with the unit stopped; no admin endpoint. The stdio entry point stays.
- The deployment passes "the KB-1 embedding environment variables, the key, vision and browser off" as container environment.
- KB-3 runs "the endpoint gate (KB-1) before the port is bound"; KB-4 extends "KB-1's variable set". KB-3/4/5/6 are not implemented here.
- ADR-0020 (memory-engine) is not merged yet and KB has no ADR-0015 copy yet (KB-4 adds it); ADR-0015 in memory-engine has
  no rule this plan contradicts. Re-checked before merge (item 19).

Working rules: the repository is public, so fixtures use `*.example.test` hosts, the synthetic addresses `100.64.0.10`
and `fd7a:115c:a1e0::10` and the key `sk-test-only`; tests never reach a network service; TDD (each red is seen
before its change); atomic Conventional Commits, no co-author line on branch commits; the default branch is `master`.

## Decisions

**D1. Variables.** Names follow KB's `KNOWLEDGE_BASE_` prefix and memory-engine's `MEMORY_ENGINE_EMBED_*` shape. An
empty value counts as unset. The first failing rule wins, and its message starts with the variable name.

| Variable | Required | Grammar | Recorded in `config` as |
| --- | --- | --- | --- |
| `KNOWLEDGE_BASE_EMBED_PROVIDER` | always | exactly `openai_compat`, `openai`, `ollama` or `onnx` | `embed_provider` |
| `KNOWLEDGE_BASE_EMBED_MODEL` | always | non-blank, stripped | `embed_model` |
| `KNOWLEDGE_BASE_EMBED_DIM` | always | `^[1-9][0-9]*$`: the stored dimension, after any Matryoshka truncation | `embed_dim` |
| `KNOWLEDGE_BASE_EMBED_URL` | for `openai_compat`, `openai`, `ollama`; refused for `onnx` | `http`/`https` with a host; no userinfo, query or fragment; normalized as `rstrip("/")`, then `removesuffix("/v1")` for the two OpenAI families (the rule `configure_embeddings` used, so legacy rows compare equal) | `embed_base_url` |
| `ONNX_EMBED_MODEL_PATH` (existing) | for `onnx` | an existing file: the explicit local endpoint of a provider that sends no request | not recorded |
| `KNOWLEDGE_BASE_EMBED_API_KEY` | optional | the secret itself, read at request time | `embed_api_key` = `env:KNOWLEDGE_BASE_EMBED_API_KEY` when set; no row otherwise |
| `KNOWLEDGE_BASE_LLM_API_KEY` | optional | the LLM's secret, the only `env:` spec an LLM key may name (D6) | `llm_api_key` = `env:KNOWLEDGE_BASE_LLM_API_KEY` when an operator configures it |
| `KNOWLEDGE_BASE_VISION`, `KNOWLEDGE_BASE_BROWSER`, `KNOWLEDGE_BASE_LLM` | optional | `on` or `off`; unset means `off`; anything else is refused | not recorded |
| `EMBED_PROVIDER` (deprecated) | must be unset | a non-empty value is refused: "no longer read; use KNOWLEDGE_BASE_EMBED_PROVIDER" | not recorded |

"API-key source" is the variable the key is read from. `config` holds only that spec, resolved per request by the
existing `utils._resolve_api_key`. `OPENAI_API_KEY` stops being an embedding key source on the config path; the
bare-name `OpenAIProvider` still reads it. "With no explicit endpoint" means: no URL for a network family, no model
file for `onnx`. The `onnx` model path is not recorded: it sends no request, and ADR-0015 makes identity the
operator-declared model slug, not the weights ("Slug-level identity, not weight-level"), so a different file under the
same slug is the accepted ADR-0015 limitation.

**D2. The gate.** `settings.check_environment() -> EmbedSettings` parses the embedding variables and the three
switches, touching nothing on disk. `db.open_declared_db(db_path=None, *, transition=None) -> sqlite3.Connection` calls
`check_environment()`, then `get_connection`, `init_schema` and `bind_embed_config`, closing the connection on failure;
seeding and binding both read the same `os.environ` at call time, so there is one declaration per call. `server.main()`
runs `open_declared_db().close()` before `mcp.run`; KB-3 adds its transport after the same line. A `KnowledgeBaseError`
prints `knowledge-base: <message>` to stderr and exits 78 (`EX_CONFIG`), so the deployment unit can set
`RestartPreventExitStatus=78` instead of restart-looping. The CLI's embedding commands (`ingest`, `reingest`,
`ingest-url`) open through `open_declared_db()` and exit 1 through their existing handler; `status`, `schema` and
`migrate` stay declaration-free on an existing database; `re-embed` and `rebind` are the two transitions (D3).

- Blank database (no `embed_model` row): `_seed_default_config` writes the declared rows before `_create_base_schema`,
  so the `vec0` tables and the bootstrapped `default` space take the declared provider, model and dimension. Without a
  declaration it raises naming the first missing variable. `DEFAULT_EMBED_MODEL/DIM/PROVIDER` are deleted.
- Existing database: `init_schema` reads only the recorded dimension (a stamped database returns early), so the job
  worker and `_conn._get_conn` need no declaration. `bind_embed_config(conn, declared, *, transition=None)` walks one
  field-to-key map, shared with seeding (KB-4 adds entries):
  - `embed_provider`, `embed_model`, `embed_dim`, `embed_base_url` (the recorded URL normalized by the D1 rule first):
    a recorded value must equal the declared one, else `KnowledgeBaseError` naming the key, both values (URLs through
    `_sanitize_url`) and the variable, plus the remedy (`rebind` or `re-embed`). A missing identity row refuses too. A
    missing `embed_base_url` (every database created before #569) is recorded: a first binding, not a disagreement, so
    an existing local database starts without an admin write.
  - The active space's `(provider, model, dim)` must equal the declaration too, else the bind refuses naming the field
    and `re-embed` as the remedy; a database with no active space refuses as well. This catches a pre-#569 database whose `config` drifted ahead of its active space
    (the drift `configure_embeddings` allowed) before serving, not at the first ingest.
  - `transition="rebind"` requires the active space's identity to equal the declaration, then records the declared URL
    and rewrites the `config` identity rows from the declaration. That also repairs a pre-#569 database whose `config`
    drifted away from its active space (the vectors are what the active space says they are).
    `transition="re-embed"` (used only by the `re-embed` CLI command, so `re_embed_tool` is unaffected) requires the
    active space's identity to differ from the declaration ("use rebind"), then
    records the declared URL, or deletes the row when the declaration has none (`onnx`), and skips the identity checks;
    item 14 then makes them pass.
  - Every check runs before any write of the bind, which commits once at the end, so a refused bind writes nothing of
    its own (`init_schema`, which runs first, may still converge a legacy database, as `_conn._get_conn` does).
  - `embed_api_key` is derived, never compared: the row is rewritten to the declared spec, or deleted when no key is
    declared, at every bind. That purges an inline key or foreign `env:` spec the old tool wrote; a WARNING names the
    field, never the old value.

**D3. Runtime: the declaration is the only embedding identity and endpoint.**

- `configure_embeddings` (with its tool and connectivity probe) is deleted. It was the only runtime writer of the
  endpoint rows, and its probe sent the resolved key as a Bearer header to any public host a caller named.
- One builder, `embed_swap.declared_provider_config(conn, family) -> ProviderConfig`, replaces the five hand-built
  `ProviderConfig`s (`ingest._embed_with_config`, `search.search`, `folder_summaries.update_folder_summary`,
  `embed_swap.backfill_space`, `embed_swap.promote_space`). It takes base URL and key spec from the recorded (bound)
  rows and raises `ValidationError` when `family` differs from the declared `KNOWLEDGE_BASE_EMBED_PROVIDER` ("the
  space's provider X is not the declared provider Y; re-embed under the declaration"). In a served process the recorded
  and declared providers are equal (the gate); during a `re-embed` transition the declared one is the target. So no tool-created or deprecated space of another family
  is ever embedded against the declared endpoint with the wrong wire format or without its floor, and the per-site
  `same_family` fallbacks to an auto-detected Ollama disappear.
- `promote_space` and `re_embed` (so `re_embed_tool` and `promote_embed_space_tool`) refuse a target whose
  `(provider, model, dim)` differs from the declaration; `re_embed` checks before it creates a space, so no orphan is
  left. In a served process the active identity therefore stays equal to the declaration for its lifetime. Creating,
  backfilling and comparing a same-provider space of another model stays allowed (owner call (f)); its requests go only
  to the declared endpoint. `re_embed_tool` keeps one use: rebuilding the declared identity with another
  `matryoshka_base_dim` or chunk strategy.
- Transitions run as maintenance CLI commands with the unit stopped (the settled admin model), never by hand-written
  SQL:
  - `knowledge-base-ingest re-embed [--batch-size N] [--matryoshka-base-dim N]` re-embeds to the declaration: the
    operator first sets the new `KNOWLEDGE_BASE_EMBED_*` values, then runs it. It opens with `transition="re-embed"`
    (which refuses, before any write, when the active space already has the declared identity: "use rebind"), then
    `re_embed` creates a new space with the declared identity, backfills it and promotes it, as today; a plain bind then
    passes. Until it completes, a plain bind refuses on the active-space check, so a failed or interrupted run never
    serves. When the backfill or the promotion raises (`BaseException`, so Ctrl-C too), `re_embed` removes the space it
    created (`deprecate_space`, then `cleanup_space`, both existing, in their own `try` whose failure is logged) and
    re-raises the original exception, so a rerun starts clean. A killed process cannot run that
    handler and leaves a `populating` space: harmless (never searched, never promoted without a full backfill), and
    removed with `deprecate_embed_space_tool` then `cleanup_embed_space_tool` once the server runs again (documented). The `--model`,
    `--dim` and `--provider` flags go: the declaration supplies them. The CLI handler also catches `httpx.HTTPError`,
    so a failed embed exits 1 with "<command> failed: <error>; rerun it" naming the subcommand, instead of a traceback.
  - `promote_space` becomes all-or-nothing and holds the write lock only briefly:
    1. it refuses a connection already in a transaction (`conn.in_transaction`), so it never commits or discards a
       caller's pending writes;
    2. it embeds a snapshot of the folder summaries through `declared_provider_config`, outside the lock;
    3. under `isolation_level = None` (restored in a `finally`), `BEGIN IMMEDIATE`, then: the completeness check; a
       re-read of `folder_summaries`, embedding the rows that are new or whose text changed since the snapshot (usually
       none), skipping rows that vanished, and reusing a snapshot vector only where path and text are both unchanged; the status swap, the identity rows, the `DELETE` of `similar` relationships and the drop,
       recreate and fill of `folder_summaries_vec`; `COMMIT`, with `ROLLBACK` on any `BaseException`.

    `_re_embed_folder_summaries` splits into an embed step and a write step with no commit of its own. Today the
    identity flip is committed before the rebuild, so a failed rebuild left stale summaries under a valid identity with
    no retry; now a failure changes nothing and the command can be rerun. A writer that embedded against the old space
    before the promotion and writes after it is the pre-existing space-lifecycle race; in a served process promotion
    is limited to the declared, already active `(provider, model, dim)`, and identity-changing promotion runs only in
    the CLI with the unit stopped, so only a Matryoshka or chunk-strategy variant can meet it (collateral finding).
    That bound relies on the CLI transitions running with the unit stopped, which nothing enforces (an idle server
    holds no SQLite lock); the README states the requirement, and an advisory lock between server and CLI is part of
    the same collateral finding.
  - The routes' own `DELETE FROM relationships WHERE relation_type = 'similar'` after `re_embed_tool` and
    `promote_embed_space_tool` (`routes/embeddings.py`) go: `promote_space` now does it inside its transaction.
  - `knowledge-base-ingest rebind` moves the endpoint, or repairs drifted `config` identity rows: it opens with
    `transition="rebind"` (D2).
- Rejected: a per-call re-check (a second source of truth) and ignoring `config` (status and `embed_config` would report
  values the process does not use).

**D4. `openai` vs `openai_compat`.** `OpenAICompatProvider.__init__` raises `ValidationError` on a falsy `base_url`.
`_build_provider` raises the same way for `ollama`, `openai_compat` and `openai` without `cfg.base_url`; `onnx` builds
locally. Unchanged, which is how this plan reads "the explicit `openai` provider is unchanged": the bare-name
`OpenAIProvider` pinned to `https://api.openai.com` with `OPENAI_API_KEY`. A database seeded with `EMBED_PROVIDER=openai`
starts by declaring `KNOWLEDGE_BASE_EMBED_PROVIDER=openai`, `KNOWLEDGE_BASE_EMBED_URL=https://api.openai.com` and moving
the key to `KNOWLEDGE_BASE_EMBED_API_KEY` (documented).

**D5. The exemption.** `validate_base_url(url, *, allow_loopback=False, exempt_host=None)`: after the scheme and host
checks, a parsed host equal to `exempt_host` (both lowercased, one trailing dot stripped; port and path ignored) returns
before any DNS lookup. Exact equality only. `OpenAICompatProvider.embed` alone passes it, as
`settings.declared_embed_host()` read at call time; `llm.py`, `web.py` and every `configure_*` path never do. The
`ollama` family stays family-trusted (unfloored); its URL now only comes from the declaration.

**D6. Off switches.** All three are off when unset; the switch wins over any `config` row.

- Vision: `vision._get_vision_config` raises `ValidationError` naming `KNOWLEDGE_BASE_VISION` unless it is `on`.
  `web._extract_html_images` (every `ingest_url`), `_extract_web_figures` and `_extract_element_captures` already turn
  that raise into 0 before any image download; `extract_figures` (tool and queued job) and `estimate_figures_time` call
  it as their first statement, before rendering or the OmniParser check. `configure_vision` and `configure_omniparser`
  refuse while it is off, before writing, a read-only query included.
- Browser: `web._get_browser_config` returns `None` unless `KNOWLEDGE_BASE_BROWSER=on`; `configure_browser` refuses
  while it is off, a read-only query included.
- LLM: `llm._get_llm_config` and `configure_llm` raise naming `KNOWLEDGE_BASE_LLM` unless it is `on`, before any write
  or probe. Whatever the switch, an LLM key is an inline value or exactly `env:KNOWLEDGE_BASE_LLM_API_KEY`; any other
  `env:` spec is refused by `configure_llm` and, for a row stored before #569, by `_get_llm_config` before any probe or
  call; `configure_llm` validates the effective key (its argument, else the stored row) before it writes anything.
  So a tool call cannot send the embedder key, or any other secret in the server's environment, to a host it
  names. Reason: the issue's Outcome says KB "sends no request anywhere else", while
  `configure_llm_tool` can point extraction at any public URL (or, for `ollama`, an unfloored one such as
  `169.254.169.254`), probe it with `api_key="env:KNOWLEDGE_BASE_EMBED_API_KEY"`, and `extract_structure_tool` then
  posts whole papers there.

**D7. Legacy paths.** The `EMBED_PROVIDER` seed read, its warning and the bare-name `get_provider(allow_env_override=)`
override go (D1 refuses the variable). Ollama auto-detection (`OLLAMA_HOST`, the WSL2 probe, `localhost`) leaves the
embedding config path; it remains for the bare-name `OllamaProvider()` and for vision and LLM when switched on.

**Owner calls, each with its consequence (Michaël approves them with this plan):**

- (a) **LLM switch (D6) is a scope addition beyond the five acceptance bullets.** With it, local extraction needs
  `KNOWLEDGE_BASE_LLM=on`; without it, "sends no request anywhere else" is false. It is one self-contained item (12).
- (b) **A changed URL refuses at start; `rebind` adopts it.** A mistyped URL on an existing corpus cannot redirect it,
  and moving the embedder is one maintenance command with the unit stopped. The alternative, rewriting the endpoint
  rows from the environment at every start, needs no command but no longer catches a mistyped URL (documents go to it
  at the first embed). The AC's "never needs an admin write" holds for setting the endpoint (blank or pre-#569
  database); moving it is a maintenance command, not a raw SQL write.
- (c) **`configure_embeddings_tool` is deleted**, reversing ADR-0018 §3; ADR-0018 gets an amendment, and KB-5's tool
  inventory loses one tool. The space tools stay, bounded by D3.
- (d) **Pre-existing floor gaps left for KB-6**, which owns `web.py`/`utils.py` hardening: `ingest_url` follows
  redirects before it checks the final host (the epic's KB-6 per-hop pinning); the `ollama` LLM family's URL is
  unfloored once `KNOWLEDGE_BASE_LLM=on`; `configure_vision`, `configure_omniparser` and `configure_browser` check the
  scheme only. All are inert with the deployment's switches off. The issue's "any URL a tool supplies still goes through
  the floor" is read as "the exemption opens no tool URL", which item 14 proves.
- (e) **The local harness registration loses KB at the first release after merge** until its MCP `env` block carries
  the declaration (the server exits 78 instead of serving). The release checklist gains that step (Documentation);
  editing the registration itself is Michaël's.
- (f) **Comparison spaces stay.** A same-provider space of another model can still be created, backfilled and compared
  (requests go only to the declared endpoint); only promotion is bound to the declaration. The strict alternative,
  refusing any space but the declared identity, retires the A/B comparison feature.
- (g) **Rolling back a model costs a full re-embed.** The deprecated space stays on disk, but promoting it in a served
  process is refused unless it is the declared identity, and the gate refuses a served declaration that differs from
  the active space. The alternative, letting `re-embed` reuse a matching deprecated or `populating` space, needs
  matching rules over every space parameter (strategy, Matryoshka base, element type, the endpoint it was built
  against); this plan leaves that out.

## Implementation sequence

Restrictive changes land first; the one permissive change, the exemption, lands last among code items. Every item's
green ends with the three AGENTS.md checks, `uv run pytest tests/ -q` (slow tests included), `uv run ruff check src/ tests/`
and `uv run ruff format --check src/ tests/`, so each commit leaves the project green; the red lines name the observed
failure.

- [ ] **0. Plan on the branch** (operational). Work only in the existing locked worktree of
  `fix/569-explicit-embedding-endpoint`. Commit this plan and its review artifacts; the `-drafts/` directory is never
  committed. Commit: `docs(plans): add the #569 implementation plan`.

- [ ] **1. Boy-scout: `knowledge-base-ingest status` prints the embedding key.** Files: `src/knowledge_base/indexer.py`,
  `tests/test_indexer.py`. Red: `test_cmd_status_never_prints_the_embedding_key` (an inline `embed_api_key=sk-test-only`
  row; the output must not contain it) via `uv run pytest tests/test_indexer.py -q -k never_prints` → 1 failed. Change:
  `cmd_status` drops `api_key` before printing, as the `status` tool does. Green: same command → passed. Commit:
  `fix(cli): keep the embedding key out of the status output`.

- [ ] **2. Boy-scout: dead `embed_provider_config`.** Files: `src/knowledge_base/embed_swap.py`. No behaviour:
  `rg -n embed_provider_config src tests` lists only its definition before, nothing after. Green:
  `uv run pytest tests/test_embed_swap.py -q`. Commit: `refactor(embeddings): delete the unused embed_provider_config`.

- [ ] **3. One test declaration for the suite.** Files: `tests/declared_env.py` (new), `tests/conftest.py`, and the 14
  test files importing `DEFAULT_EMBED_*`. No new behaviour: `tests/declared_env.py` defines `EMBED_PROVIDER = "ollama"`,
  `EMBED_MODEL = "bge-m3"`, `EMBED_DIM = 1024`, `EMBED_URL = "http://embedder.example.test:11434"`; the autouse
  `_declared_environment` fixture sets the four `KNOWLEDGE_BASE_EMBED_*` variables and the three switches to `on`, and
  deletes `EMBED_PROVIDER` and `KNOWLEDGE_BASE_EMBED_API_KEY`. Green: `uv run pytest tests/ -q -m "not slow"` → same
  pass count as before (1125 passed at 8fa69bd); `rg -n DEFAULT_EMBED_ tests` → nothing. Commit:
  `test: take embedding fixture values from one declaration`.

- [ ] **4. Parse the declaration.** Files: `src/knowledge_base/settings.py` (new; imports only the standard library and
  `exceptions`), `tests/test_settings.py` (new). Red: one test per D1 rule (each missing variable names itself; invalid
  provider `anthropic_compat`/`OpenAI_Compat`, dim `0`/`-1`/`1_024`/`abc`, URL `ftp://h`/`https://`/`https://u:p@h`/
  `https://h/?q=1`/`https://h/#f`; URL with `onnx`; `onnx` without an existing `ONNX_EMBED_MODEL_PATH`; set
  `EMBED_PROVIDER`; switch values `ON`/`true`/`1` refused; `https://embedder.example.test/v1/` normalized to
  `https://embedder.example.test` for `openai_compat`; `api_key_source` set or None; `declared_embed_host()` lowercases
  and strips the trailing dot, None when unset; `repr()` never contains the key). `uv run pytest tests/test_settings.py -q`
  → collection error, `ModuleNotFoundError`. Change: `EmbedSettings(provider, model, dim, base_url, api_key_source)`
  frozen with slots; `VARIABLES`; `normalize_embed_url(provider, url)`; `load_embed_settings()`,
  `declared_embed_host()`, `switch_on(var)`, `check_environment()`, all reading `os.environ` at call time. Green: same
  command → all pass. Commit: `feat(settings): parse the declared embedding environment`.

- [ ] **5. A blank database is seeded from the declaration** (AC1). Files: `src/knowledge_base/db.py`,
  `src/knowledge_base/embed_swap.py`, `src/knowledge_base/embeddings.py`, `tests/test_db.py`, `tests/test_embed_swap.py`.
  Red: `test_blank_database_seeds_from_the_declaration` (`openai_compat`, `qwen3-embedding`, 768,
  `https://embedder.example.test/v1/`, key `sk-test-only`): the five rows equal the declaration, no `config` value
  contains the key, the active space is `(openai_compat, qwen3-embedding, 768)`, and `insert_chunk_vec` accepts 768
  floats while 1024 raise; `test_blank_database_without_a_declaration_names_the_variable`. Guard (green before and
  after): `test_existing_database_initializes_without_a_declaration` (legacy unversioned database, all variables unset).
  `uv run pytest tests/test_db.py -q -k declaration` → the defaults are seeded and nothing raises. Change: D2 blank
  branch; the pre-provider backfill keeps the literal `'ollama'` with its why-comment; delete the three constants, their
  `__all__` entries and every fallback to them; `OllamaProvider.embed` checks the dimension only when `expected_dim` is
  given; delete `test_char_bootstrap_falls_back_to_defaults_when_config_absent`,
  `test_seed_honors_embed_provider_env_on_fresh_db`, `test_get_embed_config_env_no_longer_selects_provider`,
  `test_get_embed_config_explicit_config_overrides_env`, and the `EMBED_PROVIDER` warning branch of `get_embed_config`.
  Green: the focused command, then the suite; `rg -n DEFAULT_EMBED_ src` → nothing. Commit:
  `fix(db): seed a blank database from the declared embedding environment`.

- [ ] **6. An existing database is bound to the declaration** (AC1). Files: `src/knowledge_base/db.py`,
  `tests/test_db.py`. Red: `test_bind_refuses_a_disagreeing_field`, parametrized over provider, model, dim and URL:
  `KnowledgeBaseError` naming `embed_<field>` and its variable; `test_bind_accepts_a_legacy_normalized_url` (a row
  `https://embedder.example.test` written the old way, declaration `https://embedder.example.test/v1/`);
  `test_bind_records_an_unrecorded_endpoint`; `test_bind_refuses_a_missing_identity_row`;
  `test_bind_derives_the_key_row` (an inline `sk-inline-secret` row is replaced by the declared spec, or deleted when no
  key is declared; neither the message nor the log contains the secret); `test_bind_refuses_a_drifted_active_space`
  (`config` equal to the declaration, active space of another model: refuses naming `re-embed`);
  `test_bind_transitions` (`transition="rebind"` records a changed URL and rewrites drifted `config` identity rows, but
  refuses when the active space's model differs;
  `transition="re-embed"` records the URL and lets the identity differ; an `ollama` → `onnx` re-embed transition
  deletes the URL row). `uv run pytest tests/test_db.py -q -k "bind or open_declared"` → `ImportError` on `bind_embed_config`.
  Change: `bind_embed_config` and `open_declared_db` (D2, D3 transitions). Green: the focused command, then the suite.
  Commit: `feat(db): bind an existing database to the declared embedding configuration`.

- [ ] **7. Every embedding provider comes from the declaration** (AC3, D3, D4). Files: `src/knowledge_base/embeddings.py`,
  `src/knowledge_base/embed_swap.py`, `src/knowledge_base/ingest.py`, `src/knowledge_base/search.py`,
  `src/knowledge_base/folder_summaries.py`, `tests/conftest.py`, `tests/test_embeddings.py`, `tests/test_embed_swap.py`,
  `tests/test_embed_spaces.py`, `tests/test_search.py`, `tests/test_folder_summaries.py`. Red:
  - `test_no_provider_without_base_url`: `OpenAICompatProvider(None)` and `OpenAICompatProvider("")` raise
    `ValidationError` matching "base URL" (today they construct); `embed(["x"], _provider_cfg=ProviderConfig(family=f))`
    for `openai_compat`, `openai`, `ollama` raises with `http_requests` empty and a mocked `_get_ollama_url` never called
    (today the recorder holds a POST to `https://api.openai.com/v1/embeddings`, or the Ollama URL is resolved).
  - `test_backfill_refuses_a_space_of_another_provider`: declared `openai_compat`; `create_embed_space_tool` with
    `provider="ollama"`, then `backfill_embed_space_tool`, returns an error naming both providers, with zero requests
    (today it posts to the declared host's `/api/embed`); the same for a search and a folder-summary update on an
    active space of another provider (today: auto-detected Ollama).
  - Guard: `test_explicit_openai_provider_is_unchanged`: `OpenAIProvider()` and `get_provider("openai")` target
    `https://api.openai.com`.

  `uv run pytest tests/test_embeddings.py tests/test_embed_swap.py tests/test_search.py tests/test_folder_summaries.py -q -k "without_base_url or another_provider or unchanged"`
  → `DID NOT RAISE` and the recorded requests above. Change: D4 for `OpenAICompatProvider` and `_build_provider`;
  `declared_provider_config(conn, family)` (D3) replaces the five hand-built `ProviderConfig`s and the `same_family`
  branches; tests that backfilled or searched a space of another provider than the config now expect the refusal. New
  opt-in fixture `http_requests`: patches `httpx.HTTPTransport.handle_request` and
  `httpx.AsyncHTTPTransport.handle_async_request`, records every request, answers from a `{host: handler}` map and raises
  `httpx.ConnectError` for any other host. Green: the focused command, then the suite. Commit:
  `fix(embeddings): build every embedding provider from the declaration`.

- [ ] **8. Remove the runtime writer** (D3). Files: `src/knowledge_base/embed_swap.py`,
  `src/knowledge_base/routes/embeddings.py`, `tests/test_server.py`, `tests/test_embed_swap.py`, `tests/test_search.py`.
  Red: drop `configure_embeddings_tool` from `_EMBEDDING_TOOLS`; `uv run pytest tests/test_server.py -q` → the inventory
  tests fail (the hub still mounts it). Change: delete `configure_embeddings`, `_test_embedding_connectivity`,
  `_EMBED_CONNECTIVITY_TIMEOUT`, `_EMBED_FAMILIES`, `_LOCAL_EMBED_FAMILIES`, the tool, and their tests; tests that
  drifted `config` through the tool write the rows by SQL. Footer `BREAKING CHANGE:` names the tool and the variables
  that replace it. Green: the focused command, then the suite; `rg -n configure_embeddings src tests` → nothing.
  Commit: `refactor(embeddings)!: remove configure_embeddings, the environment declares the endpoint`.

- [ ] **9. The declared source is the only embedding key source** (D1, D7). Files: `src/knowledge_base/embed_swap.py`,
  `src/knowledge_base/embeddings.py`, `tests/test_embed_swap.py`, `tests/test_embeddings.py`. Red: replace the four
  `test_openai_key_*` tests with `test_openai_api_key_is_never_an_embedding_key_source` (`OPENAI_API_KEY=sk-test-only`,
  config `openai_compat` at `https://api.openai.com` and again `openai`, no `embed_api_key` row:
  `get_embed_config(conn)["api_key"] is None`). `uv run pytest tests/test_embed_swap.py -q -k never_an_embedding_key` →
  the key is injected. Change: delete the injection, `_warn_embed_env_deprecated` and its flag, and the bare-name
  `get_provider` `allow_env_override` read of `EMBED_PROVIDER` with its test. Green: the focused command, then the suite;
  `rg -n "OPENAI_API_KEY|deprecat" src/knowledge_base/embed_swap.py` and `rg -n EMBED_PROVIDER src/knowledge_base/embeddings.py`
  → nothing. Commit: `fix(embeddings): take the embedding key only from the declared source`.

- [ ] **10. Vision off unless switched on** (AC5). Files: `src/knowledge_base/vision.py`, `tests/test_vision.py`,
  `tests/test_ingest.py`, `tests/test_routes_extraction.py`. Red: with `KNOWLEDGE_BASE_VISION` unset,
  `test_ingest_url_with_vision_off_makes_no_vision_request` (`vision_base_url=https://vision.example.test` in `config`;
  `http_requests` serves `https://page.example.test/article` with 200+ characters and
  `<img src="https://img.example.test/fig.png" width="800" height="600">`; `knowledge_base.web.is_private_ip` returns
  False; `knowledge_base.ingest.embed` faked): `figures_extracted == 0`, the only recorded request is the page GET
  (today the image GET is recorded); `extract_figures(conn, 1)` and `estimate_figures_time(conn, 1)` raise naming the
  variable before any work (today `NotFoundError`); `extract_figures_tool` returns an error with `jobs` empty;
  `configure_vision` and `configure_omniparser` raise and write no row (today they write). `uv run pytest tests/test_vision.py tests/test_ingest.py tests/test_routes_extraction.py -q -k vision_off`
  → those failures. Change: D6 vision. Green: the focused command plus
  `tests/test_multi_figure.py tests/test_vision_omniparser_integration.py`, then the suite. Commit:
  `fix(vision): keep vision off unless KNOWLEDGE_BASE_VISION=on`.

- [ ] **11. Browser fallback off unless switched on** (AC5). Files: `src/knowledge_base/web.py`, `tests/test_ingest.py`.
  Red: `test_ingest_url_with_the_browser_off_launches_nothing` (`browser_mode=local` and `browser_venv` pointing at a
  temp directory holding an executable `bin/python`, inserted by SQL; `KNOWLEDGE_BASE_BROWSER` unset; a JS-only page
  under 200 characters): a mocked `knowledge_base.web.subprocess.run` is never called, `browser_rendered` is False,
  `http_requests` holds only the page (today the subprocess is called once); `configure_browser` raises and writes no
  row. `uv run pytest tests/test_ingest.py -q -k browser_off` → those failures. Change: D6 browser. Green: the focused
  command plus `tests/test_routes_ingestion.py`, then the suite. Commit:
  `fix(web): keep the browser fallback off unless KNOWLEDGE_BASE_BROWSER=on`.

- [ ] **12. LLM extraction off unless switched on; no embedder key for the LLM** (owner call (a), D6). Files:
  `src/knowledge_base/llm.py`, `tests/test_llm.py`, `tests/test_routes_extraction.py`. Red: with `KNOWLEDGE_BASE_LLM`
  unset, `test_llm_off_refuses_before_any_request`: `configure_llm(conn, "ollama", "http://169.254.169.254")` and
  `_llm_call("p", conn=conn)` raise naming the variable, `http_requests` empty, no `llm_base_url` row written (today
  the probe is recorded and the row written); with the switch on, `test_llm_key_spec_allowlist`:
  `configure_llm(..., api_key="env:KNOWLEDGE_BASE_EMBED_API_KEY")` and `api_key="env:HOME"` raise, nothing written or
  sent; `env:KNOWLEDGE_BASE_LLM_API_KEY` and an inline value are accepted; a stored `llm_api_key` row
  `env:KNOWLEDGE_BASE_EMBED_API_KEY` makes `_get_llm_config`, and so a `configure_llm` call that omits `api_key`,
  raise before any request and before any row is written. `uv run pytest tests/test_llm.py -q -k "llm_off or key_spec_allowlist"` → those failures. Change: D6
  LLM. Green: the focused command plus `tests/test_extraction.py tests/test_routes_extraction.py`, then the suite.
  Commit: `fix(extraction): keep LLM extraction off unless KNOWLEDGE_BASE_LLM=on`.

- [ ] **13. The startup gate** (AC1, AC2). Files: `src/knowledge_base/server.py`, `src/knowledge_base/indexer.py`,
  `tests/test_server.py`, `tests/test_indexer.py`. Red, with `server.mcp.run` patched:
  `test_main_exits_before_serving_when_a_variable_is_missing`, parametrized over PROVIDER, MODEL, DIM, and URL with
  `openai_compat` declared and `OPENAI_API_KEY` set: `SystemExit` 78, stderr names the variable, `mcp.run` not called,
  `http_requests` empty, no file at `KNOWLEDGE_BASE_DB`; `test_main_refuses_a_database_that_disagrees` (model changed
  after seeding): 78 naming `embed_model` and the variable, `config` unchanged;
  `test_main_records_a_legacy_endpoint_and_serves` (URL row deleted): `mcp.run` called once, the row equals the
  declaration; `test_main_refuses_a_bad_switch` (`KNOWLEDGE_BASE_VISION=yes`);
  `test_server_process_exits_78_without_a_declaration` runs `python -m knowledge_base.server` with the declaration
  removed from a copied environment, stdin from `/dev/null` and `timeout=30` (`# noqa: S603`, trusted argv): return code
  78, stderr names `KNOWLEDGE_BASE_EMBED_PROVIDER`; `test_server_process_serves_under_a_declaration`: the same with the
  suite declaration exits 0 at stdin EOF and leaves a seeded database (verified on master: `knowledge-base </dev/null`
  exits 0). `tests/test_indexer.py`: `ingest` without a declaration exits 1
  naming it; `ingest` on a disagreeing database exits 1 naming the field; guards: `status`, `schema` and
  `migrate --check` succeed on an existing database with no declaration. `uv run pytest tests/test_server.py tests/test_indexer.py -q`
  → `main` serves regardless (no `SystemExit`). Change: D2 gate, with a one-line why-comment on 78. Green: the focused
  command, then the suite; the unchanged `test_main_runs_stdio_transport` passes under the suite declaration. Commit:
  `fix(server): refuse to start without an explicit embedding endpoint`.

- [ ] **14. Identity changes only through the declaration** (D3 transitions). Files: `src/knowledge_base/embed_swap.py`,
  `src/knowledge_base/indexer.py`, `src/knowledge_base/routes/embeddings.py` (docstrings), `tests/test_embed_swap.py`,
  `tests/test_embed_spaces.py`, `tests/test_indexer.py`. Red:
  - `test_promote_refuses_an_undeclared_identity`: `promote_embed_space_tool` on a deprecated space of another model,
    and `re_embed_tool(model="other-model", dim=8)`, return an error naming the field before any backfill request;
    `config` and `embed_spaces` unchanged (today both rewrite `embed_model`, and `re_embed` creates a space).
  - `test_cli_re_embed_moves_to_the_declaration`: a database seeded as `ollama`, `bge-m3`, 1024 at
    `http://embedder.example.test:11434`; the declaration then names `ollama`, `other-embed`, 8 at
    `http://embedder-b.example.test:11434` (the unfloored family, so the test needs no DNS before item 15);
    `main(["--db", db, "re-embed"])` exits 0, every recorded request goes to `http://embedder-b.example.test:11434/api/embed`,
    and afterwards `open_declared_db()` binds (today `--model`/`--dim` are required and the new endpoint is never
    recorded). A second run is refused ("use rebind") and leaves `config` unchanged, a changed URL included, so a plain
    open still refuses it. An interrupted run (the handler fails after the first batch) exits 1 with the rerun
    message and no traceback, a plain open then
    refuses, and a rerun completes in a new space and binds.
  - `test_promote_is_all_or_nothing`, parametrized over a failure in the summary embed, a failure while inserting
    the second summary vector (after the drop and recreate) and a `KeyboardInterrupt` there: statuses, identity rows
    and the old `folder_summaries_vec` rows are unchanged, read both on the same connection and on a fresh one,
    `isolation_level` is restored, and a retry succeeds (today the identity flip is committed first);
    `test_promote_refuses_a_pending_transaction` (an uncommitted insert on the connection: refused, the insert still
    pending); `test_promote_re_embeds_a_summary_changed_after_the_snapshot` (a summary updated between the snapshot and
    the lock gets the vector of its new text).
  - `test_failed_re_embed_leaves_no_orphan_space`: the backfill fails; `embed_spaces` holds only the original spaces.
  - `test_cli_rebind_adopts_a_moved_endpoint`: a changed URL refuses a plain open; `main(["--db", db, "rebind"])`
    exits 0; a plain open then binds; `rebind` when the active space's model differs exits 1 naming `embed_model`;
    `rebind` on a database whose `config` drifted to another model while its active space matches the declaration
    exits 0 and leaves `config` equal to the declaration.

  `uv run pytest tests/test_embed_swap.py tests/test_embed_spaces.py tests/test_indexer.py -q -k "undeclared_identity or re_embed_moves or rebind or all_or_nothing or no_orphan or pending_transaction or after_the_snapshot"`
  → those failures. Change: D3 transitions (`promote_space`/`re_embed` identity guard; `cmd_re_embed` loses
  `--model/--dim/--provider` and opens with `transition="re-embed"`; `promote_space` all-or-nothing; a failed `re_embed` removes its space; new `rebind`
  subcommand opening with `transition="rebind"`); tests that promoted or re-embedded to an undeclared identity declare it first. Green: the
  focused command, then the suite. Commit: `fix(embeddings): change the embedding identity only through the declaration`.

- [ ] **15. Exempt the declared host, and only it, from the floor** (AC4). Files: `src/knowledge_base/utils.py`,
  `src/knowledge_base/embeddings.py`, `tests/conftest.py`, `tests/test_utils.py`, `tests/test_embeddings.py`,
  `tests/test_ingest.py`, `tests/test_llm.py`. New opt-in fixture `fake_dns` (patches
  `knowledge_base.utils.socket.getaddrinfo` from a `{host: [addresses]}` map, `socket.gaierror` otherwise) maps
  `embedder.example.test` and `other.example.test` to `100.64.0.10` and `fd7a:115c:a1e0::10`.
  - API red: `validate_base_url(..., exempt_host="embedder.example.test")` → `TypeError: unexpected keyword argument`.
  - Behaviour red (after the keyword exists, before the embed path passes it): under a declaration of `openai_compat`,
    `https://embedder.example.test`, dim 8, key `sk-test-only`, on a database opened by
    `open_declared_db(tmp_path / "kb.db")` with no `configure_*` call, `_embed_with_config(conn, ["hello"])` is
    rejected by the floor (`private/reserved`); green, it sends exactly one request, to
    `https://embedder.example.test/v1/embeddings` with `Authorization: Bearer sk-test-only`.
  - Exemption scope: with `exempt_host="embedder.example.test"`, `https://EMBEDDER.example.test.:8443/v1` passes without
    a lookup; `https://embedder.example.test.evil.example`, `https://sub.embedder.example.test`,
    `https://xembedder.example.test`, `https://embedder.example.test@evil.example` and `ftp://embedder.example.test`
    still raise; a provider for `https://other.example.test` raises and sends nothing.
  - Guards (green before and after): `ingest_url(conn, "https://embedder.example.test/doc")`,
    `web._validate_image_url("https://embedder.example.test/a.png")` and, with the LLM on,
    `configure_llm(conn, "openai_compat", "https://embedder.example.test")` are all refused with zero requests.

  `uv run pytest tests/test_utils.py tests/test_embeddings.py tests/test_ingest.py tests/test_llm.py -q -k "exempt or declared"`.
  Change: D5. Green: the focused command, then the suite; the existing `tests/test_utils.py` floor tests pass
  unchanged. Commit: `fix(embeddings): exempt only the declared endpoint host from the SSRF floor`.

- [ ] **16. ADR-0018 amendment.** Files: `docs/design/adr/0018-provider-abstraction.md`. No behaviour. Change: the
  status line gains "amended 2026-11-16 (#569)" and a closing "Amendment" section records D1–D5 (the declaration is
  authoritative and `config` records it; `configure_embeddings()` and the env back-compat window are superseded;
  identity and endpoint change only through the `re-embed`/`rebind` maintenance commands; the declared key source is
  the only embedding key source; the declared host is the one §4 floor exemption, on the embedding request path only;
  no HTTP family has a default endpoint on the config path). Check:
  `rg -n "Amendment" docs/design/adr/0018-provider-abstraction.md`. Commit:
  `docs(adr): amend ADR-0018 for the environment-declared embedding endpoint`.

- [ ] **17. User and agent documentation**, as listed under Documentation. Check:
  `rg -n "configure_embeddings|EMBED_PROVIDER" README.md CLAUDE.md AGENTS.md GEMINI.md docs .claude --glob '!docs/plans/**' --glob '!docs/design/adr/**'`
  → only the line documenting that `EMBED_PROVIDER` is refused. Commit: `docs: document the declared embedding environment`.

- [ ] **18. Boy-scout: stale tool inventory and agent notes.** The README says 46 tools while the hub mounts 48 (47
  after item 8); `AGENTS.md`/`GEMINI.md` call `embeddings.py` an "Ollama embedding client" and tell contributors to patch
  `httpx.AsyncClient.post` while the code calls `httpx.post`; the `configure_llm_tool` docstring calls `allow_loopback`
  "shared with the embedding path", false after item 8. Check:
  `uv run python -c "import asyncio; from knowledge_base.server import mcp; print(len(asyncio.run(mcp.list_tools())))"`
  prints the README total. Commit: `docs: sync the tool inventory and agent notes with the code`.

- [ ] **19. Gates, pull request, review, finish** (operational). Run Verification at the clean head. Open the PR from
  `fix/569-explicit-embedding-endpoint` to `master`, titled as the issue; the body carries
  `Classification: Security patch (user-confirmed)`, the execution path, planning tier and review gate lines, the owner
  calls, an "Upgrading an existing install" note, a `Collateral findings` section, `Closes #569` and
  `Refs dutiona/infra#37` (no bare `#37`, no closing keyword next to any other number, negated or not). Before merge,
  re-check whether ADR-0020 (memory-engine) or a KB ADR-0015 copy has merged and changed any rule restated here. Run
  `/super-review` at the confirmed Full gate; each fix is its own commit followed by Verification. `/finish-pr` reuses
  the single plan issue published by `~/.claude/skills/finish-pr/scripts/archive-plan-issue.sh` at approval time and
  removes the `docs/plans/` files in its last commit. Michaël merges with `/squash-merge`; the implementer never merges.

## Documentation

- `README.md`: "Run the server" and the MCP client JSON gain an `env` block (a local Ollama example at
  `http://localhost:11434` and an `openai_compat` example at `https://embedder.example.test` with a key). A new
  "Configuration" section: the D1 table (`KNOWLEDGE_BASE_EMBED_DIM` is the stored dimension after any Matryoshka
  truncation); the start rules (blank database seeded; existing database must agree or exit 78 naming the field;
  pre-#569 databases record their endpoint at the first declared start; the key row always follows the declaration);
  the declared-host exemption; the three switches, off by default; changing the model or provider (unit stopped: set the
  new declaration, run `knowledge-base-ingest re-embed`, start); moving the embedder (unit stopped: set the new URL, run
  `knowledge-base-ingest rebind`, start); "Upgrading an existing install" (declare `ollama`, `bge-m3`, `1024` and the
  Ollama URL auto-detection used to find; an `openai` database declares `KNOWLEDGE_BASE_EMBED_URL=https://api.openai.com`
  and moves `OPENAI_API_KEY` to `KNOWLEDGE_BASE_EMBED_API_KEY`; set `KNOWLEDGE_BASE_VISION=on`/`KNOWLEDGE_BASE_LLM=on`
  to keep figure or structured extraction; an inline embedding key the old tool stored is removed from `config` at
  the first start, but its bytes may survive in free pages, the WAL and earlier backups, so rotate that key). "Config
  keys": the `embed_*` rows are recorded from the declaration. Rolling back a model: declare the previous identity and
  run `re-embed` (a full re-embed, owner call (g)); a `re-embed` that fails removes the space it created and can be
  rerun, while a killed one leaves a `populating` space to remove with the deprecate and cleanup tools.
- `docs/usage/ingesting-documents.md`: replace "Configuring the embedding provider" and "Environment Variable Override
  (deprecated)" with the declaration and the exemption rule; the browser sentence names `KNOWLEDGE_BASE_BROWSER=on`.
- `docs/usage/figure-extraction.md`, `docs/usage/structured-extraction.md`: the `KNOWLEDGE_BASE_VISION`/`_LLM` switches.
  `docs/usage/embedding-spaces.md`: re-embedding under a declaration; promotion is limited to the declared identity.
- `docs/reference/mcp-tools.md`: `configure_embeddings_tool` removed; `configure_vision_tool`,
  `configure_browser_tool` and `configure_llm_tool` refuse while their switch is off; `re_embed_tool` and
  `promote_embed_space_tool` accept only the declared identity. `docs/reference/schema.md`: the embedding `config` keys
  are seeded from the declaration.
- `docs/getting-started/installation.md`, `docs/getting-started/quickstart.md`, `docs/requirements.md`: the client
  configuration carries the declaration; `OLLAMA_HOST` now steers only the LLM and vision defaults.
- `docs/design/architecture-overview.md`: the `embeddings.py` paragraph and a new `settings.py` one.
- `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`: the stack line and "start MCP server" command mention the declaration.
- `.claude/skills/release-checklist/SKILL.md`: `.release-env` exports the declaration, because a first release's
  `migrate` creates, and so seeds, the database; and before the promote step, the client MCP registration's `env`
  block must carry the declaration (owner call (e)).
- `docs/design/adr/0018-provider-abstraction.md`: item 16.

## Testing

- No network in the new tests: `http_requests` records at the httpx transport (sync and async) and fails any
  unexpected host; `fake_dns` answers `getaddrinfo` from a map and fails closed. Both are opt-in; every "no request"
  proof uses them and asserts the recorder is empty or holds exactly the expected requests. Existing tests keep their
  own mocks.
- The autouse `_declared_environment` supplies the declaration and turns the three switches on, so the ~640
  `init_schema`/`kb_conn` call sites and the existing vision, browser and LLM tests run unchanged; the new tests pin the
  off defaults by unsetting the switches.
- Acceptance to proof: environment authoritative, variables named, blank seeded without defaults, disagreement
  refused, no admin write to set the endpoint: items 4, 5, 6, 13, 15 (end to end on a database no tool configured). No
  endpoint means a non-zero exit naming the variable with zero requests: item 13 (in process and a real subprocess).
  `openai_compat` raises, `openai` unchanged: item 7. Exact-host exemption, tool URLs floored: item 15. Vision and
  browser off: items 10 and 11, the issue's two `ingest_url` scenarios plus queued figure jobs. The outcome's "sends no
  request anywhere else": items 7, 12 and 14 (spaces of another provider, LLM, identity changes).
- Deleted with the behaviour they covered: the `configure_embeddings` tests, the `EMBED_PROVIDER` seed, warning and
  bare-name override tests, the four `OPENAI_API_KEY` injection tests (replaced by one), the bootstrap default-fallback
  test.
- Not covered: a live llama.cpp embedder and real tailnet DNS; the infra deployment ticket's smoke test owns them.

## Verification

From the worktree root, after `uv sync --all-groups`:

```bash
uv run ruff check src/ tests/                           # All checks passed!
uv run ruff format --check src/ tests/                  # every file already formatted
uv run pytest tests/ -q -m "not slow"                   # merge gate: 0 failed
uv run pytest tests/ -q                                 # AGENTS.md full suite, slow tests included: 0 failed
uv run basedpyright src/ tests/                         # 0 errors
rg -n "DEFAULT_EMBED_|configure_embeddings" src tests   # no output
rg -n 'or "https://api.openai.com"' src                 # no output
rg -n "exempt_host" src                                 # utils.py and embeddings.py only
d=$(mktemp -d); env -u KNOWLEDGE_BASE_EMBED_PROVIDER KNOWLEDGE_BASE_DB="$d/kb.db" uv run knowledge-base </dev/null; echo "exit=$?"; test ! -e "$d/kb.db" && echo "no database created"
git diff origin/master...HEAD | grep -nE '[a-z0-9-]+\.ts\.net'                          # no output (public repository)
```
