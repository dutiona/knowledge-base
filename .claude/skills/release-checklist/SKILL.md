---
name: release-checklist
description: "Use when releasing knowledge-base to its durable home — promoting a built pinned runtime into ~/.local/opt/knowledge-base/{data,venv} that the harness consumes (never the in-repo dev `uv run`). Runs an execute-with-gates procedure: build the wheel, run the quality gate (pytest/ruff), back up the live DB, migrate + verify the schema version, then atomically promote the venv and write a RELEASE manifest — STOP on any failure, explicit go before each irreversible step (DB migrate, promote). Triggers on /release-checklist, 'release knowledge-base', 'promote knowledge-base to ~/.local/opt', 'cut a knowledge-base release'."
---

# /release-checklist — knowledge-base durable release

Promote a built `knowledge-base` runtime into its **durable home**
`~/.local/opt/knowledge-base/{data,venv}` — the artifacts the harness consumes. The harness must
**never** run the in-repo dev `uv run`; this skill installs a verified pinned runtime, with
**database and code migrated together** and the migration ordered **before** the new runtime is
served.

This is an **execute-with-gates** procedure. Run each step in order; **STOP and report on the first
failure**; require an explicit user **"go"** before every IRREVERSIBLE step (the DB **migrate** in
Step 4, the **promote** in Step 5). CI does not gate this repo — **this checklist is the gate.**

> **Ordering is load-bearing.** Migrate the live DB **before** flipping the `venv` symlink, so the
> harness never starts the new runtime against an un-migrated DB. If the migration fails, restore the
> backup and **abort** — the old `venv` keeps serving the old (intact) DB.

**Conventions used below**

```bash
OPT=~/.local/opt/knowledge-base
DB="$OPT/data/knowledge.db"           # the live DB the harness uses (env KNOWLEDGE_BASE_DB)
BACKUPS="$OPT/data/backups"
REPO=~/dev/knowledge-base             # the source checkout (build here, never consume `uv run`)
TS=$(date -u +%Y%m%dT%H%M%SZ)
```

## Step 1 — Build the pinned runtime (a wheel)

```bash
cd "$REPO"
uv build                              # → dist/*.whl (+ sdist)
```

**STOP** on any build error. The wheel is **staged**, not yet promoted.

## Step 2 — Quality gate — GATE (all must pass; blocks promote)

Run in order; **STOP on the first failure** — a failure here means **do not release**:

```bash
uv run pytest                         # LLM/embedding-dependent tests need Ollama up
uv run ruff format --check .
uv run ruff check .
```

Version-bump check: confirm `version` in `pyproject.toml` was bumped for this release (SemVer).
Any gate failure → STOP; fix on a feature branch → PR → restart from Step 1.

## Step 3 — Back up the live DB (before any mutation)

```bash
mkdir -p "$BACKUPS"
[ -f "$DB" ] && cp -- "$DB" "$BACKUPS/knowledge.$TS.pre-release.db" && echo "backed up → $BACKUPS/knowledge.$TS.pre-release.db"
```

knowledge-base's `migrate` also self-backs-up (VACUUM INTO) before mutating, but the gate takes its
**own** copy first so restore-on-fail never depends on the very operation that just failed. If `$DB`
does not exist (first release), there is nothing to back up — skip.

## Step 4 — Migrate + verify — IRREVERSIBLE (requires explicit "go")

> **STOP. Ask the user to confirm "go" before migrating the live DB.**

```bash
cd "$REPO"
# Dry-run first: report what would change without mutating (exit non-zero unless current).
uv run knowledge-base-ingest --db "$DB" migrate --check; echo "check exit=$?"
# On "go": apply. A fresh DB is initialized; an existing DB is backed up + migrated.
uv run knowledge-base-ingest --db "$DB" migrate
# Verify: schema reports the current version (exit 0 = matched; non-zero = mismatch).
uv run knowledge-base-ingest --db "$DB" schema; echo "schema exit=$?"
# Smoke the DB opens + reports.
uv run knowledge-base-ingest --db "$DB" status >/dev/null; echo "status exit=$?"
```

**On migration OR verify failure → restore the backup and ABORT (do not promote):**

```bash
cp -- "$BACKUPS/knowledge.$TS.pre-release.db" "$DB"   # restore the pre-release copy
# leave the OLD venv symlink in place — it serves the restored (old, intact) DB.
```

Only a `schema` exit code of `0` clears this gate.

## Step 5 — Promote the runtime atomically — IRREVERSIBLE (requires explicit "go")

> **STOP. Ask the user to confirm "go" before promoting.** The DB is already migrated (Step 4).

```bash
# Install the built wheel into a fresh, versioned venv (no in-place mutation of the live one).
INSTALL="$OPT/releases/$TS"
mkdir -p "$INSTALL"
uv venv "$INSTALL/venv"
uv pip install --python "$INSTALL/venv/bin/python" "$(ls -t "$REPO"/dist/*.whl | head -1)"
# Atomic flip: the harness uses $OPT/venv/bin/...; a symlink swap is atomic (a dir mv is not).
ln -sfn "$INSTALL/venv" "$OPT/venv"
# Write the RELEASE manifest (provenance for "what is installed").
cat > "$OPT/RELEASE" <<MANIFEST
version: $(grep -m1 '^version' "$REPO/pyproject.toml" | cut -d'"' -f2)
git_sha: $(git -C "$REPO" rev-parse HEAD)
schema_version: $(uv run --project "$REPO" knowledge-base-ingest --db "$DB" schema 2>/dev/null | grep -oE '"schema_version"[: ]+[0-9]+' | grep -oE '[0-9]+' | head -1)
released_at: $TS
MANIFEST
# Smoke the PROMOTED runtime against the live DB.
"$OPT/venv/bin/knowledge-base-ingest" --db "$DB" status >/dev/null; echo "smoke exit=$?"
```

A non-zero smoke exit after promote is a **release failure** — investigate before declaring done.

## Done

Report: built version, git sha, schema_version, backup path, and the smoke result. Keep the old
`~/.local/share/knowledge-base` DB (if any) until the new `~/.local/opt` install is verified in live
use. The `RELEASE` manifest at `$OPT/RELEASE` records what is installed; old releases stay under
`$OPT/releases/` for rollback (re-point the `venv` symlink).

## Failure-mode summary

| Failure | Action |
|---|---|
| Build (Step 1) / quality gate (Step 2) | STOP — do not release; fix → PR → restart |
| Migration or verify (Step 4) | restore backup → ABORT; old `venv` keeps serving the old DB |
| Promote smoke (Step 5) | release failure — the DB is migrated but the new runtime is unhealthy; investigate (re-point `venv` to the previous release to roll back) |
