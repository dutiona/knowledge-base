"""Keyword intent extraction for search query preprocessing.

Extracts high-level intent keywords from natural language queries using a
lightweight RAKE-inspired algorithm. Strips stopwords and context-specific
filler to produce focused terms for FTS5 matching.

Based on insights from Agentic Plan Caching (Zhang et al., NeurIPS 2025):
keyword-based matching outperforms full-query semantic similarity for
identifying structurally similar queries (lower false positive AND false
negative rates at all thresholds).
"""

from __future__ import annotations

import re
from collections import Counter

from .utils import STOPWORDS as _STOPWORDS

__all__ = [
    "build_fts_query",
    "extract_keywords",
]

# Pattern: split on whitespace and strip surrounding punctuation, but keep
# internal hyphens and dots (e.g. "ResNet-50", "GPT-3.5").
# Uses \w to support Unicode letters (e.g. "naïve", "Gödel").
_TOKEN_RE = re.compile(r"[\w][\w\-\.]*[\w]|[\w]", re.UNICODE)


def extract_keywords(query: str, *, max_keywords: int = 5) -> list[str]:
    """Extract intent keywords from a search query.

    Returns up to ``max_keywords`` terms, ordered by relevance (frequency-
    weighted, compound terms with hyphens/dots scored higher). All terms
    are lowercased.

    Args:
        query: Natural language search query.
        max_keywords: Maximum number of keywords to return.
    """
    if not query or not query.strip():
        return []

    # Track standalone uppercase single-char words (language identifiers: C, R).
    # Strip surrounding punctuation so "C," or "(R)" still match.
    single_char_ids = {
        w.strip(".,;:!?()[]{}\"'").lower()
        for w in query.split()
        if len(w.strip(".,;:!?()[]{}\"'")) == 1 and w.strip(".,;:!?()[]{}\"'").isupper()
    }

    tokens = _TOKEN_RE.findall(query.lower())

    # Filter stopwords; keep single-char tokens only if they appeared as
    # standalone uppercase words in the original query.
    content_words = [
        t
        for t in tokens
        if t not in _STOPWORDS and (len(t) > 1 or t in single_char_ids)
    ]

    if not content_words:
        return []

    # Score: frequency count with bonus for compound terms (hyphens/dots).
    # Secondary sort: first occurrence position in query (earlier = higher priority).
    counts = Counter(content_words)
    first_pos = {}
    for i, w in enumerate(content_words):
        if w not in first_pos:
            first_pos[w] = i
    scored = [
        (word, count + (0.5 if "-" in word or "." in word else 0), first_pos[word])
        for word, count in counts.items()
    ]
    scored.sort(key=lambda x: (-x[1], x[2]))

    return [word for word, _, _ in scored[:max_keywords]]


# FTS5 reserved words that must not appear as bare terms in queries.
_FTS5_OPERATORS = frozenset({"and", "or", "not", "near"})


def build_fts_query(keywords: list[str]) -> str:
    """Convert keyword list to an FTS5 OR query.

    Terms containing special characters (hyphens, dots) are double-quoted.
    FTS5 operator words are excluded.

    Args:
        keywords: List of extracted keywords.

    Returns:
        FTS5 query string, or empty string if no valid terms.
    """
    if not keywords:
        return ""

    terms = []
    for kw in keywords:
        if kw in _FTS5_OPERATORS:
            continue
        # Quote terms with special chars to prevent FTS5 misinterpretation
        if "-" in kw or "." in kw:
            terms.append(f'"{kw}"')
        else:
            terms.append(kw)

    return " OR ".join(terms)
