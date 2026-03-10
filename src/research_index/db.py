"""SQLite database setup with FTS5 + sqlite-vec."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import sqlite_vec

DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "research-index" / "research.db"
# Bootstrap defaults for fresh databases. Existing databases read from the
# config table — these constants are only used during initial schema creation.
DEFAULT_EMBED_MODEL = "bge-m3"
DEFAULT_EMBED_DIM = 1024

RELATIONSHIP_TYPES = (
    "extends",
    "contradicts",
    "replicates",
    "cites",
    "compares",
    "applies",
    "implements",
)


def _relationship_check_constraint() -> str:
    values = ", ".join(f"'{t}'" for t in RELATIONSHIP_TYPES)
    return f"CHECK(relation_type IN ({values}))"


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
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
    if not row or "'applies'" in row[0]:
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
        conn.execute(
            "INSERT INTO config (key, value) VALUES ('embed_model', ?)",
            (DEFAULT_EMBED_MODEL,),
        )
        conn.execute(
            "INSERT INTO config (key, value) VALUES ('embed_dim', ?)",
            (str(DEFAULT_EMBED_DIM),),
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
    """)

    conn.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('llm_provider', 'ollama')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('llm_model', 'qwen3.5:27b')"
    )

    conn.commit()

    _migrate_source_type_figure(conn)
    _migrate_relationship_types(conn)
    _migrate_papers_fts(conn)
