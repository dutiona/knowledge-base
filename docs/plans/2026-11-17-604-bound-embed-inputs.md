# fix(embeddings): bound every input by the served max_input_tokens so one long section can't fail a batch (#604)

## Outcome

When `KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS` is declared, no embedding input KB sends counts more tokens than that,
as the server counts them (end-of-sequence token included). Each count is the embedder's own: llama-server's
`POST /tokenize` with `add_special: true`. Document chunks over the limit are split into consecutive pieces that each
fit and that together are exactly the chunk, so nothing is dropped. Every other input (folder summaries, figure and
image descriptions, queries, stored chunks during a re-embed) is cut down to a prefix that fits, in the provider, so
no caller can send an over-length input. An over-length section or folder summary no longer fails its whole batch
with HTTP 400 `exceed_context_size_error`. Without the variable nothing changes, and no `/tokenize` request is made.

## Constraints

- "Every embedding input is bounded by the active space's `max_input_tokens` (as the server counts, end-of-sequence
  token included) before it is sent: chunks are split, summaries truncated, and the choice is stated per input kind."
- "A test with the stub embedder enforcing the limit proves an over-length section and an over-length folder summary
  no longer fail the batch."
- Served-artifact facts (infra#77, measured 2026-11-17): llama-server counts at most 2,048 tokens
  (`usage.prompt_tokens`, which includes the end-of-sequence token it appends to text), so text must fit in 2,047. One
  token more is HTTP 400 `exceed_context_size_error`, and one over-length element fails its whole batch.
- Counting needs the model's tokenizer or a round-trip to the server: the choice and its cost are stated (Decision 1).
- KB#573 (in flight, plan v2 on #606) records `max_input_tokens` per space. Bound by the active or target space's
  recorded value once #573 has landed, else by configuration. Whichever lands second rebases (Decision 4).
- Keep `ingest.py` changes small: KB#592 and KB#597 also change it.
- Public repository: no home paths, tailnet names, addresses or tokens. Tests use synthetic hosts and limits.
- Branch commits: Conventional Commits, no co-author line. Only Michaël merges.

## Decisions

1. **Counting is a round-trip to the server's `/tokenize`.** In llama.cpp at tag `b12048`, the deployed build, the
   embeddings handler tokenizes each string input with `tokenize_input_subprompt(..., add_special=true,
   parse_special=true)`, which calls `tokenize_mixed(vocab, prompt, true, true)`. `/tokenize` with
   `{"content": text, "add_special": true}` calls `tokenize_mixed` with the same flags (`parse_special` defaults to
   true). So `len(tokens)` is the count the embeddings call checks: BOS when the vocabulary adds one, and EOS
   (`llama-vocab.cpp`: `if (add_special && add_eos)`). This was read in `tools/server/server-context.cpp` and
   `server-common.cpp` at `b12048`. `/tokenize` is not a public endpoint there (`server-http.cpp`), so it takes the
   same `Authorization` header. The live check that the two counts agree is Verification step 3, before merge.
   Cost:
   - one `/tokenize` request per input in every `embed` call, queries included;
   - one more per document chunk when ingest checks whether to split it;
   - about log2(number of cut points) more per over-length input.

   `/tokenize` runs on the HTTP thread and takes no slot, so it does not use the one-input drain budget ME and KB
   share. Nothing measures its latency here: the stub cannot, and the embedder is not reachable from this machine.
   Verification step 3 and the first deployed ingest (infra#81) show it, and no cap or timeout is invented in the
   meantime (latency rules).
   Rejected:
   - a client-side tokenizer: it needs a `tokenizer.json` that matches the GGUF, `tokenizers` in the default
     dependency group, and a second artifact to keep in step with `artifact_sha256`;
   - a UTF-8 byte bound: it is provable only for byte-level BPE, and it would cut English text at about a quarter of
     the limit.
2. **Where each input kind is bounded, and how.**

   | Input | Where | How |
   | --- | --- | --- |
   | Chunks from file ingest (every chunker) and `ingest_text` (stored text, URL pages) | `ingest.py`, before dedup | split into consecutive pieces that together are exactly the chunk, cut after whitespace where possible, no overlap |
   | Folder summaries (update and the re-embed rebuild) | the provider | truncated: one vector per folder; the stored summary text stays whole |
   | Figure, image and element-capture descriptions (`vision.py`, `web.py`) | the provider | truncated: one chunk per figure keeps the figure's metadata |
   | Queries | the provider | truncated |
   | Stored chunks during `re-embed` | the provider | truncated: a stored chunk is one row in the target space; reingesting the document splits a chunk stored before this fix |

   The provider-level bound is the invariant: every caller gets its provider from
   `embed_swap.declared_provider_config`, so nothing reaches `/v1/embeddings` over the limit, whatever the caller does.
   The ingest split only keeps document text from being lost. Each truncation is logged at INFO with both counts.
3. **The cut.** `_cut(text, limit, count) -> int` returns a position `c > 0` such that `count(text[:c]) <= limit`.
   - It first binary-searches the positions just after a whitespace character. The best verified position so far is
     the lower bound, and it moves only to a position whose count was checked. If no whitespace position fits, it
     searches every character position the same way. If not even one character fits, it raises `ValidationError`
     naming the variable; at real limits this cannot happen.
   - Counts need not be monotone in length, so `c` is a verified fit, not necessarily the longest one. A long word
     after a fitting boundary ends the piece at that boundary, and the next piece is cut inside the word.
   - `split_to_limit(text, limit, count) -> list[str]` returns `[text]` when the whole text fits. Otherwise it emits
     `text[:c]` and repeats on `text[c:]`. Every piece is a slice that was counted, each is non-empty, so the loop
     always advances, and `"".join(pieces) == text`. Truncation is the first piece.
4. **Configuration until #573 lands.** `KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS` is #573's own variable name and
   `EmbedSettings.max_input_tokens` its field. Here they stand alone and are optional. A declared limit is refused
   unless the provider is `openai_compat`: only an OpenAI-wire llama-server has `/tokenize`, and `openai`, `ollama` and
   `onnx` cannot count. The limit travels in `ProviderConfig.max_input_tokens`, set by `declared_provider_config`.
   Rebase rule, for whichever PR lands second:
   - `declared_provider_config(conn, space)` (#573's signature) sets `max_input_tokens=space["max_input_tokens"]`.
   - Each call site passes the space whose vectors it writes or reads:
     - ingest's `_bounded_chunks` and `_embed_with_config`, and `update_folder_summary`: the active space
       (`get_active_space`);
     - `backfill_space` and the re-embed folder rebuild: the target space;
     - `search`: the queried space.
   - Item 1 shrinks to the family rule, extended to `llama.cpp`, because #573 already parses the variable as part of
     the served artifact.
   - One test is added: a space whose recorded limit differs from another space's governs backfill into it.
5. **A `/tokenize` the endpoint does not have** (404) raises `ValidationError`: "`KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS`
   needs an embedder that counts tokens at /tokenize (llama-server)". It never falls back to sending unbounded input.
6. **Metadata of split pieces.**
   - PDF: each piece keeps its parent's `pages` (parent-level provenance). Its `images` come from the piece's own
     text, because the split happens before the existing loop that reads image references.
   - Python: each piece keeps its parent's `start_line`, `end_line` and `type`, and is named `name[k]`. That is
     `chunk_python_ast`'s own convention for an oversized chunk.
   - Fixed-size and `ingest_text` chunks carry no per-chunk metadata.

## Implementation sequence

The acceptance tests share one transport stub, `bounded_embedder`, defined in the new `tests/test_embed_limits.py` and
built on `http_requests`. It declares `openai_compat` at `http://embedder.example.test` with
`KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS=LIMIT` (`LIMIT = 64`) and opens a database under that declaration
(`declared_db`). Its toy tokenizer counts one token per whitespace-separated word, plus one EOS. It answers:

- `POST /tokenize`: `{"tokens": [0] * count}`, after asserting `add_special` is true;
- `POST /v1/embeddings`: if any input counts more than `LIMIT`, the whole request fails with 400 and the body
  `{"error": {"code": 400, "type": "exceed_context_size_error", ...}}`, as llama-server does; otherwise one unit vector
  per input at the declared dimension.

It records each `/v1/embeddings` body. Every item's Green ends with the full suite (`uv run pytest tests/ -q -m "not
slow"`, under `flock /run/user/1000/memlane-heavy.lock`), so each commit stands on its own.

**Baseline, before item 1:** the acceptance tests of items 2 and 3 run against master in a throwaway worktree at
`master`, with only `tests/test_embed_limits.py` copied in. Each fails with `httpx.HTTPStatusError` 400, which is the
defect. The PR body records the output.

- [ ] **1. Declare the limit.**
  - Files: `src/knowledge_base/settings.py`, `tests/conftest.py`, `tests/test_settings.py`.
  - Red: `uv run pytest tests/test_settings.py -q -k max_input_tokens` fails. The new tests:
    - unset gives `None`, and `2048` gives `2048`;
    - `0`, `-1` and `2k` raise a message starting with `KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS`;
    - setting it under `ollama`, `openai` or `onnx` raises, naming the variable and `openai_compat`.
  - Change:
    - `VARIABLES["max_input_tokens"] = "KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS"`;
    - `EmbedSettings.max_input_tokens: int | None = None`, parsed with the dim rule (a positive ASCII integer);
    - the family rule from Decision 4;
    - `_declared_environment` deletes the variable, so a developer's shell cannot leak a limit into the suite.
  - Green: the Red command, then the full suite.
  - Commit: `feat(settings): declare the embedder's max input tokens`
- [ ] **2. The provider counts and truncates.**
  - Files: `src/knowledge_base/embeddings.py`, `src/knowledge_base/embed_swap.py`, `tests/test_embeddings.py`,
    `tests/test_embed_limits.py` (new).
  - Red: `uv run pytest tests/test_embeddings.py tests/test_embed_limits.py -q` fails. The new tests:
    - `split_to_limit`, with fake counters, gives pieces that each count at most the limit and satisfy
      `"".join(pieces) == text`. The inputs include newlines, indentation and trailing whitespace. The counters:
      - a word counter;
      - a non-monotone counter, where some longer prefix counts less than a shorter one;
      - a word longer than the limit right after a fitting boundary.
    - A text that already fits comes back as `[text]`.
    - `OpenAICompatProvider.count_tokens` posts `{"content": ..., "add_special": true}` to `<base>/tokenize`, with the
      declared `Authorization` header, and returns `len(tokens)`; a 404 raises Decision 5's error;
    - with a limit, `embed` sends an in-limit input byte for byte and an over-length one as its first piece. Without a
      limit it makes no `/tokenize` request;
    - acceptance: `update_folder_summary` on a folder whose summary counts more than `LIMIT` returns `True` and stores
      a vector, and the body it sent counts at most `LIMIT`.
  - Change:
    - `ProviderConfig.max_input_tokens: int | None = None`, shown in its `repr`;
    - `_cut` and `split_to_limit`, as Decision 3 describes;
    - `OpenAICompatProvider(..., max_input_tokens=None)` with `count_tokens(text) -> int`. Its `embed` replaces each
      input that counts over the limit by its first piece, before batching;
    - `_build_provider` passes the limit;
    - `split_inputs(texts, cfg) -> list[list[str]]` returns `[[t] for t in texts]` when `cfg.max_input_tokens` is
      `None`, else splits each text with the cached provider's counter;
    - `declared_provider_config` sets `max_input_tokens=declared.max_input_tokens`.
  - Green: the Red command, then the full suite.
  - Commit: `fix(embeddings): truncate any input over max_input_tokens before sending it`
- [ ] **3. Ingest splits document chunks.**
  - Files: `src/knowledge_base/ingest.py`, `tests/test_embed_limits.py`.
  - Red: `uv run pytest tests/test_embed_limits.py -q -k section` fails. Item 2 already truncates, so no 400 occurs.
    The failure is that a stored chunk still counts more than `LIMIT`, and the embedded body held only its first piece.
    Each new test asserts that:
    - the ingest succeeds;
    - every stored chunk counts at most `LIMIT`;
    - the stored chunks, joined in `chunk_index` order, equal the section exactly.

    The section is 3 × `LIMIT` distinct words (`w0 w1 …`) under 1,000 characters, so each chunker keeps it as one
    chunk. The tests:
    - `ingest_file` of a `.md` (the fixed-size `chunk_text`, which every non-PDF, non-Python file uses);
    - the semantic strategy on a PDF: `_extract_pdf_markdown` is patched to return the section under one H2 heading,
      with one `![](fig.png)` reference among its words; the chunk strategy is set to `semantic`, and
      `chunk_by_section` keeps the section whole. Only the piece holding the reference lists `fig.png` in its
      `images`, and every piece keeps the parent's `pages`;
    - a `.py` file whose one function is over the limit: its pieces are named `f[0]`, `f[1]`, … with the function's
      line range;
    - `ingest_text` of the section.
  - Change:
    - `_bounded_chunks(conn, texts) -> list[list[str]]` returns
      `split_inputs(texts, declared_provider_config(conn, get_embed_config(conn)["provider"]))`.
    - In `_produce_and_insert_chunks`, each branch expands its chunk list before its items are built, by Decision 6:
      `ast_chunks` (dicts), `md_chunks` (`(text, pages)`) and `fixed_chunks`. Items then number `0..n-1` and hash
      each piece, as today.
    - `ingest_text` expands `chunks` the same way.
  - Green: the Red command, then the full suite.
  - Commit: `fix(ingest): split a chunk over max_input_tokens so no section fails its batch`
- [ ] **4. Documentation.**
  - Files: `README.md`, `docs/usage/ingesting-documents.md`.
  - No executable behaviour. The README's configuration table gains a row:
    `KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS` | optional, `openai_compat` only | the embedder's input limit as llama-server
    counts it, EOS included (`2048` for ADR-0015's served artifact); counted at its `/tokenize`.
    `ingesting-documents.md` gains Decision 2's table, Decision 6's metadata rule and the cost line.
  - Green: `git grep -n KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS -- README.md docs/` lists both.
  - Commit: `docs: bound embedding inputs by KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS`

## Documentation

`README.md` (the configuration row) and `docs/usage/ingesting-documents.md` (the per-kind choice, the metadata of
split pieces and the counting cost), in item 4. No ADR changes: ADR-0015 leaves input truncation to each layer's
ingest.

## Testing

- Unit, in `tests/test_settings.py`: the variable's parse and family rules.
- Unit, in `tests/test_embeddings.py`:
  - `split_to_limit` with word, non-monotone and long-word counters, checking exact reconstruction;
  - the `/tokenize` wire;
  - provider truncation, and no `/tokenize` request without a limit.
- Acceptance, in `tests/test_embed_limits.py`, through the transport stub that enforces the limit the way llama-server
  does:
  - over-length markdown, semantic-PDF (with an image reference) and Python sections;
  - an over-length `ingest_text`;
  - an over-length folder summary.

  Each first fails with the 400 at the master baseline, then for the reason its item names.

## Verification

1. The merge gate at the final clean head, in the order `AGENTS.md` gives:
   - `uv lock --check`;
   - `uv sync`;
   - `uv run ruff check src/ tests/`;
   - `uv run ruff format --check src/ tests/`;
   - `flock /run/user/1000/memlane-heavy.lock uv run pytest tests/ -q -m "not slow"`;
   - `uv sync --all-groups`, then `uv run basedpyright src/ tests/`.
2. `/super-review` at the confirmed Full gate, then `/finish-pr`.
3. Before merge, run by the owner, because the embedder is not reachable from the worker: check the deployed
   embedder against Decision 1. The PR body carries the exact commands, and the result is recorded there.
   - A text whose `/tokenize` (`add_special: true`) count is exactly 2,048 embeds, and its `usage.prompt_tokens` is
     2,048.
   - One more word, counted at 2,049 or more, is refused with 400 `exceed_context_size_error`.
   - The time of each `/tokenize` call is recorded.

   Until that check runs, the PR states that "as the server counts" was verified in the `b12048` source only.

## Resolution

One independent review: Codex, `2026-11-17-604-bound-embed-inputs-lean-review.md`. Planning-review severity: Default,
so every finding is resolved. No Blocker; four High, three Medium, no Low.

1. **High: binary search cannot guarantee the longest fit with non-monotone counts.** Revised Decision 3: the
   guarantee is now a verified, non-empty fit, not the longest one. The lower bound moves only to a counted position.
   A long word after a fitting boundary ends the piece at that boundary. Item 2 adds a non-monotone counter and a
   long-word-after-boundary case.
2. **High: `rstrip` plus `lstrip` discards whitespace.** Revised Decision 3: pieces are exact consecutive slices, each
   counted as emitted, and `"".join(pieces) == text`. Item 2 asserts exact reconstruction on inputs with newlines,
   indentation and trailing whitespace. Item 3 asserts that the stored chunks equal the section exactly.
3. **High: item 3's red cannot be the 400 once item 2 truncates.** Item 3's Red now fails because a stored chunk
   still counts over `LIMIT` and only its first piece was embedded. The 400 is shown once, as a baseline against
   master in a throwaway worktree, before item 1, and recorded in the PR body.
4. **High: tokenizer equivalence unverified at merge; an over-length input cannot report `usage.prompt_tokens`.**
   Decision 1 is now checked in the deployed tag's own source, llama.cpp `b12048` (`server-context.cpp`,
   `server-common.cpp`, `llama-vocab.cpp`, `server-http.cpp`); `/tokenize` needs the key there. Verification step 3
   is now a pre-merge owner check: a text counted at exactly 2,048 embeds with `usage.prompt_tokens` 2,048, and
   2,049 is refused with the 400. Until it runs, the PR states that the claim was verified in source only.
5. **Medium: copied metadata misdescribes pieces.** New Decision 6: PDF pieces keep the parent's `pages` (stated as
   parent-level provenance) and get `images` from their own text, because the split moves before the loop that reads
   image references. Python pieces follow `chunk_python_ast`'s `name[k]` convention with the parent's line range.
   Item 3 tests both.
6. **Medium: the #573 rebase is more than one line.** Revised Decision 4: each call site passes its space to
   `declared_provider_config(conn, space)`. Ingest and folder summaries pass the active space, backfill and the
   folder rebuild the target space, and search the queried space. One test is added: a space's own recorded limit
   governs backfill into it.
7. **Medium: the merge gate omitted `uv sync`.** Verification step 1 now lists `AGENTS.md`'s full sequence, `uv sync`
   included.
