"""Tests for the standalone indexer CLI."""

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest

from knowledge_base.db import DEFAULT_EMBED_DIM, get_connection, init_schema
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NEW_DIM = 384


def _fake_embed(texts, model="bge-m3", expected_dim=None, **_kwargs):
    dim = expected_dim if expected_dim is not None else DEFAULT_EMBED_DIM
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


def _mock_httpx_get(url, **kwargs):
    resp = MagicMock()
    resp.status_code = 200
    resp.text = FAKE_HTML
    resp.raise_for_status = MagicMock()
    return resp


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
        "re-embed": ["re-embed", "--model", "x", "--dim", "8"],
        "status": ["status"],
    }
    for subcmd, argv in test_args.items():
        args = parser.parse_args(argv)
        assert args.command == subcmd


def test_build_parser_re_embed_requires_model_and_dim():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["re-embed"])


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
    sources = conn.execute(
        "SELECT COUNT(DISTINCT source_uri) AS n FROM chunks"
    ).fetchone()["n"]
    assert sources == 2


def test_cmd_ingest_nonexistent(tmp_path):
    _setup(tmp_path)
    args = _make_args(
        tmp_path, path=str(tmp_path / "nope.md"), source_type=None, session_id=None
    )
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
@patch("knowledge_base.web.httpx.get", _mock_httpx_get)
def test_cmd_ingest_url(tmp_path):
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
def test_cmd_re_embed(mock_provider, tmp_path):
    conn, db_path = _setup(tmp_path)

    # Ingest a file first
    md_file = tmp_path / "doc.md"
    md_file.write_text("# Test\n\nContent for re-embedding.\n")

    from knowledge_base.ingest import ingest_file

    ingest_file(conn, md_file)

    # Add a "similar" relationship to verify it gets deleted
    conn.execute("INSERT INTO papers (id, title) VALUES (1, 'P1'), (2, 'P2')")
    conn.execute(
        "INSERT INTO relationships (source_paper_id, target_paper_id, relation_type) "
        "VALUES (1, 2, 'similar')"
    )
    conn.commit()
    conn.close()

    args = _make_args(
        tmp_path,
        model="mxbai-embed-large",
        dim=NEW_DIM,
        batch_size=32,
        provider=None,
        matryoshka_base_dim=None,
    )
    cmd_re_embed(args)

    conn = get_connection(db_path)
    rels = conn.execute(
        "SELECT COUNT(*) AS n FROM relationships WHERE relation_type = 'similar'"
    ).fetchone()["n"]
    assert rels == 0


# ---------------------------------------------------------------------------
# cmd_status
# ---------------------------------------------------------------------------


@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_cmd_status(tmp_path, capsys):
    conn, db_path = _setup(tmp_path)

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
    conn.execute(
        "INSERT INTO jobs (id, paper_id, job_type, params) "
        "VALUES (42, 1, 'extract_structure', '{}')"
    )
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

    args = _make_args(
        tmp_path, db=custom_db, path=str(md_file), source_type=None, session_id=None
    )
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
