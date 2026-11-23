"""Tests for the standalone indexer CLI."""

import argparse
import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from knowledge_base.db import get_connection, init_schema
from knowledge_base.migrate import CURRENT_SCHEMA_VERSION
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
from tests.conftest import fake_embedding_provider
from tests.declared_env import TEST_EMBED_DIM


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NEW_DIM = 384


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
        "redact": ["redact", "--rewrite"],
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
    assert out["schema_version"] == CURRENT_SCHEMA_VERSION
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
    assert out["schema_version"] == CURRENT_SCHEMA_VERSION


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
    monkeypatch.setattr("knowledge_base.migrate.CURRENT_SCHEMA_VERSION", CURRENT_SCHEMA_VERSION + 1)
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
    assert mm.peek_schema_version(db) == mm.CURRENT_SCHEMA_VERSION  # re-stamped


def test_build_parser_re_embed_takes_its_identity_from_the_declaration():
    """#569/#573: --model/--dim/--provider/--matryoshka-base-dim are gone; the declaration supplies them."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["re-embed", "--model", "x"])
    with pytest.raises(SystemExit):
        parser.parse_args(["re-embed", "--matryoshka-base-dim", "2048"])


# ---------------------------------------------------------------------------
# cmd_ingest
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("stub_document_embedder")
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


@pytest.mark.usefixtures("stub_document_embedder")
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


@pytest.mark.usefixtures("stub_document_embedder")
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


@pytest.mark.usefixtures("stub_document_embedder")
@patch("knowledge_base.jobs._ensure_worker_running")
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


@pytest.mark.usefixtures("stub_document_embedder")
@patch("knowledge_base.indexer._drain_jobs")
@patch("knowledge_base.jobs._ensure_worker_running")
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


@pytest.mark.usefixtures("stub_document_embedder")
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


@pytest.mark.usefixtures("stub_document_embedder")
@patch(
    "knowledge_base.embed_swap.get_provider",
    return_value=_mock_provider(_fake_embed_new),
)
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
    cmd_re_embed(_make_args(tmp_path, batch_size=32))

    conn = get_connection(db_path)
    rels = conn.execute("SELECT COUNT(*) AS n FROM relationships WHERE relation_type = 'similar'").fetchone()["n"]
    assert rels == 0


# ---------------------------------------------------------------------------
# cmd_status
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("stub_document_embedder")
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


@pytest.mark.usefixtures("stub_document_embedder")
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


@pytest.mark.usefixtures("stub_document_embedder")
def test_main_integration(tmp_path, capsys):
    conn, db_path = _setup(tmp_path)
    conn.close()

    md_file = tmp_path / "doc.md"
    md_file.write_text("# Test\n\nContent.\n")

    main(["--db", str(db_path), "--quiet", "ingest", str(md_file)])

    conn = get_connection(db_path)
    count = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
    assert count >= 1


@pytest.mark.usefixtures("stub_document_embedder")
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


@pytest.mark.usefixtures("stub_document_embedder")
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
    assert "KNOWLEDGE_BASE_EMBED_MODEL" in capsys.readouterr().err


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


def _seed_corpus(tmp_path, n_files: int = 1):
    conn, db_path = _setup(tmp_path)
    from knowledge_base.ingest import ingest_file

    with fake_embedding_provider():
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
    assert "KNOWLEDGE_BASE_EMBED_MODEL" in capsys.readouterr().err


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


# --- #596: the store lock ---

#: Every subcommand, classified: a writing one holds the store lock, a read-only one takes none.
_WRITING = {
    "remove": ["remove", "content:///x"],
    "ingest": ["ingest", "{doc}"],
    "reingest": ["reingest", "{doc}"],
    "ingest-url": ["ingest-url", "https://docs.example.test/"],
    "re-embed": ["re-embed"],
    "rebind": ["rebind"],
    "migrate": ["migrate"],
    "redact": ["redact", "--rewrite"],
}
_READ_ONLY = {
    "status": ["status"],
    "schema": ["schema"],
    "migrate": ["migrate", "--check"],
    "redact": ["redact"],
}


def _exit_of(argv: list[str]) -> int | str | None:
    try:
        main(argv)
    except SystemExit as exc:
        return exc.code
    return 0


def _store(tmp_path):
    """A current store with one ingested document; returns its path and the document."""
    conn, db = _setup(tmp_path)
    doc = tmp_path / "doc.md"
    doc.write_text("# Doc\n\nSome text to keep.\n")
    from knowledge_base.ingest import ingest_file

    with fake_embedding_provider():
        ingest_file(conn, doc)
    conn.close()
    return db, doc


def _dump(db) -> list[str]:
    conn = get_connection(db)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def test_every_command_is_classified():
    choices = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)).choices
    assert set(choices) == set(_WRITING) | set(_READ_ONLY)


@pytest.mark.parametrize("argv", list(_WRITING.values()), ids=list(_WRITING))
def test_a_writing_command_refuses_while_the_store_is_held(tmp_path, capsys, http_requests, argv):
    from knowledge_base.store_lock import hold, lock_path

    db, doc = _store(tmp_path)
    before = _dump(db)
    with hold(db, "test"):
        assert _exit_of(["--db", str(db), *(a.format(doc=doc) for a in argv)]) == 1
    error = json.loads(capsys.readouterr().err)["error"]
    assert str(lock_path(db)) in error
    assert http_requests == []
    assert _dump(db) == before


@pytest.mark.parametrize("argv", list(_READ_ONLY.values()), ids=list(_READ_ONLY))
def test_a_read_only_command_runs_while_the_store_is_held(tmp_path, argv):
    from knowledge_base.store_lock import hold

    db, _ = _store(tmp_path)
    with hold(db, "test"):
        assert _exit_of(["--db", str(db), "--quiet", *argv]) == 0


@pytest.mark.parametrize("argv", [["status"], ["redact"]], ids=["status", "redact"])
def test_a_read_only_command_never_initializes_a_store(tmp_path, capsys, argv):
    import sqlite3

    missing = tmp_path / "missing.db"
    assert _exit_of(["--db", str(missing), *argv]) == 1
    assert "no database at" in capsys.readouterr().err
    assert not missing.exists()

    legacy = tmp_path / "legacy.db"
    sqlite3.connect(legacy).executescript("CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT);").close()
    before = legacy.read_bytes()
    assert _exit_of(["--db", str(legacy), *argv]) == 1
    assert "migrate" in capsys.readouterr().err
    assert legacy.read_bytes() == before


# --- #578: a server started during a transition exits 78 naming it ---


@pytest.mark.parametrize(
    ("command", "env"),
    [("re-embed", _MOVED), ("rebind", {"KNOWLEDGE_BASE_EMBED_URL": "http://embedder-b.example.test:11434"})],
    ids=["re-embed", "rebind"],
)
def test_a_server_started_during_a_transition_exits_78_naming_it(tmp_path, monkeypatch, http_requests, command, env):
    import io
    import os
    from contextlib import redirect_stderr

    from knowledge_base import indexer, server
    from knowledge_base.db import open_declared_db

    db_path = _seed_corpus(tmp_path)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("KNOWLEDGE_BASE_DB", str(db_path))
    http_requests.handlers["embedder-b.example.test"] = _ollama_embedder()
    started: list[tuple[int | str | None, str]] = []

    def _open_then_start_a_server(*args, **kwargs):
        conn = open_declared_db(*args, **kwargs)
        err = io.StringIO()
        with patch.object(server.mcp, "run") as run, redirect_stderr(err), pytest.raises(SystemExit) as refused:
            server.main([])
        run.assert_not_called()
        started.append((refused.value.code, err.getvalue()))
        return conn

    monkeypatch.setattr(indexer, "open_declared_db", _open_then_start_a_server)
    main(["--db", str(db_path), "--quiet", command])
    ((code, err),) = started
    assert code == 78
    assert f"knowledge-base-ingest {command} (pid {os.getpid()}, since " in err
    _plain_open(db_path)  # the transition completed


# --- #595: knowledge-base-ingest remove ---

_PAGE = "https://docs.example.test/page"


def _chunk_ids(conn, source_uri: str) -> list[int]:
    return [r["id"] for r in conn.execute("SELECT id FROM chunks WHERE source_uri = ? ORDER BY id", (source_uri,))]


def _vector_ids(conn, table: str) -> set[int]:
    return {r[0] for r in conn.execute(f"SELECT chunk_id FROM [{table}]")}  # noqa: S608


def test_remove_a_url_document(tmp_path, capsys, stub_document_embedder):
    from knowledge_base.conclusions import record_conclusion
    from knowledge_base.db import insert_chunk_vec
    from knowledge_base.embed_swap import create_space
    from knowledge_base.ingest import ingest_text
    from knowledge_base.papers import add_relationship, register_paper
    from tests.declared_env import TEST_EMBED_MODEL

    conn, db = _setup(tmp_path)
    ingest_text(conn, "A page about attention mechanisms.", source_uri=_PAGE, source_type="web")
    figure = conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('fig-hash', 'A figure of the page.', 'figure', ?, 9000)",
        (_PAGE,),
    ).lastrowid
    assert figure is not None
    insert_chunk_vec(conn, figure, [0.1] * TEST_EMBED_DIM)
    ingest_text(conn, "Another document entirely.", source_uri="content:///other", source_type="markdown")
    page_chunk, other_chunk = _chunk_ids(conn, _PAGE)[0], _chunk_ids(conn, "content:///other")[0]
    space = create_space(conn, "spare", TEST_EMBED_MODEL, TEST_EMBED_DIM)["table_name"]
    for chunk in (page_chunk, figure, other_chunk):
        insert_chunk_vec(conn, chunk, [0.1] * TEST_EMBED_DIM, table_name=space)
    conn.execute("UPDATE embed_spaces SET chunk_count = 3 WHERE name = 'spare'")
    paper = register_paper(conn, "P")["paper_id"]
    other_paper = register_paper(conn, "Q")["paper_id"]
    conn.execute("UPDATE papers SET abstract_chunk_id = ? WHERE id = ?", (page_chunk, paper))
    add_relationship(conn, paper, other_paper, "cites", evidence_chunk_id=page_chunk)
    conclusion = record_conclusion(conn, "claim", source_chunk_ids=[page_chunk, other_chunk])["conclusion_id"]
    conn.commit()
    conn.close()

    main(["--db", str(db), "remove", "https://reader:example-pass@docs.example.test/page"])

    assert json.loads(capsys.readouterr().out) == {"source_uri": _PAGE, "chunks_deleted": 2}
    conn = get_connection(db)
    assert _chunk_ids(conn, _PAGE) == []
    assert conn.execute("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'attention'").fetchone()[0] == 0
    for table in ("chunks_vec", space):
        assert _vector_ids(conn, table) == {other_chunk}
    for row in conn.execute("SELECT table_name, chunk_count FROM embed_spaces"):
        assert row["chunk_count"] == len(_vector_ids(conn, row["table_name"]))
    assert conn.execute("SELECT abstract_chunk_id FROM papers WHERE id = ?", (paper,)).fetchone()[0] is None
    assert conn.execute("SELECT evidence_chunk_id FROM relationships").fetchone()[0] is None
    cited = conn.execute("SELECT source_chunk_ids FROM conclusions WHERE id = ?", (conclusion,)).fetchone()[0]
    assert json.loads(cited) == [other_chunk]
    assert _chunk_ids(conn, "content:///other") == [other_chunk]


def test_remove_a_content_document_takes_its_shared_passage_from_later_ones(tmp_path, stub_document_embedder):
    from knowledge_base.ingest import ingest_text

    conn, db = _setup(tmp_path)
    passage = "A passage two documents contain."
    ingest_text(conn, passage, source_uri="content://laptop/early.md", source_type="markdown")
    later = ingest_text(conn, passage, source_uri="content://laptop/later.md", source_type="markdown")
    assert later == {"chunks_added": 0, "chunks_skipped": 1}  # stored once, under the first document
    conn.close()

    main(["--db", str(db), "--quiet", "remove", "content://laptop/early.md"])

    conn = get_connection(db)
    assert conn.execute("SELECT COUNT(*) FROM chunks WHERE content = ?", (passage,)).fetchone()[0] == 0


def test_remove_an_unknown_source_uri_changes_nothing(tmp_path, capsys):
    db, _ = _store(tmp_path)
    before = _dump(db)
    assert _exit_of(["--db", str(db), "remove", "content:///nowhere"]) == 1
    assert "content:///nowhere" in json.loads(capsys.readouterr().err)["error"]
    assert _dump(db) == before


def test_remove_refuses_a_file_that_exists(tmp_path, capsys):
    db, doc = _store(tmp_path)
    before = _dump(db)
    assert _exit_of(["--db", str(db), "remove", doc.as_posix()]) == 1
    assert "reingest" in json.loads(capsys.readouterr().err)["error"]
    assert _dump(db) == before
    with fake_embedding_provider():
        assert _exit_of(["--db", str(db), "--quiet", "reingest", str(doc)]) == 0


def test_remove_a_deleted_files_document(tmp_path, capsys, stub_document_embedder):
    """A file document whose file is gone is removed by its stored path, like a URL document (#631)."""
    from knowledge_base.conclusions import record_conclusion
    from knowledge_base.db import insert_chunk_vec
    from knowledge_base.embed_swap import create_space
    from knowledge_base.ingest import ingest_file, ingest_text
    from knowledge_base.papers import register_paper
    from tests.declared_env import TEST_EMBED_MODEL

    conn, db = _setup(tmp_path)
    notes = tmp_path / "notes"
    notes.mkdir()
    doc = notes / "gone.md"
    doc.write_text("# Gone\n\nA passage about granulometries that will be deleted.\n")
    ingest_file(conn, doc)
    uri = doc.resolve().as_posix()
    ingest_text(conn, "Another document entirely.", source_uri="content:///other", source_type="markdown")
    (gone,), (other,) = _chunk_ids(conn, uri), _chunk_ids(conn, "content:///other")
    space = create_space(conn, "spare", TEST_EMBED_MODEL, TEST_EMBED_DIM)["table_name"]
    for chunk in (gone, other):
        insert_chunk_vec(conn, chunk, [0.1] * TEST_EMBED_DIM, table_name=space)
    conn.execute("UPDATE embed_spaces SET chunk_count = 2 WHERE name = 'spare'")
    paper = register_paper(conn, "P")["paper_id"]
    conn.execute("UPDATE papers SET abstract_chunk_id = ? WHERE id = ?", (gone, paper))
    conn.execute("INSERT INTO paper_paths (paper_id, path) VALUES (?, ?)", (paper, uri))
    conclusion = record_conclusion(conn, "claim", source_chunk_ids=[gone, other])["conclusion_id"]
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM folder_summaries").fetchone()[0] == 1
    conn.close()
    doc.unlink()

    main(["--db", str(db), "remove", uri])

    assert json.loads(capsys.readouterr().out) == {"source_uri": uri, "chunks_deleted": 1}
    conn = get_connection(db)
    assert _chunk_ids(conn, uri) == []
    assert conn.execute("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'granulometries'").fetchone()[0] == 0
    for table in ("chunks_vec", space):
        assert _vector_ids(conn, table) == {other}
    for row in conn.execute("SELECT table_name, chunk_count FROM embed_spaces"):
        assert row["chunk_count"] == len(_vector_ids(conn, row["table_name"]))
    assert conn.execute("SELECT abstract_chunk_id FROM papers WHERE id = ?", (paper,)).fetchone()[0] is None
    cited = conn.execute("SELECT source_chunk_ids FROM conclusions WHERE id = ?", (conclusion,)).fetchone()[0]
    assert json.loads(cited) == [other]
    assert conn.execute("SELECT COUNT(*) FROM folder_summaries").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM folder_summaries_vec").fetchone()[0] == 0
    assert conn.execute("SELECT path FROM paper_paths WHERE paper_id = ?", (paper,)).fetchone()[0] == uri


def test_remove_a_moved_files_document(tmp_path, stub_document_embedder):
    from knowledge_base.ingest import ingest_file

    conn, db = _setup(tmp_path)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    old = tmp_path / "a" / "doc.md"
    old.write_text("# Doc\n\n" + "".join(f"distinct passage number {i}. " for i in range(60)))
    stored = ingest_file(conn, old)
    conn.close()
    new = old.rename(tmp_path / "b" / "doc.md")

    assert _exit_of(["--db", str(db), "--quiet", "remove", old.resolve().as_posix()]) == 0
    conn = get_connection(db)
    again = ingest_file(conn, new)
    assert (again["chunks_added"], again["chunks_skipped"]) == (stored["chunks_added"], 0)


def test_reingest_of_a_gone_file_names_remove(tmp_path, capsys):
    db, doc = _store(tmp_path)
    before = _dump(db)
    doc.unlink()
    assert _exit_of(["--db", str(db), "reingest", str(doc)]) == 1
    assert "knowledge-base-ingest remove" in json.loads(capsys.readouterr().err)["error"]
    assert _dump(db) == before


def test_a_failed_remove_changes_nothing(tmp_path, stub_document_embedder, monkeypatch):
    from knowledge_base.ingest import ingest_text

    conn, db = _setup(tmp_path)
    ingest_text(conn, "Text that must survive.", source_uri="content:///keep", source_type="markdown")
    conn.close()
    before = _dump(db)

    def _broken(*_args, **_kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("knowledge_base.db.delete_chunk_vecs", _broken)
    with pytest.raises(RuntimeError):
        main(["--db", str(db), "remove", "content:///keep"])
    assert _dump(db) == before


def test_remove_help_states_what_dedup_implies(capsys):
    with pytest.raises(SystemExit):
        main(["remove", "--help"])
    assert "only copy of a passage" in " ".join(capsys.readouterr().out.split())
