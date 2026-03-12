# Installation

## Requirements

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** package manager
- **[Ollama](https://ollama.com/)** for embeddings and optional LLM extraction

## Ollama setup

Install Ollama, then pull the embedding model:

```bash
ollama pull bge-m3
```

BGE-M3 produces 1024-dimensional embeddings and is the default model.

For structured extraction (methods, datasets, metrics), pull an LLM:

```bash
ollama pull qwen3.5:27b
```

Any Ollama-compatible model works. Configure it at runtime with `configure_llm_tool`.

### WSL2 note

Ollama typically runs on the Windows host, not inside WSL. The server auto-detects
the Windows host IP via the default gateway. Override with `OLLAMA_HOST` if needed:

```bash
export OLLAMA_HOST=http://192.168.1.100:11434
```

## Clone and install

```bash
git clone https://github.com/dutiona/knowledge-base.git
cd knowledge-base
uv sync
```

This installs all runtime dependencies:

| Package              | Purpose                                |
| -------------------- | -------------------------------------- |
| fastmcp (>=3.1.0)    | MCP server framework (stdio transport) |
| httpx                | HTTP client for Ollama API             |
| pymupdf, pymupdf4llm | PDF text extraction                    |
| sqlite-vec           | Vector similarity search               |
| trafilatura          | Web page content extraction            |
| pillow               | Image processing for figure extraction |

## MCP client registration

Add the server to your MCP client configuration. For Claude Code (`~/.claude.json`
or project-level `.mcp.json`):

```json
{
  "mcpServers": {
    "knowledge-base": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/path/to/knowledge-base",
        "knowledge-base"
      ],
      "env": {}
    }
  }
}
```

The server uses FastMCP's stdio transport. The `knowledge-base` entry point is
defined in `pyproject.toml` and maps to `knowledge_base.server:main`.

## Database location

The SQLite database is created automatically at:

```
~/.local/share/knowledge-base/research.db
```

No manual setup required. The schema is initialized on first connection.

## Optional dependencies

### Vision model for figure extraction

Figure extraction requires a multimodal model. The default is `gemma3:27b`:

```bash
ollama pull gemma3:27b
```

Configure with `configure_vision_tool`. Any vision-capable model served via
an OpenAI-compatible `/v1/chat/completions` endpoint works.

### OmniParser for figure OCR enrichment

[OmniParser](https://github.com/microsoft/OmniParser) adds OCR text and icon
detection to figure descriptions. It requires a separate Python venv due to
dependency conflicts:

```bash
git clone https://github.com/microsoft/OmniParser.git
cd OmniParser
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Register the path with `configure_omniparser_tool`:

```
configure_omniparser_tool(path="/path/to/OmniParser")
```

The server expects `parse.py` and `.venv/bin/python` inside the OmniParser directory.

### Playwright for JS-heavy web pages

When `trafilatura` extracts insufficient content from a URL (< 200 characters),
the server falls back to browser rendering. Two modes:

**Local mode** -- launches headless Chromium:

```bash
python -m venv /path/to/playwright-venv
/path/to/playwright-venv/bin/pip install playwright
/path/to/playwright-venv/bin/playwright install --with-deps chromium
```

**CDP mode** -- connects to a running browser (e.g., Docker container):

```bash
python -m venv /path/to/playwright-venv
/path/to/playwright-venv/bin/pip install playwright
```

Register with `configure_browser_tool`:

```
# Local mode
configure_browser_tool(venv_path="/path/to/playwright-venv")

# CDP mode
configure_browser_tool(
    venv_path="/path/to/playwright-venv",
    cdp_endpoint="ws://localhost:3000"
)
```
