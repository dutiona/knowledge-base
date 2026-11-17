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
    return _downgrade_to_v1(conn)


def _downgrade_to_v1(conn: sqlite3.Connection) -> sqlite3.Connection:
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
    conn = get_connection(db)
    init_schema(conn)
    ingest_text(conn, "A chunk stored before the upgrade.", source_uri="/docs/a.md", source_type="markdown")
    _downgrade_to_v1(conn).close()

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
    create_space(conn, "alt", QWEN, TEST_EMBED_DIM)
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
    conn.execute(  # a legacy store holds content; an empty one is a blank store (see the interrupted-init test)
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('h1', 'some text', 'markdown', '/docs/a.md', 0)"
    )
    conn.execute("DELETE FROM embed_spaces")
    conn.execute("DELETE FROM config WHERE key = 'schema_version'")
    conn.commit()
    _declare_full(monkeypatch)
    init_schema(conn)
    recorded = _space_identity(conn)
    assert [recorded[field] for field in ("artifact_sha256", "pooling", "normalization", "max_input_tokens")] == [
        None
    ] * 4


# --- new spaces: the endpoint's fields from the declaration, the shape from the caller --


def test_a_new_space_records_its_shape_and_the_endpoint(kb_conn):
    import json

    import knowledge_base.routes.embeddings as emb
    from knowledge_base.embed_swap import create_space

    create_space(kb_conn, "alt", "nomic-embed-text", 768)
    assert _space_identity(kb_conn, "alt") == {
        "model": "nomic-embed-text",
        "provider": "ollama",
        "dim": 768,
        "matryoshka_base_dim": None,
        "element_type": "float32",
        "artifact_sha256": None,
        "pooling": None,
        "normalization": None,
        "max_input_tokens": None,
    }
    assert kb_conn.execute("SELECT query_instruction FROM embed_spaces WHERE name = 'alt'").fetchone()[0] is None
    with patch.object(emb, "_get_conn", return_value=kb_conn):
        result = json.loads(
            emb.create_embed_space_tool(name="alt2", model="nomic-embed-text", dim=768, query_instruction="")
        )
    assert "error" not in result
    assert kb_conn.execute("SELECT query_instruction FROM embed_spaces WHERE name = 'alt2'").fetchone()[0] == ""


_SINGLE_MODEL = {
    "artifact": {**FULL},
    "llama.cpp": {
        "KNOWLEDGE_BASE_EMBED_PROVIDER": "llama.cpp",
        "KNOWLEDGE_BASE_EMBED_URL": "https://embedder.example.test",
    },
    "openai_compat": {
        "KNOWLEDGE_BASE_EMBED_PROVIDER": "openai_compat",
        "KNOWLEDGE_BASE_EMBED_URL": "https://embedder.example.test",
    },
    "openai elsewhere": {
        "KNOWLEDGE_BASE_EMBED_PROVIDER": "openai",
        "KNOWLEDGE_BASE_EMBED_URL": "https://embedder.example.test",
    },
    "onnx": {"KNOWLEDGE_BASE_EMBED_PROVIDER": "onnx", "KNOWLEDGE_BASE_EMBED_URL": ""},
}


@pytest.mark.parametrize("endpoint", list(_SINGLE_MODEL))
def test_a_single_model_endpoint_refuses_a_space_of_another_model(endpoint, kb_conn, monkeypatch, tmp_path):
    from knowledge_base.embed_swap import create_space
    from knowledge_base.exceptions import EmbeddingModelMismatchError

    model_file = tmp_path / "model.onnx"
    model_file.write_bytes(b"")
    monkeypatch.setenv("ONNX_EMBED_MODEL_PATH", str(model_file))
    for name, value in _SINGLE_MODEL[endpoint].items():
        monkeypatch.setenv(name, value)
    before = kb_conn.execute("SELECT COUNT(*) FROM embed_spaces").fetchone()[0]
    with pytest.raises(EmbeddingModelMismatchError, match="model"):
        create_space(kb_conn, "other", "nomic-embed-text", TEST_EMBED_DIM)
    assert kb_conn.execute("SELECT COUNT(*) FROM embed_spaces").fetchone()[0] == before
    create_space(kb_conn, "smaller", "bge-m3", 512, matryoshka_base_dim=TEST_EMBED_DIM)
    assert _space_identity(kb_conn, "smaller")["model"] == "bge-m3"


def test_re_embed_records_the_whole_declaration(kb_conn, monkeypatch, embedder):
    from knowledge_base.embed_swap import re_embed
    from knowledge_base.settings import declared_identity, load_embed_settings

    _declare_full(monkeypatch, KNOWLEDGE_BASE_EMBED_MODEL=QWEN)
    result = re_embed(kb_conn)
    assert _space_identity(kb_conn, result["space"]) == declared_identity(load_embed_settings())
    active = kb_conn.execute("SELECT name FROM embed_spaces WHERE status = 'active'").fetchone()[0]
    assert active == result["space"]


# --- startup compares all nine fields --------------------------------------------

_SEED = {**FULL, "KNOWLEDGE_BASE_EMBED_MATRYOSHKA_BASE_DIM": "2048"}

#: (case, environment changes after seeding, active-row change after seeding, the differing fields)
_STARTUP_CASES = [
    ("model", {"KNOWLEDGE_BASE_EMBED_MODEL": "other-model"}, None, ["model"]),
    ("provider", {"KNOWLEDGE_BASE_EMBED_PROVIDER": "openai_compat"}, None, ["provider"]),
    ("dim", {"KNOWLEDGE_BASE_EMBED_DIM": "768"}, None, ["dim"]),
    ("matryoshka_base_dim", {"KNOWLEDGE_BASE_EMBED_MATRYOSHKA_BASE_DIM": ""}, None, ["matryoshka_base_dim"]),
    ("element_type", {}, "element_type = 'int8'", ["element_type"]),
    ("artifact_sha256", {"KNOWLEDGE_BASE_EMBED_ARTIFACT_SHA256": "b" * 64}, None, ["artifact_sha256"]),
    ("pooling", {"KNOWLEDGE_BASE_EMBED_POOLING": "mean"}, None, ["pooling"]),
    ("normalization", {"KNOWLEDGE_BASE_EMBED_NORMALIZATION": "none"}, None, ["normalization"]),
    ("max_input_tokens", {"KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS": "512"}, None, ["max_input_tokens"]),
    ("served artifact dropped", dict.fromkeys(FULL, ""), None, list(FULL)),
]


def _rows(conn: sqlite3.Connection) -> tuple:
    return (
        sorted(tuple(row) for row in conn.execute("SELECT key, value FROM config")),
        [tuple(row) for row in conn.execute("SELECT * FROM embed_spaces ORDER BY name")],
    )


@pytest.mark.parametrize(("case", "env", "row_change", "fields"), _STARTUP_CASES, ids=[c[0] for c in _STARTUP_CASES])
def test_startup_refuses_a_mismatch_in_any_field(case, env, row_change, fields, monkeypatch, capsys):
    from knowledge_base.db import open_declared_db
    from knowledge_base.settings import VARIABLES

    for name, value in _SEED.items():
        monkeypatch.setenv(name, value)
    conn = open_declared_db()
    if row_change:
        conn.execute(f"UPDATE embed_spaces SET {row_change} WHERE status = 'active'")  # noqa: S608  # literal case
        conn.commit()
    before = _rows(conn)
    conn.close()
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with patch.object(server.mcp, "run") as run, pytest.raises(SystemExit) as exit_info:
        server.main([])
    assert exit_info.value.code == 78
    err = capsys.readouterr().err
    for field in (f.removeprefix("KNOWLEDGE_BASE_EMBED_").lower() for f in fields):
        assert field in err
        assert VARIABLES.get(field, "KB declares 'float32'") in err
    assert "knowledge-base-ingest re-embed" in err
    run.assert_not_called()
    conn = get_connection(resolve_db_path())
    assert _rows(conn) == before
    conn.close()


def test_startup_refuses_adopting_a_served_artifact(monkeypatch, capsys, declared_db):
    conn = get_connection(resolve_db_path())
    before = _rows(conn)
    conn.close()
    _declare_full(monkeypatch)
    with patch.object(server.mcp, "run") as run, pytest.raises(SystemExit) as exit_info:
        server.main([])
    assert exit_info.value.code == 78
    err = capsys.readouterr().err
    assert all(name in err for name in FULL)
    assert "knowledge-base-ingest re-embed" in err
    run.assert_not_called()
    conn = get_connection(resolve_db_path())
    assert _rows(conn) == before
    conn.close()


@pytest.mark.parametrize("matryoshka", ["", "2048"])
def test_startup_accepts_the_identity_it_recorded(matryoshka, monkeypatch):
    from knowledge_base.db import open_declared_db

    _declare_full(monkeypatch, KNOWLEDGE_BASE_EMBED_MATRYOSHKA_BASE_DIM=matryoshka)
    open_declared_db().close()
    with patch.object(server.mcp, "run") as run:
        server.main([])
    run.assert_called_once_with(transport="stdio")


def test_the_server_process_exits_78_on_an_adopted_served_artifact(tmp_path, declared_db):
    import os
    import subprocess
    import sys

    env = {**os.environ, **FULL}
    proc = subprocess.run(  # noqa: S603  # trusted argv: this interpreter, our module
        [sys.executable, "-m", "knowledge_base.server"],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 78
    assert "KNOWLEDGE_BASE_EMBED_ARTIFACT_SHA256" in proc.stderr


def test_adopting_a_served_artifact_is_a_re_embed(monkeypatch, capsys, embedder):
    from knowledge_base.db import open_declared_db
    from knowledge_base.indexer import main as cli
    from knowledge_base.ingest import ingest_text

    conn = open_declared_db()
    ingest_text(conn, "Stored before the artifact was declared.", source_uri="/docs/a.md", source_type="markdown")
    conn.close()
    db = str(resolve_db_path())
    _declare_full(monkeypatch)
    with pytest.raises(SystemExit) as exit_info:
        cli(["--db", db, "--quiet", "rebind"])
    assert exit_info.value.code == 1
    assert "KNOWLEDGE_BASE_EMBED_ARTIFACT_SHA256" in capsys.readouterr().err
    cli(["--db", db, "--quiet", "re-embed"])
    conn = open_declared_db()
    active = conn.execute("SELECT name FROM embed_spaces WHERE status = 'active'").fetchone()[0]
    assert _space_identity(conn, active)["artifact_sha256"] == "a" * 64
    assert _space_identity(conn, "default")["artifact_sha256"] is None
    conn.close()


# --- every embedding boundary checks the space against its own identity -----------


def _call_boundary(boundary: str, conn: sqlite3.Connection, space: str) -> None:
    from knowledge_base.embed_swap import backfill_space, promote_space
    from knowledge_base.folder_summaries import update_folder_summary
    from knowledge_base.ingest import ingest_text
    from knowledge_base.search import search

    if boundary == "ingest_text":
        ingest_text(conn, "A new document.", source_uri="/docs/new.md", source_type="markdown")
    elif boundary == "update_folder_summary":
        update_folder_summary(conn, "/docs")
    elif boundary == "search":
        search(conn, "attention", mode="vec", space_name=None if space == "default" else space)
    elif boundary == "backfill_space":
        backfill_space(conn, space)
    else:
        promote_space(conn, space)


def _drift_store(tmp_path, monkeypatch, *, named: bool, provider_env: dict[str, str] | None = None):
    from knowledge_base.embed_swap import create_space

    for name, value in (provider_env or {}).items():
        monkeypatch.setenv(name, value)
    conn = _store(tmp_path)
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('h1', 'some text', 'markdown', '/docs/a.md', 0)"
    )
    conn.commit()
    if named:
        create_space(conn, "alt", "bge-m3", TEST_EMBED_DIM)
    return conn, ("alt" if named else "default")


_ACTIVE_BOUNDARIES = ["ingest_text", "update_folder_summary", "search"]
_NAMED_BOUNDARIES = ["search", "backfill_space", "promote_space"]
_DRIFT = [
    ("provider", "provider = 'onnx'"),
    ("artifact_sha256", f"artifact_sha256 = '{'b' * 64}'"),
]
_ACTIVE_ONLY_DRIFT = [
    ("model", "model = 'other-model'"),
    ("dim", "dim = 768"),
    ("matryoshka_base_dim", "matryoshka_base_dim = 2048"),
]


def _snapshot(conn: sqlite3.Connection) -> tuple:
    return (
        conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM chunks_vec").fetchone()[0],
        [tuple(row) for row in conn.execute("SELECT * FROM embed_spaces ORDER BY name")],
        sorted(tuple(row) for row in conn.execute("SELECT key, value FROM config")),
    )


@pytest.mark.parametrize(
    ("boundary", "named", "field", "change"),
    [(b, False, f, c) for b in _ACTIVE_BOUNDARIES for f, c in _DRIFT + _ACTIVE_ONLY_DRIFT]
    + [(b, True, f, c) for b in _NAMED_BOUNDARIES for f, c in _DRIFT],
)
def test_every_embedding_boundary_refuses_a_space_the_endpoint_cannot_serve(
    boundary, named, field, change, tmp_path, monkeypatch, http_requests
):
    from knowledge_base.exceptions import EmbeddingModelMismatchError

    conn, space = _drift_store(tmp_path, monkeypatch, named=named)
    conn.execute(f"UPDATE embed_spaces SET {change} WHERE name = ?", (space,))  # noqa: S608  # literal cases
    conn.commit()
    before = _snapshot(conn)
    with pytest.raises(EmbeddingModelMismatchError, match=field):
        _call_boundary(boundary, conn, space)
    assert http_requests == []
    assert _snapshot(conn) == before


@pytest.mark.parametrize("endpoint", ["onnx", "artifact"])
@pytest.mark.parametrize("boundary", _NAMED_BOUNDARIES)
def test_a_single_model_endpoint_refuses_a_named_space_of_another_model(
    endpoint, boundary, tmp_path, monkeypatch, http_requests
):
    from knowledge_base.exceptions import EmbeddingModelMismatchError

    conn, space = _drift_store(tmp_path, monkeypatch, named=True)
    conn.execute("UPDATE embed_spaces SET model = 'nomic-embed-text' WHERE name = 'alt'")
    conn.commit()
    if endpoint == "onnx":
        model_file = tmp_path / "model.onnx"
        model_file.write_bytes(b"")
        monkeypatch.setenv("ONNX_EMBED_MODEL_PATH", str(model_file))
        monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_PROVIDER", "onnx")
        monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_URL", "")
        conn.execute("UPDATE embed_spaces SET provider = 'onnx'")
    else:
        _declare_full(monkeypatch)
        conn.execute(
            "UPDATE embed_spaces SET artifact_sha256 = ?, pooling = 'last', normalization = 'l2',"
            " max_input_tokens = 2048",
            ("a" * 64,),
        )
    conn.commit()
    before = _snapshot(conn)
    with pytest.raises(EmbeddingModelMismatchError, match="model"):
        _call_boundary(boundary, conn, space)
    assert http_requests == []
    assert _snapshot(conn) == before


def test_cross_model_comparison_keeps_each_space_whole(kb_conn, embedder):
    """A guard: green before and after the gate, it proves the gate does not refuse a servable space (ruling 1)."""
    import json

    import knowledge_base.routes.embeddings as emb
    from knowledge_base.embed_swap import backfill_space, create_space
    from knowledge_base.ingest import ingest_text

    ingest_text(kb_conn, "Attention is all you need.", source_uri="/docs/a.md", source_type="markdown")
    create_space(kb_conn, "qwen", "qwen3-embedding:0.6b", TEST_EMBED_DIM)
    backfill_space(kb_conn, "qwen")
    embedder.bodies.clear()
    with patch.object(emb, "_get_conn", return_value=kb_conn):
        result = json.loads(emb.compare_spaces_tool(query="attention", space_a="default", space_b="qwen", mode="vec"))
    assert "error" not in result
    assert sorted((body["model"], body["input"][0]) for body in embedder.bodies) == [
        ("bge-m3", "attention"),
        ("qwen3-embedding:0.6b", LITERAL + "attention"),
    ]
    assert kb_conn.execute("SELECT COUNT(*) FROM chunks_vec_qwen").fetchone()[0] == 1


def test_promote_requires_the_declared_identity(kb_conn, embedder):
    from knowledge_base.embed_swap import backfill_space, create_space, promote_space
    from knowledge_base.exceptions import EmbeddingModelMismatchError

    create_space(kb_conn, "qwen", "qwen3-embedding:0.6b", TEST_EMBED_DIM)
    backfill_space(kb_conn, "qwen")
    with pytest.raises(EmbeddingModelMismatchError, match="model"):
        promote_space(kb_conn, "qwen")


def test_a_keyword_search_embeds_nothing_and_checks_nothing(tmp_path, monkeypatch, http_requests):
    from knowledge_base.ingest import ingest_text
    from knowledge_base.search import search

    conn = _store(tmp_path)
    with patch("knowledge_base.ingest.embed", lambda texts, **_kw: [[0.1] * TEST_EMBED_DIM for _ in texts]):
        ingest_text(conn, "Attention is all you need.", source_uri="/docs/a.md", source_type="markdown")
    conn.execute("UPDATE embed_spaces SET provider = 'onnx'")
    conn.commit()
    assert search(conn, "attention", mode="fts")
    assert http_requests == []


@pytest.mark.parametrize("tool", ["compare", "batch", "benchmark"])
def test_the_comparison_tools_report_a_space_the_endpoint_cannot_serve(tool, kb_conn, embedder):
    import json

    import knowledge_base.routes.embeddings as emb
    from knowledge_base.embed_swap import create_space

    create_space(kb_conn, "alt", "bge-m3", TEST_EMBED_DIM)
    kb_conn.execute("UPDATE embed_spaces SET provider = 'onnx' WHERE name = 'alt'")
    kb_conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('h1', 'some text', 'markdown', '/docs/a.md', 0)"
    )
    kb_conn.commit()
    with patch.object(emb, "_get_conn", return_value=kb_conn):
        if tool == "compare":
            result = json.loads(emb.compare_spaces_tool(query="q", space_a="default", space_b="alt", mode="vec"))
        elif tool == "batch":
            result = json.loads(
                emb.batch_compare_spaces_tool(queries=["q"], space_a="default", space_b="alt", mode="vec")
            )
        else:
            result = json.loads(emb.benchmark_spaces_tool(sample_queries=1))
    error = result["comparisons"][0]["error"] if tool == "benchmark" else result["error"]
    assert "provider 'onnx'" in error


# --- super-review round 1 (PR #612) -----------------------------------------------


def test_an_interrupted_first_initialization_still_records_the_declaration(tmp_path, monkeypatch):
    from knowledge_base.db import _seed_default_config
    from knowledge_base.settings import declared_identity, load_embed_settings

    _declare_full(monkeypatch, KNOWLEDGE_BASE_EMBED_MATRYOSHKA_BASE_DIM="2048")
    conn = get_connection(tmp_path / "kb.db")
    _seed_default_config(conn)  # committed; the process dies before the schema is built
    conn.close()
    conn = get_connection(tmp_path / "kb.db")
    init_schema(conn)
    assert _space_identity(conn) == declared_identity(load_embed_settings())


@pytest.mark.parametrize(("next_model", "kept"), [(QWEN, ""), ("other-model", None)])
def test_re_embed_keeps_the_instruction_of_the_same_model(next_model, kept, tmp_path, monkeypatch, embedder):
    from knowledge_base.embed_swap import re_embed

    monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_MODEL", QWEN)
    conn = _store(tmp_path)
    conn.execute("UPDATE embed_spaces SET query_instruction = ''")
    conn.commit()
    _declare_full(monkeypatch, KNOWLEDGE_BASE_EMBED_MODEL=next_model)
    result = re_embed(conn)
    recorded = conn.execute("SELECT query_instruction FROM embed_spaces WHERE name = ?", (result["space"],)).fetchone()
    assert recorded[0] == kept


def test_a_keyword_search_needs_no_space(kb_conn, http_requests):
    from knowledge_base.ingest import ingest_text
    from knowledge_base.search import search

    with patch("knowledge_base.ingest.embed", lambda texts, **_kw: [[0.1] * TEST_EMBED_DIM for _ in texts]):
        ingest_text(kb_conn, "Attention is all you need.", source_uri="/docs/a.md", source_type="markdown")
    assert search(kb_conn, "attention", mode="fts", space_name="no_such_space")
    assert http_requests == []
