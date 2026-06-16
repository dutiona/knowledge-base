# Clean-slate subagent review — KB #450 plan

**Verdict: LGTM with conditions.** No BLOCKERs; the design is correct (read-validates/writable-migrates split, risk-retirement-first sequencing, ME-parity traced to file:line). Findings are spec-tightening.

## Must-fix (consolidated)

1. **[HIGH]** init_schema's three callers (indexer `_get_conn`, `jobs.py:137` worker, `_conn.py:22` server) all share `init_schema` — confirm none takes a backup (the single idempotent UPSERT stamp covers all three).
2. **[HIGH]** `set_schema_version` must write `str(v)` — `config.value` is `TEXT NOT NULL`; getter parses `int(...)`. Matches ME `to_string()` (schema.rs:111).
3. **[MEDIUM]** Make `restore_backup` crash-safe (copy → `os.replace`, not delete → copy); state that `migrate()` owns closing the passed-in `conn` on failure (CLI must not touch it after — no `finally: conn.close()` that would double-close / re-create the WAL).
4. **[MEDIUM]** `peek`/`schema_report` must handle (a) a nonexistent file (`mode=ro` open raises) and (b) `schema_version=None` (config table present, row absent) without a traceback — add ME's `path.is_file()` precheck and define `None` semantics in the report (`matches=False`, "unstamped").
5. **[MEDIUM/feasibility]** Ensure `CURRENT_SCHEMA_VERSION` is monkeypatchable where `db.py` reads it (reference `migrate.CURRENT_SCHEMA_VERSION` via the module, not a frozen `from`-import copy) — else the `test_failed_migration_restores_backup` keystone can't simulate v2.

## LOW / advisory (all accepted)

- WAL pre-checkpoint is defense-in-depth (VACUUM INTO is WAL-safe regardless); don't assert-fail on `busy=1`.
- Empty `_MIGRATIONS` registry + `disable_fk` tuple + `foreign_key_check` harness: justified premature abstraction (ME parity, ~15 lines, exercised by the monkeypatched keystone test) — keep.
- Dedicated `migrate.py` module justified (db.py is 928 lines, 5 concerns); `STORAGE_EPOCH`/`_migrate_*`-retirement deferrals correctly scoped.

## Resolution

All five must-fix items + the LOW items folded into the plan's "Review resolution (round 1)" section and Design Decisions #3/#4/#6:

- (1) → F2/F4 note that all three `init_schema` callers share the cheap stamp; no backup on any hot path.
- (2) → F6 (`str(v)`).
- (3) → F5 (copy→`os.replace`; `migrate()` owns conn close; None-guard).
- (4) → F7 (`is_file()` precheck → None; report None = mismatch).
- (5) → Design Decision #3 (`db.py` reads `migrate.CURRENT_SCHEMA_VERSION` via the module attribute for monkeypatchability).
  No redesign required.
