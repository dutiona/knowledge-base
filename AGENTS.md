# knowledge-base — Agent Instructions

> For OpenAI Codex, Devin, and other autonomous coding agents.

## What This Is

Hybrid semantic search MCP server (Python 3.12+, FastMCP). Single SQLite database combining FTS5 keyword search + sqlite-vec cosine similarity via Reciprocal Rank Fusion. Exposes 33+ tools to AI assistants for ingesting, searching, and analyzing research papers, code, and notes.

## Setup

```bash
uv sync                          # install all deps (never use pip)
```

## Verify

```bash
uv run pytest tests/ -q          # must pass before any PR
ruff check src/ tests/           # must pass — zero warnings
ruff format --check src/ tests/  # must pass — no reformats
```

Run all three commands after every change. Do not skip any.

## Project Layout

```
src/knowledge_base/
  server.py        # FastMCP entry point, registers all MCP tools
  db.py            # SQLite schema, migrations, connection helpers
  ingest.py        # PDF/code/web/markdown ingestion pipeline
  search.py        # Hybrid search (FTS5 + sqlite-vec + RRF)
  extraction.py    # LLM-powered structured extraction (map-reduce)
  vision.py        # Vision-based figure extraction from PDFs
  papers.py        # Paper CRUD, relationships, BibTeX export
  conclusions.py   # Evidence-chained conclusions with supersession
  embed_swap.py    # Atomic embedding model swap
  embeddings.py    # Ollama embedding client
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
6. **Mock external services** — tests must not call Ollama or any LLM. Use `unittest.mock.patch` on `knowledge_base.embeddings.embed` and `httpx.AsyncClient.post`.
7. **Content-hash dedup** — ingestion uses SHA-256 hashes. If adding new content types, maintain this pattern.
8. **Batched SQL** — use `_batched_execute`/`_batched_select` from `db.py` for any IN-clause query to stay under SQLite's 999 variable limit.

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
`docs/design/project-management.md`; scripts in `scripts/` (`scripts/README.md`).

## Architecture Docs

For deeper context, read these files (only when working on the relevant area):

- `docs/design/architecture-overview.md` — module responsibilities, data flow diagrams
- `docs/reference/schema.md` — ER diagram, all tables and columns
- `docs/reference/mcp-tools.md` — full tool reference
- `docs/design/project-management.md` — label taxonomy, Projects, migration
- `ROADMAP.md` — issue dependency graph, current phase
