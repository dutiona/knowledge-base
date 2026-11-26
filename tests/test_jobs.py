"""Tests for deferred extraction job queue."""

import json
import secrets
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from knowledge_base.db import get_connection, init_schema
from knowledge_base.jobs import (
    _JobWorker,
    get_job,
    get_worker,
    list_jobs,
    submit_job,
)
from knowledge_base.papers import register_paper


def _setup(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    return conn, db_path


def _make_paper(conn):
    return register_paper(conn, "Test Paper")["paper_id"]


@pytest.fixture(autouse=True)
def _reset_worker():
    """Ensure the global worker is stopped before/after each test.

    Also suppress _ensure_worker_running so submit_job doesn't start
    the global worker — tests that need a worker create their own instance.
    """
    worker = get_worker()
    worker.reset()
    with patch("knowledge_base.jobs._ensure_worker_running"):
        yield
    worker.reset()


# --- submit / get / list ---


def test_submit_creates_pending_row(tmp_path):
    conn, _ = _setup(tmp_path)
    paper_id = _make_paper(conn)
    job_id = submit_job(conn, paper_id, "extract_structure")
    job = get_job(conn, job_id)
    assert job is not None
    assert job["status"] == "pending"
    assert job["paper_id"] == paper_id
    assert job["job_type"] == "extract_structure"


def test_get_job_not_found(tmp_path):
    conn, _ = _setup(tmp_path)
    assert get_job(conn, 9999) is None


def test_list_jobs_filters(tmp_path):
    conn, _ = _setup(tmp_path)
    p1 = _make_paper(conn)
    p2 = register_paper(conn, "Paper 2")["paper_id"]
    submit_job(conn, p1, "extract_structure")
    submit_job(conn, p2, "extract_figures")

    all_jobs = list_jobs(conn)
    assert len(all_jobs) == 2

    struct_jobs = list_jobs(conn, paper_id=p1)
    assert len(struct_jobs) == 1
    assert struct_jobs[0]["job_type"] == "extract_structure"

    pending = list_jobs(conn, status="pending")
    assert len(pending) == 2

    completed = list_jobs(conn, status="completed")
    assert len(completed) == 0


def test_deduplication(tmp_path):
    conn, _ = _setup(tmp_path)
    paper_id = _make_paper(conn)
    job_id1 = submit_job(conn, paper_id, "extract_structure")
    job_id2 = submit_job(conn, paper_id, "extract_structure")
    assert job_id1 == job_id2

    # Different job type is not a duplicate
    job_id3 = submit_job(conn, paper_id, "extract_figures")
    assert job_id3 != job_id1

    # Same type but different params is not a duplicate
    job_id4 = submit_job(conn, paper_id, "extract_figures", {"pages": [0]})
    job_id5 = submit_job(conn, paper_id, "extract_figures", {"pages": [1]})
    assert job_id4 != job_id3  # different params vs default
    assert job_id5 != job_id4  # different page lists


# --- Worker ---


def test_worker_processes_to_completion(tmp_path):
    conn, db_path = _setup(tmp_path)
    paper_id = _make_paper(conn)

    fake_result = {"methods_added": 3, "datasets_added": 1, "metrics_added": 2}

    submit_job(conn, paper_id, "extract_structure")

    worker = _JobWorker()
    with patch("knowledge_base.extraction.extract_structure", return_value=fake_result):
        worker.start(db_path=db_path)
        # Wait for worker to process
        for _ in range(50):
            time.sleep(0.1)
            job = get_job(conn, 1)
            if job and job["status"] == "completed":
                break
        worker.stop()

    job = get_job(conn, 1)
    assert job is not None
    assert job["status"] == "completed"
    result = json.loads(job["result"])
    assert result["methods_added"] == 3


def test_worker_handles_failure(tmp_path):
    conn, db_path = _setup(tmp_path)
    paper_id = _make_paper(conn)

    submit_job(conn, paper_id, "extract_structure")

    worker = _JobWorker()
    with patch(
        "knowledge_base.extraction.extract_structure",
        side_effect=RuntimeError("LLM down"),
    ):
        worker.start(db_path=db_path)
        for _ in range(50):
            time.sleep(0.1)
            job = get_job(conn, 1)
            if job and job["status"] == "failed":
                break
        worker.stop()

    job = get_job(conn, 1)
    assert job is not None
    assert job["status"] == "failed"
    assert "LLM down" in job["error"]


def test_progress_callback_updates_row(tmp_path):
    conn, db_path = _setup(tmp_path)
    paper_id = _make_paper(conn)

    def fake_extract(conn_, paper_id_, confirmed=False, on_progress=None, **kwargs):
        if on_progress:
            # Simulate chunk progress — every 5th gets written
            for i in range(10):
                on_progress(f"chunk {i + 1}/10")
            on_progress("resolving entities...")
            on_progress("storing results...")
        return {"methods_added": 0}

    submit_job(conn, paper_id, "extract_structure")

    worker = _JobWorker()
    with patch("knowledge_base.extraction.extract_structure", side_effect=fake_extract):
        worker.start(db_path=db_path)
        for _ in range(50):
            time.sleep(0.1)
            job = get_job(conn, 1)
            if job and job["status"] == "completed":
                break
        worker.stop()

    job = get_job(conn, 1)
    assert job is not None
    assert job["status"] == "completed"
    # Last progress written should be "storing results..."
    assert job["progress"] == "storing results..."


def test_sequential_processing(tmp_path):
    conn, db_path = _setup(tmp_path)
    paper_id = _make_paper(conn)

    call_order = []

    def fake_extract(conn_, paper_id_, confirmed=False, on_progress=None, **kwargs):
        call_order.append(len(call_order))
        time.sleep(0.05)
        return {"methods_added": 0}

    # Submit two jobs (different types to avoid deduplication)
    submit_job(conn, paper_id, "extract_structure")
    submit_job(conn, paper_id, "extract_figures")

    worker = _JobWorker()
    with (
        patch("knowledge_base.extraction.extract_structure", side_effect=fake_extract),
        patch("knowledge_base.vision.extract_figures", side_effect=fake_extract),
    ):
        worker.start(db_path=db_path)
        for _ in range(100):
            time.sleep(0.1)
            jobs = list_jobs(conn, status="completed")
            if len(jobs) >= 2:
                break
        worker.stop()

    jobs = list_jobs(conn)
    completed = [j for j in jobs if j["status"] == "completed"]
    assert len(completed) == 2

    # Second job started after first completed
    sorted_jobs = sorted(completed, key=lambda j: j["started_at"])
    assert sorted_jobs[0]["completed_at"] <= sorted_jobs[1]["started_at"]


def test_crash_recovery(tmp_path):
    conn, db_path = _setup(tmp_path)
    paper_id = _make_paper(conn)

    # Manually insert a "running" job (simulating a crash)
    conn.execute(
        "INSERT INTO jobs (paper_id, job_type, status, started_at) "
        "VALUES (?, 'extract_structure', 'running', datetime('now'))",
        (paper_id,),
    )
    conn.commit()

    job_before = get_job(conn, 1)
    assert job_before is not None
    assert job_before["status"] == "running"

    fake_result = {"methods_added": 0}

    worker = _JobWorker()
    with patch("knowledge_base.extraction.extract_structure", return_value=fake_result):
        worker.start(db_path=db_path)
        for _ in range(50):
            time.sleep(0.1)
            job = get_job(conn, 1)
            if job and job["status"] == "completed":
                break
        worker.stop()

    job = get_job(conn, 1)
    assert job is not None
    assert job["status"] == "completed"


def test_paper_deleted_while_pending(tmp_path):
    conn, _ = _setup(tmp_path)
    paper_id = _make_paper(conn)
    job_id = submit_job(conn, paper_id, "extract_structure")

    # Delete the paper — ON DELETE CASCADE should remove the job
    conn.execute("DELETE FROM papers WHERE id = ?", (paper_id,))
    conn.commit()

    job = get_job(conn, job_id)
    assert job is None


def test_worker_stop_reset(tmp_path):
    """Verify stop/reset lifecycle methods work without hanging."""
    worker = _JobWorker()
    worker.start()
    assert worker._thread is not None and worker._thread.is_alive()
    worker.stop()
    assert worker._thread is None
    # Reset after stop is safe
    worker.reset()


# --- A job's uncommitted writes (#643) ---


def _run_once(tmp_path, fake):
    """Submit one job, let the real worker run it once on this connection; read the outcome through another."""
    conn, db_path = _setup(tmp_path)
    paper_id = _make_paper(conn)
    job_id = submit_job(conn, paper_id, "extract_structure")
    with patch("knowledge_base.extraction.extract_structure", side_effect=fake):
        _JobWorker()._tick(conn)
    other = get_connection(db_path)
    try:
        job = get_job(other, job_id)
        names = [r["name"] for r in other.execute("SELECT name FROM methods ORDER BY name")]
    finally:
        other.close()
    assert job is not None
    return job, names


@pytest.mark.parametrize("progress", [False, True], ids=["quiet", "progress"])
@pytest.mark.parametrize("outcome", ["completes", "extraction-error", "raises"])
def test_a_job_s_uncommitted_writes_land_only_with_its_completion(tmp_path, outcome, progress):
    from knowledge_base.exceptions import ExtractionError

    def fake(conn_, paper_id_, confirmed=False, *, on_progress, **kwargs):
        conn_.execute("INSERT INTO methods (name, paper_id) VALUES ('leak', ?)", (paper_id_,))
        if progress:
            on_progress("storing results...")
        if outcome == "extraction-error":
            raise ExtractionError("boom", errors=[{"chunk_id": 1, "error": "boom"}])
        if outcome == "raises":
            raise RuntimeError("boom")
        return {}

    job, names = _run_once(tmp_path, fake)

    if outcome == "completes":
        assert (job["status"], names) == ("completed", ["leak"])
        return
    assert (job["status"], names) == ("failed", [])
    assert "boom" in job["error"]
    if outcome == "extraction-error":
        assert json.loads(job["result"])["errors"] == [{"chunk_id": 1, "error": "boom"}]


def test_a_failed_job_keeps_the_unit_it_committed_itself(tmp_path):
    def fake(conn_, paper_id_, confirmed=False, on_progress=None, **kwargs):
        conn_.execute("INSERT INTO methods (name, paper_id) VALUES ('kept', ?)", (paper_id_,))
        conn_.commit()
        conn_.execute("INSERT INTO methods (name, paper_id) VALUES ('leak', ?)", (paper_id_,))
        raise RuntimeError("boom")

    job, names = _run_once(tmp_path, fake)

    assert (job["status"], names) == ("failed", ["kept"])


def test_progress_is_visible_while_its_job_runs(tmp_path):
    seen = []

    def fake(conn_, paper_id_, confirmed=False, *, on_progress, **kwargs):
        on_progress("resolving entities...")
        other = get_connection(Path(conn_.execute("PRAGMA database_list").fetchone()["file"]))
        try:
            seen.append(other.execute("SELECT progress FROM jobs").fetchone()["progress"])
        finally:
            other.close()
        return {}

    job, _names = _run_once(tmp_path, fake)

    assert job["status"] == "completed"
    assert seen == ["resolving entities..."]


# --- #662: a failed job stores and logs no endpoint and no header value ---

_LLM_URL = "http://llm.example.test:1234/v1/chat/completions"


def _llm_status_error() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", _LLM_URL)
    response = httpx.Response(503, request=request)
    return httpx.HTTPStatusError(f"Server error '503' for url '{request.url}'", request=request, response=response)


@pytest.mark.parametrize("failure", ["status", "protocol"], ids=["status-error", "h11-refusal"])
def test_a_failed_job_stores_no_endpoint_and_no_header_value(tmp_path, caplog, failure):
    """The caller-visible job row and the log carry the failure class, never httpx's text (#662)."""
    import logging

    key = secrets.token_hex(16)

    def fake(conn_, paper_id_, confirmed=False, *, on_progress, **kwargs):
        if failure == "status":
            raise _llm_status_error()
        # h11's own shape: the refused header value is quoted in the message
        raise httpx.LocalProtocolError(f"Illegal header value b'Bearer {key}'")

    with caplog.at_level(logging.ERROR, logger="knowledge_base.jobs"):
        job, _names = _run_once(tmp_path, fake)

    assert job["status"] == "failed"
    if failure == "status":
        assert job["error"] == "HTTPStatusError (HTTP 503)"
    assert "llm.example.test" not in job["error"]
    assert key not in job["error"]
    assert "llm.example.test" not in json.dumps(job["result"])
    assert key not in json.dumps(job["result"])
    assert "llm.example.test" not in caplog.text
    assert key not in caplog.text
