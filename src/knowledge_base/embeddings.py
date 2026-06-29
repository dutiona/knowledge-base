"""Embedding provider abstraction.

Supports pluggable backends: Ollama (default), OpenAI, ONNX Runtime.
Auto-detects Ollama URL: OLLAMA_HOST env > WSL2 Windows host > localhost.
"""

from __future__ import annotations

import logging
import math
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from onnxruntime import InferenceSession

import httpx

from .db import DEFAULT_EMBED_DIM
from .utils import _resolve_api_key, _sanitize_url, validate_base_url

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


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """A resolved, hashable embedding-provider identity (ADR-0018 §2).

    The frozen tuple ``(family, base_url, api_key, allow_loopback)`` is the provider
    *cache key* — replacing the old name-keyed cache that made two ``openai_compat``
    configs (different ``base_url``) collide. ``api_key`` holds the **raw** config
    spec (inline value or ``env:VARNAME`` indirection, never a resolved secret); it is
    omitted from ``__repr__`` so no key reaches a log line.
    """

    family: str
    base_url: str | None = None
    api_key: str | None = None
    allow_loopback: bool = False

    def __repr__(self) -> str:
        bu = _sanitize_url(self.base_url) if self.base_url else None
        return f"ProviderConfig(family={self.family!r}, base_url={bu!r}, allow_loopback={self.allow_loopback})"


class OllamaProvider:
    """Embedding provider using Ollama's /api/embed endpoint.

    With no ``base_url`` the URL is auto-detected (``OLLAMA_HOST`` env > WSL2 Windows
    host > localhost). A configured ``base_url`` (e.g. a remote/LAN Ollama) overrides
    auto-detection. Ollama is localhost-trusted by family, so it is not SSRF-validated.
    """

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = base_url.rstrip("/") if base_url else None

    def embed(
        self,
        texts: list[str],
        model: str = "bge-m3",
        expected_dim: int | None = None,
    ) -> list[list[float] | None]:
        dim = expected_dim if expected_dim is not None else DEFAULT_EMBED_DIM
        url = self._base_url or _get_ollama_url()
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


class OpenAICompatProvider:
    """Embedding provider for ANY OpenAI-compatible ``/v1/embeddings`` endpoint.

    One class reaches OpenAI, vLLM, LM Studio, OpenRouter, Ollama-Cloud, HuggingFace
    TEI — selected purely by ``base_url`` (ADR-0018 §2), no per-vendor code. Uses
    ``httpx`` directly to avoid a hard dependency on the openai package.

    ``base_url`` is normalized FIRST (``rstrip('/').removesuffix('/v1')``) and that
    same normalized value is what ``validate_base_url`` checks and what the request
    targets — closing the suffix-strip smuggle gap (ADR-0018 §4/§5). ``api_key`` is
    the raw spec (inline or ``env:VARNAME``), resolved at call time, never logged.
    """

    def __init__(self, base_url: str | None, api_key: str | None = None, *, allow_loopback: bool = False) -> None:
        self._base_url = (base_url or "https://api.openai.com").rstrip("/").removesuffix("/v1")
        self._api_key = api_key  # raw spec; never logged
        self._allow_loopback = allow_loopback

    def __repr__(self) -> str:
        return f"OpenAICompatProvider(base_url={_sanitize_url(self._base_url)!r})"

    def embed(
        self,
        texts: list[str],
        model: str = "text-embedding-3-large",
        expected_dim: int | None = None,
    ) -> list[list[float] | None]:
        # Defense-in-depth on the embed path (AC4); the primary gate is config-write.
        validate_base_url(self._base_url, allow_loopback=self._allow_loopback)
        api_key = _resolve_api_key(self._api_key)
        results = []
        batch_size = 512  # OpenAI supports up to 2048, 512 balances throughput/memory
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            raw_embeddings = self._call_api(api_key, batch, model, expected_dim)
            for emb in raw_embeddings:
                if expected_dim is not None and len(emb) != expected_dim:
                    raise ValueError(f"Expected {expected_dim} dims, got {len(emb)}")
                results.append(_normalize_or_none(emb, "OpenAI-compat"))
        return results

    def _call_api(
        self,
        api_key: str | None,
        texts: list[str],
        model: str,
        dimensions: int | None,
    ) -> list[list[float]]:
        body: dict = {"model": model, "input": texts}
        if dimensions is not None:
            body["dimensions"] = dimensions
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        resp = httpx.post(
            f"{self._base_url}/v1/embeddings",
            headers=headers,
            json=body,
            timeout=120,
            follow_redirects=False,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        data.sort(key=lambda x: x["index"])
        return [item["embedding"] for item in data]


class OpenAIProvider(OpenAICompatProvider):
    """OpenAI literal-endpoint alias (back-compat).

    Degenerate case of :class:`OpenAICompatProvider` with ``base_url`` fixed to
    ``https://api.openai.com``. Unlike a generic ``openai_compat`` server (where a
    missing key means "no auth", valid for local backends), the OpenAI literal
    *requires* ``OPENAI_API_KEY`` — that contract is preserved here.
    """

    def __init__(self) -> None:
        super().__init__("https://api.openai.com", os.environ.get("OPENAI_API_KEY"))

    def embed(
        self,
        texts: list[str],
        model: str = "text-embedding-3-large",
        expected_dim: int | None = None,
    ) -> list[list[float] | None]:
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY environment variable required for OpenAI embeddings")
        return super().embed(texts, model=model, expected_dim=expected_dim)


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


# Bare-name factories (legacy / no-config path). ``openai_compat`` is intentionally
# absent here: it has no working default — it requires a configured ``base_url`` and
# must be constructed via the config path (``get_provider(cfg=ProviderConfig(...))``).
_PROVIDERS: dict[str, Callable[[], EmbeddingProvider]] = {
    "ollama": OllamaProvider,
    "openai": OpenAIProvider,
    "onnx": ONNXProvider,
}

# Cache keyed by EITHER a resolved family name (str, legacy path) OR a frozen
# ``ProviderConfig`` (config path). Keying the config path on the full identity tuple
# is the fix for the old name-collision bug where two ``openai_compat`` base_urls
# shared the single ``"openai_compat"`` slot (ADR-0018 §2).
_provider_cache: dict[str | ProviderConfig, EmbeddingProvider] = {}


def _build_provider(cfg: ProviderConfig) -> EmbeddingProvider:
    """Construct a provider from a resolved :class:`ProviderConfig`."""
    family = cfg.family.lower()
    if family == "ollama":
        return OllamaProvider(cfg.base_url)
    if family in ("openai_compat", "openai"):
        return OpenAICompatProvider(cfg.base_url, cfg.api_key, allow_loopback=cfg.allow_loopback)
    if family == "onnx":
        return ONNXProvider()
    raise ValueError(
        f"'{cfg.family}' is not an embedding provider "
        "(anthropic_compat is chat-only; valid: openai_compat, ollama, onnx)."
    )


def get_provider(
    name: str | None = None,
    *,
    cfg: ProviderConfig | None = None,
    allow_env_override: bool = True,
) -> EmbeddingProvider:
    """Get an embedding provider, by config (preferred) or by bare name (legacy).

    When *cfg* is given, the provider is built from the resolved identity tuple and
    cached by it — two ``openai_compat`` configs with different ``base_url`` never
    collide. When *cfg* is None, the legacy bare-name path applies: with
    *allow_env_override* True (default) ``EMBED_PROVIDER`` takes precedence over
    *name*; callers that already resolved an explicit choice pass
    ``allow_env_override=False`` so the env-var cannot silently redirect the backend.
    """
    if cfg is not None:
        cached = _provider_cache.get(cfg)
        if cached is not None:
            return cached
        instance = _build_provider(cfg)
        _provider_cache[cfg] = instance
        return instance

    resolved = name or "ollama"
    if allow_env_override:
        resolved = os.environ.get("EMBED_PROVIDER", resolved)
    resolved = resolved.lower()
    if resolved in _provider_cache:
        return _provider_cache[resolved]
    factory = _PROVIDERS.get(resolved)
    if factory is None:
        raise ValueError(f"Unknown embedding provider '{resolved}'. Available: {', '.join(sorted(_PROVIDERS))}")
    instance = factory()
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
    _provider_cfg: ProviderConfig | None = None,
) -> list[list[float] | None]:
    """Embed a batch of texts using the configured provider.

    Signature is intentionally stable (ADR-0018 §2): the new ``_provider_cfg`` is a
    purely-additive private kwarg, so existing callers and ``@patch(...embed)`` mocks
    keep working. When *_provider_cfg* is given it selects the provider (config path);
    otherwise the legacy *_provider_name* bare-name path applies.
    """
    provider = get_provider(cfg=_provider_cfg) if _provider_cfg is not None else get_provider(_provider_name)
    return provider.embed(texts, model=model, expected_dim=expected_dim)


def embed_single(
    text: str,
    model: str = "bge-m3",
    expected_dim: int | None = None,
    *,
    _provider_name: str = "ollama",
    _provider_cfg: ProviderConfig | None = None,
) -> list[float] | None:
    """Embed a single text using the configured provider. Returns None for zero-norm."""
    return embed(
        [text],
        model=model,
        expected_dim=expected_dim,
        _provider_name=_provider_name,
        _provider_cfg=_provider_cfg,
    )[0]
