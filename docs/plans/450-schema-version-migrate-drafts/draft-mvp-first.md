# Implementation Plan — #450 schema-version + backup-before-migrate (MVP-FIRST lens)

**Lens**: smallest correct increment. Ship the thin vertical slice that satisfies all three acceptance
criteria, defer everything not load-bearing, and reuse the memory-engine (ME) shape verbatim so future
parity work is mechanical.

**Repo**: `knowledge-base` (Python). **Worktree** (already created):
`/home/mroynard/dev/knowledge-base/.worktrees/450-schema-version-migrate`.

---

## BLUF

The smallest correct slice is **~70 lines of new code in `db.py` + ~60 lines of two CLI handlers in
`indexer.py`**, plus tests. Concretely:

1. **`CURRENT_SCHEMA_VERSION = 1`** constant + a `schema_version` row written into the **existing
   `config` table** (db.py:629) — no new table (design (a), ratified).
2. **`init_schema` stamps the version** on a fresh DB and is otherwise unchanged: the ~11 idempotent
   `_migrate_*` builds (db.py:912-927) STAY as the v1 baseline (design (b), ratified). This ships the
   machinery, not a re-versioning of history.
3. **`migrate(conn, *, backup_dir, dry_run=False)`** in db.py: reads live version, no-ops when current,
   refuses a newer-than-code DB, and for a real upgrade takes a **`VACUUM INTO` timestamped backup
   first** (design (c)), then runs version-gated steps inside a transaction, **restoring the backup +
   aborting on failure**.
4. **`migrate` (+ `--check`) and `schema` subcommands** on `knowledge-base-ingest` (design (d)),
   mirroring ME's exit-code contract: mismatch / pending / newer → non-zero; up-to-date / applied → 0.

Because `CURRENT_SCHEMA_VERSION = 1`, the v1→vN migration ladder is **empty today**. That is the MVP's
entire point: the machinery is exercised by tests against a _simulated_ v2, but ships with zero real
migrations to run. The first real schema change (a future PR) adds the v2 entry and proves the path on
live data.

---

## What "smallest correct slice" excludes (honest deferrals)

Each of these is recommended for a follow-up, not silently dropped. None is required by the three
acceptance criteria.

| Deferred                                                                                                                  | Why it is safe to defer                                                                                                                                                                            | Where it lands                                                                                                                             |
| ------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| **`STORAGE_EPOCH`** (ME schema.rs:16) — coarse breaking-change gate.                                                      | KB has exactly one epoch and no history of breaking it. A second `config` key with no second value adds surface with zero behavior.                                                                | Add alongside the _first_ time KB needs a non-migratable break.                                                                            |
| **Retiring the unconditional `_migrate_*` calls** in `init_schema` (db.py:912-927) and folding them under version gating. | They are idempotent and already correct; re-versioning them is the explicit non-goal of this issue (design (b)). Touching them risks regressions in the live DB for zero acceptance-criteria gain. | A later "v1 baseline consolidation" PR, if ever wanted.                                                                                    |
| **A backup-retention / pruning policy** (cap N backups, GC old ones).                                                     | One backup per migrate is the safety contract; unbounded growth is a hygiene issue, not a correctness one, and migrations are rare.                                                                | Follow-up once migrations actually run in the field.                                                                                       |
| **JSON/table/plain output-format switch** (ME has three via `OutputFormat`).                                              | KB's CLI already standardizes on `json.dumps(indent=2)` via `_print` (indexer.py:57). One format = the existing house style.                                                                       | Only if an operator asks for a plain/grep format.                                                                                          |
| **A standalone `kb-migrate` binary / MCP tool surface.**                                                                  | The ingest CLI is the established operator entry point (`pyproject.toml:25`); two subcommands reuse all its plumbing.                                                                              | N/A unless a non-CLI caller needs it.                                                                                                      |
| **`foreign_keys` OFF/ON dance + `PRAGMA foreign_key_check`** (ME schema.rs:241-266).                                      | Needed only by table-rebuild migrations. The v1→vN ladder is empty, so no rebuild migration exists yet. Adding the harness now is dead code.                                                       | Add with the first table-rebuild migration that needs it (the `_migrate_*` rebuilds already inline their own `PRAGMA foreign_keys = OFF`). |

The deferrals are the lens working: the issue asks for the **framework + baseline v1**, and the
framework is fully proven by a simulated-v2 test without any real v2 in tree.

---

## Pre-reading (already done; cited inline below)

- `src/knowledge_base/db.py` — `init_schema` (db.py:626), `config` table create (db.py:629),
  the seed-config block (db.py:634-646), the trailing `_migrate_*` call list (db.py:912-927),
  `get_connection` (db.py:179, WAL at db.py:191), `resolve_db_path` (db.py:45), `DEFAULT_DB_PATH`
  (db.py:38), `__all__` (db.py:17).
- `src/knowledge_base/indexer.py` — `_get_conn` (indexer.py:29), `build_parser` (indexer.py:202),
  the `sub.add_parser` block (indexer.py:228-259), the `dispatch` map (indexer.py:287-293),
  `_print`/`_print_error` (indexer.py:57-64), `main` exception funnel (indexer.py:295-299).
- `src/knowledge_base/_conn.py` — MCP server connection path: calls `init_schema` once under a lock
  (\_conn.py:22). **Constraint: stamping the version inside `init_schema` must remain idempotent and
  cheap**, because every server boot hits it.
- `tests/test_db.py` — fixture style: `get_connection(tmp_path / "test.db")` then `init_schema(conn)`;
  the "build an OLD schema by hand, re-run `init_schema`, assert migration" pattern
  (test_db.py:283-326, 411-464). This is the template for the simulated-v2 migrate test.
- `tests/test_indexer.py` — `_make_args` namespace builder (test_indexer.py:74), `build_parser`
  subcommand-coverage test (test_indexer.py:87), `main([...])` end-to-end (test_indexer.py:392).
- ME parity (read-only reference, different repo):
  - `src/store/schema.rs` — `CURRENT_SCHEMA_VERSION` (schema.rs:8), `init_schema` stamping on fresh DB
    (schema.rs:96-114), `migrate(conn, backup_dir)` (schema.rs:200-277), `validate_schema_version`
    (schema.rs:290-344), `backup_before_migration` via `VACUUM INTO` with null-byte guard
    (schema.rs:364-413).
  - `memory-engine-cli/src/commands/migrate.rs` — `--check` dry-run, `MigrateReport`, exit-code contract
    (migrate.rs:46-123): newer→1, pending under `--check`→1, applied/up-to-date→0.
  - `memory-engine-cli/src/commands/schema.rs` — release-gate verify hook, mismatch→1 (schema.rs:23-55).
  - `memory-engine-cli/src/db.rs` — `peek_schema_version_from_db` read-only peek (db.rs:42-66),
    `open_engine_writable` sets `backup_dir` next to the DB (db.rs:84-94).

---

## Design decisions (ratify / refine through the MVP lens)

- **(a) Store in `config`** — RATIFIED. The table already exists (db.py:629) and is already the home of
  every scalar setting (db.py:886-902). Key `schema_version`, value `str(int)`. Matches ME's
  `set_config(conn, "schema_version", …)` exactly.
- **(b) Framework + baseline v1** — RATIFIED. `CURRENT_SCHEMA_VERSION = 1`. The existing `_migrate_*`
  builds stay where they are and keep running unconditionally; they _are_ v1. The migration **ladder**
  this PR introduces starts empty.
- **(c) `VACUUM INTO data/backups/<timestamp>.db`** — REFINED on two points:
  1. **Location.** ME writes the backup _next to the database_ (db.rs:88, `path.parent()`). KB's
     `DEFAULT_DB_PATH` is `~/.local/share/knowledge-base/knowledge.db` (db.py:38), so the parity-correct
     and least-surprising location is **`<db_parent>/backups/`**, not a CWD-relative `data/backups/`. A
     CWD-relative path breaks the moment the CLI runs from a cron dir. I take the DB's own parent and
     append `backups/`. (If the issue's literal `data/backups/` is mandated, it becomes a one-line
     constant; I flag the divergence rather than silently following CWD.)
  2. **Filename.** `<db_stem>.v<from>-<timestamp>.db`, e.g. `knowledge.v1-20260616T141233Z.db`. The
     `v<from>` records the pre-migration version (ME uses `.v{current_version}.bak`, schema.rs:382);
     the timestamp satisfies the "timestamped" criterion and avoids clobbering on re-run.
- **(d) `migrate` (+`--check`) and `schema` subcommands** — RATIFIED, on `knowledge-base-ingest`.

**Restore-on-fail (the one place I diverge from ME's mechanism, deliberately).** ME relies on each
migration being a single transaction (`tx.commit()` / rollback, schema.rs:247-260): on failure the
_data_ is already rolled back, and the backup is a belt-and-suspenders artifact it does **not** restore.
The issue for KB explicitly says "on failure RESTORES the backup + aborts." For the MVP I honor the
**literal acceptance criterion**: wrap the whole migrate in try/except; on any exception after the backup
exists, **close the connection, copy the backup file back over the live DB (and drop `-wal`/`-shm`
sidecars), then re-raise**. This is simpler to reason about than transaction-scoping (and correct even
for a hypothetical multi-statement migration that a naive author forgets to wrap), at the cost of
reopening the connection. Given migrations are rare and operator-invoked, that cost is irrelevant.

---

## File-by-file changes

### 1. `src/knowledge_base/db.py`

**1a. Constant** (near `DEFAULT_EMBED_*`, after db.py:66):

```python
#: Current on-disk schema version. Bump (and add a step to ``_SCHEMA_MIGRATIONS``)
#: whenever a schema change must be version-gated. v1 == the baseline schema that
#: ``init_schema`` builds via the idempotent ``_migrate_*`` functions; this issue
#: ships the machinery, not a re-versioning of that history.
CURRENT_SCHEMA_VERSION = 1
```

**1b. Stamp the version in `init_schema`.** `init_schema` must set `schema_version` on a fresh DB and
must be idempotent for existing DBs (the MCP server hits it on every boot, _conn.py:22). The seed block
at db.py:634-646 only inserts `embed_*`when`embed_model` is absent — that is the precise "is this a
fresh DB?" signal. I add the stamp to the SAME guarded block so it is written exactly once for fresh
DBs and is a no-op thereafter. For *existing\* DBs that predate this PR (have `embed_model` but no
`schema_version`), I backfill `schema_version = '1'` with `INSERT OR IGNORE` next to the other
`INSERT OR IGNORE` config seeds (db.py:886-902), so a legacy DB is correctly labeled v1 without a
migrate.

Edit inside the fresh-DB branch (db.py:637-646), append one row:

```python
        conn.executemany(
            "INSERT INTO config (key, value) VALUES (?, ?)",
            [
                ("embed_model", DEFAULT_EMBED_MODEL),
                ("embed_dim", str(DEFAULT_EMBED_DIM)),
                ("embed_provider", DEFAULT_EMBED_PROVIDER),
                ("schema_version", str(CURRENT_SCHEMA_VERSION)),   # <-- new
            ],
        )
```

And in the `INSERT OR IGNORE` seed run (after db.py:902), backfill legacy DBs:

```python
    conn.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('schema_version', ?)",
        (str(CURRENT_SCHEMA_VERSION),),
    )
```

Rationale for both: a fresh DB gets the stamp via the `executemany`; a pre-#450 DB (which skips the
fresh branch) gets it via `INSERT OR IGNORE`. Either way, after any `init_schema` the row exists and
equals `1`. This satisfies **AC-1 (fresh DB initializes at CURRENT_SCHEMA_VERSION)** and means
`schema`/`migrate` never face a missing-key edge on a KB-created DB.

**1c. Read helpers:**

```python
def get_schema_version(conn: sqlite3.Connection) -> int:
    """Return the DB's recorded schema version.

    Defaults to 1 when the row is absent (a DB created before this key existed
    is, by definition, the v1 baseline)."""
    row = conn.execute(
        "SELECT value FROM config WHERE key = 'schema_version'"
    ).fetchone()
    return int(row["value"]) if row and row["value"] is not None else 1


def peek_schema_version(db_path: Path) -> int:
    """Read schema_version from a DB file WITHOUT running init_schema/migrate.

    Read-only mirror of ME ``peek_schema_version_from_db`` (db.rs:42): opens the
    file read-only so the release-gate ``schema`` check never mutates or creates
    a database. Raises FileNotFoundError if the path is not a file, and
    KnowledgeBaseError if it has no config/schema_version (not a KB database)."""
    if not db_path.is_file():
        raise FileNotFoundError(f"database not found (or is a directory): {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT value FROM config WHERE key = 'schema_version'"
        ).fetchone()
    except sqlite3.OperationalError as exc:
        raise KnowledgeBaseError(
            f"{db_path} has no config table — is this a knowledge-base database? ({exc})"
        ) from exc
    finally:
        conn.close()
    if row is None:
        raise KnowledgeBaseError(
            f"{db_path} has no schema_version in config — not a knowledge-base database"
        )
    return int(row["value"])
```

`peek_schema_version` opens read-only via SQLite URI (`mode=ro`) — it must NOT call `get_connection`
(which would `mkdir` the parent and create the file, db.py:185-186) nor load sqlite-vec (unnecessary for
a config read). Import `KnowledgeBaseError` from `.exceptions` at module top.

**1d. Backup primitive** (parity with ME `backup_before_migration`, schema.rs:364):

```python
def backup_before_migrate(db_path: Path, from_version: int) -> Path:
    """Write a WAL-safe, timestamped copy of *db_path* via ``VACUUM INTO``.

    Returns the backup path. ``VACUUM INTO`` produces an atomic, defragmented
    standalone copy regardless of WAL state (no -wal/-shm to chase), so it is
    safe even though get_connection opens in WAL mode (db.py:191).
    """
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup_path = backup_dir / f"{db_path.stem}.v{from_version}-{ts}.db"

    # Defense-in-depth: reject a NUL before any filesystem/SQL use (mirrors ME
    # schema.rs:389). VACUUM INTO cannot parameterize its target, so the path is
    # interpolated; a NUL would truncate the C string the OS/SQLite receives.
    target = str(backup_path)
    if "\x00" in target:
        raise KnowledgeBaseError("backup path contains null byte")
    escaped = target.replace("'", "''")  # SQLite literal escaping: ' -> ''

    src = get_connection(db_path)
    try:
        src.execute(f"VACUUM INTO '{escaped}'")
    finally:
        src.close()
    return backup_path
```

`time` is already imported in indexer.py but NOT in db.py — add `import time` to db.py's imports
(db.py:5-8 block). `backup_dir` is derived from the live DB's own parent (parity with ME db.rs:88),
satisfying **AC-2 (timestamped backup before mutating)** and the `data/backups/` intent without a
CWD trap.

**1e. The migrate driver** (parity with ME `migrate`, schema.rs:200, but with literal restore-on-fail):

```python
#: Forward-only migration ladder. Index i upgrades (i+1) -> (i+2); i.e.
#: _SCHEMA_MIGRATIONS[0] is v1 -> v2. EMPTY at v1 — this issue ships the driver,
#: not any concrete migration. The first real schema change appends its function
#: here AND bumps CURRENT_SCHEMA_VERSION.
_SCHEMA_MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = []


def migrate(
    conn: sqlite3.Connection,
    *,
    backup: bool = True,
    dry_run: bool = False,
) -> dict:
    """Bring *conn*'s database up to CURRENT_SCHEMA_VERSION.

    Returns a report dict: {schema_version, current_schema_version, pending,
    migrated, checked, newer}. Mirrors ME MigrateReport (migrate.rs:20).

    - up-to-date  -> no-op, migrated=False
    - newer DB    -> migrated=False, newer=True (caller must refuse; we do NOT
                     mutate a DB written by newer code)
    - dry_run     -> reports `pending` without mutating
    - real run    -> VACUUM INTO backup (when backup=True and db is on disk),
                     then run each pending step; on ANY failure, restore the
                     backup file over the live DB and re-raise.
    """
    live = get_schema_version(conn)
    current = CURRENT_SCHEMA_VERSION
    newer = live > current
    pending = list(range(live + 1, current + 1)) if live < current else []

    report = {
        "schema_version": live,
        "current_schema_version": current,
        "pending": pending,
        "migrated": False,
        "checked": dry_run,
        "newer": newer,
    }
    if newer or dry_run or not pending:
        return report

    db_path = _connection_path(conn)  # None for :memory:
    backup_path = None
    if backup and db_path is not None:
        backup_path = backup_before_migrate(db_path, live)

    try:
        for step in pending:
            fn = _SCHEMA_MIGRATIONS[step - 2]  # step v2 -> index 0
            fn(conn)
            conn.execute(
                "UPDATE config SET value = ? WHERE key = 'schema_version'",
                (str(step),),
            )
            conn.commit()
    except Exception:
        conn.rollback()
        if backup_path is not None and db_path is not None:
            _restore_backup(conn, db_path, backup_path)
        raise

    report["migrated"] = True
    report["schema_version"] = current
    return report
```

Supporting helpers in db.py:

```python
def _connection_path(conn: sqlite3.Connection) -> Path | None:
    """The main DB file backing *conn*, or None for an in-memory DB."""
    for _seq, name, file in conn.execute("PRAGMA database_list").fetchall():
        if name == "main":
            return Path(file) if file else None
    return None


def _restore_backup(conn: sqlite3.Connection, db_path: Path, backup_path: Path) -> None:
    """Roll the live DB back to *backup_path*, dropping WAL sidecars.

    Closes *conn* first (an open WAL connection holds the -wal/-shm files), then
    overwrites the live DB with the backup and removes any stale sidecars so the
    next open sees a clean v(from) database."""
    conn.close()
    shutil.copyfile(backup_path, db_path)
    for sidecar in (db_path.with_name(db_path.name + "-wal"),
                    db_path.with_name(db_path.name + "-shm")):
        if sidecar.exists():
            sidecar.unlink()
```

Add `import shutil` and `from collections.abc import Callable` to db.py imports. `_connection_path`
reuses `PRAGMA database_list` (the same primitive ME uses, schema.rs:370) so the driver works for any
connection without the caller threading a path through. The `_SCHEMA_MIGRATIONS[step - 2]` index will
not execute at v1 (loop body unreached when `pending == []`), so the empty list is correct, not a bug.

This satisfies **AC-3 (version mismatch reportable; failed migrate restores the backup)** — the report
carries `schema_version != current_schema_version`, and the except-clause restores.

**1f. `validate_schema_version`** (release-gate verify, parity with ME schema.rs:290) — thin, since the
`schema` CLI command does the reporting; expose a boolean+ints for programmatic callers:

```python
def schema_status(conn: sqlite3.Connection) -> dict:
    """Read-only {schema_version, current_schema_version, matches} for the gate."""
    live = get_schema_version(conn)
    return {
        "schema_version": live,
        "current_schema_version": CURRENT_SCHEMA_VERSION,
        "matches": live == CURRENT_SCHEMA_VERSION,
    }
```

**1g. `__all__`** (db.py:17): add `"CURRENT_SCHEMA_VERSION"`, `"backup_before_migrate"`,
`"get_schema_version"`, `"migrate"`, `"peek_schema_version"`, `"schema_status"`.

### 2. `src/knowledge_base/indexer.py`

**2a. `cmd_schema`** — the release-gate verify hook (parity ME schema.rs). Read-only; uses
`peek_schema_version` so it never creates/mutates a DB and never needs sqlite-vec:

```python
def cmd_schema(args: argparse.Namespace) -> None:
    from .db import CURRENT_SCHEMA_VERSION, peek_schema_version

    live = peek_schema_version(args.db)
    matches = live == CURRENT_SCHEMA_VERSION
    _print(
        {
            "schema_version": live,
            "current_schema_version": CURRENT_SCHEMA_VERSION,
            "matches": matches,
        },
        quiet=args.quiet,
    )
    if not matches:
        sys.exit(1)
```

**2b. `cmd_migrate`** — apply or (with `--check`) report. Must NOT route through `_get_conn`
(indexer.py:29), because `_get_conn` calls `init_schema`, and on a pre-#450 DB that would _stamp it v1_
before we can observe the true state. Open via `get_connection` directly (no `init_schema`), then drive
`migrate`:

```python
def cmd_migrate(args: argparse.Namespace) -> None:
    from .db import CURRENT_SCHEMA_VERSION, get_connection, peek_schema_version

    # Peek first (read-only) so --check NEVER creates or mutates a DB and a
    # newer-than-code DB is refused before we touch it. Mirrors ME migrate.rs:47.
    live = peek_schema_version(args.db)
    if live > CURRENT_SCHEMA_VERSION:
        _print_error(
            {
                "error": (
                    f"database schema_version {live} is NEWER than this code "
                    f"(CURRENT_SCHEMA_VERSION {CURRENT_SCHEMA_VERSION}) — cannot migrate"
                ),
                "schema_version": live,
                "current_schema_version": CURRENT_SCHEMA_VERSION,
                "newer": True,
            }
        )
        sys.exit(1)

    conn = get_connection(args.db)   # NOTE: not _get_conn — no init_schema here
    try:
        report = migrate(conn, backup=True, dry_run=args.check)
    finally:
        # migrate() may already have closed conn during a restore; guard.
        try:
            conn.close()
        except Exception:
            pass
    _print(report, quiet=args.quiet)

    # Exit-code contract (ME migrate.rs:114): pending under --check -> 1;
    # applied or up-to-date -> 0.
    if report["pending"] and not report["migrated"]:
        sys.exit(1)
```

`migrate` is imported at module top (`from .db import ..., migrate`) alongside the existing
`get_connection, init_schema, resolve_db_path` (indexer.py:18).

**2c. Parser wiring** (indexer.py:228-259, add two parsers):

```python
    # -- migrate --------------------------------------------------------------
    p_migrate = sub.add_parser(
        "migrate", help="Migrate the DB to the current schema version (backs up first)."
    )
    p_migrate.add_argument(
        "--check",
        action="store_true",
        help="Dry-run: report pending migrations and exit non-zero if any; do not mutate.",
    )

    # -- schema ---------------------------------------------------------------
    sub.add_parser(
        "schema", help="Report live vs current schema version (exit non-zero on mismatch)."
    )
```

**2d. Dispatch** (indexer.py:287-293): add `"migrate": cmd_migrate, "schema": cmd_schema`.

**2e. Exception funnel** (indexer.py:297): widen the caught set to include `FileNotFoundError` so a
missing DB path from `peek_schema_version` reports as a clean `{"error": ...}` exit-1 rather than a
traceback:

```python
    except (KnowledgeBaseError, ValueError, FileNotFoundError) as exc:
        _print_error({"error": str(exc)})
        sys.exit(1)
```

(`SystemExit` raised by the handlers' own `sys.exit(1)` propagates untouched — it is not an `Exception`.)

---

## Backup / restore flow (the load-bearing sequence)

```mermaid
sequenceDiagram
    participant CLI as cmd_migrate
    participant DB as db.migrate
    participant FS as filesystem
    CLI->>DB: peek_schema_version(path)  (read-only)
    alt live > CURRENT
        CLI-->>CLI: error "newer", exit 1
    end
    CLI->>DB: get_connection(path)  (NO init_schema)
    CLI->>DB: migrate(conn, backup=True, dry_run=check)
    DB->>DB: live = get_schema_version(conn)
    alt up-to-date or --check or empty pending
        DB-->>CLI: report (migrated=False)
    else real upgrade
        DB->>FS: VACUUM INTO backups/<stem>.v<live>-<ts>.db
        loop each pending step
            DB->>DB: step(conn); UPDATE config schema_version; commit
        end
        alt any step raises
            DB->>DB: conn.rollback(); conn.close()
            DB->>FS: copyfile(backup -> live); rm -wal/-shm
            DB-->>CLI: re-raise  --> error exit 1, backup retained
        else all steps ok
            DB-->>CLI: report (migrated=True)
        end
    end
```

Key invariants:

- **Backup exists before any mutation.** `backup_before_migrate` runs before the loop. (AC-2)
- **Restore is byte-for-byte.** `shutil.copyfile` of the `VACUUM INTO` copy; sidecars dropped so the
  reopened DB is exactly v(from). (AC-3)
- **Empty ladder is a no-op.** At v1, `pending == []` → early return, no backup, no I/O. Zero behavior
  change for every existing caller (MCP server, all `cmd_*` via `_get_conn`).

---

## Documentation

- **Docstrings**: every new public function (`get_schema_version`, `peek_schema_version`,
  `backup_before_migrate`, `migrate`, `schema_status`) carries a docstring stating the contract and the
  ME parity point it mirrors (drafted above).
- **CLI help**: `migrate` / `schema` parser `help=` strings (drafted above) surface in
  `knowledge-base-ingest --help`.
- **`ROADMAP.md`** (`/home/mroynard/dev/knowledge-base/ROADMAP.md`): add a one-line entry under the
  relevant phase noting #450 landed the schema-version + backup-before-migrate framework at v1, with the
  CLI surface (`migrate`, `migrate --check`, `schema`) and the ME parity reference. Read it first to
  match the existing entry style; do not restructure it.
- **No new long-form doc.** A standalone `docs/migrations.md` is deferred — the docstrings + ROADMAP
  line are sufficient for a v1-empty ladder. (Add the doc when the first real migration ships and an
  operator runbook becomes load-bearing.)

## Testing

TDD: write each test, watch it fail, implement. New tests live in the existing files (parity with the
fixture style there).

**`tests/test_db.py`** (schema-version + migrate machinery):

1. `test_fresh_db_initializes_at_current_version` — `init_schema` on a fresh `tmp_path` DB →
   `get_schema_version(conn) == CURRENT_SCHEMA_VERSION`. (**AC-1**)
2. `test_legacy_db_backfilled_to_v1` — build a config table with `embed_model` but no `schema_version`
   (mimic db.py:634 fresh-skip path, like test_db.py:289), run `init_schema`, assert version == 1 via
   `INSERT OR IGNORE`.
3. `test_get_schema_version_defaults_to_1_when_missing` — config table with no `schema_version` row →
   `get_schema_version` returns 1.
4. `test_peek_schema_version_reads_without_mutating` — init a DB, capture mtime + `-wal` absence, call
   `peek_schema_version`, assert the value and that no `-wal`/`-shm` were created (read-only).
5. `test_peek_schema_version_missing_file_raises` — `peek_schema_version(tmp_path/"nope.db")` →
   `FileNotFoundError`.
6. `test_peek_schema_version_non_kb_db_raises` — create an empty sqlite file with no config table →
   `KnowledgeBaseError`.
7. `test_migrate_noop_when_current` — fresh DB, `migrate(conn)` → `migrated is False`, `pending == []`,
   and **no `backups/` dir created** (assert `(db_path.parent/"backups").exists() is False`).
8. `test_migrate_check_reports_pending` _(simulated v2)_ — monkeypatch `db.CURRENT_SCHEMA_VERSION` to 2
   and append a no-op step to `db._SCHEMA_MIGRATIONS`; set the live row to 1; `migrate(conn,
dry_run=True)` → `pending == [2]`, `migrated is False`, `checked is True`, **no backup written**.
9. `test_migrate_applies_and_backs_up` _(simulated v2)_ — same monkeypatch, with a step that performs a
   real DDL (`ALTER TABLE chunks ADD COLUMN _probe TEXT`); `migrate(conn)` →
   - a timestamped file exists under `<db_parent>/backups/` matching `*.v1-*.db` (**AC-2**),
   - the live DB now has the `_probe` column,
   - `get_schema_version(conn) == 2`, report `migrated is True`.
10. `test_migrate_restores_on_failure` _(simulated v2)_ — step that runs a valid DDL then `raise
RuntimeError("boom")`; assert:
    - `migrate(conn)` raises `RuntimeError`,
    - after the call the live DB is back at v1 (`peek_schema_version(db_path) == 1`),
    - the failed-step DDL effect is gone (the `_probe` column does NOT exist),
    - the backup file is retained. (**AC-3**)
11. `test_migrate_refuses_newer_db` — set live row to `CURRENT_SCHEMA_VERSION + 1`; `migrate(conn)` →
    `newer is True`, `migrated is False`, DB untouched.
12. `test_backup_before_migrate_is_wal_safe` — open the DB in WAL (the default), write an uncommitted-
    then-committed chunk, call `backup_before_migrate`, open the backup read-only, assert the committed
    row is present (proves `VACUUM INTO` captured WAL content).
13. `test_backup_path_null_byte_rejected` — monkeypatch `db_path` stem to contain `\x00` (or call the
    null-byte branch directly) → `KnowledgeBaseError`. (defense-in-depth parity, schema.rs:389)

Monkeypatching note: tests 8-11 set `db.CURRENT_SCHEMA_VERSION = 2` and append to
`db._SCHEMA_MIGRATIONS` inside the test, restoring both in teardown (use `monkeypatch.setattr` for the
constant and a fixture that snapshots/restores the list). This proves the driver on a _simulated_ v2
without shipping a real v2 — the crux of the MVP.

**`tests/test_indexer.py`** (CLI surface):

14. extend `test_build_parser_subcommands` (test_indexer.py:87) with `"migrate": ["migrate"]`,
    `"migrate --check": ["migrate", "--check"]`, `"schema": ["schema"]`.
15. `test_cmd_schema_matches_exit_0` — init a DB at current, `main(["--db", db, "schema"])` exits 0 and
    prints `matches: true`.
16. `test_cmd_schema_mismatch_exit_1` — write `schema_version = '0'` into a built DB, `cmd_schema` →
    `SystemExit(1)`; capture stdout shows `matches: false`.
17. `test_cmd_migrate_check_pending_exit_1` _(simulated v2 via monkeypatch)_ — `cmd_migrate` with
    `--check` and a pending v2 → exit 1, report `migrated is False`.
18. `test_cmd_migrate_applies_exit_0` _(simulated v2)_ — `cmd_migrate` without `--check` → exit 0,
    report `migrated is True`, backup present.
19. `test_cmd_migrate_does_not_init_schema_on_legacy` — build a pre-#450 DB by hand (config without
    `schema_version`), run `main(["--db", db, "schema"])`; assert it reports v1 **without** having added
    any of the v2+ artifacts (i.e. `cmd_schema`/`peek` did not run `init_schema`). Guards the
    `_get_conn`-bypass decision.
20. `test_cmd_migrate_missing_db_clean_error` — `main(["--db", tmp/"absent.db", "migrate"])` → exit 1
    with a JSON `{"error": ...}` (no traceback), via the widened funnel.

**Regression**: the existing `init_schema` tests (test*db.py) and all `cmd*\*` tests (test_indexer.py)
must stay green — the stamp is additive and the migrate path is dormant at v1.

## Verification

Run from the worktree root `/home/mroynard/dev/knowledge-base/.worktrees/450-schema-version-migrate`:

```bash
# 1. Lint + format (house style, pyproject.toml:33-38)
uv run ruff format --check src/knowledge_base/db.py src/knowledge_base/indexer.py \
    tests/test_db.py tests/test_indexer.py
uv run ruff check src/knowledge_base/db.py src/knowledge_base/indexer.py

# 2. Targeted unit tests (the new machinery)
uv run pytest tests/test_db.py tests/test_indexer.py -q

# 3. Full suite (catch regressions in MCP-server schema init path)
uv run pytest -q

# 4. Manual smoke — fresh DB, schema gate, no-op migrate, dry-run
TMP=$(mktemp -d)
uv run knowledge-base-ingest --db "$TMP/k.db" status >/dev/null   # creates + inits
uv run knowledge-base-ingest --db "$TMP/k.db" schema ; echo "schema exit=$?"   # expect matches:true, exit 0
uv run knowledge-base-ingest --db "$TMP/k.db" migrate --check ; echo "check exit=$?"  # pending:[], exit 0
uv run knowledge-base-ingest --db "$TMP/k.db" migrate ; echo "migrate exit=$?"        # migrated:false, exit 0
ls "$TMP/k.db".* 2>/dev/null; ls "$TMP/backups" 2>/dev/null || echo "no backups dir (correct at v1)"

# 5. Manual smoke — mismatch path is reportable + non-zero (AC-3 reporting)
uv run python -c "
import sqlite3, sys
c = sqlite3.connect('$TMP/k.db')
c.execute(\"UPDATE config SET value='0' WHERE key='schema_version'\"); c.commit(); c.close()
"
uv run knowledge-base-ingest --db "$TMP/k.db" schema ; echo "mismatch exit=$?"   # expect matches:false, exit 1
rm -rf "$TMP"
```

Expected: steps 1-3 exit 0; step 4 prints `matches: true`, empty pending, no `backups/` dir (v1 ladder
empty); step 5 prints `matches: false` and exits 1. The backup-write and restore-on-fail paths are
covered by the simulated-v2 unit tests (9, 10, 18), since at v1 there is nothing live to migrate — this
is the honest MVP boundary and is called out explicitly.

## Git ops

Worktree `/home/mroynard/dev/knowledge-base/.worktrees/450-schema-version-migrate` already exists on
branch `450-schema-version-migrate` (per the harness env). Plan of record:

1. **Confirm issue #450** is the tracking issue; this PR closes it. If a checklist edit is wanted, mirror
   the three acceptance criteria as tasks. (Do not create a new issue.)
2. **Commit** atomically, Conventional Commits, body explaining WHY (durable-migration safety + ME
   parity), no co-author line:
   - `feat(db): add schema_version + VACUUM-INTO backup-before-migrate framework (v1 baseline)`
   - `feat(cli): add migrate/--check and schema subcommands to knowledge-base-ingest`
   - `test: cover schema-version stamp, migrate/restore (simulated v2), CLI exit codes`
     (Split is optional; a single squashed feat commit is acceptable since this is one vertical slice.)
3. **Open PR** against `master`, body: the BLUF, the three AC mapped to tests (AC-1→test 1; AC-2→test 9;
   AC-3→tests 10+16), the ME parity table, and the explicit deferral list (so reviewers see what was
   intentionally scoped out).
4. **Super-review** (`/super-review` or the multi-model debate per house workflow): focus reviewers on
   (a) the restore-on-fail correctness (connection close ordering, sidecar cleanup), (b) the
   `_get_conn`-bypass in `cmd_migrate` (does any caller depend on `init_schema` running?), (c) the
   read-only `peek` not creating a file, (d) `VACUUM INTO` WAL-safety. Resolve every thread.
5. **Squash-merge** into `master` (house rule: always squash). Delete the worktree + branch after merge.

---

## Function-signature summary (the contract surface this PR adds)

```python
# db.py
CURRENT_SCHEMA_VERSION: int = 1
_SCHEMA_MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = []  # empty at v1

def get_schema_version(conn: sqlite3.Connection) -> int: ...
def peek_schema_version(db_path: Path) -> int: ...           # read-only, no init
def backup_before_migrate(db_path: Path, from_version: int) -> Path: ...  # VACUUM INTO
def migrate(conn, *, backup: bool = True, dry_run: bool = False) -> dict: ...
def schema_status(conn: sqlite3.Connection) -> dict: ...
def _connection_path(conn) -> Path | None: ...              # PRAGMA database_list
def _restore_backup(conn, db_path: Path, backup_path: Path) -> None: ...

# indexer.py
def cmd_migrate(args: argparse.Namespace) -> None: ...       # exit 1 on pending --check / error
def cmd_schema(args: argparse.Namespace) -> None: ...        # exit 1 on mismatch
```

**Acceptance-criteria traceability:**

- AC-1 (fresh DB at CURRENT) → §1b stamp + test 1 + smoke step 4.
- AC-2 (timestamped backup before mutating) → §1d `backup_before_migrate` + test 9.
- AC-3 (mismatch reportable; failed migrate restores) → §1e except-clause + §2a/§2b reporting +
  tests 10, 11, 16 + smoke step 5.
