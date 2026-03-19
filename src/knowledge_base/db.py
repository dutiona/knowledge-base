"""SQLite database setup with FTS5 + sqlite-vec."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import sqlite_vec

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
    conn: sqlite3.Connection, sql_template: str, ids: list
) -> list[sqlite3.Row]:
    """Execute a SELECT with an IN clause in batches, returning all rows."""
    results: list[sqlite3.Row] = []
    for i in range(0, len(ids), _SQL_BATCH_SIZE):
        batch = ids[i : i + _SQL_BATCH_SIZE]
        placeholders = ",".join("?" * len(batch))
        results.extend(
            conn.execute(
                sql_template.replace("{ph}", placeholders, 1), batch
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
            SELECT DISTINCT source_uri, session_id
            FROM chunks
            WHERE session_id IS NOT NULL
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

    conn.executescript("""
    PRAGMA foreign_keys = OFF;
    BEGIN;
    CREATE TABLE chunks_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content_hash TEXT NOT NULL UNIQUE,
        content TEXT NOT NULL,
        source_type TEXT NOT NULL CHECK(source_type IN ('pdf', 'markdown', 'code', 'web', 'note', 'figure')),
        source_uri TEXT NOT NULL,
        chunk_index INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        metadata TEXT DEFAULT '{}'
    );

    INSERT INTO chunks_new SELECT * FROM chunks;
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

    conn.commit()

    _migrate_source_type_figure(conn)
    _migrate_relationship_types(conn)
    _migrate_papers_fts(conn)
    _migrate_session_id(conn)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunks_session_id ON chunks(session_id)"
    )
    _migrate_paper_paths(conn)
    _migrate_jobs_types(conn)
    _migrate_chunk_sessions(conn)
