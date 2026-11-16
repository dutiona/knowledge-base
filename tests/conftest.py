"""Shared pytest fixtures for the knowledge-base test suite.

Database fixtures are opt-in by argument name — they do not change the
behavior of existing tests that build their own temp databases inline.
Three autouse guards apply to every test: the job-worker reset, the
data-dir redirect (#391) and the embedding declaration (#569).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator

import httpx
import pytest

from knowledge_base.db import get_connection, init_schema
from tests.declared_env import DECLARATION


@pytest.fixture
def kb_conn(tmp_path) -> Iterator[sqlite3.Connection]:
    """A fresh, schema-initialized SQLite connection backed by a temp-dir DB.

    Every test gets an isolated database file under its own ``tmp_path`` — the
    real DB at ``~/.local/share/knowledge-base`` is never touched. Use together
    with ``patch("knowledge_base.routes.<mod>._get_conn", return_value=kb_conn)``
    to exercise an MCP tool wrapper against a real database.
    """
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    try:
        yield conn
    finally:
        # Closed per test so WAL handles don't accumulate across the session —
        # leaked connections raise resource/timing pressure that can surface
        # latent concurrency races elsewhere in the suite.
        conn.close()


@pytest.fixture(autouse=True)
def _reset_job_worker():
    """Guarantee the process-global job-worker singleton is stopped at every test
    boundary.

    The worker (``knowledge_base.jobs.get_worker()``) is a process-global
    singleton; any test that triggers ``submit_job`` → ``_ensure_worker_running``
    starts a daemon thread bound to *that test's* temp DB. Without a reset, a
    later test could inherit a live daemon pointed at a deleted database (a
    flakiness and DB-lock source). Resetting before and after each test
    centralizes the invariant so individual modules don't have to remember it.

    Idempotent: ``reset()`` on an already-stopped worker is a cheap no-op. Tests
    that exercise worker *behavior* instantiate their own ``_JobWorker()`` rather
    than the singleton, so they are unaffected.
    """
    from knowledge_base.jobs import get_worker

    get_worker().reset()
    yield
    get_worker().reset()


@pytest.fixture(autouse=True)
def _isolated_kb_data_dir(tmp_path, monkeypatch):
    """Point the artifact and DB env overrides at a per-test temp dir (#391).

    ``kb_data_dir()`` (ingest.py) resolves the base directory for on-disk
    artifacts — figure PNGs, rendered vector pages — at call time: env var
    first, then the connection's DB directory, then the real home. Tests on
    tmp-path DBs are isolated structurally by the second step; this guard
    covers the conn-less and in-memory paths that would otherwise fall
    through to the developer's real ``~/.local/share/knowledge-base``.
    ``KNOWLEDGE_BASE_DB`` is pinned too so any future test hitting
    ``resolve_db_path()`` with no explicit path cannot touch the real DB.
    Tests asserting on artifact locations may still override either variable;
    monkeypatch scoping keeps each test hermetic.
    """
    monkeypatch.setenv("KNOWLEDGE_BASE_DATA_DIR", str(tmp_path / "kb-data"))
    monkeypatch.setenv("KNOWLEDGE_BASE_DB", str(tmp_path / "kb-data" / "knowledge.db"))


@pytest.fixture(autouse=True)
def _declared_environment(monkeypatch):
    """Run every test under the suite's embedding declaration (#569).

    KB refuses to seed a blank database or start without ``KNOWLEDGE_BASE_EMBED_*``,
    and vision, the browser fallback and LLM extraction are off unless switched on.
    The suite declares the pre-#569 defaults and switches all three on, so existing
    tests keep their meaning; tests of the defaults unset what they need. A
    developer's shell cannot leak a key or the deprecated ``EMBED_PROVIDER`` in.
    """
    for name, value in DECLARATION.items():
        monkeypatch.setenv(name, value)
    for name in ("EMBED_PROVIDER", "KNOWLEDGE_BASE_EMBED_API_KEY", "KNOWLEDGE_BASE_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)


class HttpRecorder(list):
    """Every httpx request a test made, in order; ``handlers`` answers by host."""

    def __init__(self) -> None:
        super().__init__()
        self.handlers: dict[str, Callable[[httpx.Request], httpx.Response]] = {}

    def hosts(self) -> list[str]:
        return [request.url.host for request in self]


@pytest.fixture
def http_requests(monkeypatch) -> HttpRecorder:
    """Record every httpx request at the transport; answer registered hosts, refuse the rest.

    Patched below every ``httpx.get``/``post``/``Client`` call, so a zero-request assertion
    holds whichever call site would have fired, and nothing ever reaches the network.
    """
    recorder = HttpRecorder()

    def _handle(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        handler = recorder.handlers.get(request.url.host)
        if handler is None:
            raise httpx.ConnectError(f"unexpected host {request.url.host}", request=request)
        return handler(request)

    async def _handle_async(_transport, request: httpx.Request) -> httpx.Response:
        return _handle(request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda _transport, request: _handle(request))
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _handle_async)
    return recorder


@pytest.fixture
def fake_dns(monkeypatch) -> dict[str, list[str]]:
    """Answer ``getaddrinfo`` for the SSRF floor from a ``{host: [addresses]}`` map; fail closed otherwise."""
    import socket

    table: dict[str, list[str]] = {}

    def _getaddrinfo(host, *_args, **_kwargs):
        if host not in table:
            raise socket.gaierror(f"fake_dns: unknown host {host}")
        return [
            (socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 0)) for a in table[host]
        ]

    monkeypatch.setattr("knowledge_base.utils.socket.getaddrinfo", _getaddrinfo)
    return table
