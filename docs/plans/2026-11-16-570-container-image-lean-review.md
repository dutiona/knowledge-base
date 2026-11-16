Reviewer: Claude Opus (fresh general-purpose subagent, no prior context) via Agent subagent

~~~~
# Lean plan review: knowledge-base #570 (container image)

Plan: `docs/plans/2026-11-16-570-container-image.md` (worktree `build-570-container`, base `8fa69bd`).

## Verdict

Sound plan. It needs two Medium fixes before implementation, both in the snapshot work (test premise, and the recipe
the downstream tickets need). No Blocker or High. All four acceptance criteria of #570 are covered and nothing in the
plan contradicts one. Confidence: high. The Medium findings were reproduced with the worktree's `.venv`
(SQLite 3.53.1 on the host) against a database built by `get_connection` + `init_schema`.

## Acceptance coverage

| #570 criterion | Plan coverage | Status |
|---|---|---|
| `uv sync --frozen --no-dev`, non-root fixed UID | item 2 (`uv sync --frozen --no-dev --no-editable --compile-bytecode`, `USER 10001:10001`); Verification `id -u; id -g`, the no-uv/no-pytest line | Covered |
| `KNOWLEDGE_BASE_DB` / `KNOWLEDGE_BASE_DATA_DIR` on two separate volumes | item 2 (two `ENV`, two `VOLUME`); Verification `image inspect` | Covered |
| read-only `VACUUM INTO` through Python `sqlite3` on a vec0 DB, never via a worker-starting KB CLI | item 1 test, recipe (plan:59-63), in-image snapshot (plan:143-146), item 3 docs | Covered (see F1, F2) |
| `podman build` from a clean tag checkout + `python -c "import knowledge_base.server"` | Verification plan:135-136 | Covered |

## Points checked and found fine

- **Hatchling build inputs.** `pyproject.toml` has no `readme` field. `license-files` (pyproject.toml:10) names exactly
  `LICENSE-MIT` and `LICENSE-APACHE`, the version is static (`0.1.0`, :7), and the package is auto-detected at
  `src/knowledge_base`. The allowlist (`pyproject.toml`, `uv.lock`, `.python-version`, both licences, `src/`) is
  complete. `find src -type f ! -name '*.py'` returns nothing, so no package data is at stake.
- **`.containerignore` allowlist semantics.** Podman 5.7.0 / buildah 1.42.1 (local) use the moby pattern matcher:
  last match wins and parent-directory matching applies, so `*` then `!src/` re-includes the whole subtree, and
  `src/**/__pycache__` is valid. A subtree that went missing would make the wheel build fail, or would fail the
  `import knowledge_base.server` smoke, because `server.py:7-12` imports every `routes/` module. The Containerfile
  being excluded by `*` does not matter, because podman reads it from the host path.
- **Venv surviving the stage copy.** The builder has `/usr/local/bin/python3.12`, Python 3.12.16, checked by running
  the local `ghcr.io/astral-sh/uv:python3.12-trixie-slim`. The local `python:3.12-slim-trixie` has the same
  `PYTHON_VERSION=3.12.16`. The venv is copied to the identical path `/app/.venv`, so the absolute shebangs and the
  `pyvenv.cfg` `home` both resolve. `--no-editable` is what makes the runtime stage need no `src/`. That is correct.
- **`--compile-bytecode` and `UV_PYTHON_DOWNLOADS=never`.** The rationale is correct: site-packages is root-owned,
  and the setting prevents a silent uv-managed interpreter. The uv image does not set `UV_PYTHON_DOWNLOADS` itself
  (its env has only `UV_TOOL_BIN_DIR`), so the explicit `ENV` is needed.
- **Optional groups out.** No module imports `openai` (grep over `src/`). `OpenAICompatProvider`
  (`embeddings.py:176`) is httpx-based, so dropping the `openai` group cannot break KB-1's tailnet embedder.
  `onnxruntime` and `playwright` are imported lazily (`reranker.py:91`, `embeddings.py:304`,
  `browser/render_page.py:90`). TLS to the Mac goes through `certifi`, which is in the locked runtime deps.
- **UID 10001, volume ownership, WAL.** Creating and chowning the directories before `VOLUME` is the right order.
  A directory volume is required: `get_connection` sets `journal_mode=WAL` (`db.py:385`), so `-wal` and `-shm` live
  beside the file, and the parent `mkdir` (`db.py:378`) needs a writable parent. `podman exec` runs as the image
  `USER`, so the snapshot process owns `-shm`, which a `mode=ro` reader of a WAL database needs.
- **Read-only recipe semantics.**
  - Python maps the write on a `mode=ro` connection to `sqlite3.OperationalError: attempt to write a readonly
    database`. Reproduced, so the plan's assertion type is right.
  - `VACUUM INTO ?` accepts a bound parameter.
  - `timeout=30` is the busy timeout that decision 16 asks for.
  - `VACUUM INTO` works without loading sqlite-vec. Reproduced: virtual-table schema rows are copied verbatim and the
    shadow tables copied as ordinary tables.
  - The snapshot passes `integrity_check` with and without the extension, and a KNN `MATCH` on it returns the row.
- **Worker risk framing.** `jobs.py:138` is exactly `UPDATE jobs SET status = 'pending' ... WHERE status = 'running'`.
  A `python -c` that imports only `sqlite3` cannot reach it, so rejecting a `knowledge_base.snapshot` module is right.
- **No `ENTRYPOINT`, no `EXPOSE`/health check before #571, minor-tracked bases.** Fine, and consistent with the
  "pin only for a written reason" rule.

## Findings

### F1 (Medium): the test's "data still in `-wal`" premise is false, so the test does not exercise a live WAL

Plan:70-72 says that after the commit, with the writer kept open, "the data [is] still in `-wal`". It is not. The
first `chunks_vec` insert makes vec0 allocate a full vector chunk: 1024 rows × 1024 dims × 4 B = 4 MiB of blob pages,
more than the default `wal_autocheckpoint` of 1000 pages. The commit therefore triggers a passive checkpoint. With no
other reader open, that checkpoint copies everything into the main file.

Reproduced on this base, using `get_connection` + `init_schema` + one chunk + `insert_chunk_vec` + commit with the
writer left open: `-wal` was 4 923 432 B and the main file 4 481 024 B, and a plain `shutil.copy` of the main file
alone already contained the chunk. So the test would pass even if the recipe were a naive file copy, which is exactly
the regression a "live database" test should catch.

The planned mutation (plan:77-78, "take the snapshot before the vec insert") only shows that the KNN assertion
notices a missing row. It does not show that the WAL matters.

Fix:

- Run `PRAGMA wal_autocheckpoint=0` on the writer right after `get_connection`.
- Optionally assert that `-wal` is non-empty.
- Change the mutation to "replace `VACUUM INTO` with a copy of the main file". It then fails with
  `no such table: chunks`. Reproduced: with autocheckpoint off, the main file stays at 4096 B, and the
  `VACUUM INTO` snapshot still returns the inserted id from KNN.
- Apply the same change to the in-image snapshot verification (plan:143-146), where the long-lived writer has the
  same autocheckpoint behaviour.

### F2 (Medium): the documented recipe is incomplete for the downstream backup ticket (INF-7, 4×/day)

- **The target must not exist.** The second `VACUUM INTO` to the same path fails with `output file already exists`
  (reproduced). The recipe hard-codes `/var/lib/knowledge-base/db/snapshot.db` (plan:62), so a recurring INF-7 run
  fails from the second run onward unless something removes or rotates the file first. Item 3 should state the rule:
  the target must be absent or empty, so remove it first or use a fresh name. It should also say that a failed run
  can leave a partial file to delete.
- **The check step is missing.** INF-7 requires the snapshot to be "checked with `integrity_check` (for KB, through
  Python's `sqlite3`, never its job-starting CLI)" (epic:537). Item 3 documents only the `VACUUM INTO`. Add the
  one-line `PRAGMA integrity_check` on the snapshot, also `sqlite3`-only. On the host (3.53.1) it returns `ok`
  without sqlite-vec loaded. Re-confirm that on the image's 3.46.1 in the in-image verification, since that is the
  SQLite INF-7 will run.

### F3 (Low): test-construction details the plan gets wrong or leaves as traps

- **The `jobs` row needs a parent.** `jobs.paper_id` is `INTEGER NOT NULL REFERENCES papers(id)` (`db.py:1069`),
  `get_connection` turns `foreign_keys=ON` (`db.py:386`), and `job_type` is CHECK-constrained (`db.py:1070`). So
  "insert one jobs row" (plan:70) also needs a `papers` row and a valid `job_type`, or the setup raises
  `IntegrityError`.
- **The assertion order is load-bearing.** In Python 3.12's legacy transaction mode, the failed `UPDATE` leaves an
  implicit `BEGIN` open, and any later `VACUUM INTO` on that connection fails with
  `cannot VACUUM from within a transaction` (reproduced). The plan's order, `VACUUM` first, is right. Opening the
  read-only connection with `autocommit=True` (3.12+) removes the trap.
- **"The source job is still `running`" (plan:74) is tautological.** The only write attempted was the one just
  asserted to fail. Drop it, or keep it knowing it adds nothing.

### F4 (Low): "the OS layer tracks security updates" holds only if the build re-pulls

The plan's rationale for minor-tracked bases (plan:28) assumes the bases get refreshed. `podman build`'s default pull
policy is `missing`, so once dutiona has a cached base it is reused for every later release, and security updates
never arrive. Item 3's build command should use `podman build --pull=newer`, so that INF-5's `memory-build` inherits
it.

### F5 (Low): the build backend is outside the lock

`[build-system] requires = ["hatchling"]` (pyproject.toml:2) is unbounded, and hatchling does not appear in `uv.lock`.
With `--no-editable`, uv builds the KB wheel through whatever hatchling is current at build time. The runtime packages
are exactly `uv.lock`; the wheel builder floats. The project relies on PEP 639 metadata (`license = "MIT OR
Apache-2.0"` plus `license-files`), which needs a recent hatchling (1.27+, I think). A floor such as
`hatchling>=1.27` is a contract rather than a snapshot. It is a pre-existing gap: surface it to Michaël. It is a
one-line boy-scout commit if he wants it.

### F6 (Low): the two-base design depends on a cross-image invariant that one base would remove

The venv works only because both images put Python 3.12 at `/usr/local/bin/python3.12`. That holds today
(3.12.16 in both). The builder and runtime are still pulled and cached independently, though, so the patch versions
can drift. That drift is harmless inside 3.12, but it is an invariant the image does not need. The alternative is
`FROM docker.io/library/python:3.12-slim-trixie` for both stages, with
`COPY --from=ghcr.io/astral-sh/uv:<tag> /uv /usr/local/bin/uv` in the builder, which is uv's documented pattern. It
guarantees the same interpreter and costs no extra lines. The plan's choice works; this is optional.

### F7 (Low, over-engineering): Verification line plan:140 is subsumed

`sqlite3.connect(':memory:').enable_load_extension(True)` proves only that extension loading is compiled in. The
in-image snapshot step (plan:143-146) builds the database through `get_connection`, which calls `sqlite_vec.load`
(`db.py:381-383`). That proves the stronger property, that the vec0 `.so` loads on the image's glibc and SQLite.
Drop line 140.

### F8 (Low): the scratch tag is never cleaned up

Plan:135 creates `smoke-570` in the shared repository's refs ("never pushed"), but no step deletes it. A later
`git push --tags` from that clone would publish it to the public repository. Add `git tag -d smoke-570` after the
smoke.

## Downstream needs (INF-5, INF-7, INF-8) checked

- INF-5 labels revision and version at build time with `--label`, so the Containerfile needs no `ARG`.
- INF-8's separate database and data directories and its `UserNS=keep-id:uid=10001,gid=10001` mapping are covered by
  item 2 and item 3.
- The HTTP transport, `EXPOSE` and the health check belong to #571, as the plan states.
- The only gaps are F2 (INF-7's rotation and integrity step) and F4 (INF-5's pull policy).

## Reviewer note

The probes ran in the session scratchpad
`<scratch>/probe570/`. That
directory already existed: the plan author's earlier probe had left `k.db*`, `snap.db` and possibly a `probe.py`
there. My cleanup glob removed `snap.db`, and my `probe.py` may have overwritten one already there. `k.db*` are
untouched. The repository was not modified.

REVIEW COMPLETE
~~~~
