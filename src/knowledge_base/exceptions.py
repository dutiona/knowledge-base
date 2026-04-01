"""Domain exceptions for knowledge-base.

Raised by library functions instead of returning ``{"error": ...}`` dicts.
Caught at the server.py MCP boundary and serialized to JSON for the client.
"""

from __future__ import annotations

__all__ = [
    "ExtractionError",
    "KnowledgeBaseError",
    "NotFoundError",
    "ValidationError",
]


class KnowledgeBaseError(Exception):
    """Base exception for all knowledge-base domain errors.

    Optional ``details`` dict carries structured data that the server.py
    boundary merges into the JSON error response for backward compatibility.
    """

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.details: dict = details or {}


class NotFoundError(KnowledgeBaseError):
    """Requested resource does not exist."""


class ValidationError(KnowledgeBaseError):
    """Input parameter fails validation."""


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
