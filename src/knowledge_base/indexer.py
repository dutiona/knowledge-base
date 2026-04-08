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

from .db import DEFAULT_DB_PATH, get_connection, init_schema
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


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knowledge-base-ingest",
        description="Batch indexing CLI for the knowledge-base.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="Path to the SQLite database (default: %(default)s).",
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

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

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
    }

    try:
        dispatch[args.command](args)
    except (KnowledgeBaseError, ValueError) as exc:
        _print_error({"error": str(exc)})
        sys.exit(1)


if __name__ == "__main__":
    main()
