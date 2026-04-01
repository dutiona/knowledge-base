"""SQLite database setup with FTS5 + sqlite-vec."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import sqlite_vec

from .utils import serialize_f32 as _serialize_f32  # shared vec serialization

__all__ = [
    "DEFAULT_DB_PATH",
    "DEFAULT_EMBED_DIM",
    "DEFAULT_EMBED_MODEL",
    "DEFAULT_EMBED_PROVIDER",
    "RELATIONSHIP_TYPES",
    "co_occurrence_pairs",
    "delete_chunk_vecs",
    "delete_chunks_cascade",
    "escape_like",
    "get_active_space",
    "get_connection",
    "get_vec_table_name",
    "init_schema",
    "insert_chunk_vec",
    "space_table_name",
]

DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "knowledge-base" / "knowledge.db"
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


def _relationship_check_constraint() -> str:
    values = ", ".join(f"'{t}'" for t in RELATIONSHIP_TYPES)
    return f"CHECK(relation_type IN ({values}))"


# SQLite's default SQLITE_MAX_VARIABLE_NUMBER is 999.
# We use 900 to leave headroom for other parameters in the same statement.
_SQL_BATCH_SIZE = 900


def _batched_execute(
    conn: sqlite3.Connection,
    sql_template: str,
    ids: list,
    extra_params: list | None = None,
) -> None:
    """Execute a SQL statement with an IN clause in batches.

    sql_template must contain a single ``{ph}`` placeholder where the
    ``IN (?,?,...)`` list will be substituted.  extra_params (if given)
    are prepended to each batch's parameter list.
    """
    for i in range(0, len(ids), _SQL_BATCH_SIZE):
        batch = ids[i : i + _SQL_BATCH_SIZE]
        placeholders = ",".join("?" * len(batch))
        params = list(extra_params or []) + list(batch)
        conn.execute(sql_template.replace("{ph}", placeholders, 1), params)


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


def co_occurrence_pairs(conn: sqlite3.Connection, min_sessions: int = 1) -> list[dict]:
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


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate_source_type_figure(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='chunks'"
    ).fetchone()
    if not row or "'figure'" in row[0]:
        return

    # Determine columns in the old table to handle INSERT INTO ... SELECT
    columns = {row[1] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()}
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
        source_type TEXT NOT NULL CHECK(source_type IN ('pdf', 'markdown', 'code', 'web', 'note', 'figure')),
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
    """)


def _migrate_relationship_types(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='relationships'"
    ).fetchone()
    if not row or "'similar'" in row[0]:
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
    """)


def _migrate_papers_fts(conn: sqlite3.Connection) -> None:
    """Backfill papers_fts for databases created before this index existed."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='papers_fts'"
    ).fetchone()
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
    columns = {row[1] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()}
    if "session_id" in columns:
        return
    conn.execute("ALTER TABLE chunks ADD COLUMN session_id TEXT")
    conn.commit()


def _migrate_chunk_strategy(conn: sqlite3.Connection) -> None:
    """Add chunk_strategy column to chunks for dual chunking support."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()}
    if "chunk_strategy" in columns:
        return
    conn.execute(
        "ALTER TABLE chunks ADD COLUMN chunk_strategy TEXT NOT NULL DEFAULT 'mechanical'"
    )
    conn.commit()


def _migrate_paper_paths(conn: sqlite3.Connection) -> None:
    """Populate paper_paths from existing paper -> abstract_chunk -> source_uri links.

    Idempotent: backfills missing entries per-paper (not all-or-nothing).
    """
    conn.execute("""
        INSERT OR IGNORE INTO paper_paths (paper_id, path, is_primary)
        SELECT p.id, c.source_uri, TRUE
        FROM papers p
        JOIN chunks c ON c.id = p.abstract_chunk_id
        WHERE p.abstract_chunk_id IS NOT NULL
          AND p.id NOT IN (SELECT paper_id FROM paper_paths)
    """)
    conn.commit()


def _migrate_jobs_types(conn: sqlite3.Connection) -> None:
    """Add 'auto_relate' to jobs.job_type CHECK constraint for existing DBs."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if not row or "'auto_relate'" in row[0]:
        return

    conn.executescript("""
    PRAGMA foreign_keys = OFF;
    BEGIN;
    CREATE TABLE jobs_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
        job_type TEXT NOT NULL CHECK(job_type IN ('extract_structure', 'extract_figures', 'auto_relate')),
        params TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending', 'running', 'completed', 'failed')),
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
    """)


def _migrate_chunk_sessions(conn: sqlite3.Connection) -> None:
    """Create chunk_sessions join table and backfill from chunks.session_id."""
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='chunk_sessions'"
    ).fetchone()
    if exists:
        return
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunk_sessions (
            chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
            session_id TEXT NOT NULL,
            UNIQUE(chunk_id, session_id)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunk_sessions_session "
        "ON chunk_sessions(session_id)"
    )
    # Backfill from existing chunks.session_id
    conn.execute("""
        INSERT OR IGNORE INTO chunk_sessions (chunk_id, session_id)
        SELECT id, session_id FROM chunks WHERE session_id IS NOT NULL
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Embedding space helpers (#99)
# ---------------------------------------------------------------------------

_SPACE_NAME_RE = re.compile(r"^[a-zA-Z0-9_]+$")


def space_table_name(name: str) -> str:
    """Deterministic vec table name for an embedding space."""
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    return f"chunks_vec_{sanitized}"


def get_active_space(conn: sqlite3.Connection) -> dict | None:
    """Return the active embedding space row as a dict, or None."""
    row = conn.execute("SELECT * FROM embed_spaces WHERE status = 'active'").fetchone()
    if row is None:
        return None
    return dict(row)


def get_vec_table_name(conn: sqlite3.Connection) -> str:
    """Return the active space's vec table name, falling back to 'chunks_vec'."""
    row = conn.execute(
        "SELECT table_name FROM embed_spaces WHERE status = 'active'"
    ).fetchone()
    if row is None:
        return "chunks_vec"
    return row["table_name"]


def get_active_chunk_strategy(conn: sqlite3.Connection) -> str:
    """Return the active space's chunk_strategy, falling back to 'mechanical'."""
    row = conn.execute(
        "SELECT chunk_strategy FROM embed_spaces WHERE status = 'active'"
    ).fetchone()
    if row is None:
        return "mechanical"
    return row["chunk_strategy"]


def insert_chunk_vec(
    conn: sqlite3.Connection,
    chunk_id: int,
    embedding: list[float],
    table_name: str | None = None,
) -> None:
    """Insert a single chunk embedding into the specified (or active) vec table."""
    tbl = table_name or get_vec_table_name(conn)
    conn.execute(
        f"INSERT INTO [{tbl}] (rowid, embedding, chunk_id) VALUES (?, ?, ?)",
        (chunk_id, _serialize_f32(embedding), chunk_id),
    )
    # Keep embed_spaces.chunk_count in sync for the active space
    conn.execute(
        "UPDATE embed_spaces SET chunk_count = chunk_count + 1 "
        "WHERE table_name = ? AND status = 'active'",
        (tbl,),
    )


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
    tbl = table_name or get_vec_table_name(conn)
    # Count actual rows before deletion — not all chunks may have embeddings
    count_rows = _batched_select(
        conn, f"SELECT COUNT(*) AS n FROM [{tbl}] WHERE chunk_id IN ({{ph}})", chunk_ids
    )
    actual_deleted = sum(r["n"] for r in count_rows)
    _batched_execute(conn, f"DELETE FROM [{tbl}] WHERE chunk_id IN ({{ph}})", chunk_ids)
    # Keep embed_spaces.chunk_count in sync
    if actual_deleted:
        conn.execute(
            "UPDATE embed_spaces SET chunk_count = MAX(0, chunk_count - ?) "
            "WHERE table_name = ?",
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
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(embed_spaces)").fetchall()
    }
    if "matryoshka_base_dim" in columns:
        return
    conn.execute("ALTER TABLE embed_spaces ADD COLUMN matryoshka_base_dim INTEGER")
    conn.commit()


def _bootstrap_embed_spaces(conn: sqlite3.Connection, embed_dim: int) -> None:
    """Register the default embedding space if embed_spaces is empty.

    Works for both fresh DBs (chunks_vec just created, 0 rows) and legacy
    DBs (chunks_vec has data). Idempotent — skips if any space already exists.
    """
    existing = conn.execute("SELECT 1 FROM embed_spaces LIMIT 1").fetchone()
    if existing:
        return

    model_row = conn.execute(
        "SELECT value FROM config WHERE key = 'embed_model'"
    ).fetchone()
    provider_row = conn.execute(
        "SELECT value FROM config WHERE key = 'embed_provider'"
    ).fetchone()

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
        """INSERT INTO embed_spaces
           (name, model, provider, dim, chunk_strategy, status, table_name, chunk_count)
           VALUES (?, ?, ?, ?, ?, 'active', 'chunks_vec', ?)""",
        ("default", model, provider, embed_dim, strategy, count),
    )
    conn.commit()


def init_schema(conn: sqlite3.Connection) -> None:
    # --- Create config table first so we can read embed_dim before chunks_vec ---
    conn.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    existing = conn.execute(
        "SELECT value FROM config WHERE key = 'embed_model'"
    ).fetchone()
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

    # Read the configured dimension (handles both fresh and existing DBs)
    dim_row = conn.execute(
        "SELECT value FROM config WHERE key = 'embed_dim'"
    ).fetchone()
    embed_dim = int(dim_row["value"]) if dim_row else DEFAULT_EMBED_DIM

    conn.executescript(f"""
    CREATE TABLE IF NOT EXISTS chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content_hash TEXT NOT NULL UNIQUE,
        content TEXT NOT NULL,
        source_type TEXT NOT NULL CHECK(source_type IN ('pdf', 'markdown', 'code', 'web', 'note', 'figure')),
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
        UNIQUE(name, paper_id)
    );

    CREATE TABLE IF NOT EXISTS datasets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        paper_id INTEGER NOT NULL REFERENCES papers(id),
        description TEXT,
        chunk_id INTEGER REFERENCES chunks(id),
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
        chunk_id INTEGER REFERENCES chunks(id)
    );

    CREATE TABLE IF NOT EXISTS entities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_name TEXT NOT NULL,
        entity_type TEXT NOT NULL CHECK(entity_type IN ('method', 'dataset', 'metric')),
        paper_id INTEGER NOT NULL REFERENCES papers(id),
        description TEXT,
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
        job_type TEXT NOT NULL CHECK(job_type IN ('extract_structure', 'extract_figures', 'auto_relate')),
        params TEXT NOT NULL DEFAULT '{{}}',
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending', 'running', 'completed', 'failed')),
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
            CHECK(matryoshka_base_dim IS NULL OR matryoshka_base_dim > dim)
    );
    """)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_paper_paths_paper_id ON paper_paths(paper_id, is_primary)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_paper_paths_hash ON paper_paths(content_hash)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_paths_one_primary ON paper_paths(paper_id) WHERE is_primary = TRUE"
    )
    conn.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('llm_provider', 'ollama')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('llm_model', 'qwen3.5:27b')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('prediction_error_threshold', '0.025')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('auto_relate_propose_threshold', '0.82')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('auto_relate_accept_threshold', '0.95')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('chunk_strategy', 'mechanical')"
    )

    # Enforce at most one active embedding space at the DB level
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_embed_spaces_one_active "
        "ON embed_spaces(status) WHERE status = 'active'"
    )

    conn.commit()

    _migrate_source_type_figure(conn)
    _migrate_relationship_types(conn)
    _migrate_papers_fts(conn)
    _migrate_session_id(conn)
    _migrate_chunk_strategy(conn)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunks_session_id ON chunks(session_id)"
    )
    _migrate_paper_paths(conn)
    _migrate_jobs_types(conn)
    _migrate_chunk_sessions(conn)
    _bootstrap_embed_spaces(conn, embed_dim)
    _migrate_embed_spaces_matryoshka(conn)
