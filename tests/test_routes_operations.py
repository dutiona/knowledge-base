"""Wrapper-level tests for the operations route MCP tools.

These exercise the *server-only* logic of each tool wrapper in
``knowledge_base.routes.operations`` — branch selection, error mapping, and
JSON shaping — against a real (temp) SQLite database. The underlying domain
logic in ``jobs`` and ``prediction_errors`` is verified by their own module
tests; here we only confirm the wrapper threads inputs through and shapes the
output correctly.

Each test patches the route module's bound ``_get_conn`` to return the
``kb_conn`` fixture (a fresh, schema-initialized temp DB). The job worker is
stopped around every test so the background daemon never claims jobs from a
test database (which would trigger real LLM extraction).
"""

from __future__ import annotations

import json

import pytest

from knowledge_base.jobs import get_worker, submit_job
from knowledge_base.papers import register_paper
from knowledge_base.routes import operations as ops


@pytest.fixture(autouse=True)
def _no_worker(monkeypatch):
    """Neutralize the singleton job worker for every test in this module.

    ``submit_job`` calls ``_ensure_worker_running``, which starts the singleton
    daemon thread. Against a temp DB that thread would race to claim the very
    jobs we insert (flipping ``pending``→``running``) and could even dispatch
    real extraction. Patching it to a no-op lets ``submit_job`` exercise its real
    INSERT/dedup path while the wrappers' read-only behavior stays deterministic.
    Any worker leaked by a prior test is stopped first and last.
    """
    get_worker().stop()
    monkeypatch.setattr(
        "knowledge_base.jobs._ensure_worker_running", lambda *a, **k: None
    )
    yield
    get_worker().stop()


# --------------------------------------------------------------------------- #
# get_job_status_tool
# --------------------------------------------------------------------------- #


def test_get_job_status_tool_returns_job(kb_conn, monkeypatch):
    monkeypatch.setattr(ops, "_get_conn", lambda: kb_conn)
    paper_id = register_paper(kb_conn, "Paper A", ["X"], 2024)["paper_id"]
    # Worker is neutralized by the _no_worker fixture, so the job stays 'pending'.
    job_id = submit_job(kb_conn, paper_id, "extract_structure", {})

    result = json.loads(ops.get_job_status_tool(job_id))

    assert result["id"] == job_id
    assert result["paper_id"] == paper_id
    assert result["job_type"] == "extract_structure"
    assert result["status"] == "pending"


def test_get_job_status_tool_missing_returns_error(kb_conn, monkeypatch):
    monkeypatch.setattr(ops, "_get_conn", lambda: kb_conn)

    result = json.loads(ops.get_job_status_tool(99999))

    assert result == {"error": "Job 99999 not found"}


# --------------------------------------------------------------------------- #
# list_jobs_tool
# --------------------------------------------------------------------------- #


def test_list_jobs_tool_no_filter_returns_all(kb_conn, monkeypatch):
    monkeypatch.setattr(ops, "_get_conn", lambda: kb_conn)
    p1 = register_paper(kb_conn, "Paper A", ["X"], 2024)["paper_id"]
    p2 = register_paper(kb_conn, "Paper B", ["Y"], 2024)["paper_id"]
    j1 = submit_job(kb_conn, p1, "extract_structure", {})
    j2 = submit_job(kb_conn, p2, "extract_figures", {})
    get_worker().stop()

    result = json.loads(ops.list_jobs_tool())

    assert isinstance(result, list)
    assert {row["id"] for row in result} == {j1, j2}


def test_list_jobs_tool_status_filter_narrows(kb_conn, monkeypatch):
    monkeypatch.setattr(ops, "_get_conn", lambda: kb_conn)
    p1 = register_paper(kb_conn, "Paper A", ["X"], 2024)["paper_id"]
    p2 = register_paper(kb_conn, "Paper B", ["Y"], 2024)["paper_id"]
    j_pending = submit_job(kb_conn, p1, "extract_structure", {})
    j_done = submit_job(kb_conn, p2, "extract_figures", {})
    get_worker().stop()
    # Move one job to 'completed' so the status filter has something to exclude.
    kb_conn.execute("UPDATE jobs SET status = 'completed' WHERE id = ?", (j_done,))
    kb_conn.commit()

    pending = json.loads(ops.list_jobs_tool(status="pending"))
    completed = json.loads(ops.list_jobs_tool(status="completed"))

    assert [row["id"] for row in pending] == [j_pending]
    assert [row["id"] for row in completed] == [j_done]


def test_list_jobs_tool_paper_id_filter_narrows(kb_conn, monkeypatch):
    monkeypatch.setattr(ops, "_get_conn", lambda: kb_conn)
    p1 = register_paper(kb_conn, "Paper A", ["X"], 2024)["paper_id"]
    p2 = register_paper(kb_conn, "Paper B", ["Y"], 2024)["paper_id"]
    j1 = submit_job(kb_conn, p1, "extract_structure", {})
    submit_job(kb_conn, p2, "extract_figures", {})
    get_worker().stop()

    result = json.loads(ops.list_jobs_tool(paper_id=p1))

    assert [row["id"] for row in result] == [j1]
    assert all(row["paper_id"] == p1 for row in result)


# --------------------------------------------------------------------------- #
# list_prediction_errors_tool
# --------------------------------------------------------------------------- #


def _insert_prediction_error(
    conn,
    *,
    query: str,
    error_type: str = "no_results",
    resolved: bool = False,
    detected_at: str | None = None,
) -> int:
    """Insert a prediction_errors row directly and return its id.

    Direct SQL is used rather than ``detect_and_log`` because that path requires
    ``SearchResult`` objects and threshold/rate-limit machinery irrelevant to
    the wrapper under test; the table schema is confirmed against db.py.
    """
    cols = "query, query_hash, error_type"
    vals = [query, f"hash-{query}", error_type]
    placeholders = "?, ?, ?"
    if resolved:
        cols += ", resolved_at"
        vals.append("2024-01-01 00:00:00")
        placeholders += ", ?"
    if detected_at is not None:
        cols += ", detected_at"
        vals.append(detected_at)
        placeholders += ", ?"
    cursor = conn.execute(
        f"INSERT INTO prediction_errors ({cols}) VALUES ({placeholders})", vals
    )
    conn.commit()
    return cursor.lastrowid


def test_list_prediction_errors_tool_lists_unresolved(kb_conn, monkeypatch):
    monkeypatch.setattr(ops, "_get_conn", lambda: kb_conn)
    eid = _insert_prediction_error(kb_conn, query="missing topic")

    result = json.loads(ops.list_prediction_errors_tool())

    assert isinstance(result, list)
    assert [row["id"] for row in result] == [eid]
    assert result[0]["query"] == "missing topic"
    assert result[0]["resolved_at"] is None


def test_list_prediction_errors_tool_unresolved_only_excludes_resolved(
    kb_conn, monkeypatch
):
    monkeypatch.setattr(ops, "_get_conn", lambda: kb_conn)
    open_id = _insert_prediction_error(kb_conn, query="open gap")
    resolved_id = _insert_prediction_error(kb_conn, query="closed gap", resolved=True)

    # Default (unresolved_only=True) hides the resolved one.
    default = json.loads(ops.list_prediction_errors_tool())
    assert [row["id"] for row in default] == [open_id]

    # unresolved_only=False surfaces both.
    everything = json.loads(ops.list_prediction_errors_tool(unresolved_only=False))
    assert {row["id"] for row in everything} == {open_id, resolved_id}


def test_list_prediction_errors_tool_since_filter(kb_conn, monkeypatch):
    monkeypatch.setattr(ops, "_get_conn", lambda: kb_conn)
    old_id = _insert_prediction_error(
        kb_conn, query="old", detected_at="2020-01-01 00:00:00"
    )
    new_id = _insert_prediction_error(
        kb_conn, query="new", detected_at="2024-06-01 00:00:00"
    )

    result = json.loads(ops.list_prediction_errors_tool(since="2023-01-01"))

    ids = [row["id"] for row in result]
    assert new_id in ids
    assert old_id not in ids


# --------------------------------------------------------------------------- #
# resolve_prediction_error_tool
# --------------------------------------------------------------------------- #


def test_resolve_prediction_error_tool_success(kb_conn, monkeypatch):
    monkeypatch.setattr(ops, "_get_conn", lambda: kb_conn)
    eid = _insert_prediction_error(kb_conn, query="fixable")

    result = json.loads(ops.resolve_prediction_error_tool(eid))

    assert result == {"resolved": eid}
    # The underlying row is actually marked resolved.
    row = kb_conn.execute(
        "SELECT resolved_at FROM prediction_errors WHERE id = ?", (eid,)
    ).fetchone()
    assert row["resolved_at"] is not None


def test_resolve_prediction_error_tool_missing_maps_error(kb_conn, monkeypatch):
    monkeypatch.setattr(ops, "_get_conn", lambda: kb_conn)

    result = json.loads(ops.resolve_prediction_error_tool(12345))

    # resolve_prediction_error raises NotFoundError (a KnowledgeBaseError);
    # the wrapper maps it to {"error": <message>, **details}. details is empty.
    assert "error" in result
    assert "12345" in result["error"]
    assert result == {"error": "prediction error 12345 not found"}
