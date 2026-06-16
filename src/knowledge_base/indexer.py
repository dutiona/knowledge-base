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

from . import migrate as _mig
from .db import get_connection, init_schema, resolve_db_path
from .exceptions import KnowledgeBaseError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_conn(db_path: Path) -> sqlite3.Connection:
    conn = get_connection(db_path)
    init_schema(conn)
    return conn


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
            f"SELECT COUNT(*) AS n FROM jobs "
            f"WHERE id IN ({placeholders}) AND status IN ('pending', 'running')",
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
    affected = conn.execute(
        "SELECT paper_id FROM paper_paths WHERE path = ?", (source_uri,)
    ).fetchall()
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
    from .embed_swap import re_embed

    conn = _get_conn(args.db)
    result = re_embed(
        conn,
        args.model,
        args.dim,
        batch_size=args.batch_size,
        provider=args.provider,
        matryoshka_base_dim=args.matryoshka_base_dim,
    )

    # Delete stale similarity relationships (mirrors routes/embeddings.py:59).
    conn.execute("DELETE FROM relationships WHERE relation_type = 'similar'")
    conn.commit()

    _print(result, quiet=args.quiet)


def cmd_status(args: argparse.Namespace) -> None:
    from .embed_swap import get_embed_config

    conn = _get_conn(args.db)

    chunks = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
    sources = conn.execute(
        "SELECT COUNT(DISTINCT source_uri) AS n FROM chunks"
    ).fetchone()["n"]
    papers = conn.execute("SELECT COUNT(*) AS n FROM papers").fetchone()["n"]

    job_rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
    ).fetchall()
    jobs = {row["status"]: row["n"] for row in job_rows}

    config = get_embed_config(conn)

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

    conn = get_connection(args.db)  # raw open — does NOT run init_schema/validation
    # Fail fast (not a 30s stall) if a forgotten-running server holds the lock.
    conn.execute("PRAGMA busy_timeout=5000")
    if _mig.get_schema_version(conn) is None:
        # Fresh / unversioned DB: bootstrap the baseline (builds + stamps v1).
        init_schema(conn)
        _print(
            {"bootstrapped": True, "schema_version": current, "applied": []},
            quiet=args.quiet,
        )
        return
    # Existing stamped DB: run pending migrations under backup + restore-on-fail.
    report = _mig.migrate(conn, backup_dir=args.backup_dir)
    _print(report, quiet=args.quiet)


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
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging."
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true", help="Suppress stdout output."
    )

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
    p_embed = sub.add_parser("re-embed", help="Re-embed all chunks with a new model.")
    p_embed.add_argument("--model", required=True, help="Embedding model name.")
    p_embed.add_argument("--dim", required=True, type=int, help="Embedding dimension.")
    p_embed.add_argument("--batch-size", type=int, default=32)
    p_embed.add_argument("--provider", default=None)
    p_embed.add_argument(
        "--matryoshka-base-dim",
        dest="matryoshka_base_dim",
        type=int,
        default=None,
    )

    # -- status ---------------------------------------------------------------
    sub.add_parser("status", help="Show index statistics.")

    # -- migrate --------------------------------------------------------------
    p_migrate = sub.add_parser(
        "migrate", help="Apply pending schema migrations (backs up the DB first)."
    )
    p_migrate.add_argument(
        "--check",
        action="store_true",
        help="Dry run: report pending migrations without mutating "
        "(exit non-zero unless already current).",
    )
    p_migrate.add_argument(
        "--backup-dir",
        dest="backup_dir",
        type=Path,
        default=None,
        help="Directory for the pre-migration backup (default: <db dir>/backups).",
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
        "status": cmd_status,
        "migrate": cmd_migrate,
        "schema": cmd_schema,
    }

    try:
        dispatch[args.command](args)
    except (KnowledgeBaseError, ValueError) as exc:
        _print_error({"error": str(exc)})
        sys.exit(1)


if __name__ == "__main__":
    main()
