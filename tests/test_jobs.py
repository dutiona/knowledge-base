"""Tests for deferred extraction job queue."""

import json
import time
from unittest.mock import patch

import pytest

from research_index.db import get_connection, init_schema
from research_index.jobs import (
    _JobWorker,
    get_job,
    get_worker,
    list_jobs,
    submit_job,
)
from research_index.papers import register_paper


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
    with patch("research_index.jobs._ensure_worker_running"):
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
    with patch("research_index.extraction.extract_structure", return_value=fake_result):
        worker.start(db_path=db_path)
        # Wait for worker to process
        for _ in range(50):
            time.sleep(0.1)
            job = get_job(conn, 1)
            if job and job["status"] == "completed":
                break
        worker.stop()

    job = get_job(conn, 1)
    assert job["status"] == "completed"
    result = json.loads(job["result"])
    assert result["methods_added"] == 3


def test_worker_handles_failure(tmp_path):
    conn, db_path = _setup(tmp_path)
    paper_id = _make_paper(conn)

    submit_job(conn, paper_id, "extract_structure")

    worker = _JobWorker()
    with patch(
        "research_index.extraction.extract_structure",
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
    assert job["status"] == "failed"
    assert "LLM down" in job["error"]


def test_progress_callback_updates_row(tmp_path):
    conn, db_path = _setup(tmp_path)
    paper_id = _make_paper(conn)

    def fake_extract(conn_, paper_id_, confirmed=False, on_progress=None):
        if on_progress:
            # Simulate chunk progress — every 5th gets written
            for i in range(10):
                on_progress(f"chunk {i + 1}/10")
            on_progress("resolving entities...")
            on_progress("storing results...")
        return {"methods_added": 0}

    submit_job(conn, paper_id, "extract_structure")

    worker = _JobWorker()
    with patch("research_index.extraction.extract_structure", side_effect=fake_extract):
        worker.start(db_path=db_path)
        for _ in range(50):
            time.sleep(0.1)
            job = get_job(conn, 1)
            if job and job["status"] == "completed":
                break
        worker.stop()

    job = get_job(conn, 1)
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
        patch("research_index.extraction.extract_structure", side_effect=fake_extract),
        patch("research_index.vision.extract_figures", side_effect=fake_extract),
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
    assert job_before["status"] == "running"

    fake_result = {"methods_added": 0}

    worker = _JobWorker()
    with patch("research_index.extraction.extract_structure", return_value=fake_result):
        worker.start(db_path=db_path)
        for _ in range(50):
            time.sleep(0.1)
            job = get_job(conn, 1)
            if job and job["status"] == "completed":
                break
        worker.stop()

    job = get_job(conn, 1)
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
