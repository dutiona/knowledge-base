"""Standalone CLI for knowledge-base indexing operations.

Wraps the same domain functions used by the MCP server routes, but runs
as a short-lived batch process rather than a long-running stdio server.
Designed for cron jobs, CI pipelines, and manual bulk ingestion.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path

import httpx

from . import migrate as _mig
from .db import get_connection, init_schema, open_declared_db, resolve_db_path
from .exceptions import KnowledgeBaseError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_conn(db_path: Path) -> sqlite3.Connection:
    """The embedding commands' connection: through the startup gate (#569)."""
    return open_declared_db(db_path)


def _drain_jobs(
    conn: sqlite3.Connection,
    job_ids: list[int],
    timeout: float = 300.0,
) -> None:
    """Block until the specified *job_ids* are no longer pending/running."""
    if not job_ids:
        return
    placeholders = ",".join("?" for _ in job_ids)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM jobs WHERE id IN ({placeholders}) AND status IN ('pending', 'running')",  # noqa: S608  # placeholders are only '?' marks; values bound via params
            job_ids,
        ).fetchone()
        if row["n"] == 0:
            return
        time.sleep(1.0)
    logger.warning("Job drain timed out after %.0fs", timeout)


def _print(data: object, *, quiet: bool = False) -> None:
    if not quiet:
        print(json.dumps(data, indent=2))


def _print_error(data: object) -> None:
    """Always print errors, even in quiet mode."""
    print(json.dumps(data, indent=2), file=sys.stderr)


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def cmd_ingest(args: argparse.Namespace) -> None:
    from .ingest import ingest_directory, ingest_file

    conn = _get_conn(args.db)
    p = Path(args.path).expanduser().resolve()

    if not p.exists():
        _print_error({"error": f"Path does not exist: {p}"})
        sys.exit(1)

    if p.is_dir():
        if args.source_type is not None:
            _print_error({"error": "--source-type is not supported for directories"})
            sys.exit(1)
        results = ingest_directory(conn, p, session_id=args.session_id)
        total_added = sum(r["chunks_added"] for r in results)
        total_skipped = sum(r["chunks_skipped"] for r in results)
        _print(
            {
                "files_processed": len(results),
                "chunks_added": total_added,
                "chunks_skipped": total_skipped,
                "details": results,
            },
            quiet=args.quiet,
        )
    else:
        result = ingest_file(conn, p, args.source_type, session_id=args.session_id)
        _print(result, quiet=args.quiet)


def cmd_reingest(args: argparse.Namespace) -> None:
    from .ingest import reingest_file
    from .jobs import submit_job

    conn = _get_conn(args.db)
    p = Path(args.path).expanduser().resolve()

    if not p.exists():
        _print_error({"error": f"Path does not exist: {p}"})
        sys.exit(1)

    result = reingest_file(conn, p, args.source_type, session_id=args.session_id)

    # Post-reingest: invalidate stale "similar" relationships and submit
    # auto_relate jobs for affected papers (mirrors routes/ingestion.py:83-99).
    source_uri = p.as_posix()
    affected = conn.execute("SELECT paper_id FROM paper_paths WHERE path = ?", (source_uri,)).fetchall()
    submitted_job_ids: list[int] = []
    if affected:
        for row in affected:
            pid = row["paper_id"]
            conn.execute(
                "DELETE FROM relationships WHERE relation_type = 'similar' "
                "AND (source_paper_id = ? OR target_paper_id = ?)",
                (pid, pid),
            )
            jid = submit_job(conn, pid, "auto_relate", {"paper_id": pid})
            submitted_job_ids.append(jid)
        conn.commit()
        _drain_jobs(conn, submitted_job_ids)

    _print(result, quiet=args.quiet)


def cmd_ingest_url(args: argparse.Namespace) -> None:
    from .web import ingest_url

    conn = _get_conn(args.db)
    result = ingest_url(conn, args.url, session_id=args.session_id)
    _print(result, quiet=args.quiet)


def cmd_re_embed(args: argparse.Namespace) -> None:
    """Re-embed to the declared identity (#569): set the new KNOWLEDGE_BASE_EMBED_* first, unit stopped."""
    from .embed_swap import re_embed

    conn = open_declared_db(args.db, transition="re-embed")
    result = re_embed(conn, batch_size=args.batch_size)
    _print(result, quiet=args.quiet)


def cmd_rebind(args: argparse.Namespace) -> None:
    """Record a moved endpoint, or repair drifted config identity rows (#569); unit stopped."""
    open_declared_db(args.db, transition="rebind").close()
    _print({"rebound": str(args.db)}, quiet=args.quiet)


def cmd_status(args: argparse.Namespace) -> None:
    from .embed_swap import get_embed_config

    conn = get_connection(args.db)  # read-only report: no declaration needed
    init_schema(conn)

    chunks = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
    sources = conn.execute("SELECT COUNT(DISTINCT source_uri) AS n FROM chunks").fetchone()["n"]
    papers = conn.execute("SELECT COUNT(*) AS n FROM papers").fetchone()["n"]

    job_rows = conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
    jobs = {row["status"]: row["n"] for row in job_rows}

    config = get_embed_config(conn)
    config.pop("api_key", None)

    _print(
        {
            "chunks": chunks,
            "sources": sources,
            "papers": papers,
            "jobs": jobs,
            "embedding": config,
        },
        quiet=args.quiet,
    )


def cmd_redact(args: argparse.Namespace) -> None:
    """Report stored rows that hold a secret (#592); ``--rewrite`` (server stopped) redacts them."""
    from .redact_store import needs_rewrite, report, rewrite

    if not args.db.is_file():  # a mistyped path would otherwise be created, and reported clean
        raise KnowledgeBaseError(f"no database at {args.db}")
    if args.rewrite:
        conn = open_declared_db(args.db)  # it re-embeds: the declared embedder, as re-embed
    else:
        conn = get_connection(args.db)  # the report only reads: no declaration needed
        init_schema(conn)
    try:
        result = rewrite(conn) if args.rewrite else report(conn)
    finally:
        conn.close()  # a held connection is what --rewrite refuses
    _print(result, quiet=args.quiet)
    sys.exit(1 if needs_rewrite(result) else 0)


def cmd_schema(args: argparse.Namespace) -> None:
    """Report live-vs-current schema version. Exit non-zero on any mismatch
    (release-gate verify, ME parity). Read-only — never creates/mutates the DB."""
    report = _mig.schema_report(args.db)
    _print(report, quiet=args.quiet)
    sys.exit(0 if report["matches"] else 1)


def cmd_migrate(args: argparse.Namespace) -> None:
    """Apply pending schema migrations (backs up first). ``--check`` is a dry run
    that exits non-zero unless the DB is already at the current version."""
    current = _mig.CURRENT_SCHEMA_VERSION
    if args.check:
        live = _mig.peek_schema_version(args.db)
        pending = list(range(live + 1, current + 1)) if live is not None else []
        _print(
            {
                "schema_version": live,
                "current_schema_version": current,
                "pending": pending,
                "newer": live is not None and live > current,
            },
            quiet=args.quiet,
        )
        sys.exit(0 if live == current else 1)

    backup_dir = args.backup_dir.expanduser() if args.backup_dir is not None else None
    conn = get_connection(args.db)  # raw open — does NOT run init_schema/validation
    try:
        # Fail fast (not a 30s stall) if a forgotten-running server holds the lock.
        conn.execute("PRAGMA busy_timeout=5000")
        if _mig.get_schema_version(conn) is None:
            # Fresh / unversioned DB: bootstrap the baseline (builds + stamps). A
            # brand-new empty DB (no `config` table) has nothing to lose; an
            # existing unversioned/legacy DB (config present, data possible) is
            # backed up first so a failed convergent build is recoverable.
            backup_path = None
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='config'").fetchone():
                bp = _mig.resolve_backup_path(conn)
                if bp is not None:
                    backup_path = _mig.backup_database(conn, bp)
            init_schema(conn)
            _print(
                {
                    "bootstrapped": True,
                    "schema_version": current,
                    "backup_path": str(backup_path) if backup_path else None,
                    "applied": [],
                },
                quiet=args.quiet,
            )
            return
        # Existing stamped DB: run pending migrations under backup + restore-on-fail.
        report = _mig.migrate(conn, backup_dir=backup_dir)
        _print(report, quiet=args.quiet)
    finally:
        conn.close()  # idempotent; migrate() may have already closed it on failure


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knowledge-base-ingest",
        description="Batch indexing CLI for the knowledge-base.",
    )
    parser.add_argument(
        "--db-path",
        "--db",
        dest="db",
        type=Path,
        default=None,
        help=(
            "Path to the SQLite database. Precedence: this flag > "
            "$KNOWLEDGE_BASE_DB > the default "
            "(~/.local/share/knowledge-base/knowledge.db)."
        ),
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging.")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress stdout output.")

    sub = parser.add_subparsers(dest="command")

    # -- ingest ---------------------------------------------------------------
    p_ingest = sub.add_parser("ingest", help="Ingest a file or directory.")
    p_ingest.add_argument("path", help="File or directory to ingest.")
    p_ingest.add_argument("--source-type", dest="source_type", default=None)
    p_ingest.add_argument("--session-id", dest="session_id", default=None)

    # -- reingest -------------------------------------------------------------
    p_reingest = sub.add_parser("reingest", help="Force re-ingest of a file.")
    p_reingest.add_argument("path", help="File to re-ingest.")
    p_reingest.add_argument("--source-type", dest="source_type", default=None)
    p_reingest.add_argument("--session-id", dest="session_id", default=None)

    # -- ingest-url -----------------------------------------------------------
    p_url = sub.add_parser("ingest-url", help="Ingest a web page by URL.")
    p_url.add_argument("url", help="URL to fetch and ingest.")
    p_url.add_argument("--session-id", dest="session_id", default=None)

    # -- re-embed -------------------------------------------------------------
    p_embed = sub.add_parser(
        "re-embed",
        help="Re-embed all chunks to the declared KNOWLEDGE_BASE_EMBED_* identity (server stopped).",
    )
    p_embed.add_argument("--batch-size", type=int, default=32)

    # -- rebind ---------------------------------------------------------------
    sub.add_parser(
        "rebind",
        help="Adopt a moved KNOWLEDGE_BASE_EMBED_URL for the same identity (server stopped).",
    )

    # -- status ---------------------------------------------------------------
    sub.add_parser("status", help="Show index statistics.")

    # -- migrate --------------------------------------------------------------
    p_migrate = sub.add_parser("migrate", help="Apply pending schema migrations (backs up the DB first).")
    p_migrate.add_argument(
        "--check",
        action="store_true",
        help="Dry run: report pending migrations without mutating (exit non-zero unless already current).",
    )
    p_migrate.add_argument(
        "--backup-dir",
        dest="backup_dir",
        type=Path,
        default=None,
        help="Directory for the pre-migration backup (default: <db dir>/backups).",
    )

    # -- redact ---------------------------------------------------------------
    p_redact = sub.add_parser(
        "redact",
        help="Report stored chunks holding a secret (exit 1 if any); never prints them.",
    )
    p_redact.add_argument(
        "--rewrite",
        action="store_true",
        help="Redact them, re-embed, rebuild folder summaries and purge the file (server stopped).",
    )

    # -- schema ---------------------------------------------------------------
    sub.add_parser(
        "schema",
        help="Report live vs current schema version (exit non-zero on mismatch).",
    )

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Apply the #449 precedence: explicit --db-path/--db > $KNOWLEDGE_BASE_DB >
    # default. From here on, args.db is a concrete resolved Path.
    args.db = resolve_db_path(args.db)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        stream=sys.stderr,
        format="%(levelname)s: %(message)s",
    )

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "ingest": cmd_ingest,
        "reingest": cmd_reingest,
        "ingest-url": cmd_ingest_url,
        "re-embed": cmd_re_embed,
        "rebind": cmd_rebind,
        "status": cmd_status,
        "migrate": cmd_migrate,
        "schema": cmd_schema,
        "redact": cmd_redact,
    }

    try:
        dispatch[args.command](args)
    except (KnowledgeBaseError, ValueError) as exc:
        _print_error({"error": str(exc)})
        sys.exit(1)
    except httpx.HTTPError as exc:
        _print_error({"error": f"{args.command} failed: {exc}; rerun it"})
        sys.exit(1)


if __name__ == "__main__":
    main()
