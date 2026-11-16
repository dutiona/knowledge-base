# build(container): build the KB image only from a git archive of a tag (#580)

## Outcome

The knowledge-base image builds only from a `git archive` of a commit, normally a release tag. `git archive` fills in a
tracked marker file through `export-subst`, and a checkout keeps its literal placeholder. The build checks that marker
first, before any source is copied, and refuses a checkout (clean or dirty) with a message that names the archive
command. The allowlist `.containerignore` still filters what the archive build can copy. The image content is unchanged:
only build timestamps differ, as in any uncached rebuild.

## Constraints

- Michaël (2026-11-16): "ok archive only build". This supersedes #570's "`podman build` succeeds from a clean checkout of a tag" wording.
- `podman build` from a checkout (clean or dirty) fails with a message pointing at the archive command; `git archive <tag> | podman build -` succeeds and gives the same image ID as before.
- The mechanism doesn't send the whole tracked tree to the builder (the review concern from #575): the allowlist `.containerignore` still filters the archive context.
- `docs/getting-started/installation.md` documents only the archive build.
- No untracked or git-ignored file under `src/` can ever reach the image.
- knowledge-base is public: no host name, address or token in any file, commit or PR.
- Touch `docs/getting-started/installation.md` only among the docs (#581 edits the AGENTS/CLAUDE/GEMINI lists).

## Decision for Michaël (approving this plan decides it)

Acceptance 1 has two halves, and no Containerfile change satisfies both literally:

- **"same image ID as before"** can hold only with a warm layer cache. Measured on podman 5.7.0 / buildah 1.42.1 with
  fixed bases: a `--no-cache` rebuild of the unchanged merge base `bd0fee3` gives image `fadb251aa391…`, while the cached
  build gives `2c145caf01a1…`. Uncached builds never repeat an image ID, because uv writes install timestamps and pyc
  headers.
- **"fails with a message pointing at the archive command"** for a dirty checkout needs the check to run before the
  existing non-Python guard (`Containerfile:14-15`), or that guard's message to change. Either one changes an
  instruction before `uv sync`, so the venv layer is rebuilt and the warm-cache ID changes.

**This plan (option A, recommended):** check first, and delete the non-Python guard. Every checkout is refused before
anything from `src/` is copied into a layer, and every refusal names the archive command. "Same image ID as before" is
read as **the same image content**. Measured: the image of this variant (`9bc08bfa1d1d…`) and the merge base's image
(`2c145caf01a1…`) have the same 24 112 `/app/.venv` entries. Every non-pyc file and every pyc body (bytes 16 onward)
hash identically. The only differences are build timestamps: `knowledge_base-0.1.0.dist-info/uv_cache.json`, that
file's line in `RECORD`, and the 16-byte pyc headers, which hold the source mtime. The image config is identical. A
warm-cache rebuild of one commit still repeats its image ID.

**Option B (rejected unless Michaël picks it):** append the check after `uv sync` and keep the guard unchanged. Measured:
it repeats the warm-cache ID `2c145caf01a1…` exactly. But a dirty checkout with a non-Python file under `src/` is
refused by the old guard's message, which does not name the archive command. A dirty `.py` also reaches the builder's
cached `COPY src` and `uv sync` layers in local storage before the refusal. It never reaches the runtime image, but
buildah can export those intermediate layers as cache. Picking B means a successor plan.

## Design decisions (measured in a scratch clone of merge base `bd0fee3`)

- **Discriminator: an `export-subst` marker.** The build context has no `.git`, so the build cannot tell tracked files
  from untracked ones, but it can tell an archive from a checkout. A new tracked file `.archive-commit` holds
  `$Format:%H$`, and `.gitattributes` marks it `export-subst`. `git archive` reads that attribute from the tree it
  archives and writes the full commit id. A checkout, clean or dirty, keeps the placeholder. Measured through an
  annotated tag: the archived file holds the tag's commit id.
- **What it guarantees, and what it does not.** It refuses an ordinary checkout build whose tracked marker is intact.
  It is not provenance:
  - Editing the marker to a commit id, deleting it, or modifying an extracted archive are deliberate bypasses. The
    person building owns both the tree and the image.
  - Any commit's archive passes, not only a tag's, as `git archive $(git stash create)` should. "Of a tag" stays the
    documented practice, not a check.
  - A raw tree id expands nothing, so its archive is refused.
  - A checkout without the marker (a tag older than this change, or a deleted file) fails at `COPY .archive-commit` with
    podman's "no such file" error, not with the message. A message cannot be attached to a failed `COPY`.
- **The allowlist is kept.** `.containerignore` gains `!.archive-commit` and nothing else. Podman unpacks the whole
  archive, which is the tag's public tracked tree, into a temporary context directory. `.containerignore` then limits
  what any `COPY` can read. Measured with a probe Containerfile (`COPY . /ctx`): the copyable context outside `src/`
  is exactly `.archive-commit`, `.python-version`, the two licences, `pyproject.toml` and `uv.lock`. That is the
  #575 concern: 32ec3fb export-ignored a `*` ignore file, so every tracked file was copyable. A restricted `git archive
  <tag> <paths>` was not adopted. It would repeat the allowlist inside the documented command, and the acceptance names
  `.containerignore` as the filter.
- **The check runs first in the build stage,** right after `WORKDIR /app`, before any project file is copied.
- **The non-Python guard is deleted.** It can no longer fire: every checkout is refused before `COPY src`, and an
  archive holds only tracked files. It would also refuse a future tracked non-Python package file such as `py.typed`.
- **Cache safety.** The marker's `COPY` layer is keyed on its content. A checkout's placeholder never matches an
  archive's cached `COPY` layer, so a checkout can never reuse an archive's successful check. A failed `RUN` is never
  cached.

## Implementation sequence

- [ ] **1. Refuse every build context but a git archive.**
  - Files: `.archive-commit` (new), `.gitattributes` (new), `.containerignore`, `Containerfile`.
  - Red: in a scratch clone at `bd0fee3`, `podman build .` exits 0. It must be refused.
  - Change:
    - `.archive-commit` is the single line `$Format:%H$`.
    - `.gitattributes` is `.archive-commit export-subst`.
    - `.containerignore` adds `!.archive-commit`. Its header comment says the marker is how the build tells an archive
      from a checkout.
    - `Containerfile`:
      - Right after `WORKDIR /app`, add `COPY .archive-commit ./`, then
        `RUN grep -Eqx '[0-9a-f]{40}|[0-9a-f]{64}' .archive-commit || { printf 'refusing to build from a working tree: git archive --format=tar <tag> | podman build --pull=newer -t localhost/knowledge-base:<tag> -\n' >&2; exit 1; }`.
        Put a two-line why comment before them: git archive writes the commit into the marker and a checkout keeps the
        placeholder; the check runs before any source is copied.
      - Delete the non-Python guard and its comment (lines 12-15).
      - Header lines 2-3 keep the archive command and drop "a fresh clone's podman build . also works".
  - Green, in a scratch clone of the branch commit, through a scratch annotated tag, with fixed bases and no `--pull=newer`:
    - `podman build .` exits non-zero and prints the refusing line, at the check step, from each of:
      - a clean checkout;
      - an untracked `src/knowledge_base/local_settings.py`;
      - a git-ignored `src/knowledge_base/tmp/scratch.py`;
      - an untracked non-Python `src/knowledge_base/credentials.json`.
    - `git archive --format=tar <tag> | podman build -t <scratch> -` exits 0.
      - `python -c "import knowledge_base.server"` exits 0 in the image.
      - Rebuilding the same archive repeats the image ID, with every step a cache hit.
    - The venv comparison against the merge base's archive image gives the result above: identical apart from
      `uv_cache.json`, its `RECORD` line and the pyc headers. The image configs are identical.
    - A probe Containerfile on the same tree shows the copyable context outside `src/` is the allowlist plus `.archive-commit`.
    - The evidence records the full image IDs, the podman and buildah versions, and both base digests.
  - Commit: `build(container): refuse every build context but a git archive`.
- [ ] **2. Document only the archive build.**
  - Files: `docs/getting-started/installation.md` (the "Container image" section, its first three paragraphs).
  - Red: N/A, docs only. The current text says any release containing the `Containerfile` will do. It recommends the
    archive and also documents `podman build .` from a fresh clone.
  - Change: keep the archive command block.
    - The tag must contain this change: "the first release after archive-only builds landed, or any later one".
    - Replace "`podman build .` from a fresh clone of the tag works too. Prefer the archive: ..." with a paragraph that
      says any other context, a checkout's `podman build .` included, clean or dirty, is refused and names the archive
      command. An older tag's own `Containerfile` predates the check, which is why the tag must contain this change.
    - The paragraph explains why: `git archive` writes the commit into `.archive-commit` (`export-subst`), and a
      checkout keeps the placeholder. It also says the check stops ordinary mistakes, not a deliberately edited marker.
    - It says podman unpacks the tag's tracked files and `.containerignore` limits what the build can copy to
      `pyproject.toml`, `uv.lock`, `.python-version`, the licences, `src/` and the marker.
    - The later paragraphs (lock, UID, volumes, snapshot) are unchanged.
  - Green: `grep -n 'podman build \.' docs/getting-started/installation.md` finds only the refusal sentence, and
    `grep -rn 'fresh clone' docs/ Containerfile` finds nothing.
  - Commit: `docs(install): document the git archive build as the only one`.

## Documentation

Item 2 rewrites the build paragraphs of `docs/getting-started/installation.md`. Item 1 updates the `Containerfile`
header and comments and the `.containerignore` comment. No other file names the build: `grep -rn 'podman build'`
finds only these.

## Testing

N/A for pytest. The behaviour under change is podman's build of the repository's own context, and a pytest of it needs
podman and a full image build. That would be a `slow` test the merge gate (`-m "not slow"`) never runs. The regressions
that matter fail closed and loudly at the next archive build: a dropped `export-subst`, a dropped allowlist line, or a
renamed marker. The one silent regression is a deleted check `RUN`, and review of that diff catches it. The podman
matrix in item 1 is the test. It runs on the final branch commit, and its outputs go in the PR test plan: exit codes,
refusal lines, image IDs, the venv comparison and the context listing.

## Verification

At the final clean HEAD, unpiped, in the worktree:

- `uv lock --check`
- `ruff check src/ tests/`
- `ruff format --check src/ tests/`
- `uv run pytest tests/ -q -m "not slow"`
- `uv sync --all-groups` then `uv run basedpyright src/ tests/`
- the item 1 podman matrix on a scratch clone of that HEAD (scratch tags and images only, removed afterwards).

## Resolution

Lean review: Codex (`gpt-6.1-sol`, headless), 0 BLOCKER, 1 HIGH, 4 MEDIUM, 1 LOW. Planning-review severity Default:
every finding is resolved below.

- **F1 HIGH (a hash-shaped marker is not provenance).** Accepted. "Design decisions" now states what the check
  guarantees: it refuses an ordinary checkout with the tracked marker intact. Edited markers, deleted markers, modified
  extracted archives and untagged commit archives are listed with their outcomes. The false "an edited file is refused"
  claim is gone, and item 2's docs say the check stops mistakes, not a deliberate edit. A trusted build script that
  resolves the tag and builds the archive itself was not adopted. The person building owns the tree, the script would
  be as bypassable as the marker, and the acceptance names `git archive <tag> | podman build -` as the entry point. The
  bypass cases are documented rather than added to the matrix: they would only demonstrate the stated limit.
- **F2 MEDIUM (a dirty non-Python checkout never prints the archive command; a missing marker gives no message).**
  Resolved by option A. The check moves before any source `COPY`, and the guard is deleted, so every checkout refusal is
  the archive message. The matrix gains the untracked non-Python case. The missing-marker case is documented as
  podman's own `COPY` error, because a failed `COPY` cannot carry a message. This trades the warm-cache image ID, which
  is now the "Decision for Michaël" section.
- **F3 MEDIUM (dirty sources reach intermediate images).** Resolved by option A. Under the early check, a refused
  checkout has committed only the base layers and the placeholder marker. No `src/` file reaches any layer. Option B's
  section now states its intermediate-layer exposure exactly.
- **F4 MEDIUM (the image-ID argument proves cache reuse only).** Accepted and measured. The uncached control
  (`fadb251aa391…` vs `2c145caf01a1…` for identical inputs) shows image IDs repeat only with a warm cache. The early
  check was then compared by content (venv hashes, pyc bodies, config), not by ID. The full IDs, versions and base
  digests go in the evidence (item 1).
- **F5 MEDIUM (COPY filtering versus what reaches the builder).** Accepted as wording. The plan and item 2's docs say
  podman unpacks the whole public tracked tree, and `.containerignore` limits what the build can copy. A restricted
  archive was not adopted, for the reason in "Design decisions".
- **F6 LOW (release prerequisite).** Fixed in item 2: the tag must contain the archive-only change, because an older
  tag's own `Containerfile` predates the check and still builds from a checkout.
- **Note (a cached successful check).** The wording is now "a checkout can never reuse an archive's successful check",
  not "the check always runs".
