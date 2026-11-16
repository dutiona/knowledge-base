# The knowledge-base MCP server, built from git alone: the committed uv.lock, no registry.
# Build from the tag's tracked files alone:
#   git archive --format=tar <tag> | podman build --pull=newer -t localhost/knowledge-base:<tag> -

FROM docker.io/library/python:3.12-slim-trixie AS build
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
# A uv-managed interpreter would leave the venv pointing at a Python the runtime stage lacks.
ENV UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version LICENSE-MIT LICENSE-APACHE ./
COPY src ./src
# Hatchling packages every file under src/, and a dirty checkout's context can carry an untracked .env or
# credentials file, so refuse anything but Python sources. A new kind of package file is allowed here.
RUN stray="$(find src -type f ! -name '*.py')"; \
    if [ -n "$stray" ]; then printf 'refusing to build: non-Python files under src/:\n%s\n' "$stray" >&2; exit 1; fi
# --locked installs exactly uv.lock and fails the build when it no longer matches pyproject.toml;
# --no-editable installs the project as a wheel, so the runtime stage needs only the venv;
# --compile-bytecode because the runtime user cannot write .pyc files into root-owned site-packages.
RUN uv sync --locked --no-dev --group reranker --no-editable --compile-bytecode

FROM docker.io/library/python:3.12-slim-trixie
# Fixed IDs, so a rootless host maps its service user onto them with UserNS=keep-id:uid=10001,gid=10001.
RUN groupadd --gid 10001 kb \
    && useradd --uid 10001 --gid 10001 --create-home kb \
    && install -d -o 10001 -g 10001 /var/lib/knowledge-base/db /var/lib/knowledge-base/data
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    KNOWLEDGE_BASE_DB=/var/lib/knowledge-base/db/knowledge.db \
    KNOWLEDGE_BASE_DATA_DIR=/var/lib/knowledge-base/data
# A directory, not the file: WAL keeps -wal and -shm beside the database.
VOLUME ["/var/lib/knowledge-base/db", "/var/lib/knowledge-base/data"]
USER 10001:10001
WORKDIR /home/kb
CMD ["knowledge-base"]
