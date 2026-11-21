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

| Package              | Purpose                                          |
| -------------------- | ------------------------------------------------ |
| fastmcp (>=3.1.0)    | MCP server framework (stdio and HTTP transports) |
| httpx                | HTTP client for Ollama API                       |
| pymupdf, pymupdf4llm | PDF text extraction                              |
| sqlite-vec           | Vector similarity search                         |
| trafilatura          | Web page content extraction                      |
| pillow               | Image processing for figure extraction           |
| uvicorn              | HTTP server for `--http`                         |

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

The server uses FastMCP's stdio transport by default, with every tool, or stateless HTTP behind per-machine tokens
with `--http`, which serves only `search_index`, `ingest_text` and `ingest_url` and refuses to start while
`KNOWLEDGE_BASE_VISION` or `KNOWLEDGE_BASE_BROWSER` is on (see the README's HTTP transport and machine tokens
section). The `knowledge-base` entry point is
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
error gives the archive command above. `git archive` writes the commit into `.archive-commit` (`export-subst` in
`.gitattributes`), a checkout keeps its placeholder, and the build checks it first. A checkout missing the file fails
at its `COPY` instead. The check catches mistakes, not a deliberately edited marker, and it tells an archive from a
checkout, nothing more: an archive of any commit passes, not only a tag's, and so does a directory unpacked from an
archive, so files added to that directory afterwards ship. Podman unpacks
the whole archive, and `.containerignore` lets the build copy only `pyproject.toml`, `uv.lock`, `.python-version`, the
licences, `src/` and the marker.

The image installs exactly `uv.lock` with `uv sync --locked --no-dev --group reranker`: no dev tools, no uv, and a
build that stops when the lock no longer matches `pyproject.toml` (run `uv lock` and commit it). The
reranker group ships its runtime, but not a model: mount one and set `ONNX_RERANK_MODEL_PATH` and
`ONNX_RERANK_TOKENIZER_PATH` as described in [searching](../usage/searching.md#cross-encoder-reranking).

Only steps that run no dependency code have network: the base image's own tools and the fetch, where uv installs the
locked wheels, the server's into its environment and the build backend's (hatchling, the `build` dependency group) into
one the image does not ship. Installing a wheel runs none of its code, and `--no-build` makes a package published only
as source fail the build instead of running its build backend with network. Every later build-stage `RUN` starts with
`RUN --network=none`, which leaves it only loopback: compiling the bytecode (it starts Python, which runs installed
`.pth` files), the backend building the project's wheel, and installing that wheel. The backend is locked and
hash-checked like every other package because it writes the code the server runs; `uv lock --upgrade-package hatchling`
moves it. uv itself, which runs the fetch, comes from `ghcr.io/astral-sh/uv:latest` unpinned: an accepted risk, as uv is
widely used and secured (the owner's ruling, #619).

The server runs as UID/GID `10001`. On a rootless host, map the service user onto it so the volumes stay owned by
that user, with `--userns keep-id:uid=10001,gid=10001` (`UserNS=` in a quadlet). Run it with `--init`
(`RunInit=true` in a quadlet): as PID 1, Python ignores `SIGTERM`, so without an init process `podman stop` waits out
its grace period and then kills the server. The state lives on two volumes:

| Variable                  | Volume                         | Holds                                            |
| ------------------------- | ------------------------------ | ------------------------------------------------ |
| `KNOWLEDGE_BASE_DB`       | `/var/lib/knowledge-base/db`   | `knowledge.db`, its WAL files and its store lock |
| `KNOWLEDGE_BASE_DATA_DIR` | `/var/lib/knowledge-base/data` | figures and other on-disk artifacts              |

The image carries no embedding declaration: pass the `KNOWLEDGE_BASE_EMBED_*` variables (and any
`KNOWLEDGE_BASE_{VISION,BROWSER,LLM}` switch) with `--env` (`Environment=` in a quadlet), and the key
`KNOWLEDGE_BASE_EMBED_API_KEY` through `--env-file` or a secret, never in the unit file. Without them the server
exits 78 at start (see the README's Configuration section). A llama.cpp embedder with a known model file declares its
served artifact too (`KNOWLEDGE_BASE_EMBED_ARTIFACT_SHA256`, `_POOLING`, `_NORMALIZATION`, `_MAX_INPUT_TOKENS`, all
four), so the store records the exact ADR-0015 identity. A database from before schema v3 needs
`knowledge-base-ingest migrate` with the unit stopped before the new image starts.

Maintenance commands run through the same image as the unit, with the same user namespace, both volumes (the named
`db` volume, never the database file alone) and, for a command that embeds, the same declaration, with the unit
stopped. The [store lock](../../README.md#the-store-lock) enforces the stop: a writing command run while the unit
serves, through `podman run` or `podman exec`, exits 1 naming the lock file, and a unit started while a command runs
exits 78 (set `RestartPreventExitStatus=78` so it stays down; start it once the command has finished). Read-only
commands (`status`, `schema`, `migrate --check`, `redact`) run against the live unit. The lock file,
`knowledge.db.store-lock`, stays on the `db` volume: never delete it; after a crash or `podman kill` there is nothing
to clean.

Also set `KNOWLEDGE_BASE_HOST_ADDRESSES` to the host's public IPv4 address and its IPv6 prefix (for example
`203.0.113.7,2001:db8:7::/64`). They are global, so the SSRF floor would otherwise accept them, yet a container
reaching them reaches the host's own services. Tailnet, bridge and gateway addresses are refused without it.

### Snapshotting a running server

Under `--http` the container needs no `-i`. Inside it the server binds `--http 0.0.0.0:<port>` (a loopback bind
inside a bridge or slirp4netns container is not reachable through a published port); the unit publishes that port on
the host's loopback only (`PublishPort=127.0.0.1:<port>:<port>`), so plain HTTP crosses only the hop inside the host,
and mounts the token-hash file read-only for `--token-file`. Podman's stop timeout (`StopTimeout=`, 10 s by default)
bounds how long a stop waits for calls in flight. Over stdio, `knowledge-base` exits when its standard input closes,
so a detached container needs `-i` to stay up.

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

**CDP mode** -- connects to a running browser at an IP address, or at `localhost` with the
`allow_loopback_base_url` opt-in (set through `configure_llm_tool`). The endpoint keeps the SSRF floor, and a host
name is refused because the browser would resolve it again after KB's check:

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
