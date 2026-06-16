"""Schema versioning + backup-before-migrate framework (#450).

Parity with memory-engine ``src/store/schema.rs`` +
``memory-engine-cli/src/commands/{migrate,schema}.rs`` + ``db.rs``: the
read path *validates* the version (see ``db.init_schema``) and the explicit,
backed-up ``migrate()`` path *mutates*. The schema version lives in the existing
``config`` key/value table under ``schema_version``.

This module is intentionally **independent of ``db.py``** (no import in this
direction): every function takes a ``conn``/``db_path``; ``peek_schema_version``
opens its own raw read-only connection. ``db.py`` imports the constant + the
``get_schema_version``/``set_schema_version`` helpers from here (one direction,
no cycle). ``db.py`` reads ``migrate.CURRENT_SCHEMA_VERSION`` via the module
attribute so tests can monkeypatch it.

The ``_MIGRATIONS`` registry ships **empty**: ``CURRENT_SCHEMA_VERSION = 1`` is
the current schema, whose definition is the idempotent builds in
``db.init_schema``. Future schema changes are version-gated v2+ — one registry
entry + one function.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .exceptions import KnowledgeBaseError

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "SCHEMA_VERSION_KEY",
    "backup_database",
    "get_schema_version",
    "migrate",
    "peek_schema_version",
    "resolve_backup_path",
    "restore_backup",
    "schema_report",
    "set_schema_version",
]

#: The schema version this build targets. A fresh DB is stamped with this; the
#: read path validates against it.
CURRENT_SCHEMA_VERSION = 1

#: `config` key under which the applied schema version is stored.
SCHEMA_VERSION_KEY = "schema_version"

#: Migration type: ``(fn, disable_fk)`` — ``fn(conn)`` performs the DDL for one
#: version step, ``disable_fk`` toggles ``PRAGMA foreign_keys`` around it (for
#: table-rebuild migrations). Keyed by the **target** version (the one it
#: produces), so `_MIGRATIONS[2]` migrates v1 → v2.
Migration = tuple[Callable[[sqlite3.Connection], None], bool]
_MIGRATIONS: dict[int, Migration] = {}


# --- version read/write on the config table ------------------------------------


def get_schema_version(conn: sqlite3.Connection) -> int | None:
    """Read the applied schema version, or ``None`` if unstamped.

    Tolerates a missing ``config`` table (a brand-new DB) → ``None``. Raises
    :class:`KnowledgeBaseError` if the stored value is not an integer.
    """
    try:
        row = conn.execute(
            f"SELECT value FROM config WHERE key = '{SCHEMA_VERSION_KEY}'"
        ).fetchone()
    except sqlite3.OperationalError:
        # No `config` table yet — treat as unversioned.
        return None
    if row is None:
        return None
    raw = row[0]
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise KnowledgeBaseError(f"corrupt schema_version in config: {raw!r}") from exc


def set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    """Upsert the schema version. Stored as TEXT (config.value is ``TEXT NOT NULL``)."""
    conn.execute(
        "INSERT INTO config (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (SCHEMA_VERSION_KEY, str(version)),
    )


# --- backup path resolution ----------------------------------------------------


def _main_db_path(conn: sqlite3.Connection) -> Path | None:
    """The on-disk path of the connection's ``main`` database, or ``None`` for
    an in-memory / transient DB."""
    for _seq, name, file in conn.execute("PRAGMA database_list"):
        if name == "main":
            return Path(file) if file else None
    return None


def resolve_backup_path(
    conn: sqlite3.Connection, backup_dir: Path | None = None
) -> Path | None:
    """Where to write the pre-migration backup, or ``None`` if the DB is in-memory.

    Defaults beside the live DB (``<db_parent>/backups/``), NOT a CWD-relative
    ``data/backups`` (matches ME ``db.rs:88``). Filename carries a UTC timestamp
    + the from-version. Rejects a null byte in the resolved path.
    """
    src = _main_db_path(conn)
    if src is None:
        return None
    directory = backup_dir if backup_dir is not None else src.parent / "backups"
    from_version = get_schema_version(conn) or 0
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = Path(directory) / f"{src.stem}.{ts}.v{from_version}.db"
    if "\x00" in str(target):
        raise KnowledgeBaseError(f"invalid backup path (null byte): {target!r}")
    directory.mkdir(parents=True, exist_ok=True)
    return target


# --- backup + restore ----------------------------------------------------------


def _sqlite_str_literal(path: Path) -> str:
    """A safely single-quoted SQLite string literal for a path (VACUUM INTO
    cannot be parameterized). POSIX-normalized for Windows backslash safety."""
    return "'" + path.as_posix().replace("'", "''") + "'"


def backup_database(conn: sqlite3.Connection, backup_path: Path) -> Path:
    """Take a WAL-safe, self-contained backup at ``backup_path`` via VACUUM INTO.

    Checkpoints the WAL first (defense-in-depth; VACUUM INTO snapshots
    consistently regardless), writes to a ``.partial`` then atomically renames,
    and integrity-checks the result before returning.
    """
    # Defense-in-depth: empty the WAL so a manual file copy elsewhere stays
    # consistent. A `busy=1` result is harmless (VACUUM INTO is WAL-safe).
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    tmp = backup_path.with_name(backup_path.name + ".partial")
    tmp.unlink(missing_ok=True)
    backup_path.unlink(missing_ok=True)
    conn.execute(f"VACUUM INTO {_sqlite_str_literal(tmp)}")

    # Verify the backup opens clean before we trust it.
    check = sqlite3.connect(f"file:{tmp.as_posix()}?mode=ro", uri=True)
    try:
        ok = check.execute("PRAGMA integrity_check").fetchone()
        if not ok or ok[0] != "ok":
            raise KnowledgeBaseError(f"backup failed integrity_check: {ok}")
    finally:
        check.close()

    os.replace(tmp, backup_path)
    return backup_path


def restore_backup(db_path: Path, backup_path: Path) -> None:
    """Atomically restore ``backup_path`` over ``db_path``.

    Precondition: the migrating connection is already closed. Deletes the live
    WAL sidecars (a stale ``-wal``/``-shm`` would otherwise replay the partial
    migration on next open), then copies the backup to a temp name and
    ``os.replace``-s it into place (never a delete-then-copy window).
    """
    for sidecar in ("-wal", "-shm", "-journal"):
        Path(str(db_path) + sidecar).unlink(missing_ok=True)
    tmp = db_path.with_name(db_path.name + ".restoring")
    shutil.copyfile(backup_path, tmp)
    os.replace(tmp, db_path)


# --- the migrate orchestrator --------------------------------------------------


def migrate(
    conn: sqlite3.Connection,
    *,
    backup_dir: Path | None = None,
    dry_run: bool = False,
) -> dict:
    """Apply pending version-gated migrations with backup + restore-on-fail.

    Assumes a **stamped** DB (the CLI bootstraps a fresh DB via ``init_schema``
    first). Reads the live version; for each pending target runs its registry
    entry in an explicit transaction (``isolation_level=None`` + ``BEGIN
    IMMEDIATE`` — Python's sqlite3 otherwise implicit-commits before DDL,
    defeating atomicity), stamping the version **inside** the same transaction.
    On any failure: rollback → close → restore the backup → re-raise.
    """
    current = CURRENT_SCHEMA_VERSION
    live = get_schema_version(conn)
    if live is None:
        raise KnowledgeBaseError(
            "database is unversioned; initialize it first (init_schema)"
        )
    if live > current:
        raise KnowledgeBaseError(
            f"database schema v{live} is newer than this build (v{current}); "
            "upgrade the code"
        )
    pending = list(range(live + 1, current + 1))
    report: dict = {
        "from_version": live,
        "to_version": current,
        "pending": pending,
        "backup_path": None,
        "applied": [],
    }
    if dry_run or not pending:
        return report

    backup_path = resolve_backup_path(conn, backup_dir)
    if backup_path is not None:
        backup_database(conn, backup_path)
        report["backup_path"] = str(backup_path)
    db_path = _main_db_path(conn)

    orig_iso = conn.isolation_level
    conn.isolation_level = None  # autocommit; we manage transactions explicitly
    try:
        for target in pending:
            fn, disable_fk = _MIGRATIONS[target]
            if disable_fk:
                conn.execute("PRAGMA foreign_keys=OFF")
            try:
                conn.execute("BEGIN IMMEDIATE")
                fn(conn)
                if disable_fk:
                    bad = conn.execute("PRAGMA foreign_key_check").fetchall()
                    if bad:
                        raise KnowledgeBaseError(
                            f"foreign_key_check failed migrating to v{target}: {bad}"
                        )
                set_schema_version(conn, target)
                conn.execute("COMMIT")
            finally:
                if disable_fk:
                    conn.execute("PRAGMA foreign_keys=ON")
            report["applied"].append(target)
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        conn.close()
        if backup_path is not None and db_path is not None:
            restore_backup(db_path, backup_path)
        raise
    else:
        conn.isolation_level = orig_iso
    return report


# --- read-only version reporting (release-gate verify) -------------------------


def peek_schema_version(db_path: Path) -> int | None:
    """Read the schema version from ``db_path`` **read-only**, without creating
    or mutating it. ``None`` if the file does not exist or is unstamped."""
    path = Path(db_path)
    if not path.is_file():
        return None
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return get_schema_version(conn)
    finally:
        conn.close()


def schema_report(db_path: Path) -> dict:
    """Report live-vs-current version for the release-gate.

    ``matches`` is True only when the DB is stamped at exactly
    ``CURRENT_SCHEMA_VERSION``; an unstamped/absent DB (``None``) reports
    ``matches=False`` (uninitialized). ``newer`` flags a DB from a future build.
    """
    live = peek_schema_version(db_path)
    current = CURRENT_SCHEMA_VERSION
    return {
        "schema_version": live,
        "current_schema_version": current,
        "matches": live == current,
        "newer": live is not None and live > current,
    }
