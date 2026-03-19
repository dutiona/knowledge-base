"""Embedding provider abstraction.

Supports pluggable backends: Ollama (default), OpenAI, ONNX Runtime.
Auto-detects Ollama URL: OLLAMA_HOST env > WSL2 Windows host > localhost.
"""

from __future__ import annotations

import logging
import math
import os
import subprocess
from typing import Protocol, runtime_checkable

import httpx

from .db import DEFAULT_EMBED_DIM

logger = logging.getLogger(__name__)

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
                ["ip", "route", "show", "default"],
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
        except Exception:
            pass

    # 3. Localhost (baremetal Linux or WSL2 fallback)
    _OLLAMA_URL = "http://localhost:11434"
    return _OLLAMA_URL


def _l2_normalize(vec: list[float]) -> list[float]:
    """L2-normalize a vector to unit length."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        logger.warning("Zero-norm embedding vector — upstream model returned all zeros")
        return vec
    return [x / norm for x in vec]


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Interface for embedding backends."""

    def embed(
        self,
        texts: list[str],
        model: str,
        expected_dim: int | None = None,
    ) -> list[list[float]]:
        """Embed a batch of texts. Returns L2-normalized vectors."""
        ...


class OllamaProvider:
    """Embedding provider using Ollama's /api/embed endpoint."""

    def embed(
        self,
        texts: list[str],
        model: str = "bge-m3",
        expected_dim: int | None = None,
    ) -> list[list[float]]:
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
                results.append(_l2_normalize(emb))
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
    ) -> list[list[float]]:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY environment variable required for OpenAI embeddings"
            )
        results = []
        for i in range(0, len(texts), 32):
            batch = texts[i : i + 32]
            raw_embeddings = self._call_api(api_key, batch, model, expected_dim)
            for emb in raw_embeddings:
                if expected_dim is not None and len(emb) != expected_dim:
                    raise ValueError(f"Expected {expected_dim} dims, got {len(emb)}")
                results.append(_l2_normalize(emb))
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
    The ONNX model must accept string inputs (e.g., exported with
    SentenceTransformers optimum).
    """

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], object] = {}

    def embed(
        self,
        texts: list[str],
        model: str = "bge-m3",
        expected_dim: int | None = None,
    ) -> list[list[float]]:
        session = self._get_session(model)
        results = []
        for i in range(0, len(texts), 32):
            batch = texts[i : i + 32]
            import numpy as np

            inputs = {session.get_inputs()[0].name: np.array(batch)}
            outputs = session.run(None, inputs)
            embeddings = outputs[0].tolist()
            for emb in embeddings:
                if expected_dim is not None and len(emb) != expected_dim:
                    raise ValueError(f"Expected {expected_dim} dims, got {len(emb)}")
                results.append(_l2_normalize(emb))
        return results

    def _get_session(self, model: str) -> object:
        model_path = os.environ.get("ONNX_EMBED_MODEL_PATH", "")
        cache_key = (model, model_path)
        if cache_key not in self._sessions:
            try:
                import onnxruntime as ort
            except ImportError:
                raise ImportError(
                    "onnxruntime is required for ONNX embeddings. "
                    "Install with: uv sync --group onnx"
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


def get_provider(name: str) -> EmbeddingProvider:
    """Get an embedding provider by name.

    Supports env-var override: EMBED_PROVIDER takes precedence.
    """
    resolved = os.environ.get("EMBED_PROVIDER", name).lower()
    cls = _PROVIDERS.get(resolved)
    if cls is None:
        raise ValueError(
            f"Unknown embedding provider '{resolved}'. "
            f"Available: {', '.join(sorted(_PROVIDERS))}"
        )
    return cls()


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
) -> list[list[float]]:
    """Embed a batch of texts using the named provider."""
    provider = get_provider(_provider_name)
    return provider.embed(texts, model=model, expected_dim=expected_dim)


def embed_single(
    text: str,
    model: str = "bge-m3",
    *,
    _provider_name: str = "ollama",
) -> list[float]:
    """Embed a single text using the named provider."""
    return embed([text], model=model, _provider_name=_provider_name)[0]
