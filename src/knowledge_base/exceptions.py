"""Domain exceptions for knowledge-base.

Raised by library functions instead of returning ``{"error": ...}`` dicts. A tool lets them through:
``routes.tool``'s call boundary reports each as an MCP tool error carrying its message (#598).
"""

from __future__ import annotations

__all__ = [
    "EmbeddingModelMismatchError",
    "ExtractionError",
    "KnowledgeBaseError",
    "NotFoundError",
    "ValidationError",
]


class KnowledgeBaseError(Exception):
    """Base exception for all knowledge-base domain errors."""


class NotFoundError(KnowledgeBaseError):
    """Requested resource does not exist."""


class ValidationError(KnowledgeBaseError):
    """Input parameter fails validation."""


class EmbeddingModelMismatchError(KnowledgeBaseError):
    """ADR-0015 §3: an embedding identity differs from the recorded one (ME: ``EmbeddingModelMismatch``)."""


class ExtractionError(KnowledgeBaseError):
    """LLM extraction or entity resolution failure."""

    def __init__(
        self,
        message: str,
        *,
        errors: list[dict] | None = None,
        raw: str | None = None,
    ) -> None:
        super().__init__(message)
        self.errors = errors
        self.raw = raw
