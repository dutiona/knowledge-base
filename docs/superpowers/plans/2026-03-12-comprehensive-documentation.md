# Comprehensive Documentation — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set up Sphinx + MyST documentation infrastructure and write all core documentation pages for the research-index project (partial implementation of issue #71 — documentation workstream only). Also update ROADMAP.md to mark Phase 0 complete. This PR does NOT close #71; follow-up PRs for typing and API reference are required before #71 can be closed and Phase 1 exit criteria met.

**Architecture:** Sphinx with MyST-Parser for markdown-native docs. Auto-build validates all pages compile. No deployment — just the content + config. Each doc page is a standalone MyST markdown file organized by audience (getting-started, usage, design, reference).

**Tech Stack:** Sphinx, myst-parser, sphinx-mermaid (for architecture diagrams), Python 3.12+

---

## Scope

**In scope (this PR):**

- Sphinx infrastructure (conf.py, deps, index.md, build config)
- Getting-started docs (installation, quickstart, core concepts)
- Usage guides (ingesting, searching, extraction, figures, relationships, bibtex)
- Design docs (architecture overview with Mermaid diagrams)
- Reference docs (MCP tools, schema, glossary)
- Requirements page
- ROADMAP.md update (Phase 0 complete)
- README.md fix (embed model says nomic-embed-text, code uses bge-m3)

**Out of scope (follow-up PRs):**

- ADRs (requires mining closed GitHub issues — separate research task)
- Comparison section (requires competitive analysis)
- Scaling considerations (speculative)
- Typing improvements (pyright config, TypedDict — code changes)
- Test coverage setup (pytest-cov, CI enforcement — tooling changes)
- Auto-generated API reference (reference/api.md — requires autodoc + typing PR first)
- Hosted site deployment (GitHub Pages — issue says non-goal)

---

## Chunk 1: Infrastructure + ROADMAP

### Task 1: Add Sphinx dev dependencies

**Files:**

- Modify: `pyproject.toml`

- [ ] **Step 1: Add docs dependency group to pyproject.toml**

Add a `docs` dependency group alongside the existing `dev` group:

```toml
[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "ruff>=0.15.5",
]
docs = [
    "sphinx>=8.0",
    "myst-parser>=4.0",
    "sphinxcontrib-mermaid>=1.0",
    "sphinx-rtd-theme>=3.0",
]
```

- [ ] **Step 2: Install docs deps and verify**

Run: `cd /home/mroynard/dev/research-index/.worktrees/docs-71 && uv sync --group docs`
Expected: Dependencies install successfully

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "docs: add Sphinx + MyST documentation dependencies"
```

### Task 2: Create Sphinx configuration

**Files:**

- Create: `docs/conf.py`

- [ ] **Step 1: Write docs/conf.py**

```python
"""Sphinx configuration for research-index documentation."""

project = "research-index"
author = "Michael Roynard"
release = "0.1.0"

extensions = [
    "myst_parser",
    "sphinxcontrib.mermaid",
]

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
    "tasklist",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", "superpowers", "plans", "insights"]

html_theme = "sphinx_rtd_theme"
html_static_path = []

# MyST settings
myst_heading_anchors = 3

# Mermaid settings
mermaid_output_format = "raw"
```

Key decisions:

- `exclude_patterns` includes `superpowers`, `plans`, and `insights` to skip existing non-doc markdown from the built site
- `myst_heading_anchors = 3` generates anchor links for h1-h3
- `mermaid_output_format = "raw"` lets the browser render Mermaid (no server needed)
- No autodoc yet — that comes with the typing improvement PR

- [ ] **Step 2: Verify Sphinx can find the config**

Run: `cd /home/mroynard/dev/research-index/.worktrees/docs-71 && uv run --group docs sphinx-build -b html docs docs/_build 2>&1 | tail -5`
Expected: Build completes (warnings about missing index.md are OK at this stage)

### Task 3: Create docs landing page

**Files:**

- Create: `docs/index.md`

- [ ] **Step 1: Write docs/index.md**

The landing page should:

- State what research-index is (one paragraph)
- Link to getting-started, usage, design, and reference sections
- Use a MyST toctree directive to organize navigation

````markdown
# research-index

Hybrid semantic search MCP server for research papers, code, and notes. Ingests
documents into a local SQLite database with FTS5 full-text search and sqlite-vec
vector similarity, then exposes them as MCP tools for AI assistants.

## Documentation

```{toctree}
:maxdepth: 2
:caption: Getting Started

getting-started/installation
getting-started/quickstart
getting-started/core-concepts
```

```{toctree}
:maxdepth: 2
:caption: Usage

usage/ingesting-documents
usage/searching
usage/structured-extraction
usage/figure-extraction
usage/relationships-conclusions
usage/bibtex-export
```

```{toctree}
:maxdepth: 2
:caption: Design

design/architecture-overview
```

```{toctree}
:maxdepth: 2
:caption: Reference

reference/mcp-tools
reference/schema
reference/glossary
requirements
```
````

### Task 4: Update ROADMAP.md — Phase 0 complete

**Files:**

- Modify: `ROADMAP.md`

- [ ] **Step 1: Mark all Phase 0 items as done**

Update Phase 0 section to reflect all issues are closed:

- PR #89 → merged (close #88)
- #85 → fixed (PR #112)
- #78 → done (PR #118)
- #46 → done (PR #114)
- #45 → done (PR #116)
- #16 → done (PR #115)

Change the Phase 0 section to show completion status. Add checkmarks or
strikethrough to indicate completion. Update the "Last updated" date.

Also update Phase 1 to show #71 is in-progress.

- [ ] **Step 2: Fix README.md embed model reference**

README line 48 says `ollama pull nomic-embed-text` but `db.py:13` sets
`DEFAULT_EMBED_MODEL = "bge-m3"` and `DEFAULT_EMBED_DIM = 1024`. Update
the README to reflect the actual default.

Also update the architecture diagram in README (line 35) which says
`embeddings.py (Ollama nomic-embed-text)` → should say `embeddings.py (Ollama bge-m3)`.

Fix README line 168: config table says `embed_dim` default is `768` but
`db.py:14` sets `DEFAULT_EMBED_DIM = 1024`. Update to `1024`.

Fix README line 30: architecture diagram says "FastMCP Server (24 tools)"
→ should say "FastMCP Server (33 tools)".

- [ ] **Step 3: Commit**

```bash
git add ROADMAP.md README.md
git commit -m "docs: mark Phase 0 complete, fix embed model references in README"
```

### Task 5: Create directory structure and stub files

**Files:**

- Create: `docs/getting-started/` directory
- Create: `docs/usage/` directory
- Create: `docs/design/` directory
- Create: `docs/reference/` directory

- [ ] **Step 1: Create all documentation directories**

```bash
mkdir -p docs/getting-started docs/usage docs/design docs/reference
```

- [ ] **Step 2: Verify Sphinx builds with index.md**

Run: `cd /home/mroynard/dev/research-index/.worktrees/docs-71 && uv run --group docs sphinx-build -b html docs docs/_build 2>&1 | tail -10`
Expected: Build runs (warnings about missing referenced files are expected — we'll create them next)

- [ ] **Step 3: Add \_build to .gitignore if not already present**

Check if `docs/_build` is ignored. If not, add it to `.gitignore`.

- [ ] **Step 4: Commit infrastructure**

```bash
git add docs/conf.py docs/index.md .gitignore
git commit -m "docs: add Sphinx infrastructure (conf.py, index.md, directory structure)"
```

---

## Chunk 2: Getting Started

### Task 6: Write installation.md

**Files:**

- Create: `docs/getting-started/installation.md`

- [ ] **Step 1: Write installation.md**

Must cover:

- **Python 3.12+** requirement
- **uv** package manager (link to docs.astral.sh/uv)
- **Ollama** setup:
  - Installation link
  - `ollama pull bge-m3` for embeddings (1024-dim)
  - `ollama pull qwen3.5:27b` or alternative for structured extraction (optional)
  - WSL2 note: Ollama runs on Windows host, accessed via `OLLAMA_HOST`
- **Clone + install:**
  ```bash
  git clone https://github.com/dutiona/research-index.git
  cd research-index
  uv sync
  ```
- **MCP client registration:** JSON config snippet for Claude Code
- **Optional dependencies:**
  - OmniParser for figure OCR enrichment (link to repo, note: requires separate venv)
  - Playwright for JS-heavy web pages (local or CDP mode)
  - Vision model for figure extraction (e.g. `ollama pull gemma3:27b`)

Source of truth: `pyproject.toml` (deps), `db.py:10-14` (defaults), `vision.py` (vision config), `ingest.py` (browser config). MCP client registration snippet follows standard FastMCP `stdio` transport conventions (author from project patterns, not copied from a specific source file).

- [ ] **Step 2: Verify build**

Run: `uv run --group docs sphinx-build -b html docs docs/_build 2>&1 | grep -E "(ERROR|warning:.*installation)" | head -5`
Expected: No errors related to installation.md

### Task 7: Write quickstart.md

**Files:**

- Create: `docs/getting-started/quickstart.md`

- [ ] **Step 1: Write quickstart.md**

A walk-through showing the three core workflows:

**1. Ingest a paper:**

- Use `ingest` tool with a local PDF path
- Show expected JSON output (chunks_added, chunks_skipped)

**2. Search the index:**

- Use `search_index` with a natural language query
- Explain the result fields (chunk_id, content, score, match_type)
- Show filtering by source_type
- Show mode switching (hybrid vs fts vs vec)

**3. Extract structure:**

- Register the paper with `register_paper_tool`
- Run `extract_structure_tool` with the paper_id
- Show the extracted methods, datasets, metrics

Source of truth: `server.py` tool docstrings for parameter names and descriptions

### Task 8: Write core-concepts.md

**Files:**

- Create: `docs/getting-started/core-concepts.md`

- [ ] **Step 1: Write core-concepts.md**

Define the domain model. Must cover:

**Chunks** — The atomic unit. Documents are split into chunks (~1000 chars for text, AST-aware for Python). Each chunk gets a content hash (dedup), an embedding vector, and FTS5 indexing. Source types: pdf, markdown, code, web, note, figure.

**Papers** — Metadata records (title, authors, year, venue, DOI). A paper links to chunks via source_uri matching. Papers can have multiple file paths (paper_paths table).

**Entities** — Resolved names for methods and datasets (not metrics — metrics are stored directly in the metrics table without entity resolution). Entity resolution maps surface forms ("ResNet-50", "ResNet50", "resnet-50") to canonical names. Entities are scoped per-paper.

**Relationships** — Typed edges between papers: extends, contradicts, replicates, cites, compares, applies, implements. Each has a confidence score and optional evidence chunk.

**Conclusions** — Evidence-chained claims with supersession. A conclusion links to source chunks and can be superseded by a newer conclusion, forming a revision chain.

**Jobs** — Background tasks for expensive operations (extract_structure, extract_figures). Queued, polled via job_id.

**Hybrid search** — BM25 (FTS5) + cosine similarity (sqlite-vec), merged via Reciprocal Rank Fusion (k=60). Over-fetches 3x, then re-ranks.

Use a Mermaid entity-relationship diagram showing: chunks ← papers, papers ↔ relationships, chunks ← conclusions, papers ← entities ← entity_mentions → chunks.

Source of truth: `db.py:188-381` (schema), `search.py:62-76` (RRF), `extraction.py` (entities)

- [ ] **Step 2: Commit getting-started docs**

```bash
git add docs/getting-started/
git commit -m "docs: add getting-started guides (installation, quickstart, core-concepts)"
```

---

## Chunk 3: Reference

### Task 9: Write mcp-tools.md

**Files:**

- Create: `docs/reference/mcp-tools.md`

- [ ] **Step 1: Write mcp-tools.md**

Comprehensive reference for all 33 MCP tools. Organized by category matching server.py.
For each tool, document:

- Tool name (as exposed via MCP)
- Description (from docstring)
- Parameters with types and defaults
- Return format (JSON structure)
- Example usage

Categories (from server.py):

1. **Ingest** (3): ingest, reingest, ingest_url
2. **Search** (2): search_index, status
3. **Papers** (9): register_paper_tool, get_paper_tool, add_relationship_tool, get_relationships_tool, export_bibtex_tool, sync_bibtex_tool, suggest_relationships_tool, relocate_paper_tool, get_paper_paths_tool
4. **Conclusions** (4): record_conclusion_tool, get_conclusions_tool, supersede_conclusion_tool, get_conclusion_chain_tool
5. **Extraction** (6): record_method_tool, record_dataset_tool, record_metric_tool, compare_papers_tool, extract_structure_tool, get_entities_tool
6. **Figures** (1): extract_figures_tool
7. **Config** (6): embed_config, re_embed_tool, configure_llm_tool, configure_vision_tool, configure_omniparser_tool, configure_browser_tool
8. **Jobs** (2): get_job_status_tool, list_jobs_tool

Source of truth: `server.py` (all @mcp.tool() decorated functions)

### Task 10: Write schema.md

**Files:**

- Create: `docs/reference/schema.md`

- [ ] **Step 1: Write schema.md**

Document every table, column, constraint, index, and trigger. Organized as:

For each table:

- Purpose (one sentence)
- Column definitions (name, type, constraints, default)
- Foreign keys
- Unique constraints
- Indexes

Tables (from db.py init_schema):

1. config — key-value store
2. chunks — document content with content-hash dedup
3. chunks_fts — FTS5 virtual table (auto-synced via triggers)
4. chunks_vec — sqlite-vec virtual table (float[dim])
5. papers — paper metadata
6. paper_paths — filesystem paths per paper
7. relationships — inter-paper typed edges
8. papers_fts — FTS5 on paper titles
9. conclusions — evidence-chained claims
10. executions — execution history
11. methods — methods per paper
12. datasets — datasets per paper
13. metrics — metric values linking methods + datasets
14. entities — resolved entity names
15. entity_mentions — surface forms + chunk mentions
16. jobs — background extraction jobs

Include a Mermaid ER diagram showing FK relationships.

Source types CHECK constraint: pdf, markdown, code, web, note, figure
Relationship types CHECK constraint: extends, contradicts, replicates, cites, compares, applies, implements
Job types CHECK constraint: extract_structure, extract_figures
Job status CHECK constraint: pending, running, completed, failed

Source of truth: `db.py:188-381`

### Task 11: Write glossary.md

**Files:**

- Create: `docs/reference/glossary.md`

- [ ] **Step 1: Write glossary.md**

Use MyST glossary directive. Terms:

- BM25, chunk, content hash, cosine similarity, entity, entity mention,
  entity resolution, evidence chain, FTS5, hybrid search, map-reduce extraction,
  MCP (Model Context Protocol), OmniParser, Reciprocal Rank Fusion (RRF),
  sqlite-vec, supersession, surface form, WAL mode

Each definition should be 1-2 sentences, cross-referencing other glossary terms where appropriate.

### Task 12: Write requirements.md

**Files:**

- Create: `docs/requirements.md`

- [ ] **Step 1: Write requirements.md**

Must cover:

**Required:**

- Python >= 3.12
- uv package manager
- Ollama (local or network) with bge-m3 model (approximate download size varies by platform)
- SQLite with FTS5 support (included in Python stdlib)
- Disk usage depends on document size and count (estimate empirically)

**Optional:**

- LLM for structured extraction: Ollama or OpenAI-compatible endpoint
  - Recommended: qwen3.5:27b or equivalent
  - Minimum: any model supporting JSON output
- Vision model for figure extraction: gemma3:27b or equivalent
- OmniParser for figure OCR enrichment (separate installation)
- Playwright for JS-heavy web page ingestion

**System dependencies:**

- sqlite-vec native extension (installed via pip, no manual compilation)
- PyMuPDF for PDF processing (installed via pip)

Source of truth: `pyproject.toml` (deps), `db.py` (defaults), `vision.py` (vision models)

- [ ] **Step 2: Commit reference docs**

```bash
git add docs/reference/ docs/requirements.md
git commit -m "docs: add reference documentation (MCP tools, schema, glossary, requirements)"
```

---

## Chunk 4: Architecture + Usage Guides

### Task 13: Write architecture-overview.md

**Files:**

- Create: `docs/design/architecture-overview.md`

- [ ] **Step 1: Write architecture-overview.md**

Must include:

**High-level architecture** — Mermaid diagram showing:

```
MCP Client → FastMCP Server → Module layer → SQLite + Ollama
```

**Module responsibilities** — One paragraph per module:

- `server.py` — FastMCP entry point, tool registration, thread-local connections
- `db.py` — Schema creation, migrations, batch SQL helpers
- `ingest.py` — PDF/markdown/code/web ingestion, chunking, embedding, dedup
- `search.py` — Hybrid FTS5 + vec search with RRF merging
- `extraction.py` — LLM-powered map-reduce extraction, entity resolution
- `vision.py` — Figure extraction via vision model + OmniParser
- `papers.py` — Paper CRUD, relationships, BibTeX export, citation suggestion
- `conclusions.py` — Evidence-chained conclusions with supersession
- `embed_swap.py` — Atomic embedding model swap + re-embedding
- `embeddings.py` — Ollama embedding client
- `jobs.py` — Background job queue for long-running extraction
- `browser/render_page.py` — Playwright-based page rendering for JS-heavy URLs and vision screenshots

**Data flow diagrams** (Mermaid):

1. Ingest flow: file → detect type → chunk → hash dedup → embed → store
2. Search flow: query → embed → FTS5 + vec search → RRF merge → fetch chunks
3. Extraction flow: paper chunks → map (per-chunk LLM) → reduce (merge + entity resolution) → store

**Thread safety model:**

- Thread-local connections (threading.local in server.py)
- Schema init protected by threading.Lock (double-checked locking)
- WAL mode for read concurrency

**Design choices** (brief, linking to future ADRs):

- Single SQLite file (portability, zero-config)
- Content-hash dedup (idempotent ingestion)
- RRF over learned fusion (no training data needed)
- Map-reduce for unbounded documents

Source of truth: all source files

### Task 14: Write usage guides

**Files:**

- Create: `docs/usage/ingesting-documents.md`
- Create: `docs/usage/searching.md`
- Create: `docs/usage/structured-extraction.md`
- Create: `docs/usage/figure-extraction.md`
- Create: `docs/usage/relationships-conclusions.md`
- Create: `docs/usage/bibtex-export.md`

- [ ] **Step 1: Write ingesting-documents.md**

Cover:

- **PDF ingestion** — `ingest` tool, auto-detection, pymupdf4llm structured extraction
- **Markdown ingestion** — fixed-size chunking (header-aware chunking only applies to PDF content extracted via pymupdf4llm, not plain .md files)
- **Code ingestion** — Python AST-aware chunking, fixed-size for other languages
- **Web ingestion** — `ingest_url`, trafilatura extraction, browser fallback for JS pages
- **Directory ingestion** — recursive walk, limited to `.pdf, .md, .txt, .typ, .rst` extensions (code files like `.py` must be ingested individually via `ingest` tool, not via directory scan)
- **Re-ingestion** — `reingest` tool, force-deletes old chunks and reinserts (not skip-on-unchanged — that's normal `ingest` behavior). Cleans up FK references in papers, relationships, conclusions
- **Chunking strategy** — fixed-size (1000 chars, 200 char overlap) for text/markdown; AST-aware for Python code; pymupdf4llm header-aware chunking for PDFs
- **Deduplication** — content-hash (SHA-256) on normal `ingest` — unchanged chunks are skipped. `reingest` always force-replaces
- **Embedding** — automatic embedding on ingest, uses configured model

Source: `ingest.py` (all public functions), `server.py:111-172`

- [ ] **Step 2: Write searching.md**

Cover:

- **Hybrid search** — default mode, combines FTS5 BM25 + sqlite-vec cosine
- **FTS-only mode** — keyword search, good for exact terms
- **Vec-only mode** — semantic search, good for conceptual queries
- **RRF merging** — how scores are combined (k=60 constant)
- **Source type filtering** — filter by pdf, markdown, code, web, note, figure
- **Result format** — chunk_id, content, source_type, source_uri, chunk_index, score, match_type
- **Query tips** — when to use which mode, how FTS5 tokenization works (porter stemming + unicode61)
- **Status tool** — checking index statistics

Source: `search.py`, `server.py:175-277`

- [ ] **Step 3: Write structured-extraction.md**

Cover:

- **What gets extracted** — methods, datasets, metrics, entities
- **Map-reduce architecture** — why (unbounded doc size), how (chunk → extract → merge)
- **Entity resolution** — surface form → canonical name deduplication (methods and datasets only; metrics bypass entity resolution)
- **Manual recording** — record_method_tool, record_dataset_tool, record_metric_tool
- **LLM extraction** — extract_structure_tool, ETA warning, confirmed flag, background jobs
- **Cross-paper comparison** — compare_papers_tool on shared datasets
- **Entity inspection** — get_entities_tool with surface forms and chunk mentions
- **LLM configuration** — configure_llm_tool (ollama vs openai_compat)
- **Connectivity test** — configure_llm_tool saves config first, then runs an advisory connectivity probe (failure returns a warning, does not rollback)

Source: `extraction.py`, `server.py:565-715`

- [ ] **Step 4: Write figure-extraction.md**

Cover:

- **How it works** — renders PDF pages as images → vision model describes figures → stored as 'figure' chunks
- **Vision model config** — configure_vision_tool (model name + base_url)
- **OmniParser enrichment** — optional OCR + icon detection layer
- **Page selection** — auto-detect candidate pages or specify explicitly (1-based)
- **Background jobs** — always queued (rendering + API calls are slow)
- **ETA gate** — warning for >2min estimated, confirm to proceed
- **Chunk encoding** — figure chunks use special chunk_index encoding (page_slot)
- **Browser config** — configure_browser_tool for JS rendering (CDP or local)

Source: `vision.py`, `server.py:718-841`

- [ ] **Step 5: Write relationships-conclusions.md**

Cover:

- **Relationship types** — extends, contradicts, replicates, cites, compares, applies, implements
- **Adding relationships** — add_relationship_tool (upserts on conflict)
- **Querying relationships** — get_relationships_tool (direction: outgoing/incoming/both)
- **Auto-suggestion** — suggest_relationships_tool (DOI matching, title word overlap, author+year)
- **Recording conclusions** — claim + confidence + source_chunk_ids + session_context
- **Supersession** — supersede_conclusion_tool creates a new conclusion and links old → new
- **Evidence chains** — get_conclusion_chain_tool traces oldest → newest
- **Filtering conclusions** — keyword search, min_confidence, include_superseded flag

Source: `papers.py` (relationships), `conclusions.py`, `server.py:346-471`

- [ ] **Step 6: Write bibtex-export.md**

Cover:

- **Export to string** — export_bibtex_tool returns BibTeX content
- **Export to file** — output_path parameter, .bib/.bibtex extension required
- **Path validation** — must be under home or cwd (security)
- **Sync mode** — sync_bibtex_tool appends only new entries, skips duplicates
- **Filtering** — by paper_ids or title_pattern
- **Typst integration** — how to use exported .bib with Typst's #bibliography()
- **Citation keys** — auto-generated from first-author surname + year (DOI is stored as a field in the BibTeX entry, not used for the key)

Source: `papers.py` (export_bibtex, sync_bibtex), `server.py:474-523`

- [ ] **Step 7: Commit usage + architecture docs**

```bash
git add docs/usage/ docs/design/
git commit -m "docs: add usage guides and architecture overview"
```

### Task 15: Final build verification

- [ ] **Step 1: Run full Sphinx build**

Run: `cd /home/mroynard/dev/research-index/.worktrees/docs-71 && uv run --group docs sphinx-build -W -b html docs docs/_build 2>&1 | tail -20`

The `-W` flag treats warnings as errors. Fix any broken cross-references or toctree issues.

Expected: Clean build with 0 warnings.

- [ ] **Step 2: Run existing tests to confirm no regressions**

Run: `cd /home/mroynard/dev/research-index/.worktrees/docs-71 && PYTHONPATH=src pytest tests/ -q --tb=short 2>&1 | tail -5`

Expected: All tests pass (223+ tests). No regressions from docs-only changes.

- [ ] **Step 3: Run ruff on any Python files changed**

Run: `ruff check docs/conf.py`

Expected: No lint errors

- [ ] **Step 4: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "docs: fix build warnings"
```
