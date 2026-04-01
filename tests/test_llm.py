"""Tests for LLM configuration, calling, and connectivity (llm.py)."""

import json
from unittest.mock import patch

import httpx
import pytest

from knowledge_base.db import get_connection, init_schema
from knowledge_base.llm import (
    _get_llm_config,
    _llm_call,
    _strip_think_tags,
    configure_llm,
)


def _setup(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    return conn


def _mock_get_ok(*args, **kwargs):
    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {}

    return FakeResp()


# --- _get_llm_config ---


def test_llm_config_defaults(tmp_path):
    conn = _setup(tmp_path)
    provider = conn.execute(
        "SELECT value FROM config WHERE key = 'llm_provider'"
    ).fetchone()
    model = conn.execute("SELECT value FROM config WHERE key = 'llm_model'").fetchone()
    assert provider["value"] == "ollama"
    assert model["value"] == "qwen3.5:27b"


def test_get_llm_config_defaults(tmp_path):
    conn = _setup(tmp_path)
    cfg = _get_llm_config(conn)
    assert cfg["provider"] == "ollama"
    assert cfg["model"] == "qwen3.5:27b"
    assert "base_url" in cfg


def test_get_llm_config_custom(tmp_path):
    conn = _setup(tmp_path)
    conn.execute("UPDATE config SET value = 'openai_compat' WHERE key = 'llm_provider'")
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('llm_base_url', 'http://192.168.1.41:1234')"
    )
    conn.execute(
        "UPDATE config SET value = 'qwen/qwen3.5-35b-a3b' WHERE key = 'llm_model'"
    )
    conn.commit()
    cfg = _get_llm_config(conn)
    assert cfg["provider"] == "openai_compat"
    assert cfg["base_url"] == "http://192.168.1.41:1234"
    assert cfg["model"] == "qwen/qwen3.5-35b-a3b"


@pytest.mark.parametrize(
    "input_url,expected",
    [
        ("http://host:1234", "http://host:1234"),
        ("http://host:1234/v1", "http://host:1234"),
        ("http://host:1234/v1/", "http://host:1234"),
        ("https://api.openai.com/v1", "https://api.openai.com"),
        ("http://host:1234/", "http://host:1234"),
        ("http://host/v1beta", "http://host/v1beta"),  # not stripped
    ],
)
def test_get_llm_config_strips_v1(tmp_path, input_url, expected):
    conn = _setup(tmp_path)
    conn.execute("UPDATE config SET value = 'openai_compat' WHERE key = 'llm_provider'")
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('llm_base_url', ?)",
        (input_url,),
    )
    conn.commit()
    cfg = _get_llm_config(conn)
    assert cfg["base_url"] == expected


def test_get_llm_config_ollama_preserves_v1(tmp_path):
    """Ollama provider should NOT strip /v1 (proxy path-prefix scenario)."""
    conn = _setup(tmp_path)
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('llm_base_url', 'http://proxy:8080/v1')"
    )
    conn.commit()
    cfg = _get_llm_config(conn)
    assert cfg["provider"] == "ollama"
    assert cfg["base_url"] == "http://proxy:8080/v1"


# --- configure_llm ---


@patch("knowledge_base.llm.httpx.get", _mock_get_ok)
def test_configure_llm(tmp_path):
    conn = _setup(tmp_path)

    result = configure_llm(
        conn,
        provider="openai_compat",
        base_url="http://192.168.1.41:1234",
        model="qwen/qwen3.5-35b-a3b",
        api_key="sk-test-123",
    )
    assert result["provider"] == "openai_compat"
    assert "api_key" not in result  # Redacted from response

    cfg = _get_llm_config(conn)
    assert cfg["provider"] == "openai_compat"
    assert cfg["base_url"] == "http://192.168.1.41:1234"
    assert cfg["model"] == "qwen/qwen3.5-35b-a3b"
    assert cfg["api_key"] == "sk-test-123"  # But stored correctly


@patch("knowledge_base.llm.httpx.get", _mock_get_ok)
def test_configure_llm_switch_to_ollama_clears_stale(tmp_path):
    """Switching from openai_compat to ollama clears stale base_url and api_key."""
    conn = _setup(tmp_path)
    # First configure openai_compat with base_url and api_key
    configure_llm(
        conn,
        provider="openai_compat",
        base_url="http://192.168.1.41:1234",
        model="some-model",
        api_key="sk-secret",
    )
    cfg = _get_llm_config(conn)
    assert cfg["base_url"] == "http://192.168.1.41:1234"
    assert cfg["api_key"] == "sk-secret"

    # Switch to ollama without explicit base_url — stale values should be cleared
    configure_llm(conn, provider="ollama", model="qwen3.5:27b")
    cfg = _get_llm_config(conn)
    assert cfg["provider"] == "ollama"
    assert cfg["api_key"] is None
    # base_url should be auto-detected (not the old openai_compat URL)
    assert cfg["base_url"] != "http://192.168.1.41:1234"


@patch("knowledge_base.llm.httpx.get", _mock_get_ok)
def test_configure_llm_remote_ollama_preserves_base_url(tmp_path):
    """Explicitly setting base_url for ollama (remote) is preserved."""
    conn = _setup(tmp_path)
    configure_llm(
        conn,
        provider="ollama",
        base_url="http://remote-ollama:11434",
        model="qwen3.5:27b",
    )
    cfg = _get_llm_config(conn)
    assert cfg["provider"] == "ollama"
    assert cfg["base_url"] == "http://remote-ollama:11434"


# --- configure_llm connectivity tests ---


@patch("knowledge_base.llm.httpx.get", _mock_get_ok)
def test_configure_llm_connectivity_ollama_reachable(tmp_path):
    """Ollama reachable: reachable=True, no warning."""
    conn = _setup(tmp_path)
    result = configure_llm(conn, provider="ollama", base_url="http://localhost:11434")
    assert result["reachable"] is True
    assert "warning" not in result


@patch(
    "knowledge_base.llm.httpx.get",
    side_effect=httpx.ConnectError("Connection refused"),
)
def test_configure_llm_connectivity_ollama_unreachable(mock_get, tmp_path):
    """Ollama unreachable: reachable=False, warning present, config still saved."""
    conn = _setup(tmp_path)
    result = configure_llm(conn, provider="ollama", base_url="http://localhost:11434")
    assert result["reachable"] is False
    assert "warning" in result
    assert "Cannot connect" in result["warning"]
    # Config must still be persisted
    cfg = _get_llm_config(conn)
    assert cfg["provider"] == "ollama"
    assert cfg["base_url"] == "http://localhost:11434"


@patch("knowledge_base.llm.httpx.get", _mock_get_ok)
@patch(
    "knowledge_base.llm._get_ollama_url",
    return_value="http://auto-detected:11434",
)
def test_configure_llm_connectivity_ollama_default_url(mock_url, tmp_path):
    """No base_url param: probe uses auto-detected Ollama URL."""
    conn = _setup(tmp_path)
    result = configure_llm(conn, provider="ollama", model="qwen3.5:27b")
    assert result["reachable"] is True
    assert result["base_url"] == "http://auto-detected:11434"


def test_configure_llm_connectivity_openai_reachable(tmp_path):
    """OpenAI-compat reachable: reachable=True, auth header sent."""
    captured_headers = {}

    def _mock_get_capture(*args, **kwargs):
        captured_headers.update(kwargs.get("headers", {}))

        class FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

        return FakeResp()

    conn = _setup(tmp_path)
    with patch("knowledge_base.llm.httpx.get", _mock_get_capture):
        result = configure_llm(
            conn,
            provider="openai_compat",
            base_url="http://localhost:1234",
            api_key="sk-test",
        )
    assert result["reachable"] is True
    assert captured_headers.get("Authorization") == "Bearer sk-test"


def test_configure_llm_connectivity_openai_auth_failure(tmp_path):
    """OpenAI-compat 401: reachable=False, warning mentions auth."""

    def _mock_get_401(*args, **kwargs):
        resp = httpx.Response(401, request=httpx.Request("GET", args[0]))
        raise httpx.HTTPStatusError("401", request=resp.request, response=resp)

    conn = _setup(tmp_path)
    with patch("knowledge_base.llm.httpx.get", _mock_get_401):
        result = configure_llm(
            conn,
            provider="openai_compat",
            base_url="http://localhost:1234",
            api_key="sk-bad",
        )
    assert result["reachable"] is False
    assert "auth" in result["warning"].lower() or "Authentication" in result["warning"]


@patch(
    "knowledge_base.llm.httpx.get",
    side_effect=httpx.ReadTimeout("timed out"),
)
def test_configure_llm_connectivity_timeout(mock_get, tmp_path):
    """Timeout: reachable=False, warning mentions timeout."""
    conn = _setup(tmp_path)
    result = configure_llm(conn, provider="ollama", base_url="http://localhost:11434")
    assert result["reachable"] is False
    assert (
        "timed out" in result["warning"].lower()
        or "timeout" in result["warning"].lower()
    )


def test_configure_llm_connectivity_server_error(tmp_path):
    """Server 500: reachable=False, warning mentions HTTP status."""

    def _mock_get_500(*args, **kwargs):
        resp = httpx.Response(500, request=httpx.Request("GET", args[0]))
        raise httpx.HTTPStatusError("500", request=resp.request, response=resp)

    conn = _setup(tmp_path)
    with patch("knowledge_base.llm.httpx.get", _mock_get_500):
        result = configure_llm(
            conn, provider="ollama", base_url="http://localhost:11434"
        )
    assert result["reachable"] is False
    assert "500" in result["warning"]


@patch(
    "knowledge_base.llm.httpx.get",
    side_effect=RuntimeError("unexpected"),
)
def test_configure_llm_connectivity_generic_exception(mock_get, tmp_path):
    """Generic exception: reachable=False, warning present, no exception escapes."""
    conn = _setup(tmp_path)
    result = configure_llm(conn, provider="ollama", base_url="http://localhost:11434")
    assert result["reachable"] is False
    assert "RuntimeError" in result["warning"]


@patch(
    "knowledge_base.llm.httpx.get",
    side_effect=httpx.ConnectError("refused"),
)
def test_configure_llm_connectivity_malformed_url(mock_get, tmp_path):
    """Unusual base_url: _sanitize_url doesn't crash, warning still works."""
    conn = _setup(tmp_path)
    # URL with valid scheme (passes validation) but unusual format
    result = configure_llm(
        conn, provider="ollama", base_url="http://user:pass@host:11434?token=secret"
    )
    assert result["reachable"] is False
    assert "warning" in result
    # Verify URL sanitization: no query params or userinfo in warning
    assert "secret" not in result["warning"]
    assert "pass" not in result["warning"]


def test_configure_llm_connectivity_openai_fallback_auth(tmp_path):
    """OpenAI 404 on /v1/models + 401 on fallback: warning mentions auth."""
    call_count = {"n": 0}

    def _mock_get_fallback(*args, **kwargs):
        call_count["n"] += 1
        url = args[0]
        if "/v1/models" in url:
            # Return 404 response — raise_for_status() will trigger inner handler
            return httpx.Response(404, request=httpx.Request("GET", url))
        # fallback /v1/chat/completions returns 401
        return httpx.Response(401, request=httpx.Request("GET", url))

    conn = _setup(tmp_path)
    with patch("knowledge_base.llm.httpx.get", _mock_get_fallback):
        result = configure_llm(
            conn,
            provider="openai_compat",
            base_url="http://localhost:1234",
            api_key="sk-bad",
        )
    assert result["reachable"] is False
    assert "auth" in result["warning"].lower() or "Authentication" in result["warning"]
    assert call_count["n"] == 2  # both endpoints were tried


# --- _strip_think_tags ---


@pytest.mark.parametrize(
    "input_text, expected",
    [
        # No tags — passthrough
        ('{"test": "hello"}', '{"test": "hello"}'),
        # Empty <think></think> (Qwen empty-thinking case)
        ('<think></think>{"test": "hello"}', '{"test": "hello"}'),
        # Empty with whitespace inside
        ('<think>  \n</think>{"test": "hello"}', '{"test": "hello"}'),
        # Think tags with content before JSON
        (
            '<think>Let me analyze this carefully...</think>{"methods": []}',
            '{"methods": []}',
        ),
        # <thinking> variant (safety net)
        ('<thinking>reasoning here</thinking>{"data": 1}', '{"data": 1}'),
        # Multiline thinking content
        (
            '<think>\nStep 1: read the text\nStep 2: extract\n</think>\n{"methods": []}',
            '{"methods": []}',
        ),
        # Only thinking tags, no JSON — should return empty string
        ("<think>just thinking</think>", ""),
        # JSON entirely inside think tags (qwen3.5 thinking-mode, issue #163)
        (
            '<think>{"methods": [{"name": "ResNet"}]}</think>',
            '{"methods": [{"name": "ResNet"}]}',
        ),
        # Reasoning + JSON inside think tags (qwen3.5 verbose thinking)
        (
            "<think>\nLet me analyze this...\n"
            '{"methods": [], "datasets": [], "metrics": []}\n</think>',
            '{"methods": [], "datasets": [], "metrics": []}',
        ),
        # <thinking> variant with JSON inside
        (
            '<thinking>{"data": [1, 2]}</thinking>',
            '{"data": [1, 2]}',
        ),
        # Brackets in reasoning before JSON — must skip to real JSON
        (
            '<think>Need [2] passes before I emit JSON. {"methods": []}</think>',
            '{"methods": []}',
        ),
        # Multiple JSON objects — returns first complete one
        (
            '<think>Example object: {"draft": true}\n{"methods": []}</think>',
            '{"draft": true}',
        ),
        # Trailing text after JSON inside think block
        (
            '<think>Reasoning only\n{"methods": []}\nDone.</think>',
            '{"methods": []}',
        ),
        # No tags, just whitespace — preserves stripped result
        ('  {"x": 1}  ', '{"x": 1}'),
        # Literal <think> inside JSON field — must NOT be corrupted
        (
            '{"description": "Uses <think>tags</think> for reasoning"}',
            '{"description": "Uses <think>tags</think> for reasoning"}',
        ),
        # Think preamble + literal <think> inside JSON — only preamble stripped
        (
            '<think>reasoning</think>{"description": "model uses <think>mode</think>"}',
            '{"description": "model uses <think>mode</think>"}',
        ),
    ],
    ids=[
        "no_tags",
        "empty_think",
        "empty_think_whitespace",
        "think_with_content",
        "thinking_variant",
        "multiline_thinking",
        "only_thinking_no_json",
        "json_inside_think_tags",
        "reasoning_plus_json_inside_think",
        "thinking_variant_json_inside",
        "brackets_in_reasoning_before_json",
        "multiple_json_objects_returns_first",
        "trailing_text_after_json",
        "whitespace_only",
        "literal_think_in_json",
        "preamble_plus_literal_in_json",
    ],
)
def test_strip_think_tags(input_text, expected):
    assert _strip_think_tags(input_text) == expected


# --- _llm_call ---


def test_llm_call_empty_response_raises(tmp_path):
    """_llm_call raises ValueError when LLM returns empty after tag stripping."""
    conn = _setup(tmp_path)

    def _mock_post(*args, **kwargs):
        class FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"response": "<think>internal reasoning</think>"}

        return FakeResp()

    with patch("knowledge_base.llm.httpx.post", _mock_post):
        with pytest.raises(ValueError, match="empty response"):
            _llm_call("test prompt", conn=conn)


def test_llm_call_recovers_json_from_inside_think_tags(tmp_path):
    """_llm_call recovers JSON when model wraps entire response in think tags (#163)."""
    conn = _setup(tmp_path)

    def _mock_post(*args, **kwargs):
        class FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "response": (
                        "<think>\nLet me analyze this paper...\n"
                        '{"methods": [{"name": "ResNet"}]}\n</think>'
                    )
                }

        return FakeResp()

    with patch("knowledge_base.llm.httpx.post", _mock_post):
        result = _llm_call("test prompt", conn=conn)
    parsed = json.loads(result)
    assert parsed == {"methods": [{"name": "ResNet"}]}


def test_llm_call_strips_tags_returns_json(tmp_path):
    """_llm_call strips think tags and returns the JSON portion."""
    conn = _setup(tmp_path)

    def _mock_post(*args, **kwargs):
        class FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"response": '<think>hmm</think>{"methods": []}'}

        return FakeResp()

    with patch("knowledge_base.llm.httpx.post", _mock_post):
        result = _llm_call("test prompt", conn=conn)
    assert result == '{"methods": []}'


def test_llm_call_ollama_sends_system_directive(tmp_path):
    """_llm_call sends system JSON directive to Ollama."""
    conn = _setup(tmp_path)
    captured = {}

    def _mock_post(url, **kwargs):
        captured.update(kwargs.get("json", {}))

        class FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"response": '{"ok": true}'}

        return FakeResp()

    with patch("knowledge_base.llm.httpx.post", _mock_post):
        _llm_call("test prompt", conn=conn)

    assert "system" in captured
    assert "JSON" in captured["system"]


@patch("knowledge_base.llm.httpx.get", _mock_get_ok)
def test_llm_call_openai_sends_system_message(tmp_path):
    """_llm_call sends system message for openai_compat provider."""
    conn = _setup(tmp_path)
    configure_llm(
        conn,
        provider="openai_compat",
        base_url="http://localhost:1234",
        model="test-model",
    )
    captured = {}

    def _mock_post(url, **kwargs):
        captured.update(kwargs.get("json", {}))

        class FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": '{"ok": true}'}}]}

        return FakeResp()

    with patch("knowledge_base.llm.httpx.post", _mock_post):
        _llm_call("test prompt", conn=conn)

    messages = captured.get("messages", [])
    assert messages[0]["role"] == "system"
    assert "JSON" in messages[0]["content"]


def test_llm_call_requires_conn_or_cfg():
    """_llm_call raises ValueError when neither conn nor cfg is provided."""
    with pytest.raises(ValueError, match="Either conn or cfg"):
        _llm_call("test prompt")
