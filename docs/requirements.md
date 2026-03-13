# Requirements

## Required

| Dependency                       | Version    | Purpose                                                |
| -------------------------------- | ---------- | ------------------------------------------------------ |
| Python                           | >= 3.12    | Runtime                                                |
| [uv](https://docs.astral.sh/uv/) | latest     | Package management (replaces pip)                      |
| [Ollama](https://ollama.com/)    | latest     | Embedding model host                                   |
| bge-m3 model                     | via Ollama | Default embedding model (1024 dimensions)              |
| SQLite with FTS5                 | stdlib     | Full-text search (included in Python's sqlite3 module) |

### Python packages (installed automatically)

These are declared in `pyproject.toml` and installed by `uv sync`:

| Package     | Version        | Purpose                                     |
| ----------- | -------------- | ------------------------------------------- |
| fastmcp     | >= 3.1.0       | MCP server framework                        |
| httpx       | >= 0.28.1      | HTTP client for Ollama and vision API calls |
| pillow      | >= 10.0        | Image processing for figure cropping        |
| pymupdf     | >= 1.27.1      | PDF rendering and page analysis             |
| pymupdf4llm | >= 1.27.2, < 2 | Markdown extraction from PDFs               |
| sqlite-vec  | >= 0.1.6       | SQLite vector similarity extension          |
| trafilatura | >= 2.0.0       | Web page content extraction                 |

## Optional

### LLM for structured extraction

Required by `extract_structure_tool` and `configure_llm_tool`.

| Component | Default                  | Notes                                                          |
| --------- | ------------------------ | -------------------------------------------------------------- |
| LLM model | `qwen3.5:27b` via Ollama | Any Ollama-hosted or OpenAI-compatible model works             |
| Provider  | `ollama`                 | Also supports `openai_compat` for remote/alternative endpoints |

Configure with `configure_llm_tool`. The server runs a connectivity test on configuration changes.

### Vision model for figure extraction

Required by `extract_figures_tool` and `configure_vision_tool`.

| Component    | Default                   | Notes                                  |
| ------------ | ------------------------- | -------------------------------------- |
| Vision model | `gemma3:27b` via Ollama   | Must support image inputs (multimodal) |
| Base URL     | auto-detected from Ollama | Override with `configure_vision_tool`  |

### OmniParser for figure enrichment

Adds OCR text and icon detection to extracted figure descriptions.

| Component               | Notes                                                     |
| ----------------------- | --------------------------------------------------------- |
| OmniParser installation | Separate directory with `parse.py` and `.venv/bin/python` |

Configure with `configure_omniparser_tool`. Not required for basic figure extraction.

### Playwright for browser rendering

Enables JS-rendered fallback for web page ingestion (when trafilatura extracts < 200 chars).

| Component                     | Notes                                                                     |
| ----------------------------- | ------------------------------------------------------------------------- |
| Python venv with `playwright` | Separate venv from the main project                                       |
| Chromium                      | Installed via `playwright install --with-deps chromium` (local mode)      |
| CDP endpoint                  | WebSocket endpoint for Docker/remote mode (alternative to local Chromium) |

Two modes:

- **Local mode**: Provide `venv_path` only. Requires Chromium installed in the venv.
- **CDP mode**: Provide both `cdp_endpoint` and `venv_path`. Chromium runs remotely.

Configure with `configure_browser_tool`.

## System requirements

- **Disk**: Database grows with ingested content. Embeddings (1024-dim float32) add ~4 KB per chunk.
- **Memory**: Ollama models require GPU or sufficient RAM (bge-m3: ~2 GB, qwen3.5:27b: ~16 GB, gemma3:27b: ~16 GB).
- **Network**: Ollama API must be reachable (default `http://localhost:11434`, override via `OLLAMA_HOST` environment variable).
