"""Shared utility functions used across multiple modules."""

from __future__ import annotations

import hashlib
import ipaddress
import socket
import struct
from pathlib import Path
from urllib.parse import urlparse

from knowledge_base.exceptions import ValidationError

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


def is_private_ip(hostname: str) -> bool:
    """Return True if *hostname* resolves to a non-global IP.

    Uses ``not is_global`` to block private, loopback, link-local, multicast,
    reserved, and unspecified addresses.  Resolves hostnames to ALL IPs via
    getaddrinfo to catch DNS rebinding (e.g., 127.0.0.1.nip.io) and
    multi-homed hosts with mixed public/private addresses.
    """
    try:
        addr = ipaddress.ip_address(hostname)
        return not addr.is_global
    except ValueError:
        pass  # Not an IP literal — fall through to DNS resolution
    # Resolve hostname to ALL IPs — reject if any is non-global
    try:
        infos = socket.getaddrinfo(hostname, None)
        for _family, _type, _proto, _canonname, sockaddr in infos:
            addr = ipaddress.ip_address(sockaddr[0])
            if not addr.is_global:
                return True
        return False
    except (OSError, ValueError):
        return True  # Can't resolve → reject


def validate_base_url(url: str) -> None:
    """Validate a base URL for scheme and SSRF safety.

    Raises :class:`~knowledge_base.exceptions.ValidationError` on failure.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValidationError(
            f"Invalid URL scheme: {parsed.scheme!r}. Use http or https."
        )
    if not parsed.hostname:
        raise ValidationError("URL must include a hostname.")
    if is_private_ip(parsed.hostname):
        raise ValidationError(
            f"URL points to a private/reserved address ({parsed.hostname}). "
            "Use a public IP or hostname."
        )


def compute_file_hash(path: Path) -> str:
    """SHA-256 hex digest of file bytes."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
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
    "a about above after again against all am an and any are aren't as at be "
    "because been before being below between both but by can could did didn't "
    "do does doesn't doing don't down during each few for from further get got "
    "had has have having he her here hers herself him himself his how i if in "
    "into is it its itself just let me more most my myself no nor not of off on "
    "once only or other our ours ourselves out over own same she should so some "
    "such than that the their theirs them themselves then there these they this "
    "those through to too under until up very was we were what when where which "
    "while who whom why will with would you your yours yourself yourselves "
    "also use used using can't won't shall may might must need vs "
    "best better good well many much several "
    "won don doesn didn shouldn couldn wouldn isn aren hasn weren "
    "via based".split()
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
