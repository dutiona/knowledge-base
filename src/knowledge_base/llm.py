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

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _get_llm_config(conn: sqlite3.Connection) -> dict:
    """Read LLM configuration from config table."""
    provider = conn.execute(
        "SELECT value FROM config WHERE key = 'llm_provider'"
    ).fetchone()
    model = conn.execute("SELECT value FROM config WHERE key = 'llm_model'").fetchone()
    base_url_row = conn.execute(
        "SELECT value FROM config WHERE key = 'llm_base_url'"
    ).fetchone()
    api_key_row = conn.execute(
        "SELECT value FROM config WHERE key = 'llm_api_key'"
    ).fetchone()

    prov = provider["value"] if provider else "ollama"

    if base_url_row:
        base_url = base_url_row["value"]
    elif prov == "ollama":
        base_url = _get_ollama_url()
    else:
        raise ValueError(
            "llm_base_url is required when llm_provider is 'openai_compat'"
        )

    return {
        "provider": prov,
        "model": model["value"] if model else "qwen3.5:27b",
        "base_url": base_url.rstrip("/").removesuffix("/v1")
        if prov == "openai_compat"
        else base_url.rstrip("/"),
        "api_key": api_key_row["value"] if api_key_row else None,
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

    When ``client`` is provided, uses that connection-pooled client instead of
    creating a new connection per request (avoids TCP overhead in parallel loops).
    """
    if cfg is None:
        if conn is None:
            raise ValueError("Either conn or cfg must be provided to _llm_call")
        cfg = _get_llm_config(conn)

    post = client.post if client is not None else httpx.post

    if cfg["provider"] == "ollama":
        resp = post(
            f"{cfg['base_url']}/api/generate",
            json={
                "model": cfg["model"],
                "prompt": prompt,
                "system": _SYSTEM_JSON_DIRECTIVE,
                "stream": False,
                "format": "json",
            },
            timeout=_LLM_TIMEOUT,
        )
        resp.raise_for_status()
        raw = resp.json()["response"]
    else:  # openai_compat
        headers = {}
        if cfg.get("api_key"):
            headers["Authorization"] = f"Bearer {cfg['api_key']}"
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


def _sanitize_url(url: str) -> str:
    """Strip query parameters and userinfo from a URL for safe logging."""
    from urllib.parse import urlparse, urlunparse

    try:
        parsed = urlparse(url)
        host = parsed.hostname or "unknown"
        try:
            port = f":{parsed.port}" if parsed.port else ""
        except ValueError:
            port = ""
        return urlunparse((parsed.scheme, f"{host}{port}", parsed.path, "", "", ""))
    except Exception:
        # Conservative fallback: strip everything that could contain credentials
        # Remove userinfo (before @), query params (?), and fragments (#)
        safe = url.split("://", 1)[-1] if "://" in url else url
        safe = safe.split("@")[-1]  # drop userinfo
        safe = safe.split("?")[0]  # drop query
        safe = safe.split("#")[0]  # drop fragment
        scheme = url.split("://", 1)[0] if "://" in url else "http"
        return f"{scheme}://{safe}"


_CONNECTIVITY_TIMEOUT = 3


def _test_llm_connectivity(
    provider: str, base_url: str, api_key: str | None = None
) -> dict:
    """Probe LLM endpoint reachability. Returns advisory status, never raises."""
    safe_url = _sanitize_url(base_url)
    try:
        if provider == "ollama":
            resp = httpx.get(f"{base_url}/api/tags", timeout=_CONNECTIVITY_TIMEOUT)
            resp.raise_for_status()
        else:
            headers: dict[str, str] = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            resp = httpx.get(
                f"{base_url}/v1/models",
                headers=headers,
                timeout=_CONNECTIVITY_TIMEOUT,
            )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    # Some providers don't implement /v1/models — fall back
                    fallback = httpx.get(
                        f"{base_url}/v1/chat/completions",
                        headers=headers,
                        timeout=_CONNECTIVITY_TIMEOUT,
                    )
                    # Any non-connection response (even 405) means reachable
                    if fallback.status_code in (401, 403):
                        raise httpx.HTTPStatusError(
                            f"HTTP {fallback.status_code}",
                            request=fallback.request,
                            response=fallback,
                        )
                else:
                    raise
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
    logger.warning(
        "LLM connectivity test failed for %s at %s: %s", provider, safe_url, warning
    )
    return {"reachable": False, "warning": warning}


def configure_llm(
    conn: sqlite3.Connection,
    provider: str = "ollama",
    base_url: str | None = None,
    model: str = "qwen3.5:27b",
    api_key: str | None = None,
) -> dict:
    """Configure LLM provider settings.

    Note: ``api_key`` is stored as plain text in the SQLite config table.
    Acceptable for local-only use; consider system keyring integration
    (e.g. ``keyring`` library) before exposing this tool over a network.
    """
    if provider not in ("ollama", "openai_compat"):
        raise ValidationError(
            f"Unknown provider: {provider}. Use 'ollama' or 'openai_compat'."
        )
    if provider == "openai_compat" and not base_url:
        raise ValidationError("base_url is required for openai_compat provider")
    if base_url:
        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https"):
            raise ValidationError(
                f"Invalid URL scheme: {parsed.scheme}. Use http or https."
            )

    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('llm_provider', ?)",
        (provider,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('llm_model', ?)", (model,)
    )
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
    conn.commit()
    cfg = _get_llm_config(conn)
    connectivity = _test_llm_connectivity(
        cfg["provider"], cfg["base_url"], cfg.get("api_key")
    )
    # Redact sensitive fields from response
    cfg.pop("api_key", None)
    cfg.update(connectivity)
    return cfg
