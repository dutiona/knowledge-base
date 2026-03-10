"""Deferred extraction job queue with background worker."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Callable
from typing import Any

from .db import get_connection, init_schema

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def submit_job(
    conn: sqlite3.Connection,
    paper_id: int,
    job_type: str,
    params: dict[str, Any] | None = None,
) -> int:
    """Insert a pending job row, wake the worker, return job_id.

    If a pending/running job already exists for the same (paper_id, job_type),
    returns the existing job_id instead of creating a duplicate.
    """
    params_json = json.dumps(params or {}, sort_keys=True)
    existing = conn.execute(
        "SELECT id FROM jobs "
        "WHERE paper_id = ? AND job_type = ? AND params = ? "
        "AND status IN ('pending', 'running')",
        (paper_id, job_type, params_json),
    ).fetchone()
    if existing:
        return existing["id"]

    cursor = conn.execute(
        "INSERT INTO jobs (paper_id, job_type, params) VALUES (?, ?, ?)",
        (paper_id, job_type, params_json),
    )
    conn.commit()
    job_id = cursor.lastrowid
    _ensure_worker_running()
    return job_id


def get_job(conn: sqlite3.Connection, job_id: int) -> dict[str, Any] | None:
    """Read a single job row."""
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    return dict(row)


def list_jobs(
    conn: sqlite3.Connection,
    status: str | None = None,
    paper_id: int | None = None,
) -> list[dict[str, Any]]:
    """List jobs with optional filters."""
    clauses: list[str] = []
    params: list[Any] = []
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if paper_id is not None:
        clauses.append("paper_id = ?")
        params.append(paper_id)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM jobs{where} ORDER BY created_at DESC", params
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


class _JobWorker:
    """Singleton daemon thread that processes jobs sequentially."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._stop_flag = False

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._event.set()
                return
            self._stop_flag = False
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="job-worker"
            )
            self._thread.start()

    def stop(self) -> None:
        """Signal the worker to stop and wait for it."""
        self._stop_flag = True
        self._event.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    def reset(self) -> None:
        """Stop and clear state — for test isolation."""
        self.stop()
        self._stop_flag = False

    def notify(self) -> None:
        self._event.set()

    def _run(self) -> None:
        conn = get_connection()
        init_schema(conn)
        conn.execute("PRAGMA busy_timeout=5000")

        # Crash recovery: reset stale running jobs
        conn.execute(
            "UPDATE jobs SET status = 'pending', started_at = NULL "
            "WHERE status = 'running'"
        )
        conn.commit()
        logger.info("Job worker started (crash recovery applied)")

        while not self._stop_flag:
            try:
                self._tick(conn)
            except Exception:
                logger.exception("Unexpected error in job worker loop")

    def _tick(self, conn: sqlite3.Connection) -> None:
        """Process one job or sleep."""
        # Atomic claim: UPDATE ... RETURNING
        row = conn.execute(
            "UPDATE jobs SET status = 'running', started_at = datetime('now') "
            "WHERE id = ("
            "  SELECT id FROM jobs WHERE status = 'pending' "
            "  ORDER BY created_at LIMIT 1"
            ") RETURNING *"
        ).fetchone()
        conn.commit()

        if row is None:
            self._event.wait(timeout=5.0)
            self._event.clear()
            return

        job = dict(row)
        job_id = job["id"]
        logger.info(
            "Job %d started: %s for paper %d", job_id, job["job_type"], job["paper_id"]
        )

        # Build throttled progress callback
        _progress_counter = 0

        def on_progress(msg: str) -> None:
            nonlocal _progress_counter
            _progress_counter += 1
            # Throttle: write every 5th call, or on phase transitions (non-"chunk" messages)
            if _progress_counter % 5 != 0 and msg.startswith("chunk "):
                return
            conn.execute("UPDATE jobs SET progress = ? WHERE id = ?", (msg, job_id))
            conn.commit()

        try:
            # Verify paper still exists
            paper = conn.execute(
                "SELECT id FROM papers WHERE id = ?", (job["paper_id"],)
            ).fetchone()
            if paper is None:
                raise ValueError(f"Paper {job['paper_id']} no longer exists")

            result = self._dispatch(conn, job, on_progress)
            conn.execute(
                "UPDATE jobs SET status = 'completed', result = ?, "
                "completed_at = datetime('now') WHERE id = ?",
                (json.dumps(result), job_id),
            )
            conn.commit()
            logger.info("Job %d completed", job_id)
        except Exception as exc:
            conn.execute(
                "UPDATE jobs SET status = 'failed', error = ?, "
                "completed_at = datetime('now') WHERE id = ?",
                (str(exc), job_id),
            )
            conn.commit()
            logger.error("Job %d failed: %s", job_id, exc)

    def _dispatch(
        self,
        conn: sqlite3.Connection,
        job: dict[str, Any],
        on_progress: Callable[[str], None],
    ) -> dict[str, Any]:
        params = json.loads(job["params"])

        if job["job_type"] == "extract_structure":
            from .extraction import extract_structure

            return extract_structure(
                conn, job["paper_id"], confirmed=True, on_progress=on_progress
            )
        elif job["job_type"] == "extract_figures":
            from .vision import extract_figures

            return extract_figures(
                conn,
                job["paper_id"],
                pages=params.get("pages"),
                confirmed=True,
                on_progress=on_progress,
            )
        else:
            raise ValueError(f"Unknown job type: {job['job_type']}")


_worker = _JobWorker()


def _ensure_worker_running() -> None:
    _worker.start()


def get_worker() -> _JobWorker:
    """Expose worker for test isolation."""
    return _worker
