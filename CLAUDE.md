# knowledge-base

Hybrid semantic search MCP server for research papers, code, and notes.
Single SQLite database with FTS5 (BM25) + sqlite-vec (cosine) merged via Reciprocal Rank Fusion.

## Stack

- Python 3.12+, hatchling build, uv (never pip)
- FastMCP server (`src/knowledge_base/server.py` entry point)
- SQLite with FTS5, sqlite-vec, WAL mode — DB at `~/.local/share/knowledge-base/knowledge.db`
- Ollama for embeddings (bge-m3) and LLM extraction (qwen3.5:27b)
- pymupdf4llm for PDF ingestion, trafilatura for web content

## Commands

```bash
uv sync                          # install deps
uv run knowledge-base            # start MCP server
uv run pytest tests/ -q          # run all tests
uv run pytest tests/test_X.py -v # run one test file
ruff check src/ tests/           # lint (strict select: E,F,W,B,SIM,UP,C4,PTH,RUF,S)
ruff format src/ tests/          # format (120 cols)
uv sync --all-groups             # install optional deps (browser, reranker, …) for type-check
uv run basedpyright src/ tests/  # type-check (basedpyright; needs --all-groups synced)
```

CI gates on all four (`ruff check`, `ruff format --check`, `pytest -m "not slow"`,
`basedpyright`).

## Architecture

`server.py` is a thin hub (~40 LOC) that mounts 6 route sub-modules via `FastMCP.mount()`. Each route registers its own tools; domain logic lives in dedicated modules. All share a single SQLite file via thread-local connections (`_conn.py`) with double-checked locking for schema init.

Read these when the task touches the corresponding area:

**Route modules** (MCP tool definitions):

| Route      | Tools                                                                    | Module                                    |
| ---------- | ------------------------------------------------------------------------ | ----------------------------------------- |
| Ingestion  | ingest, reingest, ingest_url, configure_chunking, configure_browser_tool | `src/knowledge_base/routes/ingestion.py`  |
| Search     | search_index, co_occurrence, status                                      | `src/knowledge_base/routes/search.py`     |
| Embeddings | embed_config, re_embed_tool, space lifecycle, compare                    | `src/knowledge_base/routes/embeddings.py` |
| Papers     | paper CRUD, relationships, conclusions, bibtex                           | `src/knowledge_base/routes/papers.py`     |
| Extraction | LLM extraction, vision, entity recording                                 | `src/knowledge_base/routes/extraction.py` |
| Operations | jobs, prediction errors                                                  | `src/knowledge_base/routes/operations.py` |

**Domain modules** (business logic):

| Area                                     | Module                                    | Deep-dive doc                             |
| ---------------------------------------- | ----------------------------------------- | ----------------------------------------- |
| Thread-local DB connections              | `src/knowledge_base/_conn.py`             | `docs/design/architecture-overview.md`    |
| Chunking (text, markdown, Python AST)    | `src/knowledge_base/chunking.py`          | `docs/usage/ingesting-documents.md`       |
| Ingestion (PDF, code, markdown)          | `src/knowledge_base/ingest.py`            | `docs/usage/ingesting-documents.md`       |
| Web ingestion (URL, SSRF, browser)       | `src/knowledge_base/web.py`               | `docs/usage/ingesting-documents.md`       |
| Hybrid search (FTS5 + vec + RRF)         | `src/knowledge_base/search.py`            | `docs/usage/searching.md`                 |
| Keyword intent extraction                | `src/knowledge_base/keywords.py`          | `docs/usage/searching.md`                 |
| LLM config, calling & connectivity       | `src/knowledge_base/llm.py`               | `docs/usage/structured-extraction.md`     |
| LLM extraction (map-reduce)              | `src/knowledge_base/extraction.py`        | `docs/usage/structured-extraction.md`     |
| Vision/figure extraction                 | `src/knowledge_base/vision.py`            | `docs/usage/figure-extraction.md`         |
| Paper metadata & relationships           | `src/knowledge_base/papers.py`            | `docs/usage/relationships-conclusions.md` |
| BibTeX export & sync                     | `src/knowledge_base/bibtex.py`            | `docs/usage/bibtex-export.md`             |
| Background jobs                          | `src/knowledge_base/jobs.py`              | `docs/design/architecture-overview.md`    |
| Folder context boosting                  | `src/knowledge_base/folder_summaries.py`  | `docs/usage/searching.md`                 |
| Embedding providers (Ollama/OpenAI/ONNX) | `src/knowledge_base/embeddings.py`        | `docs/usage/ingesting-documents.md`       |
| Embedding model swap                     | `src/knowledge_base/embed_swap.py`        | `docs/design/architecture-overview.md`    |
| Prediction-error detection               | `src/knowledge_base/prediction_errors.py` | `docs/usage/prediction-errors.md`         |
| Auto-relationship discovery              | `src/knowledge_base/auto_relate.py`       | `docs/usage/auto-relationships.md`        |
| Cross-encoder reranking                  | `src/knowledge_base/reranker.py`          | `docs/usage/searching.md`                 |
| DB schema & migrations                   | `src/knowledge_base/db.py`                | `docs/reference/schema.md`                |

## Testing Conventions

- Every module has a corresponding `tests/test_<module>.py`
- Tests use temporary SQLite databases via `tmp_path` fixtures — never touch the real DB
- Mock Ollama embeddings and LLM calls with `unittest.mock.patch`
- `@pytest.mark.slow` for tests requiring real LLM/network access
- Test/source ratio is 1.35x — maintain or improve it

## Key Patterns

- **Content-hash deduplication**: SHA-256 on chunk content prevents duplicate ingestion
- **Thread-local connections**: each thread gets its own `sqlite3.Connection`, never shared
- **Batched SQL**: IN-clause queries batched to stay under SQLite's 999 variable limit (`db.py:_batched_execute`)
- **Map-reduce extraction**: short docs (<8000 chars) get single LLM call; longer docs split into per-chunk extraction then entity resolution

## Roadmap

`ROADMAP.md` is the dependency-graph narrative (phases, ordering, parallelism).
**GitHub Projects is the live tracker** — see Project Management below. Currently
in Phase 3 (intelligence, integration & search refinement).

## Project Management

Issues and PRs are tracked with a `prefix:value` label taxonomy fed into four
GitHub Projects. Design: `docs/design/project-management.md`. The label/project/
migration scripts live in `scripts/` (`sync-labels.sh`, `setup-projects.sh`,
`gen-mapping.sh`, `migrate-issues.sh` — all idempotent, all `--dry-run`-aware;
see `scripts/README.md`).

**Title convention:** `type(area): description` (Conventional-Commits style),
e.g. `feat(search): query-type classifier`.

**Required labels — every issue carries exactly one `type:` and one `area:`:**

- **`type:`** — bug · feature · enhancement · perf · refactor · test · docs ·
  chore · research · eval · security · epic · plan
- **`area:`** — ingest · search · embeddings · extraction · vision · papers ·
  db · mcp · infra · integration · docs (the subsystem the code lands in)

**Additive (optional):**

- **`priority:`** critical · high · medium · low — scheduling urgency. Mirrors
  the Project **Priority** field 1:1. (KB-P0→critical, P1→high, P2→medium,
  P3/P4→low.)
- **`severity:`** critical · high · medium · low · info — `/super-qa` findings
  only; intrinsic technical impact (distinct from priority).
- **`status:`** blocked · needs-design — only when non-default.
- **`super-qa`** — provenance marker for audit findings.

> Deferral is **not** a priority — it is the **`Deferred`** value of the Project
> **Phase** field (the parking lot). Phase is a field, never a label.

**Projects** (auto-populated by `.github/workflows/add-to-project.yml`):

| Project                            | Contents                                          |
| ---------------------------------- | ------------------------------------------------- |
| KB — Main (#7)                     | every open issue/PR; fields Status/Priority/Phase |
| KB — Critical Path to Phase 4 (#8) | manually curated gate + Phase-4 targets           |
| KB — Bug & Security Triage (#9)    | auto: `type:bug` \|\| `type:security`             |
| KB — Research & Eval (#10)         | auto: `type:research` \|\| `type:eval`            |

**Epics:** reserve `type:epic` + native sub-issues for large decomposable
features (e.g. #107 code indexing, #124 build/serve). Roadmap _phase_ grouping
is the Project **Phase** field, not epics. Link a child to its epic via GraphQL:

```bash
PARENT=$(gh api graphql -f query='{ repository(owner:"dutiona",name:"knowledge-base"){ issue(number:EPIC){ id }}}' --jq '.data.repository.issue.id')
CHILD=$(gh api graphql -f query='{ repository(owner:"dutiona",name:"knowledge-base"){ issue(number:NEW){ id }}}' --jq '.data.repository.issue.id')
gh api graphql -f query="mutation { addSubIssue(input:{issueId:\"$PARENT\", subIssueId:\"$CHILD\"}){ subIssue { number }}}"
```

> **Division of labour:** the `scripts/` automate labels, project containers,
> fields, and migration. Project **views/boards** and the built-in Status field's
> **In Review** option are web-UI-only (`gh` has no `view-create`/`field-edit`) —
> see `scripts/README.md`. The auto-add workflow needs a `KB_PROJECT_TOKEN`
> secret (classic PAT, `repo`+`project` scope).
