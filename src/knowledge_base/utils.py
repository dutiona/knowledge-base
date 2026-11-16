"""Shared utility functions used across multiple modules."""

from __future__ import annotations

import hashlib
import ipaddress
import os
import socket
import struct
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from knowledge_base.exceptions import ValidationError
from knowledge_base.settings import host_networks

__all__ = [
    "ELEMENT_INSERT_EXPR",
    "ELEMENT_QUERY_EXPR",
    "STOPWORDS",
    "TITLE_STOPWORDS",
    "VALID_ELEMENT_TYPES",
    "compute_file_hash",
    "content_hash",
    "is_private_ip",
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

VALID_ELEMENT_TYPES: frozenset[str] = frozenset(ELEMENT_INSERT_EXPR.keys())


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
