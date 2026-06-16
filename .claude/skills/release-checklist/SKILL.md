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

## Preconditions — release offline

Run this checklist with **no live writer holding the DB**: stop the harness and any running
knowledge-base MCP server first. This is load-bearing, not advisory:

- `migrate` runs `BEGIN EXCLUSIVE`; a running MCP server holding the lock makes it fail `database is
  locked`.
- The Step-3 `cp` backup of a WAL-mode DB is only consistent when no writer is mid-transaction.
- Between Step 4 (migrate) and Step 5 (promote) the on-disk DB is at the **new** schema while the
  **old** `venv` would reject it — keep that window offline and brief.

Confirm nothing holds the DB before starting:

```bash
DB=~/.local/opt/knowledge-base/data/knowledge.db
{ command -v fuser >/dev/null && fuser "$DB" 2>/dev/null && { echo "STOP: a process still has the DB open"; exit 1; }; } || echo "DB is free (or absent — first release)"
```

## Step 0 — Establish the release environment (run once; every later step sources it)

Each step runs in its **own** shell — and the gated STOPs mean the steps cannot share one shell — so
shell variables do **not** persist between them. A re-evaluated `$TS` would also drift between backup
and restore. Freeze the config (one stable `$TS`) into a sourceable env file now; every later step
begins by sourcing it.

```bash
OPT=~/.local/opt/knowledge-base
REPO=$(git -C ~/dev/knowledge-base rev-parse --show-toplevel 2>/dev/null || echo ~/dev/knowledge-base)
TS=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$OPT/data/backups"
cat > "$OPT/.release-env" <<EOF
OPT=$OPT
REPO=$REPO
DB=$OPT/data/knowledge.db
BACKUPS=$OPT/data/backups
TS=$TS
BACKUP=$OPT/data/backups/knowledge.$TS.pre-release.db
EOF
cat "$OPT/.release-env"
```

## Step 1 — Build the pinned runtime (a wheel)

```bash
source ~/.local/opt/knowledge-base/.release-env
cd "$REPO"
rm -rf dist/                          # so the Step-5 `ls -t dist/*.whl` can only pick THIS build
uv build                              # → dist/*.whl (+ sdist)
```

**STOP** on any build error. The wheel is **staged**, not yet promoted.

## Step 2 — Quality gate — GATE (all must pass; blocks promote)

Run in order; **STOP on the first failure** — a failure here means **do not release**:

```bash
source ~/.local/opt/knowledge-base/.release-env
cd "$REPO"
git diff-index --quiet HEAD -- || { echo "STOP: uncommitted changes — commit/stash so the manifest git_sha matches what is built"; exit 1; }
uv run pytest                         # LLM/embedding-dependent tests need Ollama up
uv run ruff format --check .
uv run ruff check .
```

Version-bump check: confirm `version` in `pyproject.toml` was bumped for this release (SemVer).
Any gate failure → STOP; fix on a feature branch → PR → restart from Step 1.

## Step 3 — Back up the live DB (before any mutation)

```bash
source ~/.local/opt/knowledge-base/.release-env
if [ -f "$DB" ]; then
  cp -- "$DB" "$BACKUP" && echo "backed up → $BACKUP"
else
  echo "first release: no live DB at $DB — nothing to back up (migrate bootstraps it)"
fi
```

knowledge-base's `migrate` also self-backs-up (VACUUM INTO) before mutating an existing DB, but the
gate takes its **own** independent `cp` copy first so restore-on-fail never depends on the very
operation that just failed. With the harness stopped (Preconditions) the DB is quiescent, so the
plain `cp` is consistent.

## Step 4 — Migrate + verify — IRREVERSIBLE (requires explicit "go")

> **STOP. Ask the user to confirm "go" before migrating the live DB.**

```bash
source ~/.local/opt/knowledge-base/.release-env
cd "$REPO"
# Dry-run report. `migrate --check` exits non-zero when migrations are pending OR the DB is absent —
# a STATUS signal, NOT a gate failure — so show it but never abort on its exit.
uv run knowledge-base-ingest --db "$DB" migrate --check || true
# On "go": apply. An existing DB is backed up (VACUUM INTO) + migrated transactionally with
# restore-on-fail; a fresh/absent DB is bootstrapped to the current schema. A genuine failure exits
# non-zero here.
uv run knowledge-base-ingest --db "$DB" migrate || NEED_RESTORE=1
if [ -z "$NEED_RESTORE" ]; then
  # GATE: schema must report current (exit 0); then a status smoke must open cleanly.
  uv run knowledge-base-ingest --db "$DB" schema || NEED_RESTORE=1
fi
if [ -z "$NEED_RESTORE" ]; then
  uv run knowledge-base-ingest --db "$DB" status >/dev/null || NEED_RESTORE=1
fi
if [ -n "$NEED_RESTORE" ]; then
  if [ -f "$BACKUP" ]; then
    echo "migrate/verify FAILED — restoring the pre-release backup and ABORTING (do not promote)"
    # Drop stale WAL sidecars first, or SQLite replays the failed migration's frames onto the
    # restored file → silent corruption. Restore via a temp file so a short write can't truncate $DB.
    cp -- "$BACKUP" "$DB.restoring"
    rm -f -- "$DB-wal" "$DB-shm" "$DB-journal"
    mv -f -- "$DB.restoring" "$DB"
    echo "restored from $BACKUP — the old venv keeps serving this DB"
  else
    echo "first release failed before any backup existed — removing the half-created DB"
    rm -f -- "$DB" "$DB-wal" "$DB-shm" "$DB-journal"
  fi
  exit 1
fi
echo "migrated + verified"
```

Only a clean `schema` exit `0` clears this gate.

## Step 5 — Promote the runtime atomically — IRREVERSIBLE (requires explicit "go")

> **STOP. Ask the user to confirm "go" before promoting.** The DB is already migrated (Step 4).

```bash
source ~/.local/opt/knowledge-base/.release-env
# Install the built wheel into a fresh, versioned venv (no in-place mutation of the live one).
INSTALL="$OPT/releases/$TS"
mkdir -p "$INSTALL"
uv venv "$INSTALL/venv"
WHEEL=$(ls -t "$REPO"/dist/*.whl | head -1)   # dist/ was cleaned in Step 1 → this is the build above
uv pip install --python "$INSTALL/venv/bin/python" "$WHEEL"
# Atomic flip: `ln -sfn` is unlink()+symlink() (non-atomic — an ENOENT window). Create a temp link,
# then `mv -Tf` it over $OPT/venv (a single atomic rename(2)).
ln -sfn "$INSTALL/venv" "$OPT/venv.tmp"
mv -Tf "$OPT/venv.tmp" "$OPT/venv"
# Manifest schema_version: parse the JSON `schema` output with the venv's own Python (robust to
# null / key ordering — no grep). `schema` prints its JSON even when it exits non-zero.
SCHEMA_VER=$("$OPT/venv/bin/knowledge-base-ingest" --db "$DB" schema 2>/dev/null | "$OPT/venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin).get("schema_version"))' 2>/dev/null)
cat > "$OPT/RELEASE" <<MANIFEST
version: $(grep -m1 '^version' "$REPO/pyproject.toml" | cut -d'"' -f2)
git_sha: $(git -C "$REPO" rev-parse HEAD)
schema_version: ${SCHEMA_VER:-unknown}
released_at: $TS
MANIFEST
cat "$OPT/RELEASE"
# Smoke the PROMOTED runtime against the live DB.
"$OPT/venv/bin/knowledge-base-ingest" --db "$DB" status >/dev/null || { echo "SMOKE FAILED"; exit 1; }
echo "promoted OK"
```

A non-zero smoke exit after promote is a **release failure** — investigate before declaring done.

## Done

Report: built version, git sha, schema_version, backup path, and the smoke result. Keep the old
`~/.local/share/knowledge-base` DB (if any) until the new `~/.local/opt` install is verified in live
use. The `RELEASE` manifest at `$OPT/RELEASE` records what is installed; old releases stay under
`$OPT/releases/` for rollback (re-point the `venv` symlink with `ln -sfn … "$OPT/venv.tmp" && mv -Tf
"$OPT/venv.tmp" "$OPT/venv"`).

## Failure-mode summary

| Failure | Action |
|---|---|
| DB still open (Preconditions) | STOP — stop the harness/MCP; `migrate`'s `BEGIN EXCLUSIVE` would fail locked, and a hot backup is unsafe |
| Build (Step 1) / quality gate (Step 2) | STOP — do not release; fix → PR → restart |
| Migration or verify (Step 4) | sidecar-aware restore from `$BACKUP` (or rm the half-created DB on first release) → ABORT; old `venv` keeps serving the old DB |
| Promote smoke (Step 5) | release failure — the DB is migrated but the new runtime is unhealthy; investigate (re-point `venv` to the previous release to roll back) |
