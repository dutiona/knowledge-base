# Plan (RISK-FIRST lens): KB #450 — schema-version + backup-before-migrate framework

**Repo:** knowledge-base (Python) · **Issue:** #450 · **Worktree:** `/home/mroynard/dev/knowledge-base/.worktrees/450-schema-version-migrate`
**Parity target:** memory-engine `src/store/schema.rs` + `memory-engine-cli/src/commands/{migrate,schema}.rs` + `memory-engine-cli/src/db.rs`.

## BLUF

The framework itself is small (a `schema_version` config key, a `CURRENT_SCHEMA_VERSION=1` constant, a `migrate()` with VACUUM-INTO backup + restore-on-fail, two CLI subcommands). **The risk is entirely in the seams**, not the feature: WAL sidecar files vs. file-copy restore, restoring a DB that a live server still holds open via a cached thread-local connection, and stamping ~existing unversioned production DBs to v1 without a destructive re-migration. This plan leads with those failure modes and sequences the work so the **scariest assumptions are retired in the first two phases** (a restore that actually restores; a stamp that is provably non-destructive), before any CLI sugar is written.

The single most important design decision, forced by risk, diverges from a naive reading of design point (c): **the backup/restore unit is the main DB file plus a checkpoint, and restore is performed against a freshly-closed connection — never against the live server's cached connection.** The migrate path is therefore a **short-lived, exclusive CLI operation** (ME's model), not something the long-running MCP server ever triggers. `init_schema` must NOT silently call `migrate`.

---

## Part 1 — RISK LEDGER (lead with this)

Each risk states: the failure, why the proposed design is exposed, the mitigation, and **the phase that retires it**. Risks are ordered by severity × likelihood. The phase plan in Part 2 is literally this ledger, sequenced.

### R1 — Restore-on-fail does not actually restore (CRITICAL, retire in Phase 2)

**Failure.** A v2+ migration mutates the live DB, fails midway, we "restore" the timestamped backup by copying it over the DB file — but the live DB is in WAL mode. A stale `knowledge.db-wal` / `knowledge.db-shm` left next to the restored `knowledge.db` will be **replayed on next open**, re-applying the partial migration's committed-but-unwanted WAL frames on top of the restored file, or producing a malformed-image error. We think we restored; we corrupted.

**Why exposed.** Design point (c) says "VACUUM INTO backup, restore-on-fail" but does not specify _how_ restore copies back, and KB's `get_connection` unconditionally sets `PRAGMA journal_mode=WAL` (db.py:191). VACUUM INTO produces a clean standalone file (good — the backup has no sidecars), but the **restore direction** is the danger: overwriting the main file while sidecars exist.

**Mitigation (ordered, this is the core invariant of the whole feature):**

1. Before backup, run `PRAGMA wal_checkpoint(TRUNCATE)` so the live file is self-contained and `-wal` is emptied.
2. Take the backup with `VACUUM INTO '<backup>'` (atomic, sidecar-free — ME `backup_before_migration`, schema.rs:364-413).
3. Run the migration on a connection that is the **sole opener** (see R4).
4. On any exception: `conn.close()` the migrating connection FIRST (releasing the WAL), then **delete `<db>`, `<db>-wal`, `<db>-shm` and `<db>-journal`**, then copy the backup over `<db>`. Only after the copy succeeds, re-open. Restoring without first deleting the sidecars is the bug; deletion is mandatory and tested.
5. Re-raise the original migration error after a successful restore (abort, non-zero).

**Retired by:** Phase 2 — `_restore_backup()` + the `test_failed_migration_restores_backup` test that injects a failing migration and asserts (a) data intact, (b) version unchanged, (c) **no orphan `-wal`/`-shm` remain**, (d) the restored DB opens clean and `PRAGMA integrity_check` returns `ok`.

> Parity note: ME leaves the pre-migration backup in place and relies on the _transaction_ rolling back the live DB in-process (schema.rs:241-268: each migration is `conn.unchecked_transaction()` → on error the `?` propagates and the tx drops/rolls back). KB v1 has **no v2+ migration to wrap yet**, so the transactional rollback path is latent. We still build the file-level restore because (i) it is the issue's explicit acceptance criterion ("a failed migrate restores the backup"), and (ii) it is the safety net for the table-rebuild migrations (DROP/RENAME under `foreign_keys=OFF`) that the existing `_migrate_*` use and future v2+ will use — those can leave a half-built `*_new` table if a crash (not a Python exception) interrupts them.

### R2 — Stamping an existing unversioned production DB destroys data or double-applies (CRITICAL, retire in Phase 1)

**Failure.** A real `~/.local/share/knowledge-base/knowledge.db` exists today with **no `schema_version` key**. If `migrate()` treats "no version" as "fresh, create everything" it is fine, but if it treats it as "version 0, run the v1→v2…→vN chain" against an already-migrated DB, or if the stamping logic re-runs the 11 idempotent `_migrate_*` in a way that is NOT actually idempotent on a populated DB, we corrupt or lose data. The table-rebuild migrations (`_migrate_source_type_figure` db.py:215, `_migrate_relationship_types` db.py:259, `_migrate_jobs_types` db.py:342) do `INSERT INTO ..._new SELECT * FROM ...; DROP TABLE; RENAME` — a double-apply that mis-maps columns silently truncates rows.

**Why exposed.** Design point (b) is explicitly "the existing ~11 idempotent `_migrate_*` STAY as the v1 baseline." So the entire installed base is "v1 content, but unstamped." The framework must recognize that state and **stamp it to v1 without running anything destructive**.

**Mitigation:**

1. Define the stamping rule precisely (mirrors ME init_schema, schema.rs:96-114, and ME migrate's `unwrap_or("1")`, schema.rs:201):
   - **No `config` table at all** → truly fresh → after `init_schema` builds everything, write `schema_version = CURRENT_SCHEMA_VERSION (=1)`.
   - **`config` table exists but no `schema_version` key** → legacy/unversioned but already-v1-shaped → write `schema_version = 1`. **Do NOT** infer "0" and run a chain.
   - **`schema_version` present** → trust it.
2. The ~11 `_migrate_*` stay exactly where they are (called at the tail of `init_schema`, db.py:912-927). They are **the definition of v1** and remain self-guarding (each checks `sqlite_master` / `PRAGMA table_info` before acting). The stamping logic never re-invokes them as a "migration chain"; it only writes the version number.
3. `CURRENT_SCHEMA_VERSION = 1` means: on the entire current installed base, `migrate()` is a **no-op that only writes one missing config row**. There is no v0→v1 chain to get wrong. This is the deliberate, risk-minimizing scoping choice of design (b).

**Retired by:** Phase 1 — `_stamp_or_read_version()` + three tests: fresh DB → 1; legacy DB built by hand with the pre-v1 DDL + data, then `init_schema` → version 1 **and row count unchanged + content_hash set intact** (reuse the fixtures already in test_db.py:283-326, 329-372, 411-464, 500-527, which build pre-migration schemas); already-stamped DB → untouched.

### R3 — Backup location assumptions break on the real default path / in-memory / read-only parent (HIGH, retire in Phase 1)

**Failure.** Design point (c) names `data/backups/<timestamp>.db`. But:

- The production DB lives at `~/.local/share/knowledge-base/knowledge.db` (db.py:38). A relative `data/backups/` is resolved against the CLI's CWD — wrong, possibly unwritable, and not co-located with the DB.
- Tests and some callers use `:memory:` or a `sqlite3.connect(":memory:")`-style connection; `VACUUM INTO` of an in-memory DB to a file "works" but backing up an in-memory DB before migrating it is meaningless and `PRAGMA database_list` returns an empty/`:memory:` path.
- The DB's parent dir could be read-only (CI sandbox, packaged install).

**Why exposed.** A hardcoded relative backup dir is a latent footgun the moment the tool runs anywhere but the repo root.

**Mitigation (ME parity, schema.rs:88 + db.rs:88 `path.parent()`):**

1. Backup dir defaults to **a `backups/` subdirectory beside the live DB file** (`<db>.parent / "backups"`), created with `mkdir(parents=True, exist_ok=True)`, NOT a CWD-relative `data/backups`. Allow override via `--backup-dir`.
2. Backup filename: `<db_stem>.<UTC timestamp>.v<from_version>.db` (timestamp = `datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")`), mirroring ME's `{db_name}.v{version}.bak` but adding the timestamp the issue requires.
3. Resolve the **actual** source path via `PRAGMA database_list` (ME schema.rs:370-376). If it is empty or `:memory:`, **skip backup** and require the caller to pass `backup=False` semantics — `migrate()` of an in-memory DB takes `backup_dir=None` (exactly ME's contract, schema.rs:191-193, 374-376).
4. Null-byte guard on the interpolated path (ME schema.rs:389-391) since `VACUUM INTO` cannot parameterize its target; escape `'` → `''` (ME schema.rs:407).
5. If `mkdir`/VACUUM fails with a write error, that is a backup failure → abort BEFORE mutating (fail-fast; never migrate without a backup unless explicitly opted out).

**Retired by:** Phase 1 — `_backup_path_for()` + tests: default lands beside DB; `:memory:` raises a clear "cannot back up in-memory database" (parity with ME schema.rs:374-375) unless `backup_dir=None`; null-byte path rejected.

### R4 — Concurrency: server holds the DB open while the CLI migrates (HIGH, retire in Phase 3 design + Phase 5 docs)

**Failure.** The MCP server (`_conn.py`) caches a **thread-local connection** and a module-global `_schema_ready` flag (lines 9-25): it runs `init_schema` exactly once per process and then holds the connection open for the server's lifetime. If an operator runs `knowledge-base-ingest migrate` while the server is running, two writers contend. The CLI's VACUUM INTO needs a read lock (fine), but the table-rebuild path of a future v2 migration needs an **exclusive** lock; the server's open WAL connection will cause `SQLITE_BUSY`. Worse: even if migrate succeeds, the **server's cached connection keeps serving the OLD schema** (it never re-runs `init_schema`; `_schema_ready` stays `True`) and its prepared statements may reference dropped tables → runtime errors until restart.

**Why exposed.** KB has a long-running server AND a CLI both targeting the same default DB path. ME's model sidesteps this: migrate is a short-lived CLI command and the engine is not a persistent daemon in the same way.

**Mitigation:**

1. **migrate is an offline operation.** Document (Phase 5) that the server must be stopped before `migrate`. This is the honest constraint, matching ME's operator model — not a code workaround.
2. Open the migrating connection with a **bounded busy timeout** and, for the actual migration step (v2+), attempt `BEGIN IMMEDIATE` / set a short `PRAGMA busy_timeout`; if the DB is locked, **fail fast with a clear "database is busy — stop the server and retry"** rather than hanging or partially migrating. `get_connection` already sets `timeout=30.0` (db.py:186); migrate will use a _short_ timeout so it errors quickly instead of blocking 30s.
3. `migrate` and `schema` use their **own freshly-opened connection** (the CLI's `_get_conn` pattern, indexer.py:29-32), never the server's thread-local. The `schema --check` / `schema` read path uses a **read-only** connection (parity with ME `peek_schema_version_from_db`, db.rs:42-66) so it can report version even against a busy DB without taking a write lock.
4. After a successful CLI migrate, the server (if running) is stale by construction; the doc says restart. We do **not** try to invalidate the server's cache cross-process (impossible without IPC). Surfacing this is required, per the no-silent-punt rule.

**Retired by:** Phase 3 (read-only peek + fresh-connection design that structurally cannot touch the server's cache) and Phase 5 (the operator doc that states the stop-server precondition). The residual (operator ignores the doc and migrates a live v2 in future) is bounded by mitigation #2's fail-fast.

### R5 — Partial/failed backup leaves a corrupt or misleading artifact (MEDIUM, retire in Phase 2)

**Failure.** VACUUM INTO is interrupted by disk-full → a truncated `.db` sits in `backups/`. A later restore picks a corrupt backup; or the operator believes a backup exists when it does not.

**Mitigation:**

1. VACUUM INTO to a `.db.partial` temp name, then `os.replace()` to the final timestamped name only on success (atomic rename within the same dir). A crash leaves a `.partial` that the restore path ignores by construction (it only ever restores the exact path it just wrote and held in a variable — never "the newest file in backups/").
2. Restore only ever uses the **in-memory `backup_path` variable** from the current migrate invocation, not a directory scan — so a stale/corrupt sibling can never be selected.
3. Remove a pre-existing identical target before VACUUM (ME schema.rs:393-401) to avoid VACUUM-INTO-fails-on-existing-file.
4. (Defense in depth) After writing the backup, open it read-only and `PRAGMA integrity_check` / read its `schema_version` before proceeding to mutate. If the backup is not readable, abort before touching the live DB.

**Retired by:** Phase 2 — `test_backup_atomic_rename` (simulate failure between VACUUM and rename → no final-named file appears) and the integrity-check-the-backup step.

### R6 — Transactional boundary: a mid-migration _crash_ (not exception) leaves a half-built schema (MEDIUM, retire in Phase 2 framework shape)

**Failure.** The existing table-rebuild migrations run `PRAGMA foreign_keys=OFF; BEGIN; CREATE ..._new; INSERT; DROP; RENAME; COMMIT; PRAGMA foreign_keys=ON` (db.py:215-248). A Python exception inside rolls back the transaction (good). But a process kill / power loss between `COMMIT` of one `_migrate_*` and the next leaves a DB that is internally consistent but at an **unknown version** — there is no per-step version stamp in v1 (all 11 run unconditionally inside one `init_schema`).

**Why exposed.** v1 collapses 11 historical steps into one idempotent batch with no intermediate version markers. That is acceptable _because each step is self-guarding and re-runnable_, but the framework must preserve that property and the future v2+ path must not.

**Mitigation:**

1. For v1: rely on the **existing idempotency** (each `_migrate_*` re-checks state). A re-run of `init_schema` after a crash converges. The backup taken by `migrate` is the outer safety net.
2. Establish the **v2+ contract now** (in code comments + the migration registry shape, parity with ME `MIGRATIONS` table schema.rs:172-183 and the per-migration transaction + version stamp schema.rs:241-268): each future migration is `(fn, disable_fk: bool)`, runs inside one transaction, and **the `schema_version` bump is written inside the same transaction** (ME schema.rs:257). So a crash either leaves version N (migration didn't commit) or version N+1 (it did) — never an ambiguous in-between. Build the registry + the per-step loop now, even though it is empty for v1, so the safe pattern is the only available pattern when v2 lands.
3. FK toggling: re-enable `PRAGMA foreign_keys=ON` in a `finally` (parity ME schema.rs:261-266) so a failed FK-disabled migration cannot leave the connection with enforcement off.

**Retired by:** Phase 2 — the `migrate()` loop is written with the transactional-stamp pattern and a `disable_fk` flag, validated by a synthetic `_MIGRATIONS = [(failing_fn, False)]` test that asserts the version did NOT advance and FK enforcement is back ON.

### R7 — `init_schema` is on the hot path of every CLI command and every server start (MEDIUM, retire in Phase 3)

**Failure.** If `migrate()` (with backup!) is wired into `init_schema`, then **every** `knowledge-base-ingest ingest`, every server boot, and every test that calls `init_schema` (test_db.py everywhere) would take a VACUUM-INTO backup — slow, disk-filling, and surprising. The CLI's `_get_conn` calls `init_schema` on every invocation (indexer.py:31).

**Mitigation:**

1. **Strict separation (ME parity, schema.rs:96-114 vs 200-277):** `init_schema` ONLY (a) creates the fresh schema and stamps version on a truly-fresh DB, or (b) on an existing DB, ensures the `schema_version` key exists (stamp-to-1 if missing) and runs the existing idempotent `_migrate_*` — but **never takes a backup and never runs a v2+ chain.** Backup + version-gated migration live exclusively in the new `migrate()` function, invoked only by the `migrate` CLI subcommand.
2. This means the day-to-day path (ingest, server) is unchanged in cost and behavior. Only the explicit operator `migrate` command pays for backup. Exactly ME's split.

**Retired by:** Phase 3 — `migrate()` is a separate public function; `init_schema` gains only the cheap stamp. A test asserts `init_schema` does NOT create anything under `backups/`.

### R8 — sqlite-vec extension must be loaded on the migrate connection (LOW, retire in Phase 3)

**Failure.** `migrate`/`schema` open their own connection. If they use a bare `sqlite3.connect` without `sqlite_vec.load` (which `get_connection` does, db.py:187-189), any DDL or VACUUM touching the `vec0` virtual tables (`chunks_vec`, `folder_summaries_vec`, db.py:686, 851) fails with "no such module: vec0". VACUUM INTO of a DB containing vec0 tables **requires the module loaded**.

**Mitigation:** `migrate` reuses `get_connection()` (which loads sqlite-vec) for the writable path. The read-only `schema` peek does NOT touch vec tables (only `SELECT value FROM config`) so it can use a plain read-only connection — but to be safe and uniform, load the extension there too, or scope the read strictly to `config`. Decision: read-only peek opens read-only and selects only from `config` (no vec access needed), matching ME db.rs:49-66.

**Retired by:** Phase 3 — the writable migrate uses `get_connection`; a test runs migrate on a DB with populated `chunks_vec` and asserts the backup + (no-op) migrate succeed.

### Risk → phase map

| Risk                                          | Severity | Retired in        |
| --------------------------------------------- | -------- | ----------------- |
| R1 restore really restores (WAL sidecars)     | CRITICAL | Phase 2           |
| R2 stamp unversioned DB, no double-apply      | CRITICAL | Phase 1           |
| R3 backup location (default/in-mem/read-only) | HIGH     | Phase 1           |
| R4 server+CLI concurrency / stale cache       | HIGH     | Phase 3 + Phase 5 |
| R5 partial backup artifact                    | MEDIUM   | Phase 2           |
| R6 crash-mid-migration version ambiguity      | MEDIUM   | Phase 2           |
| R7 init_schema hot path                       | MEDIUM   | Phase 3           |
| R8 sqlite-vec on migrate conn                 | LOW      | Phase 3           |

---

## Part 2 — IMPLEMENTATION (sequenced to retire risk earliest)

All paths absolute under the worktree root `/home/mroynard/dev/knowledge-base/.worktrees/450-schema-version-migrate`.

New module: `src/knowledge_base/schema_version.py` (keeps db.py focused; importable by both db.py and indexer.py without cycles). Constants and the read/stamp helpers may alternatively live in db.py near the `config` table (db.py:628); the draft puts the _framework_ in a new module and the `CURRENT_SCHEMA_VERSION` constant + `_stamp_or_read_version` wiring in db.py to keep `init_schema` self-contained. Final placement decided in Phase 0.

### Phase 0 — Plan issue + skeleton (no behavior change)

1. `gh issue` already exists (#450). Confirm scope in an issue comment: framework + v1 baseline, CURRENT_SCHEMA_VERSION=1, backup beside DB (not `data/backups`), migrate is offline.
2. Add `CURRENT_SCHEMA_VERSION = 1` and `SCHEMA_VERSION_KEY = "schema_version"` constants to db.py (near line 62, with the bootstrap defaults). Export from `__all__` (db.py:17-36).
3. No logic yet. Run the suite to confirm green baseline: `uv run pytest tests/test_db.py tests/test_indexer.py -q`.

### Phase 1 — Versioning + stamp + backup-path (retires R2, R3)

Implement, in db.py / schema_version.py:

- `get_schema_version(conn) -> int | None`: `SELECT value FROM config WHERE key='schema_version'`; parse int; `None` if absent. Raise `KnowledgeBaseError` on a non-integer value (parity ME schema.rs:64-65).
- `set_schema_version(conn, v)`: `INSERT ... ON CONFLICT(key) DO UPDATE` upsert (parity ME `set_config` schema.rs:157-164). The `config` table is `(key PRIMARY KEY, value)` (db.py:629-632), so upsert is clean.
- `_stamp_or_read_version(conn)` called at the **end** of `init_schema` (after the existing `_migrate_*` tail, db.py:927): if `schema_version` key missing, `set_schema_version(conn, CURRENT_SCHEMA_VERSION)`. This is the **R2 mitigation** — both fresh and legacy DBs converge to v1, never to 0, never re-running a destructive chain.
- `resolve_backup_path(conn, backup_dir=None) -> Path | None`: read source path via `PRAGMA database_list` (row col 2); if empty/`:memory:` → return `None`; else `backup_dir or (Path(src).parent / "backups")`, mkdir, return `dir / f"{stem}.{utc_ts}.v{from_version}.db"`. Null-byte guard (R3.4).

Tests (tests/test_db.py, extend):

- `test_fresh_db_stamps_current_version`: fresh `init_schema` → `get_schema_version == 1`.
- `test_legacy_unversioned_db_stamps_to_v1_nondestructively`: build the pre-v1 DDL+data using the existing hand-rolled fixtures (test_db.py:289-311 style), snapshot `COUNT(*)` and a `content_hash`, run `init_schema`, assert version==1 AND counts/hashes unchanged. **(R2)**
- `test_already_versioned_db_untouched`: set version to 1 manually, re-`init_schema`, version still 1, no error.
- `test_backup_path_beside_db_default` / `test_backup_path_memory_returns_none` / `test_backup_path_rejects_null_byte`. **(R3)**

### Phase 2 — backup + restore-on-fail core (retires R1, R5, R6)

This phase is the **heart of the safety story** and is written before any CLI.

- `backup_database(conn, backup_path) -> Path`:
  1. `conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")` (R1.1 — empty the WAL so the file is self-contained).
  2. Remove an existing identical `backup_path` (R5.3, ME schema.rs:393-401).
  3. `tmp = backup_path.with_suffix(backup_path.suffix + ".partial")`; escape `'`→`''`; `conn.execute(f"VACUUM INTO '{tmp_escaped}'")`.
  4. Open `tmp` read-only, `PRAGMA integrity_check` + read its `schema_version`; on failure raise `KnowledgeBaseError` and unlink `tmp` (R5.4).
  5. `os.replace(tmp, backup_path)` (atomic; R5.1).
  6. Return `backup_path`.
- `_restore_backup(db_path, backup_path)` (R1.4 — the load-bearing restore):
  1. Caller has already `conn.close()`-d the migrating connection (precondition; assert no open handle by attempting an exclusive open, or document that migrate owns the only handle).
  2. Delete `db_path`, `db_path + "-wal"`, `db_path + "-shm"`, `db_path + "-journal"` (the **WAL-sidecar deletion is the bug-fix**, R1).
  3. `shutil.copyfile(backup_path, db_path)`.
- `migrate(conn_or_path, *, backup_dir=None, dry_run=False) -> dict`: the orchestrator (parity ME schema.rs:200-277). For v1, `CURRENT_SCHEMA_VERSION=1`, so:
  - read live version (stamp-to-1 if missing — same rule as init);
  - `pending = list(range(live+1, CURRENT+1))` → empty for v1;
  - if `dry_run` or `pending` empty → report, no mutation;
  - else (future v2+): `backup_path = backup_database(...)`; then for each `(fn, disable_fk)` in `_MIGRATIONS`, in a transaction, run `fn`, optional `PRAGMA foreign_key_check` (ME schema.rs:251-256), `set_schema_version(tx, target)` **inside the tx** (R6.2, ME schema.rs:257), commit; `finally` re-enable FK (R6.3). On any exception: close conn, `_restore_backup`, re-raise (R1.5).
  - Build the empty `_MIGRATIONS: list[tuple[Callable, bool]] = []` registry now (R6.2 — the safe v2+ pattern is the only pattern).

Tests (new tests/test_schema_migrate.py):

- `test_backup_creates_timestamped_file_beside_db` — acceptance criterion "migrate creates a timestamped backup before mutating" (force a synthetic pending migration so backup actually runs).
- `test_backup_atomic_rename_no_partial_on_failure` (R5).
- `test_backup_is_self_contained_no_wal_replay`: after backup, open the backup standalone, assert no `-wal` needed and `integrity_check==ok` (R1).
- `test_failed_migration_restores_backup`: monkeypatch `_MIGRATIONS=[(lambda tx: (_ for _ in ()).throw(RuntimeError("boom")), False)]` with a temporary `CURRENT=2`; seed data; run `migrate`; assert (a) data intact, (b) `get_schema_version==1` (NOT advanced), (c) **no `-wal`/`-shm` orphan beside db**, (d) restored db `integrity_check==ok`, (e) FK enforcement back ON. **This single test retires R1+R6.** (Acceptance: "a failed migrate restores the backup.")

### Phase 3 — version-report + read-only peek + separation (retires R4 read path, R7, R8)

- `peek_schema_version(db_path) -> int | None`: open **read-only** (`sqlite3.connect(f"file:{path}?mode=ro", uri=True)`), `SELECT value FROM config WHERE key='schema_version'`, parse; clear error if the DB has no config table (parity ME db.rs:42-66). No vec load needed (R8). This is the release-gate verify primitive.
- `schema_report(db_path) -> dict`: `{schema_version, current_schema_version, matches}` (parity ME commands/schema.rs:11-15).
- Confirm `init_schema` stays backup-free (R7): only the cheap stamp added in Phase 1; assert in a test that `init_schema` creates nothing under `backups/`.

Tests:

- `test_peek_readonly_does_not_create_wal` (R4/R7): peek a DB, assert no `-wal` file materializes and the DB mtime is unchanged.
- `test_schema_report_matches_and_mismatch` (force a fake `schema_version=0` row → `matches=False`).

### Phase 4 — CLI subcommands `migrate` + `schema` (design point d)

In indexer.py (parity ME commands/migrate.rs + schema.rs):

- `cmd_schema(args)`: `report = schema_report(args.db)`; `_print(report)`; **exit non-zero on mismatch** (release-gate contract, ME schema.rs:50-54).
- `cmd_migrate(args)`: if `args.check` → compute `pending` via `peek_schema_version`, print, **exit non-zero if pending non-empty** (ME migrate.rs:114-122), no mutation. Else open writable via `get_connection(args.db)` (loads sqlite-vec, R8), call `migrate(conn, backup_dir=args.backup_dir)`, print report incl. backup path, exit 0 on success. On `KnowledgeBaseError` → `_print_error`, exit 1 (the restore already happened inside `migrate`).
- Parser: add `migrate` subparser with `--check` (`action="store_true"`) and `--backup-dir` (`type=Path, default=None`); add `schema` subparser (no args). Register in `build_parser` (indexer.py:226-261) and the `dispatch` dict (indexer.py:287-293). **Do NOT** route these through the existing `_get_conn` (which force-runs `init_schema`); `schema --check`/`schema` must use the read-only peek so they work against a stale or busy DB (R4).
- **NEWER-than-binary guard** (ME migrate.rs:49-57, schema.rs): if `live > CURRENT`, report `newer=True` and exit non-zero for both `schema` and `migrate`/`migrate --check` — a rollback DB must never read as "nothing to do."

Tests (tests/test_indexer.py, extend; mirror the `_make_args`/`_setup` helpers indexer.py test:67-79):

- `test_cli_schema_reports_version` (capsys JSON parse → version 1, matches True, exit 0).
- `test_cli_schema_mismatch_exits_nonzero` (seed version 0).
- `test_cli_migrate_check_up_to_date_exit_zero` (v1 DB, `--check` → exit 0, no backup created).
- `test_cli_migrate_check_pending_exit_nonzero` (synthetic CURRENT bump → exit non-zero, still no mutation).
- `test_cli_migrate_creates_backup` (synthetic pending → backup file appears).
- `test_build_parser_has_migrate_and_schema` (extend the existing subcommand-presence test, test_indexer.py:87+).

### Phase 5 — Documentation (retires R4 operator residual)

- `docs/plans/450-schema-version-migrate.md`: the chosen plan (this draft, post-synthesis).
- README / operations doc: an **"Upgrading / migrating the database"** section stating:
  - `knowledge-base-ingest schema` reports live-vs-current (exit non-zero on mismatch — usable in CI/release gates, parity ME).
  - `knowledge-base-ingest migrate --check` is a dry run; `migrate` backs up beside the DB then migrates; on failure it restores and aborts.
  - **STOP the MCP server before running `migrate`** (R4) — the server caches its connection and will serve a stale schema until restarted; concurrent migrate may fail with "database is busy."
  - Where backups land (`<db dir>/backups/<stem>.<ts>.vN.db`) and that they are not auto-pruned (operator owns cleanup).
- Module + function docstrings citing the ME parity functions (schema.rs, migrate.rs) so the cross-repo mirror is discoverable.

---

## Part 3 — TESTING (consolidated)

Framework: `uv run pytest`. New file `tests/test_schema_migrate.py`; extensions to `tests/test_db.py` and `tests/test_indexer.py`. All use `tmp_path` (no touching the real `~/.local/share` DB).

**Acceptance-criteria coverage (issue):**

- "Fresh DB initializes at CURRENT_SCHEMA_VERSION" → `test_fresh_db_stamps_current_version`.
- "Migrate creates a timestamped backup before mutating" → `test_backup_creates_timestamped_file_beside_db` / `test_cli_migrate_creates_backup`.
- "Version mismatch is reportable" → `test_schema_report_matches_and_mismatch` / `test_cli_schema_mismatch_exits_nonzero`.
- "A failed migrate restores the backup" → `test_failed_migration_restores_backup` (the R1+R6 keystone).

**Risk-coverage cross-check** (every CRITICAL/HIGH risk has a named test): R1→`test_backup_is_self_contained_no_wal_replay` + `test_failed_migration_restores_backup`; R2→`test_legacy_unversioned_db_stamps_to_v1_nondestructively`; R3→`test_backup_path_*`; R4→`test_peek_readonly_does_not_create_wal` + the offline-migrate doc; R5→`test_backup_atomic_rename_no_partial_on_failure`; R6→`test_failed_migration_restores_backup` (FK + version assertions); R7→`init_schema`-creates-no-backup assertion; R8→migrate-on-vec-populated-DB test.

**Property to assert broadly:** after ANY operation (init, migrate, failed-migrate-restore), `PRAGMA integrity_check` returns `ok` and **no `-wal`/`-shm` orphan files remain** once all connections are closed.

---

## Part 4 — VERIFICATION (commands; run from worktree root)

```bash
cd /home/mroynard/dev/knowledge-base/.worktrees/450-schema-version-migrate
uv run ruff format --check .
uv run ruff check .
uv run pytest tests/test_db.py tests/test_indexer.py tests/test_schema_migrate.py -q
uv run pytest -q                      # full suite, no regressions

# Manual smoke (acceptance criteria), throwaway DB:
TMPDB=$(mktemp -d)/k.db
uv run knowledge-base-ingest --db "$TMPDB" status          # creates+stamps fresh DB at v1
uv run knowledge-base-ingest --db "$TMPDB" schema          # expect matches=true, exit 0
echo "exit=$?"
uv run knowledge-base-ingest --db "$TMPDB" migrate --check # expect "no pending", exit 0
echo "exit=$?"
# version-mismatch path: hand-set schema_version=0 then re-check
uv run python -c "import sqlite3,sys; c=sqlite3.connect('$TMPDB'); c.execute(\"UPDATE config SET value='0' WHERE key='schema_version'\"); c.commit()"
uv run knowledge-base-ingest --db "$TMPDB" schema; echo "exit=$? (expect nonzero, mismatch)"
```

(No network, no Ollama needed for any of the above — schema/migrate are offline and embedding-free.)

---

## Part 5 — GIT OPS

Worktree already exists at `.worktrees/450-schema-version-migrate` (branch presumably `450-schema-version-migrate`). Conventional Commits, imperative, WHY in body, atomic.

1. **Confirm/comment the issue** (#450) with the scope decisions (offline migrate; backup beside DB not `data/backups`; CURRENT*SCHEMA_VERSION=1; v1 baseline = existing `\_migrate*\*`).
2. **Commits** (atomic, roughly per phase):
   - `feat(db): add CURRENT_SCHEMA_VERSION + schema_version config stamping` (Phase 0–1).
   - `feat(db): WAL-safe VACUUM-INTO backup with restore-on-fail migrate()` (Phase 2).
   - `feat(db): read-only schema_version peek + report` (Phase 3).
   - `feat(cli): add migrate (--check) and schema subcommands to knowledge-base-ingest` (Phase 4).
   - `test: schema-version, backup/restore, and CLI migrate/schema coverage` (if not folded into above).
   - `docs: database upgrade/migrate operator guide + ME parity notes` (Phase 5).
3. **PR** to `master`: title `feat: schema-version + backup-before-migrate framework (#450)`. Body: link #450, the risk ledger summary (R1/R2 as the load-bearing ones), the ME-parity table (CURRENT_SCHEMA_VERSION ↔ schema.rs:8; backup ↔ schema.rs:364; peek ↔ db.rs:42; CLI ↔ migrate.rs/schema.rs), and the explicit offline-migrate operator note.
4. **Super-review** (multi-model). Direct reviewers at file paths (per house convention), flagging the three highest-risk seams to scrutinize:
   - the **restore-on-fail WAL-sidecar deletion** in `_restore_backup` (R1) — is sidecar deletion complete and ordered after `close()`?
   - the **stamp-unversioned-to-v1** rule in `_stamp_or_read_version` (R2) — can any legacy DB be mis-stamped to 0 and trigger a destructive chain?
   - the **init_schema vs migrate separation** (R7) — does any hot path accidentally take a backup?
5. Address review threads; re-run the full Verification block; resolve all threads.
6. **Squash-merge** to `master` (house default). Delete the worktree/branch after merge.

---

## Appendix — ME parity map (cite:line)

| KB (this plan)                                                    | ME parity                                             | ME file:line               |
| ----------------------------------------------------------------- | ----------------------------------------------------- | -------------------------- |
| `CURRENT_SCHEMA_VERSION = 1`                                      | `CURRENT_SCHEMA_VERSION: u32 = 11`                    | schema.rs:8                |
| `schema_version` in `config` (key/value)                          | same table+key                                        | schema.rs:111, 157-164     |
| stamp fresh, stamp-missing-to-current                             | `init_schema` fresh-only + `migrate` `unwrap_or("1")` | schema.rs:96-114, 201      |
| `backup_database` VACUUM INTO + null-byte guard + remove-existing | `backup_before_migration`                             | schema.rs:364-413          |
| per-migration tx + version stamp inside tx + FK finally           | `migrate` loop                                        | schema.rs:241-268          |
| `_MIGRATIONS` registry `(fn, disable_fk)`                         | `MIGRATIONS`                                          | schema.rs:172-183          |
| `peek_schema_version` read-only                                   | `peek_schema_version_from_db`                         | db.rs:42-66                |
| `schema_report` `{version,current,matches}`                       | `SchemaReport`                                        | commands/schema.rs:11-15   |
| `migrate --check` exit-nonzero-on-pending; NEWER guard            | `MigrateReport` + exit codes                          | commands/migrate.rs:46-122 |
| backup dir = `<db>.parent/"backups"`                              | `path.parent()` backup_dir                            | db.rs:88                   |

**Divergences from the proposed design, forced by risk:**

- (c) `data/backups/<timestamp>.db` → **`<db dir>/backups/<stem>.<ts>.vN.db`** (R3: co-locate with DB, ME parity; relative `data/` is a CWD footgun).
- restore mechanism → **explicit WAL-sidecar deletion before file copy, against a closed connection** (R1: the un-obvious correctness requirement the design omits).
- migrate scope → **offline operator command; `init_schema` never backs up and never runs a v2+ chain** (R4, R7: the server's cached connection makes online migrate unsafe; surfaced in docs, not worked around).
