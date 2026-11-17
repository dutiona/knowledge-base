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
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .exceptions import ValidationError

__all__ = [
    "API_KEY_VAR",
    "BROWSER_VAR",
    "EMBED_PROVIDERS",
    "FETCH_DEADLINE_VAR",
    "FETCH_MAX_BYTES_VAR",
    "FETCH_MAX_REDIRECTS_VAR",
    "HOST_ADDRESSES_VAR",
    "LLM_API_KEY_VAR",
    "LLM_VAR",
    "VARIABLES",
    "VISION_VAR",
    "EmbedSettings",
    "FetchLimits",
    "check_environment",
    "declared_embed_host",
    "fetch_limits",
    "host_networks",
    "load_embed_settings",
    "normalize_embed_url",
    "switch_on",
]

#: Embedding field -> the variable that declares it (KB-4 adds the rest of the ADR-0015 tuple).
VARIABLES = {
    "provider": "KNOWLEDGE_BASE_EMBED_PROVIDER",
    "model": "KNOWLEDGE_BASE_EMBED_MODEL",
    "dim": "KNOWLEDGE_BASE_EMBED_DIM",
    "base_url": "KNOWLEDGE_BASE_EMBED_URL",
}
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
_COUNT_RE = re.compile(r"0|[1-9][0-9]*")


@dataclass(frozen=True, slots=True)
class EmbedSettings:
    """A parsed declaration. ``api_key_source`` is the ``env:`` spec, never the secret."""

    provider: str
    model: str
    dim: int
    base_url: str | None
    api_key_source: str | None = None


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
    return EmbedSettings(
        provider=provider,
        model=model,
        dim=int(dim_raw),
        base_url=base_url,
        api_key_source=f"env:{API_KEY_VAR}" if _get(API_KEY_VAR) else None,
    )


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
    if not raw.isascii() or not _COUNT_RE.fullmatch(raw) or int(raw) < minimum:
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
