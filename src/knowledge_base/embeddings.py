"""Embedding provider abstraction.

Pluggable backends by API family (ADR-0018): any OpenAI-compatible server, Ollama, ONNX
Runtime. Embedding always goes to the declared ``KNOWLEDGE_BASE_EMBED_URL`` (#569); the
Ollama URL auto-detection below serves only the LLM and vision defaults.
"""

from __future__ import annotations

import logging
import math
import os
import subprocess
import threading
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

if TYPE_CHECKING:
    from onnxruntime import InferenceSession

import httpx

from .exceptions import ValidationError
from .redact import link_extents
from .settings import VARIABLES, declared_embed_host
from .utils import _resolve_api_key, _sanitize_url, validate_base_url

logger = logging.getLogger(__name__)

#: KB's query instruction, verbatim from ADR-0015 §6; ``default_query_instruction`` gives it to Qwen3-Embedding spaces.
QUERY_INSTRUCTION = "Instruct: Given a search query, retrieve relevant document passages.\nQuery: "


def default_query_instruction(model: str) -> str:
    """A space's instruction when it records none (Michaël, #573): KB's pinned one for Qwen3-Embedding, else none."""
    return QUERY_INSTRUCTION if "qwen3-embedding" in model.lower() else ""


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


def truncate_embedding(vec: list[float], target_dim: int) -> list[float] | None:
    """Truncate a Matryoshka embedding and L2 re-normalize; None for a zero-norm prefix, as for a zero-norm vector.

    Matryoshka models produce embeddings where prefix subsets retain
    semantic meaning. After truncation, re-normalization is required
    because the truncated prefix is not unit-length.
    """
    return _normalize_or_none(vec[:target_dim], "Matryoshka truncation")


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

    The frozen tuple ``(family, base_url, api_key, allow_loopback, max_input_tokens, batch_size)`` is the provider
    *cache key*, so two ``openai_compat`` configs with different ``base_url`` never
    collide. ``api_key`` holds the **raw** config
    spec (inline value or ``env:VARNAME`` indirection, never a resolved secret); it is
    omitted from ``__repr__`` so no key reaches a log line.
    """

    family: str
    base_url: str | None = None
    api_key: str | None = None
    allow_loopback: bool = False
    max_input_tokens: int | None = None
    batch_size: int = 1

    def __repr__(self) -> str:
        bu = _sanitize_url(self.base_url) if self.base_url else None
        return (
            f"ProviderConfig(family={self.family!r}, base_url={bu!r}, allow_loopback={self.allow_loopback}, "
            f"max_input_tokens={self.max_input_tokens}, batch_size={self.batch_size})"
        )


class OllamaProvider:
    """Embedding provider using Ollama's /api/embed endpoint.

    Built only from the declared ``base_url`` (#569), never auto-detected. Ollama is
    localhost-trusted by family, so it is not SSRF-validated.
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def embed(
        self,
        texts: list[str],
        model: str,
        expected_dim: int | None = None,
    ) -> list[list[float] | None]:
        results = []
        for i in range(0, len(texts), 32):
            batch = texts[i : i + 32]
            resp = httpx.post(
                f"{self._base_url}/api/embed",
                json={"model": model, "input": batch},
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
            embeddings = data["embeddings"]
            for emb in embeddings:
                if expected_dim is not None and len(emb) != expected_dim:
                    raise ValueError(f"Expected {expected_dim} dims, got {len(emb)}")
                results.append(_normalize_or_none(emb, "Ollama"))
        return results


_LIMIT_VAR = VARIABLES["max_input_tokens"]

#: An embedding call's inputs cut to fit ``max_input_tokens``, by position: (tokens counted, tokens kept).
Cuts = dict[int, tuple[int, int]]


def _nothing_fits(limit: int) -> ValidationError:
    return ValidationError(f"{_LIMIT_VAR}: not even one character of an embedding input fits in {limit} tokens")


def _fit(text: str, limit: int, count: Callable[[str], int]) -> int:
    """The length of a prefix of *text* that counts at most *limit*: all of it when it fits, 0 when nothing does.

    It gallops from *limit* characters (doubling), then bisects. It only returns a length it counted: a count that
    does not grow with length can shorten a piece, never overfill one. The cut moves back to just after the last line
    end, else the last whitespace, when that keeps at least half of it, so a line shorter than half a piece (an image
    reference on its own line) is never cut (#604).
    """
    best, probe = 0, max(limit, 1)
    while True:
        probe = min(probe, len(text))
        if count(text[:probe]) > limit:
            break
        best = probe
        if probe == len(text):
            return best
        probe *= 2
    lo, hi = best + 1, probe - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if count(text[:mid]) <= limit:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    if not best:
        return 0
    half = range(best - 1, best // 2 - 1, -1)
    line_end = next((i + 1 for i in half if text[i] == "\n"), None)
    space = line_end or next((i + 1 for i in half if text[i].isspace()), best)
    return space if space == best or count(text[:space]) <= limit else best


#: How far past a cut the link scan reads to see whether the cut falls inside a link.
_LINK_LOOKAHEAD = 4096


def _cuts_outside_link(text: str, cut: int) -> list[int]:
    """Where to cut instead when *cut* falls inside a link, best first: before the link or after it.

    A link is not redacted (#592), but a cut fragment of one would be read as text. Cutting before the link is
    preferred while it keeps at least half the piece, so a piece is not a stray ``(``.
    """
    window = text[: cut + _LINK_LOOKAHEAD]
    for start, end in link_extents(window):
        if start < cut < end:
            after = [end] if end < len(window) or len(window) == len(text) else []
            before = [start] if start else []
            return before + after if start >= cut // 2 else after + before
    return []


def split_to_limit(text: str, limit: int, count: Callable[[str], int]) -> list[str]:
    """*text* as consecutive slices that each count at most *limit*; joined, they are *text* again.

    The pieces but the last aim at an even share of the content, each with the tokens the server adds to every input
    (``count("")``, its EOS), so the last one is not a stray word; the last aims at the limit to take what the cuts
    left over.
    """
    total = count(text)
    if total <= limit:
        return [text]
    overhead = count("")
    if limit <= overhead:
        raise _nothing_fits(limit)
    planned = math.ceil((total - overhead) / (limit - overhead))
    share = math.ceil((total - overhead) / planned) + overhead
    pieces = []
    while text:
        aim = share if len(pieces) < planned - 1 else limit
        cut = _fit(text, aim, count) or _fit(text, limit, count)
        if not cut:
            raise _nothing_fits(limit)
        # A count is not monotone, so a moved cut is counted again, as _fit does.
        cut = next((c for c in _cuts_outside_link(text, cut) if count(text[:c]) <= limit), cut)
        pieces.append(text[:cut])
        text = text[cut:]
    return pieces


class _Turns:
    """One holder at a time, the others served in arrival order (#614).

    ``threading.Lock`` does not define which waiter proceeds on release; the queue makes it arrival order by
    construction, so a waiter is never served after a later one, whatever the scheduler does.
    """

    def __init__(self) -> None:
        self._cv = threading.Condition()
        self._queue: deque[object] = deque()

    @contextmanager
    def turn(self) -> Iterator[None]:
        me = object()
        try:
            with self._cv:
                self._queue.append(me)
                self._cv.wait_for(lambda: self._queue[0] is me)
            yield
        finally:  # whatever raised, and wherever: the token leaves the queue, so it cannot block the next waiter
            with self._cv:
                if me in self._queue:
                    self._queue.remove(me)
                self._cv.notify_all()


#: One turn queue per endpoint: every provider of an embedder shares it, whatever else its configuration says.
_TURNS: dict[str, _Turns] = {}


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

    def __init__(
        self,
        base_url: str | None,
        api_key: str | None = None,
        *,
        allow_loopback: bool = False,
        max_input_tokens: int | None = None,
        batch_size: int = 1,
    ) -> None:
        if not base_url:
            raise ValidationError(
                "an openai_compat embedding provider needs a base URL (KNOWLEDGE_BASE_EMBED_URL); "
                "KB never defaults to https://api.openai.com"
            )
        self._base_url = base_url.rstrip("/").removesuffix("/v1")
        self._api_key = api_key  # raw spec; never logged
        self._allow_loopback = allow_loopback
        self._max_input_tokens = max_input_tokens
        self._batch_size = batch_size
        # FastMCP runs every sync tool in a thread pool, so concurrent tool calls would each put a request in flight.
        self._turns = _TURNS.setdefault(self._base_url, _Turns())
        # Kept alive for the per-input counts and the embedding requests: one input per request (#614) would
        # otherwise pay a new connection, and its TLS handshake, per input.
        self._http = httpx.Client(timeout=120, follow_redirects=False)

    def __repr__(self) -> str:
        return f"OpenAICompatProvider(base_url={_sanitize_url(self._base_url)!r})"

    def _headers(self) -> dict[str, str]:
        # The floor on every call; the declared endpoint alone is exempt (#569), so a
        # tailnet embedder works while any other private host is still refused.
        validate_base_url(self._base_url, allow_loopback=self._allow_loopback, exempt_host=declared_embed_host())
        api_key = _resolve_api_key(self._api_key)
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def token_counter(self) -> Callable[[str], int]:
        """Count a text's tokens as llama-server's embeddings call does: its ``/tokenize`` with ``add_special``.

        Both tokenize a string input with the same call (special tokens added and parsed), so the count
        includes the EOS the server appends (llama.cpp b12048, measured 2026-11-17 against the served model, #604).
        """
        headers = self._headers()
        body = {"add_special": True, "parse_special": True}

        def count(text: str) -> int:
            resp = self._http.post(f"{self._base_url}/tokenize", headers=headers, json={**body, "content": text})
            if resp.status_code not in (400, 404, 405, 422):  # what a server without llama-server's /tokenize answers
                resp.raise_for_status()
                try:
                    data = resp.json()
                except ValueError:  # an HTML fallback page, say
                    data = None
                tokens = data.get("tokens") if isinstance(data, dict) else None
                if isinstance(tokens, list):
                    return len(tokens)
            raise ValidationError(f"{_LIMIT_VAR} needs an embedder that counts tokens at /tokenize (llama-server)")

        return count

    def _bounded(self, texts: list[str]) -> tuple[list[str], Cuts]:
        limit = self._max_input_tokens
        if limit is None:
            return texts, {}
        count = self.token_counter()
        bounded, cuts = [], {}
        for i, text in enumerate(texts):
            tokens = count(text)
            if tokens > limit:
                cut = _fit(text, limit, count)
                if not cut:
                    raise _nothing_fits(limit)
                text = text[:cut]
                cuts[i] = (tokens, count(text))
                logger.info(
                    "an embedding input of %d tokens is truncated to %d tokens to fit %s=%d",
                    tokens,
                    cuts[i][1],
                    _LIMIT_VAR,
                    limit,
                )
            bounded.append(text)
        return bounded, cuts

    def embed(
        self,
        texts: list[str],
        model: str,
        expected_dim: int | None = None,
    ) -> list[list[float] | None]:
        return self._embed_with_cuts(texts, model, expected_dim)[0]

    def _embed_with_cuts(
        self, texts: list[str], model: str, expected_dim: int | None
    ) -> tuple[list[list[float] | None], Cuts]:
        texts, cuts = self._bounded(texts)
        results = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            headers = self._headers()  # the floor's check before every request, as it was when a call was one request
            with self._turns.turn():
                raw_embeddings = self._call_api(headers, batch, model, expected_dim)
            for emb in raw_embeddings:
                if expected_dim is not None and len(emb) != expected_dim:
                    raise ValueError(f"Expected {expected_dim} dims, got {len(emb)}")
                results.append(_normalize_or_none(emb, "OpenAI-compat"))
        return results, cuts

    def _call_api(
        self,
        headers: dict[str, str],
        texts: list[str],
        model: str,
        dimensions: int | None,
    ) -> list[list[float]]:
        body: dict = {"model": model, "input": texts}
        if dimensions is not None:
            body["dimensions"] = dimensions
        resp = self._http.post(f"{self._base_url}/v1/embeddings", headers=headers, json=body)
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
        model: str,
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


#: One provider per frozen ``ProviderConfig``: two ``openai_compat`` base URLs never share one (ADR-0018 §2).
_provider_cache: dict[ProviderConfig, EmbeddingProvider] = {}
#: Concurrent first calls build one provider, not one each.
_provider_lock = threading.Lock()


def _build_provider(cfg: ProviderConfig) -> EmbeddingProvider:
    """Construct a provider from a resolved :class:`ProviderConfig`."""
    family = cfg.family.lower()
    if family == "onnx":
        return ONNXProvider()
    if family not in ("ollama", "openai_compat", "openai", "llama.cpp"):
        raise ValueError(
            f"'{cfg.family}' is not an embedding provider "
            "(anthropic_compat is chat-only; valid: openai_compat, llama.cpp, ollama, onnx)."
        )
    if not cfg.base_url:
        raise ValidationError(
            f"the {family} embedding provider needs a base URL (KNOWLEDGE_BASE_EMBED_URL); "
            "KB has no default embedding endpoint"
        )
    if family == "ollama":
        return OllamaProvider(cfg.base_url)
    return OpenAICompatProvider(
        cfg.base_url,
        cfg.api_key,
        allow_loopback=cfg.allow_loopback,
        max_input_tokens=cfg.max_input_tokens,
        batch_size=cfg.batch_size,
    )


def get_provider(*, cfg: ProviderConfig) -> EmbeddingProvider:
    """The provider for *cfg*'s resolved identity, built once and cached by it."""
    with _provider_lock:
        cached = _provider_cache.get(cfg)
        if cached is None:
            cached = _provider_cache[cfg] = _build_provider(cfg)
        return cached


# --- Module-level dispatch functions ---
# These are the primary API. Callers (ingest, folder_summaries, search)
# import and call these. Provider dispatch happens here.
# Tests that @patch("knowledge_base.ingest.embed", _fake) replace
# the name in the caller's namespace, so mocks work unchanged.


def embed(
    texts: list[str],
    model: str,
    expected_dim: int | None = None,
    *,
    _provider_cfg: ProviderConfig,
) -> list[list[float] | None]:
    """Embed a batch of texts with the provider *_provider_cfg* selects."""
    return get_provider(cfg=_provider_cfg).embed(texts, model=model, expected_dim=expected_dim)


def embed_with_cuts(
    provider: EmbeddingProvider, texts: list[str], model: str, expected_dim: int | None = None
) -> tuple[list[list[float] | None], Cuts]:
    """*provider*'s vectors for *texts*, and the inputs it cut to fit (#620); only a counting provider cuts any."""
    if isinstance(provider, OpenAICompatProvider):
        return provider._embed_with_cuts(texts, model, expected_dim)
    return provider.embed(texts, model=model, expected_dim=expected_dim), {}


def split_inputs(texts: list[str], cfg: ProviderConfig) -> list[list[str]]:
    """Each text as the pieces that fit *cfg*'s ``max_input_tokens``; one piece per text when it has none (#604)."""
    limit = cfg.max_input_tokens
    if limit is None:
        return [[text] for text in texts]
    # Settings declare a limit only for an endpoint that counts, which _build_provider makes an OpenAICompatProvider.
    count = cast("OpenAICompatProvider", get_provider(cfg=cfg)).token_counter()
    return [split_to_limit(text, limit, count) for text in texts]


def embed_query(
    text: str,
    instruction: str,
    model: str,
    expected_dim: int | None = None,
    *,
    _provider_cfg: ProviderConfig,
) -> list[float] | None:
    """Embed a search query as its space's *instruction* followed by *text*; None for zero-norm.

    The only place a query input is built (ADR-0015 §6): document paths call ``embed``, which never adds one.
    """
    return embed([instruction + text], model=model, expected_dim=expected_dim, _provider_cfg=_provider_cfg)[0]
