"""Wrapper-level unit tests for the ingestion route MCP tools.

These exercise the *server-only* logic of each tool wrapper in
``knowledge_base.routes.ingestion`` — path existence checks, dir-vs-file
branching, result aggregation, exception→JSON error mapping, and the reingest
orchestration side-effect (stale 'similar' relationship invalidation + auto_relate
job submission). The domain ingestion functions (``ingest_file``,
``ingest_directory``, ``reingest_file``, ``ingest_url``) compute embeddings via
Ollama / hit the network, so they are mocked at the route module namespace; every
test here is fully offline.

Config wrappers (``configure_chunking`` and ``configure_browser_tool``'s
disable/error paths) run against the real ``kb_conn`` temp DB with no mocks.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from knowledge_base.exceptions import NotFoundError, ValidationError
from knowledge_base.routes.ingestion import (
    configure_browser_tool,
    configure_chunking,
    ingest,
    ingest_url,
    reingest,
)

# ---------------------------------------------------------------------------
# ingest()
# ---------------------------------------------------------------------------


def test_ingest_nonexistent_path_returns_error(kb_conn, tmp_path):
    """A path that does not exist short-circuits to an error dict (no domain call)."""
    missing = tmp_path / "does_not_exist.pdf"
    with patch("knowledge_base.routes.ingestion._get_conn", return_value=kb_conn):
        result = json.loads(ingest(str(missing)))
    assert result == {"error": f"Path does not exist: {missing.resolve()}"}


def test_ingest_file_passes_through_result_and_forwards_args(kb_conn, tmp_path):
    """A real file path routes to ingest_file; its dict is returned verbatim and
    source_type/session_id are forwarded."""
    f = tmp_path / "doc.md"
    f.write_text("")  # real file so .exists()/.is_dir() are correct
    domain_result = {"file": f.as_posix(), "chunks_added": 3, "chunks_skipped": 1}

    with (
        patch("knowledge_base.routes.ingestion._get_conn", return_value=kb_conn),
        patch(
            "knowledge_base.routes.ingestion.ingest_file",
            return_value=domain_result,
        ) as mock_ingest_file,
        patch("knowledge_base.routes.ingestion.ingest_directory") as mock_dir,
    ):
        result = json.loads(ingest(str(f), source_type="markdown", session_id="sess-1"))

    assert result == domain_result
    mock_dir.assert_not_called()
    # ingest_file(conn, p, source_type, session_id=session_id)
    args, kwargs = mock_ingest_file.call_args
    assert args[0] is kb_conn
    assert args[1] == f.resolve()
    assert args[2] == "markdown"
    assert kwargs["session_id"] == "sess-1"


def test_ingest_directory_aggregates_per_file_results(kb_conn, tmp_path):
    """A directory path routes to ingest_directory; the wrapper aggregates counts
    and echoes the per-file list under 'details'."""
    d = tmp_path / "papers"
    d.mkdir()
    per_file = [
        {"file": "a.md", "chunks_added": 5, "chunks_skipped": 2},
        {"file": "b.md", "chunks_added": 3, "chunks_skipped": 0},
        {"file": "c.md", "chunks_added": 0, "chunks_skipped": 4},
    ]

    with (
        patch("knowledge_base.routes.ingestion._get_conn", return_value=kb_conn),
        patch(
            "knowledge_base.routes.ingestion.ingest_directory",
            return_value=per_file,
        ) as mock_dir,
        patch("knowledge_base.routes.ingestion.ingest_file") as mock_file,
    ):
        result = json.loads(ingest(str(d)))

    mock_file.assert_not_called()
    args, _ = mock_dir.call_args
    assert args[0] is kb_conn
    assert args[1] == d.resolve()
    assert result["files_processed"] == 3
    assert result["chunks_added"] == 8  # 5 + 3 + 0
    assert result["chunks_skipped"] == 6  # 2 + 0 + 4
    assert result["details"] == per_file


# ---------------------------------------------------------------------------
# reingest()
# ---------------------------------------------------------------------------


def test_reingest_nonexistent_path_returns_error(kb_conn, tmp_path):
    missing = tmp_path / "gone.pdf"
    with patch("knowledge_base.routes.ingestion._get_conn", return_value=kb_conn):
        result = json.loads(reingest(str(missing)))
    assert result == {"error": f"Path does not exist: {missing.resolve()}"}


def test_reingest_happy_path_passthrough_no_affected_papers(kb_conn, tmp_path):
    """When no paper_paths row matches the file, the wrapper returns the domain
    result verbatim and submits no job."""
    f = tmp_path / "doc.md"
    f.write_text("")
    domain_result = {"file": f.as_posix(), "chunks_added": 2, "chunks_skipped": 0}

    with (
        patch("knowledge_base.routes.ingestion._get_conn", return_value=kb_conn),
        patch(
            "knowledge_base.routes.ingestion.reingest_file",
            return_value=domain_result,
        ),
        patch("knowledge_base.jobs.submit_job") as mock_submit,
    ):
        result = json.loads(reingest(str(f)))

    assert result == domain_result
    mock_submit.assert_not_called()


def test_reingest_maps_knowledgebase_error_to_json(kb_conn, tmp_path):
    """reingest_file raising a KnowledgeBaseError → {"error": str(e), **e.details}."""
    f = tmp_path / "doc.md"
    f.write_text("")
    exc = NotFoundError(
        "No chunks found for source_uri: x", details={"source_uri": "x"}
    )

    with (
        patch("knowledge_base.routes.ingestion._get_conn", return_value=kb_conn),
        patch("knowledge_base.routes.ingestion.reingest_file", side_effect=exc),
        patch("knowledge_base.jobs.submit_job") as mock_submit,
    ):
        result = json.loads(reingest(str(f)))

    assert result == {"error": str(exc), "source_uri": "x"}
    mock_submit.assert_not_called()


def test_reingest_invalidates_similar_relationships_and_submits_jobs(kb_conn, tmp_path):
    """After a successful reingest of a file matching a paper_paths row, the
    wrapper DELETEs 'similar' relationships for affected papers and submits an
    auto_relate job per affected paper."""
    f = tmp_path / "paper.pdf"
    f.write_text("")
    source_uri = f.resolve().as_posix()  # wrapper uses p.as_posix() on resolved path

    # Four papers. paper 1 is linked to the file (affected); paper 2 is its
    # 'similar' target. papers 3 & 4 are UNAFFECTED — their 'similar' edge must
    # survive, which pins the paper-id scoping of the DELETE.
    cur = kb_conn.execute("INSERT INTO papers (title) VALUES ('Paper One')")
    pid1 = cur.lastrowid
    cur = kb_conn.execute("INSERT INTO papers (title) VALUES ('Paper Two')")
    pid2 = cur.lastrowid
    cur = kb_conn.execute("INSERT INTO papers (title) VALUES ('Paper Three')")
    pid3 = cur.lastrowid
    cur = kb_conn.execute("INSERT INTO papers (title) VALUES ('Paper Four')")
    pid4 = cur.lastrowid
    kb_conn.execute(
        "INSERT INTO paper_paths (paper_id, path) VALUES (?, ?)", (pid1, source_uri)
    )
    # 'similar' edge touching the affected paper — must be purged.
    kb_conn.execute(
        "INSERT INTO relationships (source_paper_id, target_paper_id, relation_type) "
        "VALUES (?, ?, 'similar')",
        (pid1, pid2),
    )
    # 'similar' edge between two UNAFFECTED papers — must survive. Guards against
    # dropping the `(source_paper_id = ? OR target_paper_id = ?)` clause (which
    # would wipe every 'similar' row regardless of which paper was reingested).
    kb_conn.execute(
        "INSERT INTO relationships (source_paper_id, target_paper_id, relation_type) "
        "VALUES (?, ?, 'similar')",
        (pid3, pid4),
    )
    # A non-'similar' relationship on the affected paper that must survive.
    # Guards against dropping the `relation_type = 'similar'` clause.
    kb_conn.execute(
        "INSERT INTO relationships (source_paper_id, target_paper_id, relation_type) "
        "VALUES (?, ?, 'cites')",
        (pid1, pid2),
    )
    kb_conn.commit()

    domain_result = {"file": source_uri, "chunks_added": 4, "chunks_skipped": 0}

    with (
        patch("knowledge_base.routes.ingestion._get_conn", return_value=kb_conn),
        patch(
            "knowledge_base.routes.ingestion.reingest_file",
            return_value=domain_result,
        ),
        # reingest() imports submit_job lazily (`from ..jobs import submit_job`
        # inside the function), so patch it at its definition module — patching
        # knowledge_base.routes.ingestion.submit_job would NOT intercept it.
        patch("knowledge_base.jobs.submit_job", return_value=99) as mock_submit,
    ):
        result = json.loads(reingest(str(f)))

    assert result == domain_result

    # The affected paper's 'similar' edge is gone, but the unaffected papers'
    # 'similar' edge survives — exactly one 'similar' row remains, and it is the
    # pid3↔pid4 one (proves the DELETE was scoped to the reingested paper).
    similar_rows = kb_conn.execute(
        "SELECT source_paper_id, target_paper_id FROM relationships "
        "WHERE relation_type = 'similar'"
    ).fetchall()
    assert [(r["source_paper_id"], r["target_paper_id"]) for r in similar_rows] == [
        (pid3, pid4)
    ]
    # ...and the unrelated 'cites' relationship survives (relation_type scoping).
    remaining_cites = kb_conn.execute(
        "SELECT COUNT(*) AS n FROM relationships WHERE relation_type = 'cites'"
    ).fetchone()["n"]
    assert remaining_cites == 1

    # submit_job called once for the single affected paper with auto_relate.
    mock_submit.assert_called_once()
    call_args = mock_submit.call_args
    assert call_args[0][0] is kb_conn
    assert call_args[0][1] == pid1
    assert call_args[0][2] == "auto_relate"
    assert call_args[0][3] == {"paper_id": pid1}


# ---------------------------------------------------------------------------
# ingest_url()
# ---------------------------------------------------------------------------


def test_ingest_url_passthrough_success(kb_conn):
    """_ingest_url success result is serialized verbatim; args forwarded."""
    domain_result = {
        "url": "https://example.com",
        "chunks_added": 7,
        "chunks_skipped": 0,
    }
    with (
        patch("knowledge_base.routes.ingestion._get_conn", return_value=kb_conn),
        patch(
            "knowledge_base.routes.ingestion._ingest_url",
            return_value=domain_result,
        ) as mock_url,
    ):
        result = json.loads(ingest_url("https://example.com", session_id="s-9"))

    assert result == domain_result
    args, kwargs = mock_url.call_args
    assert args[0] is kb_conn
    assert args[1] == "https://example.com"
    assert kwargs["session_id"] == "s-9"


def test_ingest_url_maps_knowledgebase_error_to_json(kb_conn):
    """A KnowledgeBaseError from _ingest_url → {"error": str(e), **e.details}."""
    exc = ValidationError("URL must include a hostname", details={"url": "ftp://x"})
    with (
        patch("knowledge_base.routes.ingestion._get_conn", return_value=kb_conn),
        patch("knowledge_base.routes.ingestion._ingest_url", side_effect=exc),
    ):
        result = json.loads(ingest_url("ftp://x"))

    assert result == {"error": str(exc), "url": "ftp://x"}


# ---------------------------------------------------------------------------
# configure_chunking() — pure config against real kb_conn
# ---------------------------------------------------------------------------


def test_configure_chunking_query_default(kb_conn):
    """Querying (strategy=None) on a fresh DB returns the 'mechanical' default."""
    with patch("knowledge_base.routes.ingestion._get_conn", return_value=kb_conn):
        result = json.loads(configure_chunking())
    assert result == {"chunk_strategy": "mechanical"}


def test_configure_chunking_set_semantic_persists(kb_conn):
    """Setting 'semantic' echoes it and persists across a subsequent query."""
    with patch("knowledge_base.routes.ingestion._get_conn", return_value=kb_conn):
        set_result = json.loads(configure_chunking("semantic"))
        assert set_result == {"chunk_strategy": "semantic"}
        # Re-query with None confirms persistence.
        query_result = json.loads(configure_chunking())
    assert query_result == {"chunk_strategy": "semantic"}


def test_configure_chunking_invalid_strategy_rejected_no_write(kb_conn):
    """An invalid strategy returns an error mentioning valid options and does not
    mutate the stored config."""
    with patch("knowledge_base.routes.ingestion._get_conn", return_value=kb_conn):
        result = json.loads(configure_chunking("bogus"))
        assert "error" in result
        assert "mechanical" in result["error"]
        assert "semantic" in result["error"]
        # Config unchanged — still the default.
        after = json.loads(configure_chunking())
    assert after == {"chunk_strategy": "mechanical"}


# ---------------------------------------------------------------------------
# configure_browser_tool()
# ---------------------------------------------------------------------------


def test_configure_browser_tool_disable_success(kb_conn):
    """Disabling (both empty strings) is a no-filesystem success path returning
    {"browser": None}."""
    with patch("knowledge_base.routes.ingestion._get_conn", return_value=kb_conn):
        result = json.loads(configure_browser_tool(cdp_endpoint="", venv_path=""))
    assert result == {"browser": None}


def test_configure_browser_tool_maps_validation_error(kb_conn):
    """An invalid (non-absolute / missing) venv path surfaces as an error dict via
    the KnowledgeBaseError mapping — exercised against the real configure_browser."""
    with patch("knowledge_base.routes.ingestion._get_conn", return_value=kb_conn):
        result = json.loads(
            configure_browser_tool(venv_path="/nonexistent/venv/does/not/exist")
        )
    assert "error" in result
    # configure_browser raises "Python executable not found in venv at ..."
    assert "Python executable not found" in result["error"]


def test_configure_browser_tool_error_mapping_includes_details(kb_conn):
    """When configure_browser raises a KnowledgeBaseError with details, the wrapper
    merges them into the JSON error response."""
    exc = ValidationError("boom", details={"field": "venv_path"})
    with (
        patch("knowledge_base.routes.ingestion._get_conn", return_value=kb_conn),
        patch("knowledge_base.routes.ingestion.configure_browser", side_effect=exc),
    ):
        result = json.loads(configure_browser_tool(venv_path="/some/path"))
    assert result == {"error": "boom", "field": "venv_path"}
