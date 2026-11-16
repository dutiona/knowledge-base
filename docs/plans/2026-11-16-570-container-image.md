# build(container): build the KB server image from the committed uv.lock (#570)

## Outcome

Any KB tag becomes a server image built from git alone: `podman build` on a clean checkout of a tag produces an
image that installs exactly the committed `uv.lock` (no dev group), runs as a fixed non-root UID, keeps the database
and the on-disk artifacts on two separate volumes, imports `knowledge_base.server`, and can take a read-only
`VACUUM INTO` snapshot of a live database with `vec0` tables through Python's `sqlite3` alone, never through a
KB entry point that could start the job worker.

## Constraints

- The `Containerfile` installs with `uv sync --frozen --no-dev` and runs as a non-root user with a fixed UID.
- `KNOWLEDGE_BASE_DB` and `KNOWLEDGE_BASE_DATA_DIR` point at two separate volumes.
- A read-only `VACUUM INTO` through Python's `sqlite3` works on a database with `vec0` tables. It never goes through a KB CLI that starts a job worker, because a worker resets running jobs.
- `podman build` succeeds from a clean checkout of a tag, and an import smoke (`python -c "import knowledge_base.server"`) passes in the image. `knowledge-base --help` would prove nothing: until dutiona/knowledge-base#571 the entry point has no argument parser.
- Likely files: `Containerfile` (new), `.containerignore` (new).
- Epic constraints (spec decision 5): one Containerfile per repository, built from committed lockfiles; images built on dutiona with `podman build` from a release tag, no registry, no new CI dependency.
- Spec decision 16: status and `VACUUM INTO` backups run read-only inside the running container with `podman exec`, with a busy timeout.
- knowledge-base is PUBLIC: no tailnet name, host address, dutiona port mapping or token in any file.
- PR references the umbrella as `Refs dutiona/infra#37`; closes only `#570`.

Decisions this plan makes (each open to the reviewer):

- **One base image, minor-tracked, not digest-pinned.** Both stages start `FROM
  docker.io/library/python:3.12-slim-trixie`; the builder adds uv with `COPY --from=ghcr.io/astral-sh/uv:latest /uv
  /usr/local/bin/uv` (uv's documented pattern). One base guarantees the builder's venv runs on the identical
  interpreter in the runtime stage. Package reproducibility is `uv.lock`'s job; the OS layer tracks security updates
  because the documented build re-pulls (`podman build --pull=newer`). Fully qualified names, because rootless
  podman's short-name resolution can prompt or refuse when run from a unit. Probed 2026-11-16: both pull; uv 0.13.8;
  Python 3.12.16; SQLite 3.46.1 with extension loading enabled.
- **Multi-stage.** uv, hatchling and the build cache stay out of the runtime image; it carries only `/app/.venv`.
- **The build backend gets a floor.** `[build-system] requires = ["hatchling"]` is unbounded and outside `uv.lock`,
  while the project's PEP 639 metadata (`license = "MIT OR Apache-2.0"` + `license-files`) needs a hatchling that
  supports it. A minimum (`hatchling>=1.27`) is a contract, not a pin. Its own boy-scout commit.
- **`uv sync --frozen --no-dev --no-editable --compile-bytecode`**, with `UV_PYTHON_DOWNLOADS=never`.
  `--no-editable` installs the project as a wheel into the venv, so the runtime stage needs no `src/`.
  `--compile-bytecode` because site-packages is root-owned and the runtime user could not write `.pyc` files, so
  every start would recompile. `UV_PYTHON_DOWNLOADS=never` makes a missing image interpreter fail the build instead
  of silently linking the venv to a uv-managed Python that the runtime stage does not have.
- **Fixed UID/GID 10001, user `kb`, home `/home/kb`.** Outside Debian's system (<1000) and the usual first-user
  (1000) ranges. The infra quadlet (dutiona/infra#37, the KB deploy ticket) maps the host service user onto it with
  `UserNS=keep-id:uid=10001,gid=10001`, the `keep-id` pattern the host's existing quadlets already use.
- **Volumes.** `KNOWLEDGE_BASE_DB=/var/lib/knowledge-base/db/knowledge.db` with `VOLUME /var/lib/knowledge-base/db`
  (a directory, because WAL keeps `-wal` and `-shm` beside the file), and
  `KNOWLEDGE_BASE_DATA_DIR=/var/lib/knowledge-base/data` with `VOLUME /var/lib/knowledge-base/data`. Both are created
  owned by `10001:10001`; `/app` stays root-owned and read-only to the service.
- **`CMD ["knowledge-base"]`**, no `ENTRYPOINT`, so `podman run <img> python …` and `podman exec` work as
  written. No `EXPOSE` or health check: the server speaks stdio until #571 adds HTTP; #571 adds both.
- **Optional dependency groups stay out**, exactly as `--no-dev` with no `--group` gives: no `reranker`, `onnx`,
  `openai` or `browser`. Consequence: the deployed KB searches without the cross-encoder reranker (its degradation
  path, locked by #560, falls back with a warning), and the browser fallback is absent, which #569's "browser off"
  wants anyway. Adding `--group reranker` later is a one-line change.
- **The snapshot is a documented recipe, not a new KB module.** The acceptance says "through Python's `sqlite3`",
  and the risk it guards is a KB entry point reaching the job worker; a `python -c` that imports only `sqlite3`
  cannot. A `knowledge_base.snapshot` CLI was considered and rejected: it is a KB CLI, which is what the criterion
  steers away from, and it adds a module for three statements. Probed 2026-11-16: the read-only `VACUUM INTO`
  succeeds **without** loading `sqlite-vec` (virtual-table schema rows and shadow tables are copied as-is), and the
  snapshot passes `integrity_check` with the extension loaded.

The recipe (documented in item 4, pinned by item 1's test). `VACUUM INTO` refuses an existing non-empty target
(`output file already exists`), so a recurring backup removes the previous snapshot, or any partial file a failed run
left, before taking the next one; the check is `sqlite3`-only too:

```sh
rm -f "$SNAP"
python -c 'import sqlite3, sys
c = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=30, autocommit=True)
c.execute("VACUUM INTO ?", (sys.argv[2],))' "$KNOWLEDGE_BASE_DB" "$SNAP"
python -c 'import sqlite3, sys
r = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True).execute("PRAGMA integrity_check").fetchall()
sys.exit(r != [("ok",)])' "$SNAP"
```

The check opens the snapshot `mode=ro` as well: a plain `connect` on a missing snapshot would create an empty
database, whose `integrity_check` is `ok`.

## Implementation sequence

- [ ] **1. Pin the read-only snapshot recipe against a live vec0 database.**
  - Files: `tests/test_db.py`.
  - Test `test_read_only_vacuum_into_snapshots_live_vec0_database`: build a database with `get_connection` +
    `init_schema` on `tmp_path` and set `PRAGMA wal_autocheckpoint=0` on that writer (otherwise the 4 MiB vec0 chunk
    allocation passes the 1000-page autocheckpoint and the commit copies everything into the main file). Insert one
    chunk with its `chunks_vec` row, commit, **keep the writer open**, and assert the `-wal` file is non-empty: the
    committed data lives only in the WAL, as on a live server. Then, with a plain
    `sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30, autocommit=True)` and no `sqlite_vec` import or
    load: run `VACUUM INTO ?`; then assert an `UPDATE chunks SET content = ''` on that connection raises
    `sqlite3.OperationalError` (the connection cannot write, so it cannot reset jobs either; no `jobs` row is needed,
    which avoids its `papers` foreign key and `job_type` CHECK). Open the snapshot `mode=ro`, assert
    `PRAGMA integrity_check` is `[("ok",)]` without the extension, then load `sqlite_vec` and assert a `chunks_vec`
    KNN `MATCH` returns the inserted chunk id.
  - Red: none is possible — the test pins an existing SQLite property KB's schema must keep. Its power is shown by
    one mutation run locally and reverted: replace `VACUUM INTO` with `shutil.copy` of the main file alone, and the
    test fails (`no such table: chunks`), because the data is only in the WAL.
    Command: `uv run pytest tests/test_db.py -k read_only_vacuum -q`.
  - Green: same command, `1 passed`.
  - Commit: `test(db): pin a read-only VACUUM INTO snapshot of a live vec0 database`.

- [ ] **2. Give the build backend a floor.**
  - Files: `pyproject.toml` (`[build-system] requires = ["hatchling>=1.27"]`: hatchling 1.27.0, 2024-11-26, made
    core metadata 2.4 the default, the version that carries PEP 639 `License-Expression` and `License-File`; urllib3
    set the same floor for the same reason).
  - Red/green: no executable behaviour of its own; `uv build --wheel` (into the scratchpad) succeeds before and
    after, and item 3's image build exercises it.
  - Commit: `build(deps): floor hatchling at the first release with PEP 639 license metadata`.

- [ ] **3. Add the Containerfile and the allowlist .containerignore.**
  - Files: `Containerfile` (new), `.containerignore` (new).
  - `.containerignore` is an allowlist: `*`, then `!pyproject.toml`, `!uv.lock`, `!.python-version`,
    `!LICENSE-MIT`, `!LICENSE-APACHE`, `!src/`, then `src/**/__pycache__`. Nothing else (`.env`, `*.db`, `.venv`,
    `.git`, `.worktrees`, `tests`, `qa`, `utils`, `docs`) reaches the build context.
  - `Containerfile`: stage `build` from the Python image with uv copied in from `ghcr.io/astral-sh/uv:latest`,
    `WORKDIR /app`, `ENV UV_PYTHON_DOWNLOADS=never`, explicit `COPY` of the allowlisted files,
    `RUN uv sync --frozen --no-dev --no-editable --compile-bytecode`; stage runtime from the same Python image: `groupadd --gid 10001 kb && useradd --uid 10001 --gid 10001 --create-home kb`, create
    both volume directories owned `10001:10001`, `COPY --from=build /app/.venv /app/.venv`, `ENV` for `PATH`,
    `PYTHONUNBUFFERED=1`, `KNOWLEDGE_BASE_DB`, `KNOWLEDGE_BASE_DATA_DIR`, the two `VOLUME`s, `USER 10001:10001`,
    `WORKDIR /home/kb`, `CMD ["knowledge-base"]`.
  - Red: `podman build -t localhost/knowledge-base:smoke .` fails before the file exists.
  - Green: the image smoke in Verification, every line as expected.
  - Commit: `build(container): build the KB server image from the committed uv.lock`.

- [ ] **4. Document building, running and snapshotting the image.**
  - Files: `docs/getting-started/installation.md` (a `## Container image` section after `## Database location`).
  - Content: build command from a tag checkout, `podman build --pull=newer` so a cached base never hides OS security
    updates; what is installed (`uv.lock`, no dev or optional groups); UID/GID 10001 and the `keep-id` mapping a
    rootless host needs; the two volumes and their variables; the snapshot recipe and its check above, run with
    `podman exec`, the absent-target rule, and why it must never go through `knowledge-base`/`knowledge-base-ingest`
    (the job worker's crash recovery resets running jobs). Generic only: no host, port or deployment detail.
  - Red/green: N/A (prose); `uv run sphinx-build -W -b html docs docs/_build` if the docs group builds cleanly at
    base, else recorded as not run.
  - Commit: `docs(install): build, run and snapshot the container image`.

## Documentation

`docs/getting-started/installation.md` gains the `## Container image` section (item 4). README is unchanged: it points
at the installation guide. No API or MCP surface changes.

## Testing

- Unit: item 1's test in `tests/test_db.py`, in the default `-m "not slow"` run (SQLite only, no network).
- Image: no automated test. A podman-based pytest would add a container runtime to the test gate, which the epic
  rules out (no new CI dependency); the image is proven by the Verification smoke, recorded in the PR, and rebuilt
  by the infra build ticket from each release tag.
- Integration/e2e: N/A — no HTTP surface until #571.

## Verification

Repository gates at the final clean HEAD (unpiped):

```sh
ruff check src/ tests/
ruff format --check src/ tests/
uv run pytest tests/ -q -m "not slow"
uv run basedpyright src/ tests/
```

Image smoke, from a clean clone under the session scratchpad, with the tag created inside that clone only, so the
shared repository never holds it:

```sh
git clone <worktree> "$S/clean" && git -C "$S/clean" tag smoke-570 && git -C "$S/clean" checkout -q smoke-570
podman build --pull=newer -t localhost/knowledge-base:smoke "$S/clean"                                           # succeeds
podman run --rm localhost/knowledge-base:smoke python -c "import knowledge_base.server"                          # exit 0
podman run --rm localhost/knowledge-base:smoke sh -c 'id -u; id -g'                                              # 10001 10001
podman image inspect localhost/knowledge-base:smoke --format '{{.Config.Volumes}} {{.Config.Env}}'               # both volumes, both variables
podman run --rm localhost/knowledge-base:smoke sh -c '! command -v uv && ! python -c "import pytest"'            # no uv, no dev group
```

Snapshot in the image, on volumes mapped as the deployment will map them (`--userns keep-id:uid=10001,gid=10001`,
two host directories): in a long-lived container, create a schema'd database with one `vec0` row through
`get_connection` + `init_schema` (which proves `sqlite_vec` loads on the image's glibc and SQLite 3.46.1), with
`wal_autocheckpoint=0` and the writer held open; `podman exec` the recipe and its `integrity_check` twice (the second
run proves the remove-first rule); then a KNN query on the snapshot in a second `podman run`. Expected: exit 0 both
times and the inserted id.

## Resolution

Lean review: one independent clean-slate reviewer (`docs/plans/2026-11-16-570-container-image-lean-review.md`),
planning-review severity **Default** (every finding resolved). 0 Blocker, 0 High, 2 Medium, 6 Low.

- **F1 (Medium), the test's live-WAL premise was false.** Revised item 1: `PRAGMA wal_autocheckpoint=0` on the
  writer, an assertion that `-wal` is non-empty, and the mutation is now "copy the main file instead of
  `VACUUM INTO`", which fails. The in-image snapshot check holds the writer with autocheckpoint off too.
- **F2 (Medium), the recipe was incomplete for the recurring backup.** The recipe now removes the previous snapshot
  (or a failed run's partial file) first and adds a `sqlite3`-only `integrity_check` that opens the snapshot
  `mode=ro` (a plain `connect` on a missing file would create an empty database that checks `ok`). Item 4 documents
  both; the in-image verification runs the recipe twice, on the image's SQLite 3.46.1.
- **F3 (Low), test-construction traps.** The `jobs` row is dropped: the read-only property is asserted with an
  `UPDATE chunks`, which needs no `papers` parent or valid `job_type`. The read-only connection opens with
  `autocommit=True`. The tautological "source job still running" assertion is gone.
- **F4 (Low), cached bases hide OS updates.** The documented build and the smoke use `podman build --pull=newer`.
- **F5 (Low), the build backend floats outside the lock.** New item 2: `hatchling>=1.27` (1.27.0 made core metadata
  2.4, PEP 639, the default), its own boy-scout commit in this PR.
- **F6 (Low), two bases carried a cross-image invariant.** One base, `python:3.12-slim-trixie`, for both stages, uv
  copied in from `ghcr.io/astral-sh/uv:latest`.
- **F7 (Low), a subsumed verification line.** Dropped; the in-image `get_connection` proves `sqlite_vec` loads.
- **F8 (Low), the scratch tag leaked into the shared refs.** The tag is created inside the scratch clone only.
