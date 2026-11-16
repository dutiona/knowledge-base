"""LLM configuration, calling, and connectivity.

Centralises all LLM interaction: reading provider config from the DB,
dispatching HTTP requests to Ollama or OpenAI-compatible endpoints,
stripping thinking tags from responses, and testing connectivity.

Extracted from extraction.py (issue #240) so that any module needing
LLM calls (extraction, keywords, …) can reuse the same infrastructure
without circular imports.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3

import httpx

from .embeddings import _get_ollama_url
from .exceptions import ValidationError
from .settings import LLM_API_KEY_VAR, LLM_VAR, switch_on
from .utils import _resolve_api_key, _sanitize_url, egress_client, loopback_opt_in, validate_base_url

# Re-exported for back-compat: _sanitize_url moved to utils.py (ADR-0018 §4) so
# embeddings/embed_swap can share it; callers importing it from llm still resolve.
__all__ = ["_sanitize_url"]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


_HTTP_LLM_FAMILIES = ("openai_compat", "anthropic_compat")


def _require_llm() -> None:
    """LLM extraction posts whole papers to a model; it runs only when switched on (#569)."""
    if not switch_on(LLM_VAR):
        raise ValidationError(f"LLM extraction is off; set {LLM_VAR}=on to enable it")


def _check_llm_key_spec(spec: str | None) -> None:
    """An LLM key is inline or ``env:KNOWLEDGE_BASE_LLM_API_KEY``: no other secret of the process."""
    if spec and spec.startswith("env:") and spec != f"env:{LLM_API_KEY_VAR}":
        raise ValidationError(f"an LLM api_key is an inline value or env:{LLM_API_KEY_VAR}, not {spec!r}")


def _get_llm_config(conn: sqlite3.Connection) -> dict:
    """Read LLM configuration from config table. Refuses while LLM extraction is off."""
    _require_llm()
    provider = conn.execute("SELECT value FROM config WHERE key = 'llm_provider'").fetchone()
    model = conn.execute("SELECT value FROM config WHERE key = 'llm_model'").fetchone()
    base_url_row = conn.execute("SELECT value FROM config WHERE key = 'llm_base_url'").fetchone()
    api_key_row = conn.execute("SELECT value FROM config WHERE key = 'llm_api_key'").fetchone()

    prov = provider["value"] if provider else "ollama"
    _check_llm_key_spec(api_key_row["value"] if api_key_row else None)

    if base_url_row:
        base_url = base_url_row["value"]
    elif prov == "ollama":
        base_url = _get_ollama_url()
    else:
        raise ValueError(f"llm_base_url is required when llm_provider is {prov!r}")

    return {
        "provider": prov,
        "model": model["value"] if model else "qwen3.5:27b",
        "base_url": base_url.rstrip("/").removesuffix("/v1") if prov in _HTTP_LLM_FAMILIES else base_url.rstrip("/"),
        "api_key": api_key_row["value"] if api_key_row else None,
        "allow_loopback": loopback_opt_in(conn),
        # A stored URL came from a tool and keeps the SSRF floor; the auto-detected Ollama is the operator's (#572).
        "floor": base_url_row is not None,
    }


# ---------------------------------------------------------------------------
# Think-tag stripping
# ---------------------------------------------------------------------------

_THINK_TAG_RE = re.compile(r"<(think(?:ing)?)>.*?</\1>", re.DOTALL)

_SYSTEM_JSON_DIRECTIVE = (
    "/no_think\n"
    "Respond directly with valid JSON. "
    "Do NOT use thinking mode or <think> tags. "
    "Output only the JSON object, with no preamble, tags, or commentary."
)

_THINK_WRAP_RE = re.compile(r"\A\s*<(think(?:ing)?)>(.*)</\1>\s*\Z", re.DOTALL)


def _strip_think_tags(text: str) -> str:
    """Strip reasoning/thinking tags from the preamble/trailer of LLM responses.

    Only strips tags outside the JSON payload to avoid corrupting literal
    <think> text inside JSON string fields.

    Also handles models (e.g. qwen3.5) that wrap their entire response —
    including JSON — inside a single think block (#163).
    """
    # Fast path: entire response is one think block (qwen3.5 thinking-mode)
    wrap_match = _THINK_WRAP_RE.match(text)
    if wrap_match:
        inner = wrap_match.group(2).strip()
        # Use raw_decode to find the first complete JSON value, ignoring
        # reasoning text before/after it (e.g. "[2] passes" or trailing "Done.")
        # Prefer objects over arrays — reasoning text often has stray brackets.
        decoder = json.JSONDecoder()
        for target in ("{", "["):
            for i, ch in enumerate(inner):
                if ch == target:
                    try:
                        _, end = decoder.raw_decode(inner, i)
                        return inner[i:end]
                    except json.JSONDecodeError:
                        continue
        # No valid JSON inside — model only reasoned
        return ""

    # Find the start of JSON content
    json_start = -1
    for i, ch in enumerate(text):
        if ch in ("{", "["):
            json_start = i
            break

    if json_start == -1:
        # No JSON found — strip tags from entire text
        stripped = _THINK_TAG_RE.sub("", text).strip()
    else:
        # Strip tags only from preamble before JSON
        preamble = text[:json_start]
        json_body = text[json_start:]
        stripped = (_THINK_TAG_RE.sub("", preamble) + json_body).strip()

    if stripped != text.strip():
        logger.debug(
            "Stripped thinking tags from LLM response (%d → %d chars)",
            len(text),
            len(stripped),
        )
    return stripped


# ---------------------------------------------------------------------------
# LLM calling
# ---------------------------------------------------------------------------


_LLM_TIMEOUT = 120

# Anthropic Messages API: the stable version header, and a max_tokens ceiling sized for
# single-shot JSON extraction (the API requires max_tokens). Documented constants — a
# future bump is a one-line change (ADR-0018 §1).
_ANTHROPIC_VERSION = "2023-06-01"
_ANTHROPIC_MAX_TOKENS = 8192


def _llm_call(
    prompt: str,
    *,
    conn: sqlite3.Connection | None = None,
    cfg: dict | None = None,
    client: httpx.Client | None = None,
) -> str:
    """Call LLM to extract structured data. Supports Ollama and OpenAI-compatible APIs.

    Accepts either a ``conn`` (reads config from DB) or a pre-read ``cfg`` dict.
    The ``cfg`` path is preferred in hot loops to avoid threading issues.

    When ``client`` is provided (an ``egress_client`` built for ``cfg``), it is shared across a
    parallel loop; otherwise one is opened for the call.
    """
    if cfg is None:
        if conn is None:
            raise ValueError("Either conn or cfg must be provided to _llm_call")
        cfg = _get_llm_config(conn)

    if client is None:
        # The client floors and pins a stored URL on every request (#572); the operator's endpoint is plain.
        with egress_client(floor=cfg.get("floor", True), allow_loopback=cfg.get("allow_loopback", False)) as own:
            return _llm_call(prompt, cfg=cfg, client=own)
    post = client.post

    # follow_redirects=False on every provider POST: a chat endpoint is served directly, so a 3xx is
    # anomalous and is not followed (ADR-0018 §4). URL ingest, by contrast, follows redirects (web.py).
    if cfg["provider"] == "ollama":
        payload = {
            "model": cfg["model"],
            "prompt": prompt,
            "system": _SYSTEM_JSON_DIRECTIVE,
            "stream": False,
            "format": "json",
            # Thinking-capable models (qwen3.5) spend every token in the separate
            # `thinking` field under format=json, leaving `response` empty (#552).
            "think": False,
        }
        resp = post(
            f"{cfg['base_url']}/api/generate",
            json=payload,
            timeout=_LLM_TIMEOUT,
            follow_redirects=False,
        )
        if resp.status_code == 400 and "think" in resp.text.lower():
            # An Ollama build that rejects the think field for this model —
            # retry once without it (non-thinking models never needed it).
            # Gated on the error text so a legitimate 400 (bad model, prompt
            # rejection, proxy error) propagates instead of being re-POSTed.
            payload.pop("think")
            resp = post(
                f"{cfg['base_url']}/api/generate",
                json=payload,
                timeout=_LLM_TIMEOUT,
                follow_redirects=False,
            )
        resp.raise_for_status()
        raw = resp.json()["response"]
    elif cfg["provider"] == "anthropic_compat":
        headers = {"anthropic-version": _ANTHROPIC_VERSION}
        resolved_key = _resolve_api_key(cfg.get("api_key"))
        if resolved_key:
            headers["x-api-key"] = resolved_key
        resp = post(
            f"{cfg['base_url']}/v1/messages",
            headers=headers,
            json={
                "model": cfg["model"],
                "max_tokens": _ANTHROPIC_MAX_TOKENS,
                "system": _SYSTEM_JSON_DIRECTIVE,  # Anthropic: system is a top-level field
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=_LLM_TIMEOUT,
            follow_redirects=False,
        )
        resp.raise_for_status()
        # Response is a list of content blocks; concatenate the text ones.
        raw = "".join(block["text"] for block in resp.json()["content"] if block.get("type") == "text")
    else:  # openai_compat
        headers = {}
        resolved_key = _resolve_api_key(cfg.get("api_key"))
        if resolved_key:
            headers["Authorization"] = f"Bearer {resolved_key}"
        resp = post(
            f"{cfg['base_url']}/v1/chat/completions",
            headers=headers,
            json={
                "model": cfg["model"],
                "messages": [
                    {"role": "system", "content": _SYSTEM_JSON_DIRECTIVE},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout=_LLM_TIMEOUT,
            follow_redirects=False,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]

    raw = _strip_think_tags(raw)

    if not raw:
        raise ValueError("LLM returned empty response (possible thinking-mode issue)")

    return raw


# ---------------------------------------------------------------------------
# Connectivity & configuration
# ---------------------------------------------------------------------------


_CONNECTIVITY_TIMEOUT = 3


def _probe(client: httpx.Client, provider: str, base_url: str, api_key: str | None) -> None:
    """One reachability probe; raises an httpx error on failure."""
    if provider == "ollama":
        client.get(f"{base_url}/api/tags").raise_for_status()
        return
    # openai_compat
    headers: dict[str, str] = {}
    resolved_key = _resolve_api_key(api_key)  # env:VARNAME resolved at probe time
    if resolved_key:
        headers["Authorization"] = f"Bearer {resolved_key}"
    resp = client.get(f"{base_url}/v1/models", headers=headers)
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
        # Some providers don't implement /v1/models — fall back
        fallback = client.get(f"{base_url}/v1/chat/completions", headers=headers)
        # Any non-connection response (even 405) means reachable
        if fallback.status_code in (401, 403):
            raise httpx.HTTPStatusError(
                f"HTTP {fallback.status_code}", request=fallback.request, response=fallback
            ) from exc


def _test_llm_connectivity(
    provider: str, base_url: str, api_key: str | None = None, *, floor: bool, allow_loopback: bool = False
) -> dict:
    """Probe LLM endpoint reachability. Returns advisory status, never raises."""
    safe_url = _sanitize_url(base_url)
    try:
        if provider == "anthropic_compat":
            # Anthropic exposes no lightweight unauthenticated probe (no /v1/models; a real
            # /v1/messages probe needs a billable call). Validate the URL and assume reachable
            # — the config still persists either way (advisory probe).
            validate_base_url(base_url, allow_loopback=allow_loopback)
            return {"reachable": True}
        with egress_client(floor=floor, allow_loopback=allow_loopback, timeout=_CONNECTIVITY_TIMEOUT) as client:
            _probe(client, provider, base_url, api_key)
        return {"reachable": True}
    except httpx.ConnectError:
        warning = f"Cannot connect to {safe_url}"
    except httpx.TimeoutException:
        warning = f"Connection timed out to {safe_url} ({_CONNECTIVITY_TIMEOUT}s)"
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            warning = "Authentication failed \u2014 check api_key"
        else:
            warning = f"Server returned HTTP {exc.response.status_code}"
    except Exception as exc:
        warning = f"Connectivity test failed: {type(exc).__name__}"
    logger.warning("LLM connectivity test failed for %s at %s: %s", provider, safe_url, warning)
    return {"reachable": False, "warning": warning}


def configure_llm(
    conn: sqlite3.Connection,
    provider: str = "ollama",
    base_url: str | None = None,
    model: str = "qwen3.5:27b",
    api_key: str | None = None,
    allow_loopback_base_url: bool | None = None,
) -> dict:
    """Configure LLM provider settings.

    Every family's ``base_url`` is normalized then SSRF-validated **before** it is persisted
    (the primary gate, ADR-0018 §4; ``ollama`` included since #572), and every request to it is
    floored and pinned again. Leave ``base_url`` unset for ``ollama`` to use the operator's
    auto-detected endpoint (``OLLAMA_HOST``, else localhost), which is not floored. ``allow_loopback_base_url``
    is a config flag SHARED with the embedding path: pass ``True``/``False`` to set it,
    or leave it ``None`` to preserve the current value (so configuring the LLM does not
    clobber an embedding-side loopback opt-in).

    Note: ``api_key`` is stored as plain text in the SQLite config table (or as an
    ``env:VARNAME`` indirection). Acceptable for local-only use; keyring hardening is
    deferred.
    """
    _require_llm()
    if provider not in ("ollama", *_HTTP_LLM_FAMILIES):
        raise ValidationError(f"Unknown provider: {provider}. Use 'ollama', 'openai_compat', or 'anthropic_compat'.")
    if api_key or provider == "ollama":
        _check_llm_key_spec(api_key)
    else:  # an HTTP provider configured without a key keeps the stored one
        row = conn.execute("SELECT value FROM config WHERE key = 'llm_api_key'").fetchone()
        _check_llm_key_spec(row["value"] if row else None)
    if provider in _HTTP_LLM_FAMILIES and not base_url:
        raise ValidationError(f"base_url is required for {provider} provider")
    # Preserve the shared loopback flag unless the caller set it explicitly.
    loopback = loopback_opt_in(conn) if allow_loopback_base_url is None else allow_loopback_base_url
    if base_url:
        # Normalize FIRST, then validate the normalized value (close the suffix-strip gap).
        base_url = base_url.rstrip("/")
        if provider in _HTTP_LLM_FAMILIES:
            base_url = base_url.removesuffix("/v1")
        validate_base_url(base_url, allow_loopback=loopback)

    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('llm_provider', ?)",
        (provider,),
    )
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('llm_model', ?)", (model,))
    if base_url:
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES ('llm_base_url', ?)",
            (base_url,),
        )
    elif provider == "ollama":
        # Clear stale base_url from previous provider to use auto-detection
        conn.execute("DELETE FROM config WHERE key = 'llm_base_url'")
    if api_key:
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES ('llm_api_key', ?)",
            (api_key,),
        )
    elif provider == "ollama":
        # Clear stale api_key — Ollama doesn't use auth
        conn.execute("DELETE FROM config WHERE key = 'llm_api_key'")
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('allow_loopback_base_url', ?)",
        ("true" if loopback else "false",),
    )
    conn.commit()
    cfg = _get_llm_config(conn)
    connectivity = _test_llm_connectivity(
        cfg["provider"], cfg["base_url"], cfg.get("api_key"), floor=cfg["floor"], allow_loopback=cfg["allow_loopback"]
    )
    # Redact sensitive fields from response
    cfg.pop("api_key", None)
    cfg.update(connectivity)
    return cfg
