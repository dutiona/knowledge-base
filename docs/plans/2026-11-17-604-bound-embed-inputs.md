# fix(embeddings): bound every input by the served max_input_tokens so one long section can't fail a batch (#604)

## Outcome

When `KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS` is declared, no embedding input KB sends counts more tokens than that,
as the server counts them (end-of-sequence token included). Each count is the embedder's own: llama-server's
`POST /tokenize` with `add_special: true`. Document chunks over the limit are split into consecutive pieces that each
fit, so nothing is dropped. Every other input (folder summaries, figure and image descriptions, queries, stored chunks
during a re-embed) is cut down to its longest prefix that fits, in the provider, so no caller can send an over-length
input. An over-length section or folder summary no longer fails its whole batch with HTTP 400
`exceed_context_size_error`. Without the variable nothing changes, and no `/tokenize` request is made.

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

1. **Counting is a round-trip to the server's `/tokenize`.** llama-server's embeddings handler tokenizes each string
   input with `tokenize_mixed(vocab, prompt, add_special=true, parse_special=true)`, and `/tokenize` with
   `{"content": text, "add_special": true}` calls the same function (`parse_special` defaults to true), so
   `len(tokens)` is the count the embeddings call checks: BOS when the vocabulary adds one, and EOS. This was checked
   in the llama.cpp server source (`tools/server/server-context.cpp`, `server-common.cpp`), not against the deployed
   b12048. Verification step 3 checks it live. Cost:
   - one `/tokenize` request per input in every `embed` call, queries included;
   - one more per document chunk when ingest checks whether to split it;
   - about log2(number of cut points) more per over-length input.

   `/tokenize` runs on the HTTP thread and takes no slot, so it does not use the one-input drain budget ME and KB
   share. Nothing measures its latency here: the stub cannot, and the embedder is not reachable from this machine. The
   first deployed ingest (infra#81) shows it, and no cap or timeout is invented in the meantime (latency rules).
   Rejected:
   - a client-side tokenizer: it needs a `tokenizer.json` that matches the GGUF, `tokenizers` in the default
     dependency group, and a second artifact to keep in step with `artifact_sha256`;
   - a UTF-8 byte bound: it is provable only for byte-level BPE, and it would cut English text at about a quarter of
     the limit.
2. **Where each input kind is bounded, and how.**

   | Input | Where | How |
   | --- | --- | --- |
   | Chunks from file ingest (every chunker) and `ingest_text` (stored text, URL pages) | `ingest.py`, before dedup | split into consecutive pieces at whitespace, no overlap, nothing dropped |
   | Folder summaries (update and the re-embed rebuild) | the provider | truncated: one vector per folder; the stored summary text stays whole |
   | Figure, image and element-capture descriptions (`vision.py`, `web.py`) | the provider | truncated: one chunk per figure keeps the figure's metadata |
   | Queries | the provider | truncated |
   | Stored chunks during `re-embed` | the provider | truncated: a stored chunk is one row in the target space; reingesting the document splits a chunk stored before this fix |

   The provider-level bound is the invariant: every caller gets its provider from
   `embed_swap.declared_provider_config`, so nothing reaches `/v1/embeddings` over the limit, whatever the caller does.
   The ingest split only keeps document text from being lost. Each truncation is logged at INFO with both counts.
3. **The cut.** `_fit(text, limit, count)` returns the largest cut position `c` for which `count(text[:c].rstrip())
   <= limit`. It binary-searches the positions just after a whitespace character, then `len(text)`. If no such
   position fits (one word longer than the limit, such as a base64 blob), it searches every character position. The
   piece emitted is exactly the string that was counted, so every piece is verified by the server, even where counts
   are not monotone in length. If not even one character fits, it raises `ValidationError` naming the variable; at real
   limits this cannot happen.
4. **Configuration until #573 lands.** `KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS` is #573's own variable name and
   `EmbedSettings.max_input_tokens` its field. Here they stand alone and are optional. A declared limit is refused
   unless the provider is `openai_compat`: only an OpenAI-wire llama-server has `/tokenize`, and `openai`, `ollama` and
   `onnx` cannot count. The limit travels in `ProviderConfig.max_input_tokens`, set by `declared_provider_config`.
   Rebase rule:
   - #573 first: `declared_provider_config(conn, space)` takes `space["max_input_tokens"]` instead of the declaration
     (one line). Item 1 shrinks to the family rule, extended to `llama.cpp`, because #573 already parses the variable
     as part of the served artifact.
   - #604 first: #573 applies the same change when it rebases.
5. **A `/tokenize` the endpoint does not have** (404) raises `ValidationError`: "`KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS`
   needs an embedder that counts tokens at /tokenize (llama-server)". It never falls back to sending unbounded input.

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
  - Red: `uv run pytest tests/test_embeddings.py tests/test_embed_limits.py -q` fails:
    - with a fake counter, `_fit` and `split_to_limit` give pieces that each count at most the limit and keep every
      word in order (`" ".join(pieces).split() == text.split()`); one word longer than the limit is cut at characters;
      a text that already fits comes back unchanged;
    - `OpenAICompatProvider.count_tokens` posts `{"content": ..., "add_special": true}` to `<base>/tokenize`, with the
      declared `Authorization` header, and returns `len(tokens)`; a 404 raises Decision 5's error;
    - with a limit, `embed` sends an in-limit input byte for byte and an over-length one as its verified prefix. Without
      a limit it makes no `/tokenize` request;
    - acceptance: `update_folder_summary` on a folder whose summary counts more than `LIMIT` returns `True` and stores
      a vector, and the body it sent counts at most `LIMIT`. On master this raises `httpx.HTTPStatusError` (400).
  - Change:
    - `ProviderConfig.max_input_tokens: int | None = None`, shown in its `repr`;
    - `_fit` and `split_to_limit(text, limit, count) -> list[str]`, as Decision 3 describes;
    - `OpenAICompatProvider(..., max_input_tokens=None)` with `count_tokens(text) -> int`. Its `embed` replaces each
      input by its verified prefix when it counts over the limit, before batching;
    - `_build_provider` passes the limit;
    - `split_inputs(texts, cfg) -> list[list[str]]` returns one piece per text when `cfg.max_input_tokens` is `None`,
      else splits each text with the cached provider's counter;
    - `declared_provider_config` sets `max_input_tokens=declared.max_input_tokens`.
  - Green: the Red command, then the full suite.
  - Commit: `fix(embeddings): truncate any input over max_input_tokens before sending it`
- [ ] **3. Ingest splits document chunks.**
  - Files: `src/knowledge_base/ingest.py`, `tests/test_embed_limits.py`.
  - Red: `uv run pytest tests/test_embed_limits.py -q -k section` fails with a 400. Each new test checks that the
    ingest succeeds, that no `/v1/embeddings` request was refused, that every stored chunk counts at most `LIMIT`, and
    that the stored chunks, in `chunk_index` order, hold every word of the section in order. The section is 3 ×
    `LIMIT` distinct words (`w0 w1 …`) under 1,000 characters, so each chunker keeps it as one chunk:
    - `ingest_file` of a `.md` (the fixed-size `chunk_text`, which every non-PDF, non-Python file uses);
    - the semantic strategy on a PDF: `_extract_pdf_markdown` is patched to return the section under one H2 heading,
      the chunk strategy is set to `semantic`, and `chunk_by_section` keeps the section whole;
    - `ingest_text` of the same text.
  - Change: `_bounded_chunks(conn, texts) -> list[list[str]]` returns
    `split_inputs(texts, declared_provider_config(conn, get_embed_config(conn)["provider"]))`.
    - `_produce_and_insert_chunks` expands its `items` once, after the three branches and before dedup: each piece
      keeps its parent's metadata, then items are renumbered `0..n-1` and rehashed.
    - `ingest_text` expands `chunks` the same way.
  - Green: the Red command, then the full suite.
  - Commit: `fix(ingest): split a chunk over max_input_tokens so no section fails its batch`
- [ ] **4. Documentation.**
  - Files: `README.md`, `docs/usage/ingesting-documents.md`.
  - No executable behaviour. The README's configuration table gains a row:
    `KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS` | optional, `openai_compat` only | the embedder's input limit as llama-server
    counts it, EOS included (`2048` for ADR-0015's served artifact); counted at its `/tokenize`.
    `ingesting-documents.md` gains Decision 2's table and its cost line.
  - Green: `git grep -n KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS -- README.md docs/` lists both.
  - Commit: `docs: bound embedding inputs by KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS`

## Documentation

`README.md` (the configuration row) and `docs/usage/ingesting-documents.md` (the per-kind choice and the counting
cost), in item 4. No ADR changes: ADR-0015 leaves input truncation to each layer's ingest.

## Testing

- Unit, in `tests/test_settings.py`: the variable's parse and family rules.
- Unit, in `tests/test_embeddings.py`:
  - `_fit` and `split_to_limit` with a fake counter;
  - the `/tokenize` wire;
  - provider truncation, and no `/tokenize` request without a limit.
- Acceptance, in `tests/test_embed_limits.py`, through the transport stub that enforces the limit the way llama-server
  does: over-length markdown and semantic-PDF sections, an over-length `ingest_text`, and an over-length folder
  summary. Each is shown red on master (the 400) before its item lands.

## Verification

1. The merge gate at the final clean head, in order:
   - `uv lock --check`;
   - `uv run ruff check src/ tests/`;
   - `uv run ruff format --check src/ tests/`;
   - `flock /run/user/1000/memlane-heavy.lock uv run pytest tests/ -q -m "not slow"`;
   - `uv sync --all-groups`, then `uv run basedpyright src/ tests/`.
2. `/super-review` at the confirmed Full gate, then `/finish-pr`.
3. After deploy (infra#81, not this PR): check one over-length document against the live embedder. Its `/tokenize`
   count must equal the `usage.prompt_tokens` its embedding reports. That measures the round-trip cost too. The PR
   body records this as an open check.
