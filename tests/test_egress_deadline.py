"""A floored request made under ``egress_deadline`` ends by the deadline, at every socket operation (#588).

The per-read timeout cannot carry the deadline: httpcore takes it once per response and reuses it for every
read of the headers and the body. The backend tests use a stub inner backend and a fake clock; two tests use a
real loopback socket, because the faked network replaces exactly the socket reads the deadline bounds.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import httpcore
import httpx
import pytest

from knowledge_base import utils
from knowledge_base.utils import DeadlineExceeded, egress_client, egress_deadline


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(utils, "monotonic", fake)
    return fake


class _StubStream(httpcore.NetworkStream):
    def __init__(self, calls: list[tuple[str, float | None]], fail: type[Exception] | None = None) -> None:
        self.calls = calls
        self.fail = fail

    def _record(self, name: str, timeout: float | None) -> None:
        self.calls.append((name, timeout))
        if self.fail is not None:
            raise self.fail("stub timeout")

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        self._record("read", timeout)
        return b""

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._record("write", timeout)

    def start_tls(self, ssl_context, server_hostname=None, timeout=None) -> httpcore.NetworkStream:
        self._record("start_tls", timeout)
        return self


class _StubBackend(httpcore.NetworkBackend):
    def __init__(self) -> None:
        self.calls: list[tuple[str, float | None]] = []
        self.fail: type[Exception] | None = None

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.calls.append(("connect", timeout))
        return _StubStream(self.calls, self.fail)


@pytest.fixture
def stub() -> tuple[utils._DeadlineBackend, _StubBackend]:
    backend = utils._DeadlineBackend()
    inner = _StubBackend()
    backend._backend = inner
    return backend, inner


def test_every_socket_operation_is_clipped_to_the_time_left(clock, stub):
    backend, inner = stub
    with egress_deadline(10):
        stream = backend.connect_tcp("203.0.113.1", 443, timeout=30)
        clock.now += 4
        tls = stream.start_tls(None, "page.example.test", timeout=30)
        tls.write(b"GET", timeout=None)
        tls.read(10, timeout=5)
    assert inner.calls == [("connect", 10), ("start_tls", 6), ("write", 6), ("read", 5)]


def test_outside_a_deadline_the_timeouts_are_unchanged(clock, stub):
    backend, inner = stub
    stream = backend.connect_tcp("203.0.113.1", 443, timeout=30)
    stream.read(10, timeout=None)
    assert inner.calls == [("connect", 30), ("read", None)]


def test_a_timeout_the_deadline_imposed_is_the_deadline(clock, stub):
    backend, inner = stub
    inner.fail = httpcore.ReadTimeout
    with egress_deadline(10):
        stream = backend.connect_tcp("203.0.113.1", 443, timeout=5)
        with pytest.raises(DeadlineExceeded):
            stream.read(10, timeout=30)
        with pytest.raises(httpcore.ReadTimeout):
            stream.read(10, timeout=5)


def test_a_passed_deadline_sends_nothing(clock, stub):
    backend, inner = stub
    with egress_deadline(10):
        clock.now += 10
        with pytest.raises(DeadlineExceeded):
            backend.connect_tcp("203.0.113.1", 443, timeout=30)
    assert inner.calls == []


def test_the_outer_deadline_comes_back_after_a_nested_one(clock):
    with egress_deadline(10):
        with egress_deadline(1):
            assert utils.deadline_left() == 1
        assert utils.deadline_left() == 10
    assert utils.deadline_left() is None


@pytest.fixture
def drip_server() -> Iterator[tuple[int, list[bytes]]]:
    """A loopback server that sends ``script`` (set by the test) one byte every 50 ms to each connection."""
    script: list[bytes] = []
    server = socket.create_server(("127.0.0.1", 0))
    server.settimeout(5)
    done = threading.Event()

    def _serve() -> None:
        try:
            conn, _ = server.accept()
        except OSError:
            return
        with conn:
            conn.recv(65536)
            for byte in b"".join(script):
                if done.wait(0.05):
                    return
                try:
                    conn.sendall(bytes([byte]))
                except OSError:
                    return

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    yield server.getsockname()[1], script
    done.set()
    server.close()
    thread.join(5)


def test_a_header_drip_ends_at_the_deadline(drip_server):
    port, script = drip_server
    script.append(b"HTTP/1.1 200 OK\r\n" + b"X-Drip: " + b"a" * 400 + b"\r\n\r\n")
    started = time.monotonic()
    with (
        egress_client(floor=True, allow_loopback=True, timeout=30.0) as client,
        egress_deadline(0.5),
        pytest.raises(DeadlineExceeded),
    ):
        client.get(f"http://127.0.0.1:{port}/")
    assert time.monotonic() - started < 1.5


def test_a_body_drip_ends_at_the_deadline(drip_server):
    port, script = drip_server
    script.append(b"HTTP/1.1 200 OK\r\nContent-Length: 400\r\n\r\n" + b"a" * 400)
    started = time.monotonic()
    with (
        egress_client(floor=True, allow_loopback=True, timeout=30.0) as client,
        egress_deadline(0.5),
        pytest.raises(DeadlineExceeded),
        client.stream("GET", f"http://127.0.0.1:{port}/") as response,
    ):
        for _chunk in response.iter_bytes():
            pass
    assert time.monotonic() - started < 1.5


def test_a_second_address_is_not_tried_after_the_deadline(clock, http_requests, fake_dns, public_net):
    fake_dns["two.example.test"] = ["203.0.113.1", "203.0.113.2"]

    def _slow_refusal(request: httpx.Request) -> httpx.Response:
        clock.now += 11
        raise httpx.ConnectTimeout("no answer", request=request)

    http_requests.handlers["two.example.test"] = _slow_refusal
    with egress_client(floor=True) as client, egress_deadline(10), pytest.raises(DeadlineExceeded):
        client.get("http://two.example.test/")
    assert http_requests.targets() == ["203.0.113.1"]


def test_a_hop_due_after_the_deadline_is_neither_resolved_nor_sent(clock, http_requests, fake_dns, public_net):
    fake_dns["page.example.test"] = ["203.0.113.1"]
    http_requests.handlers["page.example.test"] = lambda request: httpx.Response(200)
    with egress_client(floor=True) as client, egress_deadline(10):
        clock.now += 10
        with pytest.raises(DeadlineExceeded):
            client.get("http://page.example.test/")
    assert fake_dns.lookups == []
    assert http_requests == []
