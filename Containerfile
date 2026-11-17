# The knowledge-base MCP server, built from git alone: the committed uv.lock, no registry.
# Build from a git archive of a tag; a checkout is refused:
#   git archive --format=tar <tag> | podman build --pull=newer -t localhost/knowledge-base:<tag> -
# Network only where no dependency code runs: the base image's tools, and the fetch, where uv installs locked wheels
# (installing a wheel runs none of its code). Every step that runs dependency code, the venv's Python or the build
# backend, starts with RUN --network=none, so code that only builds cannot open a connection from the build host.

FROM docker.io/library/python:3.12-slim-trixie AS build
# uv is the one tool that runs the fetch with network, and it tracks upstream unpinned: an accepted risk (the owner's
# ruling on #600), since uv is widely used and maintained and a pin would have to be bumped by hand.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
# A uv-managed interpreter would leave the venv pointing at a Python the runtime stage lacks.
ENV UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# git archive writes the commit into .archive-commit (export-subst) and a checkout keeps the placeholder, so this
# refuses a working-tree build, clean or dirty, before any source is copied.
COPY .archive-commit ./
RUN grep -Eqx '[0-9a-f]{40}|[0-9a-f]{64}' .archive-commit || { printf 'refusing to build: .archive-commit holds no commit id, so this is not a git archive of a commit; build with: git archive --format=tar <tag> | podman build --pull=newer -t localhost/knowledge-base:<tag> -\n' >&2; exit 1; }
COPY pyproject.toml uv.lock .python-version LICENSE-MIT LICENSE-APACHE ./
# --locked installs exactly uv.lock and fails the build when it no longer matches pyproject.toml; --no-build fails it
# on a package without a wheel (the uv cache starts empty) instead of running that package's build backend here.
# The build group, the locked backend, goes to /backend, which the runtime stage never copies.
RUN uv sync --locked --no-build --no-dev --group reranker --no-install-project \
    && UV_PROJECT_ENVIRONMENT=/backend uv sync --locked --no-build --only-group build
# Offline: compiling starts the venv's Python, which runs its packages' .pth files. Compiled at build time because
# the runtime user cannot write .pyc files into root-owned site-packages; --inexact so it never uninstalls.
RUN --network=none uv sync --locked --offline --inexact --no-dev --group reranker --no-install-project \
    --compile-bytecode
COPY src ./src
# The locked backend builds the wheel (--no-build-isolation, or uv would fetch one); --no-deps because uv.lock chose
# the dependencies. The project installs as a wheel, so the runtime stage needs only the venv.
RUN --network=none uv build --wheel --no-build-isolation --offline --python /backend/bin/python --out-dir /dist
RUN --network=none uv pip install --python /app/.venv/bin/python --offline --no-deps --compile-bytecode /dist/*.whl

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
