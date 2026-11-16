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

Ollama typically runs on the Windows host, not inside WSL. Embeddings use the declared
`KNOWLEDGE_BASE_EMBED_URL`, so declare the Windows host's address there:

```bash
export KNOWLEDGE_BASE_EMBED_URL=http://192.168.1.100:11434
```

`OLLAMA_HOST` (and the default-gateway auto-detection) now steers only the LLM and vision
defaults, when those are switched on.

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
      "env": {
        "KNOWLEDGE_BASE_EMBED_PROVIDER": "ollama",
        "KNOWLEDGE_BASE_EMBED_MODEL": "bge-m3",
        "KNOWLEDGE_BASE_EMBED_DIM": "1024",
        "KNOWLEDGE_BASE_EMBED_URL": "http://localhost:11434"
      }
    }
  }
}
```

The `env` block is required: the server refuses to start without an explicit embedding
declaration, and a blank database is created from it (see the README's Configuration
section). Vision, the browser fallback and LLM extraction stay off unless you add
`KNOWLEDGE_BASE_VISION`, `KNOWLEDGE_BASE_BROWSER` or `KNOWLEDGE_BASE_LLM` set to `"on"`.

The server uses FastMCP's stdio transport. The `knowledge-base` entry point is
defined in `pyproject.toml` and maps to `knowledge_base.server:main`.

## Database location

The SQLite database is created automatically at:

```
~/.local/share/knowledge-base/knowledge.db
```

No manual setup required. The schema is initialized on first connection.

To use a different location, set it by precedence (highest first):

1. `--db-path PATH` (the `knowledge-base-ingest` CLI; `--db` is a back-compat alias)
2. `KNOWLEDGE_BASE_DB=PATH` environment variable (honored by both the CLI and the MCP server)
3. otherwise the default above

A configured path is used verbatim — its parent directory is created if needed, and
there is no silent fallback to the default.

## Container image

The repository's `Containerfile` builds a server image from git alone, from a `git archive` of a release tag that
contains it (the first release after it landed, or any later one). The archive holds the tag's tracked files and
nothing else, so no untracked or git-ignored file in a working tree can reach the image. Let podman re-pull the base so
a cached one never hides OS security updates:

```bash
git archive --format=tar <tag> | podman build --pull=newer -t localhost/knowledge-base:<tag> -
```

A build from a checkout, clean or dirty, `podman build .` included, is refused before any source is copied, and the
error names that command. `git archive` writes the commit into `.archive-commit` (`export-subst` in `.gitattributes`),
a checkout keeps its placeholder, and the build checks it first. A checkout missing the file fails at its `COPY`
instead. The check tells an archive from a checkout, nothing more: an archive of any commit passes, not only a tag's,
and so does a directory unpacked from an archive, so files added to that directory afterwards ship. Podman unpacks
the whole archive, and `.containerignore` lets the build copy only `pyproject.toml`, `uv.lock`, `.python-version`, the
licences, `src/` and the marker.

The image installs exactly `uv.lock` with `uv sync --locked --no-dev --group reranker`: no dev tools, no uv, and a
build that stops when the lock no longer matches `pyproject.toml` (run `uv lock` and commit it). The
reranker group ships its runtime, but not a model: mount one and set `ONNX_RERANK_MODEL_PATH` and
`ONNX_RERANK_TOKENIZER_PATH` as described in [searching](../usage/searching.md#cross-encoder-reranking).

The server runs as UID/GID `10001`. On a rootless host, map the service user onto it so the volumes stay owned by
that user, with `--userns keep-id:uid=10001,gid=10001` (`UserNS=` in a quadlet). Run it with `--init`
(`RunInit=true` in a quadlet): as PID 1, Python ignores `SIGTERM`, so without an init process `podman stop` waits out
its grace period and then kills the server. The state lives on two volumes:

| Variable                  | Volume                         | Holds                               |
| ------------------------- | ------------------------------ | ----------------------------------- |
| `KNOWLEDGE_BASE_DB`       | `/var/lib/knowledge-base/db`   | `knowledge.db` and its WAL files    |
| `KNOWLEDGE_BASE_DATA_DIR` | `/var/lib/knowledge-base/data` | figures and other on-disk artifacts |

The image carries no embedding declaration: pass the `KNOWLEDGE_BASE_EMBED_*` variables (and any
`KNOWLEDGE_BASE_{VISION,BROWSER,LLM}` switch) with `--env` (`Environment=` in a quadlet), and the key
`KNOWLEDGE_BASE_EMBED_API_KEY` through `--env-file` or a secret, never in the unit file. Without them the server
exits 78 at start (see the README's Configuration section).

### Snapshotting a running server

Until the server gains an HTTP transport (#571), `knowledge-base` speaks MCP over stdio and exits when its standard
input closes, so a detached container needs `-i` to stay up.

Take a backup inside the running container, read-only, through Python's `sqlite3` alone. Never use `knowledge-base`
or `knowledge-base-ingest` for it: a KB entry point can start the job worker, whose crash recovery resets running
jobs. `VACUUM INTO` refuses an existing target, so the recipe writes a fresh temporary file, checks it, and only then
replaces the previous snapshot; a failed run leaves the last good snapshot in place and exits non-zero:

```bash
podman exec knowledge-base sh -c 'SNAP=/var/lib/knowledge-base/db/snapshot.db; rm -f "$SNAP.tmp"
python -c "import sqlite3, sys
c = sqlite3.connect(f\"file:{sys.argv[1]}?mode=ro\", uri=True, timeout=30, autocommit=True)
c.execute(\"VACUUM INTO ?\", (sys.argv[2],))" "$KNOWLEDGE_BASE_DB" "$SNAP.tmp" &&
python -c "import sqlite3, sys
r = sqlite3.connect(f\"file:{sys.argv[1]}?mode=ro\", uri=True).execute(\"PRAGMA integrity_check\").fetchall()
sys.exit(None if r == [(\"ok\",)] else f\"integrity_check: {r}\")" "$SNAP.tmp" &&
mv "$SNAP.tmp" "$SNAP"'
```

The `timeout` is the busy timeout. The snapshot includes committed data still in the WAL, and needs no `sqlite-vec`:
the `vec0` tables are copied as they are. The check opens the snapshot read-only too, because a plain `connect` on a
missing file would create an empty database whose `integrity_check` is `ok`.

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
