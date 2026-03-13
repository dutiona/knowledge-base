# knowledge-base

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
