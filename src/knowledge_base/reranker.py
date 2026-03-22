"""Reranker provider abstraction.

Supports pluggable backends for reranking search results.
Currently implements ONNX Runtime cross-encoder inference.
"""

from __future__ import annotations

import logging
import math
import os
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from onnxruntime import InferenceSession
    from tokenizers import Tokenizer

logger = logging.getLogger(__name__)


@runtime_checkable
class RerankerProvider(Protocol):
    """Interface for reranking backends."""

    def rerank(self, query: str, candidates: list[str]) -> list[float]:
        """Score each candidate against the query.

        Returns one float per candidate in [0, 1], higher = more relevant.
        """
        ...


class ONNXReranker:
    """Reranker using ONNX Runtime cross-encoder inference.

    Expects two environment variables:
      - ONNX_RERANK_MODEL_PATH: path to the cross-encoder .onnx file
      - ONNX_RERANK_TOKENIZER_PATH: path to the tokenizer directory
        (HuggingFace tokenizers JSON format)

    The model must accept ``input_ids`` and ``attention_mask`` tensors
    and produce a single logit per input pair (cross-encoder output).
    """

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], tuple[InferenceSession, Tokenizer]] = {}

    def rerank(self, query: str, candidates: list[str]) -> list[float]:
        import numpy as np

        if not candidates:
            return []

        session, tokenizer = self._get_session()

        # Tokenize all (query, candidate) pairs with padding/truncation.
        # encode_batch is faster than per-pair encode() calls.
        encodings = tokenizer.encode_batch(
            [(query, candidate) for candidate in candidates]
        )

        input_ids = np.array([enc.ids for enc in encodings], dtype=np.int64)
        attention_mask = np.array(
            [enc.attention_mask for enc in encodings], dtype=np.int64
        )

        # Build feed dict — include token_type_ids if model expects them
        feed: dict[str, Any] = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }
        input_names = {inp.name for inp in session.get_inputs()}
        if "token_type_ids" in input_names:
            feed["token_type_ids"] = np.array(
                [enc.type_ids for enc in encodings], dtype=np.int64
            )

        outputs = session.run(None, feed)

        # Cross-encoder output: logits of shape (batch, 1) or (batch,)
        logits: np.ndarray = outputs[0]  # type: ignore[assignment]
        if logits.ndim == 2:
            logits = logits[:, 0]

        # Sigmoid normalize to [0, 1]
        scores: list[float] = [_sigmoid(float(x)) for x in logits]
        return scores

    def _get_session(self) -> tuple[InferenceSession, Tokenizer]:
        model_path = os.environ.get("ONNX_RERANK_MODEL_PATH", "")
        tokenizer_path = os.environ.get("ONNX_RERANK_TOKENIZER_PATH", "")
        cache_key = (model_path, tokenizer_path)

        if cache_key not in self._sessions:
            try:
                import onnxruntime as ort
            except ImportError:
                raise ImportError(
                    "onnxruntime is required for ONNX reranking. "
                    "Install with: uv sync --group reranker"
                ) from None

            try:
                from tokenizers import Tokenizer
            except ImportError:
                raise ImportError(
                    "tokenizers is required for ONNX reranking. "
                    "Install with: uv sync --group reranker"
                ) from None

            if not model_path:
                raise RuntimeError(
                    "ONNX_RERANK_MODEL_PATH environment variable required "
                    "for ONNX reranking. Point it to the cross-encoder .onnx file."
                )
            if not tokenizer_path:
                raise RuntimeError(
                    "ONNX_RERANK_TOKENIZER_PATH environment variable required "
                    "for ONNX reranking. Point it to the tokenizer directory "
                    "(containing tokenizer.json)."
                )

            session = ort.InferenceSession(model_path)
            tokenizer_file = tokenizer_path
            if os.path.isdir(tokenizer_path):
                tokenizer_file = os.path.join(tokenizer_path, "tokenizer.json")
            tokenizer = Tokenizer.from_file(tokenizer_file)
            # Enable padding and truncation so batched encode produces
            # rectangular arrays (candidates have different lengths).
            tokenizer.enable_padding()
            tokenizer.enable_truncation(max_length=512)

            self._sessions[cache_key] = (session, tokenizer)

        return self._sessions[cache_key]


def _sigmoid(x: float) -> float:
    """Numerically stable sigmoid."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    exp_x = math.exp(x)
    return exp_x / (1.0 + exp_x)


# --- Provider registry ---

_RERANKER_PROVIDERS: dict[str, type] = {
    "onnx": ONNXReranker,
}

_reranker_cache: dict[str, RerankerProvider] = {}


def get_reranker(
    name: str = "onnx", *, allow_env_override: bool = True
) -> RerankerProvider:
    """Get a reranker provider by name.

    When *allow_env_override* is True (the default), ``RERANK_PROVIDER``
    env-var takes precedence over *name*.
    """
    resolved = name
    if allow_env_override:
        resolved = os.environ.get("RERANK_PROVIDER", name)
    resolved = resolved.lower()

    if resolved in _reranker_cache:
        return _reranker_cache[resolved]

    cls = _RERANKER_PROVIDERS.get(resolved)
    if cls is None:
        raise ValueError(
            f"Unknown reranker provider '{resolved}'. "
            f"Available: {', '.join(sorted(_RERANKER_PROVIDERS))}"
        )
    instance = cls()
    _reranker_cache[resolved] = instance
    return instance


# --- Module-level dispatch ---


def rerank(
    query: str,
    candidates: list[str],
    *,
    _provider_name: str = "onnx",
) -> list[float]:
    """Rerank candidates against a query using the named provider.

    Returns one score per candidate in [0, 1], higher = more relevant.
    """
    provider = get_reranker(_provider_name)
    return provider.rerank(query, candidates)
