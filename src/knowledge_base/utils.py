"""Shared utility functions used across multiple modules."""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

__all__ = [
    "STOPWORDS",
    "TITLE_STOPWORDS",
    "compute_file_hash",
    "content_hash",
    "serialize_f32",
]


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
