# knowledge-base — Agent Instructions

> For OpenAI Codex, Devin, and other autonomous coding agents.

## What This Is

Hybrid semantic search MCP server (Python 3.12+, FastMCP). Single SQLite database combining FTS5 keyword search + sqlite-vec cosine similarity via Reciprocal Rank Fusion. Serves 47 tools over stdio, 3 over HTTP (search and ingest; see `docs/reference/mcp-tools.md`) to AI assistants for ingesting, searching, and analyzing research papers, code, and notes.

## Setup

```bash
uv sync                          # install all deps (never use pip)
```

## Verify

```bash
sudo apt-get update && sudo apt-get install -y tesseract-ocr  # once per host: without it the OCR tests skip instead of running
uv lock --check                        # uv.lock matches pyproject.toml; first, because uv sync rewrites a drifted lock
uv sync
uv run ruff check src/ tests/          # zero warnings
uv run ruff format --check src/ tests/ # no reformats
uv run pytest tests/ -q -m "not slow"
uv sync --all-groups                   # the type-check resolves the optional groups' imports
uv run basedpyright src/ tests/
uv run --group docs sphinx-build -W -E -q -b html docs docs/_build  # zero warnings: an orphan page or a broken link fails it
# Heavy (up to 32 GiB, 16 CPUs), only when an image input changed: builds HEAD's archive, so commit first
git diff --quiet origin/master...HEAD -- Containerfile .containerignore pyproject.toml uv.lock .python-version || (
    git archive --format=tar HEAD | podman build --pull=newer --layers=false --memory=32g \
        --cpu-period=100000 --cpu-quota=1600000 -t knowledge-base:verify - || exit
    podman run --rm --network=none knowledge-base:verify python -c "import knowledge_base.server"; rc=$?
    podman rmi knowledge-base:verify && exit $rc
)
```

This list is the merge gate: run every command, in this order, at the PR's final head with a
clean tree (an earlier `uv sync` may already have rewritten a drifted `uv.lock`), and quote the
results in the PR. `.github/workflows/ci.yml` no longer runs, so CI is not relied on and a CI
status is never evidence.

The image step (#617) builds `HEAD`'s `git archive`, as the deployment builds a tag. A lock that pulls in a package
without a Linux wheel, or a Containerfile edit, passes every other step and fails only there. It is heavy: a CPU quota
bounds it to 16 CPUs (rootless podman is delegated only the cpu, memory and pids controllers, so `--cpuset-cpus` fails)
and `--memory` to 32 GiB. It keeps no intermediate layers, and it removes its image whether or not the smoke passes. Its
paths are the build inputs no other step exercises: add one when the Containerfile copies a new file.

## Project Layout

```
src/knowledge_base/
  server.py        # entry point: stdio, or stateless HTTP behind the gate with --http
  auth.py          # machine tokens: the token-hash file, the Bearer gate, the per-call log
  store_lock.py    # the store lock: one writer process per database (#596)
  db.py            # SQLite schema, migrations, connection helpers
  ingest.py        # PDF/code/web/markdown ingestion pipeline
  redact.py        # Secret redaction (memory-engine's rules); redact_store.py: the redact command
  search.py        # Hybrid search (FTS5 + sqlite-vec + RRF)
  extraction.py    # LLM-powered structured extraction (map-reduce)
  vision.py        # Vision-based figure extraction from PDFs
  papers.py        # Paper CRUD, relationships, BibTeX export
  conclusions.py   # Evidence-chained conclusions with supersession
  embed_swap.py    # Atomic embedding model swap
  embeddings.py    # Embedding providers (openai_compat, ollama, onnx)
  settings.py      # The KNOWLEDGE_BASE_* declaration and egress switches
  jobs.py          # Background job queue (singleton daemon thread)
  browser/         # Playwright page renderer (subprocess)
tests/             # pytest suite, 1.35x source ratio
docs/              # Sphinx-compatible documentation
```

## Rules

1. **Never commit secrets** — no API keys, tokens, or .env files
2. **TDD** — write the failing test first, then implement
3. **Conventional Commits** — `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`
4. **Atomic commits** — one logical change per commit
5. **Thread safety** — all SQLite access via thread-local connections (`threading.local()`). Never share a connection across threads.
6. **Mock external services** — tests must not call Ollama or any LLM. Fake the embedder with the `stub_document_embedder` fixture (or `fake_embedding_provider()` in a helper): it is the provider every embedding path resolves in that test, records every text, answers through its `answer` and fails as an endpoint does while `down` is set; never patch a module's `embed` name. Input-limit tests use `bounded_embedder` (`tests/test_embed_limits.py`), a real provider over the `http_requests` fixture, which records every httpx request at the transport. Fake outbound HTTP with `http_requests`, names with `fake_dns` and public hosts with `public_net`; never patch `httpx` inside a module, since a floored call builds its own pinned client (`utils.egress_client`) and the patch would miss it.
7. **Content-hash dedup** — ingestion uses SHA-256 hashes. If adding new content types, maintain this pattern.
8. **Batched SQL** — use `_batched_execute`/`_batched_select` from `db.py` for any IN-clause query to stay under SQLite's 999 variable limit.
9. **Redaction** — text stored as chunk content passes `redact.scrub()` before it is hashed or embedded; a new insert site needs a leak case in `tests/test_redact_paths.py` (its tripwire fails until it has one). `tests/fixtures/redaction-cases.jsonl` is shared byte for byte with memory-engine: change it only through a linked pair of PRs, memory-engine first.

## Issue & PR Labels

Title format `type(area): description`. Every issue carries **exactly one
`type:` and one `area:`** label:

- **`type:`** bug · feature · enhancement · perf · refactor · test · docs · chore
  · research · eval · security · epic · plan
- **`area:`** ingest · search · embeddings · extraction · vision · papers · db ·
  mcp · infra · integration · docs

Additive: `priority:{critical,high,medium,low}`, `severity:{…}` (super-qa only),
`status:{blocked,needs-design}`, `super-qa`. Deferral is the Project **Phase**
field value `Deferred`, not a label. New issues/PRs auto-add to the GitHub
Projects via `.github/workflows/add-to-project.yml`. Full scheme:
`docs/design/project-management.md`; scripts in `utils/scripts/` (`utils/scripts/README.md`).

## Architecture Docs

For deeper context, read these files (only when working on the relevant area):

- `docs/design/architecture-overview.md` — module responsibilities, data flow diagrams
- `docs/reference/schema.md` — ER diagram, all tables and columns
- `docs/reference/mcp-tools.md` — full tool reference
- `docs/design/project-management.md` — label taxonomy, Projects, migration
- `ROADMAP.md` — issue dependency graph, current phase
