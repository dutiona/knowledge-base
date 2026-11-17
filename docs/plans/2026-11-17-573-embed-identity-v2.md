# feat(embeddings): enforce the shared ADR-0015 identity with KB's own query instruction (#573), v2

Successor of the plan Michaël approved on #603 (sha256 3e5fd19460fdc2ae8faaa5a538cb583969064ae1d87ee2dcd17a29246839a425).
It carries his two rulings of 2026-11-17 ("I agree with your concerns and what you'll tell the worker"):

1. Keep cross-model comparison: each embedding space is checked against its own recorded nine-field identity, not
   only the active one; a space of another model can still be created, backfilled and compared.
2. The query instruction is per-space configuration, recorded with the space: a bge-m3 space gets none (or its own),
   Qwen3-Embedding spaces get KB's pinned one; the README quickstart's retrieval must not regress.

Everything else in the approved plan stands. Changed: Outcome, Decisions 2, 3, 4, 5, 8 and new 11, items 2-5 and 7-10
(and the order of the instruction and schema items), Documentation, Testing, Operations.

## Outcome

KB carries `docs/design/adr/0015-cross-layer-embedding-identity-policy.md` byte-identical to memory-engine's. The
`KNOWLEDGE_BASE_EMBED_*` declaration covers the whole ADR-0015 tuple, and `llama.cpp` is a declarable provider, so KB
can record exactly the §5 identity ME records. Every `embed_spaces` row records the nine fields (schema v2 adds the four
served-artifact columns, with no data migration). One comparison, field by field, refuses any difference with a typed
`EmbeddingModelMismatchError`: at startup (exit 78, naming each differing field and its variable) and at the single
point every embedding call takes its provider from (ingest, folder summaries, queries on the active or a named space,
backfill, promote), where each space is checked against its own recorded identity: a space of another model can still
be created, backfilled and compared when the declared endpoint can serve it. Every query, and no document, is sent as
the queried space's instruction followed by the text (KB's pinned ADR §6 instruction for Qwen3-Embedding spaces, none
for others unless the space records its own), applied only in `embeddings.embed_query`; a transport-level stub embedder
asserts both request bodies.

## Constraints

- "`docs/design/adr/0015-cross-layer-embedding-identity-policy.md` is added, byte-identical to ME's copy after
  dutiona/memory-engine#1017 (`diff` prints nothing)." Source: memory-engine `origin/main` (97c8e77), sha256
  `b4b6e60ff7820525bba7ac74b49c87ab0f78e90b4064d22d8a5643ca68346016`. Its wording is not edited; infra#77's §5 change is
  not anticipated.
- "`embed_spaces` records the full identity, including dim (unchecked today) and the served-artifact fields. Their
  configured values come from the environment, extending dutiona/knowledge-base#569's variable set. A mismatch in any
  field makes startup exit non-zero."
- "Queries carry KB's pinned instruction and documents never do; the stub embedder asserts the request bodies."
  Under Michaël's ruling 2 this holds for Qwen3-Embedding spaces; other spaces carry their own instruction or none
  (Decision 11 lists it among the departures the PR names).
- ADR-0015 wins over any restatement and over KB's own ADR-0018 where they disagree.
- §1 tuple, names normative: `model, provider, dim, matryoshka_base_dim, element_type, artifact_sha256, pooling,
  normalization, max_input_tokens`; the last four are the served artifact, "all four or none".
- §3: "compatible iff they are field-by-field equal. An absent served artifact equals only another absent one." A typed
  error, "never a warning that proceeds", at the embedding boundary (ingest and query) and at startup.
- §4: changing any served-artifact field, or adopting one on a store recorded without one, is a reconstruction;
  "A reconstruction re-embeds with a provider that reports exactly the target identity."
- §6, KB's instruction, verbatim: `Instruct: Given a search query, retrieve relevant document passages.\nQuery: `
  (`\n` is one U+000A; one trailing space). Prepended "to every query automatically; callers never build it";
  documents never carry one; "policy, not identity".
- Served-artifact validation as ME's `ServedArtifact::from_parts`: sha256 exactly 64 lowercase hex digits; pooling and
  normalization non-empty; max tokens at least 1; a partial declaration names the missing fields.
- Startup refusal exits 78 (#569); a settings refusal starts with the variable's name.
- Keep `ingest.py` changes minimal: KB#592 and KB#597 land after this ticket there. Reuse #543's `stub_embedder` /
  `stub_document_embedder` and its single `ingest.ingest_text`.
- Out of scope: truncating input longer than `max_input_tokens` (ADR: each layer's ingest concern; reported as a
  follow-up), the §5 wording (infra#77), redaction (#592), file-ingest rollback (#597).
- Public repository: no home paths, tailnet names, host addresses or tokens committed. infra#77's served-artifact values
  appear in docs and examples only; tests use synthetic ones.
- Branch commits: Conventional Commits, no co-author line. Only Michaël merges.

## Decisions

1. **`matryoshka_base_dim` and `element_type`.** `KNOWLEDGE_BASE_EMBED_MATRYOSHKA_BASE_DIM` is optional: unset means
   null, otherwise an integer greater than dim. `element_type` has no variable: KB declares `float32`, because ADR-0015
   §1 reserves `int8` and KB's ADR-0017 §7 allows int8 only after whitening, which KB does not have. A store whose
   active space records null and `float32` (every store built without `--matryoshka-base-dim` or an int8 promote)
   starts unchanged. `re-embed --matryoshka-base-dim` goes (the variable replaces it). Upgrade consequences: a store
   whose active space is Matryoshka must declare its recorded `KNOWLEDGE_BASE_EMBED_MATRYOSHKA_BASE_DIM` or it refuses
   to start; an int8 space can no longer be created or promoted, and a store whose active space is int8 refuses to start
   until re-embedded.
2. **Served artifact: `embed_spaces` only.** The active row is the store's identity (§4). `config` keeps exactly the
   provider/model/dim rows #569 binds and `rebind` repairs. A blank database's `default` space records the whole
   declaration at bootstrap; a legacy database's records the config-row identity with no served artifact; new spaces
   record the endpoint's fields from the declaration and their own shape
   (Decision 8). The two config rows `_swap_active` writes and nothing reads (`embed_matryoshka_base_dim`,
   `embed_element_type`) are dropped in a boy-scout commit.
3. **Schema v2.** `migrate.CURRENT_SCHEMA_VERSION = 2`; `_MIGRATIONS = {2: (add_v2_space_columns, False)}`.
   The function adds `artifact_sha256 TEXT, pooling TEXT, normalization TEXT, max_input_tokens INTEGER` (no CHECK, as
   in ME: the boundary validates) and `query_instruction TEXT` (Decision 5) to `embed_spaces`, each only if absent, and commits nothing (`migrate.migrate` wraps
   each step in its own `BEGIN IMMEDIATE`). The base DDL is not changed: `db._run_migrations` calls the same function,
   so fresh, legacy-unversioned and stamped-v1 databases all get the columns through one `ALTER` path before they are
   stamped v2. A stamped-v1 database exits 78 at startup with the
   existing `database schema v1 is behind v2; run \`knowledge-base-ingest migrate\`` (which backs up first). After
   `migrate` its spaces have no served artifact: it starts under a declaration without one, and declaring one is a
   `knowledge-base-ingest re-embed`.
4. **One gate at every embedding boundary, each space against its own identity (Michaël's ruling 1).** The declared
   endpoint is the only embedder, so what it can produce for a space is that space's *serving identity*:
   `settings.serving_identity(declared, space)` takes the space's own shape (`dim`, `matryoshka_base_dim`) and the
   endpoint's own fields (`provider`, the four served-artifact fields, `element_type` `float32`). `model` is the
   space's own only when the declared endpoint routes requests by model, `settings.routes_by_model(declared)`: the
   provider is `ollama` (Ollama loads the model named in the request), or `openai` whose normalized declared URL is
   exactly the canonical origin `https://api.openai.com` (KB accepts any URL for `openai`, so another scheme, port,
   path or host may be a single-model server that ignores the name, and plain HTTP authenticates nothing); and the
   declaration carries no served artifact (an artifact pins its model). Otherwise `model` is the declared one,
   fail-closed: `onnx` loads `ONNX_EMBED_MODEL_PATH` whatever the request names, `llama.cpp` serves its one file, and
   an `openai_compat` backend may be a single-model server (TEI, vLLM) that ignores or rejects the name. The active
   space is the store's identity, so a call into it compares against `declared_identity(declared)` (all nine);
   a call into any other space compares against `serving_identity`.
   `embed_swap.declared_provider_config(conn, space)` runs `settings.require_identity` with the expected identity
   chosen that way (a space is the active one when its own row says `status = 'active'`), field by field over the
   nine, then returns the declared endpoint. The space's `dim` and
   `matryoshka_base_dim` are its own choice; the provider's returned length is still checked against them on every call
   (the existing `expected_dim` check), so a drifted shape fails loudly, never silently. Every embedding call takes its provider there:
   `_embed_with_config`, `update_folder_summary`, `search` (named or active space, `hybrid`/`vec` only),
   `backfill_space`, `promote_space`; so nothing embeds without passing it. Consequences: under an Ollama or
   api.openai.com declaration without a served artifact, a space of another model of the same provider is created, backfilled,
   queried and compared as today (#569's rule kept); under `llama.cpp`, `onnx` or `openai_compat`, or any declaration
   with a served artifact, a space of another model, or one recorded under another served artifact or provider, is
   refused, because that endpoint cannot be shown to produce its vectors. For Michaël: `openai_compat` is pinned by
   default (safe); allowing it would accept ADR-0015's named residual risk of a server that ignores the request's
   model. The store's own identity is stricter: startup (item 9), `promote_space` and `re-embed` still require the full
   declaration, so the active space always is the declared identity. `assert_embed_identity_match` and
   `_assert_declared_identity` are deleted, superseded by the gate and the promote check.
5. **The query instruction is per space (Michaël's ruling 2).** `embed_spaces.query_instruction TEXT` (nullable,
   schema v2) records the space's instruction: NULL means "the default for its model", `''` means none, any other text
   is that space's own instruction. `embeddings.default_query_instruction(model) -> str` is
   `embeddings.QUERY_INSTRUCTION` (ADR §6's KB string) when `"qwen3-embedding"` is in `model.lower()` (matching
   `Qwen/Qwen3-Embedding-0.6B`, Ollama's `qwen3-embedding:0.6b` and the README's `qwen3-embedding`), else `''`.
   `db.space_query_instruction(space) -> str` resolves the recorded value or the default. `embeddings.embed_query(text,
   instruction, model, expected_dim=None, *, _provider_cfg)` returns `embed([instruction + text], ...)[0]`: the only
   expression that builds a query input; `search` passes the queried space's resolved instruction. `embed_single`
   (search was its only caller) is deleted; `embed` and the providers never prefix, so no document path can. Recording
   NULL for the default keeps the pinned string in one place in code, so an ADR §6 change re-embeds nothing and
   rewrites no row. The instruction is not identity: startup does not compare it. The README quickstart (bge-m3)
   sends exactly the query text, byte for byte as today, so its retrieval cannot change; item 4 tests that body.
6. **Startup test.** In-process `server.main([])`, parametrized over the nine fields plus "served artifact adopted" and
   "dropped", with a positive control, plus one subprocess run of the adopted case.
7. **`llama.cpp` is a provider.** ADR-0015 §1 makes `provider` the serving backend and §5 pins `llama.cpp` for both
   layers; ADR-0018 §5 made KB's `provider` the API family, which cannot express it. `KNOWLEDGE_BASE_EMBED_PROVIDER`
   accepts `llama.cpp`, which speaks the `openai_compat` wire (`/v1/embeddings`, same URL and key rules). ADR-0018
   gains an amendment saying so. The existing family names stay valid.
8. **Spaces: the endpoint's fields come from the declaration, the shape from the caller; `re_embed_tool` goes.**
   `create_space` and `create_embed_space_tool` take `name`, `model`, `dim`, `chunk_strategy`, `matryoshka_base_dim`
   and `query_instruction`, which cross-model comparison needs; they record `provider` and the served artifact from
   the declaration (the endpoint that will fill the space) and `element_type` `float32`, and refuse up front a space
   the endpoint cannot serve (Decision 4). The `provider` and `element_type` parameters go: another provider's space
   could never be filled. `re_embed_tool` is deleted: a promoted space must be the declared identity, so the tool could
   only rebuild the running server's own identity, which the CLI already refuses ("use rebind"); moving the store is
   `knowledge-base-ingest re-embed` with the unit stopped. The stdio tool count goes from 48 to 47.
9. **The int8 storage path is deleted.** Under Decisions 1 and 4 no int8 space can be created, filled, queried or made
   active, so `ELEMENT_INSERT_EXPR`/`ELEMENT_QUERY_EXPR`'s int8 entries, `get_space_element_type` and the int8 sizing in
   `benchmark_spaces_tool` become unreachable; this PR made them dead, so it removes them (own commit). The
   `element_type` column and its CHECK stay: old databases may hold int8 rows, and ADR-0015 reserves the value. Re-adding
   int8 belongs with whitening (ADR-0017 §7).
10. **No in-repository parity test of the ADR copy.** ADR-0015 says audit "SHOULD assert the two copies match", but KB's
   tests cannot reach memory-engine, and a pinned-digest test would be a snapshot of the other repository's text that
   breaks on every lockstep change. The PR shows the `diff`; lockstep stays a review obligation.
11. **Where the rulings depart from the ADR text, KB follows Michaël and says so.** ADR-0015 §6 says each store's
   client prepends its instruction "to every query" and that the instruction "is not recorded in the store". Under
   ruling 2 a non-Qwen3 space gets none, and an explicit per-space value is recorded (the default is not: NULL). The
   byte-identical ADR copy is not edited here (lockstep, and infra#77's §5 pair is pending); the PR names both
   sentences so the next lockstep pair can amend §6 in both repositories, and states that #573's acceptance line
   "Queries carry KB's pinned instruction" now holds for Qwen3-Embedding spaces only. Ruling 1 needs no ADR change: §3 compares a
   provider's identity with "the store's recorded identity", and KB's multi-space store records one per space.

## Implementation sequence

`FULL` (in `tests/test_embed_identity.py`) is the suite declaration plus a synthetic served artifact:
`_ARTIFACT_SHA256="a" * 64`, `_POOLING=last`, `_NORMALIZATION=l2`, `_MAX_INPUT_TOKENS=2048`. The transport stub is an
`http_requests` handler for the suite's embedder host that answers `/api/embed` and `/v1/embeddings` with one unit
vector per input at a dim the test passes (Ollama's body carries no dimension, as in `tests/test_indexer.py`'s
re-embed test) and records each JSON body. Every item's Green ends with the full suite,
`uv run pytest tests/ -q -m "not slow"`, so each commit stands on its own.

- [ ] **1. ADR-0015 copy.**
  - Files: `docs/design/adr/0015-cross-layer-embedding-identity-policy.md` (new).
  - No executable behaviour. `git -C <memory-engine checkout> show
    97c8e77:docs/design/adr/0015-cross-layer-embedding-identity-policy.md > docs/design/adr/0015-...md`, never retyped
    (97c8e77 is the ME#1017 merge the acceptance names; `origin/main` can move with infra#77's lockstep pair). If
    `origin/main`'s copy differs from 97c8e77's at PR time, stop and ask Michaël.
  - Green: `sha256sum` prints the Constraints value; the same `show` piped to `diff -` prints nothing.
  - Commit: `docs(adr): add ADR-0015, byte-identical to memory-engine's`
- [ ] **2. Version-proof the schema-version tests.**
  - Files: `tests/test_schema_migrate.py`, `tests/test_indexer.py`.
  - Test-only, no red: a literal `1` meaning "current" becomes `CURRENT_SCHEMA_VERSION`, a monkeypatched `2` (and its
    `_MIGRATIONS` key) becomes `CURRENT_SCHEMA_VERSION + 1`.
  - Green: both files pass unchanged in outcome.
  - Commit: `test(migrate): derive schema versions from CURRENT_SCHEMA_VERSION`
- [ ] **3. Schema v2.**
  - Files: `src/knowledge_base/migrate.py`, `db.py`, `tests/test_embed_identity.py` (new).
  - Red (`uv run pytest tests/test_embed_identity.py -q -k "v1 or v2"`):
    - `test_every_origin_converges_to_one_v2_shape`: a fresh database, a v1 database put through `migrate.migrate`, and
      a legacy-unversioned one put through `init_schema` end stamped `CURRENT_SCHEMA_VERSION` with equal
      `PRAGMA table_info(embed_spaces)`, and the five new columns NULL on pre-existing rows. The v1 database is built by
      the v1 path itself: `init_schema` with `migrate.CURRENT_SCHEMA_VERSION` patched to 1 and
      `migrate.add_v2_space_columns` patched to a no-op (db reads migrate's attributes live, #450; the base DDL has no
      new columns, Decision 3); the legacy-unversioned one is the same with `schema_version` deleted. Both fixtures
      assert the five columns are absent before converging, so the `ALTER`s really run.
    - `test_a_v1_database_refuses_to_start_until_migrated`: with a chunk and its vector stored, `server.main([])` exits
      78 naming `knowledge-base-ingest migrate`, `mcp.run` not called; CLI `migrate` writes a backup and keeps the
      vector; `server.main([])` then serves under the unchanged declaration.
  - Change: Decision 3, with a fifth column: `migrate.add_v2_space_columns(conn)` adds `artifact_sha256 TEXT`,
    `pooling TEXT`, `normalization TEXT`, `max_input_tokens INTEGER` and `query_instruction TEXT` (Decision 5), each
    only if absent, committing nothing. `EmbedSpace` gains the five keys. In `_run_migrations`, the bootstrap moves after
    `_migrate_embed_spaces_matryoshka`, `_migrate_embed_spaces_element_type` and `add_v2_space_columns`, so every column
    it writes exists; the order note says why.
  - Green: the Red command plus `tests/test_schema_migrate.py tests/test_db.py tests/test_indexer.py`, then the full
    suite.
  - Commit: `feat(db): schema v2 records the served artifact and the query instruction in embed_spaces`
- [ ] **4. Each space's query instruction.**
  - Files: `src/knowledge_base/embeddings.py`, `db.py`, `search.py`, `tests/conftest.py`,
    `tests/test_embed_identity.py`, `tests/test_embeddings.py`; the `knowledge_base.search.embed_single` patch targets
    in the tests that use it.
  - Red (`uv run pytest tests/test_embed_identity.py -q -k instruction`):
    - `test_a_qwen3_space_queries_carry_the_pinned_instruction`, over `search(mode="vec")` on the active space, on
      `space_name=` a named space, and on an active space with `matryoshka_base_dim=2048` (the truncating branch; the
      test also sets `KNOWLEDGE_BASE_EMBED_MATRYOSHKA_BASE_DIM=2048`, unread until item 5, so it stays valid under item
      10's gate), each space's `model` set to `Qwen/Qwen3-Embedding-0.6B` (and the declaration's model with it): exactly
      one body, `input == [LITERAL + "attention"]`, `LITERAL` written out in the test from ADR §6.
    - `test_the_readme_quickstart_query_is_unchanged`: the suite's bge-m3 space sends exactly `["attention"]`, the bytes
      it sends today.
    - `test_a_space_records_its_own_instruction`: with `query_instruction` set to `"Represent this: "` the body is that
      plus the query; set to `''` on a Qwen3 space, the raw query.
    - `test_no_document_carries_the_instruction`, on a Qwen3 space: `ingest_text`, `update_folder_summary`,
      `backfill_space` and `promote_space` (with a folder summary seeded first, so promotion re-embeds it) each send at
      least one body; no input contains `"Instruct:"`; `ingest_text`'s inputs equal the stored chunk contents.
    - `test_the_instruction_is_the_adrs`: the ADR copy's Knowledge-layer row, with `\n` read as U+000A, equals
      `QUERY_INSTRUCTION`.
  - Change: Decision 5 (`QUERY_INSTRUCTION`, `default_query_instruction`, `db.space_query_instruction`, `embed_query`).
    `search` resolves the queried space's instruction; with no active space (a store `init_schema` never bootstrapped)
    it uses `default_query_instruction` of the configured model. `stub_embedder` patches
    `knowledge_base.search.embed_query` and still records the raw query (`kb_http` cannot share the transport stub).
    `test_embeddings.py`'s two `embed_single` tests become: `embed_query` sends the instruction plus the text; `embed`
    sends the text unchanged. Helpers named `_fake_embed_single` keep their names.
  - Green: the Red command plus `tests/test_embeddings.py tests/test_search.py tests/test_reranker.py
    tests/test_comparison.py tests/test_folder_summaries.py tests/test_server_chunking.py tests/test_http_auth.py
    tests/test_lean_surface.py`, then the full suite; `git grep -nw embed_single -- src tests` prints nothing.
  - Commit: `feat(embeddings): prefix each query with its space's instruction, KB's pinned one for Qwen3-Embedding`
- [ ] **5. Declare the whole tuple; one comparison; one typed error.**
  - Files: `src/knowledge_base/settings.py`, `exceptions.py`, `tests/test_settings.py`, `tests/conftest.py`.
  - Red (`uv run pytest tests/test_settings.py -q`):
    - a full declaration parses to the typed values; declaring nothing new gives `None`, `"float32"`, four `None`;
    - a partial served artifact (each subset of one to three) raises a message starting with the first missing
      variable and naming every missing one;
    - each invalid value raises a message starting with its variable: sha256 upper-case, 63 digits or non-hex; tokens
      `0`, `-1`, `2k`; matryoshka `abc`, equal to dim, below dim;
    - `identity_mismatch` returns `[]` for an equal record, exactly `[field]` when only that field differs (each of the
      nine), and exactly the four served fields when the artifact is absent on one side only (both directions);
      `require_identity` raises `EmbeddingModelMismatchError` naming every differing field and its variable.
    - `serving_identity(declared, space)` keeps the space's dim and matryoshka_base_dim always; it keeps the space's
      model only when `routes_by_model(declared)` (`ollama`, or `openai` at exactly `https://api.openai.com`, without
      a served artifact; a positive case for each), and uses the declared model for `llama.cpp`, `onnx`,
      `openai_compat`, `openai` at `http://api.openai.com`, at another port, path or host, and any declaration with an
      artifact; `provider`, the four served fields and
      `element_type` always come from the declaration.
  - Change:
    - `VARIABLES` gains `matryoshka_base_dim`, `artifact_sha256`, `pooling`, `normalization`, `max_input_tokens` →
      `KNOWLEDGE_BASE_EMBED_<FIELD>`; the "KB-4 adds" comment goes. `IDENTITY_FIELDS` lists §1's nine names in order.
    - `EmbedSettings` gains `matryoshka_base_dim: int | None = None`, `element_type: str = "float32"` and the four
      served fields (default `None`). One positive-integer parser (today's dim rule) serves dim, matryoshka and tokens.
    - `routes_by_model(declared: EmbedSettings) -> bool` (Decision 4), `declared_identity(declared: EmbedSettings) ->
      dict[str, object]` (the nine, §1 order) and
      `serving_identity(declared, space: Mapping[str, object]) -> dict[str, object]` (Decision 4).
    - `identity_mismatch(recorded: Mapping[str, object], expected: Mapping[str, object]) -> list[str]`, in §1 order.
    - `require_identity(recorded, expected, *, subject: str, remedy: str) -> None` raises
      `f"{subject} records {clauses}; {remedy}"`, one clause per differing field:
      `f"{field} {recorded!r} ({VARIABLES.get(field, 'KB')} declares {expected!r})"`.
    - `exceptions.EmbeddingModelMismatchError(KnowledgeBaseError)`, §3's typed error (ME:
      `EmbeddingModelMismatch`); as a `KnowledgeBaseError` it keeps exit 78, CLI exit 1 and every route mapping.
    - `_declared_environment` also deletes the five new variables, so a shell cannot leak a served artifact in.
  - Green: the Red command, then the full suite (nothing reads the fields yet).
  - Commit: `feat(settings): declare the whole ADR-0015 identity`
- [ ] **6. `llama.cpp` is a declarable provider.**
  - Files: `src/knowledge_base/settings.py`, `embeddings.py`, `docs/design/adr/0018-provider-abstraction.md`,
    `tests/test_settings.py`, `tests/test_embeddings.py`.
  - Red: `KNOWLEDGE_BASE_EMBED_PROVIDER=llama.cpp` parses (its URL normalized like `openai_compat`, `/v1` stripped);
    `get_provider(cfg=ProviderConfig("llama.cpp", url))` is an `OpenAICompatProvider`; its `embed` posts to
    `/v1/embeddings` with the declared key, the whole JSON body pinned (`model`, `input`, and `dimensions` as KB
    sends it today; whether llama-server accepts `dimensions` is unverified here and shows at the first deployed
    ingest, loudly) (transport stub plus `fake_dns`).
  - Change: `EMBED_PROVIDERS` and `_OPENAI_FAMILIES` gain `llama.cpp`; `_build_provider` maps it to
    `OpenAICompatProvider`. ADR-0018 gets "Amendment (2026-11-17, #573)": `provider` names the serving backend where
    ADR-0015 §5 pins one; `llama.cpp` speaks the `openai_compat` wire.
  - Green: `uv run pytest tests/test_settings.py tests/test_embeddings.py -q`, then the full suite.
  - Commit: `feat(embeddings): accept llama.cpp as the provider ADR-0015 §5 pins`
- [ ] **7. A blank store records the whole declaration.**
  - Files: `src/knowledge_base/db.py`, `tests/test_embed_identity.py`, `tests/test_db.py`.
  - Red: `test_a_blank_store_records_the_declaration`: under `FULL` plus `_MATRYOSHKA_BASE_DIM=2048`,
    `open_declared_db().close()` leaves a `default` row whose `IDENTITY_FIELDS` equal the declaration's. Guard
    (passes before and after): a legacy database (config rows only) initialized under `FULL` records the four NULL.
  - Change: `_seed_default_config(conn) -> tuple[int, EmbedSettings | None]` also returns the declaration it seeded a
    blank database from; `_bootstrap_embed_spaces(conn, embed_dim, declared)` writes every `IDENTITY_FIELDS` column
    from it, or today's legacy row without it, and `query_instruction` NULL (its model's default, Decision 5);
    `_run_migrations` passes it through.
  - Green: the Red command plus `tests/test_db.py tests/test_embed_spaces.py`, then the full suite.
  - Commit: `feat(db): a blank store records the whole declared identity`
- [ ] **8. Spaces: the endpoint's fields from the declaration, the shape from the caller (breaking).**
  - Files: `src/knowledge_base/embed_swap.py`, `routes/embeddings.py`, `indexer.py`, `utils.py`; tests
    `test_embed_identity.py`, `test_embed_spaces.py`, `test_embed_swap.py`, `test_comparison.py`, `test_db.py`,
    `test_routes_embeddings.py`, `test_indexer.py`, `test_server.py`, `test_utils.py`.
  - Red (`uv run pytest tests/test_embed_identity.py -q -k new_space`):
    - `test_a_new_space_records_its_shape_and_the_endpoint`: under the suite (Ollama, no served artifact),
      `create_space(conn, "alt", "nomic-embed-text", 768)` records that model and dim with the declared provider, no
      served artifact, `float32` and `query_instruction` NULL; `create_embed_space_tool(name="alt2", model=...,
      dim=..., query_instruction="")` records `''`.
    - `test_a_single_model_endpoint_refuses_a_space_of_another_model`, parametrized over `FULL` (an artifact),
      `onnx` (no artifact), `llama.cpp` without an artifact, `openai_compat`, and `openai` at a non-canonical URL: `create_space` of another model raises
      `EmbeddingModelMismatchError` naming `model` and creates nothing; one of the declared model with another `dim` and
      `matryoshka_base_dim` is created.
    - `re_embed(conn)` (transport stub) leaves the active space recording the declaration's nine fields.
    - `test_build_parser_re_embed_takes_its_identity_from_the_declaration` also refuses `--matryoshka-base-dim 2048`.
  - Change:
    - `create_space(conn, name, model, dim, chunk_strategy="mechanical", matryoshka_base_dim=None,
      query_instruction=None) -> dict` (Decision 8): `provider` and the served artifact from `load_embed_settings()`,
      `float[{dim}]`; it runs the item-5 `require_identity` against `serving_identity` before inserting. Its element-type
      check goes; `utils.VALID_ELEMENT_TYPES` loses its only caller and goes.
    - `re_embed(conn, batch_size=32) -> dict` builds from the declaration, named `<model>_<dim>` as today;
      `cmd_re_embed` passes only `batch_size`; `--matryoshka-base-dim` goes.
    - `create_embed_space_tool(name, model, dim, chunk_strategy="mechanical", matryoshka_base_dim=None,
      query_instruction=None)`; `re_embed_tool` is deleted (Decision 8).
    - Existing space tests drop the `provider` and `element_type` arguments; a foreign-provider space (for refusal
      tests) is made by `UPDATE embed_spaces`. `test_server.py`'s tool inventory drops `re_embed_tool` (47 tools).
    - Deleted with the behaviour they test (int8 cannot be created, Decision 1): in `test_embed_spaces.py`
      `test_create_space_with_int8_element_type`, `test_create_space_invalid_element_type`, `test_backfill_int8_space`,
      `test_search_int8_space`, `test_promote_int8_space_syncs_config`, `test_get_space_element_type_int8` and
      `test_insert_chunk_vec_auto_resolves_element_type`; `test_db.py`'s `test_char_delete_chunk_vecs_int8_space` (it
      builds its int8 space through `create_space`); `test_routes_embeddings.py`'s `re_embed_tool` tests. Each is
      listed in the PR.
    - Docs two tests couple to the tool set, in this commit: `docs/reference/mcp-tools.md` loses `### re_embed_tool`
      and gets `create_embed_space_tool`'s new parameters (`test_the_reference_documents_every_stdio_tool`), and
      README, AGENTS, GEMINI and mcp-tools state "47 tools over stdio" (`test_docs_state_the_counts_per_surface`).
  - Green: those test files, then the full suite.
  - Commit: `feat(embed_swap)!: new spaces take the endpoint's identity fields from the declaration`
- [ ] **9. Startup compares all nine fields.**
  - Files: `src/knowledge_base/db.py`, `tests/test_embed_identity.py`, `tests/test_db.py`, `tests/test_server.py`,
    `tests/test_indexer.py`.
  - Red (`uv run pytest tests/test_embed_identity.py -q -k "startup or adopt"`):
    - `test_startup_refuses_a_mismatch_in_any_field`: seed under `FULL` plus `_MATRYOSHKA_BASE_DIM=2048`, then change
      one thing per case: model, provider (`openai_compat`), dim (`768`), matryoshka (unset), element_type (the active
      row set to `int8`), artifact_sha256 (`"b" * 64`), pooling (`mean`), normalization (`none`), max_input_tokens
      (`512`), served artifact dropped, served artifact adopted on a store seeded without one. Each: `server.main([])`
      raises `SystemExit(78)`, stderr names the field, its variable (for `element_type`, which has none, the clause
      reads `KB declares 'float32'`) and `knowledge-base-ingest re-embed`, `mcp.run` is
      not called, `config` and `embed_spaces` rows are unchanged.
    - `test_startup_accepts_the_identity_it_recorded`: unchanged `FULL` serves, with and without
      `_MATRYOSHKA_BASE_DIM` (a migrated Matryoshka store starts once its base dim is declared).
    - `test_the_server_process_exits_78_on_an_adopted_served_artifact`: the adopted case through
      `python -m knowledge_base.server` (the `_run_server` pattern); stderr names
      `KNOWLEDGE_BASE_EMBED_ARTIFACT_SHA256`.
    - `test_adopting_a_served_artifact_is_a_re_embed`: on a store recorded without one, with the four declared, CLI
      `rebind` exits 1 naming them; CLI `re-embed` (transport stub) succeeds; `open_declared_db` then passes, the
      active space records the artifact and the deprecated `default` keeps NULL.
  - Change: `bind_embed_config` compares with `identity_mismatch(dict(active), declared_identity(declared))`: the plain
    bind and `rebind` call `require_identity(..., subject=f"the active space {active['name']!r}", remedy=_REMEDY_RE_EMBED)` before the
    config-row checks (replacing the per-key loop); `re-embed` refuses only an empty mismatch ("use rebind"). The
    config identity, URL and key rows keep their #569 rules, and every check still runs before any write. #569
    assertions that matched a config key for an active-space refusal now match the field or variable.
  - Green: the Red command plus `tests/test_db.py tests/test_server.py tests/test_indexer.py`, then the full suite.
  - Commit: `feat(db): refuse to start unless the active space records the whole declared identity`
- [ ] **10. Every embedding boundary checks the space against its own identity.**
  - Files: `src/knowledge_base/embed_swap.py`, `search.py`, `ingest.py` (`_embed_with_config` and imports only),
    `folder_summaries.py`, `routes/embeddings.py`, `tests/test_embed_identity.py`, `tests/test_embed_swap.py`,
    `tests/test_routes_embeddings.py`.
  - Red (`uv run pytest tests/test_embed_identity.py -q -k "boundary or cross_model or promote_requires or keyword"`):
    - `test_every_embedding_boundary_refuses_a_space_the_endpoint_cannot_serve`, over `ingest_text`,
      `update_folder_summary`, `search` on the active space, `search(space_name=...)`, `backfill_space` and
      `promote_space`, each with the target space's `provider` and, separately, its `artifact_sha256` changed by
      `UPDATE` (as another process would leave it); the active-space cases also cover `model`, `dim` and
      `matryoshka_base_dim` drift (compared with the full declaration); the named-space cases also cover a space of
      another model under `onnx` and under `FULL`: each raises `EmbeddingModelMismatchError` naming the field,
      `http_requests == []`, and chunks, vectors, spaces and config are unchanged.
    - `test_cross_model_comparison_keeps_each_space_whole`: under the suite (Ollama), a `qwen3-embedding:0.6b` space is
      created, backfilled and compared with the bge-m3 `default` through `compare_spaces_tool`; its backfill and query
      requests name its own model, its query carries the pinned instruction while `default`'s carries none, and its
      vectors land in its own table. A guard, green before and after this item: it proves the new gate does not refuse
      a servable space (ruling 1). Item 10's red is the boundary matrix above.
    - `test_promote_requires_the_declared_identity`: promoting that servable space of another model is refused naming
      `model` (only the declared identity becomes active, so startup keeps passing).
    - `test_a_keyword_search_embeds_nothing_and_checks_nothing`: `search(mode="fts")` on a drifted store returns its hit.
    - `compare_spaces_tool`, `batch_compare_spaces_tool` and `benchmark_spaces_tool` report the error (not raise) for
      a space the endpoint cannot serve.
    - `update_folder_summary` itself raises the typed error (in the matrix above). `_update_folder_summary_safe`, which
      file ingest calls after its chunks committed, keeps logging and proceeding: the refused summary embeds nothing,
      so no vector is ever written under another identity (ADR §3's rejection holds), while re-raising there would
      report a failure for a file whose chunks are durable. It is reachable only when another process changes the
      active space between the two gates, which the unit-stopped rule for `re-embed` forbids; the next ingest then
      fails loudly at `_embed_with_config`.
  - Change: Decision 4. `declared_provider_config(conn, space: Mapping[str, object]) -> ProviderConfig` runs
    `require_identity(space, expected, subject=f"space {space['name']!r}", remedy=...)` with `expected` =
    `declared_identity(declared)` for the active space and `serving_identity(declared, space)` for any other;
    `promote_space` also runs `require_identity(space, declared_identity(declared), ...)` before anything else.
    `db.require_active_space(conn) -> EmbedSpace` raises today's "no active embedding space" message.
    `_embed_with_config` and `update_folder_summary` embed with `require_active_space(conn)`'s model, dim and
    matryoshka_base_dim (their `get_embed_config` fallbacks go, and so does item 4's no-active-space instruction
    fallback in `search`); `search` resolves the named or active space only in
    `hybrid`/`vec` mode and takes the provider before either leg, so `fts` on a store without an active space keeps
    working; `backfill_space` and `promote_space` pass `dict(row)` (a `sqlite3.Row` is not a `Mapping` for
    basedpyright). `_update_folder_summary_safe` is unchanged.
    `compare_spaces_tool`, `batch_compare_spaces_tool` and `benchmark_spaces_tool` also catch `KnowledgeBaseError`.
    `test_embed_swap.py`'s `assert_embed_identity_match` tests and `test_embed_with_config_rejects_identity_mismatch`
    go (superseded by the boundary matrix); its three "another provider" tests expect the typed error naming
    `provider`.
  - Green: the Red command plus `tests/test_embed_swap.py tests/test_embed_spaces.py tests/test_comparison.py
    tests/test_search.py tests/test_folder_summaries.py tests/test_ingest.py tests/test_routes_embeddings.py`, then
    the full suite.
  - Commit: `feat(embed_swap): check every space against its own identity at each embedding boundary`
- [ ] **11. Boy-scout: stop writing two config rows nothing reads.**
  - Files: `src/knowledge_base/embed_swap.py` (`_swap_active`), `tests/test_embed_spaces.py`.
  - Red: after a promote, the `embed_*` config keys are exactly `embed_provider`, `embed_model`, `embed_dim`,
    `embed_base_url` (plus `embed_api_key` when declared); today `embed_matryoshka_base_dim` and `embed_element_type`
    are also written.
  - Change: `_swap_active` stops writing them; existing rows stay, unread.
  - Green: `uv run pytest tests/test_embed_spaces.py -q`, then the full suite; `git grep -nE
    "embed_matryoshka_base_dim|embed_element_type" -- src` prints nothing.
  - Commit: `refactor(embed_swap): stop writing never-read matryoshka and element-type config rows`
- [ ] **12. Delete the int8 storage path this PR made unreachable (Decision 9).**
  - Files: `src/knowledge_base/utils.py`, `db.py`, `search.py`, `embed_swap.py`, `routes/embeddings.py`;
    `tests/test_utils.py`, `tests/test_search.py`, `tests/test_embed_spaces.py` (imports and tests
    `get_space_element_type`), `tests/test_routes_embeddings.py` (`test_benchmark_happy_storage_ratio_int8_quarter`
    goes).
  - No new red: behaviour-preserving for every reachable state (float32 only). Before deleting, `git grep -nE
    "int8|ELEMENT_(INSERT|QUERY)_EXPR|get_space_element_type|_BYTES_PER_ELEMENT" -- src` lists every site; each is
    reduced to the float32 form (`?` insert, plain float query, 4 bytes per element).
  - Green: the full suite; the same grep shows only the `embed_spaces.element_type` CHECK and its docstring.
  - Commit: `refactor(embeddings): drop the int8 storage path no space can reach`
- [ ] **13. Boy-scout: `ingest_directory` summarizes the folder it ingested, not its parent.**
  - Files: `src/knowledge_base/ingest.py`, `tests/test_folder_summaries.py`.
  - Red: `test_ingest_directory_batches_folder_summaries` also asserts `update_folder_summary` is called with each
    ingested folder; today `ingest_directory` collects `f.resolve().parent` and `_update_folder_summary_safe` takes
    `path.parent` again, so the parent folder is summarized and the ingested one never is.
  - Change: `_update_folder_summary_safe(conn, folder: str)` takes the folder; its two file callers pass
    `path.parent.as_posix()`, `ingest_directory` collects and passes `f.resolve().parent.as_posix()` (POSIX form,
    matching the forward-slash `source_uri`s #158 enforces).
  - Green: `uv run pytest tests/test_folder_summaries.py tests/test_ingest.py -q`, then the full suite.
  - Commit: `fix(ingest): summarize the folder ingest_directory ingested, not its parent`
- [ ] **14. Documentation** (see Documentation). No executable behaviour. Green: `git grep -nE
  "re_embed_tool|--matryoshka-base-dim|\bembed_single\b|48 tools" -- . ':!docs/plans' ':!qa' ':!docs/design/adr'`
  prints nothing (`qa/` holds dated audit runs and ADRs keep their history; ADR-0018's amendment records the
  `embed_single` -> `embed_query` rename). Commit:
  `docs: the whole embedding identity, schema v2, llama.cpp and the query instruction`

## Documentation

- `README.md`: Configuration table rows for `KNOWLEDGE_BASE_EMBED_MATRYOSHKA_BASE_DIM` and the four served-artifact
  variables ("all four or none"), `llama.cpp` in the provider row; "an existing database must agree" covers all nine
  fields; changing or adopting any of them is `knowledge-base-ingest re-embed`, which `rebind` refuses. Upgrading: stop
  the unit, `knowledge-base-ingest migrate` (schema v2, backed up), then declare what the store records. The tool
  tables lose `re_embed_tool`; `create_embed_space_tool` takes `name, model, dim, chunk_strategy,
  matryoshka_base_dim, query_instruction` (no `provider`, no `element_type`); tool count 47. The query instruction:
  per space, KB's pinned one for Qwen3-Embedding models, none otherwise (the quickstart's bge-m3 queries are unchanged).
- `AGENTS.md`, `GEMINI.md`, `CLAUDE.md`: tool count 47; `CLAUDE.md`'s Embeddings route row drops `re_embed_tool`.
- `docs/getting-started/installation.md`: the container passes the served-artifact variables; a pre-v2 database needs
  `migrate` with the unit stopped.
- `docs/usage/ingesting-documents.md`: the declaration example uses `llama.cpp` and ADR §5's values (the declared model
  slug selects the default query instruction, so a llama.cpp deployment declares `Qwen/Qwen3-Embedding-0.6B`, not an
  arbitrary name); documents carry no instruction.
- `docs/usage/searching.md`: a query carries its space's instruction (verbatim ADR §6 string for Qwen3-Embedding
  spaces); a named space must be one the declared endpoint can serve.
- `docs/usage/embedding-spaces.md`: create takes the shape (model, dim, Matryoshka, instruction) and records the
  endpoint's fields from the declaration; under a single-file endpoint only the declared model is servable; the
  active space's Matryoshka is declared; int8 is gone; cross-model comparison stays under multi-model endpoints; only
  the declared identity can be promoted; the `re_embed_tool` section becomes the CLI procedure.
- `docs/usage/auto-relationships.md`: models are switched with `knowledge-base-ingest re-embed`, not `re_embed_tool`.
- `docs/reference/schema.md`: `embed_spaces` gains the four served-artifact columns (schema v2; NULL = recorded before
  the served artifact) and `query_instruction` (NULL = the model's default, `''` = none); `provider` lists the accepted names; `chunks_vec`'s stale "dropped and recreated by `re_embed_tool`" goes;
  `config` mirrors only provider/model/dim.
- `docs/reference/mcp-tools.md` (tool list and count already in item 8): the identity error on `search_index`'s
  `space_name` and the compare tools.
- `docs/design/architecture-overview.md`: `settings.py` holds the whole identity and `require_identity`;
  `declared_provider_config` gates every embedding; `embed_query`.
- `.claude/skills/release-checklist/SKILL.md`: `.release-env` passes the five new variables through (`${VAR:-}`).
- `docs/design/adr/0018-provider-abstraction.md`: the amendment (item 6), also recording `embed_single` ->
  `embed_query`.

## Testing

- `tests/test_embed_identity.py` (new) holds the acceptance tests: request bodies (every query branch, every document
  call site, parity with the ADR string), v2 convergence and the v1 startup, blank and legacy bootstrap, new spaces,
  the eleven-case startup matrix plus control and one process run, adoption through `re-embed`, and the boundary
  matrix with the FTS case.
- Unit: `test_settings.py` (parse rules, the comparison, `llama.cpp`), `test_embeddings.py` (`embed_query` prefixes,
  `embed` does not; `llama.cpp` wire).
- The acceptance's "stub embedder" is the transport stub on `http_requests`: it sees the bytes a real endpoint would.
  `stub_embedder` keeps its #543 role on the HTTP surface; `search_index` calls the same `search()`.
- `tests/declared_env.py` is unchanged: the suite records no served artifact, so existing fixtures stay valid.
- Item 13's test pins the folder argument the old `call_count` assertion let slip.
- The README-quickstart guard: the bge-m3 query body is byte-identical to today's (item 4), and cross-model comparison
  under an Ollama declaration keeps working end to end (item 10).
- Adapted, never loosened: version literals (item 2), space-building tests (item 8), active-space refusal regexes
  (item 9), identity-guard tests superseded by the boundary matrix (item 10). Deleted with their behaviour: the int8
  and `re_embed_tool` tests (item 8). Each is named in its item and in the PR.
- Excluded: any real embedder (tests never reach the network; the served file's sha256 is checked at install, ADR
  "Declared, not probed"); no slow tests.

## Verification

```bash
uv lock --check
uv sync
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest tests/ -q -m "not slow"
uv sync --all-groups
uv run basedpyright src/ tests/
sha256sum docs/design/adr/0015-cross-layer-embedding-identity-policy.md   # b4b6e60f...346016
git -C <memory-engine checkout> show 97c8e77:docs/design/adr/0015-cross-layer-embedding-identity-policy.md \
  | diff - docs/design/adr/0015-cross-layer-embedding-identity-policy.md  # prints nothing
git -C <memory-engine checkout> fetch origin main
git -C <memory-engine checkout> diff --quiet 97c8e77 origin/main -- docs/design/adr/0015-cross-layer-embedding-identity-policy.md; echo "rc=$?"   # rc=0, quoted in the PR
git grep -n QUERY_INSTRUCTION -- src     # embeddings.py only
uv run pytest tests/test_embed_identity.py -v
```

Run in order at the PR's final head with a clean tree; quote every result in the PR.

## Operations

1. Worktree `.worktrees/feat-573-embed-identity`, branch `feat/573-embed-identity` from `origin/master` at 252c044,
   locked. One commit per item.
2. Plan issue: this successor gets its own, published by `archive-plan-issue.sh` before the approval handoff and
   linking the approved predecessor's #603; `/finish-pr` Item 5 reuses it. Only the active plan file is committed; the
   drafts directory and the review artifacts (they hold local paths) never are.
3. PR `feat(embeddings)!: enforce the shared ADR-0015 identity with KB's own query instruction`, body with
   `Classification: Feature (researched) (user-confirmed)`, the route lines, the breaking changes (schema v2,
   `re_embed_tool` deleted, `create_embed_space_tool` loses `provider` and `element_type` and gains
   `query_instruction`, `re-embed --matryoshka-base-dim` gone, int8 gone, a Matryoshka store must declare its base dim,
   a single-file endpoint serves only its model's spaces), the two ADR §6 sentences the rulings depart from (Decision
   11), the deploy steps (stop, `migrate`, declare what the store records, `re-embed` to adopt the served
   artifact, start) and the rollback (after `migrate` an older build exits "newer than this build": restore the
   pre-migration backup `migrate` wrote), the gate output, `Refs dutiona/infra#37`, `Closes #573`, and Collateral
   findings; `Refs #604` (bound inputs by `max_input_tokens`) and `Refs #605` (the dead bare-name embed path).
4. `/super-review` at the confirmed Full gate; fixes in new commits; the gate re-runs at the new head.
5. `/finish-pr` to READY. Michaël squash-merges; the squash commit carries exactly one co-author line.
6. Follow-ups, filed by the orchestrator, not done here: #604 (inputs longer than `max_input_tokens`), #605 (the
   bare-name legacy embed path, so "nothing embeds without the gate" stays a convention until it goes), and the
   infra#81 quadlet note (the five new variables and `llama.cpp` as the provider).
