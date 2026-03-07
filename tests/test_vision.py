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


# ---------------------------------------------------------------------------
# Step 1: Config functions
# ---------------------------------------------------------------------------


def test_get_vision_config_defaults(tmp_path):
    """Defaults returned when no vision config rows exist."""
    from research_index.vision import _get_vision_config

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    cfg = _get_vision_config(conn)
    assert cfg["model"] == "gemma3:27b"
    assert isinstance(cfg["base_url"], str)
    assert cfg["base_url"]  # non-empty


def test_configure_vision_roundtrip(tmp_path):
    """Set values, read them back."""
    from research_index.vision import _get_vision_config, configure_vision

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    result = configure_vision(conn, model="llava:13b", base_url="http://localhost:11434")
    assert result["model"] == "llava:13b"
    assert result["base_url"] == "http://localhost:11434"

    cfg = _get_vision_config(conn)
    assert cfg["model"] == "llava:13b"
    assert cfg["base_url"] == "http://localhost:11434"


# ---------------------------------------------------------------------------
# Step 2: Figure validation
# ---------------------------------------------------------------------------


def test_validate_figure_valid():
    """Full valid input passes through."""
    from research_index.vision import _validate_figure

    obj = {
        "figure_type": "diagram",
        "description": "Architecture overview",
        "title": "Fig 1",
        "entities_mentioned": ["ResNet", "BERT"],
    }
    result = _validate_figure(obj)
    assert result is not None
    assert result["figure_type"] == "diagram"
    assert result["description"] == "Architecture overview"
    assert result["title"] == "Fig 1"
    assert result["entities_mentioned"] == ["ResNet", "BERT"]


def test_validate_figure_coerces_optionals():
    """Missing title/entities get defaults."""
    from research_index.vision import _validate_figure

    obj = {"figure_type": "chart", "description": "Loss curves"}
    result = _validate_figure(obj)
    assert result is not None
    assert result["title"] is None
    assert result["entities_mentioned"] == []


def test_validate_figure_rejects_empty_description():
    """Empty description returns None."""
    from research_index.vision import _validate_figure

    result = _validate_figure({"figure_type": "chart", "description": ""})
    assert result is None


def test_validate_figure_rejects_missing_type():
    """Missing figure_type returns None."""
    from research_index.vision import _validate_figure

    result = _validate_figure({"description": "something"})
    assert result is None


# ---------------------------------------------------------------------------
# Step 3: Page rendering
# ---------------------------------------------------------------------------

import fitz as _fitz
import pytest


def _make_test_pdf(path, pages_text: list[str]) -> str:
    """Create a minimal PDF with given page texts."""
    doc = _fitz.open()
    for text in pages_text:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()
    return str(path)


def test_render_page_valid_png(tmp_path):
    """Render page 0 and check PNG header."""
    from research_index.vision import _render_page

    pdf_path = _make_test_pdf(tmp_path / "test.pdf", ["Hello World"])
    png_bytes = _render_page(pdf_path, 0)
    assert png_bytes[:4] == b"\x89PNG"


def test_render_page_out_of_range(tmp_path):
    """Out-of-range page raises IndexError."""
    from research_index.vision import _render_page

    pdf_path = _make_test_pdf(tmp_path / "test.pdf", ["Only page"])
    with pytest.raises(IndexError):
        _render_page(pdf_path, 5)


# ---------------------------------------------------------------------------
# Step 4: Heuristic filter
# ---------------------------------------------------------------------------


def test_heuristic_filter_caption_cues(tmp_path):
    """Page with 'Figure 1: Test' is selected, plain text pages are not."""
    from research_index.vision import _heuristic_filter

    pdf_path = _make_test_pdf(
        tmp_path / "test.pdf",
        ["Plain text only", "Figure 1: Test diagram", "More plain text"],
    )
    candidates = _heuristic_filter(pdf_path)
    assert 1 in candidates  # page with caption cue
    # Should not include all pages (at least one excluded)
    assert len(candidates) < 3


def test_heuristic_filter_fallback_all_pages(tmp_path):
    """All-text PDF with no signals returns all pages."""
    from research_index.vision import _heuristic_filter

    pdf_path = _make_test_pdf(
        tmp_path / "test.pdf",
        ["Just some text", "Another paragraph", "Third page of text"],
    )
    candidates = _heuristic_filter(pdf_path)
    assert candidates == [0, 1, 2]


# ---------------------------------------------------------------------------
# Step 6: Source URI helper
# ---------------------------------------------------------------------------


def test_get_paper_source_uri_found(tmp_path):
    """Paper with abstract_chunk_id resolves to source_uri."""
    from research_index.vision import _get_paper_source_uri

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('abs_hash', 'abstract text', 'pdf', '/tmp/paper.pdf', 0)"
    )
    chunk_id = conn.execute("SELECT id FROM chunks WHERE content_hash = 'abs_hash'").fetchone()["id"]
    conn.execute(
        "INSERT INTO papers (title, abstract_chunk_id) VALUES ('Test Paper', ?)",
        (chunk_id,),
    )
    paper_id = conn.execute("SELECT id FROM papers WHERE title = 'Test Paper'").fetchone()["id"]
    conn.commit()

    uri = _get_paper_source_uri(conn, paper_id)
    assert uri == "/tmp/paper.pdf"


def test_get_paper_source_uri_not_found(tmp_path):
    """Paper without abstract_chunk_id returns None."""
    from research_index.vision import _get_paper_source_uri

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    conn.execute("INSERT INTO papers (title) VALUES ('No Abstract Paper')")
    paper_id = conn.execute("SELECT id FROM papers WHERE title = 'No Abstract Paper'").fetchone()["id"]
    conn.commit()

    uri = _get_paper_source_uri(conn, paper_id)
    assert uri is None


# ---------------------------------------------------------------------------
# Step 5: Vision API call
# ---------------------------------------------------------------------------

import json
from unittest.mock import MagicMock, patch


def _mock_httpx_response(content: str, status_code: int = 200) -> MagicMock:
    """Build a mock httpx response with the given content string."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}],
    }
    resp.raise_for_status = MagicMock()
    return resp


def test_vision_call_parses_valid_response():
    """Valid JSON array response is parsed and validated."""
    from research_index.vision import _vision_call

    figures = [
        {
            "figure_type": "diagram",
            "title": "Fig 1",
            "description": "Architecture overview",
            "entities_mentioned": ["BERT"],
        }
    ]
    mock_resp = _mock_httpx_response(json.dumps(figures))

    with patch("research_index.vision.httpx.post", return_value=mock_resp) as mock_post:
        result = _vision_call(
            "base64data", "prompt", base_url="http://localhost:11434", model="gemma3:27b"
        )

    assert len(result) == 1
    assert result[0]["figure_type"] == "diagram"
    assert result[0]["description"] == "Architecture overview"
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert "http://localhost:11434/v1/chat/completions" in call_kwargs.args or \
           call_kwargs.kwargs.get("url", call_kwargs.args[0] if call_kwargs.args else "") == "http://localhost:11434/v1/chat/completions"


def test_vision_call_strips_markdown_fences():
    """Response wrapped in ```json ... ``` is parsed correctly."""
    from research_index.vision import _vision_call

    figures = [{"figure_type": "chart", "description": "Loss curves", "title": None, "entities_mentioned": []}]
    content = f"```json\n{json.dumps(figures)}\n```"
    mock_resp = _mock_httpx_response(content)

    with patch("research_index.vision.httpx.post", return_value=mock_resp):
        result = _vision_call(
            "base64data", "prompt", base_url="http://localhost:11434", model="test"
        )

    assert len(result) == 1
    assert result[0]["figure_type"] == "chart"


def test_vision_call_filters_invalid_figures():
    """Mix of valid and invalid objects: only valid ones returned."""
    from research_index.vision import _vision_call

    figures = [
        {"figure_type": "diagram", "description": "Valid figure"},
        {"figure_type": "", "description": "Missing type"},  # invalid: empty type
        {"description": "No type field"},  # invalid: no figure_type
        {"figure_type": "table", "description": "Another valid one"},
    ]
    mock_resp = _mock_httpx_response(json.dumps(figures))

    with patch("research_index.vision.httpx.post", return_value=mock_resp):
        result = _vision_call(
            "base64data", "prompt", base_url="http://localhost:11434", model="test"
        )

    assert len(result) == 2
    assert result[0]["figure_type"] == "diagram"
    assert result[1]["figure_type"] == "table"


def test_vision_call_unwraps_dict_wrapper():
    """Response as {"figures": [...]} is unwrapped."""
    from research_index.vision import _vision_call

    figures = [{"figure_type": "photo", "description": "A photograph"}]
    wrapper = {"figures": figures}
    mock_resp = _mock_httpx_response(json.dumps(wrapper))

    with patch("research_index.vision.httpx.post", return_value=mock_resp):
        result = _vision_call(
            "base64data", "prompt", base_url="http://localhost:11434", model="test"
        )

    assert len(result) == 1
    assert result[0]["figure_type"] == "photo"
