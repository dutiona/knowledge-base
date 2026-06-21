"""Embedding provider abstraction.

Supports pluggable backends: Ollama (default), OpenAI, ONNX Runtime.
Auto-detects Ollama URL: OLLAMA_HOST env > WSL2 Windows host > localhost.
"""

from __future__ import annotations

import logging
import math
import os
import subprocess
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from onnxruntime import InferenceSession

import httpx

from .db import DEFAULT_EMBED_DIM

logger = logging.getLogger(__name__)


class ZeroNormError(ValueError):
    """Raised when a vector has zero norm and cannot be L2-normalized."""


_OLLAMA_URL: str | None = None


def _get_ollama_url() -> str:
    global _OLLAMA_URL
    if _OLLAMA_URL is not None:
        return _OLLAMA_URL

    # 1. Explicit env override
    env_host = os.environ.get("OLLAMA_HOST")
    if env_host:
        _OLLAMA_URL = env_host if env_host.startswith("http") else f"http://{env_host}"
        return _OLLAMA_URL

    # 2. WSL2: try Windows host via default gateway
    if os.environ.get("WSL_DISTRO_NAME"):
        try:
            result = subprocess.run(
                ["ip", "route", "show", "default"],  # noqa: S607  # trusted argv, no shell
                capture_output=True,
                text=True,
                timeout=5,
            )
            gateway = result.stdout.split()[2]
            url = f"http://{gateway}:11434"
            resp = httpx.get(f"{url}/api/tags", timeout=3)
            if resp.status_code == 200:
                _OLLAMA_URL = url
                return _OLLAMA_URL
        except Exception as err:
            logger.debug("WSL2 Ollama gateway probe failed, falling back to localhost: %s", err)

    # 3. Localhost (baremetal Linux or WSL2 fallback)
    _OLLAMA_URL = "http://localhost:11434"
    return _OLLAMA_URL


def _l2_normalize(vec: list[float]) -> list[float]:
    """L2-normalize a vector to unit length.

    Raises ZeroNormError if the vector has zero norm (all zeros),
    since zero vectors produce undefined cosine similarity.
    """
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        raise ZeroNormError("Zero-norm embedding vector — upstream model returned all zeros")
    return [x / norm for x in vec]


def _normalize_or_none(emb: list[float], provider_name: str) -> list[float] | None:
    """L2-normalize an embedding, returning None on zero norm."""
    try:
        return _l2_normalize(emb)
    except ZeroNormError:
        logger.warning(
            "%s returned zero-norm embedding — skipping vector storage",
            provider_name,
        )
        return None


def truncate_embedding(vec: list[float], target_dim: int) -> list[float]:
    """Truncate a Matryoshka embedding and L2 re-normalize.

    Matryoshka models produce embeddings where prefix subsets retain
    semantic meaning. After truncation, re-normalization is required
    because the truncated prefix is not unit-length.
    """
    truncated = vec[:target_dim]
    return _l2_normalize(truncated)


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Interface for embedding backends."""

    def embed(
        self,
        texts: list[str],
        model: str,
        expected_dim: int | None = None,
    ) -> list[list[float] | None]:
        """Embed a batch of texts. Returns L2-normalized vectors, or None for zero-norm."""
        ...


class OllamaProvider:
    """Embedding provider using Ollama's /api/embed endpoint."""

    def embed(
        self,
        texts: list[str],
        model: str = "bge-m3",
        expected_dim: int | None = None,
    ) -> list[list[float] | None]:
        dim = expected_dim if expected_dim is not None else DEFAULT_EMBED_DIM
        url = _get_ollama_url()
        results = []
        for i in range(0, len(texts), 32):
            batch = texts[i : i + 32]
            resp = httpx.post(
                f"{url}/api/embed",
                json={"model": model, "input": batch},
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
            embeddings = data["embeddings"]
            for emb in embeddings:
                if len(emb) != dim:
                    raise ValueError(f"Expected {dim} dims, got {len(emb)}")
                results.append(_normalize_or_none(emb, "Ollama"))
        return results


class OpenAIProvider:
    """Embedding provider using the OpenAI API.

    Requires OPENAI_API_KEY env var. Uses httpx directly to avoid
    hard dependency on the openai package.
    """

    def embed(
        self,
        texts: list[str],
        model: str = "text-embedding-3-large",
        expected_dim: int | None = None,
    ) -> list[list[float] | None]:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY environment variable required for OpenAI embeddings")
        results = []
        batch_size = 512  # OpenAI supports up to 2048, 512 balances throughput/memory
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            raw_embeddings = self._call_api(api_key, batch, model, expected_dim)
            for emb in raw_embeddings:
                if expected_dim is not None and len(emb) != expected_dim:
                    raise ValueError(f"Expected {expected_dim} dims, got {len(emb)}")
                results.append(_normalize_or_none(emb, "OpenAI"))
        return results

    def _call_api(
        self,
        api_key: str,
        texts: list[str],
        model: str,
        dimensions: int | None,
    ) -> list[list[float]]:
        body: dict = {"model": model, "input": texts}
        if dimensions is not None:
            body["dimensions"] = dimensions
        resp = httpx.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        data.sort(key=lambda x: x["index"])
        return [item["embedding"] for item in data]


class ONNXProvider:
    """Embedding provider using ONNX Runtime for local inference.

    Expects the model path in ONNX_EMBED_MODEL_PATH env var.
    The ONNX model must accept raw string inputs as its first input node
    (i.e., models exported with an embedded tokenizer, such as those
    produced by ``optimum-cli export onnx`` with SentenceTransformers).
    Standard HuggingFace ONNX exports that expect pre-tokenized
    ``input_ids``/``attention_mask`` tensors are NOT supported.
    """

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], InferenceSession] = {}

    def embed(
        self,
        texts: list[str],
        model: str = "bge-m3",
        expected_dim: int | None = None,
    ) -> list[list[float] | None]:
        import numpy as np

        session = self._get_session(model)
        results = []
        for i in range(0, len(texts), 32):
            batch = texts[i : i + 32]
            inputs = {session.get_inputs()[0].name: np.array(batch)}
            outputs = session.run(None, inputs)  # type: ignore[arg-type]
            embeddings: list[list[float]] = outputs[0].tolist()  # type: ignore[union-attr]
            for emb in embeddings:
                if expected_dim is not None and len(emb) != expected_dim:
                    raise ValueError(f"Expected {expected_dim} dims, got {len(emb)}")
                results.append(_normalize_or_none(emb, "ONNX"))
        return results

    def _get_session(self, model: str) -> InferenceSession:
        model_path = os.environ.get("ONNX_EMBED_MODEL_PATH", "")
        cache_key = (model, model_path)
        if cache_key not in self._sessions:
            try:
                import onnxruntime as ort
            except ImportError:
                raise ImportError(
                    "onnxruntime is required for ONNX embeddings. Install with: uv sync --group onnx"
                ) from None
            if not model_path:
                raise RuntimeError(
                    "ONNX_EMBED_MODEL_PATH environment variable required "
                    "for ONNX embeddings. Point it to the .onnx model file."
                )
            self._sessions[cache_key] = ort.InferenceSession(model_path)
        return self._sessions[cache_key]


_PROVIDERS: dict[str, type] = {
    "ollama": OllamaProvider,
    "openai": OpenAIProvider,
    "onnx": ONNXProvider,
}

_provider_cache: dict[str, EmbeddingProvider] = {}


def get_provider(name: str, *, allow_env_override: bool = True) -> EmbeddingProvider:
    """Get an embedding provider by name.

    When *allow_env_override* is True (the default), ``EMBED_PROVIDER``
    env-var takes precedence over *name*.  Callers that already resolved
    an explicit provider choice (e.g. ``re_embed(provider=...)``) should
    pass ``allow_env_override=False`` so the env-var cannot silently
    redirect to a different backend.
    """
    resolved = name
    if allow_env_override:
        resolved = os.environ.get("EMBED_PROVIDER", name)
    resolved = resolved.lower()
    if resolved in _provider_cache:
        return _provider_cache[resolved]
    cls = _PROVIDERS.get(resolved)
    if cls is None:
        raise ValueError(f"Unknown embedding provider '{resolved}'. Available: {', '.join(sorted(_PROVIDERS))}")
    instance = cls()
    _provider_cache[resolved] = instance
    return instance


# --- Module-level dispatch functions ---
# These are the primary API. All callers (ingest, search, embed_swap)
# import and call these. Provider dispatch happens here.
# Tests that @patch("knowledge_base.ingest.embed", _fake) replace
# the name in the caller's namespace, so mocks work unchanged.


def embed(
    texts: list[str],
    model: str = "bge-m3",
    expected_dim: int | None = None,
    *,
    _provider_name: str = "ollama",
) -> list[list[float] | None]:
    """Embed a batch of texts using the named provider."""
    provider = get_provider(_provider_name)
    return provider.embed(texts, model=model, expected_dim=expected_dim)


def embed_single(
    text: str,
    model: str = "bge-m3",
    expected_dim: int | None = None,
    *,
    _provider_name: str = "ollama",
) -> list[float] | None:
    """Embed a single text using the named provider. Returns None for zero-norm."""
    return embed([text], model=model, expected_dim=expected_dim, _provider_name=_provider_name)[0]
