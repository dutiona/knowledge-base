"""Tests for vision/figure extraction schema support."""

import json
import pytest
import sqlite3
from pathlib import Path
from unittest.mock import patch

from knowledge_base.db import get_connection, init_schema, DEFAULT_EMBED_DIM
from knowledge_base.vision import _FIGURE_BASE, _FIGS_PER_PAGE


@pytest.fixture
def vision_conn(tmp_path):
    """Create a temporary DB with full schema for vision tests."""
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    return conn


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
    embedding float[{DEFAULT_EMBED_DIM}],
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

    row = conn.execute(
        "SELECT source_type FROM chunks WHERE content_hash = 'fig_hash'"
    ).fetchone()
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
    rows = conn.execute(
        "SELECT content_hash, source_type FROM chunks ORDER BY id"
    ).fetchall()
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

    fig_row = conn.execute(
        "SELECT source_type FROM chunks WHERE content_hash = 'fig_hash'"
    ).fetchone()
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
    from knowledge_base.vision import _get_vision_config

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    cfg = _get_vision_config(conn)
    assert cfg["model"] == "gemma3:27b"
    assert isinstance(cfg["base_url"], str)
    assert cfg["base_url"]  # non-empty


@pytest.mark.parametrize(
    "input_url,expected",
    [
        ("http://host:1234", "http://host:1234"),
        ("http://host:1234/v1", "http://host:1234"),
        ("http://host:1234/v1/", "http://host:1234"),
        ("https://api.openai.com/v1", "https://api.openai.com"),
        ("http://host:1234/", "http://host:1234"),
        ("http://host/v1beta", "http://host/v1beta"),
    ],
)
def test_get_vision_config_strips_v1(tmp_path, input_url, expected):
    from knowledge_base.vision import _get_vision_config

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('vision_base_url', ?)",
        (input_url,),
    )
    conn.commit()
    cfg = _get_vision_config(conn)
    assert cfg["base_url"] == expected


def test_configure_vision_roundtrip(tmp_path):
    """Set values, read them back."""
    from knowledge_base.vision import _get_vision_config, configure_vision

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    result = configure_vision(
        conn, model="llava:13b", base_url="http://localhost:11434"
    )
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
    from knowledge_base.vision import _validate_figure

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
    from knowledge_base.vision import _validate_figure

    obj = {"figure_type": "chart", "description": "Loss curves"}
    result = _validate_figure(obj)
    assert result is not None
    assert result["title"] is None
    assert result["entities_mentioned"] == []


def test_validate_figure_rejects_empty_description():
    """Empty description returns None."""
    from knowledge_base.vision import _validate_figure

    result = _validate_figure({"figure_type": "chart", "description": ""})
    assert result is None


def test_validate_figure_rejects_missing_type():
    """Missing figure_type returns None."""
    from knowledge_base.vision import _validate_figure

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
    from knowledge_base.vision import _render_page

    pdf_path = _make_test_pdf(tmp_path / "test.pdf", ["Hello World"])
    png_bytes = _render_page(pdf_path, 0)
    assert png_bytes[:4] == b"\x89PNG"


def test_render_page_out_of_range(tmp_path):
    """Out-of-range page raises IndexError."""
    from knowledge_base.vision import _render_page

    pdf_path = _make_test_pdf(tmp_path / "test.pdf", ["Only page"])
    with pytest.raises(IndexError):
        _render_page(pdf_path, 5)


# ---------------------------------------------------------------------------
# Step 4: Heuristic filter
# ---------------------------------------------------------------------------


def test_heuristic_filter_caption_cues(tmp_path):
    """Page with 'Figure 1: Test' is selected, plain text pages are not."""
    from knowledge_base.vision import _heuristic_filter

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
    from knowledge_base.vision import _heuristic_filter

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
    from knowledge_base.vision import _get_paper_source_uri

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('abs_hash', 'abstract text', 'pdf', '/tmp/paper.pdf', 0)"
    )
    chunk_id = conn.execute(
        "SELECT id FROM chunks WHERE content_hash = 'abs_hash'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO papers (title, abstract_chunk_id) VALUES ('Test Paper', ?)",
        (chunk_id,),
    )
    paper_id = conn.execute(
        "SELECT id FROM papers WHERE title = 'Test Paper'"
    ).fetchone()["id"]
    conn.commit()

    uri = _get_paper_source_uri(conn, paper_id)
    assert uri == "/tmp/paper.pdf"


def test_get_paper_source_uri_not_found(tmp_path):
    """Paper without abstract_chunk_id returns None."""
    from knowledge_base.vision import _get_paper_source_uri

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    conn.execute("INSERT INTO papers (title) VALUES ('No Abstract Paper')")
    paper_id = conn.execute(
        "SELECT id FROM papers WHERE title = 'No Abstract Paper'"
    ).fetchone()["id"]
    conn.commit()

    uri = _get_paper_source_uri(conn, paper_id)
    assert uri is None


# ---------------------------------------------------------------------------
# Step 5: Vision API call
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock


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
    from knowledge_base.vision import _vision_call

    figures = [
        {
            "figure_type": "diagram",
            "title": "Fig 1",
            "description": "Architecture overview",
            "entities_mentioned": ["BERT"],
        }
    ]
    mock_resp = _mock_httpx_response(json.dumps(figures))

    with patch("knowledge_base.vision.httpx.post", return_value=mock_resp) as mock_post:
        result = _vision_call(
            "base64data",
            "prompt",
            base_url="http://localhost:11434",
            model="gemma3:27b",
        )

    assert len(result) == 1
    assert result[0]["figure_type"] == "diagram"
    assert result[0]["description"] == "Architecture overview"
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert (
        "http://localhost:11434/v1/chat/completions" in call_kwargs.args
        or call_kwargs.kwargs.get(
            "url", call_kwargs.args[0] if call_kwargs.args else ""
        )
        == "http://localhost:11434/v1/chat/completions"
    )


def test_vision_call_strips_markdown_fences():
    """Response wrapped in ```json ... ``` is parsed correctly."""
    from knowledge_base.vision import _vision_call

    figures = [
        {
            "figure_type": "chart",
            "description": "Loss curves",
            "title": None,
            "entities_mentioned": [],
        }
    ]
    content = f"```json\n{json.dumps(figures)}\n```"
    mock_resp = _mock_httpx_response(content)

    with patch("knowledge_base.vision.httpx.post", return_value=mock_resp):
        result = _vision_call(
            "base64data", "prompt", base_url="http://localhost:11434", model="test"
        )

    assert len(result) == 1
    assert result[0]["figure_type"] == "chart"


def test_vision_call_filters_invalid_figures():
    """Mix of valid and invalid objects: only valid ones returned."""
    from knowledge_base.vision import _vision_call

    figures = [
        {"figure_type": "diagram", "description": "Valid figure"},
        {"figure_type": "", "description": "Missing type"},  # invalid: empty type
        {"description": "No type field"},  # invalid: no figure_type
        {"figure_type": "table", "description": "Another valid one"},
    ]
    mock_resp = _mock_httpx_response(json.dumps(figures))

    with patch("knowledge_base.vision.httpx.post", return_value=mock_resp):
        result = _vision_call(
            "base64data", "prompt", base_url="http://localhost:11434", model="test"
        )

    assert len(result) == 2
    assert result[0]["figure_type"] == "diagram"
    assert result[1]["figure_type"] == "table"


def test_vision_call_unwraps_dict_wrapper():
    """Response as {"figures": [...]} is unwrapped."""
    from knowledge_base.vision import _vision_call

    figures = [{"figure_type": "photo", "description": "A photograph"}]
    wrapper = {"figures": figures}
    mock_resp = _mock_httpx_response(json.dumps(wrapper))

    with patch("knowledge_base.vision.httpx.post", return_value=mock_resp):
        result = _vision_call(
            "base64data", "prompt", base_url="http://localhost:11434", model="test"
        )

    assert len(result) == 1
    assert result[0]["figure_type"] == "photo"


def test_vision_call_malformed_response():
    """Malformed API response (missing choices key) raises ValueError."""
    from knowledge_base.vision import _vision_call

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"error": "something went wrong"}
    resp.raise_for_status = MagicMock()

    with patch("knowledge_base.vision.httpx.post", return_value=resp):
        with pytest.raises(ValueError, match="Malformed vision API response"):
            _vision_call(
                "base64data",
                "prompt",
                base_url="http://localhost:11434",
                model="test",
            )


def test_vision_call_skips_non_dict_items():
    """Non-dict items in the response list are silently skipped."""
    from knowledge_base.vision import _vision_call

    mixed = [
        {"figure_type": "diagram", "description": "Valid figure"},
        None,
        "stray string",
        42,
        {"figure_type": "table", "description": "Another valid one"},
    ]
    mock_resp = _mock_httpx_response(json.dumps(mixed))

    with patch("knowledge_base.vision.httpx.post", return_value=mock_resp):
        result = _vision_call(
            "base64data", "prompt", base_url="http://localhost:11434", model="test"
        )

    assert len(result) == 2
    assert result[0]["figure_type"] == "diagram"
    assert result[1]["figure_type"] == "table"


# ---------------------------------------------------------------------------
# Step 7: Orchestrator — extract_figures
# ---------------------------------------------------------------------------


def _setup_paper_with_pdf(tmp_path, pages_text: list[str] | None = None):
    """Helper: create a DB with a paper linked to a real test PDF.

    Returns (conn, paper_id, pdf_path).
    """
    if pages_text is None:
        pages_text = ["Figure 1: architecture", "Plain text page"]

    pdf_path = _make_test_pdf(tmp_path / "paper.pdf", pages_text)

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Insert a chunk so the paper has a source_uri
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('abs_hash', 'abstract text', 'pdf', ?, 0)",
        (pdf_path,),
    )
    chunk_id = conn.execute(
        "SELECT id FROM chunks WHERE content_hash = 'abs_hash'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO papers (title, abstract_chunk_id) VALUES ('Test Paper', ?)",
        (chunk_id,),
    )
    paper_id = conn.execute(
        "SELECT id FROM papers WHERE title = 'Test Paper'"
    ).fetchone()["id"]
    conn.commit()

    return conn, paper_id, pdf_path


def test_extract_figures_paper_not_found(tmp_path):
    """Nonexistent paper_id returns error dict."""
    from knowledge_base.vision import extract_figures

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    result = extract_figures(conn, paper_id=9999)
    assert "error" in result
    assert "not found" in result["error"]


def test_extract_figures_eta_gate(tmp_path):
    """Many candidate pages + not confirmed returns confirm_required."""
    from knowledge_base.vision import extract_figures

    # Create a PDF with 50 pages — all will be candidates (fallback: all pages)
    pages_text = [f"Page {i}" for i in range(50)]
    conn, paper_id, _ = _setup_paper_with_pdf(tmp_path, pages_text)

    result = extract_figures(conn, paper_id=paper_id, confirmed=False)
    assert result.get("confirm_required") is True
    assert result["estimated_seconds"] == 50 * 4
    # No extracted images, all 50 pages go through heuristic fallback
    assert result["vector_pages"] == 50


@patch("knowledge_base.vision._embed_with_config")
@patch("knowledge_base.vision._vision_call")
def test_extract_figures_end_to_end(mock_vision, mock_embed, tmp_path):
    """Full run: mock vision + embed, verify chunks created correctly."""
    from knowledge_base.vision import extract_figures

    mock_figures = [
        {
            "figure_type": "diagram",
            "description": "Test diagram of architecture",
            "title": "Fig 1",
            "entities_mentioned": ["ResNet"],
        }
    ]
    mock_vision.return_value = mock_figures
    mock_embed.return_value = [[0.1] * DEFAULT_EMBED_DIM]

    conn, paper_id, pdf_path = _setup_paper_with_pdf(tmp_path)

    # Pass specific pages so we control which are processed
    result = extract_figures(conn, paper_id=paper_id, pages=[0])

    assert result["pages_processed"] == 1
    assert result["pages_failed"] == 0
    assert result["figures_found"] == 1
    assert result["chunks_created"] == 1
    assert result["errors"] == []

    # Verify chunk in DB
    rows = conn.execute("SELECT * FROM chunks WHERE source_type = 'figure'").fetchall()
    assert len(rows) == 1

    row = rows[0]
    assert row["source_uri"] == pdf_path
    assert row["chunk_index"] >= _FIGURE_BASE

    meta = json.loads(row["metadata"])
    assert meta["page"] == 0
    assert meta["figure_type"] == "diagram"
    assert meta["title"] == "Fig 1"
    assert meta["entities_mentioned"] == ["ResNet"]
    assert "vision_model" in meta

    # Verify vector embedding inserted
    vec_rows = conn.execute(
        "SELECT chunk_id FROM chunks_vec WHERE chunk_id = ?", (row["id"],)
    ).fetchall()
    assert len(vec_rows) == 1


@patch("knowledge_base.vision._embed_with_config")
@patch("knowledge_base.vision._vision_call")
def test_extract_figures_idempotent(mock_vision, mock_embed, tmp_path):
    """Running twice replaces old chunks, no duplicates."""
    from knowledge_base.vision import extract_figures

    mock_figures = [
        {
            "figure_type": "chart",
            "description": "Loss curve over epochs",
            "title": "Fig 2",
            "entities_mentioned": [],
        }
    ]
    mock_vision.return_value = mock_figures
    mock_embed.return_value = [[0.2] * DEFAULT_EMBED_DIM]

    conn, paper_id, _ = _setup_paper_with_pdf(tmp_path)

    # First run
    extract_figures(conn, paper_id=paper_id, pages=[0])
    count_1 = conn.execute(
        "SELECT COUNT(*) as c FROM chunks WHERE source_type = 'figure'"
    ).fetchone()["c"]

    # Second run — old chunks should be deleted first
    # Use a different description so content_hash differs
    mock_figures_2 = [
        {
            "figure_type": "chart",
            "description": "Updated loss curve over epochs v2",
            "title": "Fig 2 updated",
            "entities_mentioned": [],
        }
    ]
    mock_vision.return_value = mock_figures_2
    extract_figures(conn, paper_id=paper_id, pages=[0])
    count_2 = conn.execute(
        "SELECT COUNT(*) as c FROM chunks WHERE source_type = 'figure'"
    ).fetchone()["c"]

    assert count_1 == 1
    assert count_2 == 1  # No duplicates — old one was deleted

    # Verify it's the new one
    row = conn.execute(
        "SELECT content FROM chunks WHERE source_type = 'figure'"
    ).fetchone()
    assert "v2" in row["content"]


@patch("knowledge_base.vision._embed_with_config")
@patch("knowledge_base.vision._vision_call")
def test_extract_figures_idempotent_with_fk_references(
    mock_vision, mock_embed, tmp_path
):
    """Re-extracting figures when entity_mentions/methods/datasets reference
    figure chunk IDs must not raise FOREIGN KEY constraint error (#53)."""
    from knowledge_base.vision import extract_figures

    mock_figures = [
        {
            "figure_type": "diagram",
            "description": "Architecture overview",
            "title": "Fig 1",
            "entities_mentioned": ["BERT"],
        }
    ]
    mock_vision.return_value = mock_figures
    mock_embed.return_value = [[0.1] * DEFAULT_EMBED_DIM]

    conn, paper_id, _ = _setup_paper_with_pdf(tmp_path)

    # First extraction — creates figure chunks
    result1 = extract_figures(conn, paper_id=paper_id, pages=[0])
    assert result1["chunks_created"] == 1

    fig_chunk_id = conn.execute(
        "SELECT id FROM chunks WHERE source_type = 'figure'"
    ).fetchone()["id"]

    # Simulate extract_structure creating FK references to figure chunks:
    # 1. entity_mentions referencing the figure chunk
    conn.execute(
        "INSERT INTO entities (canonical_name, entity_type, paper_id) "
        "VALUES ('BERT', 'method', ?)",
        (paper_id,),
    )
    entity_id = conn.execute(
        "SELECT id FROM entities WHERE canonical_name = 'BERT'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO entity_mentions (entity_id, surface_form, chunk_id) "
        "VALUES (?, 'BERT', ?)",
        (entity_id, fig_chunk_id),
    )

    # 2. methods referencing the figure chunk
    conn.execute(
        "INSERT INTO methods (name, paper_id, chunk_id) VALUES ('BERT', ?, ?)",
        (paper_id, fig_chunk_id),
    )

    # 3. datasets referencing the figure chunk
    conn.execute(
        "INSERT INTO datasets (name, paper_id, chunk_id) VALUES ('ImageNet', ?, ?)",
        (paper_id, fig_chunk_id),
    )
    conn.commit()

    # Re-extract — this triggers chunk deletion. Without FK cleanup, this
    # raises sqlite3.IntegrityError: FOREIGN KEY constraint failed
    mock_figures_2 = [
        {
            "figure_type": "diagram",
            "description": "Updated architecture overview v2",
            "title": "Fig 1 updated",
            "entities_mentioned": ["BERT"],
        }
    ]
    mock_vision.return_value = mock_figures_2
    result2 = extract_figures(conn, paper_id=paper_id, pages=[0])
    assert result2["chunks_created"] == 1

    # entity_mentions referencing old chunk should be deleted
    em_count = conn.execute(
        "SELECT COUNT(*) as c FROM entity_mentions WHERE chunk_id = ?",
        (fig_chunk_id,),
    ).fetchone()["c"]
    assert em_count == 0

    # methods.chunk_id should be NULLed out (not deleted — method still exists)
    method = conn.execute(
        "SELECT chunk_id FROM methods WHERE name = 'BERT' AND paper_id = ?",
        (paper_id,),
    ).fetchone()
    assert method["chunk_id"] is None

    # datasets.chunk_id should be NULLed out
    dataset = conn.execute(
        "SELECT chunk_id FROM datasets WHERE name = 'ImageNet' AND paper_id = ?",
        (paper_id,),
    ).fetchone()
    assert dataset["chunk_id"] is None


@patch("knowledge_base.vision._embed_with_config")
@patch("knowledge_base.vision._vision_call")
def test_extract_figures_pages_hint(mock_vision, mock_embed, tmp_path):
    """Passing specific pages processes only those pages."""
    from knowledge_base.vision import extract_figures

    mock_vision.return_value = [
        {
            "figure_type": "table",
            "description": "Results table",
            "title": "Table 1",
            "entities_mentioned": [],
        }
    ]
    mock_embed.return_value = [[0.3] * DEFAULT_EMBED_DIM]

    conn, paper_id, _ = _setup_paper_with_pdf(
        tmp_path, ["Page 0 text", "Page 1 text", "Page 2 text"]
    )

    result = extract_figures(conn, paper_id=paper_id, pages=[1])

    assert result["pages_processed"] == 1
    # vision_call should have been called exactly once (for page 1)
    assert mock_vision.call_count == 1

    # Verify chunk_index encodes page 1
    row = conn.execute(
        "SELECT chunk_index FROM chunks WHERE source_type = 'figure'"
    ).fetchone()
    assert row["chunk_index"] == _FIGURE_BASE + 1 * _FIGS_PER_PAGE + 0


@patch("knowledge_base.vision._embed_with_config")
@patch("knowledge_base.vision._vision_call")
def test_extract_figures_scoped_delete_preserves_other_pages(
    mock_vision, mock_embed, tmp_path
):
    """Re-extracting a subset of pages must NOT delete figures from other pages (#79)."""
    from knowledge_base.vision import extract_figures

    fig_page0 = [
        {
            "figure_type": "diagram",
            "description": "Architecture on page 0",
            "title": "Fig 1",
            "entities_mentioned": [],
        }
    ]
    fig_page2 = [
        {
            "figure_type": "chart",
            "description": "Results chart on page 2",
            "title": "Fig 3",
            "entities_mentioned": [],
        }
    ]

    conn, paper_id, _ = _setup_paper_with_pdf(tmp_path, ["Page 0", "Page 1", "Page 2"])

    # Extract page 0
    mock_vision.return_value = fig_page0
    mock_embed.return_value = [[0.1] * DEFAULT_EMBED_DIM]
    extract_figures(conn, paper_id=paper_id, pages=[0])

    # Extract page 2
    mock_vision.return_value = fig_page2
    mock_embed.return_value = [[0.2] * DEFAULT_EMBED_DIM]
    extract_figures(conn, paper_id=paper_id, pages=[2])

    # Both pages should have figure chunks
    rows = conn.execute(
        "SELECT chunk_index, content FROM chunks WHERE source_type = 'figure' ORDER BY chunk_index"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["chunk_index"] == _FIGURE_BASE + 0 * _FIGS_PER_PAGE + 0  # page 0
    assert rows[1]["chunk_index"] == _FIGURE_BASE + 2 * _FIGS_PER_PAGE + 0  # page 2

    # Re-extract page 0 with different content — page 2 must survive
    fig_page0_v2 = [
        {
            "figure_type": "diagram",
            "description": "Updated architecture on page 0 v2",
            "title": "Fig 1 updated",
            "entities_mentioned": [],
        }
    ]
    mock_vision.return_value = fig_page0_v2
    mock_embed.return_value = [[0.3] * DEFAULT_EMBED_DIM]
    extract_figures(conn, paper_id=paper_id, pages=[0])

    rows = conn.execute(
        "SELECT chunk_index, content FROM chunks WHERE source_type = 'figure' ORDER BY chunk_index"
    ).fetchall()
    assert len(rows) == 2, (
        f"Expected 2 figure chunks, got {len(rows)}: page 2 was destroyed"
    )
    assert rows[0]["chunk_index"] == _FIGURE_BASE + 0 * _FIGS_PER_PAGE + 0
    assert "v2" in rows[0]["content"]
    assert rows[1]["chunk_index"] == _FIGURE_BASE + 2 * _FIGS_PER_PAGE + 0
    assert "page 2" in rows[1]["content"].lower()


@patch("knowledge_base.vision._embed_with_config")
@patch("knowledge_base.vision._vision_call")
def test_extract_figures_fig_idx_overflow_capped(
    mock_vision, mock_embed, tmp_path, caplog
):
    """fig_idx >= _FIGS_PER_PAGE is capped at _FIGS_PER_PAGE-1 with a warning (#85)."""
    from knowledge_base.vision import extract_figures

    # Generate more figures than the slot size allows
    overflow_count = _FIGS_PER_PAGE + 5
    figures = [
        {
            "figure_type": "diagram",
            "description": f"Figure {i}",
            "title": f"Fig {i}",
            "entities_mentioned": [],
        }
        for i in range(overflow_count)
    ]

    conn, paper_id, _ = _setup_paper_with_pdf(tmp_path, ["Dense page"])

    mock_vision.return_value = figures
    mock_embed.return_value = [[0.1] * DEFAULT_EMBED_DIM] * overflow_count

    import logging

    with caplog.at_level(logging.WARNING, logger="knowledge_base.vision"):
        extract_figures(conn, paper_id=paper_id, pages=[0])

    # Warning should fire for each overflow figure
    overflow_warnings = [
        r for r in caplog.records if "capping chunk_index" in r.message
    ]
    assert len(overflow_warnings) == 5

    # All chunk_index values must stay within page 0's slot
    rows = conn.execute(
        "SELECT chunk_index FROM chunks WHERE source_type = 'figure' ORDER BY chunk_index"
    ).fetchall()
    for row in rows:
        assert (
            _FIGURE_BASE + 0 * _FIGS_PER_PAGE
            <= row["chunk_index"]
            < _FIGURE_BASE + 1 * _FIGS_PER_PAGE
        ), f"chunk_index {row['chunk_index']} overflows page 0 slot"


@patch("knowledge_base.vision._embed_with_config")
@patch("knowledge_base.vision._vision_call")
def test_extract_figures_full_extraction_deletes_all(mock_vision, mock_embed, tmp_path):
    """Full extraction (pages=None) should delete all figure chunks (original behavior)."""
    from knowledge_base.vision import extract_figures

    fig = [
        {
            "figure_type": "diagram",
            "description": "Some figure",
            "title": "Fig",
            "entities_mentioned": [],
        }
    ]

    conn, paper_id, _ = _setup_paper_with_pdf(tmp_path, ["Page 0", "Page 1"])

    # Extract page 0 only
    mock_vision.return_value = fig
    mock_embed.return_value = [[0.1] * DEFAULT_EMBED_DIM]
    extract_figures(conn, paper_id=paper_id, pages=[0])
    assert (
        conn.execute(
            "SELECT COUNT(*) as c FROM chunks WHERE source_type = 'figure'"
        ).fetchone()["c"]
        == 1
    )

    # Full extraction (pages=None) — should wipe page 0's figure even if heuristic
    # only processes page 0 again (the DELETE is unscoped)
    mock_vision.return_value = []  # no figures found
    extract_figures(conn, paper_id=paper_id, pages=None)
    assert (
        conn.execute(
            "SELECT COUNT(*) as c FROM chunks WHERE source_type = 'figure'"
        ).fetchone()["c"]
        == 0
    )


def test_extract_figures_empty_pages_is_noop(tmp_path):
    """pages=[] should return immediately without deleting anything (#79)."""
    from knowledge_base.vision import extract_figures

    conn, paper_id, _ = _setup_paper_with_pdf(tmp_path, ["Page 0"])

    # Pre-seed a figure chunk to verify it survives
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('fig_hash', 'existing figure', 'figure', "
        "(SELECT source_uri FROM chunks WHERE source_type='pdf' LIMIT 1), 1000000)",
    )
    conn.commit()

    result = extract_figures(conn, paper_id=paper_id, pages=[])

    assert result["pages_processed"] == 0
    assert result["figures_found"] == 0
    # The existing figure chunk must NOT have been deleted
    assert (
        conn.execute(
            "SELECT COUNT(*) as c FROM chunks WHERE source_type = 'figure'"
        ).fetchone()["c"]
        == 1
    )


@patch("knowledge_base.vision._embed_with_config")
@patch("knowledge_base.vision._vision_call")
def test_extract_figures_per_page_error(mock_vision, mock_embed, tmp_path):
    """One page fails, others succeed."""
    from knowledge_base.vision import extract_figures

    def side_effect(b64, prompt, *, base_url, model):
        # We can't easily tell which page from b64, so fail on second call
        if mock_vision.call_count <= 1:
            return [
                {
                    "figure_type": "diagram",
                    "description": "Good figure",
                    "title": None,
                    "entities_mentioned": [],
                }
            ]
        raise RuntimeError("Vision API timeout")

    mock_vision.side_effect = side_effect
    mock_embed.return_value = [[0.4] * DEFAULT_EMBED_DIM]

    conn, paper_id, _ = _setup_paper_with_pdf(
        tmp_path, ["Page 0 with Figure 1: test", "Page 1 with Figure 2: test"]
    )

    result = extract_figures(conn, paper_id=paper_id, pages=[0, 1])

    assert result["pages_processed"] == 1
    assert result["pages_failed"] == 1
    assert len(result["errors"]) == 1
    assert result["figures_found"] == 1


# ---------------------------------------------------------------------------
# Step 8: Page boundary tests for MCP tool layer
# ---------------------------------------------------------------------------


def test_extract_figures_page_out_of_range(tmp_path):
    """Passing a 0-indexed page >= total_pages returns error dict."""
    from knowledge_base.vision import extract_figures

    conn, paper_id, _ = _setup_paper_with_pdf(tmp_path, ["Page 0 text", "Page 1 text"])

    # Page 2 is out of range for a 2-page document (0-indexed)
    result = extract_figures(conn, paper_id=paper_id, pages=[2])
    assert "error" in result
    assert "out of range" in result["error"]

    # Negative page is also out of range
    result_neg = extract_figures(conn, paper_id=paper_id, pages=[-1])
    assert "error" in result_neg
    assert "out of range" in result_neg["error"]


def test_extract_figures_zero_indexed_boundary(tmp_path):
    """Page 0 is valid, page total_pages is not — confirms 0-indexed internal API.

    This validates that the MCP tool's 1-to-0 conversion is correct:
    MCP pages=[1] -> internal pages=[0] (valid for any document).
    MCP pages=[N+1] where N=total_pages -> internal pages=[N] (out of range).
    """
    from knowledge_base.vision import extract_figures

    # 3-page document: valid 0-indexed pages are 0, 1, 2
    conn, paper_id, _ = _setup_paper_with_pdf(tmp_path, ["Page 0", "Page 1", "Page 2"])

    # Page 2 (0-indexed, last page) should NOT error
    # It will fail at vision call (no mock), but should not return page-range error
    with patch("knowledge_base.vision._vision_call", return_value=[]):
        with patch("knowledge_base.vision._embed_with_config", return_value=[]):
            result = extract_figures(conn, paper_id=paper_id, pages=[2])
    assert "error" not in result

    # Page 3 (0-indexed) is out of range for a 3-page doc
    result_bad = extract_figures(conn, paper_id=paper_id, pages=[3])
    assert "error" in result_bad
    assert "out of range" in result_bad["error"]


# ---------------------------------------------------------------------------
# Step 7 addendum: Transaction rollback on failure
# ---------------------------------------------------------------------------


@patch("knowledge_base.vision._embed_with_config")
@patch("knowledge_base.vision._vision_call")
def test_extract_figures_transaction_rollback(mock_vision, mock_embed, tmp_path):
    """If _serialize_f32 fails mid-insert, the transaction is rolled back."""
    from knowledge_base.vision import extract_figures

    mock_vision.return_value = [
        {
            "figure_type": "diagram",
            "description": "First figure description",
            "title": "Fig 1",
            "entities_mentioned": [],
        },
        {
            "figure_type": "chart",
            "description": "Second figure description",
            "title": "Fig 2",
            "entities_mentioned": [],
        },
    ]
    mock_embed.return_value = [[0.1] * DEFAULT_EMBED_DIM, [0.2] * DEFAULT_EMBED_DIM]

    conn, paper_id, _ = _setup_paper_with_pdf(tmp_path)

    call_count = 0
    original_serialize = __import__(
        "knowledge_base.vision", fromlist=["_serialize_f32"]
    )._serialize_f32

    def failing_serialize(vec):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise RuntimeError("Simulated serialization failure")
        return original_serialize(vec)

    with patch("knowledge_base.vision._serialize_f32", side_effect=failing_serialize):
        with pytest.raises(RuntimeError, match="Simulated serialization failure"):
            extract_figures(conn, paper_id=paper_id, pages=[0])

    # Rollback should have cleaned up: no figure chunks at all
    figure_count = conn.execute(
        "SELECT COUNT(*) as c FROM chunks WHERE source_type = 'figure'"
    ).fetchone()["c"]
    assert figure_count == 0, "Transaction should have been rolled back"

    vec_count = conn.execute("SELECT COUNT(*) as c FROM chunks_vec").fetchone()["c"]
    assert vec_count == 0, "Vector inserts should have been rolled back"


# ---------------------------------------------------------------------------
# Step 8 addendum: MCP tool page conversion tests
# ---------------------------------------------------------------------------


@patch("knowledge_base.server.submit_job")
@patch("knowledge_base.server.estimate_figures_time")
@patch("knowledge_base.server._get_conn")
def test_mcp_tool_page_conversion(mock_get_conn, mock_eft, mock_submit):
    """extract_figures_tool converts 1-based pages to 0-based and submits a job."""
    from knowledge_base.server import extract_figures_tool

    mock_get_conn.return_value = MagicMock()
    mock_eft.return_value = {
        "candidate_pages": 3,
        "estimated_seconds": 200,
        "has_omniparser": False,
    }
    mock_submit.return_value = 42

    result = json.loads(
        extract_figures_tool(paper_id=1, pages=[1, 5, 10], confirmed=True)
    )
    assert result["deferred"] is True
    assert result["job_id"] == 42

    # Verify estimate_figures_time was called with 0-based pages
    call_args = mock_eft.call_args
    assert call_args[1].get("pages") == [0, 4, 9] or call_args[0][2] == [0, 4, 9]

    # Verify submit_job was called with 0-based pages in params
    submit_args = mock_submit.call_args
    assert submit_args[0][3] == {"pages": [0, 4, 9]}


@patch("knowledge_base.server._get_conn")
def test_mcp_tool_rejects_zero_and_negative_pages(mock_get_conn):
    """extract_figures_tool rejects pages <= 0."""
    from knowledge_base.server import extract_figures_tool

    mock_get_conn.return_value = MagicMock()

    result = json.loads(extract_figures_tool(paper_id=1, pages=[0, 3]))
    assert "error" in result
    assert "Pages must be >= 1" in result["error"]

    result_neg = json.loads(extract_figures_tool(paper_id=1, pages=[-1]))
    assert "error" in result_neg
    assert "Pages must be >= 1" in result_neg["error"]


# ---------------------------------------------------------------------------
# OmniParser config tests
# ---------------------------------------------------------------------------


def test_get_omniparser_config_default(tmp_path):
    """Returns None when no omniparser_path is configured."""
    from knowledge_base.vision import _get_omniparser_config

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    assert _get_omniparser_config(conn) is None


def test_configure_omniparser_valid(tmp_path):
    """Set a valid path, read it back."""
    from knowledge_base.vision import _get_omniparser_config, configure_omniparser

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Create fake omniparser directory structure
    omni_dir = tmp_path / "omniparser"
    omni_dir.mkdir()
    (omni_dir / "parse.py").write_text("# fake")
    venv_bin = omni_dir / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("# fake")

    result = configure_omniparser(conn, str(omni_dir))
    assert result["omniparser_path"] == str(omni_dir)
    assert "error" not in result

    # Read back
    assert _get_omniparser_config(conn) == str(omni_dir)


def test_configure_omniparser_invalid_path(tmp_path):
    """Rejects nonexistent path."""
    from knowledge_base.vision import configure_omniparser

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    result = configure_omniparser(conn, "/nonexistent/path")
    assert "error" in result


def test_configure_omniparser_disable(tmp_path):
    """Empty string clears the config."""
    from knowledge_base.vision import _get_omniparser_config, configure_omniparser

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Set first
    omni_dir = tmp_path / "omniparser"
    omni_dir.mkdir()
    (omni_dir / "parse.py").write_text("# fake")
    venv_bin = omni_dir / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("# fake")
    configure_omniparser(conn, str(omni_dir))
    assert _get_omniparser_config(conn) is not None

    # Disable
    result = configure_omniparser(conn, "")
    assert result["omniparser_path"] is None
    assert _get_omniparser_config(conn) is None


# ---------------------------------------------------------------------------
# OmniParser subprocess tests
# ---------------------------------------------------------------------------

import subprocess


def test_run_omniparser_success(tmp_path):
    """Mock subprocess returns valid JSON; verify parsed output."""
    from knowledge_base.vision import _run_omniparser

    omni_elements = {
        "elements": [
            {
                "id": 0,
                "type": "text",
                "bbox": [0, 0, 1, 1],
                "content": "Hello",
                "interactivity": False,
                "source": "ocr",
            },
        ],
        "label_coordinates": {},
        "image_size": {"width": 100, "height": 100},
    }

    png_path = tmp_path / "test.png"
    png_path.write_bytes(b"\x89PNG fake")

    def fake_run(cmd, **kwargs):
        # Write JSON to the -j output path
        json_path = cmd[cmd.index("-j") + 1]
        Path(json_path).write_text(json.dumps(omni_elements))
        return subprocess.CompletedProcess(cmd, 0)

    with patch("knowledge_base.vision.subprocess.run", side_effect=fake_run):
        result = _run_omniparser(png_path, str(tmp_path))

    assert result is not None
    assert len(result["elements"]) == 1
    assert result["elements"][0]["content"] == "Hello"


def test_run_omniparser_timeout():
    """TimeoutExpired returns None."""
    from knowledge_base.vision import _run_omniparser

    with patch(
        "knowledge_base.vision.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="test", timeout=120),
    ):
        result = _run_omniparser(Path("/fake.png"), "/fake/omniparser")

    assert result is None


def test_run_omniparser_subprocess_error():
    """CalledProcessError returns None."""
    from knowledge_base.vision import _run_omniparser

    with patch(
        "knowledge_base.vision.subprocess.run",
        side_effect=subprocess.CalledProcessError(1, cmd="test"),
    ):
        result = _run_omniparser(Path("/fake.png"), "/fake/omniparser")

    assert result is None


def test_run_omniparser_malformed_json(tmp_path):
    """Valid subprocess but invalid JSON output returns None."""
    from knowledge_base.vision import _run_omniparser

    def fake_run(cmd, **kwargs):
        json_path = cmd[cmd.index("-j") + 1]
        Path(json_path).write_text("not valid json {{{")
        return subprocess.CompletedProcess(cmd, 0)

    with patch("knowledge_base.vision.subprocess.run", side_effect=fake_run):
        result = _run_omniparser(Path("/fake.png"), str(tmp_path))

    assert result is None


# ---------------------------------------------------------------------------
# OmniParser merge tests
# ---------------------------------------------------------------------------


def test_merge_omniparser_elements_text_and_icons():
    """Both text and icon elements are appended to description."""
    from knowledge_base.vision import _merge_omniparser_elements

    figure = {"figure_type": "chart", "description": "A bar chart"}
    elements = [
        {"type": "text", "content": "X-axis", "bbox": [0, 0, 1, 1]},
        {"type": "icon", "content": "legend icon", "bbox": [0, 0, 1, 1]},
        {"type": "text", "content": "Y-axis", "bbox": [0, 0, 1, 1]},
    ]

    result = _merge_omniparser_elements(figure, elements)
    assert result is not figure  # new dict
    assert result["description"].startswith("A bar chart")
    assert 'Detected text: "X-axis", "Y-axis"' in result["description"]
    assert 'Detected elements: "legend icon"' in result["description"]


def test_merge_omniparser_elements_text_only():
    """Only text elements present."""
    from knowledge_base.vision import _merge_omniparser_elements

    figure = {"figure_type": "table", "description": "Results table"}
    elements = [
        {"type": "text", "content": "Accuracy", "bbox": [0, 0, 1, 1]},
        {"type": "text", "content": "92.5%", "bbox": [0, 0, 1, 1]},
    ]

    result = _merge_omniparser_elements(figure, elements)
    assert "Detected text:" in result["description"]
    assert "Detected elements:" not in result["description"]


def test_merge_omniparser_elements_empty():
    """No elements — description unchanged, same dict returned."""
    from knowledge_base.vision import _merge_omniparser_elements

    figure = {"figure_type": "diagram", "description": "Original"}
    result = _merge_omniparser_elements(figure, [])
    assert result is figure


def test_merge_omniparser_elements_empty_content():
    """Elements with null/empty/short content are skipped."""
    from knowledge_base.vision import _merge_omniparser_elements

    figure = {"figure_type": "chart", "description": "Original"}
    elements = [
        {"type": "text", "content": None, "bbox": [0, 0, 1, 1]},
        {"type": "text", "content": "", "bbox": [0, 0, 1, 1]},
        {"type": "text", "content": "x", "bbox": [0, 0, 1, 1]},  # len < 2
        {"type": "icon", "content": "  ", "bbox": [0, 0, 1, 1]},  # whitespace only
    ]

    result = _merge_omniparser_elements(figure, elements)
    assert result is figure  # nothing to merge


def test_merge_omniparser_elements_dedup():
    """Duplicate content strings are deduplicated (case-insensitive)."""
    from knowledge_base.vision import _merge_omniparser_elements

    figure = {"figure_type": "chart", "description": "Chart"}
    elements = [
        {"type": "text", "content": "Label", "bbox": [0, 0, 1, 1]},
        {"type": "text", "content": "label", "bbox": [0, 0, 1, 1]},
        {"type": "text", "content": "LABEL", "bbox": [0, 0, 1, 1]},
        {"type": "text", "content": "Other", "bbox": [0, 0, 1, 1]},
    ]

    result = _merge_omniparser_elements(figure, elements)
    # Should have "Label" and "Other", not three copies
    desc = result["description"]
    assert desc.count("Label") == 1 or desc.count("label") == 1
    assert "Other" in desc


def test_merge_omniparser_elements_size_cap():
    """Total appended text is capped at 500 chars."""
    from knowledge_base.vision import _merge_omniparser_elements

    figure = {"figure_type": "chart", "description": "Chart"}
    # 100 elements with 10-char content = 1000+ chars without cap
    elements = [
        {"type": "text", "content": f"element_{i:02d}", "bbox": [0, 0, 1, 1]}
        for i in range(100)
    ]

    result = _merge_omniparser_elements(figure, elements)
    # The appended portion (after "Chart\n\n") should be <= 500 chars
    appended = result["description"][len("Chart") :]
    assert len(appended) <= 520  # small margin for prefix "Detected text: "


# ---------------------------------------------------------------------------
# OmniParser integration tests (extract_figures pipeline)
# ---------------------------------------------------------------------------


_OMNI_ELEMENTS = {
    "elements": [
        {
            "id": 0,
            "type": "text",
            "bbox": [0.1, 0.1, 0.5, 0.2],
            "content": "X-axis label",
            "interactivity": False,
            "source": "ocr",
        },
        {
            "id": 1,
            "type": "icon",
            "bbox": [0.5, 0.15, 0.7, 0.25],
            "content": "data point",
            "interactivity": False,
            "source": "yolo",
        },
    ],
    "label_coordinates": {},
    "image_size": {"width": 800, "height": 600},
}


@patch("knowledge_base.vision._embed_with_config")
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._run_omniparser")
def test_extract_figures_with_omniparser_single_figure(
    mock_omni, mock_vision, mock_embed, tmp_path
):
    """Single figure on page: omniparser elements merged into description."""
    from knowledge_base.vision import extract_figures

    mock_vision.return_value = [
        {
            "figure_type": "chart",
            "description": "A bar chart showing results",
            "title": "Fig 1",
            "entities_mentioned": [],
        }
    ]
    mock_embed.return_value = [[0.1] * DEFAULT_EMBED_DIM]
    mock_omni.return_value = _OMNI_ELEMENTS

    conn, paper_id, pdf_path = _setup_paper_with_pdf(tmp_path)

    # Configure omniparser
    omni_dir = tmp_path / "omniparser"
    omni_dir.mkdir()
    (omni_dir / "parse.py").write_text("# fake")
    venv_bin = omni_dir / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("# fake")
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('omniparser_path', ?)",
        (str(omni_dir),),
    )
    conn.commit()

    result = extract_figures(conn, paper_id=paper_id, pages=[0])

    assert result["pages_processed"] == 1
    assert result["chunks_created"] == 1
    assert result["omniparser_enriched"] == 1

    # Verify description was enriched
    row = conn.execute(
        "SELECT content FROM chunks WHERE source_type = 'figure'"
    ).fetchone()
    assert "A bar chart showing results" in row["content"]
    assert "X-axis label" in row["content"]
    assert "data point" in row["content"]


@patch("knowledge_base.vision._embed_with_config")
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._run_omniparser")
def test_extract_figures_with_omniparser_multi_figure(
    mock_omni, mock_vision, mock_embed, tmp_path
):
    """Multiple figures on page: omniparser stored in metadata only, not in description."""
    from knowledge_base.vision import extract_figures

    mock_vision.return_value = [
        {
            "figure_type": "chart",
            "description": "First chart",
            "title": "Fig 1a",
            "entities_mentioned": [],
        },
        {
            "figure_type": "diagram",
            "description": "Second diagram",
            "title": "Fig 1b",
            "entities_mentioned": [],
        },
    ]
    mock_embed.return_value = [[0.1] * DEFAULT_EMBED_DIM, [0.2] * DEFAULT_EMBED_DIM]
    mock_omni.return_value = _OMNI_ELEMENTS

    conn, paper_id, pdf_path = _setup_paper_with_pdf(tmp_path)

    # Configure omniparser
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('omniparser_path', '/fake/omniparser')",
    )
    conn.commit()

    result = extract_figures(conn, paper_id=paper_id, pages=[0])

    assert result["pages_processed"] == 1
    assert result["chunks_created"] == 2
    assert result["omniparser_enriched"] == 0  # no description enrichment

    # Descriptions should NOT contain omniparser text
    rows = conn.execute(
        "SELECT content, metadata FROM chunks WHERE source_type = 'figure' ORDER BY chunk_index"
    ).fetchall()
    for row in rows:
        assert "X-axis label" not in row["content"]
        # But metadata should have omniparser_elements
        meta = json.loads(row["metadata"])
        assert "omniparser_elements" in meta
        assert len(meta["omniparser_elements"]) == 2


@patch("knowledge_base.vision._embed_with_config")
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._run_omniparser")
def test_extract_figures_omniparser_failure_graceful(
    mock_omni, mock_vision, mock_embed, tmp_path
):
    """Omniparser fails: figures still created with LLM-only description."""
    from knowledge_base.vision import extract_figures

    mock_vision.return_value = [
        {
            "figure_type": "chart",
            "description": "Original caption only",
            "title": "Fig 1",
            "entities_mentioned": [],
        }
    ]
    mock_embed.return_value = [[0.1] * DEFAULT_EMBED_DIM]
    mock_omni.return_value = None  # omniparser failed

    conn, paper_id, _ = _setup_paper_with_pdf(tmp_path)
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('omniparser_path', '/fake/omniparser')",
    )
    conn.commit()

    result = extract_figures(conn, paper_id=paper_id, pages=[0])

    assert result["pages_processed"] == 1
    assert result["chunks_created"] == 1
    assert result["omniparser_enriched"] == 0

    row = conn.execute(
        "SELECT content FROM chunks WHERE source_type = 'figure'"
    ).fetchone()
    assert row["content"] == "Original caption only"


@patch("knowledge_base.vision._embed_with_config")
@patch("knowledge_base.vision._vision_call")
def test_extract_figures_without_omniparser(mock_vision, mock_embed, tmp_path):
    """Omniparser not configured: no subprocess calls, identical behavior."""
    from knowledge_base.vision import extract_figures

    mock_vision.return_value = [
        {
            "figure_type": "diagram",
            "description": "Architecture diagram",
            "title": "Fig 1",
            "entities_mentioned": [],
        }
    ]
    mock_embed.return_value = [[0.1] * DEFAULT_EMBED_DIM]

    conn, paper_id, _ = _setup_paper_with_pdf(tmp_path)

    # No omniparser configured
    result = extract_figures(conn, paper_id=paper_id, pages=[0])

    assert result["pages_processed"] == 1
    assert result["chunks_created"] == 1
    assert result.get("omniparser_enriched", 0) == 0

    row = conn.execute(
        "SELECT content FROM chunks WHERE source_type = 'figure'"
    ).fetchone()
    assert row["content"] == "Architecture diagram"


# ---------------------------------------------------------------------------
# Timing instrumentation tests (#58)
# ---------------------------------------------------------------------------


@patch("knowledge_base.vision._embed_with_config")
@patch("knowledge_base.vision._vision_call")
def test_extract_figures_returns_timing(mock_vision, mock_embed, tmp_path):
    """extract_figures result includes timing dict with expected keys."""
    from knowledge_base.vision import extract_figures

    mock_vision.return_value = [
        {
            "figure_type": "diagram",
            "description": "Timing test figure",
            "title": None,
            "entities_mentioned": [],
        }
    ]
    mock_embed.return_value = [[0.1] * DEFAULT_EMBED_DIM]

    conn, paper_id, _ = _setup_paper_with_pdf(tmp_path)
    result = extract_figures(conn, paper_id=paper_id, pages=[0])

    assert "timing" in result
    timing = result["timing"]
    assert "vision_secs" in timing
    assert "omniparser_secs" in timing
    assert "total_secs" in timing
    assert isinstance(timing["vision_secs"], float)
    assert isinstance(timing["omniparser_secs"], float)
    assert isinstance(timing["total_secs"], float)
    assert timing["omniparser_secs"] == 0.0  # no omniparser configured
    assert timing["total_secs"] >= timing["vision_secs"]


@patch("knowledge_base.vision._embed_with_config")
@patch("knowledge_base.vision._vision_call")
def test_extract_figures_timing_with_omniparser(mock_vision, mock_embed, tmp_path):
    """Timing dict includes non-zero omniparser_secs when omniparser is active."""
    from knowledge_base.vision import extract_figures

    mock_vision.return_value = [
        {
            "figure_type": "chart",
            "description": "OmniParser timing test",
            "title": None,
            "entities_mentioned": [],
        }
    ]
    mock_embed.return_value = [[0.1] * DEFAULT_EMBED_DIM]

    conn, paper_id, _ = _setup_paper_with_pdf(tmp_path)

    # Configure omniparser with valid-looking paths
    omni_dir = tmp_path / "omniparser"
    omni_dir.mkdir()
    (omni_dir / "parse.py").write_text("# stub")
    venv_bin = omni_dir / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/bin/sh\n")

    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('omniparser_path', ?)",
        (str(omni_dir),),
    )
    conn.commit()

    # Mock _run_omniparser to return elements (avoid subprocess)
    with patch("knowledge_base.vision._run_omniparser") as mock_omni:
        mock_omni.return_value = {
            "elements": [{"type": "text", "content": "axis label"}]
        }
        result = extract_figures(conn, paper_id=paper_id, pages=[0])

    assert result["timing"]["omniparser_secs"] >= 0.0
    assert result["timing"]["total_secs"] >= result["timing"]["vision_secs"]


def test_vision_call_logs_timing(caplog):
    """_vision_call logs elapsed time at INFO level."""
    from knowledge_base.vision import _vision_call

    mock_response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        [
                            {
                                "figure_type": "diagram",
                                "description": "test",
                                "title": None,
                                "entities_mentioned": [],
                            }
                        ]
                    )
                }
            }
        ]
    }

    with patch("knowledge_base.vision.httpx.post") as mock_post:
        mock_resp = mock_post.return_value
        mock_resp.raise_for_status = lambda: None
        mock_resp.json.return_value = mock_response

        import logging

        with caplog.at_level(logging.INFO, logger="knowledge_base.vision"):
            _vision_call(
                "fakebase64", "prompt", base_url="http://localhost", model="test"
            )

    assert any("Vision call completed in" in msg for msg in caplog.messages)


def test_vision_call_warns_on_slow_response(caplog):
    """_vision_call emits WARNING when elapsed exceeds drift threshold."""
    from knowledge_base.vision import (
        _ETA_SECS_PER_PAGE_BASE,
        _TIMING_DRIFT_FACTOR,
        _vision_call,
    )

    mock_response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        [
                            {
                                "figure_type": "diagram",
                                "description": "test",
                                "title": None,
                                "entities_mentioned": [],
                            }
                        ]
                    )
                }
            }
        ]
    }

    # Simulate a slow response by patching time.monotonic
    slow_duration = _ETA_SECS_PER_PAGE_BASE * _TIMING_DRIFT_FACTOR + 1
    call_count = 0

    def fake_monotonic():
        nonlocal call_count
        call_count += 1
        # First call returns 0 (start), second call returns slow_duration (end)
        return 0.0 if call_count % 2 == 1 else slow_duration

    with (
        patch("knowledge_base.vision.httpx.post") as mock_post,
        patch("knowledge_base.vision.time.monotonic", side_effect=fake_monotonic),
    ):
        mock_resp = mock_post.return_value
        mock_resp.raise_for_status = lambda: None
        mock_resp.json.return_value = mock_response

        import logging

        with caplog.at_level(logging.WARNING, logger="knowledge_base.vision"):
            _vision_call(
                "fakebase64", "prompt", base_url="http://localhost", model="test"
            )

    assert any("consider raising" in msg for msg in caplog.messages)


def test_run_omniparser_logs_timing(caplog, tmp_path):
    """_run_omniparser logs elapsed time on success."""
    from knowledge_base.vision import _run_omniparser

    json_data = json.dumps({"elements": [{"type": "text", "content": "hello"}]})

    def fake_subprocess_run(cmd, **kwargs):
        # Write the expected JSON to the output file (last arg after -j)
        json_path = cmd[-1]
        Path(json_path).write_text(json_data)

    with patch("knowledge_base.vision.subprocess.run", side_effect=fake_subprocess_run):
        import logging

        with caplog.at_level(logging.INFO, logger="knowledge_base.vision"):
            result = _run_omniparser(Path("/fake.png"), "/fake/omniparser")

    assert result is not None
    assert any("OmniParser completed for" in msg for msg in caplog.messages)


def test_run_omniparser_uses_timeout_constant():
    """_run_omniparser default timeout matches _OMNIPARSER_SUBPROCESS_TIMEOUT."""
    import inspect

    from knowledge_base.vision import _OMNIPARSER_SUBPROCESS_TIMEOUT, _run_omniparser

    sig = inspect.signature(_run_omniparser)
    assert sig.parameters["timeout"].default == _OMNIPARSER_SUBPROCESS_TIMEOUT


def test_vision_call_uses_timeout_constant():
    """_vision_call uses _VISION_CALL_TIMEOUT for the httpx request."""
    from knowledge_base.vision import _VISION_CALL_TIMEOUT, _vision_call

    mock_response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        [
                            {
                                "figure_type": "diagram",
                                "description": "test",
                                "title": None,
                                "entities_mentioned": [],
                            }
                        ]
                    )
                }
            }
        ]
    }

    with patch("knowledge_base.vision.httpx.post") as mock_post:
        mock_resp = mock_post.return_value
        mock_resp.raise_for_status = lambda: None
        mock_resp.json.return_value = mock_response

        _vision_call("fakebase64", "prompt", base_url="http://localhost", model="test")

    _, kwargs = mock_post.call_args
    assert kwargs["timeout"] == _VISION_CALL_TIMEOUT


def test_constants_are_consistent():
    """Timeout constants must be >= ETA constants to avoid premature timeouts."""
    from knowledge_base.vision import (
        _ETA_SECS_PER_PAGE_BASE,
        _ETA_SECS_PER_PAGE_OMNIPARSER,
        _OMNIPARSER_SUBPROCESS_TIMEOUT,
        _VISION_CALL_TIMEOUT,
    )

    assert _VISION_CALL_TIMEOUT >= _ETA_SECS_PER_PAGE_BASE, (
        f"Vision timeout ({_VISION_CALL_TIMEOUT}s) < ETA ({_ETA_SECS_PER_PAGE_BASE}s)"
    )
    assert _OMNIPARSER_SUBPROCESS_TIMEOUT >= _ETA_SECS_PER_PAGE_OMNIPARSER, (
        f"OmniParser timeout ({_OMNIPARSER_SUBPROCESS_TIMEOUT}s) < ETA ({_ETA_SECS_PER_PAGE_OMNIPARSER}s)"
    )


# ---------------------------------------------------------------------------
# Phase 3: Extracted-image pipeline tests (#110)
# ---------------------------------------------------------------------------


def test_collect_extracted_images_returns_images_grouped_by_page(vision_conn, tmp_path):
    """_collect_extracted_images reads chunk metadata and resolves image paths."""
    conn = vision_conn
    source_uri = str(tmp_path / "paper.pdf")

    image_dir = tmp_path / "figures" / "paper_abc123" / "extracted"
    image_dir.mkdir(parents=True)
    (image_dir / "image_0.png").write_bytes(b"PNG_FAKE_1")
    (image_dir / "image_1.png").write_bytes(b"PNG_FAKE_2")

    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, metadata) "
        "VALUES (?, ?, 'pdf', ?, 0, ?)",
        (
            "h1",
            "text about fig1",
            source_uri,
            json.dumps({"pages": [1], "images": ["image_0.png"]}),
        ),
    )
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, metadata) "
        "VALUES (?, ?, 'pdf', ?, 1, ?)",
        (
            "h2",
            "text about fig2",
            source_uri,
            json.dumps({"pages": [3], "images": ["image_1.png"]}),
        ),
    )
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, metadata) "
        "VALUES (?, ?, 'pdf', ?, 2, ?)",
        ("h3", "just text", source_uri, json.dumps({"pages": [2]})),
    )
    conn.commit()

    from knowledge_base.vision import _collect_extracted_images

    result = _collect_extracted_images(conn, source_uri, image_dir)

    assert len(result) == 2
    paths = {r[0].name for r in result}
    assert paths == {"image_0.png", "image_1.png"}
    page_map = {r[0].name: r[1] for r in result}
    assert page_map["image_0.png"] == 1
    assert page_map["image_1.png"] == 3


def test_collect_extracted_images_skips_missing_files(vision_conn, tmp_path):
    """Images referenced in metadata but missing on disk are skipped."""
    conn = vision_conn
    source_uri = str(tmp_path / "paper.pdf")
    image_dir = tmp_path / "figures" / "paper_abc" / "extracted"
    image_dir.mkdir(parents=True)
    (image_dir / "image_0.png").write_bytes(b"PNG_FAKE")

    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, metadata) "
        "VALUES (?, ?, 'pdf', ?, 0, ?)",
        (
            "h1",
            "fig1",
            source_uri,
            json.dumps({"pages": [1], "images": ["image_0.png", "missing.png"]}),
        ),
    )
    conn.commit()

    from knowledge_base.vision import _collect_extracted_images

    result = _collect_extracted_images(conn, source_uri, image_dir)
    assert len(result) == 1
    assert result[0][0].name == "image_0.png"


def test_collect_extracted_images_deduplicates(vision_conn, tmp_path):
    """Same image referenced in multiple chunks is returned once."""
    conn = vision_conn
    source_uri = str(tmp_path / "paper.pdf")
    image_dir = tmp_path / "figures" / "paper_abc" / "extracted"
    image_dir.mkdir(parents=True)
    (image_dir / "image_0.png").write_bytes(b"PNG_FAKE")

    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, metadata) "
        "VALUES (?, ?, 'pdf', ?, 0, ?)",
        (
            "h1",
            "first",
            source_uri,
            json.dumps({"pages": [1, 2], "images": ["image_0.png"]}),
        ),
    )
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, metadata) "
        "VALUES (?, ?, 'pdf', ?, 1, ?)",
        (
            "h2",
            "second",
            source_uri,
            json.dumps({"pages": [2, 3], "images": ["image_0.png"]}),
        ),
    )
    conn.commit()

    from knowledge_base.vision import _collect_extracted_images

    result = _collect_extracted_images(conn, source_uri, image_dir)
    assert len(result) == 1
    assert result[0][1] == 1


def test_detect_vector_pages_finds_drawing_heavy_pages(tmp_path):
    """Pages with many drawings but no extracted images are detected."""
    from knowledge_base.vision import _detect_vector_pages

    import fitz

    doc = fitz.open()
    page0 = doc.new_page(width=612, height=792)
    # Each finish()/commit() cycle creates one drawing path in the PDF,
    # so we need separate cycles to exceed the threshold.
    for i in range(15):
        shape = page0.new_shape()
        shape.draw_line(fitz.Point(10, 10 + i * 5), fitz.Point(100, 10 + i * 5))
        shape.finish()
        shape.commit()

    page1 = doc.new_page(width=612, height=792)
    page1.insert_text(fitz.Point(72, 72), "Just text, no figures.")

    pdf_path = tmp_path / "test.pdf"
    doc.save(str(pdf_path))
    doc.close()

    pages_with_images: set[int] = set()
    result = _detect_vector_pages(str(pdf_path), pages_with_images)

    assert 0 in result
    assert 1 not in result


def test_detect_vector_pages_excludes_pages_with_extracted_images(tmp_path):
    """Pages that already have extracted images are excluded even if they have drawings."""
    from knowledge_base.vision import _detect_vector_pages

    import fitz

    doc = fitz.open()
    page0 = doc.new_page(width=612, height=792)
    for i in range(15):
        shape = page0.new_shape()
        shape.draw_line(fitz.Point(10, 10 + i * 5), fitz.Point(100, 10 + i * 5))
        shape.finish()
        shape.commit()

    pdf_path = tmp_path / "test.pdf"
    doc.save(str(pdf_path))
    doc.close()

    pages_with_images: set[int] = {0}
    result = _detect_vector_pages(str(pdf_path), pages_with_images)
    assert 0 not in result


@patch("knowledge_base.vision.pdf_image_dir")
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._embed_with_config")
def test_extract_figures_uses_extracted_images(
    mock_embed, mock_vision, mock_image_dir, vision_conn, tmp_path
):
    """extract_figures reads pymupdf4llm-extracted images instead of rendering pages."""
    conn = vision_conn
    import fitz

    doc = fitz.open()
    doc.new_page(width=612, height=792)
    doc.new_page(width=612, height=792)
    pdf_path = tmp_path / "paper.pdf"
    doc.save(str(pdf_path))
    doc.close()

    conn.execute("INSERT INTO papers (id, title) VALUES (1, 'Test Paper')")
    conn.execute(
        "INSERT INTO paper_paths (paper_id, path, content_hash) VALUES (1, ?, 'abc')",
        (str(pdf_path),),
    )

    # Create extracted image directory under tmp_path
    image_dir = tmp_path / "extracted"
    image_dir.mkdir(parents=True)
    mock_image_dir.return_value = image_dir

    from PIL import Image
    import io

    img = Image.new("RGB", (100, 100), color="red")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()
    (image_dir / "image_0.png").write_bytes(png_bytes)

    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, metadata) "
        "VALUES (?, ?, 'pdf', ?, 0, ?)",
        (
            "ch1",
            "![fig](image_0.png)",
            str(pdf_path),
            json.dumps({"pages": [1], "images": ["image_0.png"]}),
        ),
    )
    conn.commit()

    mock_vision.return_value = [
        {
            "figure_type": "chart",
            "description": "A red chart.",
            "title": "Fig 1",
            "entities_mentioned": [],
        }
    ]
    mock_embed.return_value = [[0.1] * 1024]

    from knowledge_base.vision import extract_figures

    result = extract_figures(conn, paper_id=1, confirmed=True)

    assert result["figures_found"] == 1
    assert result["chunks_created"] == 1
    assert mock_vision.call_count == 1
    # Verify the figure-specific prompt was used
    call_args = mock_vision.call_args
    assert "figure image extracted from a research paper" in call_args[0][1]
    # The metadata should indicate source_image
    row = conn.execute(
        "SELECT metadata FROM chunks WHERE source_type = 'figure'"
    ).fetchone()
    meta = json.loads(row["metadata"])
    assert meta.get("source_image") == "image_0.png"


@patch("knowledge_base.vision.pdf_image_dir")
@patch("knowledge_base.vision._vision_call")
@patch("knowledge_base.vision._embed_with_config")
def test_extract_figures_falls_back_to_render_for_vector_pages(
    mock_embed, mock_vision, mock_image_dir, vision_conn, tmp_path
):
    """Pages with vector drawings but no extracted images use full-page render."""
    conn = vision_conn
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    for i in range(15):
        shape = page.new_shape()
        shape.draw_line(fitz.Point(10, 10 + i * 5), fitz.Point(100, 10 + i * 5))
        shape.finish()
        shape.commit()
    pdf_path = tmp_path / "paper.pdf"
    doc.save(str(pdf_path))
    doc.close()

    conn.execute("INSERT INTO papers (id, title) VALUES (1, 'Vector Paper')")
    conn.execute(
        "INSERT INTO paper_paths (paper_id, path, content_hash) VALUES (1, ?, 'abc')",
        (str(pdf_path),),
    )

    # Empty extracted dir — no extracted images
    image_dir = tmp_path / "extracted"
    image_dir.mkdir(parents=True)
    mock_image_dir.return_value = image_dir

    conn.commit()

    mock_vision.return_value = [
        {
            "figure_type": "diagram",
            "description": "A vector diagram.",
            "title": None,
            "entities_mentioned": [],
        }
    ]
    mock_embed.return_value = [[0.1] * 1024]

    from knowledge_base.vision import extract_figures

    result = extract_figures(conn, paper_id=1, confirmed=True)

    assert result["figures_found"] == 1
    assert result.get("vector_pages_rendered", 0) > 0


@patch("knowledge_base.vision.pdf_image_dir")
@patch("knowledge_base.vision._collect_extracted_images")
def test_estimate_figures_time_accounts_for_extracted_images(
    mock_collect, mock_image_dir, vision_conn, tmp_path
):
    """ETA should be lower when extracted images are available."""
    conn = vision_conn
    import fitz

    doc = fitz.open()
    for _ in range(10):
        doc.new_page()
    pdf_path = tmp_path / "paper.pdf"
    doc.save(str(pdf_path))
    doc.close()

    conn.execute("INSERT INTO papers (id, title) VALUES (1, 'Test')")
    conn.execute(
        "INSERT INTO paper_paths (paper_id, path, content_hash) VALUES (1, ?, 'abc')",
        (str(pdf_path),),
    )
    conn.commit()

    mock_image_dir.return_value = tmp_path / "extracted"
    # 5 extracted images, 0 vector pages → much cheaper than 10 heuristic pages
    mock_collect.return_value = [(tmp_path / f"img_{i}.png", i + 1) for i in range(5)]

    from knowledge_base.vision import estimate_figures_time

    result = estimate_figures_time(conn, paper_id=1)

    assert result.get("extracted_images", 0) == 5
    # With 5 extracted images and 0 vector pages, should be cheaper than
    # the old heuristic which would have returned all 10 pages
    assert result["estimated_seconds"] < 10 * 4
