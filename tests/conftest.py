"""Shared pytest fixtures for the knowledge-base test suite.

Database fixtures are opt-in by argument name — they do not change the
behavior of existing tests that build their own temp databases inline.
Three autouse guards apply to every test: the job-worker reset, the
data-dir redirect (#391) and the embedding declaration (#569).
"""

from __future__ import annotations

import ipaddress
import socket
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

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
    for name in (
        "EMBED_PROVIDER",
        "KNOWLEDGE_BASE_EMBED_API_KEY",
        "KNOWLEDGE_BASE_LLM_API_KEY",
        "KNOWLEDGE_BASE_HOST_ADDRESSES",
    ):
        monkeypatch.delenv(name, raising=False)


def logical_host(request: httpx.Request) -> str:
    """The host a request is for: its ``Host`` header in ASCII form, without the port or IPv6 brackets.

    A pinned request (#572) is sent to an address, so ``request.url.host`` is the connect target; the
    name it is for travels in ``Host``. A bare ``Host`` value is an authority, not a URL, hence the scheme.
    """
    header = request.headers.get("host")
    url = httpx.URL(f"http://{header}") if header else request.url
    return url.raw_host.decode("ascii")


class HttpRecorder(list):
    """Every httpx request a test made, in order; ``handlers`` answers by logical host."""

    def __init__(self) -> None:
        super().__init__()
        self.handlers: dict[str, Callable[[httpx.Request], httpx.Response]] = {}

    def hosts(self) -> list[str]:
        """The logical host of each request (what it was for)."""
        return [logical_host(request) for request in self]

    def targets(self) -> list[str]:
        """The connect target of each request: the pinned address, or the host of an unpinned one."""
        return [request.url.host for request in self]

    def urls(self) -> list[str]:
        """Each request's URL with its logical host restored."""
        return [str(request.url.copy_with(host=logical_host(request))) for request in self]


@pytest.fixture
def http_requests(monkeypatch) -> HttpRecorder:
    """Record every httpx request at the transport; answer registered hosts, refuse the rest.

    Patched below every ``httpx.get``/``post``/``Client`` call, so a zero-request assertion
    holds whichever call site would have fired, and nothing ever reaches the network.
    """
    recorder = HttpRecorder()

    def _handle(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        host = logical_host(request)
        handler = recorder.handlers.get(host)
        if handler is None:
            raise httpx.ConnectError(f"unexpected host {host}", request=request)
        return handler(request)

    async def _handle_async(_transport, request: httpx.Request) -> httpx.Response:
        return _handle(request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda _transport, request: _handle(request))
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _handle_async)
    return recorder


class FakeDns(dict):
    """``{host: [addresses]}``; a tuple of lists answers lookup n with list n, the last one repeating."""

    def __init__(self) -> None:
        super().__init__()
        self.lookups: list[str] = []


@pytest.fixture
def fake_dns(monkeypatch) -> FakeDns:
    """Answer ``getaddrinfo`` for the SSRF floor from a ``FakeDns`` table; fail closed otherwise."""
    import socket

    table = FakeDns()

    def _getaddrinfo(host, *_args, **_kwargs):
        index = table.lookups.count(host)
        table.lookups.append(host)
        if host not in table:
            raise socket.gaierror(f"fake_dns: unknown host {host}")
        answers = table[host]
        if isinstance(answers, tuple):
            answers = answers[min(index, len(answers) - 1)]
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 0)) for a in answers]

    monkeypatch.setattr("knowledge_base.utils.socket.getaddrinfo", _getaddrinfo)
    return table


_PUBLIC_TEST_NETS = tuple(
    ipaddress.ip_network(net) for net in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32")
)


@pytest.fixture
def public_net(monkeypatch) -> None:
    """Let the documentation ranges play the public internet for the SSRF floor.

    Python counts them as non-global, and the public repository allows no real address, so a test of an
    accepted (public) answer needs them. Every other address keeps the real rule.
    """
    from knowledge_base import utils

    real = utils._is_public

    def _is_public(ip) -> bool:
        return any(ip in net for net in _PUBLIC_TEST_NETS) or real(ip)

    monkeypatch.setattr(utils, "_is_public", _is_public)


@pytest.fixture
def operator_ollama(monkeypatch) -> str:
    """Declare an operator Ollama (``OLLAMA_HOST``) and return its logical host; the URL cache is reset.

    Auto-detection then lands on ``http://<host>:11434`` with no probing, so a test serves that host
    from ``http_requests.handlers`` instead of reaching a real or loopback Ollama.
    """
    monkeypatch.setenv("OLLAMA_HOST", "http://ollama.example.test:11434")
    monkeypatch.setattr("knowledge_base.embeddings._OLLAMA_URL", None)
    return "ollama.example.test"


def machine_token(machine: str) -> str:
    """A well-shaped ADR-0020 token for ``machine``, built at run time so no token literal is committed."""
    return "me_" + machine.rjust(32, "0")


def token_line(machine: str, token: str | None = None) -> str:
    """The token-hash file line for ``machine`` (its own token unless ``token`` is given)."""
    import hashlib

    return f"{machine} {hashlib.sha256((token or machine_token(machine)).encode()).hexdigest()}\n"


@pytest.fixture
def token_file(tmp_path):
    """A token-hash file admitting ``laptop`` and ``desktop`` (tokens from ``machine_token``)."""
    path = tmp_path / "token-hashes"
    path.write_text(token_line("laptop") + token_line("desktop"))
    return path


def embedder_error(status: int = 503) -> httpx.HTTPStatusError:
    """The error a real embedding endpoint raises: httpx puts the endpoint URL in its message."""
    from tests.declared_env import TEST_EMBED_URL

    request = httpx.Request("POST", f"{TEST_EMBED_URL}/v1/embeddings")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"Server error '{status}' for url '{request.url}'", request=request, response=response)


class StubEmbedder(list):
    """The texts an embedding stub received; while ``down`` it raises what a real endpoint raises."""

    down: int | None = None  # the HTTP status to fail with, or None


@pytest.fixture
def stub_embedder(monkeypatch) -> StubEmbedder:
    """Answer the query embedding ``search_index`` asks for; the fixture is the list of texts it received."""
    from tests.declared_env import TEST_EMBED_DIM

    texts = StubEmbedder()

    def _embed_single(text, *_args, **_kwargs):
        if texts.down is not None:
            raise embedder_error(texts.down)
        texts.append(text)
        return [0.1] * TEST_EMBED_DIM

    monkeypatch.setattr("knowledge_base.search.embed_single", _embed_single)
    return texts


@pytest.fixture
def stub_document_embedder(monkeypatch) -> StubEmbedder:
    """Answer the document embeddings ingest asks for; the fixture is the list of texts it received."""
    texts = StubEmbedder()

    def _embed(batch, *_args, expected_dim=None, **_kwargs):
        from tests.declared_env import TEST_EMBED_DIM

        if texts.down is not None:
            raise embedder_error(texts.down)
        texts.extend(batch)
        return [[0.1] * (expected_dim or TEST_EMBED_DIM) for _ in batch]

    monkeypatch.setattr("knowledge_base.ingest.embed", _embed)
    return texts


@pytest.fixture
def declared_db() -> None:
    """Run the #569 startup gate on the per-test database, as ``main()`` does before serving."""
    from knowledge_base.db import open_declared_db

    open_declared_db().close()


class KbHttp:
    """A KB HTTP server on an ephemeral loopback port, served exactly as ``knowledge-base --http`` serves it.

    One listening socket is held for the object's life and each server gets a duplicate, so a restart never
    leaves the port free. Never combine with ``http_requests``: it patches the transport this client uses.
    """

    def __init__(self, app_factory: Callable[[], Any]) -> None:
        self._app_factory = app_factory
        self._socket = socket.create_server(("127.0.0.1", 0))
        self.port: int = self._socket.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._server: Any = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        import uvicorn

        from knowledge_base.server import uvicorn_config

        server = uvicorn.Server(uvicorn_config(self._app_factory(), "127.0.0.1", self.port))
        errors: list[BaseException] = []

        def _run() -> None:
            try:
                server.run(sockets=[self._socket.dup()])
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive():
                raise RuntimeError("the KB HTTP server ended before it started") from (errors[0] if errors else None)
            if time.monotonic() > deadline:
                raise TimeoutError("the KB HTTP server did not start within 10 s")
            time.sleep(0.01)
        self._server, self._thread = server, thread

    def stop(self) -> None:
        if self._server is None or self._thread is None:
            return
        self._server.should_exit = True
        self._thread.join(10)
        if self._thread.is_alive():
            raise TimeoutError("the KB HTTP server did not stop within 10 s")
        self._server = self._thread = None

    def restart(self) -> None:
        self.stop()
        self.start()

    def close(self) -> None:
        self.stop()
        self._socket.close()

    def request(
        self,
        method: str,
        path: str = "/mcp",
        *,
        authorization: str | list[str] | None = None,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
    ) -> httpx.Response:
        """One request on a fresh connection; ``authorization`` is sent as that many headers."""
        values = [authorization] if isinstance(authorization, str) else authorization or []
        sent = [("Authorization", value) for value in values] + list((headers or {}).items())
        return httpx.request(method, self.url + path, headers=sent, content=content, timeout=10)

    def rpc(
        self, method: str, params: dict | None = None, *, machine: str | None = "laptop", headers=None
    ) -> httpx.Response:
        """POST one JSON-RPC request to ``/mcp`` as ``machine`` (``None`` sends no token)."""
        import json

        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()
        return self.request(
            "POST",
            authorization=f"Bearer {machine_token(machine)}" if machine else None,
            headers={"Accept": "application/json", "Content-Type": "application/json", **(headers or {})},
            content=body,
        )


@pytest.fixture
def kb_http(declared_db, token_file, stub_embedder, stub_document_embedder, monkeypatch) -> Iterator[KbHttp]:
    """KB served over HTTP behind the machine-token gate, with ``laptop`` and ``desktop`` admitted."""
    # --http refuses vision and the browser fallback, which the suite's declaration switches on.
    monkeypatch.setenv("KNOWLEDGE_BASE_VISION", "off")
    monkeypatch.setenv("KNOWLEDGE_BASE_BROWSER", "off")
    from knowledge_base.auth import TokenTable
    from knowledge_base.server import build_http_app

    server = KbHttp(lambda: build_http_app(TokenTable.load(token_file)))
    server.start()
    try:
        yield server
    finally:
        server.close()
