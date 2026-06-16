# Plan: KB #450 — schema-version + backup-before-migrate framework

**Repo:** knowledge-base (Python) · **Issue:** #450 · **Worktree:** `.worktrees/450-schema-version-migrate`
**Parity target:** memory-engine `src/store/schema.rs` + `memory-engine-cli/src/commands/{migrate,schema}.rs` + `memory-engine-cli/src/db.rs`.

## BLUF

The feature is small; the **risk lives entirely in the seams**: WAL sidecar files vs. file-copy restore, stamping the existing unversioned production DB to v1 without a destructive re-migration, and the long-running MCP server holding the DB open while the CLI migrates. This plan retires the two CRITICAL hazards first (a restore that actually restores; a stamp that is provably non-destructive), then builds the version-report + CLI on top.

**Synthesis note** (three-lens drafts in `docs/plans/450-schema-version-migrate-drafts/`): risk ledger + sequencing from `risk-first`; the dedicated `migrate.py` module boundary from `architecture-first` (db.py is already 928 lines and conflates connection mgmt + DDL + 13 legacy builds + query helpers — the framework is a distinct concern, mirroring ME's isolation in `store/schema.rs`); empty-registry + simulated-v2 test + honest deferrals from `mvp-first`.

## Ratified design decisions

1. **Version storage:** `schema_version` row in the **existing `config` key/value table** (db.py:629), not a new table — matches ME (`config`, schema.rs:111).
2. **Scope = framework + baseline v1.** `CURRENT_SCHEMA_VERSION = 1` is the current schema. The existing ~13 idempotent `_migrate_*` builds (db.py:165-540, called at the tail of `init_schema`) **stay as the v1 baseline**. This issue ships the _machinery_ (stamp + backup-before-migrate + restore-on-fail + report), not a re-versioning of past migrations. The `_MIGRATIONS` registry ships **empty**; future schema changes are version-gated v2+.
3. **Module:** a new `src/knowledge_base/migrate.py` owns the framework and is **fully independent of `db.py`** (no import in either direction at the framework→db side). All framework functions take a `conn`/`db_path`; `peek_schema_version` opens its **own** raw read-only `sqlite3.connect` (no `sqlite_vec`, no `init_schema`). `db.py` imports `CURRENT_SCHEMA_VERSION` + `get_schema_version`/`set_schema_version` from `migrate.py` (one direction; no cycle, no lazy import). The CLI handler (`indexer.py`, which already imports `db`) orchestrates the one place both meet: bootstrapping a fresh DB via `db.init_schema` before calling `migrate.migrate`. `db.py` must read `migrate.CURRENT_SCHEMA_VERSION` **via the module attribute** (so tests can monkeypatch it for the simulated-v2 keystone test), not a frozen `from`-import copy. _(Review F4 + subagent-F5.)_
4. **`init_schema` validates-then-converges (read-validates split).** On every call it reads the version (tolerating a missing `config` table → `None`) and: `== CURRENT` → **early-return** (cheap, no rebuild); `> CURRENT` → raise `KnowledgeBaseError` (DB newer than this build); `< CURRENT` (latent — none below 1 today) → raise "run `migrate`" (never auto-migrate on this hot path); `None` (fresh or legacy-unversioned) → run the idempotent v1 builds (converge) then stamp `CURRENT`. It **never** takes a backup and **never** runs a v2+ chain — those live exclusively in the explicit, backed-up `migrate()` path. This mirrors ME's validate-on-read / migrate-on-write split (schema.rs:96-114, 290) and prevents a v2-vs-v1 rebuild hazard + the per-boot build cost. _(Review F2.)_
5. **Backup:** `VACUUM INTO` to `<db_parent>/backups/<stem>.<utc_ts>.v<from>.db` (beside the DB, not CWD-relative `data/backups`; matches ME db.rs:88), WAL-checkpointed first, atomic-renamed from `.partial`.
6. **Restore-on-fail (crash-safe):** `migrate()` owns closing the passed-in `conn` on the failure path (the CLI must not touch it after) → delete `<db>-wal`/`-shm`/`-journal` sidecars → `copyfile` backup to `<db>.restoring` → `os.replace(<db>.restoring, <db>)` (atomic; never a `delete-then-copy` window that could lose the DB if the process dies mid-restore) → re-raise. Guarded by `if backup_path is not None` (in-memory migrations skip backup+restore). KB's explicit restore is a **deliberate, stronger** divergence from ME (which relies on per-step transaction rollback and merely leaves the backup on disk) — the issue requires "a failed migrate restores the backup". _(Review F5.)_
7. **Migrate is offline** (operator stops the server first); `schema`/`--check` use a **read-only** peek so they report even against a busy DB.

## Risk ledger (lead with this — full detail in `draft-risk-first.md`)

| #   | Risk                                                                                                       | Sev  | Mitigation (summary)                                                                                          | Retired in  |
| --- | ---------------------------------------------------------------------------------------------------------- | ---- | ------------------------------------------------------------------------------------------------------------- | ----------- |
| R1  | Restore doesn't restore — stale `-wal`/`-shm` replays the partial migration                                | CRIT | close → delete sidecars → copyfile; WAL-checkpoint before backup                                              | Phase 2     |
| R2  | Stamping an unversioned prod DB triggers a destructive chain / double-applies a table-rebuild `_migrate_*` | CRIT | missing version → stamp **1** (never infer 0); `_migrate_*` stay self-guarding, never re-invoked as a chain   | Phase 1     |
| R3  | Backup location: `data/backups` is a CWD trap; `:memory:`/read-only parent                                 | HIGH | backup dir beside DB; resolve source via `PRAGMA database_list`; `:memory:` → skip backup; null-byte guard    | Phase 1     |
| R4  | Server holds DB open while CLI migrates → SQLITE_BUSY + stale cached schema                                | HIGH | migrate offline (documented precondition); short busy-timeout fail-fast; read-only peek for report            | Phase 3 + 5 |
| R5  | Partial/corrupt backup artifact                                                                            | MED  | VACUUM to `.partial` → integrity-check → atomic `os.replace`; restore only uses the in-memory backup-path var | Phase 2     |
| R6  | Crash mid-migration → ambiguous version                                                                    | MED  | per-step transaction with the `set_schema_version` bump **inside the tx**; FK re-enable in `finally`          | Phase 2     |
| R7  | `init_schema` is on every CLI/server hot path → must not back up                                           | MED  | strict split: `init_schema` only stamps; backup+migrate only in `migrate()`                                   | Phase 3     |
| R8  | sqlite-vec not loaded on migrate connection (`vec0` tables)                                                | LOW  | writable migrate reuses `get_connection` (loads vec); read-only peek selects only from `config`               | Phase 3     |

## Implementation (sequenced to retire risk earliest)

### Phase 0 — skeleton, no behavior change

- [ ] Confirm scope in an issue #450 comment (offline migrate; backup beside DB; `CURRENT_SCHEMA_VERSION=1`; v1 baseline = existing `_migrate_*`).
- [ ] Create `src/knowledge_base/migrate.py` with `CURRENT_SCHEMA_VERSION = 1`, `SCHEMA_VERSION_KEY = "schema_version"`, and an empty `_MIGRATIONS: list[tuple[Callable[[sqlite3.Connection], None], bool]] = []` registry (callable, `disable_fk`). Add module docstring citing ME parity.
- [ ] Green baseline: `uv run pytest tests/test_db.py tests/test_indexer.py -q`.

### Phase 1 — versioning + stamp + backup-path (retires R2, R3)

- [ ] `get_schema_version(conn) -> int | None` — `SELECT value FROM config WHERE key='schema_version'`; parse int; `None` if absent; raise `KnowledgeBaseError` on non-integer (ME schema.rs:64).
- [ ] `set_schema_version(conn, v)` — upsert `INSERT ... ON CONFLICT(key) DO UPDATE` (config is `(key PRIMARY KEY, value)`, db.py:629).
- [ ] Wire `db.py:init_schema` to call `set_schema_version(conn, CURRENT_SCHEMA_VERSION)` **only if the key is missing**, at the tail (after the existing `_migrate_*`). Import the constant + setter from `migrate.py`. **R2:** both fresh and legacy DBs converge to v1, never 0.
- [ ] `resolve_backup_path(conn, backup_dir=None) -> Path | None` — source via `PRAGMA database_list`; `:memory:`/empty → `None`; else `(backup_dir or Path(src).parent/"backups")` (mkdir) `/ f"{stem}.{utc_ts}.v{from}.db"`; null-byte guard.
- [ ] **Tests** (extend `tests/test_db.py`): `test_fresh_db_stamps_current_version`; `test_legacy_unversioned_db_stamps_to_v1_nondestructively` (build pre-v1 DDL+data via existing fixtures, snapshot `COUNT(*)`+a `content_hash`, run `init_schema`, assert version==1 AND counts/hashes unchanged — **R2**); `test_already_versioned_db_untouched`; `test_backup_path_{beside_db_default,memory_returns_none,rejects_null_byte}` (**R3**).

### Phase 2 — backup + restore-on-fail core (retires R1, R5, R6)

- [ ] `backup_database(conn, backup_path) -> Path`: `PRAGMA wal_checkpoint(TRUNCATE)` → remove existing target → VACUUM INTO `<path>.partial` (escape `'`→`''`) → open `.partial` read-only, `PRAGMA integrity_check` + read its `schema_version` → `os.replace(.partial, path)`.
- [ ] `restore_backup(db_path, backup_path)`: precondition connection closed → delete `db_path` + `-wal`/`-shm`/`-journal` → `shutil.copyfile(backup_path, db_path)`. **The sidecar deletion is the R1 fix.**
- [ ] `migrate(conn, *, backup_dir=None, dry_run=False) -> dict` (ME schema.rs:200-277): read live version (stamp-1-if-missing rule); `pending = range(live+1, CURRENT+1)`; if `dry_run` or empty → report, no mutation; else `backup_path = backup_database(...)`, then per `(fn, disable_fk)` in `_MIGRATIONS`: in a transaction run `fn`, optional `PRAGMA foreign_key_check`, `set_schema_version(tx, target)` **inside the tx**, commit; `finally` re-enable FK. On any exception: `conn.close()` → `restore_backup` → re-raise. Returns `{from, to, pending, backup_path, applied}`.
- [ ] **Tests** (new `tests/test_schema_migrate.py`): `test_backup_creates_timestamped_file_beside_db` (synthetic pending so backup runs — AC-2); `test_backup_atomic_rename_no_partial_on_failure` (R5); `test_backup_is_self_contained_no_wal_replay` (R1); `test_failed_migration_restores_backup` — monkeypatch `_MIGRATIONS=[(boom, False)]` + temporary `CURRENT=2`, seed data, run `migrate`, assert (a) data intact, (b) version==1 (NOT advanced), (c) **no `-wal`/`-shm` orphan**, (d) restored DB `integrity_check==ok`, (e) FK enforcement ON — **R1+R6 keystone, AC-4**.

### Phase 3 — version-report + read-only peek + separation (retires R4-read, R7, R8)

- [ ] `peek_schema_version(db_path) -> int | None` — open `file:{path}?mode=ro` (uri=True), `SELECT value FROM config WHERE key='schema_version'`; clear error if no `config` table (ME db.rs:42-66). No vec load (R8). The release-gate verify primitive — **never creates/mutates the DB**.
- [ ] `schema_report(db_path) -> dict` — `{schema_version, current_schema_version, matches, newer}` (`newer = live > CURRENT`; ME schema.rs).
- [ ] **Tests:** `test_peek_readonly_does_not_create_wal` (assert no `-wal` materializes, mtime unchanged — R4/R7); `test_schema_report_matches_and_mismatch`; `test_init_schema_creates_no_backup` (R7).

### Phase 4 — CLI `migrate` + `schema` subcommands (design point d)

- [ ] `cmd_schema(args)` in indexer.py: `schema_report(args.db)` → print → **exit non-zero on mismatch OR newer** (release-gate contract, ME schema.rs:50). Uses the read-only peek, NOT `_get_conn` (which force-runs `init_schema`).
- [ ] `cmd_migrate(args)`: `--check` → `peek` + print pending → **exit non-zero if pending non-empty** (ME migrate.rs:114), no mutation. Else `get_connection(args.db)` (loads vec, R8) → `migrate(conn, backup_dir=args.backup_dir)` → print report incl. backup path → exit 0. On `KnowledgeBaseError` → `_print_error`, exit 1 (restore already happened inside `migrate`). **NEWER guard:** `live > CURRENT` → non-zero (a rollback DB must never read "nothing to do").
- [ ] Parser: `migrate` subparser (`--check` store_true, `--backup-dir` Path default None) + `schema` subparser; register in `build_parser` + `dispatch` (indexer.py).
- [ ] **Tests** (extend `tests/test_indexer.py`, reuse `_make_args`/`_setup`): `test_cli_schema_reports_version`; `test_cli_schema_mismatch_exits_nonzero`; `test_cli_migrate_check_up_to_date_exit_zero` (no backup created); `test_cli_migrate_check_pending_exit_nonzero` (no mutation); `test_cli_migrate_creates_backup`; extend the subcommand-presence test.

## Documentation

- [ ] This plan committed at `docs/plans/2026-06-16-450-schema-version-migrate.md`.
- [ ] README / docs: an **"Upgrading / migrating the database"** section — `schema` reports live-vs-current (exit non-zero on mismatch, CI/release-gate usable); `migrate --check` is a dry run; `migrate` backs up beside the DB then migrates, restore-on-fail; **STOP the MCP server before `migrate`** (R4); backup location + no auto-pruning.
- [ ] Module + function docstrings cite the ME parity functions (schema.rs, migrate.rs) for cross-repo discoverability.

## Testing

- Unit + integration via `uv run pytest` — new `tests/test_schema_migrate.py`, extensions to `tests/test_db.py` + `tests/test_indexer.py`. All use `tmp_path` (never the real `~/.local/share` DB).
- AC coverage: AC-1 fresh→version → `test_fresh_db_stamps_current_version`; AC-2 backup-before-mutate → `test_backup_creates_timestamped_file_beside_db` / `test_cli_migrate_creates_backup`; AC-3 mismatch reportable → `test_schema_report_matches_and_mismatch` / `test_cli_schema_mismatch_exits_nonzero`; AC-4 failed-migrate-restores → `test_failed_migration_restores_backup`.
- Cross-cutting property: after any op (init / migrate / failed-restore), `PRAGMA integrity_check == ok` and **no `-wal`/`-shm` orphan** once connections close.
- E2E: N/A (offline, embedding-free; CLI smoke in Verification covers the user path).

## Verification

```bash
cd .worktrees/450-schema-version-migrate
uv run ruff format --check . && uv run ruff check .
uv run pyright src/knowledge_base/migrate.py src/knowledge_base/db.py src/knowledge_base/indexer.py
uv run pytest tests/test_db.py tests/test_indexer.py tests/test_schema_migrate.py -q
uv run pytest -q          # full suite, no regressions (DB-touching modules)
# Manual smoke (throwaway DB; no network/Ollama):
TMPDB=$(mktemp -d)/k.db
uv run knowledge-base-ingest --db "$TMPDB" status          # creates+stamps fresh DB at v1
uv run knowledge-base-ingest --db "$TMPDB" schema; echo "exit=$? (expect 0, matches)"
uv run knowledge-base-ingest --db "$TMPDB" migrate --check; echo "exit=$? (expect 0, no pending)"
uv run python -c "import sqlite3; c=sqlite3.connect('$TMPDB'); c.execute(\"UPDATE config SET value='0' WHERE key='schema_version'\"); c.commit()"
uv run knowledge-base-ingest --db "$TMPDB" schema; echo "exit=$? (expect nonzero, mismatch)"
```

## Review resolution (round 1 — subagent + agy + gemini; advisor unavailable in env)

All three reviewers returned **LGTM-with-conditions, no redesign**. Binding amendments to the phases above (each implementation task inherits these):

- **F1 [BLOCKER, agy] — `migrate` on a fresh/non-existent DB crashes** (`SELECT … FROM config` → no such table). **Fix (Phase 4):** `get_schema_version(conn)` catches "no such table: config" → returns `None`. `cmd_migrate` opens via `get_connection` (no `init_schema`), then: if `get_schema_version is None` → call `db.init_schema(conn)` (builds baseline + stamps v1; 0 pending) and report "bootstrapped"; else → `migrate(conn, …)`. So `migrate` on a fresh DB initializes it; `migrate --check`'s `peek` returns `None` → reported as "uninitialized", exit non-zero.
- **F2 [HIGH, agy+gemini] — `init_schema` validate-then-converge** — folded into Design Decision #4 above (Phase 1). The Phase-1 task is amended: `init_schema` early-returns on `==CURRENT`, raises on `>CURRENT`, raises "run migrate" on `<CURRENT`, and only builds+stamps on `None`. Add tests `test_init_schema_early_returns_when_current` and `test_init_schema_raises_on_newer_db`.
- **F3 [HIGH, agy] — Python `sqlite3` implicitly commits before DDL** → a multi-DDL migration isn't atomic under the default `isolation_level`. **Fix (Phase 2):** the `migrate()` connection sets `conn.isolation_level = None` (autocommit) and the per-migration step manages the transaction explicitly: `BEGIN IMMEDIATE` → `fn(conn)` → optional `PRAGMA foreign_key_check` → `set_schema_version(conn, target)` → `COMMIT`; on exception `ROLLBACK` then restore+re-raise; `finally` re-enable FK. Add `test_migration_failure_is_atomic_no_partial_ddl`.
- **F4 [HIGH-clarity, all] — `migrate.py` fully independent of `db.py`** — folded into Design Decision #3. The lazy import is dropped; the CLI orchestrates the lone `init_schema` call (F1).
- **F5 [MED, subagent+agy] — crash-safe restore + None-guard + conn ownership** — folded into Design Decision #6 (Phase 2). `restore_backup` does `copyfile → os.replace`; `migrate()` owns `conn.close()` on failure; guarded `if backup_path is not None`.
- **F6 [HIGH, subagent] — `set_schema_version` writes `str(v)`** (config.value is `TEXT NOT NULL`; getter already `int(...)`-parses). Phase 1.
- **F7 [MED, subagent+agy] — `peek`/`schema_report` tolerate missing file + `version=None`.** **Fix (Phase 3):** `peek_schema_version` prechecks `db_path.is_file()` → `None` if absent (no `mode=ro` traceback); `schema_report` treats `None` as `matches=False` with a clear "uninitialized — run migrate/init" message, never arithmetic on `None`. Add `test_peek_missing_file_returns_none` + `test_schema_report_unstamped_db`.
- **F8 [LOW, agy+gemini] — robustness:** `resolve_backup_path` filters `PRAGMA database_list` for the row where `name == 'main'` (not row 0); the VACUUM-INTO path uses `Path(...).as_posix()` before `'`→`''` escaping (Windows backslash safety); the migrate connection sets a short `busy_timeout` (5000ms, ME parity) so a forgotten-running-server surfaces fast instead of a 30s stall. The pre-backup `wal_checkpoint(TRUNCATE)` is documented as **defense-in-depth** (VACUUM INTO is WAL-safe regardless) — do **not** assert-fail on a `busy=1` checkpoint result.

Review artifacts saved: `docs/plans/450-schema-version-migrate-subagent-review.md`, `…-agy-review.md`, `…-gemini-review.md`.

## Deferrals (honest — none required by the ACs; recommended follow-ups)

- `STORAGE_EPOCH` (ME has one; KB has no second epoch — speculative now; verify/peek already read `config`, leaving room).
- Retiring the unconditional `_migrate_*` calls (re-platform them as version-gated v1 migrations) — large, separate.
- Backup retention/GC, multi-format CLI output, a standalone migrate binary / MCP-surfaced migrate.
- The `foreign_key_check` harness is wired but dead until the first real table-rebuild v2 migration exists.

## Git ops

- [ ] Comment scope decisions on issue #450.
- [ ] Publish this plan verbatim as a GitHub issue; reference it in the PR.
- [ ] Atomic Conventional commits, roughly per phase (feat(db) stamp; feat(db) backup/restore migrate; feat(db) peek/report; feat(cli) migrate+schema; docs).
- [ ] PR to `master`, title `feat: schema-version + backup-before-migrate framework (#450)`; body links #450, the risk ledger, the ME-parity map, the offline-migrate operator note.
- [ ] `/super-review` (multi-model: advisor + agy + gemini, **no codex** — rate-limited). Flag the 3 load-bearing seams: restore-on-fail WAL-sidecar deletion (R1), stamp-unversioned→1 (R2), init_schema-vs-migrate separation (R7).
- [ ] Address threads, re-run Verification, resolve all threads. Squash-merge to `master`; delete worktree/branch.

## Appendix — ME parity map

| KB (this plan)                                         | ME parity                                | ME file:line               |
| ------------------------------------------------------ | ---------------------------------------- | -------------------------- |
| `CURRENT_SCHEMA_VERSION = 1`                           | `CURRENT_SCHEMA_VERSION: u32 = 11`       | schema.rs:8                |
| `schema_version` in `config`                           | same table+key                           | schema.rs:111, 157-164     |
| stamp fresh / stamp-missing→current                    | `init_schema` + migrate `unwrap_or("1")` | schema.rs:96-114, 201      |
| `backup_database` VACUUM INTO + guards                 | `backup_before_migration`                | schema.rs:364-413          |
| per-migration tx + in-tx version stamp + FK finally    | `migrate` loop                           | schema.rs:241-268          |
| `_MIGRATIONS` `(fn, disable_fk)`                       | `MIGRATIONS`                             | schema.rs:172-183          |
| `peek_schema_version` read-only                        | `peek_schema_version_from_db`            | db.rs:42-66                |
| `schema_report {version,current,matches,newer}`        | `SchemaReport`                           | commands/schema.rs:11-15   |
| `migrate --check` exit-nonzero-on-pending; NEWER guard | `MigrateReport` + exit codes             | commands/migrate.rs:46-122 |
| backup dir = `<db>.parent/"backups"`                   | `path.parent()`                          | db.rs:88                   |

## Post-Implementation Audit

All phase tasks **Implemented**; no drops, no incomplete items. Deviations (all same-intent, not modifications):
- **Module name:** chose `migrate.py` (per the architecture-first lens) — the risk-first draft hedged `schema_version.py` vs db.py; settled on the dedicated, db-independent module.
- **`_MIGRATIONS` shape:** a `dict[int, (fn, disable_fk)]` keyed by target version (plan said `list[tuple]`) — cleaner indexing; same registry semantics.
- **F1–F8** (review round 1) all implemented: F1 fresh-DB bootstrap + `get_schema_version` tolerates missing `config`; F2 `init_schema` validate/early-return/raise; F3 `isolation_level=None`+explicit `BEGIN IMMEDIATE`; F4 `migrate.py` fully db-independent; F5 crash-safe `copy→os.replace` restore + None-guard + conn ownership; F6 `str(v)` stamp; F7 `peek`/`report` tolerate missing-file/None; F8 `database_list` main-row filter + `as_posix` + `busy_timeout=5000`.
- **Test fix:** `test_migrate_normalize_source_uri` rewired to call the migration directly (the old "re-run init_schema to trigger" pattern is gone now that init_schema early-returns) — motivated by F2.

Verification: `ruff format --check` + `ruff check` + `pyright` clean on changed files; 548 passed across the DB/schema suite (10 failures are pre-existing LLM-endpoint/httpx env failures, reproduced on clean `origin/master`); 66 changed-area tests green.
