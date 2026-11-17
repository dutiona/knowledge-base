"""Shared utility functions used across multiple modules."""

from __future__ import annotations

import hashlib
import ipaddress
import os
import socket
import sqlite3
import struct
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpcore
import httpx

from knowledge_base.exceptions import ValidationError
from knowledge_base.settings import host_networks

__all__ = [
    "ELEMENT_INSERT_EXPR",
    "ELEMENT_QUERY_EXPR",
    "STOPWORDS",
    "TITLE_STOPWORDS",
    "DeadlineExceeded",
    "compute_file_hash",
    "content_hash",
    "deadline_left",
    "egress_client",
    "egress_deadline",
    "is_private_ip",
    "loopback_opt_in",
    "serialize_f32",
    "validate_base_url",
]


# ---------------------------------------------------------------------------
# SSRF protection — shared by web.py, llm.py, vision.py
# ---------------------------------------------------------------------------


_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def _refusal_message(host: str) -> str:
    return f"URL points to a private, reserved or this host's own address ({host}). Use a public IP or hostname."


def _is_public(ip: _IPAddress) -> bool:
    return ip.is_global


def _refused(ip: _IPAddress) -> bool:
    """The floor on one address: not global, or one of this host's own (``KNOWLEDGE_BASE_HOST_ADDRESSES``)."""
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not _is_public(ip) or any(ip in network for network in host_networks())


def _accepted_addresses(host: str) -> list[_IPAddress]:
    """Every address *host* resolves to, in resolver order, or ``ValidationError`` if any of them is refused.

    A literal answers for itself. All answers are checked, so a name with one private answer among public
    ones (a multi-homed host, a rebinding name such as ``127.0.0.1.nip.io``) is refused as a whole.
    """
    try:
        answers: list[_IPAddress] = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError):
            raise ValidationError(f"cannot resolve {host}") from None
        try:
            answers = list(dict.fromkeys(ipaddress.ip_address(info[4][0]) for info in infos))
        except ValueError:
            raise ValidationError(_refusal_message(host)) from None
    if not answers:
        raise ValidationError(f"cannot resolve {host}")
    if any(_refused(ip) for ip in answers):
        raise ValidationError(_refusal_message(host))
    return answers


def is_private_ip(hostname: str) -> bool:
    """Return True if *hostname* is, or resolves to, any address the SSRF floor refuses.

    The floor refuses every non-global address (private, loopback, link-local, ``100.64.0.0/10``, ULA,
    reserved, unspecified) and this host's own addresses. All of a name's answers are checked, and a
    failed lookup is refused too.
    """
    try:
        _accepted_addresses(hostname)
    except ValidationError:
        return True
    return False


def _is_allowed_loopback(host: str) -> bool:
    """Return True iff *host* is a loopback IP literal (127.0.0.0/8, ::1) or the
    exact RFC-6761 reserved label ``localhost``.

    Checked on the PARSED host, BEFORE any DNS resolution — never "resolved to
    loopback" (that is the DNS-rebind vector :func:`is_private_ip` defeats). A
    hostname such as ``127.0.0.1.nip.io`` is neither a loopback literal nor the
    exact label, so it falls through to :func:`is_private_ip` and is rejected.
    """
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_base_url(url: str, *, allow_loopback: bool = False, exempt_host: str | None = None) -> None:
    """Validate a base URL for scheme and SSRF safety.

    The non-loopback SSRF floor (private, link-local, metadata, DNS-rebinding) is
    **always** enforced. When *allow_loopback* is True, a loopback IP literal or the
    exact reserved label ``localhost`` is permitted — for local ``openai_compat``
    servers (ADR-0018 §4) — checked on the parsed host before any DNS resolution.
    Every other host (including names that merely *resolve* to loopback) still goes
    through :func:`is_private_ip`.

    *exempt_host* is the operator-declared embedding host (#569): a parsed host exactly
    equal to it (both lowercased, one trailing dot stripped; port and path ignored) is
    accepted before any DNS lookup. Only the embedding request path passes it; every
    tool-supplied URL keeps the floor.

    Raises :class:`~knowledge_base.exceptions.ValidationError` on failure.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValidationError(f"Invalid URL scheme: {parsed.scheme!r}. Use http or https.")
    if not parsed.hostname:
        raise ValidationError("URL must include a hostname.")
    if allow_loopback and _is_allowed_loopback(parsed.hostname):
        return  # opt-in satisfied — accept without DNS resolution
    if exempt_host is not None and parsed.hostname.rstrip(".").lower() == exempt_host.rstrip(".").lower():
        return  # the declared embedding endpoint (#569)
    if is_private_ip(parsed.hostname):
        raise ValidationError(_refusal_message(parsed.hostname))


def _pinned(request: httpx.Request, name: str, address: _IPAddress) -> httpx.Request:
    """A copy of *request* sent to *address*; ``Host`` and ``sni_hostname`` keep *name*."""
    return httpx.Request(
        request.method,
        request.url.copy_with(host=str(address)),
        headers=request.headers,
        content=request.read(),
        extensions={**request.extensions, "sni_hostname": name},
    )


class DeadlineExceeded(Exception):
    """The deadline of the enclosing :func:`egress_deadline` block passed."""


_deadline: ContextVar[float | None] = ContextVar("egress_deadline", default=None)


@contextmanager
def egress_deadline(seconds: float) -> Iterator[None]:
    """Bound every floored request made inside the block to *seconds* from now, at each socket operation (#588)."""
    token = _deadline.set(monotonic() + seconds)
    try:
        yield
    finally:
        _deadline.reset(token)


def deadline_left() -> float | None:
    """Seconds left before the enclosing deadline, None outside one; ``DeadlineExceeded`` once none are left."""
    at = _deadline.get()
    if at is None:
        return None
    left = at - monotonic()
    if left <= 0:
        raise DeadlineExceeded
    return left


def _bounded[T](call: Callable[[float | None], T], timeout: float | None) -> T:
    """Run *call* with *timeout*, or with the time left when the deadline is tighter: its timeout is the deadline."""
    left = deadline_left()
    if left is None or (timeout is not None and timeout <= left):
        return call(timeout)
    try:
        return call(left)
    except httpcore.TimeoutException as exc:
        raise DeadlineExceeded from exc


def _send(sock: socket.socket, data: memoryview, timeout: float | None) -> int:
    """One ``send``, with the error mapping of httpcore's ``SyncStream.write``."""
    try:
        sock.settimeout(timeout)
        return sock.send(data)
    except TimeoutError as exc:
        raise httpcore.WriteTimeout(exc) from exc
    except OSError as exc:
        raise httpcore.WriteError(exc) from exc


class _DeadlineStream(httpcore.NetworkStream):
    """A socket stream whose every read, write and TLS handshake ends by the deadline."""

    def __init__(self, stream: httpcore.NetworkStream) -> None:
        # write() sends on the stream's socket, which is right only while that socket carries the stream's TLS
        # (httpcore's SyncStream wraps an SSLSocket). Fail closed rather than ever send a request in plaintext.
        self._socket = stream.get_extra_info("socket")
        ssl_object = stream.get_extra_info("ssl_object")
        if self._socket is None or (
            ssl_object is not None and getattr(self._socket, "_sslobj", None) is not ssl_object
        ):
            stream.close()
            raise RuntimeError("httpcore's stream no longer sends through a socket carrying its TLS")
        self._stream = stream

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return _bounded(lambda t: self._stream.read(max_bytes, t), timeout)

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        # httpcore's own write loops send() under one timeout, so a slow reader could stretch it past the
        # deadline; here each send gets the time left when it starts.
        data = memoryview(buffer)
        while data:
            data = data[_bounded(partial(_send, self._socket, data), timeout) :]

    def close(self) -> None:
        self._stream.close()

    def start_tls(
        self, ssl_context: Any, server_hostname: str | None = None, timeout: float | None = None
    ) -> httpcore.NetworkStream:
        try:
            return _DeadlineStream(_bounded(lambda t: self._stream.start_tls(ssl_context, server_hostname, t), timeout))
        except DeadlineExceeded:
            self._stream.close()  # httpcore closes the TCP stream only on a connect error
            raise

    def get_extra_info(self, info: str) -> Any:
        return self._stream.get_extra_info(info)


class _DeadlineBackend(httpcore.NetworkBackend):
    """httpcore's sync backend with every connection, and every operation on it, bounded by the deadline."""

    def __init__(self) -> None:
        self._backend: httpcore.NetworkBackend = httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        return _DeadlineStream(
            _bounded(lambda t: self._backend.connect_tcp(host, port, t, local_address, socket_options), timeout)
        )

    def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: Iterable[Any] | None = None
    ) -> httpcore.NetworkStream:
        return _DeadlineStream(_bounded(lambda t: self._backend.connect_unix_socket(path, t, socket_options), timeout))

    def sleep(self, seconds: float) -> None:
        self._backend.sleep(seconds)


class _PinnedTransport(httpx.BaseTransport):
    """Floor every request at the transport and send it to an address the floor accepted (#572, ADR-0018 §4).

    Each request (a redirect hop included, since the client sends every hop through here) resolves its host
    once. It is refused unless every answer passes the floor, then sent to an accepted answer: the URL carries
    the address, while ``Host`` and ``sni_hostname`` keep the name for the server and for TLS verification.
    A second DNS answer therefore cannot swap the target. Wraps rather than subclasses ``HTTPTransport``.
    """

    def __init__(self, *, allow_loopback: bool) -> None:
        self._allow_loopback = allow_loopback
        # The inner pool is keyed by address: a kept-alive connection could carry one name's request over a
        # TLS session verified for another name on the same address, so no connection is ever reused.
        ssl_context = httpx.create_ssl_context()
        self._inner = httpx.HTTPTransport(verify=ssl_context)
        # httpx takes no network backend, so its pool is rebuilt on the deadline-aware one, with the values
        # httpx.Limits(max_keepalive_connections=0) gave. A release that stops building on _pool must fail here,
        # not leave the deadline silently unenforced.
        if not isinstance(getattr(self._inner, "_pool", None), httpcore.ConnectionPool):
            raise RuntimeError("httpx.HTTPTransport no longer has a _pool: the fetch deadline cannot be installed")
        self._inner._pool = httpcore.ConnectionPool(
            ssl_context=ssl_context,
            max_connections=None,
            max_keepalive_connections=0,
            keepalive_expiry=5.0,
            network_backend=_DeadlineBackend(),
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.scheme not in ("http", "https"):
            raise ValidationError(f"Invalid URL scheme: {request.url.scheme!r}. Use http or https.")
        # The IDNA 2008 form httpx connects to; a Unicode str would be IDNA 2003-encoded by ssl.
        name = request.url.raw_host.decode("ascii")
        deadline_left()  # a hop due after the deadline is neither resolved nor sent
        if self._allow_loopback and _is_allowed_loopback(name):
            return self._inner.handle_request(request)
        *earlier, final = _accepted_addresses(name)
        for address in earlier:
            deadline_left()
            try:
                return self._inner.handle_request(_pinned(request, name, address))
            except (httpx.ConnectError, httpx.ConnectTimeout):
                continue  # nothing was sent: try the next accepted answer
        deadline_left()
        return self._inner.handle_request(_pinned(request, name, final))

    def close(self) -> None:
        self._inner.close()


def egress_client(
    *,
    floor: bool,
    allow_loopback: bool = False,
    timeout: float | httpx.Timeout = 5.0,
    headers: dict[str, str] | None = None,
) -> httpx.Client:
    """An HTTP client for KB's outbound requests; ``floor`` states whether the URL is tool-supplied.

    ``floor=True`` (a URL a tool supplied or stored) floors and pins every request, redirect hops included.
    It takes no other client option on purpose: a client given a transport silently drops ``limits``,
    ``verify`` and the like, and ``proxy``/``mounts`` would add routes around the guard. Environment proxies
    are not used, since a proxy resolves the name itself. ``floor=False`` is a plain client for an endpoint
    the operator's environment declared.
    """
    if not floor:
        return httpx.Client(timeout=timeout, headers=headers)
    return httpx.Client(transport=_PinnedTransport(allow_loopback=allow_loopback), timeout=timeout, headers=headers)


def loopback_opt_in(conn: sqlite3.Connection) -> bool:
    """The shared ``allow_loopback_base_url`` opt-in (ADR-0018 §4) for every URL a tool stored."""
    row = conn.execute("SELECT value FROM config WHERE key = 'allow_loopback_base_url'").fetchone()
    return bool(row) and row["value"].strip().lower() == "true"


def _sanitize_url(url: str) -> str:
    """Strip query parameters and userinfo from a URL for safe logging.

    Shared by ``llm.py``/``embeddings.py``/``embed_swap.py`` so no api_key or
    userinfo reaches a log line (ADR-0018 §4). ``llm.py`` re-exports this name for
    back-compat.
    """
    try:
        parsed = urlparse(url)
        host = parsed.hostname or "unknown"
        try:
            port = f":{parsed.port}" if parsed.port else ""
        except ValueError:
            port = ""
        return urlunparse((parsed.scheme, f"{host}{port}", parsed.path, "", "", ""))
    except Exception:
        # Conservative fallback: strip everything that could contain credentials
        safe = url.split("://", 1)[-1] if "://" in url else url
        safe = safe.split("@")[-1]  # drop userinfo
        safe = safe.split("?")[0]  # drop query
        safe = safe.split("#")[0]  # drop fragment
        scheme = url.split("://", 1)[0] if "://" in url else "http"
        return f"{scheme}://{safe}"


def _resolve_api_key(raw: str | None) -> str | None:
    """Resolve an api_key spec to the actual secret at call time.

    ``env:VARNAME`` indirection reads ``os.environ[VARNAME]`` (None if unset); a
    plain string is returned as-is; ``None`` stays ``None``. The resolved secret is
    **never persisted** — only the raw spec (``env:VARNAME`` or inline) is stored in
    config (ADR-0018 §4).
    """
    if raw is None:
        return None
    if raw.startswith("env:"):
        return os.environ.get(raw[4:])
    return raw


def compute_file_hash(path: Path) -> str:
    """SHA-256 hex digest of file bytes."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8192), b""):
            h.update(block)
    return h.hexdigest()


def content_hash(text: str) -> str:
    """Truncated SHA-256 of text content, used for chunk deduplication."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def serialize_f32(vec: list[float]) -> bytes:
    """Serialize a float vector to bytes for sqlite-vec."""
    return struct.pack(f"{len(vec)}f", *vec)


# SQL expression templates for typed vector operations.
# Each wraps a float32 blob parameter (?) for the target element type.
# INSERT and QUERY are separate dicts because future quantization schemes
# (e.g. TurboQuant #344) may use different expressions for storage vs. lookup
# (asymmetric quantization: store with codebook, query with fast approximate).
ELEMENT_INSERT_EXPR: dict[str, str] = {
    "float32": "?",
    "int8": "vec_quantize_int8(vec_f32(?), 'unit')",
}

ELEMENT_QUERY_EXPR: dict[str, str] = {
    "float32": "?",
    "int8": "vec_quantize_int8(vec_f32(?), 'unit')",
}


# Common English stopwords — shared across keyword extraction and title matching.
# Not exhaustive: FTS5's porter stemmer handles morphological variants,
# and BM25 naturally down-weights high-frequency terms.
STOPWORDS: frozenset[str] = frozenset(
    [
        "a",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "am",
        "an",
        "and",
        "any",
        "are",
        "aren't",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can",
        "could",
        "did",
        "didn't",
        "do",
        "does",
        "doesn't",
        "doing",
        "don't",
        "down",
        "during",
        "each",
        "few",
        "for",
        "from",
        "further",
        "get",
        "got",
        "had",
        "has",
        "have",
        "having",
        "he",
        "her",
        "here",
        "hers",
        "herself",
        "him",
        "himself",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "itself",
        "just",
        "let",
        "me",
        "more",
        "most",
        "my",
        "myself",
        "no",
        "nor",
        "not",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "our",
        "ours",
        "ourselves",
        "out",
        "over",
        "own",
        "same",
        "she",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "theirs",
        "them",
        "themselves",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "yours",
        "yourself",
        "yourselves",
        "also",
        "use",
        "used",
        "using",
        "can't",
        "won't",
        "shall",
        "may",
        "might",
        "must",
        "need",
        "vs",
        "best",
        "better",
        "good",
        "well",
        "many",
        "much",
        "several",
        "won",
        "don",
        "doesn",
        "didn",
        "shouldn",
        "couldn",
        "wouldn",
        "isn",
        "aren",
        "hasn",
        "weren",
        "via",
        "based",
    ]
)

# Compact stopwords for title matching — only function words and prepositions.
# Intentionally smaller than STOPWORDS: common verbs and pronouns can be
# meaningful in paper titles (e.g., "All You Need", "Do We Really Need").
TITLE_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "and",
        "or",
        "is",
        "are",
        "was",
        "were",
        "be",
        "by",
        "with",
        "from",
        "as",
        "its",
        "this",
        "that",
        "not",
        "but",
        "no",
        "via",
        "using",
        "based",
    }
)
