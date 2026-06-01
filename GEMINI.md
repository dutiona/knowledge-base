# knowledge-base — Gemini Instructions

> For Google Gemini CLI.

## What This Is

Hybrid semantic search MCP server. Python 3.12+, FastMCP, single SQLite database with FTS5 (BM25) + sqlite-vec (cosine similarity) fused via Reciprocal Rank Fusion. Serves 33+ tools to AI assistants for managing research papers, code, and notes.

## Commands

```bash
uv sync                          # install deps (never pip)
uv run knowledge-base            # start MCP server
uv run pytest tests/ -q          # run tests
ruff check src/ tests/           # lint
ruff format src/ tests/          # format
```

All three checks (pytest, ruff check, ruff format --check) must pass before any commit.

## Project Structure

Source lives in `src/knowledge_base/`. Each module owns its domain:

- `server.py` — FastMCP entry point, tool registration
- `db.py` — SQLite schema, migrations, batched SQL helpers
- `ingest.py` — ingestion pipeline (PDF via pymupdf4llm, Python via AST, web via trafilatura, fixed-size chunking for the rest)
- `search.py` — hybrid search: FTS5 BM25 + sqlite-vec cosine, merged via RRF (k=60)
- `extraction.py` — LLM extraction using map-reduce for long documents
- `vision.py` — figure extraction from PDF pages using vision models
- `papers.py` — paper metadata, typed relationships (7 types), BibTeX export
- `conclusions.py` — evidence-chained claims with supersession tracking
- `embed_swap.py` — atomic embedding model replacement (staging table pattern)
- `jobs.py` — background job queue, singleton daemon thread
- `embeddings.py` — Ollama embedding client (auto-detects host)

Tests in `tests/`, one file per module. Test/source ratio: 1.35x.

## Conventions

- **Package manager**: `uv` only, never `pip`
- **Testing**: pytest with `tmp_path` fixtures for DB isolation. Mock Ollama/LLM calls — tests must not hit external services. `@pytest.mark.slow` for integration tests.
- **Commits**: Conventional Commits (`feat:`, `fix:`, `refactor:`, etc.), imperative mood, atomic changes
- **Thread safety**: thread-local `sqlite3.Connection` objects. Never share connections across threads.
- **Deduplication**: SHA-256 content hash on chunk content. Maintain this for any new content types.
- **SQL safety**: use `_batched_execute`/`_batched_select` from `db.py` for IN-clause queries (SQLite 999 variable limit)

## Issue & PR Labels

Title format `type(area): description`. Every issue carries **exactly one
`type:`** (bug · feature · enhancement · perf · refactor · test · docs · chore ·
research · eval · security · epic · plan) and **one `area:`** (ingest · search ·
embeddings · extraction · vision · papers · db · mcp · infra · integration ·
docs). Additive: `priority:{critical,high,medium,low}`, `severity:*` (super-qa
only), `status:{blocked,needs-design}`, `super-qa`. Deferral = Project **Phase**
field value `Deferred`, not a label. New issues/PRs auto-add to GitHub Projects.
Full scheme: `docs/design/project-management.md`; scripts in `scripts/`.

## Deep-Dive Docs

Read only when working on the relevant area:

| Topic                        | File                                      |
| ---------------------------- | ----------------------------------------- |
| Architecture & data flows    | `docs/design/architecture-overview.md`    |
| Database schema (ER diagram) | `docs/reference/schema.md`                |
| MCP tool reference           | `docs/reference/mcp-tools.md`             |
| Ingestion strategies         | `docs/usage/ingesting-documents.md`       |
| Search modes & tuning        | `docs/usage/searching.md`                 |
| Structured extraction        | `docs/usage/structured-extraction.md`     |
| Figure extraction            | `docs/usage/figure-extraction.md`         |
| Paper relationships          | `docs/usage/relationships-conclusions.md` |
| Project management           | `docs/design/project-management.md`       |
| Roadmap & priorities         | `ROADMAP.md`                              |
