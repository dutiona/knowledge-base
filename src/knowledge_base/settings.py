"""The operator's embedding declaration, egress switches, host addresses and fetch limits, read from the environment.

#569 declared the embedder and the switches, #572 the host addresses, #588 the fetch limits.

The environment is authoritative for the embedding configuration: KB starts only with an
explicit declaration, and the database records it (``db.bind_embed_config``). Every reader
looks at ``os.environ`` at call time. This module imports nothing from the package but its
exceptions, so any module may depend on it.
"""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .exceptions import EmbeddingModelMismatchError, ValidationError

__all__ = [
    "API_KEY_VAR",
    "BROWSER_VAR",
    "EMBED_PROVIDERS",
    "FETCH_DEADLINE_VAR",
    "FETCH_MAX_BYTES_VAR",
    "FETCH_MAX_REDIRECTS_VAR",
    "HOST_ADDRESSES_VAR",
    "IDENTITY_FIELDS",
    "LLM_API_KEY_VAR",
    "LLM_VAR",
    "VARIABLES",
    "VISION_VAR",
    "EmbedSettings",
    "FetchLimits",
    "check_environment",
    "declared_embed_host",
    "declared_identity",
    "fetch_limits",
    "host_networks",
    "identity_mismatch",
    "load_embed_settings",
    "normalize_embed_url",
    "require_identity",
    "routes_by_model",
    "serving_identity",
    "switch_on",
]

#: Embedding field -> the variable that declares it. ``element_type`` has none: KB declares float32 (ADR-0015 §1
#: reserves int8, ADR-0017 §7 allows it only after whitening).
VARIABLES = {
    "provider": "KNOWLEDGE_BASE_EMBED_PROVIDER",
    "model": "KNOWLEDGE_BASE_EMBED_MODEL",
    "dim": "KNOWLEDGE_BASE_EMBED_DIM",
    "base_url": "KNOWLEDGE_BASE_EMBED_URL",
    "matryoshka_base_dim": "KNOWLEDGE_BASE_EMBED_MATRYOSHKA_BASE_DIM",
    "artifact_sha256": "KNOWLEDGE_BASE_EMBED_ARTIFACT_SHA256",
    "pooling": "KNOWLEDGE_BASE_EMBED_POOLING",
    "normalization": "KNOWLEDGE_BASE_EMBED_NORMALIZATION",
    "max_input_tokens": "KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS",
}
#: ADR-0015 §1's identity tuple, names and order normative; the last four are the served artifact.
IDENTITY_FIELDS = (
    "model",
    "provider",
    "dim",
    "matryoshka_base_dim",
    "element_type",
    "artifact_sha256",
    "pooling",
    "normalization",
    "max_input_tokens",
)
_SERVED_FIELDS = IDENTITY_FIELDS[5:]
API_KEY_VAR = "KNOWLEDGE_BASE_EMBED_API_KEY"
LLM_API_KEY_VAR = "KNOWLEDGE_BASE_LLM_API_KEY"
VISION_VAR = "KNOWLEDGE_BASE_VISION"
BROWSER_VAR = "KNOWLEDGE_BASE_BROWSER"
LLM_VAR = "KNOWLEDGE_BASE_LLM"
HOST_ADDRESSES_VAR = "KNOWLEDGE_BASE_HOST_ADDRESSES"
FETCH_MAX_BYTES_VAR = "KNOWLEDGE_BASE_FETCH_MAX_BYTES"
FETCH_MAX_REDIRECTS_VAR = "KNOWLEDGE_BASE_FETCH_MAX_REDIRECTS"
FETCH_DEADLINE_VAR = "KNOWLEDGE_BASE_FETCH_DEADLINE"
_ONNX_MODEL_VAR = "ONNX_EMBED_MODEL_PATH"
_DEPRECATED_VAR = "EMBED_PROVIDER"

EMBED_PROVIDERS = ("openai_compat", "openai", "ollama", "onnx")
_OPENAI_FAMILIES = ("openai_compat", "openai")
_DIM_RE = re.compile(r"[1-9][0-9]*")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
#: The only endpoint besides Ollama known to serve the model a request names (Decision 4 of the #573 plan).
_OPENAI_ORIGIN = "https://api.openai.com"


@dataclass(frozen=True, slots=True)
class EmbedSettings:
    """A parsed declaration. ``api_key_source`` is the ``env:`` spec, never the secret."""

    provider: str
    model: str
    dim: int
    base_url: str | None
    api_key_source: str | None = None
    matryoshka_base_dim: int | None = None
    element_type: str = "float32"
    artifact_sha256: str | None = None
    pooling: str | None = None
    normalization: str | None = None
    max_input_tokens: int | None = None


def _get(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def normalize_embed_url(provider: str, url: str) -> str:
    """The stored form of a base URL: the pre-#569 ``configure_embeddings`` rule, so legacy rows compare equal."""
    url = url.rstrip("/")
    return url.removesuffix("/v1") if provider in _OPENAI_FAMILIES else url


def _parse_url(provider: str, raw: str) -> str:
    var = VARIABLES["base_url"]
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValidationError(f"{var} must be an http or https URL with a host (got scheme {parsed.scheme!r})")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValidationError(f"{var} must not carry userinfo, a query or a fragment; put the key in {API_KEY_VAR}")
    return normalize_embed_url(provider, raw)


def _positive_int(field: str) -> int | None:
    raw = _get(VARIABLES[field])
    if raw is None:
        return None
    if not _DIM_RE.fullmatch(raw) or not raw.isascii():
        raise ValidationError(f"{VARIABLES[field]} must be a positive integer, got {raw!r}")
    return int(raw)


def _served_artifact() -> dict[str, str | int | None]:
    """ADR-0015's served artifact: all four declared or none (ME ``ServedArtifact::from_parts``)."""
    declared = {field: _get(VARIABLES[field]) for field in _SERVED_FIELDS}
    missing = [VARIABLES[field] for field, value in declared.items() if value is None]
    if not missing:
        sha = declared["artifact_sha256"]
        if not _SHA256_RE.fullmatch(sha or ""):
            raise ValidationError(f"{VARIABLES['artifact_sha256']} must be 64 lowercase hex digits, got {sha!r}")
        return {**declared, "max_input_tokens": _positive_int("max_input_tokens")}
    if len(missing) < len(_SERVED_FIELDS):
        every = ", ".join(VARIABLES[field] for field in _SERVED_FIELDS)
        raise ValidationError(f"{', '.join(missing)} not set: the served artifact is all of {every} or none")
    return dict.fromkeys(_SERVED_FIELDS)


def load_embed_settings() -> EmbedSettings:
    """Parse the declaration; the first failing rule raises, its message starting with the variable."""
    if _get(_DEPRECATED_VAR):
        raise ValidationError(f"{_DEPRECATED_VAR} is no longer read; use {VARIABLES['provider']}")
    provider = _get(VARIABLES["provider"])
    if provider is None:
        raise ValidationError(f"{VARIABLES['provider']} is not set; KB starts only with an explicit embedding endpoint")
    if provider not in EMBED_PROVIDERS:
        raise ValidationError(f"{VARIABLES['provider']} must be one of {', '.join(EMBED_PROVIDERS)}, got {provider!r}")
    model = _get(VARIABLES["model"])
    if model is None:
        raise ValidationError(f"{VARIABLES['model']} is not set")
    dim_raw = _get(VARIABLES["dim"])
    if dim_raw is None:
        raise ValidationError(f"{VARIABLES['dim']} is not set")
    if not _DIM_RE.fullmatch(dim_raw) or not dim_raw.isascii():
        raise ValidationError(f"{VARIABLES['dim']} must be a positive integer, got {dim_raw!r}")
    url_raw = _get(VARIABLES["base_url"])
    if provider == "onnx":
        if url_raw is not None:
            raise ValidationError(f"{VARIABLES['base_url']} must be unset for onnx, which embeds locally")
        model_path = _get(_ONNX_MODEL_VAR)
        if model_path is None or not Path(model_path).is_file():
            raise ValidationError(f"{_ONNX_MODEL_VAR} must name an existing model file for the onnx provider")
        base_url = None
    else:
        if url_raw is None:
            raise ValidationError(
                f"{VARIABLES['base_url']} is not set; the {provider} provider has no default endpoint"
            )
        base_url = _parse_url(provider, url_raw)
    dim = int(dim_raw)
    base_dim = _positive_int("matryoshka_base_dim")
    if base_dim is not None and base_dim <= dim:
        raise ValidationError(
            f"{VARIABLES['matryoshka_base_dim']} must be greater than {VARIABLES['dim']} ({dim}), got {base_dim}"
        )
    return EmbedSettings(
        provider=provider,
        model=model,
        dim=dim,
        base_url=base_url,
        api_key_source=f"env:{API_KEY_VAR}" if _get(API_KEY_VAR) else None,
        matryoshka_base_dim=base_dim,
        **_served_artifact(),  # pyright: ignore[reportArgumentType]  # typed per field above
    )


def declared_identity(declared: EmbedSettings) -> dict[str, object]:
    """The declaration's ADR-0015 tuple, in §1 order: the store's own identity."""
    return {field: getattr(declared, field) for field in IDENTITY_FIELDS}


def routes_by_model(declared: EmbedSettings) -> bool:
    """Whether the declared endpoint serves the model a request names, so a space of another model can be served.

    Only Ollama, and OpenAI at exactly its canonical origin, are known to; ``llama.cpp`` serves one file, ``onnx``
    loads one, and an ``openai_compat`` (or another ``openai``) backend may ignore the name. A served artifact pins
    its model. Anything else fails closed.
    """
    if declared.artifact_sha256 is not None:
        return False
    return declared.provider == "ollama" or (declared.provider == "openai" and declared.base_url == _OPENAI_ORIGIN)


def serving_identity(declared: EmbedSettings, space: Mapping[str, object]) -> dict[str, object]:
    """The identity the declared endpoint produces for *space*: its own shape, the endpoint's own fields."""
    identity = declared_identity(declared)
    identity["dim"] = space["dim"]
    identity["matryoshka_base_dim"] = space["matryoshka_base_dim"]
    if routes_by_model(declared):
        identity["model"] = space["model"]
    return identity


def identity_mismatch(recorded: Mapping[str, object], expected: Mapping[str, object]) -> list[str]:
    """The ADR-0015 fields where *recorded* differs from *expected*, in §1 order; absent equals only absent."""
    return [field for field in IDENTITY_FIELDS if recorded[field] != expected[field]]


def require_identity(
    recorded: Mapping[str, object], expected: Mapping[str, object], *, subject: str, remedy: str
) -> None:
    """Refuse (ADR-0015 §3) unless *recorded* equals *expected* field by field, naming each difference."""
    fields = identity_mismatch(recorded, expected)
    if fields:
        clauses = ", ".join(
            f"{field} {recorded[field]!r} ({VARIABLES.get(field, 'KB')} declares {expected[field]!r})"
            for field in fields
        )
        raise EmbeddingModelMismatchError(f"{subject} records {clauses}; {remedy}")


def declared_embed_host() -> str | None:
    """The declared endpoint's host, lowercased without a trailing dot; None when undeclared."""
    url = _get(VARIABLES["base_url"])
    host = urlparse(url).hostname if url else None
    return host.rstrip(".").lower() if host else None


def switch_on(var: str) -> bool:
    """An egress switch: unset or ``off`` is off, ``on`` is on, anything else is refused."""
    value = _get(var)
    if value in (None, "off"):
        return False
    if value == "on":
        return True
    raise ValidationError(f"{var} must be on or off, got {value!r}")


def host_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """This host's own addresses or networks, which the SSRF floor refuses though they are public (#572).

    Comma-separated, strict: a name, an empty entry or host bits set is refused rather than silently matching
    nothing. An IPv4-mapped entry is refused too, because the floor judges mapped answers as their IPv4 form.
    """
    value = _get(HOST_ADDRESSES_VAR)
    if value is None:
        return ()
    networks = []
    for entry in (part.strip() for part in value.split(",")):
        try:
            network = ipaddress.ip_network(entry, strict=True)
        except ValueError:
            raise ValidationError(
                f"{HOST_ADDRESSES_VAR} must list IP addresses or networks separated by commas, got {entry!r}"
            ) from None
        if isinstance(network, ipaddress.IPv6Network) and network.network_address.ipv4_mapped is not None:
            raise ValidationError(f"{HOST_ADDRESSES_VAR}: write the IPv4-mapped entry {entry!r} in its IPv4 form")
        networks.append(network)
    return tuple(networks)


@dataclass(frozen=True, slots=True)
class FetchLimits:
    """What one fetch of a tool-supplied URL may take: body bytes, redirects followed, and seconds in all."""

    max_bytes: int
    max_redirects: int
    deadline: int


def _count(var: str, default: int, *, minimum: int) -> int:
    raw = _get(var)
    if raw is None:
        return default
    if not (raw.isascii() and raw.isdecimal()) or int(raw) < minimum:
        raise ValidationError(f"{var} must be a whole number of at least {minimum}, got {raw!r}")
    return int(raw)


def fetch_limits() -> FetchLimits:
    """The URL-fetch limits (#588); unset or empty means the default."""
    return FetchLimits(
        max_bytes=_count(FETCH_MAX_BYTES_VAR, 50 * 1024 * 1024, minimum=1),
        max_redirects=_count(FETCH_MAX_REDIRECTS_VAR, 5, minimum=0),
        deadline=_count(FETCH_DEADLINE_VAR, 60, minimum=1),
    )


def check_environment() -> EmbedSettings:
    """The whole startup check: the declaration, the switches, the host addresses and the fetch limits.

    Touches nothing on disk.
    """
    settings = load_embed_settings()
    for var in (VISION_VAR, BROWSER_VAR, LLM_VAR):
        switch_on(var)
    host_networks()
    fetch_limits()
    return settings
