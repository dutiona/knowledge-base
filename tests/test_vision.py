"""Tests for vision/figure extraction schema support."""

import sqlite3

from research_index.db import get_connection, init_schema, EMBED_DIM


OLD_SCHEMA_SQL = f"""
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT NOT NULL UNIQUE,
    content TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK(source_type IN ('pdf', 'markdown', 'code', 'web', 'note')),
    source_uri TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    metadata TEXT DEFAULT '{{}}'
);

CREATE VIRTUAL TABLE chunks_fts USING fts5(
    content,
    content='chunks',
    content_rowid='id',
    tokenize='porter unicode61'
);

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

CREATE VIRTUAL TABLE chunks_vec USING vec0(
    embedding float[{EMBED_DIM}],
    +chunk_id INTEGER
);

CREATE TABLE papers (
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

CREATE TABLE relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_paper_id INTEGER NOT NULL REFERENCES papers(id),
    target_paper_id INTEGER NOT NULL REFERENCES papers(id),
    relation_type TEXT NOT NULL CHECK(relation_type IN ('extends', 'contradicts', 'replicates', 'cites', 'compares')),
    confidence REAL DEFAULT 1.0,
    evidence_chunk_id INTEGER REFERENCES chunks(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source_paper_id, target_paper_id, relation_type)
);

CREATE TABLE conclusions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    source_chunk_ids TEXT NOT NULL DEFAULT '[]',
    session_context TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    superseded_by INTEGER REFERENCES conclusions(id)
);

CREATE TABLE executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT NOT NULL,
    result_summary TEXT,
    conclusion_ids TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE methods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    paper_id INTEGER NOT NULL REFERENCES papers(id),
    description TEXT,
    chunk_id INTEGER REFERENCES chunks(id),
    UNIQUE(name, paper_id)
);

CREATE TABLE datasets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    paper_id INTEGER NOT NULL REFERENCES papers(id),
    description TEXT,
    chunk_id INTEGER REFERENCES chunks(id),
    UNIQUE(name, paper_id)
);

CREATE TABLE metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT,
    dataset_id INTEGER REFERENCES datasets(id),
    method_id INTEGER REFERENCES methods(id),
    paper_id INTEGER NOT NULL REFERENCES papers(id),
    chunk_id INTEGER REFERENCES chunks(id)
);

CREATE TABLE entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name TEXT NOT NULL,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('method', 'dataset', 'metric')),
    paper_id INTEGER NOT NULL REFERENCES papers(id),
    description TEXT,
    UNIQUE(canonical_name, entity_type, paper_id)
);

CREATE TABLE entity_mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    surface_form TEXT NOT NULL,
    chunk_id INTEGER NOT NULL REFERENCES chunks(id),
    confidence REAL DEFAULT 1.0,
    UNIQUE(entity_id, surface_form, chunk_id)
);

CREATE TABLE config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def test_schema_accepts_figure_source_type(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('fig_hash', 'Figure 1: architecture diagram', 'figure', '/tmp/paper.pdf#fig1', 0)"
    )
    conn.commit()

    row = conn.execute("SELECT source_type FROM chunks WHERE content_hash = 'fig_hash'").fetchone()
    assert row["source_type"] == "figure"


def test_migration_preserves_existing_data(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)

    # Create old schema without 'figure' in the CHECK constraint
    conn.executescript(OLD_SCHEMA_SQL)
    conn.commit()

    # Insert data with old schema
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('old_hash_1', 'some pdf content', 'pdf', '/tmp/paper.pdf', 0)"
    )
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('old_hash_2', 'a note', 'note', '/tmp/note.md', 0)"
    )
    conn.commit()

    # Verify 'figure' is rejected by old schema
    with_error = False
    try:
        conn.execute(
            "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
            "VALUES ('fig_test', 'fig content', 'figure', '/tmp/fig.png', 0)"
        )
    except sqlite3.IntegrityError:
        with_error = True
    assert with_error, "Old schema should reject 'figure' source_type"

    # Run init_schema which should trigger migration
    init_schema(conn)

    # Verify old data is preserved
    rows = conn.execute("SELECT content_hash, source_type FROM chunks ORDER BY id").fetchall()
    assert len(rows) == 2
    assert rows[0]["content_hash"] == "old_hash_1"
    assert rows[0]["source_type"] == "pdf"
    assert rows[1]["content_hash"] == "old_hash_2"
    assert rows[1]["source_type"] == "note"

    # Verify 'figure' inserts now work
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('fig_hash', 'Figure 1: architecture', 'figure', '/tmp/paper.pdf#fig1', 0)"
    )
    conn.commit()

    fig_row = conn.execute("SELECT source_type FROM chunks WHERE content_hash = 'fig_hash'").fetchone()
    assert fig_row["source_type"] == "figure"

    # Verify FTS still works after migration
    fts_rows = conn.execute(
        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'architecture'"
    ).fetchall()
    assert len(fts_rows) == 1
