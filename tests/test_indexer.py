"""Tests for the standalone indexer CLI."""

import argparse
import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from knowledge_base.db import get_connection, init_schema
from knowledge_base.indexer import (
    _drain_jobs,
    build_parser,
    cmd_ingest,
    cmd_ingest_url,
    cmd_re_embed,
    cmd_reingest,
    cmd_status,
    main,
)
from tests.declared_env import TEST_EMBED_DIM


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NEW_DIM = 384


def _fake_embed(texts, model="bge-m3", expected_dim=None, **_kwargs):
    dim = expected_dim if expected_dim is not None else TEST_EMBED_DIM
    return [[0.1] * dim for _ in texts]


def _fake_embed_new(texts, model="mxbai-embed-large", expected_dim=None, **_kwargs):
    dim = expected_dim if expected_dim is not None else NEW_DIM
    return [[0.2] * dim for _ in texts]


def _mock_provider(fake_fn):
    mock = MagicMock()
    mock.embed.side_effect = lambda texts, model=None, expected_dim=None: fake_fn(
        texts, model=model, expected_dim=expected_dim
    )
    return mock


FAKE_HTML = """
<html><head><title>Test Page</title></head>
<body>
<article>
<h1>Test Article</h1>
<p>The dominant sequence transduction models are based on complex recurrent or
convolutional neural networks that include an encoder and a decoder.</p>
</article>
</body></html>
"""


def _setup(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    return conn, db_path


def _make_args(tmp_path, **overrides):
    """Build a namespace mimicking argparse output."""
    db_path = tmp_path / "test.db"
    defaults = {"db": db_path, "verbose": False, "quiet": True}
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# build_parser
# ---------------------------------------------------------------------------


def test_build_parser_subcommands():
    parser = build_parser()
    # Verify all subcommands exist
    test_args = {
        "ingest": ["ingest", "/tmp/x"],
        "reingest": ["reingest", "/tmp/x"],
        "ingest-url": ["ingest-url", "https://example.com"],
        "re-embed": ["re-embed"],
        "rebind": ["rebind"],
        "status": ["status"],
        "migrate": ["migrate", "--check"],
        "schema": ["schema"],
    }
    for subcmd, argv in test_args.items():
        args = parser.parse_args(argv)
        assert args.command == subcmd


# ---------------------------------------------------------------------------
# #450 — migrate / schema subcommands
# ---------------------------------------------------------------------------


def _bootstrap(db):
    """Create a fresh DB stamped at the current schema version (offline)."""
    main(["--db", str(db), "--quiet", "migrate"])


def test_cli_migrate_bootstraps_fresh_db(tmp_path, capsys):
    db = tmp_path / "k.db"
    main(["--db", str(db), "migrate"])
    out = json.loads(capsys.readouterr().out)
    assert out["bootstrapped"] is True
    assert out["schema_version"] == 1
    assert db.is_file()


def test_cli_schema_reports_current(tmp_path, capsys):
    db = tmp_path / "k.db"
    _bootstrap(db)
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        main(["--db", str(db), "schema"])
    assert exc.value.code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["matches"] is True
    assert out["schema_version"] == 1


def test_cli_schema_mismatch_exits_nonzero(tmp_path):
    import sqlite3

    db = tmp_path / "k.db"
    _bootstrap(db)
    c = sqlite3.connect(db)
    c.execute("UPDATE config SET value='0' WHERE key='schema_version'")
    c.commit()
    c.close()
    with pytest.raises(SystemExit) as exc:
        main(["--db", str(db), "--quiet", "schema"])
    assert exc.value.code == 1


def test_cli_migrate_check_up_to_date_exit_zero(tmp_path):
    db = tmp_path / "k.db"
    _bootstrap(db)
    with pytest.raises(SystemExit) as exc:
        main(["--db", str(db), "--quiet", "migrate", "--check"])
    assert exc.value.code == 0
    assert not (tmp_path / "backups").exists()  # --check never backs up


def test_cli_migrate_check_pending_exit_nonzero(tmp_path, monkeypatch):
    db = tmp_path / "k.db"
    _bootstrap(db)
    monkeypatch.setattr("knowledge_base.migrate.CURRENT_SCHEMA_VERSION", 2)
    with pytest.raises(SystemExit) as exc:
        main(["--db", str(db), "--quiet", "migrate", "--check"])
    assert exc.value.code == 1
    assert not (tmp_path / "backups").exists()  # dry run — no mutation


def test_cli_migrate_backs_up_legacy_unversioned(tmp_path):
    # An existing DB with a `config` table but no schema_version row (legacy) is
    # backed up before the convergent bootstrap build.
    import sqlite3

    import knowledge_base.migrate as mm

    db = tmp_path / "k.db"
    _bootstrap(db)
    c = sqlite3.connect(db)
    c.execute("DELETE FROM config WHERE key='schema_version'")
    c.commit()
    c.close()
    main(["--db", str(db), "--quiet", "migrate"])  # legacy → backup + re-stamp
    assert (tmp_path / "backups").exists(), "legacy bootstrap must take a backup"
    assert mm.peek_schema_version(db) == 1  # re-stamped


def test_build_parser_re_embed_takes_its_identity_from_the_declaration():
    """#569: --model/--dim/--provider are gone; the declaration supplies them."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["re-embed", "--model", "x"])


# ---------------------------------------------------------------------------
# cmd_ingest
# ---------------------------------------------------------------------------


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_cmd_ingest_file(tmp_path, capsys):
    conn, db_path = _setup(tmp_path)
    conn.close()

    md_file = tmp_path / "doc.md"
    md_file.write_text("# Hello\n\nSome content for indexing.\n")

    args = _make_args(tmp_path, path=str(md_file), source_type=None, session_id=None)
    cmd_ingest(args)

    conn = get_connection(db_path)
    count = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
    assert count >= 1


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_cmd_ingest_directory(tmp_path, capsys):
    conn, db_path = _setup(tmp_path)
    conn.close()

    sub = tmp_path / "docs"
    sub.mkdir()
    (sub / "a.md").write_text("# Doc A\n\nContent A.\n")
    (sub / "b.md").write_text("# Doc B\n\nContent B.\n")

    args = _make_args(tmp_path, path=str(sub), source_type=None, session_id=None)
    cmd_ingest(args)

    conn = get_connection(db_path)
    sources = conn.execute("SELECT COUNT(DISTINCT source_uri) AS n FROM chunks").fetchone()["n"]
    assert sources == 2


def test_cmd_ingest_nonexistent(tmp_path):
    _setup(tmp_path)
    args = _make_args(tmp_path, path=str(tmp_path / "nope.md"), source_type=None, session_id=None)
    with pytest.raises(SystemExit, match="1"):
        cmd_ingest(args)


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_cmd_ingest_source_type_rejected_for_dir(tmp_path):
    _setup(tmp_path)
    sub = tmp_path / "docs"
    sub.mkdir()
    (sub / "a.md").write_text("# Doc\n")

    args = _make_args(tmp_path, path=str(sub), source_type="pdf", session_id=None)
    with pytest.raises(SystemExit, match="1"):
        cmd_ingest(args)


# ---------------------------------------------------------------------------
# cmd_reingest
# ---------------------------------------------------------------------------


@patch("knowledge_base.jobs._ensure_worker_running")
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_cmd_reingest(mock_worker, tmp_path):
    conn, db_path = _setup(tmp_path)

    md_file = tmp_path / "doc.md"
    md_file.write_text("# Original\n\nOriginal content.\n")

    from knowledge_base.ingest import ingest_file

    ingest_file(conn, md_file)
    conn.close()

    # Modify and reingest
    md_file.write_text("# Updated\n\nUpdated content.\n")
    args = _make_args(tmp_path, path=str(md_file), source_type=None, session_id=None)
    cmd_reingest(args)

    conn = get_connection(db_path)
    row = conn.execute("SELECT content FROM chunks LIMIT 1").fetchone()
    assert "Updated" in row["content"]


@patch("knowledge_base.indexer._drain_jobs")
@patch("knowledge_base.jobs._ensure_worker_running")
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_cmd_reingest_auto_relate(mock_drain, mock_worker, tmp_path):
    conn, db_path = _setup(tmp_path)

    md_file = tmp_path / "doc.md"
    md_file.write_text("# Paper\n\nContent.\n")

    from knowledge_base.ingest import ingest_file
    from knowledge_base.papers import register_paper

    ingest_file(conn, md_file)
    result = register_paper(conn, "Test Paper")
    pid = result["paper_id"]
    conn.execute(
        "INSERT INTO paper_paths (paper_id, path, is_primary) VALUES (?, ?, 1)",
        (pid, md_file.resolve().as_posix()),
    )
    conn.commit()
    conn.close()

    md_file.write_text("# Paper v2\n\nUpdated.\n")
    args = _make_args(tmp_path, path=str(md_file), source_type=None, session_id=None)
    cmd_reingest(args)

    conn = get_connection(db_path)
    jobs = conn.execute("SELECT * FROM jobs WHERE job_type = 'auto_relate'").fetchall()
    assert len(jobs) >= 1
    assert jobs[0]["paper_id"] == pid


# ---------------------------------------------------------------------------
# cmd_ingest_url
# ---------------------------------------------------------------------------


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_cmd_ingest_url(tmp_path, http_requests, fake_dns, public_net):
    fake_dns["example.com"] = ["203.0.113.10"]
    http_requests.handlers["example.com"] = lambda request: httpx.Response(200, html=FAKE_HTML)
    conn, db_path = _setup(tmp_path)
    conn.close()

    args = _make_args(tmp_path, url="https://example.com/paper", session_id=None)
    cmd_ingest_url(args)

    conn = get_connection(db_path)
    count = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
    assert count >= 1


# ---------------------------------------------------------------------------
# cmd_re_embed
# ---------------------------------------------------------------------------


@patch(
    "knowledge_base.embed_swap.get_provider",
    return_value=_mock_provider(_fake_embed_new),
)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_cmd_re_embed(mock_provider, tmp_path, monkeypatch):
    conn, db_path = _setup(tmp_path)

    # Ingest a file first
    md_file = tmp_path / "doc.md"
    md_file.write_text("# Test\n\nContent for re-embedding.\n")

    from knowledge_base.ingest import ingest_file

    ingest_file(conn, md_file)

    # Add a "similar" relationship to verify it gets deleted
    conn.execute("INSERT INTO papers (id, title) VALUES (1, 'P1'), (2, 'P2')")
    conn.execute("INSERT INTO relationships (source_paper_id, target_paper_id, relation_type) VALUES (1, 2, 'similar')")
    conn.commit()
    conn.close()

    monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_MODEL", "mxbai-embed-large")
    monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_DIM", str(NEW_DIM))
    cmd_re_embed(_make_args(tmp_path, batch_size=32, matryoshka_base_dim=None))

    conn = get_connection(db_path)
    rels = conn.execute("SELECT COUNT(*) AS n FROM relationships WHERE relation_type = 'similar'").fetchone()["n"]
    assert rels == 0


# ---------------------------------------------------------------------------
# cmd_status
# ---------------------------------------------------------------------------


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_cmd_status(tmp_path, capsys):
    conn, _db_path = _setup(tmp_path)

    md_file = tmp_path / "doc.md"
    md_file.write_text("# Hello\n\nContent.\n")

    from knowledge_base.ingest import ingest_file

    ingest_file(conn, md_file)
    conn.close()

    args = _make_args(tmp_path, quiet=False)
    args.command = "status"
    cmd_status(args)

    out = capsys.readouterr().out
    data = json.loads(out)
    assert "chunks" in data
    assert "sources" in data
    assert "papers" in data
    assert "jobs" in data
    assert "embedding" in data
    assert data["chunks"] >= 1


def test_cmd_status_never_prints_the_embedding_key(tmp_path, capsys):
    conn, _db_path = _setup(tmp_path)
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('embed_api_key', 'sk-test-only')")
    conn.commit()
    conn.close()

    cmd_status(_make_args(tmp_path, quiet=False))

    out = capsys.readouterr().out
    assert "sk-test-only" not in out
    assert "api_key" not in json.loads(out)["embedding"]


# ---------------------------------------------------------------------------
# _drain_jobs
# ---------------------------------------------------------------------------


def test_drain_jobs_returns_immediately_when_empty(tmp_path):
    conn, _ = _setup(tmp_path)
    # No jobs — should return instantly
    _drain_jobs(conn, job_ids=[], timeout=2.0)


def test_drain_jobs_times_out(tmp_path):
    conn, _ = _setup(tmp_path)
    # Need a paper for FK constraint
    conn.execute("INSERT INTO papers (id, title) VALUES (1, 'Test')")
    conn.execute("INSERT INTO jobs (id, paper_id, job_type, params) VALUES (42, 1, 'extract_structure', '{}')")
    conn.commit()
    _drain_jobs(conn, job_ids=[42], timeout=1.0)  # should not hang


# ---------------------------------------------------------------------------
# --db override
# ---------------------------------------------------------------------------


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_db_override(tmp_path):
    custom_db = tmp_path / "custom" / "test.db"
    custom_db.parent.mkdir()
    conn = get_connection(custom_db)
    init_schema(conn)
    conn.close()

    md_file = tmp_path / "doc.md"
    md_file.write_text("# Test\n\nContent.\n")

    args = _make_args(tmp_path, db=custom_db, path=str(md_file), source_type=None, session_id=None)
    cmd_ingest(args)

    conn = get_connection(custom_db)
    count = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
    assert count >= 1


# ---------------------------------------------------------------------------
# main() integration
# ---------------------------------------------------------------------------


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_main_integration(tmp_path, capsys):
    conn, db_path = _setup(tmp_path)
    conn.close()

    md_file = tmp_path / "doc.md"
    md_file.write_text("# Test\n\nContent.\n")

    main(["--db", str(db_path), "--quiet", "ingest", str(md_file)])

    conn = get_connection(db_path)
    count = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
    assert count >= 1


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_main_db_path_flag(tmp_path):
    # #449: the canonical --db-path flag selects the database.
    db_path = tmp_path / "via_flag.db"
    md_file = tmp_path / "doc.md"
    md_file.write_text("# Test\n\nContent.\n")

    main(["--db-path", str(db_path), "--quiet", "ingest", str(md_file)])

    conn = get_connection(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        assert count >= 1
    finally:
        conn.close()


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_main_env_var_resolves(tmp_path, monkeypatch):
    # #449: with no flag, $KNOWLEDGE_BASE_DB selects the database.
    db_path = tmp_path / "via_env.db"
    monkeypatch.setenv("KNOWLEDGE_BASE_DB", str(db_path))
    md_file = tmp_path / "doc.md"
    md_file.write_text("# Test\n\nContent.\n")

    main(["--quiet", "ingest", str(md_file)])

    conn = get_connection(db_path)  # explicit path ignores env, reads the file
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        assert count >= 1
    finally:
        conn.close()


# --- #569: the CLI's embedding commands run through the startup gate ---


def test_cli_ingest_without_a_declaration_exits_naming_it(tmp_path, monkeypatch, capsys):
    md_file = tmp_path / "doc.md"
    md_file.write_text("# Test\n\nContent.\n")
    monkeypatch.delenv("KNOWLEDGE_BASE_EMBED_PROVIDER")
    with pytest.raises(SystemExit) as exit_info:
        main(["--db", str(tmp_path / "kb.db"), "ingest", str(md_file)])
    assert exit_info.value.code == 1
    assert "KNOWLEDGE_BASE_EMBED_PROVIDER" in capsys.readouterr().err


def test_cli_ingest_on_a_disagreeing_database_exits_naming_the_field(tmp_path, monkeypatch, capsys):
    conn, db_path = _setup(tmp_path)
    conn.close()
    md_file = tmp_path / "doc.md"
    md_file.write_text("# Test\n\nContent.\n")
    monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_MODEL", "other-model")
    with pytest.raises(SystemExit) as exit_info:
        main(["--db", str(db_path), "ingest", str(md_file)])
    assert exit_info.value.code == 1
    assert "embed_model" in capsys.readouterr().err


def test_cli_maintenance_commands_need_no_declaration(tmp_path, monkeypatch):
    """Guard: status, schema and migrate --check work on an existing database with nothing declared."""
    conn, db_path = _setup(tmp_path)
    conn.close()
    for name in ("PROVIDER", "MODEL", "DIM", "URL"):
        monkeypatch.delenv(f"KNOWLEDGE_BASE_EMBED_{name}")
    main(["--db", str(db_path), "--quiet", "status"])
    for argv in (["schema"], ["migrate", "--check"]):
        with pytest.raises(SystemExit) as exit_info:
            main(["--db", str(db_path), "--quiet", *argv])
        assert exit_info.value.code == 0


# --- #569: re-embed and rebind are the two sanctioned transitions ---

_MOVED = {
    "KNOWLEDGE_BASE_EMBED_MODEL": "other-embed",
    "KNOWLEDGE_BASE_EMBED_DIM": "8",
    "KNOWLEDGE_BASE_EMBED_URL": "http://embedder-b.example.test:11434",
}


def _ollama_embedder(fail_after: int | None = None):
    calls = {"n": 0}

    def _handler(request):
        calls["n"] += 1
        if fail_after is not None and calls["n"] > fail_after:
            return httpx.Response(503, json={"error": "down"})
        texts = json.loads(request.content)["input"]
        return httpx.Response(200, json={"embeddings": [[0.5] * 8 for _ in texts]})

    return _handler


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def _seed_corpus(tmp_path, n_files: int = 1):
    conn, db_path = _setup(tmp_path)
    from knowledge_base.ingest import ingest_file

    for i in range(n_files):
        doc = tmp_path / "docs" / f"doc{i}.md"
        doc.parent.mkdir(exist_ok=True)
        doc.write_text(f"# Doc {i}\n\nContent number {i} for re-embedding.\n")
        ingest_file(conn, doc)
    conn.close()
    return db_path


def _plain_open(db_path):
    from knowledge_base.db import open_declared_db

    open_declared_db(db_path).close()


def test_cli_re_embed_moves_to_the_declaration(tmp_path, monkeypatch, http_requests, capsys):
    from knowledge_base.exceptions import KnowledgeBaseError

    db_path = _seed_corpus(tmp_path)
    for name, value in _MOVED.items():
        monkeypatch.setenv(name, value)
    http_requests.handlers["embedder-b.example.test"] = _ollama_embedder()

    main(["--db", str(db_path), "--quiet", "re-embed"])

    assert set(http_requests.hosts()) == {"embedder-b.example.test"}
    assert all(r.url.path == "/api/embed" for r in http_requests)
    _plain_open(db_path)

    with pytest.raises(SystemExit) as exit_info:
        main(["--db", str(db_path), "--quiet", "re-embed"])
    assert exit_info.value.code == 1
    assert "use rebind" in capsys.readouterr().err
    monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_URL", "http://embedder-c.example.test:11434")
    with pytest.raises(SystemExit):
        main(["--db", str(db_path), "--quiet", "re-embed"])
    with pytest.raises(KnowledgeBaseError, match="embed_base_url"):
        _plain_open(db_path)  # the refused re-embed recorded nothing


def test_cli_re_embed_interrupted_never_serves_and_reruns(tmp_path, monkeypatch, http_requests, capsys):
    from knowledge_base.exceptions import KnowledgeBaseError

    db_path = _seed_corpus(tmp_path, n_files=3)
    for name, value in _MOVED.items():
        monkeypatch.setenv(name, value)
    http_requests.handlers["embedder-b.example.test"] = _ollama_embedder(fail_after=1)

    with pytest.raises(SystemExit) as exit_info:
        main(["--db", str(db_path), "--quiet", "re-embed", "--batch-size", "1"])
    assert exit_info.value.code == 1
    err = capsys.readouterr().err
    assert "re-embed failed" in err
    assert "Traceback" not in err
    with pytest.raises(KnowledgeBaseError, match="re-embed"):
        _plain_open(db_path)
    conn = get_connection(db_path)
    assert [r["name"] for r in conn.execute("SELECT name FROM embed_spaces")] == ["default"]
    conn.close()

    http_requests.handlers["embedder-b.example.test"] = _ollama_embedder()
    main(["--db", str(db_path), "--quiet", "re-embed", "--batch-size", "1"])
    _plain_open(db_path)


def test_cli_rebind_adopts_a_moved_endpoint(tmp_path, monkeypatch, capsys):
    from knowledge_base.exceptions import KnowledgeBaseError

    conn, db_path = _setup(tmp_path)
    conn.close()
    monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_URL", "http://embedder-b.example.test:11434")
    with pytest.raises(KnowledgeBaseError, match="embed_base_url"):
        _plain_open(db_path)
    main(["--db", str(db_path), "--quiet", "rebind"])
    _plain_open(db_path)

    monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_MODEL", "other-model")
    with pytest.raises(SystemExit) as exit_info:
        main(["--db", str(db_path), "--quiet", "rebind"])
    assert exit_info.value.code == 1
    assert "embed_model" in capsys.readouterr().err


def test_cli_rebind_repairs_drifted_config_identity(tmp_path):
    conn, db_path = _setup(tmp_path)
    conn.execute("UPDATE config SET value = 'drifted-model' WHERE key = 'embed_model'")
    conn.commit()
    conn.close()
    main(["--db", str(db_path), "--quiet", "rebind"])
    conn = get_connection(db_path)
    assert conn.execute("SELECT value FROM config WHERE key = 'embed_model'").fetchone()["value"] == "bge-m3"
    conn.close()
    _plain_open(db_path)
