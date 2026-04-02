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
ruff check src/ tests/           # lint
ruff format src/ tests/          # format
```

## Architecture

Eighteen modules behind `server.py`, each owning its domain. All share a single SQLite file via thread-local connections with double-checked locking for schema init.

Read these when the task touches the corresponding area:

| Area | Module | Deep-dive doc |
| ---- | ------ | ------------- |

| Chunking (text, markdown, Python AST) | `src/knowledge_base/chunking.py` | `docs/usage/ingesting-documents.md` |
| Ingestion (PDF, code, markdown) | `src/knowledge_base/ingest.py` | `docs/usage/ingesting-documents.md` |
| Web ingestion (URL, SSRF, browser) | `src/knowledge_base/web.py` | `docs/usage/ingesting-documents.md` |

> > > > > > > | Hybrid search (FTS5 + vec + RRF) | `src/knowledge_base/search.py` | `docs/usage/searching.md` |
> > > > > > > | Keyword intent extraction | `src/knowledge_base/keywords.py` | `docs/usage/searching.md` |
> > > > > > > | LLM config, calling & connectivity | `src/knowledge_base/llm.py` | `docs/usage/structured-extraction.md` |
> > > > > > > | LLM extraction (map-reduce) | `src/knowledge_base/extraction.py` | `docs/usage/structured-extraction.md` |
> > > > > > > | Vision/figure extraction | `src/knowledge_base/vision.py` | `docs/usage/figure-extraction.md` |
> > > > > > > | Paper metadata & relationships | `src/knowledge_base/papers.py` | `docs/usage/relationships-conclusions.md` |
> > > > > > > | BibTeX export & sync | `src/knowledge_base/bibtex.py` | `docs/usage/bibtex-export.md` |
> > > > > > > | Background jobs | `src/knowledge_base/jobs.py` | `docs/design/architecture-overview.md` |
> > > > > > > | Folder context boosting | `src/knowledge_base/folder_summaries.py` | `docs/usage/searching.md` |
> > > > > > > | Embedding providers (Ollama/OpenAI/ONNX) | `src/knowledge_base/embeddings.py` | `docs/usage/ingesting-documents.md` |
> > > > > > > | Embedding model swap | `src/knowledge_base/embed_swap.py` | `docs/design/architecture-overview.md` |
> > > > > > > | Prediction-error detection | `src/knowledge_base/prediction_errors.py` | `docs/usage/prediction-errors.md` |
> > > > > > > | Auto-relationship discovery | `src/knowledge_base/auto_relate.py` | `docs/usage/auto-relationships.md` |
> > > > > > > | Cross-encoder reranking | `src/knowledge_base/reranker.py` | `docs/usage/searching.md` |
> > > > > > > | DB schema & migrations | `src/knowledge_base/db.py` | `docs/reference/schema.md` |

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

See `ROADMAP.md` for the full dependency graph. Currently in Phase 2 (embedding architecture + search quality).
