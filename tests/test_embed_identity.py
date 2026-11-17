"""ADR-0015 embedding identity (#573): the schema, the declaration, the gates and each space's query instruction."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from knowledge_base import migrate, server
from knowledge_base.db import get_connection, init_schema, resolve_db_path
from tests.declared_env import TEST_EMBED_DIM

QWEN = "Qwen/Qwen3-Embedding-0.6B"
#: ADR-0015 §6's Knowledge-layer instruction, written out here rather than imported.
LITERAL = "Instruct: Given a search query, retrieve relevant document passages.\nQuery: "

V2_COLUMNS = ("artifact_sha256", "pooling", "normalization", "max_input_tokens", "query_instruction")


def _columns(conn: sqlite3.Connection) -> dict[str, tuple]:
    return {row[1]: (row[2], row[3], row[4]) for row in conn.execute("PRAGMA table_info(embed_spaces)")}


def _v1_database(path) -> sqlite3.Connection:
    """A v1 database: today's schema without the v2 columns, stamped 1 (the v1 code wrote none of them)."""
    conn = get_connection(path)
    init_schema(conn)
    for column in V2_COLUMNS:
        conn.execute(f"ALTER TABLE embed_spaces DROP COLUMN {column}")
    migrate.set_schema_version(conn, 1)
    conn.commit()
    assert not set(V2_COLUMNS) & set(_columns(conn))
    assert migrate.get_schema_version(conn) == 1
    return conn


# --- schema v2 ------------------------------------------------------------------


def test_every_origin_converges_to_one_v2_shape(tmp_path):
    fresh = get_connection(tmp_path / "fresh.db")
    init_schema(fresh)

    stamped_v1 = _v1_database(tmp_path / "v1.db")
    migrate.migrate(stamped_v1, backup_dir=tmp_path / "backups")

    legacy = _v1_database(tmp_path / "legacy.db")
    legacy.execute("DELETE FROM config WHERE key = 'schema_version'")
    legacy.commit()
    init_schema(legacy)

    shapes = [_columns(conn) for conn in (fresh, stamped_v1, legacy)]
    assert shapes[0] == shapes[1] == shapes[2]
    assert set(V2_COLUMNS) <= set(shapes[0])
    for conn in (stamped_v1, legacy):
        assert migrate.get_schema_version(conn) == migrate.CURRENT_SCHEMA_VERSION
        row = conn.execute(f"SELECT {', '.join(V2_COLUMNS)} FROM embed_spaces").fetchone()  # noqa: S608  # literal names
        assert tuple(row) == (None,) * len(V2_COLUMNS)


def test_a_v1_database_refuses_to_start_until_migrated(monkeypatch, capsys, stub_document_embedder):
    from knowledge_base.indexer import main as cli
    from knowledge_base.ingest import ingest_text

    db = resolve_db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = _v1_database(db)
    ingest_text(conn, "A chunk stored before the upgrade.", source_uri="/docs/a.md", source_type="markdown")
    conn.close()

    with patch.object(server.mcp, "run") as run, pytest.raises(SystemExit) as exit_info:
        server.main([])
    assert exit_info.value.code == 78
    assert "knowledge-base-ingest migrate" in capsys.readouterr().err
    run.assert_not_called()

    cli(["--db", str(db), "--quiet", "migrate"])
    assert list((db.parent / "backups").iterdir())
    conn = get_connection(db)
    assert conn.execute("SELECT COUNT(*) FROM chunks_vec").fetchone()[0] == 1
    conn.close()

    with patch.object(server.mcp, "run") as run:
        server.main([])
    run.assert_called_once_with(transport="stdio")


# --- the transport stub embedder -------------------------------------------------


class Embedder:
    """The declared endpoint, played at the httpx transport: records each JSON body it receives."""

    def __init__(self) -> None:
        self.bodies: list[dict] = []
        self.dim = TEST_EMBED_DIM

    def inputs(self) -> list[str]:
        return [text for body in self.bodies for text in body["input"]]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        dim = body.get("dimensions") or self.dim
        vectors = [[1.0] + [0.0] * (dim - 1) for _ in body["input"]]
        if request.url.path.endswith("/api/embed"):
            return httpx.Response(200, json={"embeddings": vectors})
        return httpx.Response(200, json={"data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)]})


@pytest.fixture
def embedder(http_requests) -> Embedder:
    stub = Embedder()
    http_requests.handlers["embedder.example.test"] = stub
    return stub


def _store(tmp_path: Path, name: str = "kb.db") -> sqlite3.Connection:
    conn = get_connection(tmp_path / name)
    init_schema(conn)
    return conn


# --- each space's query instruction ----------------------------------------------


@pytest.mark.parametrize("branch", ["active", "named", "matryoshka"])
def test_a_qwen3_space_queries_carry_the_pinned_instruction(branch, tmp_path, monkeypatch, embedder):
    from knowledge_base.search import search

    monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_MODEL", QWEN)
    if branch == "matryoshka":
        monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_MATRYOSHKA_BASE_DIM", "2048")
    conn = _store(tmp_path)
    if branch == "matryoshka":
        conn.execute("UPDATE embed_spaces SET matryoshka_base_dim = 2048")
        conn.commit()
        embedder.dim = 2048
    search(conn, "attention", mode="vec", space_name="default" if branch == "named" else None)
    assert [body["input"] for body in embedder.bodies] == [[LITERAL + "attention"]]


def test_the_readme_quickstart_query_is_unchanged(tmp_path, embedder):
    from knowledge_base.search import search

    search(_store(tmp_path), "attention", mode="vec")
    assert embedder.bodies == [{"model": "bge-m3", "input": ["attention"]}]


@pytest.mark.parametrize(
    ("model", "recorded", "sent"),
    [
        ("bge-m3", "Represent this: ", "Represent this: attention"),
        (QWEN, "", "attention"),
    ],
)
def test_a_space_records_its_own_instruction(model, recorded, sent, tmp_path, monkeypatch, embedder):
    from knowledge_base.search import search

    monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_MODEL", model)
    conn = _store(tmp_path)
    conn.execute("UPDATE embed_spaces SET query_instruction = ?", (recorded,))
    conn.commit()
    search(conn, "attention", mode="vec")
    assert [body["input"] for body in embedder.bodies] == [[sent]]


def test_no_document_carries_the_instruction(tmp_path, monkeypatch, embedder):
    from knowledge_base.embed_swap import backfill_space, create_space, promote_space
    from knowledge_base.folder_summaries import update_folder_summary
    from knowledge_base.ingest import ingest_text

    monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_MODEL", QWEN)
    conn = _store(tmp_path)
    ingest_text(conn, "Attention is all you need.", source_uri="/docs/a.md", source_type="markdown")
    stored = [row["content"] for row in conn.execute("SELECT content FROM chunks")]
    assert embedder.inputs() == stored
    assert update_folder_summary(conn, "/docs")
    create_space(conn, "alt", QWEN, TEST_EMBED_DIM, "ollama")
    backfill_space(conn, "alt")
    promote_space(conn, "alt")
    assert len(embedder.bodies) >= 4
    assert not [text for text in embedder.inputs() if "Instruct:" in text]


def test_the_instruction_is_the_adrs():
    from knowledge_base.embeddings import QUERY_INSTRUCTION

    adr = Path(__file__).parents[1] / "docs/design/adr/0015-cross-layer-embedding-identity-policy.md"
    row = re.search(r"^\| Knowledge layer \| `(.+)` \|$", adr.read_text(), re.MULTILINE)
    assert row is not None
    assert row.group(1).replace("\\n", "\n") == QUERY_INSTRUCTION == LITERAL


# --- the bootstrap space records the declaration ----------------------------------

FULL = {
    "KNOWLEDGE_BASE_EMBED_ARTIFACT_SHA256": "a" * 64,
    "KNOWLEDGE_BASE_EMBED_POOLING": "last",
    "KNOWLEDGE_BASE_EMBED_NORMALIZATION": "l2",
    "KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS": "2048",
}


def _declare_full(monkeypatch, **extra: str) -> None:
    for name, value in {**FULL, **extra}.items():
        monkeypatch.setenv(name, value)


def _space_identity(conn: sqlite3.Connection, name: str = "default") -> dict[str, object]:
    from knowledge_base.settings import IDENTITY_FIELDS

    row = conn.execute("SELECT * FROM embed_spaces WHERE name = ?", (name,)).fetchone()
    return {field: row[field] for field in IDENTITY_FIELDS}


def test_a_blank_store_records_the_declaration(monkeypatch):
    from knowledge_base.db import open_declared_db
    from knowledge_base.settings import declared_identity, load_embed_settings

    _declare_full(monkeypatch, KNOWLEDGE_BASE_EMBED_MATRYOSHKA_BASE_DIM="2048")
    conn = open_declared_db()
    assert _space_identity(conn) == declared_identity(load_embed_settings())
    assert conn.execute("SELECT query_instruction FROM embed_spaces").fetchone()[0] is None
    conn.close()


def test_a_store_recorded_before_the_served_artifact_keeps_it_absent(tmp_path, monkeypatch):
    conn = _v1_database(tmp_path / "legacy.db")
    conn.execute("DELETE FROM embed_spaces")
    conn.execute("DELETE FROM config WHERE key = 'schema_version'")
    conn.commit()
    _declare_full(monkeypatch)
    init_schema(conn)
    recorded = _space_identity(conn)
    assert [recorded[field] for field in ("artifact_sha256", "pooling", "normalization", "max_input_tokens")] == [
        None
    ] * 4
