"""SQLite database setup with FTS5 + sqlite-vec."""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import TypedDict, cast

import sqlite_vec

from . import migrate as _migrate  # schema-version framework (#450); read attrs live
from .exceptions import KnowledgeBaseError
from .utils import (
    ELEMENT_INSERT_EXPR,
    serialize_f32 as _serialize_f32,
)  # shared vec serialization

__all__ = [
    "DB_PATH_ENV_VAR",
    "DEFAULT_DB_PATH",
    "DEFAULT_EMBED_DIM",
    "DEFAULT_EMBED_MODEL",
    "DEFAULT_EMBED_PROVIDER",
    "RELATIONSHIP_TYPES",
    "SPACE_NAME_RE",
    "CoOccurrencePair",
    "EmbedSpace",
    "IndexStats",
    "RecentIngestion",
    "bump_chunk_count",
    "co_occurrence_pairs",
    "delete_chunk_vecs",
    "delete_chunks_cascade",
    "escape_like",
    "get_active_space",
    "get_connection",
    "get_index_stats",
    "get_space_element_type",
    "get_vec_table_name",
    "init_schema",
    "insert_chunk_vec",
    "resolve_db_path",
    "space_table_name",
]

DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "knowledge-base" / "knowledge.db"

#: Environment variable overriding the database path (lower precedence than an
#: explicit ``--db-path``/argument, higher than :data:`DEFAULT_DB_PATH`).
DB_PATH_ENV_VAR = "KNOWLEDGE_BASE_DB"


def resolve_db_path(cli_path: Path | None = None) -> Path:
    """Resolve the database path by precedence: ``cli_path`` > ``$KNOWLEDGE_BASE_DB``
    > :data:`DEFAULT_DB_PATH`.

    An explicit ``cli_path`` or a non-empty env var is honored verbatim (with a
    leading ``~`` expanded to the home directory) — there is no silent fallback to
    the default that would mask a configured (and possibly not-yet-created) path.
    An empty/whitespace env var counts as unset.
    """
    if cli_path is not None:
        return cli_path.expanduser()
    env = os.environ.get(DB_PATH_ENV_VAR)
    if env and env.strip():
        return Path(env).expanduser()
    return DEFAULT_DB_PATH


# Bootstrap defaults for fresh databases. Existing databases read from the
# config table — these constants are only used during initial schema creation.
DEFAULT_EMBED_MODEL = "bge-m3"
DEFAULT_EMBED_DIM = 1024
DEFAULT_EMBED_PROVIDER = "ollama"

RELATIONSHIP_TYPES = (
    "extends",
    "contradicts",
    "replicates",
    "cites",
    "compares",
    "applies",
    "implements",
    "similar",
)


def escape_like(value: str) -> str:
    r"""Escape ``%``, ``_``, and ``\`` for use in a ``LIKE`` clause.

    The caller must add ``ESCAPE '\'`` to the SQL statement.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _table_sql(conn: sqlite3.Connection, name: str) -> str | None:
    """Return the ``CREATE TABLE`` SQL for *name*, or ``None`` if it doesn't exist.

    Shared introspection primitive for the table-rebuild migrations, which guard
    on whether a CHECK-constraint marker is already present in this DDL text.
    """
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row[0] if row else None


def _table_columns(conn: sqlite3.Connection, name: str) -> set[str]:
    """Return the set of column names on table *name* via ``PRAGMA table_info``.

    Shared introspection primitive for the ADD-COLUMN migrations, which guard on
    whether their target column already exists.
    """
    return {row[1] for row in conn.execute(f"PRAGMA table_info({name})").fetchall()}


def _relationship_check_constraint() -> str:
    values = ", ".join(f"'{t}'" for t in RELATIONSHIP_TYPES)
    return f"CHECK(relation_type IN ({values}))"


# ---------------------------------------------------------------------------
# Shared CHECK-constraint fragments (#433).
#
# These allowed-value lists are byte-identical between the base-schema DDL in
# ``_create_base_schema`` and the table-rebuild migrations that widen them.
# Keeping a single source of truth prevents the two copies from drifting apart
# (a drift would silently change which values a rebuilt table accepts). Each
# constant is the exact ``CHECK(...)`` fragment embedded verbatim in both places.
# ---------------------------------------------------------------------------

#: Allowed ``chunks.source_type`` values (the rebuild that added 'figure').
SOURCE_TYPE_CHECK = "CHECK(source_type IN ('pdf', 'markdown', 'code', 'web', 'note', 'figure'))"
#: Allowed ``jobs.job_type`` values (the rebuild that added 'auto_relate').
JOB_TYPE_CHECK = "CHECK(job_type IN ('extract_structure', 'extract_figures', 'auto_relate'))"
#: Allowed ``jobs.status`` values (carried unchanged through the jobs rebuild).
JOB_STATUS_CHECK = "CHECK(status IN ('pending', 'running', 'completed', 'failed'))"
#: Allowed ``embed_spaces.element_type`` values (base DDL + the ADD COLUMN migration).
ELEMENT_TYPE_CHECK = "CHECK(element_type IN ('float32', 'int8'))"


# SQLite's default SQLITE_MAX_VARIABLE_NUMBER is 999.
# We use 900 to leave headroom for other parameters in the same statement.
_SQL_BATCH_SIZE = 900

# Busy-wait budget for acquiring the SQLite write lock under WAL mode. WAL still
# serializes writers, so a transient lock contended by another thread/process is
# retried for up to this many seconds before raising ``sqlite3.OperationalError``.
_CONNECTION_TIMEOUT_S = 30.0


def _batched_execute(
    conn: sqlite3.Connection,
    sql_template: str,
    ids: list,
    extra_params: list | None = None,
) -> int:
    """Execute a SQL statement with an IN clause in batches.

    sql_template must contain a single ``{ph}`` placeholder where the
    ``IN (?,?,...)`` list will be substituted.  extra_params (if given)
    are prepended to each batch's parameter list.

    Returns the summed ``cursor.rowcount`` across all batches — the number of
    rows affected by the statement (#433). For a DELETE this is the actual rows
    deleted, letting callers avoid a separate COUNT pass. Existing callers that
    ignore the return value are unaffected.

    Each batch's ``rowcount`` is clamped to ``>= 0``: sqlite3 documents -1 for
    statements whose affected-row count is indeterminate, and a negative value
    would corrupt count bookkeeping downstream (e.g. ``MAX(0, count - (-1))`` would
    *increment*). WHERE-qualified DELETEs report a true count in practice, so the
    clamp is purely defensive — never observed to fire for current callers.
    """
    affected = 0
    for i in range(0, len(ids), _SQL_BATCH_SIZE):
        batch = ids[i : i + _SQL_BATCH_SIZE]
        placeholders = ",".join("?" * len(batch))
        params = list(extra_params or []) + list(batch)
        cursor = conn.execute(sql_template.replace("{ph}", placeholders, 1), params)
        affected += max(cursor.rowcount, 0)
    return affected


def _batched_select(
    conn: sqlite3.Connection,
    sql_template: str,
    ids: list,
    extra_params: list | None = None,
) -> list[sqlite3.Row]:
    """Execute a SELECT with an IN clause in batches, returning all rows.

    The ``{ph}`` placeholder in *sql_template* is replaced with ``?,?,…``
    for each batch.  Any additional ``?`` placeholders **after** ``{ph}``
    should have their values supplied via *extra_params*.
    """
    suffix = extra_params or []
    results: list[sqlite3.Row] = []
    for i in range(0, len(ids), _SQL_BATCH_SIZE):
        batch = ids[i : i + _SQL_BATCH_SIZE]
        placeholders = ",".join("?" * len(batch))
        results.extend(
            conn.execute(
                sql_template.replace("{ph}", placeholders, 1),
                [*batch, *suffix],
            ).fetchall()
        )
    return results


class CoOccurrencePair(TypedDict):
    """A pair of documents sharing one or more ingestion sessions (#433).

    ``source_uri_a`` is always alphabetically less than ``source_uri_b``.
    """

    source_uri_a: str
    source_uri_b: str
    co_sessions: int


def co_occurrence_pairs(conn: sqlite3.Connection, min_sessions: int = 1) -> list[CoOccurrencePair]:
    """Return document pairs that share at least *min_sessions* ingestion sessions.

    Each row contains source_uri_a, source_uri_b (alphabetically ordered)
    and the count of shared sessions.
    """
    rows = conn.execute(
        """
        WITH doc_sessions AS (
            SELECT DISTINCT c.source_uri, cs.session_id
            FROM chunk_sessions cs
            JOIN chunks c ON c.id = cs.chunk_id
        )
        SELECT a.source_uri AS source_uri_a,
               b.source_uri AS source_uri_b,
               COUNT(*) AS co_sessions
        FROM doc_sessions a
        JOIN doc_sessions b
          ON a.session_id = b.session_id
         AND a.source_uri < b.source_uri
        GROUP BY a.source_uri, b.source_uri
        HAVING co_sessions >= ?
        ORDER BY co_sessions DESC
        """,
        (min_sessions,),
    ).fetchall()
    return [
        {
            "source_uri_a": r["source_uri_a"],
            "source_uri_b": r["source_uri_b"],
            "co_sessions": r["co_sessions"],
        }
        for r in rows
    ]


class RecentIngestion(TypedDict):
    """One recently-ingested document, aggregated over its chunks (#217)."""

    source_uri: str
    source_type: str
    chunks: int
    created_at: str


class IndexStats(TypedDict):
    """Aggregated index statistics backing the ``status`` MCP tool (#217).

    Every field is computed by a single SQL pass over the database in
    :func:`get_index_stats`; cross-module figures the tool also reports
    (prediction-error count, embedding config) stay in the tool, since they are
    not inline SQL. ``db_size_bytes`` is the on-disk size of the ``main``
    database file (env/CLI-resolved via ``PRAGMA database_list``); the tool
    derives the human-readable MB value and path from the connection itself.
    """

    total_chunks: int
    by_type: dict[str, int]
    papers: int
    conclusions: int
    relationships: int
    folder_summaries: int
    methods: int
    datasets: int
    metrics: int
    recent_ingestions: list[RecentIngestion]
    jobs: dict[str, int]
    embed_spaces: dict[str, int]
    chunk_strategy: str
    db_size_bytes: int


def get_index_stats(conn: sqlite3.Connection) -> IndexStats:
    """Collect the count/aggregation statistics that back the ``status`` tool (#217).

    Runs every inline aggregation query that previously lived in
    ``routes.search.status`` and returns a structured :class:`IndexStats`. The
    values are byte-for-byte the same as before the extraction — only the
    location of the SQL changed. Cross-module figures (prediction errors,
    embedding config) are intentionally left to the caller.
    """
    type_counts = conn.execute("SELECT source_type, COUNT(*) as count FROM chunks GROUP BY source_type").fetchall()

    total: int = conn.execute("SELECT COUNT(*) as count FROM chunks").fetchone()["count"]

    paper_count: int = conn.execute("SELECT COUNT(*) as count FROM papers").fetchone()["count"]
    conclusion_count: int = conn.execute("SELECT COUNT(*) as count FROM conclusions").fetchone()["count"]
    relationship_count: int = conn.execute("SELECT COUNT(*) as count FROM relationships").fetchone()["count"]
    folder_summary_count: int = conn.execute("SELECT COUNT(*) as count FROM folder_summaries").fetchone()["count"]
    method_count: int = conn.execute("SELECT COUNT(*) as count FROM methods").fetchone()["count"]
    dataset_count: int = conn.execute("SELECT COUNT(*) as count FROM datasets").fetchone()["count"]
    metric_count: int = conn.execute("SELECT COUNT(*) as count FROM metrics").fetchone()["count"]

    recent = conn.execute(
        "SELECT source_uri, source_type, COUNT(*) as chunks, created_at"
        " FROM chunks GROUP BY source_uri ORDER BY created_at DESC LIMIT 5"
    ).fetchall()

    # Report the DB the connection actually opened (env/CLI-resolved, #449), not
    # the hardcoded default — they differ when KNOWLEDGE_BASE_DB is set.
    db_main = next((r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main"), "")
    db_path = Path(db_main) if db_main else None
    db_size_bytes = db_path.stat().st_size if db_path and db_path.exists() else 0

    job_counts: dict[str, int] = {}
    for row in conn.execute("SELECT status, COUNT(*) as count FROM jobs GROUP BY status").fetchall():
        job_counts[row["status"]] = row["count"]

    space_counts: dict[str, int] = {}
    for row in conn.execute("SELECT status, COUNT(*) as count FROM embed_spaces GROUP BY status").fetchall():
        space_counts[row["status"]] = row["count"]

    chunk_strategy_row = conn.execute("SELECT value FROM config WHERE key = 'chunk_strategy'").fetchone()
    chunk_strategy: str = chunk_strategy_row["value"] if chunk_strategy_row else "mechanical"

    return {
        "total_chunks": total,
        "by_type": {row["source_type"]: row["count"] for row in type_counts},
        "papers": paper_count,
        "conclusions": conclusion_count,
        "relationships": relationship_count,
        "folder_summaries": folder_summary_count,
        "methods": method_count,
        "datasets": dataset_count,
        "metrics": metric_count,
        "recent_ingestions": [
            {
                "source_uri": row["source_uri"],
                "source_type": row["source_type"],
                "chunks": row["chunks"],
                "created_at": row["created_at"],
            }
            for row in recent
        ],
        "jobs": job_counts,
        "embed_spaces": space_counts,
        "chunk_strategy": chunk_strategy,
        "db_size_bytes": db_size_bytes,
    }


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    # ``None`` resolves via $KNOWLEDGE_BASE_DB / DEFAULT_DB_PATH (#449), so callers
    # that don't pass an explicit path (e.g. the MCP server via _conn._get_conn)
    # honor the configured path. Callers passing a path keep full control.
    if db_path is None:
        db_path = resolve_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=_CONNECTION_TIMEOUT_S)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate_source_type_figure(conn: sqlite3.Connection) -> None:
    table_sql = _table_sql(conn, "chunks")
    if not table_sql or "'figure'" in table_sql:
        return

    # Determine columns in the old table to handle INSERT INTO ... SELECT
    columns = _table_columns(conn, "chunks")
    old_cols = "id, content_hash, content, source_type, source_uri, chunk_index, created_at, metadata"
    # session_id and chunk_strategy may not exist in very old schemas
    new_cols = old_cols
    if "session_id" in columns:
        old_cols += ", session_id"
        new_cols += ", session_id"
    if "chunk_strategy" in columns:
        old_cols += ", chunk_strategy"
        new_cols += ", chunk_strategy"

    conn.executescript(f"""
    PRAGMA foreign_keys = OFF;
    BEGIN;
    CREATE TABLE chunks_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content_hash TEXT NOT NULL UNIQUE,
        content TEXT NOT NULL,
        source_type TEXT NOT NULL {SOURCE_TYPE_CHECK},
        source_uri TEXT NOT NULL,
        chunk_index INTEGER NOT NULL,
        session_id TEXT,
        chunk_strategy TEXT NOT NULL DEFAULT 'mechanical',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        metadata TEXT DEFAULT '{{}}'
    );

    INSERT INTO chunks_new ({new_cols}) SELECT {old_cols} FROM chunks;
    DROP TABLE chunks;
    ALTER TABLE chunks_new RENAME TO chunks;

    CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
        INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
    END;
    CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
        INSERT INTO chunks_fts(chunks_fts, rowid, content) VALUES ('delete', old.id, old.content);
    END;
    CREATE TRIGGER chunks_au AFTER UPDATE ON chunks BEGIN
        INSERT INTO chunks_fts(chunks_fts, rowid, content) VALUES ('delete', old.id, old.content);
        INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
    END;

    COMMIT;
    PRAGMA foreign_keys = ON;
    """)  # noqa: S608  # trusted internal identifiers (hardcoded column names), not user input


def _migrate_relationship_types(conn: sqlite3.Connection) -> None:
    table_sql = _table_sql(conn, "relationships")
    if not table_sql or "'similar'" in table_sql:
        return

    check = _relationship_check_constraint()
    conn.executescript(f"""
    PRAGMA foreign_keys = OFF;
    BEGIN;
    CREATE TABLE relationships_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_paper_id INTEGER NOT NULL REFERENCES papers(id),
        target_paper_id INTEGER NOT NULL REFERENCES papers(id),
        relation_type TEXT NOT NULL {check},
        confidence REAL DEFAULT 1.0,
        evidence_chunk_id INTEGER REFERENCES chunks(id),
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(source_paper_id, target_paper_id, relation_type)
    );

    INSERT INTO relationships_new SELECT * FROM relationships;
    DROP TABLE relationships;
    ALTER TABLE relationships_new RENAME TO relationships;

    COMMIT;
    PRAGMA foreign_keys = ON;
    """)  # noqa: S608  # trusted internal identifier (RELATIONSHIP_TYPES check constraint), not user input


def _migrate_papers_fts(conn: sqlite3.Connection) -> None:
    """Backfill papers_fts for databases created before this index existed."""
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='papers_fts'").fetchone()
    if not row:
        return
    count = conn.execute("SELECT count(*) FROM papers_fts").fetchone()[0]
    if count > 0:
        return
    paper_count = conn.execute("SELECT count(*) FROM papers").fetchone()[0]
    if paper_count == 0:
        return
    conn.execute("INSERT INTO papers_fts(rowid, title) SELECT id, title FROM papers")


def _migrate_session_id(conn: sqlite3.Connection) -> None:
    """Add session_id column to chunks for co-occurrence tracking."""
    if "session_id" in _table_columns(conn, "chunks"):
        return
    conn.execute("ALTER TABLE chunks ADD COLUMN session_id TEXT")
    conn.commit()


def _migrate_chunk_strategy(conn: sqlite3.Connection) -> None:
    """Add chunk_strategy column to chunks for dual chunking support."""
    if "chunk_strategy" in _table_columns(conn, "chunks"):
        return
    conn.execute("ALTER TABLE chunks ADD COLUMN chunk_strategy TEXT NOT NULL DEFAULT 'mechanical'")
    conn.commit()


def _migrate_paper_paths(conn: sqlite3.Connection) -> None:
    """Populate paper_paths from existing paper -> abstract_chunk -> source_uri links.

    Idempotent: backfills missing entries per-paper (not all-or-nothing).
    """
    conn.execute(
        "INSERT OR IGNORE INTO paper_paths (paper_id, path, is_primary)"
        " SELECT p.id, c.source_uri, TRUE"
        " FROM papers p"
        " JOIN chunks c ON c.id = p.abstract_chunk_id"
        " WHERE p.abstract_chunk_id IS NOT NULL"
        " AND p.id NOT IN (SELECT paper_id FROM paper_paths)"
    )
    conn.commit()


def _migrate_jobs_types(conn: sqlite3.Connection) -> None:
    """Add 'auto_relate' to jobs.job_type CHECK constraint for existing DBs."""
    table_sql = _table_sql(conn, "jobs")
    if not table_sql or "'auto_relate'" in table_sql:
        return

    conn.executescript(f"""
    PRAGMA foreign_keys = OFF;
    BEGIN;
    CREATE TABLE jobs_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
        job_type TEXT NOT NULL {JOB_TYPE_CHECK},
        params TEXT NOT NULL DEFAULT '{{}}',
        status TEXT NOT NULL DEFAULT 'pending'
            {JOB_STATUS_CHECK},
        progress TEXT,
        result TEXT,
        error TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        started_at TEXT,
        completed_at TEXT
    );

    INSERT INTO jobs_new SELECT * FROM jobs;
    DROP TABLE jobs;
    ALTER TABLE jobs_new RENAME TO jobs;

    CREATE INDEX idx_jobs_status_created ON jobs(status, created_at);

    COMMIT;
    PRAGMA foreign_keys = ON;
    """)  # noqa: S608  # trusted internal identifiers (JOB_TYPE/STATUS_CHECK constants), not user input


def _migrate_chunk_sessions(conn: sqlite3.Connection) -> None:
    """Create chunk_sessions join table and backfill from chunks.session_id."""
    exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chunk_sessions'").fetchone()
    if exists:
        return
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunk_sessions (
            chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
            session_id TEXT NOT NULL,
            UNIQUE(chunk_id, session_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunk_sessions_session ON chunk_sessions(session_id)")
    # Backfill from existing chunks.session_id
    conn.execute(
        "INSERT OR IGNORE INTO chunk_sessions (chunk_id, session_id)"
        " SELECT id, session_id FROM chunks WHERE session_id IS NOT NULL"
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Embedding space helpers (#99)
# ---------------------------------------------------------------------------

#: Validates an embedding-space name (alphanumeric/underscore only). Public so
#: ``embed_swap`` and other modules can reuse the same canonical pattern.
SPACE_NAME_RE = re.compile(r"^[a-zA-Z0-9_]+$")


def space_table_name(name: str) -> str:
    """Deterministic vec table name for an embedding space."""
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    return f"chunks_vec_{sanitized}"


class EmbedSpace(TypedDict):
    """A row of the ``embed_spaces`` registry (#433).

    Mirrors the table columns; ``get_active_space`` returns ``dict(row)`` over a
    ``SELECT *``, so every column is present at runtime.
    """

    name: str
    model: str
    provider: str
    dim: int
    chunk_strategy: str
    status: str
    table_name: str
    created_at: str
    chunk_count: int
    total_chunks: int | None
    matryoshka_base_dim: int | None
    element_type: str


def get_active_space(conn: sqlite3.Connection) -> EmbedSpace | None:
    """Return the active embedding space row as a dict, or None."""
    row = conn.execute("SELECT * FROM embed_spaces WHERE status = 'active'").fetchone()
    if row is None:
        return None
    # Runtime value is a plain dict (identical to the previous ``dict(row)``); the
    # cast asserts it conforms to the EmbedSpace schema (SELECT * over the table).
    return cast("EmbedSpace", dict(row))


def get_vec_table_name(conn: sqlite3.Connection) -> str:
    """Return the active space's vec table name, falling back to 'chunks_vec'."""
    row = conn.execute("SELECT table_name FROM embed_spaces WHERE status = 'active'").fetchone()
    if row is None:
        return "chunks_vec"
    return row["table_name"]


def _resolve_vec_table(conn: sqlite3.Connection, table_name: str | None) -> str:
    """Return *table_name* if given, else the active space's vec table (#433).

    Shared active-space fallback for the vec helpers — keeps the
    ``table_name or get_vec_table_name(conn)`` resolution in one place.
    """
    return table_name or get_vec_table_name(conn)


def get_active_chunk_strategy(conn: sqlite3.Connection) -> str:
    """Return the active space's chunk_strategy, falling back to 'mechanical'."""
    row = conn.execute("SELECT chunk_strategy FROM embed_spaces WHERE status = 'active'").fetchone()
    if row is None:
        return "mechanical"
    return row["chunk_strategy"]


def get_space_element_type(
    conn: sqlite3.Connection,
    table_name: str | None = None,
) -> str:
    """Return the element_type for the given (or active) space, defaulting to 'float32'."""
    tbl = _resolve_vec_table(conn, table_name)
    row = conn.execute("SELECT element_type FROM embed_spaces WHERE table_name = ?", (tbl,)).fetchone()
    if row is None:
        return "float32"
    return row["element_type"] or "float32"


def bump_chunk_count(conn: sqlite3.Connection, table_name: str, delta: int) -> None:
    """Increment ``embed_spaces.chunk_count`` for the active space by *delta* (#392).

    Performs the SAME ``status = 'active'`` gated, table-name-matched UPDATE that
    :func:`insert_chunk_vec` runs per-row — once, with the batched *delta*. Calling
    this once with ``delta == N`` is byte-equivalent to N per-row ``chunk_count + 1``
    bumps, so the ingestion hot path can opt out of the per-row UPDATE (passing
    ``bump_count=False`` to :func:`insert_chunk_vec`) and amortize the bookkeeping
    into a single write. The active-space gate means a *table_name* belonging to a
    non-active ('populating'/'deprecated') space is a no-op, matching the per-row path.
    """
    conn.execute(
        "UPDATE embed_spaces SET chunk_count = chunk_count + ? WHERE table_name = ? AND status = 'active'",
        (delta, table_name),
    )


def insert_chunk_vec(
    conn: sqlite3.Connection,
    chunk_id: int,
    embedding: list[float],
    table_name: str | None = None,
    element_type: str | None = None,
    bump_count: bool = True,
) -> None:
    """Insert a single chunk embedding into the specified (or active) vec table.

    If *element_type* is ``None`` (the default), it is auto-resolved from the
    space registry so callers never need to look it up themselves.

    When *bump_count* is ``True`` (the default), the per-row
    ``embed_spaces.chunk_count`` UPDATE fires exactly as before, preserving behavior
    for all incidental callers. Batch callers (the ingestion hot path, #392) pass
    ``bump_count=False`` to skip the per-row UPDATE and instead call
    :func:`bump_chunk_count` once with the total inserted — eliminating the N+1
    write amplification while keeping the resulting ``chunk_count`` identical.
    """
    tbl = _resolve_vec_table(conn, table_name)
    if element_type is None:
        element_type = get_space_element_type(conn, tbl)
    expr = ELEMENT_INSERT_EXPR[element_type]
    conn.execute(
        f"INSERT INTO [{tbl}] (rowid, embedding, chunk_id) VALUES (?, {expr}, ?)",  # noqa: S608  # trusted internal identifier (vec table name + fixed expr), not user input
        (chunk_id, _serialize_f32(embedding), chunk_id),
    )
    # Keep embed_spaces.chunk_count in sync for the active space (per-row path).
    if bump_count:
        bump_chunk_count(conn, tbl, 1)


def delete_chunk_vecs(
    conn: sqlite3.Connection,
    chunk_ids: list[int],
    table_name: str | None = None,
) -> None:
    """Delete chunk embeddings from the specified (or active) vec table.

    Also decrements ``embed_spaces.chunk_count`` to keep bookkeeping in sync.
    """
    if not chunk_ids:
        return
    # Dedup so the rowcount-based count is unconditionally correct: a duplicate id
    # split across two batches would otherwise be DELETEd in the first batch and
    # matched-zero in the second, and (historically, via the old per-batch COUNT
    # pass) over-counted. DELETE ... IN (deduped) is equivalent for the realistic
    # unique-id callers, and makes actual_deleted the true affected-row count.
    chunk_ids = list(dict.fromkeys(chunk_ids))
    tbl = _resolve_vec_table(conn, table_name)
    # Single pass: the DELETE's affected-row count IS the number of vec rows that
    # actually existed (not all chunks have embeddings) — no separate COUNT pass (#433).
    actual_deleted = _batched_execute(conn, f"DELETE FROM [{tbl}] WHERE chunk_id IN ({{ph}})", chunk_ids)  # noqa: S608  # trusted internal identifier (vec table name), not user input
    # Keep embed_spaces.chunk_count in sync
    if actual_deleted:
        conn.execute(
            "UPDATE embed_spaces SET chunk_count = MAX(0, chunk_count - ?) WHERE table_name = ?",
            (actual_deleted, tbl),
        )


def delete_chunks_cascade(
    conn: sqlite3.Connection,
    chunk_ids: list[int],
    table_name: str | None = None,
) -> int:
    """Delete chunks and their associated vec embeddings.

    Handles the two-step cascade: vec rows first (no trigger), then chunk
    rows (whose DELETE trigger cleans up FTS). Callers are responsible for
    any FK cleanup (papers, relationships, conclusions) before calling this.

    Returns the number of chunks deleted.
    """
    if not chunk_ids:
        return 0
    delete_chunk_vecs(conn, chunk_ids, table_name=table_name)
    _batched_execute(conn, "DELETE FROM chunks WHERE id IN ({ph})", chunk_ids)
    return len(chunk_ids)


def _migrate_embed_spaces_matryoshka(conn: sqlite3.Connection) -> None:
    """Add matryoshka_base_dim column to embed_spaces if missing."""
    if "matryoshka_base_dim" in _table_columns(conn, "embed_spaces"):
        return
    conn.execute("ALTER TABLE embed_spaces ADD COLUMN matryoshka_base_dim INTEGER")
    conn.commit()


def _migrate_embed_spaces_element_type(conn: sqlite3.Connection) -> None:
    """Add element_type column to embed_spaces if missing."""
    if "element_type" in _table_columns(conn, "embed_spaces"):
        return
    conn.execute(
        f"ALTER TABLE embed_spaces ADD COLUMN element_type TEXT NOT NULL DEFAULT 'float32' {ELEMENT_TYPE_CHECK}"
    )
    conn.commit()


def _migrate_normalize_source_uri(conn: sqlite3.Connection) -> None:
    """One-time migration: replace backslashes in source_uri and paper_paths.path.

    Prior to this fix, ``str(Path(...))`` on Windows produced backslash
    separators that broke ``posixpath``-based folder-summary LIKE queries.
    This migration normalises any legacy rows so lookups remain consistent.
    Idempotent — runs a no-op UPDATE when no backslashes are present.
    """
    conn.execute("UPDATE chunks SET source_uri = REPLACE(source_uri, '\\', '/') WHERE source_uri LIKE '%\\%'")
    conn.execute("UPDATE paper_paths SET path = REPLACE(path, '\\', '/') WHERE path LIKE '%\\%'")
    conn.commit()


def _migrate_extraction_source(conn: sqlite3.Connection) -> None:
    """Add source column to methods, datasets, metrics, entities tables."""
    for table in ("methods", "datasets", "metrics", "entities"):
        if "source" not in _table_columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN source TEXT NOT NULL DEFAULT 'user'")
    conn.commit()


def _bootstrap_embed_spaces(conn: sqlite3.Connection, embed_dim: int) -> None:
    """Register the default embedding space if embed_spaces is empty.

    Works for both fresh DBs (chunks_vec just created, 0 rows) and legacy
    DBs (chunks_vec has data). Idempotent — skips if any space already exists.
    """
    existing = conn.execute("SELECT 1 FROM embed_spaces LIMIT 1").fetchone()
    if existing:
        return

    model_row = conn.execute("SELECT value FROM config WHERE key = 'embed_model'").fetchone()
    provider_row = conn.execute("SELECT value FROM config WHERE key = 'embed_provider'").fetchone()

    model = model_row["value"] if model_row else DEFAULT_EMBED_MODEL
    provider = provider_row["value"] if provider_row else DEFAULT_EMBED_PROVIDER

    # Count existing embeddings (0 for fresh DBs)
    try:
        count = conn.execute("SELECT COUNT(*) FROM chunks_vec").fetchone()[0]
    except sqlite3.OperationalError:
        count = 0

    # Legacy DBs (pre-#99): chunks_vec contains ALL chunks regardless of
    # strategy. The migrate_chunk_strategy backfill marks old chunks as
    # 'mechanical'. Using config.chunk_strategy here would misclassify the
    # default space (e.g., as 'semantic' when it actually has all chunks).
    # Always bootstrap as 'mechanical' — the user can create a new space
    # with a different strategy after migration.
    strategy = "mechanical"

    conn.execute(
        "INSERT INTO embed_spaces"
        " (name, model, provider, dim, chunk_strategy, status, table_name, chunk_count)"
        " VALUES (?, ?, ?, ?, ?, 'active', 'chunks_vec', ?)",
        ("default", model, provider, embed_dim, strategy, count),
    )
    conn.commit()


def _seed_default_config(conn: sqlite3.Connection) -> int:
    """Create the config table and seed all default key/value rows.

    Consolidates the two historically-split config-seeding spots: the embed_*
    defaults (seeded only on a fresh DB, guarded by the absence of ``embed_model``)
    and the llm_*/threshold/chunk_strategy defaults (``INSERT OR IGNORE``). The
    config table is created first so the schema's configured dimension can be read
    before the vec tables (which embed it) are built.

    Returns the resolved ``embed_dim`` (configured value, or
    :data:`DEFAULT_EMBED_DIM` when absent) for the caller to thread into the base
    schema DDL.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    existing = conn.execute("SELECT value FROM config WHERE key = 'embed_model'").fetchone()
    if not existing:
        conn.executemany(
            "INSERT INTO config (key, value) VALUES (?, ?)",
            [
                ("embed_model", DEFAULT_EMBED_MODEL),
                ("embed_dim", str(DEFAULT_EMBED_DIM)),
                ("embed_provider", DEFAULT_EMBED_PROVIDER),
            ],
        )
        conn.commit()

    # These keys are seeded ahead of the base-schema DDL and the migration chain,
    # but none are read during DDL or by any migration (only embed_dim is read,
    # above) — so their position here carries no ordering dependency.
    conn.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('llm_provider', 'ollama')")
    conn.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('llm_model', 'qwen3.5:27b')")
    conn.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('prediction_error_threshold', '0.025')")
    conn.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('auto_relate_propose_threshold', '0.82')")
    conn.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('auto_relate_accept_threshold', '0.95')")
    conn.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('chunk_strategy', 'mechanical')")

    # Read the configured dimension (handles both fresh and existing DBs)
    dim_row = conn.execute("SELECT value FROM config WHERE key = 'embed_dim'").fetchone()
    return int(dim_row["value"]) if dim_row else DEFAULT_EMBED_DIM


def _create_base_schema(conn: sqlite3.Connection, embed_dim: int) -> None:
    """Create the full v1 base schema: tables, virtual tables, triggers, indexes.

    Every statement is idempotent (``IF NOT EXISTS``), so this converges both a
    fresh DB and a legacy-unversioned DB onto the current contract. *embed_dim* is
    interpolated into the vec0 virtual tables' fixed dimension.
    """
    conn.executescript(f"""
    CREATE TABLE IF NOT EXISTS chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content_hash TEXT NOT NULL UNIQUE,
        content TEXT NOT NULL,
        source_type TEXT NOT NULL {SOURCE_TYPE_CHECK},
        source_uri TEXT NOT NULL,
        chunk_index INTEGER NOT NULL,
        session_id TEXT,
        chunk_strategy TEXT NOT NULL DEFAULT 'mechanical',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        metadata TEXT DEFAULT '{{}}'
    );

    CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
        content,
        content='chunks',
        content_rowid='id',
        tokenize='porter unicode61'
    );

    CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
        INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
    END;
    CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
        INSERT INTO chunks_fts(chunks_fts, rowid, content) VALUES ('delete', old.id, old.content);
    END;
    CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
        INSERT INTO chunks_fts(chunks_fts, rowid, content) VALUES ('delete', old.id, old.content);
        INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
    END;

    CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
        embedding float[{embed_dim}],
        +chunk_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS papers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        authors TEXT DEFAULT '[]',
        year INTEGER,
        venue TEXT,
        doi TEXT UNIQUE,
        bibtex TEXT,
        abstract_chunk_id INTEGER REFERENCES chunks(id),
        added_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS relationships (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_paper_id INTEGER NOT NULL REFERENCES papers(id),
        target_paper_id INTEGER NOT NULL REFERENCES papers(id),
        relation_type TEXT NOT NULL {_relationship_check_constraint()},
        confidence REAL DEFAULT 1.0,
        evidence_chunk_id INTEGER REFERENCES chunks(id),
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(source_paper_id, target_paper_id, relation_type)
    );

    CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
        title,
        content='papers',
        content_rowid='id',
        tokenize='porter unicode61'
    );

    CREATE TRIGGER IF NOT EXISTS papers_ai AFTER INSERT ON papers BEGIN
        INSERT INTO papers_fts(rowid, title) VALUES (new.id, new.title);
    END;
    CREATE TRIGGER IF NOT EXISTS papers_ad AFTER DELETE ON papers BEGIN
        INSERT INTO papers_fts(papers_fts, rowid, title) VALUES ('delete', old.id, old.title);
    END;
    CREATE TRIGGER IF NOT EXISTS papers_au AFTER UPDATE OF title ON papers BEGIN
        INSERT INTO papers_fts(papers_fts, rowid, title) VALUES ('delete', old.id, old.title);
        INSERT INTO papers_fts(rowid, title) VALUES (new.id, new.title);
    END;

    CREATE TABLE IF NOT EXISTS conclusions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        claim TEXT NOT NULL,
        confidence REAL DEFAULT 1.0,
        source_chunk_ids TEXT NOT NULL DEFAULT '[]',
        session_context TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        superseded_by INTEGER REFERENCES conclusions(id)
    );

    CREATE TABLE IF NOT EXISTS executions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task TEXT NOT NULL,
        result_summary TEXT,
        conclusion_ids TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS methods (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        paper_id INTEGER NOT NULL REFERENCES papers(id),
        description TEXT,
        chunk_id INTEGER REFERENCES chunks(id),
        source TEXT NOT NULL DEFAULT 'user',
        UNIQUE(name, paper_id)
    );

    CREATE TABLE IF NOT EXISTS datasets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        paper_id INTEGER NOT NULL REFERENCES papers(id),
        description TEXT,
        chunk_id INTEGER REFERENCES chunks(id),
        source TEXT NOT NULL DEFAULT 'user',
        UNIQUE(name, paper_id)
    );

    CREATE TABLE IF NOT EXISTS metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        value REAL NOT NULL,
        unit TEXT,
        dataset_id INTEGER REFERENCES datasets(id),
        method_id INTEGER REFERENCES methods(id),
        paper_id INTEGER NOT NULL REFERENCES papers(id),
        chunk_id INTEGER REFERENCES chunks(id),
        source TEXT NOT NULL DEFAULT 'user'
    );

    CREATE TABLE IF NOT EXISTS entities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_name TEXT NOT NULL,
        entity_type TEXT NOT NULL CHECK(entity_type IN ('method', 'dataset', 'metric')),
        paper_id INTEGER NOT NULL REFERENCES papers(id),
        description TEXT,
        source TEXT NOT NULL DEFAULT 'user',
        UNIQUE(canonical_name, entity_type, paper_id)
    );

    CREATE TABLE IF NOT EXISTS entity_mentions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_id INTEGER NOT NULL REFERENCES entities(id),
        surface_form TEXT NOT NULL,
        chunk_id INTEGER NOT NULL REFERENCES chunks(id),
        confidence REAL DEFAULT 1.0,
        UNIQUE(entity_id, surface_form, chunk_id)
    );

    CREATE TABLE IF NOT EXISTS paper_paths (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
        path TEXT NOT NULL,
        content_hash TEXT,
        is_primary BOOLEAN DEFAULT TRUE CHECK(is_primary IN (0, 1)),
        added_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(path)
    );

    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
        job_type TEXT NOT NULL {JOB_TYPE_CHECK},
        params TEXT NOT NULL DEFAULT '{{}}',
        status TEXT NOT NULL DEFAULT 'pending'
            {JOB_STATUS_CHECK},
        progress TEXT,
        result TEXT,
        error TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        started_at TEXT,
        completed_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at);

    CREATE TABLE IF NOT EXISTS prediction_errors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query TEXT NOT NULL,
        query_hash TEXT NOT NULL,
        top_score REAL,
        top_chunk_id INTEGER REFERENCES chunks(id) ON DELETE SET NULL,
        error_type TEXT NOT NULL CHECK(error_type IN ('low_confidence', 'no_results')),
        source_type_filter TEXT,
        detected_at TEXT NOT NULL DEFAULT (datetime('now')),
        resolved_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_prediction_errors_hash_type
        ON prediction_errors(query_hash, error_type, source_type_filter, detected_at);
    CREATE INDEX IF NOT EXISTS idx_prediction_errors_unresolved
        ON prediction_errors(detected_at) WHERE resolved_at IS NULL;

    -- Folder summaries for search context boosting (#126)
    CREATE TABLE IF NOT EXISTS folder_summaries (
        folder_path TEXT PRIMARY KEY,
        summary TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE VIRTUAL TABLE IF NOT EXISTS folder_summaries_vec USING vec0(
        embedding float[{embed_dim}],
        +folder_path TEXT
    );

    -- Embedding space registry (#99)
    CREATE TABLE IF NOT EXISTS embed_spaces (
        name TEXT PRIMARY KEY,
        model TEXT NOT NULL,
        provider TEXT NOT NULL,
        dim INTEGER NOT NULL,
        chunk_strategy TEXT NOT NULL DEFAULT 'mechanical'
            CHECK(chunk_strategy IN ('mechanical', 'semantic')),
        status TEXT NOT NULL CHECK(status IN ('active', 'populating', 'deprecated')),
        table_name TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        chunk_count INTEGER DEFAULT 0,
        total_chunks INTEGER,
        matryoshka_base_dim INTEGER
            CHECK(matryoshka_base_dim IS NULL OR matryoshka_base_dim > dim),
        element_type TEXT NOT NULL DEFAULT 'float32'
            {ELEMENT_TYPE_CHECK}
    );
    """)  # noqa: S608  # trusted internal identifiers (embed_dim int + RELATIONSHIP_TYPES check), not user input

    conn.execute("CREATE INDEX IF NOT EXISTS idx_paper_paths_paper_id ON paper_paths(paper_id, is_primary)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_paper_paths_hash ON paper_paths(content_hash)")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_paths_one_primary ON paper_paths(paper_id) WHERE is_primary = TRUE"
    )
    # Enforce at most one active embedding space at the DB level
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_embed_spaces_one_active ON embed_spaces(status) WHERE status = 'active'"
    )


def _run_migrations(conn: sqlite3.Connection, embed_dim: int) -> None:
    """Drive the ordered legacy-migration chain over the just-built base schema.

    The order is **load-bearing** and must be preserved exactly: e.g.
    ``_migrate_session_id`` must add the column before ``_migrate_chunk_sessions``
    backfills from it, and ``_bootstrap_embed_spaces`` sits after the chunk-table
    migrations but before the embed_spaces ALTER migrations. The
    ``idx_chunks_session_id`` index creation is interleaved at its original
    position (after ``_migrate_chunk_strategy``, before ``_migrate_paper_paths``).
    Each entry is a no-arg callable invoked in sequence.
    """
    migrations = (
        _migrate_source_type_figure,
        _migrate_relationship_types,
        _migrate_papers_fts,
        _migrate_session_id,
        _migrate_chunk_strategy,
        lambda c: c.execute("CREATE INDEX IF NOT EXISTS idx_chunks_session_id ON chunks(session_id)"),
        _migrate_paper_paths,
        _migrate_jobs_types,
        _migrate_chunk_sessions,
        lambda c: _bootstrap_embed_spaces(c, embed_dim),
        _migrate_embed_spaces_matryoshka,
        _migrate_embed_spaces_element_type,
        _migrate_normalize_source_uri,
        _migrate_extraction_source,
    )
    for migration in migrations:
        migration(conn)


def init_schema(conn: sqlite3.Connection) -> None:
    """Bring *conn*'s database to the current schema (idempotent orchestrator).

    Validate-then-converge (#450, read-validates split, ME schema.rs:96-114): a
    stamped DB at the current version early-returns; a future or behind version
    raises. A fresh or legacy-unversioned DB seeds config, builds the base schema,
    runs the ordered migration chain, then stamps the baseline version.
    ``_migrate.CURRENT_SCHEMA_VERSION`` is read live off the module so tests can
    monkeypatch it.
    """
    current = _migrate.CURRENT_SCHEMA_VERSION
    version = _migrate.get_schema_version(conn)  # None tolerates a missing config table
    if version is not None:
        if version == current:
            return  # already at the current schema — cheap no-op, no rebuild
        if version > current:
            raise KnowledgeBaseError(
                f"database schema v{version} is newer than this build (v{current}); upgrade the code"
            )
        # version < current: do NOT auto-migrate on this hot path (per-boot backup
        # + v2+ chain belongs in the explicit `migrate` command).
        raise KnowledgeBaseError(
            f"database schema v{version} is behind v{current}; run `knowledge-base-ingest migrate`"
        )

    embed_dim = _seed_default_config(conn)
    _create_base_schema(conn, embed_dim)
    conn.commit()
    _run_migrations(conn, embed_dim)

    # Stamp the baseline version (#450). Reached only for a fresh/legacy-unversioned
    # DB (an already-current DB early-returns above); the idempotent builds have
    # converged it to the v1 contract.
    _migrate.set_schema_version(conn, _migrate.CURRENT_SCHEMA_VERSION)
    conn.commit()
