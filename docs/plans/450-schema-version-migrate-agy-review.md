## Review of Implementation Plan: KB #450 — Schema-Version + Backup-Before-Migrate Framework

### BLUF (Bottom Line Up Front)
The implementation plan is exceptionally thorough and demonstrates a deep understanding of SQLite WAL semantics, unversioned DB upgrades, and hot-path separation. Adopting the read-validates/writable-migrates split from `memory-engine` establishes a safe, clear operational boundary.

However, there are **three critical/high-severity issues** that must be addressed: Python `sqlite3` implicit commits on DDL, a crash condition when migrating fresh databases, and the lack of an early-return in [init_schema](file:///home/mroynard/dev/knowledge-base/.worktrees/450-schema-version-migrate/src/knowledge_base/db.py#L626). Addressing these findings is necessary for correctness, safety, and performance.

---

### 1. Gaps & Risks

#### [BLOCKER] Fresh Database Migration Crash
*   **Plan Section:** Phase 4 CLI `migrate` (`cmd_migrate`) + Phase 2 `migrate(...)` ([plan:L55-L56](file:///home/mroynard/dev/knowledge-base/.worktrees/450-schema-version-migrate/docs/plans/2026-06-16-450-schema-version-migrate.md#L55-L56))
*   **Risk:** Running `knowledge-base-ingest migrate` on a fresh or non-existent database will crash. 
*   **Why:** `cmd_migrate` is planned to call `get_connection` directly (which bypasses `init_schema`) and then call `migrate(conn)`. Inside `migrate()`, checking `get_schema_version(conn)` will try to run `SELECT value FROM config...`. Because the database is empty, the `config` table does not exist, throwing a `sqlite3.OperationalError: no such table: config`. Subsequent attempts to stamp the version will also fail.
*   **Mitigation:** `migrate` (or the CLI dispatch) must check if the database is fresh (i.e. does not have a `config` table in `sqlite_master`) and call [init_schema](file:///home/mroynard/dev/knowledge-base/.worktrees/450-schema-version-migrate/src/knowledge_base/db.py#L626) to bootstrap the baseline schema before running any migrations.

#### [HIGH] Lack of Early-Return in `init_schema`
*   **Plan Section:** Phase 1 versioning + stamp ([db.py:init_schema](file:///home/mroynard/dev/knowledge-base/.worktrees/450-schema-version-migrate/src/knowledge_base/db.py#L626))
*   **Risk:** Re-running obsolete DDL or legacy migrations on versioned v2+ databases.
*   **Why:** The plan runs the idempotent v1 builds and checks `_migrate_*` on *every* connection startup, only skipping the version stamp at the tail if the key is present. If a future v2+ migration drops or alters a table/trigger/index, `init_schema` will still run its v1 `CREATE TABLE/TRIGGER IF NOT EXISTS` statements on startup. This will either crash or silently recreate obsolete v1 objects.
*   **Mitigation:** Check if `schema_version` is present in the `config` table at the very beginning of `init_schema`. If it is present (i.e., `>= 1`), **return early immediately**. This matches `memory-engine`'s isolation (see [schema.rs:102-104](file:///home/mroynard/dev/memory-engine/src/store/schema.rs#L102-L104)).

#### [HIGH] Python `sqlite3` Implicit DDL Commits / Transaction Safety
*   **Plan Section:** Phase 2 `migrate(...)` transaction loop ([plan:L55](file:///home/mroynard/dev/knowledge-base/.worktrees/450-schema-version-migrate/docs/plans/2026-06-16-450-schema-version-migrate.md#L55))
*   **Risk:** Partial commits on migration failures/crashes.
*   **Why:** By default, Python's `sqlite3` library implicitly commits any active transaction *before* executing DDL statements (like `CREATE`, `DROP`, `ALTER`). If a migration executes multiple DDL statements, they will be committed individually. If a crash or error occurs mid-migration, `ROLLBACK` will do nothing, leaving the database half-migrated.
*   **Mitigation:** Set the connection's `isolation_level = None` (autocommit mode) on the migration connection before running migrations, and manage the transaction explicitly using SQL statements `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK`. This bypasses Python's implicit-commit parsing and forces SQLite to handle transactions atomically.

#### [MEDIUM] `restore_backup` Crash on In-Memory DBs
*   **Plan Section:** Phase 2 `migrate(...)` error handling ([plan:L55](file:///home/mroynard/dev/knowledge-base/.worktrees/450-schema-version-migrate/docs/plans/2026-06-16-450-schema-version-migrate.md#L55))
*   **Risk:** `TypeError` masks the original migration exception.
*   **Why:** For in-memory databases (e.g. `:memory:` in tests), `resolve_backup_path` returns `None` and backup is skipped. However, if a migration fails, the plan unconditionally calls `restore_backup(db_path, backup_path)`. Passing `None` to `restore_backup` will fail on file/path operations.
*   **Mitigation:** Guard the `restore_backup` call in the `except` block of `migrate` to only execute if `backup_path is not None`.

#### [LOW] `peek_schema_version` File Missing Error
*   **Plan Section:** Phase 3 `peek_schema_version` ([plan:L60](file:///home/mroynard/dev/knowledge-base/.worktrees/450-schema-version-migrate/docs/plans/2026-06-16-450-schema-version-migrate.md#L60))
*   **Risk:** `schema` or `migrate --check` commands crash on fresh installations.
*   **Why:** Opening a non-existent database file with `mode=ro` (read-only mode) will raise a `sqlite3.OperationalError` (unable to open database file) rather than returning gracefully.
*   **Mitigation:** Catch `sqlite3.OperationalError` (or check if `db_path.exists()` first) and return `None` when the file does not exist, letting the report gracefully show the DB as uninitialized.

#### [LOW] Robust `PRAGMA database_list` Parsing
*   **Plan Section:** Phase 1 `resolve_backup_path` ([plan:L48](file:///home/mroynard/dev/knowledge-base/.worktrees/450-schema-version-migrate/docs/plans/2026-06-16-450-schema-version-migrate.md#L48))
*   **Risk:** Incorrect database path returned if auxiliary databases are attached.
*   **Why:** `PRAGMA database_list` returns one row per attached database. Taking the first row blindly might return the temp or an attached database path.
*   **Mitigation:** Explicitly filter the list for the row where `name == 'main'` (the second column/element) to ensure the main database file is retrieved.

#### [LOW] Windows Path Separators in `VACUUM INTO`
*   **Plan Section:** Phase 2 `backup_database` ([plan:L53](file:///home/mroynard/dev/knowledge-base/.worktrees/450-schema-version-migrate/docs/plans/2026-06-16-450-schema-version-migrate.md#L53))
*   **Risk:** Malformed SQL literals on Windows due to backslashes (`\`).
*   **Why:** Backslashes inside single-quoted SQLite strings can cause escaping quirks depending on the environment.
*   **Mitigation:** Convert the backup path to a POSIX path via `backup_path.as_posix()` before escaping quotes and formatting the SQL string.

---

### 2. Over-engineering & Deferrals

#### [LOW] Unnecessary Import Cycle Precaution
*   **Plan Section:** Design Decision 3 ([plan:L16](file:///home/mroynard/dev/knowledge-base/.worktrees/450-schema-version-migrate/docs/plans/2026-06-16-450-schema-version-migrate.md#L16))
*   **Feedback:** The plan suggests that `migrate.py` imports `get_connection` lazily to avoid import cycles. However, `migrate.py` does not need `get_connection` at all. All database-mutating functions accept the connection object `conn` as an argument. The only function opening a connection is `peek_schema_version`, which should open a raw read-only connection via `sqlite3.connect` directly (avoiding loading `sqlite-vec` or running `init_schema`).
*   **Recommendation:** Remove all imports of `db.py` from `migrate.py`, rendering the module 100% independent and eliminating cycle concerns entirely.

#### Deferrals Evaluation
The proposed deferrals (retiring legacy `_migrate_*` calls, backup retention/GC, standalone binaries, and `STORAGE_EPOCH`) are **correct and highly pragmatic**. Since the v1→vN ladder is currently empty, adding the `STORAGE_EPOCH` or custom GC logic would be speculative generality.

---

### 3. Structural Completeness & Feasibility
*   **Phasing:** The phase sequence is highly logical, building and validating safety primitives (stamp, backup, restore-on-fail) in Phases 1 and 2 before exposing CLI wrappers in Phase 4.
*   **Testing:** The testing strategy is comprehensive and covers all critical points. The pytest monkeypatching strategy for testing the simulated v2 migration is sound.
*   **Documentation:** Adequate. The addition of the "Upgrading / migrating the database" section with the critical **"STOP the MCP server"** precondition is essential for operations.

---

### 4. Parity with Memory Engine
The plan closely mirrors `memory-engine`'s split behavior:
*   [validate_schema_version](file:///home/mroynard/dev/memory-engine/src/store/schema.rs#L290) and [peek_schema_version_from_db](file:///home/mroynard/dev/memory-engine/memory-engine-cli/src/db.rs#L42) are correctly translated into read-only Python counterparts.
*   The exit-code contract matches the CLI commands.
*   The deliberate divergence of adding explicit `restore-on-fail` (rather than relying on in-process transaction rollback) is well-justified because Python's DB-API does not guarantee atomic DDL rollback out of the box, and removing the WAL sidecars is required to prevent stale frames from replaying.

---

### Summary of Work Done
1.  Reviewed the implementation plan file: [2026-06-16-450-schema-version-migrate.md](file:///home/mroynard/dev/knowledge-base/.worktrees/450-schema-version-migrate/docs/plans/2026-06-16-450-schema-version-migrate.md).
2.  Inspected related lens drafts in `docs/plans/450-schema-version-migrate-drafts/`.
3.  Read the current SQLite initialization code in [db.py](file:///home/mroynard/dev/knowledge-base/.worktrees/450-schema-version-migrate/src/knowledge_base/db.py) and connection caching logic in [_conn.py](file:///home/mroynard/dev/knowledge-base/.worktrees/450-schema-version-migrate/src/knowledge_base/_conn.py).
4.  Analyzed target parity structures in `memory-engine` (`schema.rs`, `migrate.rs`, `db.rs`).
5.  Documented three blockers/high-risk findings (Fresh DB crash, lack of early-return in `init_schema`, and Python DDL transaction safety) and five lower-priority improvements. All files remain unmodified.
AGY_DONE exit=0


## Resolution

All findings folded into the plan's "Review resolution (round 1)" section + Design Decisions #3/#4/#6. Net: F1 (fresh-DB bootstrap), F2 (init_schema validate/early-return), F3 (isolation_level=None + explicit BEGIN), F4 (migrate.py db-independent), F5 (crash-safe restore + None-guard), F6 (str(v)), F7 (peek is_file + None semantics), F8 (database_list main filter + as_posix + busy_timeout). No redesign required.\n