# build(container): build the project without network, after a fetch-only step (#600)

## Outcome

The image build gives no network to any code it executes from a dependency. One networked step, before `COPY src`,
installs wheels only, every one from `uv.lock` (`--locked --no-build`): the runtime set into `/app/.venv` and the build
backend, now the locked and hash-checked `build` dependency group, into `/backend`. It starts no Python in an
environment it has filled. Every later build-stage step starts with `RUN --network=none`: compiling the venv's
bytecode, the locked backend building the project's wheel, and installing that wheel (`--offline --no-deps`). The
runtime venv is exactly the lock's runtime set plus the project, with no hatchling, and the image holds no `/backend`
or `/dist`. A checkout is still refused before any source is copied. The `Containerfile` header and the install guide
say which steps have network and why, and the PR states, measured, what the split does to #585's warm-cache image ID.

## Constraints

- Issue Outcome: "The image build gives no network to any code it executes from a dependency. The locked wheels and
  the build backend are fetched in one step that runs none of their code; the project is built by the backend and
  installed under `RUN --network=none`."
- Settled (decision M1 (a) of the dutiona/infra#76 plan, approved by the owner 2026-11-17, "M1 I agree with rec."): the
  fetch step keeps network and installs wheels only; the project build and install run under `RUN --network=none`.
- Open point: "the build backend's version is not locked today either (`requires = ["hatchling>=1.27"]` resolves at
  build time, inside or outside isolation). Pin it here, or record why not."
- Acceptance 1: "The build stage fetches only in the step before `COPY src`, and that step installs wheels only
  (`--no-build` on both commands: a dependency or backend available only as an sdist fails the build instead of running
  its build backend with network); the step that builds and installs the project carries `--network=none`, uses
  `--offline`, and installs with `--no-deps`."
- Acceptance 2: "`git archive --format=tar <tag> | podman build --pull=newer -t localhost/knowledge-base:<tag> -`
  succeeds; a checkout build is still refused by the `.archive-commit` check."
- Acceptance 3: "The image's import smoke passes (`python -c "import knowledge_base.server"`), and the runtime venv
  holds no hatchling (the backend environment stays in the build stage)."
- Acceptance 4: "The Containerfile's header and `docs/getting-started/installation.md` say which steps have network
  and why."
- Acceptance 5: "The PR states what the split does to #585's warm-cache image-ID behaviour (the `uv sync` layer now
  precedes `COPY src`)."
- The `.archive-commit` check (#585) stays the first `RUN`, before any source `COPY`.
- #574 chose `[build-system] requires = ["hatchling>=1.27"]` as "a contract, not a pin". The owner's rules: "Depend on
  contracts, not snapshots: version ranges or minimums. Reproducible builds are the lockfile's job." and "Pin only for a
  reason written next to the pin: a known-bad release, a security fix, or reproducing a published result."
- `ghcr.io/astral-sh/uv:latest` and `python:3.12-slim-trixie` (re-pulled with `--pull=newer`) are #574's decisions,
  not this ticket's.
- Public repository: no home paths, host names, tailnet names, addresses or interface names in the plan, commits, PR or
  docs.
- Merge gate: the `AGENTS.md` Verify block, in order, at the final clean head; CI is not relied on. Every `podman build`
  and `pytest` on the build machine runs under the heavy-job lock, written `$HEAVY`.
- PR: `Closes #600`, `Refs dutiona/infra#37`, `Classification: Security patch (user-confirmed)`; Full
  post-implementation review, Default severity. The owner merges (squash); the worker never merges.

Measured on 252c044 (host uv 0.13.7, podman 5.7.0):

- Since 91e6a76, where the issue's change was built, of `Containerfile`, `.containerignore`, `pyproject.toml`,
  `uv.lock` and the install guide only the guide changed. Every one of the 145 locked packages but the project has
  wheels, so `--no-build` is satisfiable. hatchling is not in `uv.lock`.
- Adding `build = ["hatchling>=1.27"]` to `[dependency-groups]` and running `uv lock` adds 47 lines, four packages
  (hatchling 1.33.0, pathspec 1.1.1, tomlkit 0.15.2, trove-classifiers 2026.9.21.13; packaging and pluggy were locked
  already) and changes no locked version. `UV_PROJECT_ENVIRONMENT=<dir> uv sync --locked --no-build --only-group build`
  installs exactly those six and no project; `uv build --wheel --no-build-isolation --offline --python
  <dir>/bin/python` builds `knowledge_base-0.1.0-py3-none-any.whl` with both licences in its dist-info.
- `.pth` probe: a wheel whose `.pth` appends to a file at interpreter start, installed by `uv sync --locked --no-build
  --no-install-project` with a fresh cache: the `.pth` ran with `--compile-bytecode` and did not run without it.
  Compiling starts the filled venv's Python, which runs site-packages' `.pth` import lines. The following
  `UV_PROJECT_ENVIRONMENT=<backend> uv sync --locked --no-build --only-group build`, fresh cache, did not start it
  either. A second `uv sync --no-install-project` without `--compile-bytecode` into the already-filled venv, fresh
  cache, did run it: uv inspects an existing environment by starting its interpreter. Today's runtime set ships no
  `.pth` file, so this is latent, but it is exactly the code this ticket keeps off the network.
- `uv pip install --compile-bytecode` compiles only the files it installs (uv's help says so; measured: another
  package's module stayed uncompiled). `uv sync --offline --compile-bytecode` on the already-filled venv compiles all
  of site-packages (6755 `.pyc`), with `--inexact` too (an untouched package's module was compiled). A
  `uv sync --no-install-project` without `--inexact` run after the wheel install removes the project, so the compile
  sync precedes the install.
- `--no-build` can reuse a cached wheel uv once built from source (uv's settings reference); the image build starts
  with an empty uv cache, so there it means a source-only package fails.

## Decisions

**D1, the open point: lock the backend in `uv.lock`, pin nothing.** hatchling runs offline now, so it cannot exfiltrate
from the build host, but it writes the wheel the server runs with network. Every runtime wheel is hash-checked against
`uv.lock`; hatchling and its five dependencies would be the only code in the build that is neither locked nor
hash-checked, and two builds of one tag could get different backends. uv does not lock `[build-system] requires`, and a
dependency group is how a package enters the lock. The contract stays `hatchling>=1.27` (#574); the exact version and
hashes live in `uv.lock`, moved by `uv lock --upgrade-package hatchling` in a reviewed commit. This applies
"reproducible builds are the lockfile's job" rather than making an exception to the pin rule. Rejected:

- `==` in `[build-system]`: a snapshot in the contract with no written reason, no hash, the five dependencies still
  floating.
- `==` in the `Containerfile`: a second version source, unhashed.
- `[tool.uv] build-constraint-dependencies` with hashes: uv checks those hashes only in build isolation, which would
  need network in the build step; a separate hashed constraints file is a second lock `uv lock` neither writes nor
  checks.
- uv's bundled `uv_build` backend, which would remove the third-party backend instead of locking it: it reopens #574's
  backend choice for every developer build, and the backend becomes the copy inside `ghcr.io/astral-sh/uv:latest`, which
  floats by #574's choice. With uv's recommended upper bound (`uv_build>=X,<0.(N+1)`) each uv minor release breaks the
  build until the bound moves; with a bare minimum the wheel's producer is whatever uv the build pulled, less
  reproducible than a locked hatchling. It is the smaller trusted base (uv alone, which the fetch already trusts with
  network); pinning the uv image would remove those costs but is #574's decision. Surfaced for Michaël at approval.
- Recording why not: leaves the gap above.

Cost: the floor is written twice, so a test keeps the group equal to `[build-system] requires`. Not covered: a
developer's `uv sync`/`uv build` still resolves the backend in build isolation, on a developer machine rather than the
build host.

**D2: bytecode is compiled offline.** The issue's fetch carries `--compile-bytecode`, which (measured above) runs
installed wheels' `.pth` code with network, contradicting its own Outcome. The fetch drops it; an offline
`uv sync --locked --offline --inexact … --compile-bytecode` right after it compiles the whole venv (`--inexact`, so a
selection that drifts from the fetch's can never uninstall anything), and the offline wheel install keeps
`--compile-bytecode` for the project's own files. The runtime user still never has to write a `.pyc`.

**D3: each offline command is its own `RUN --network=none` instruction**, as in dutiona/memory-engine#1068; `uv build`
gets `--offline` too, so neither offline uv command can try a download whatever builder runs the file.

## Implementation sequence

- [ ] **1. Lock the build backend** (`pyproject.toml`, `uv.lock`, `tests/test_container_build.py`)
  - Red: `test_build_group_is_the_declared_backend` reads `pyproject.toml` with `tomllib` and asserts
    `data["dependency-groups"]["build"] == data["build-system"]["requires"]`.
    `$HEAVY uv run pytest tests/test_container_build.py -q` → 1 failed (`KeyError: 'build'`), 3 passed.
  - Change: append to `[dependency-groups]`, after `browser`, then run `uv lock` (no `--upgrade`):

    ```toml
    # The image's build backend: the Containerfile builds the project with this group from uv.lock, so the code that
    # writes the wheel is hash-checked like every runtime wheel. Keep it equal to [build-system] requires.
    build = ["hatchling>=1.27"]
    ```

  - Green: the test run → 4 passed; `uv lock --check` exits 0; `git diff --numstat -- uv.lock` shows `<n> 0` (the lock
    only gains entries).
  - Commit: `build(deps): lock the hatchling build backend in uv.lock`

- [ ] **2. Fetch with network; compile, build and install without it** (`Containerfile`,
  `tests/test_container_build.py`)
  - Red: `test_dependency_code_runs_without_network` takes the build stage's instructions (between the first two
    `FROM`s, comments dropped, `\`-continued lines joined, whitespace collapsed) and asserts:
    - the build-stage instructions up to and including the fetch, `FROM` aside, are exactly this sequence:
      `COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv`, `ENV UV_PYTHON_DOWNLOADS=never`,
      `WORKDIR /app`, `COPY .archive-commit ./`, the marker check, `COPY pyproject.toml uv.lock .python-version
      LICENSE-MIT LICENSE-APACHE ./`, then the fetch, the exact string `RUN uv sync --locked --no-build --no-dev
      --group reranker --no-install-project && UV_PROJECT_ENVIRONMENT=/backend uv sync --locked --no-build
      --only-group build`. Exact text, not a token parser: every review round of a parser found a narrower bypass
      (`--compile`, `-p`, shell syntax in a value, a second sync into a filled environment, an equivalent path), and
      pinning the whole prefix also closes the edits that change what the fetch does without touching it (a `UV_*`,
      `PATH` or other `ENV`, a `SHELL`, a `RUN` that writes a uv config file first). The prefix is the protected
      surface, so changing it, a uv image change included (#574's decision), is meant to be a deliberate edit of this
      test too; `FROM` is left out so a base-image bump does not touch it;
    - `pyproject.toml`'s `[tool.uv]` has no `compile-bytecode` key, the one setting outside the `Containerfile` that
      would make the fetch compile;
    - every build-stage instruction after the fetch is `COPY src ./src` or starts with `RUN --network=none `, at least
      one such `RUN` follows `COPY src ./src`, every `uv` command in them carries `--offline`, and the
      `uv pip install` carries `--no-deps`.

    `_archive_check()` also asserts that the check is the build stage's first `RUN` and precedes every build-context
    `COPY` but the marker's (`COPY --from=` is not one). Today it only checks `COPY src ./src`: the fetch no longer
    needs `src/`, which makes hoisting `COPY pyproject.toml …` and the fetch above the check, for cross-commit layer
    reuse, the tempting next edit, and #585 refuses before any source is copied. The module-level skip (git, `sh` and
    a checkout) moves onto the two existing tests, `test_checkout_marker_is_refused_with_the_archive_command` and
    `test_archived_marker_passes`, which need all three; the two new tests only read files. The module docstring widens
    to the image build's static guarantees. The test run → 1 failed (the networked `uv sync` follows `COPY src`),
    4 passed.
  - Change: after the header's third line add

    ```dockerfile
    # Network only where no dependency code runs: the base image's tools, and the fetch, where uv installs locked wheels
    # (installing a wheel runs none of its code). Every step that runs dependency code, the venv's Python or the build
    # backend, starts with RUN --network=none, so code that only builds cannot open a connection from the build host.
    ```

    and replace the three comment lines and `RUN uv sync` around `COPY src ./src` with

    ```dockerfile
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
    ```

    Nothing else changes: the marker check, `.containerignore`, the runtime stage.
  - Green: the test run → 5 passed; temporarily moving `COPY pyproject.toml …` and the fetch `RUN` above
    `COPY .archive-commit ./` fails the three test cases that call `_archive_check()` and the network test (its
    prefix sequence no longer matches), then revert. Behavioural green: Verification V1 to V6.
  - Commit: `build(container): build the project without network, after a fetch-only step`

- [ ] **3. Say in the install guide which build steps have network and why** (`docs/getting-started/installation.md`,
  `## Container image`)
  - Red: `grep -c 'network=none' docs/getting-started/installation.md` prints `0` (prose, no executable behaviour).
  - Change: after the paragraph beginning "The image installs exactly `uv.lock`", a new paragraph, wrapped at 120:

    > Only steps that run no dependency code have network: the base image's own tools and the fetch, where uv installs
    > the locked wheels, the server's into its environment and the build backend's (hatchling, the `build` dependency
    > group) into one the image does not ship. Installing a wheel runs none of its code, and `--no-build` makes a
    > package published only as source fail the build instead of running its build backend with network. Every later
    > build-stage `RUN` starts with `RUN --network=none`, which leaves it only loopback: compiling the bytecode (it
    > starts Python, which runs installed `.pth` files), the backend building the project's wheel, and installing that
    > wheel. The backend is locked and hash-checked like every other package because it writes the code the server runs;
    > `uv lock --upgrade-package hatchling` moves it.

  - Green: the grep prints `1`.
  - Commit: `docs(install): say which image build steps have network and why`

## Documentation

The `Containerfile` header and step comments (item 2), the `## Container image` section of
`docs/getting-started/installation.md` (item 3) and the comment beside the `build` group in `pyproject.toml` (item 1).
These are the only places the image build is documented. The guide's other paragraphs (archive, checkout, allowlist,
UID, volumes, snapshot) stay true, and its "installs exactly `uv.lock` with `uv sync --locked --no-dev --group
reranker`" still describes the runtime set. Checked, unchanged: `README.md` (no image section), `AGENTS.md` (the gate's
`uv sync --all-groups` now also installs the `build` group into the dev venv, harmlessly), `docs/usage/searching.md`
(its `uv sync --group reranker` is the developer install). No changelog exists.

## Testing

Unit, static, in the merge gate (`tests/test_container_build.py`): the `build` group equals `[build-system] requires`
(`uv lock --check` never reads `[build-system]`, so a raised floor would otherwise reach neither the lock nor the
image); the build stage up to the fetch is an exact instruction sequence ending in the exact fetch line, after it only
`COPY src ./src` and offline `RUN`s, no `[tool.uv]` key makes the fetch compile, and the offline uv commands carry
`--offline` (and the install `--no-deps`); the archive check is the first `RUN` and precedes every source `COPY`. An
image that loses any of these still builds, so only a check notices. They pin the security flags and order, not output
format, and cannot prove isolation.

Integration, as PR evidence, not committed: real podman builds V1 to V6. A green offline build shows only that a step
did not need the network, not that it lacked it, so V1 observes the namespace against a networked control on the same
builder and V2 has the backend itself report what it sees in the real step. A committed podman test would need network
and minutes, outside the `not slow` gate; dutiona/memory-engine#1068 made the same call. Not tested: a source-only
dependency failing under `--no-build`, which is uv's documented behaviour; the static test pins the flag.

## Verification

From the worktree root. `$S` and `$LOGS` are private scratch directories outside the worktree, never committed; each
command writes its log to `$LOGS` unpiped and ends with `echo "exit=$?"`. Builds add `--cpuset-cpus=0-15`, a host-load
bound that is not part of the documented command.

- **V1, isolation on this builder** (not committed): `$S/v1/Containerfile` holds
  `FROM docker.io/library/python:3.12-slim-trixie`,
  `RUN python -c "import socket; print('interfaces', len(socket.if_nameindex()))"` and the same `RUN` with
  `--network=none`; `$HEAVY podman build --no-cache --cpuset-cpus=0-15 -t localhost/kb600-netprobe "$S/v1"`. Expect
  exit 0, the first count 2 or more, the second 1. The PR quotes counts only.
- **V2, the backend's own view** (not committed): unpack `git archive --format=tar HEAD` into `$S/v2`, append
  `[tool.hatch.build.hooks.custom]` / `path = "src/netcanary.py"` to its `pyproject.toml` (under `src/`, because
  `.containerignore` admits nothing else), and write `src/netcanary.py`, a `BuildHookInterface` whose `initialize`
  raises `RuntimeError(f"netcanary: the build backend sees {len(socket.if_nameindex())} network interfaces")`.
  `$HEAVY bash -o pipefail -c "tar -C '$S/v2' -cf - . | podman build --no-cache --cpuset-cpus=0-15 -t
  localhost/kb600-canary -"`. Expect a non-zero exit with the fetch and compile steps completed and, under the
  `RUN --network=none uv build` step, `netcanary:` reporting 1 (uv may print the message more than once).
- **V3, production path, fresh:** `git status --porcelain -- Containerfile .containerignore pyproject.toml uv.lock
  src` prints nothing; then `$HEAVY bash -o pipefail -c 'git archive --format=tar HEAD | podman build --pull=newer
  --no-cache --cpuset-cpus=0-15 -t localhost/knowledge-base:600 -'`. Expect exit 0 and, in order, the marker check, the
  fetch (its second install listing `hatchling==` the version in `uv.lock`), the offline compile, `COPY src ./src`, the
  offline build (`Successfully built …knowledge_base-0.1.0-py3-none-any.whl`), the offline install
  (`+ knowledge-base==0.1.0`), the runtime stage. `HEAD` stands in for `<tag>`: no release tag holds this change yet.
- **V4, the image**, each as `podman run --rm --network=none localhost/knowledge-base:600 …`:
  - `python -c "import knowledge_base.server"` exits 0, and no command (the `knowledge-base` script) exits 78, the
    documented refusal without an embedding declaration.
  - The runtime set: the image's `importlib.metadata` distributions (`name==version`, names normalized, sorted) into
    `$LOGS/image-dists.txt`, the same list from `UV_PROJECT_ENVIRONMENT="$S/ref" uv sync --locked --no-build --no-dev
    --group reranker --no-install-project` on the host into `$LOGS/ref-dists.txt`;
    `diff "$LOGS/ref-dists.txt" "$LOGS/image-dists.txt" | grep '^[<>]'` prints exactly `> knowledge-base==0.1.0`, and
    `! grep -q '^hatchling==' "$LOGS/image-dists.txt"` exits 0.
  - `sh -c 'test ! -e /backend && test ! -e /dist'` exits 0.
  - `.../site-packages/httpx/__pycache__/__init__.cpython-312.pyc` and
    `.../site-packages/knowledge_base/__pycache__/__init__.cpython-312.pyc` both exist: D2 compiled third-party code
    and the project.
- **V5, a checkout is refused:** `$HEAVY podman build --cpuset-cpus=0-15 -t localhost/kb600-checkout .`. Expect a
  non-zero exit, `refusing to build: .archive-commit holds no commit id`, and no `uv sync` step run.
- **V6, #585's cache and image ID:** right after V3, the same archive again without `--no-cache` or `--pull=newer` as
  `:600-warm`: exit 0, every non-`FROM` step a cache hit, `podman image inspect --format '{{.Id}}'` prints one ID for
  `:600` and `:600-warm`. Then a commit with HEAD's exact build inputs but another id: in a scratch clone of the
  worktree, `git checkout --detach <HEAD's sha>` and `git commit --allow-empty -m 'chore: probe'`, and build its
  archive as `:600-next`: exit 0, the fetch runs
  again (install output under its step, no cache hit from `COPY .archive-commit ./` on) and the ID differs. The scratch
  commit is never pushed; it works after any review fix, unlike a fixed `HEAD~1`.
- **V7, merge gate:** `uv lock --check`; `uv sync`; `uv run ruff check src/ tests/`; `uv run ruff format --check src/
  tests/`; `$HEAVY uv run pytest tests/ -q -m "not slow"`; `uv sync --all-groups`; `uv run basedpyright src/ tests/`.
  Every exit 0, `git status --porcelain` empty after; the PR quotes each summary line.

A review fix changing a `RUN`/`COPY` line, `.containerignore`, `pyproject.toml` or `uv.lock` repeats V2 to V6; a
comment- or docs-only fix repeats items 2 and 3's checks; every fix repeats V7. Once the evidence is final,
`podman rmi` the tags this work created by name; never prune globally (the layer cache is shared).

Acceptance map: A1 → item 2's test, V2, V3's step order; A2 → V3, V5; A3 → V4; A4 → items 2 and 3; A5 → V6 and the
PR statement; open point → D1, item 1.

## Operations

1. Work in the existing locked worktree of branch `build/600-offline-build`; stage files by name (the drafts directory
   enters no commit).
2. The plan commit holds exactly five files under `docs/plans/`: the plan, its altitude report, and the three review
   files `2026-11-17-offline-image-build-advisor.md`, `2026-11-17-offline-image-build-subagent-review.md` and
   `2026-11-17-offline-image-build-external-codex.md` (Codex's round verdicts, which the Super evidence manifest
   requires). The drafts directory, the review tree's copies and diffs and the reviewers' raw transcripts and logs are
   never committed. Scrub those five first, for the real identifiers rather than generic words: collect into a pattern
   file outside the worktree `$HOME`, `hostname` and `hostname -f`, the VPN's own and its peers' host and domain names,
   every interface name but `lo` (`ip -o link`) and every non-loopback address (`ip -o addr`, prefix length stripped);
   then each staged file, with its `<owner>/<repo>` references to the public GitHub owner deleted first (per match, with
   `sed -E 's#\b<owner>/[A-Za-z0-9._-]+##g'`, never by dropping whole lines), piped to `grep -nwF -f <that file>` prints
   nothing. Redact what it finds, keeping the finding's substance; a redaction in the plan refreshes its digest and
   altitude report and goes back to review. The repository and the plan issue are public, and a squash merge does not
   unpublish a PR's head commits. Then commit them: `docs(plans): plan the offline image build (#600)`. `finish-pr`'s
   archiver publishes the plan to its one plan issue before approval (approval is asked outside this conversation), and
   `finish-pr` Item 5 removes the five files from the branch before merge.
3. After approval, land items 1 to 3 in order, run V1 to V7, push, and open the PR titled
   `build(container): build the project without network, after a fetch-only step`. Body: what changed; D1 to D3 as the
   departures from the issue's Change text; the #585 statement; a Test plan quoting V1/V2's counts, V3's step lines and
   backend version, V4, V5, V6's IDs and V7's summaries, scrubbed of paths, host and interface names; the Constraints'
   PR lines plus `Execution path: Standard`, `Planning tier: Super`, `Post-implementation review: Full (Default
   severity)` and the plan issue with its sha256. The #585 statement: "The fetch now precedes `COPY src`, but
   `COPY .archive-commit ./`, whose content is the commit id, precedes the fetch, so builds of different commits still
   share no layer after the marker and the fetch re-runs for every commit, as since #585 (V6). Rebuilding the same
   archive on a warm cache still reuses every layer and repeats the image ID (V6). Sharing the fetch across commits
   would need it before the check, which #585 rules out and item 2's test now refuses."
4. `/super-review` at the Full gate, Default severity; then `/finish-pr` to READY.
5. The owner merges (squash); the worker never merges.
