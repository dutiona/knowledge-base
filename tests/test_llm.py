"""Tests for LLM configuration, calling, and connectivity (llm.py)."""

import json
import secrets

import httpx
import pytest

from knowledge_base.db import get_connection, init_schema
from knowledge_base.exceptions import ValidationError
from knowledge_base.llm import (
    _get_llm_config,
    _llm_call,
    _strip_think_tags,
    configure_llm,
)
from knowledge_base.utils import validate_base_url

PUBLIC_ADDRESS = "203.0.113.10"


def _setup(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    return conn


def _ok(request):
    return httpx.Response(200, json={})


@pytest.fixture
def serve(http_requests, fake_dns, public_net):
    """``serve(host, handler)``: a public name answered by *handler*; returns the request recorder."""

    def _serve(host, handler):
        fake_dns[host] = [PUBLIC_ADDRESS]
        http_requests.handlers[host] = handler
        return http_requests

    return _serve


def _raising(exc_type, message):
    def _handler(request):
        raise exc_type(message, request=request)

    return _handler


def _body(request) -> dict:
    return json.loads(request.content)


# --- _get_llm_config ---


def test_llm_config_defaults(tmp_path):
    conn = _setup(tmp_path)
    provider = conn.execute("SELECT value FROM config WHERE key = 'llm_provider'").fetchone()
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
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('llm_base_url', 'http://192.168.1.41:1234')")
    conn.execute("UPDATE config SET value = 'qwen/qwen3.5-35b-a3b' WHERE key = 'llm_model'")
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
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('llm_base_url', 'http://proxy:8080/v1')")
    conn.commit()
    cfg = _get_llm_config(conn)
    assert cfg["provider"] == "ollama"
    assert cfg["base_url"] == "http://proxy:8080/v1"


# --- configure_llm ---


def test_configure_llm(tmp_path, serve):
    serve("llm.example.test", _ok)
    conn = _setup(tmp_path)

    result = configure_llm(
        conn,
        provider="openai_compat",
        base_url="http://llm.example.test:1234",
        model="qwen/qwen3.5-35b-a3b",
        api_key="sk-test-123",
    )
    assert result["provider"] == "openai_compat"
    assert "api_key" not in result  # Redacted from response

    cfg = _get_llm_config(conn)
    assert cfg["provider"] == "openai_compat"
    assert cfg["base_url"] == "http://llm.example.test:1234"
    assert cfg["model"] == "qwen/qwen3.5-35b-a3b"
    assert cfg["api_key"] == "sk-test-123"  # But stored correctly


def test_configure_llm_switch_to_ollama_clears_stale(tmp_path, serve, operator_ollama):
    """Switching from openai_compat to ollama clears stale base_url and api_key."""
    serve("llm.example.test", _ok)
    serve(operator_ollama, _ok)
    conn = _setup(tmp_path)
    # First configure openai_compat with base_url and api_key
    configure_llm(
        conn,
        provider="openai_compat",
        base_url="http://llm.example.test:1234",
        model="some-model",
        api_key="sk-secret",
    )
    cfg = _get_llm_config(conn)
    assert cfg["base_url"] == "http://llm.example.test:1234"
    assert cfg["api_key"] == "sk-secret"

    # Switch to ollama without explicit base_url — stale values should be cleared
    configure_llm(conn, provider="ollama", model="qwen3.5:27b")
    cfg = _get_llm_config(conn)
    assert cfg["provider"] == "ollama"
    assert cfg["api_key"] is None
    # base_url should be auto-detected (not the old openai_compat URL)
    assert cfg["base_url"] != "http://llm.example.test:1234"


def test_configure_llm_remote_ollama_preserves_base_url(tmp_path, serve):
    """Explicitly setting base_url for ollama (remote) is preserved."""
    serve("gpu-node.example.test", _ok)
    conn = _setup(tmp_path)
    configure_llm(
        conn,
        provider="ollama",
        base_url="http://gpu-node.example.test:11434",
        model="qwen3.5:27b",
    )
    cfg = _get_llm_config(conn)
    assert cfg["provider"] == "ollama"
    assert cfg["base_url"] == "http://gpu-node.example.test:11434"


# --- configure_llm connectivity tests ---


def test_configure_llm_connectivity_ollama_reachable(tmp_path, serve, operator_ollama):
    """Ollama reachable: reachable=True, no warning."""
    serve(operator_ollama, _ok)
    conn = _setup(tmp_path)
    result = configure_llm(conn, provider="ollama")
    assert result["reachable"] is True
    assert "warning" not in result


def test_configure_llm_connectivity_ollama_unreachable(tmp_path, serve, operator_ollama):
    """Ollama unreachable: reachable=False, warning present, config still saved."""
    serve(operator_ollama, _raising(httpx.ConnectError, "Connection refused"))
    conn = _setup(tmp_path)
    result = configure_llm(conn, provider="ollama")
    assert result["reachable"] is False
    assert "warning" in result
    assert "Cannot connect" in result["warning"]
    # Config must still be persisted
    cfg = _get_llm_config(conn)
    assert cfg["provider"] == "ollama"
    assert cfg["base_url"] == f"http://{operator_ollama}:11434"


def test_configure_llm_connectivity_ollama_default_url(tmp_path, serve, operator_ollama):
    """No base_url param: probe uses auto-detected Ollama URL."""
    http_requests = serve(operator_ollama, _ok)
    conn = _setup(tmp_path)
    result = configure_llm(conn, provider="ollama", model="qwen3.5:27b")
    assert result["reachable"] is True
    assert result["base_url"] == f"http://{operator_ollama}:11434"
    assert http_requests.urls() == [f"http://{operator_ollama}:11434/api/tags"]


def test_configure_llm_connectivity_openai_reachable(tmp_path, serve):
    """OpenAI-compat reachable: reachable=True, auth header sent."""
    http_requests = serve("llm.example.test", _ok)
    conn = _setup(tmp_path)
    result = configure_llm(
        conn,
        provider="openai_compat",
        base_url="http://llm.example.test:1234",
        api_key="sk-test",
    )
    assert result["reachable"] is True
    assert http_requests[0].headers.get("Authorization") == "Bearer sk-test"


def test_configure_llm_connectivity_openai_auth_failure(tmp_path, serve):
    """OpenAI-compat 401: reachable=False, warning mentions auth."""
    serve("llm.example.test", lambda request: httpx.Response(401))
    conn = _setup(tmp_path)
    result = configure_llm(
        conn,
        provider="openai_compat",
        base_url="http://llm.example.test:1234",
        api_key="sk-bad",
    )
    assert result["reachable"] is False
    assert "auth" in result["warning"].lower() or "Authentication" in result["warning"]


def test_configure_llm_connectivity_timeout(tmp_path, serve, operator_ollama):
    """Timeout: reachable=False, warning mentions timeout."""
    serve(operator_ollama, _raising(httpx.ReadTimeout, "timed out"))
    conn = _setup(tmp_path)
    result = configure_llm(conn, provider="ollama")
    assert result["reachable"] is False
    assert "timed out" in result["warning"].lower() or "timeout" in result["warning"].lower()


def test_configure_llm_connectivity_server_error(tmp_path, serve, operator_ollama):
    """Server 500: reachable=False, warning mentions HTTP status."""
    serve(operator_ollama, lambda request: httpx.Response(500))
    conn = _setup(tmp_path)
    result = configure_llm(conn, provider="ollama")
    assert result["reachable"] is False
    assert "500" in result["warning"]


def test_configure_llm_connectivity_generic_exception(tmp_path, serve, operator_ollama):
    """Generic exception: reachable=False, warning present, no exception escapes."""

    def _boom(request):
        raise RuntimeError("unexpected")

    serve(operator_ollama, _boom)
    conn = _setup(tmp_path)
    result = configure_llm(conn, provider="ollama")
    assert result["reachable"] is False
    assert "RuntimeError" in result["warning"]


def test_configure_llm_connectivity_malformed_url(tmp_path, serve):
    """Unusual base_url: _sanitize_url doesn't crash, warning still works."""
    serve("gpu-node.example.test", _raising(httpx.ConnectError, "refused"))
    conn = _setup(tmp_path)
    # URL with valid scheme (passes validation) but unusual format
    result = configure_llm(
        conn, provider="ollama", base_url="http://user:pass@gpu-node.example.test:11434?token=secret"
    )
    assert result["reachable"] is False
    assert "warning" in result
    # Verify URL sanitization: no query params or userinfo in warning
    assert "secret" not in result["warning"]
    assert "pass" not in result["warning"]


def test_configure_llm_connectivity_openai_fallback_auth(tmp_path, serve):
    """OpenAI 404 on /v1/models + 401 on fallback: warning mentions auth."""

    def _handler(request):
        # /v1/models is not implemented (404); the fallback /v1/chat/completions answers 401
        return httpx.Response(404 if request.url.path == "/v1/models" else 401)

    http_requests = serve("llm.example.test", _handler)
    conn = _setup(tmp_path)
    result = configure_llm(
        conn,
        provider="openai_compat",
        base_url="http://llm.example.test:1234",
        api_key="sk-bad",
    )
    assert result["reachable"] is False
    assert "auth" in result["warning"].lower() or "Authentication" in result["warning"]
    assert [r.url.path for r in http_requests] == ["/v1/models", "/v1/chat/completions"]  # both endpoints were tried


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
            '<think>\nLet me analyze this...\n{"methods": [], "datasets": [], "metrics": []}\n</think>',
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


def _ollama_reply(text):
    return lambda request: httpx.Response(200, json={"response": text})


def test_llm_call_empty_response_raises(tmp_path, serve, operator_ollama):
    """_llm_call raises ValueError when LLM returns empty after tag stripping."""
    serve(operator_ollama, _ollama_reply("<think>internal reasoning</think>"))
    conn = _setup(tmp_path)

    with pytest.raises(ValueError, match="empty response"):
        _llm_call("test prompt", conn=conn)


def test_llm_call_recovers_json_from_inside_think_tags(tmp_path, serve, operator_ollama):
    """_llm_call recovers JSON when model wraps entire response in think tags (#163)."""
    serve(
        operator_ollama,
        _ollama_reply('<think>\nLet me analyze this paper...\n{"methods": [{"name": "ResNet"}]}\n</think>'),
    )
    conn = _setup(tmp_path)

    result = _llm_call("test prompt", conn=conn)
    parsed = json.loads(result)
    assert parsed == {"methods": [{"name": "ResNet"}]}


def test_llm_call_strips_tags_returns_json(tmp_path, serve, operator_ollama):
    """_llm_call strips think tags and returns the JSON portion."""
    serve(operator_ollama, _ollama_reply('<think>hmm</think>{"methods": []}'))
    conn = _setup(tmp_path)

    result = _llm_call("test prompt", conn=conn)
    assert result == '{"methods": []}'


def test_llm_call_ollama_sends_system_directive(tmp_path, serve, operator_ollama):
    """_llm_call sends system JSON directive to Ollama."""
    http_requests = serve(operator_ollama, _ollama_reply('{"ok": true}'))
    conn = _setup(tmp_path)

    _llm_call("test prompt", conn=conn)

    captured = _body(http_requests[0])
    assert "system" in captured
    assert "JSON" in captured["system"]


def test_llm_call_openai_sends_system_message(tmp_path, serve):
    """_llm_call sends system message for openai_compat provider."""

    def _handler(request):
        if request.method == "GET":  # the configure_llm probe
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})

    http_requests = serve("llm.example.test", _handler)
    conn = _setup(tmp_path)
    configure_llm(
        conn,
        provider="openai_compat",
        base_url="http://llm.example.test:1234",
        model="test-model",
    )

    _llm_call("test prompt", conn=conn)

    messages = _body(http_requests[-1]).get("messages", [])
    assert messages[0]["role"] == "system"
    assert "JSON" in messages[0]["content"]


def test_llm_call_requires_conn_or_cfg():
    """_llm_call raises ValueError when neither conn nor cfg is provided."""
    with pytest.raises(ValueError, match="Either conn or cfg"):
        _llm_call("test prompt")


# --- SSRF protection for openai_compat (#190) ---


def test_ssrf_check_blocks_private_ip():
    """validate_base_url rejects private/loopback addresses (replaces _ssrf_check_openai_compat)."""
    with pytest.raises(ValidationError, match="private"):
        validate_base_url("http://127.0.0.1:1234")
    with pytest.raises(ValidationError, match="private"):
        validate_base_url("http://192.168.1.41:1234")
    with pytest.raises(ValidationError, match="private"):
        validate_base_url("http://10.0.0.1:8080")
    with pytest.raises(ValidationError, match="private"):
        validate_base_url("http://169.254.169.254")


def test_ssrf_check_allows_public_ip(fake_dns, public_net):
    """validate_base_url allows public addresses."""
    fake_dns["api.example.test"] = [PUBLIC_ADDRESS]
    validate_base_url("http://api.example.test:1234")  # should not raise


@pytest.mark.parametrize(
    "url", ["http://tailnet.example.test:11434", "http://203.0.113.7:11434", "http://localhost:11434"]
)
def test_a_stored_ollama_url_keeps_the_floor(tmp_path, fake_dns, public_net, monkeypatch, url):
    """#572: an ollama base_url a tool supplies goes through the floor like every other family."""
    monkeypatch.setenv("KNOWLEDGE_BASE_HOST_ADDRESSES", "203.0.113.7")
    fake_dns["tailnet.example.test"] = ["100.64.0.7"]
    conn = _setup(tmp_path)
    with pytest.raises(ValidationError, match="private"):
        configure_llm(conn, provider="ollama", base_url=url)
    assert conn.execute("SELECT value FROM config WHERE key = 'llm_base_url'").fetchone() is None


def test_a_loopback_ollama_url_needs_the_opt_in(tmp_path, http_requests):
    conn = _setup(tmp_path)
    http_requests.handlers["localhost"] = _ok
    result = configure_llm(conn, provider="ollama", base_url="http://localhost:11434", allow_loopback_base_url=True)
    assert result["reachable"] is True
    assert http_requests.targets() == ["localhost"]


def test_a_stored_row_from_before_is_refused_at_use(tmp_path, http_requests, fake_dns):
    conn = _setup(tmp_path)
    fake_dns["tailnet.example.test"] = ["100.64.0.7"]
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('llm_base_url', 'http://tailnet.example.test:11434')"
    )
    conn.commit()
    with pytest.raises(ValidationError, match="refused by the request floor") as refused:
        _llm_call("p", conn=conn)
    assert "tailnet.example.test" not in str(refused.value)
    assert http_requests == []


def test_the_auto_detected_ollama_is_not_floored(tmp_path, http_requests, fake_dns, operator_ollama):
    conn = _setup(tmp_path)
    http_requests.handlers[operator_ollama] = lambda _r: httpx.Response(200, json={"response": "{}"})
    _llm_call("p", conn=conn)
    assert fake_dns.lookups == []
    assert http_requests.targets() == [operator_ollama]


def test_a_stored_url_rebinding_at_use_is_refused(tmp_path, http_requests, fake_dns, public_net):
    conn = _setup(tmp_path)
    fake_dns["llm.example.test"] = (["203.0.113.20"], ["100.64.0.7"])
    http_requests.handlers["llm.example.test"] = _ok
    configure_llm(conn, provider="openai_compat", base_url="https://llm.example.test", model="m")
    http_requests.clear()
    with pytest.raises(ValidationError, match="refused by the request floor") as refused:
        _llm_call("p", conn=conn)
    assert "llm.example.test" not in str(refused.value)
    assert http_requests == []


def test_a_floored_llm_call_targets_the_validated_address(tmp_path, http_requests, fake_dns, public_net):
    conn = _setup(tmp_path)
    fake_dns["llm.example.test"] = ["203.0.113.20"]
    http_requests.handlers["llm.example.test"] = lambda _r: httpx.Response(
        200, json={"choices": [{"message": {"content": "{}"}}], "data": []}
    )
    configure_llm(conn, provider="openai_compat", base_url="https://llm.example.test", model="m")
    http_requests.clear()
    _llm_call("p", conn=conn)
    assert http_requests.targets() == ["203.0.113.20"]


def test_ssrf_blocks_openai_compat_config_write(tmp_path):
    """configure_llm with openai_compat + private IP hard-rejects at config-write (ADR-0018 §4).

    The base_url is validated BEFORE it is persisted, so a malicious URL is never stored.
    """
    conn = _setup(tmp_path)
    with pytest.raises(ValidationError, match="private"):
        configure_llm(conn, provider="openai_compat", base_url="http://169.254.169.254", model="test")
    # Nothing persisted: provider stays at the seeded default.
    assert _get_llm_config(conn)["provider"] == "ollama"


def test_ssrf_blocks_openai_compat_llm_call(tmp_path):
    """_llm_call with openai_compat + private IP raises ValidationError."""
    cfg = {
        "provider": "openai_compat",
        "base_url": "http://169.254.169.254",
        "model": "test",
        "api_key": None,
    }
    with pytest.raises(ValidationError, match="refused by the request floor") as refused:
        _llm_call("test", cfg=cfg)
    assert "169.254.169.254" not in str(refused.value)


def _redirecting(request):
    return httpx.Response(302, headers={"Location": "http://elsewhere.example.test/steal"})


def test_llm_call_disables_redirects(serve):
    """openai_compat chat POST disables cross-host redirect following (ADR-0018 §4)."""
    cfg = {"provider": "openai_compat", "base_url": "http://api.example.test", "model": "m", "api_key": None}
    http_requests = serve("api.example.test", _redirecting)
    with pytest.raises(httpx.HTTPStatusError):
        _llm_call("test", cfg=cfg)
    assert len(http_requests) == 1


def test_llm_call_ollama_disables_redirects(serve, operator_ollama):
    """ollama generate POST also disables redirects (defense-in-depth, ADR-0018 §4)."""
    cfg = {"provider": "ollama", "base_url": f"http://{operator_ollama}:11434", "model": "m", "api_key": None}
    http_requests = serve(operator_ollama, _redirecting)
    with pytest.raises(httpx.HTTPStatusError):
        _llm_call("test", cfg=cfg)
    assert len(http_requests) == 1


# --- Slice E: anthropic_compat chat branch (AC2, #516) ---


def _anthropic_cfg(api_key):
    return {
        "provider": "anthropic_compat",
        "base_url": "https://api.example.test",
        "model": "claude-x",
        "api_key": api_key,
    }


def _anthropic_reply(blocks):
    return lambda request: httpx.Response(200, json={"content": blocks})


def test_llm_call_anthropic_posts_messages(serve):
    """anthropic_compat POSTs /v1/messages with x-api-key + anthropic-version (AC2)."""
    http_requests = serve("api.example.test", _anthropic_reply([{"type": "text", "text": '{"ok": true}'}]))

    result = _llm_call("hello", cfg=_anthropic_cfg("sk-ant-123"))

    assert len(http_requests) == 1  # a 3xx would have surfaced as an error, never followed
    request = http_requests[0]
    assert http_requests.urls() == ["https://api.example.test/v1/messages"]
    assert request.headers["x-api-key"] == "sk-ant-123"
    assert request.headers["anthropic-version"]  # pinned, non-empty
    body = _body(request)
    assert body["system"]  # system is a top-level field, not a message
    assert body["max_tokens"] > 0
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert result == '{"ok": true}'


def test_llm_call_anthropic_concatenates_text_blocks(serve):
    """Multiple text blocks concatenated; non-text blocks ignored."""
    serve(
        "api.example.test",
        _anthropic_reply(
            [
                {"type": "text", "text": '{"a":'},
                {"type": "tool_use", "id": "ignored"},
                {"type": "text", "text": "1}"},
            ]
        ),
    )
    assert _llm_call("x", cfg=_anthropic_cfg("k")) == '{"a":1}'


def test_llm_call_anthropic_resolves_env_key(serve, monkeypatch):
    """anthropic_compat api_key supports env:VARNAME indirection resolved at call time."""
    monkeypatch.setenv("KB_ANT_KEY", "sk-ant-from-env")
    http_requests = serve("api.example.test", _anthropic_reply([{"type": "text", "text": "{}"}]))

    _llm_call("x", cfg=_anthropic_cfg("env:KB_ANT_KEY"))
    assert http_requests[0].headers["x-api-key"] == "sk-ant-from-env"


def test_llm_call_anthropic_ssrf_blocked():
    cfg = {
        "provider": "anthropic_compat",
        "base_url": "http://169.254.169.254",
        "model": "claude-x",
        "api_key": "k",
    }
    with pytest.raises(ValidationError, match="refused by the request floor") as refused:
        _llm_call("x", cfg=cfg)
    assert "169.254.169.254" not in str(refused.value)


def test_configure_llm_accepts_anthropic_compat(tmp_path, serve):
    serve("api.example.test", _ok)
    conn = _setup(tmp_path)
    result = configure_llm(
        conn, provider="anthropic_compat", base_url="https://api.example.test", model="claude-x", api_key="sk-ant"
    )
    assert result["provider"] == "anthropic_compat"
    cfg = _get_llm_config(conn)
    assert cfg["provider"] == "anthropic_compat"
    assert cfg["base_url"] == "https://api.example.test"


def test_llm_connectivity_resolves_env_key(tmp_path, serve, monkeypatch):
    """The openai_compat connectivity probe resolves env:VARNAME (not the literal spec)."""
    from knowledge_base.llm import _test_llm_connectivity

    monkeypatch.setenv("KB_LLM_KEY", "sk-real")
    http_requests = serve("x.example.test", _ok)

    result = _test_llm_connectivity("openai_compat", "https://x.example.test", "env:KB_LLM_KEY", floor=True)
    assert result["reachable"] is True
    assert http_requests[0].headers.get("Authorization") == "Bearer sk-real"


def test_llm_connectivity_anthropic_validates_only(tmp_path, serve):
    """anthropic_compat has no /v1/models probe — validate the URL, assume reachable."""
    from knowledge_base.llm import _test_llm_connectivity

    serve("api.example.test", _ok)
    result = _test_llm_connectivity("anthropic_compat", "https://api.example.test", "k", floor=True)
    assert result["reachable"] is True
    # A private anthropic base_url is still rejected (advisory unreachable, never raises).
    blocked = _test_llm_connectivity("anthropic_compat", "http://169.254.169.254", "k", floor=True)
    assert blocked["reachable"] is False


def test_llm_call_ollama_disables_thinking(tmp_path, serve, operator_ollama):
    """Ollama payload carries think=False (#552): thinking models (qwen3.5)
    otherwise put every token in `thinking` and return an empty `response`
    under format=json."""
    http_requests = serve(operator_ollama, _ollama_reply('{"methods": []}'))
    conn = _setup(tmp_path)

    _llm_call("test prompt", conn=conn)

    assert _body(http_requests[0])["think"] is False


def test_llm_call_ollama_retries_without_think_on_400(tmp_path, serve, operator_ollama):
    """An Ollama build that rejects the think field gets one retry without it (#552)."""

    def _handler(request):
        if "think" in _body(request):
            return httpx.Response(400, text='{"error": "registry.ollama.ai does not support think"}')
        return httpx.Response(200, json={"response": '{"methods": []}'})

    http_requests = serve(operator_ollama, _handler)
    conn = _setup(tmp_path)

    out = _llm_call("test prompt", conn=conn)

    calls = [_body(request) for request in http_requests]
    assert out == '{"methods": []}'
    assert len(calls) == 2
    assert "think" in calls[0]
    assert "think" not in calls[1]


def test_llm_call_ollama_unrelated_400_propagates(tmp_path, serve, operator_ollama):
    """A 400 unrelated to the think field propagates after exactly one POST (#552 review)."""
    http_requests = serve(
        operator_ollama, lambda request: httpx.Response(400, text='{"error": "model \'nonexistent\' not found"}')
    )
    conn = _setup(tmp_path)

    with pytest.raises(httpx.HTTPStatusError):
        _llm_call("test prompt", conn=conn)

    assert len(http_requests) == 1  # no blind retry on a non-think 400


# --- #569: LLM extraction is off unless KNOWLEDGE_BASE_LLM=on; no embedder key for the LLM ---


def _config_rows(conn) -> dict:
    return dict(conn.execute("SELECT key, value FROM config").fetchall())


def test_llm_off_refuses_before_any_request(kb_conn, http_requests, monkeypatch):
    from knowledge_base.exceptions import ValidationError
    from knowledge_base.llm import _llm_call, configure_llm

    monkeypatch.delenv("KNOWLEDGE_BASE_LLM")
    before = _config_rows(kb_conn)
    with pytest.raises(ValidationError, match="KNOWLEDGE_BASE_LLM"):
        configure_llm(kb_conn, "ollama", "http://169.254.169.254")
    with pytest.raises(ValidationError, match="KNOWLEDGE_BASE_LLM"):
        _llm_call("p", conn=kb_conn)
    assert http_requests == []
    assert _config_rows(kb_conn) == before


@pytest.mark.parametrize(
    "spec",
    ["env:KNOWLEDGE_BASE_EMBED_API_KEY", "env:HOME", "file:KNOWLEDGE_BASE_EMBED_API_KEY_FILE", "file:/etc/hostname"],
)
def test_llm_key_spec_allowlist_refuses_other_indirect_specs(kb_conn, http_requests, serve, spec):
    from knowledge_base.exceptions import ValidationError
    from knowledge_base.llm import configure_llm

    serve("llm.example.test", _ok)
    before = _config_rows(kb_conn)
    with pytest.raises(ValidationError, match="KNOWLEDGE_BASE_LLM_API_KEY"):
        configure_llm(kb_conn, "openai_compat", "https://llm.example.test", api_key=spec)
    assert http_requests == []
    assert _config_rows(kb_conn) == before


@pytest.mark.parametrize("spec", ["env:KNOWLEDGE_BASE_LLM_API_KEY", "sk-inline"])
def test_llm_key_spec_allowlist_accepts_its_own_source(kb_conn, serve, spec):
    from knowledge_base.llm import configure_llm

    serve("llm.example.test", lambda request: httpx.Response(200, json={"data": []}))
    configure_llm(kb_conn, "openai_compat", "https://llm.example.test", api_key=spec)
    assert _config_rows(kb_conn)["llm_api_key"] == spec


@pytest.mark.parametrize("spec", ["env:KNOWLEDGE_BASE_EMBED_API_KEY", "file:KNOWLEDGE_BASE_EMBED_API_KEY_FILE"])
def test_a_stored_foreign_key_spec_is_refused_before_any_request(kb_conn, http_requests, serve, spec):
    from knowledge_base.exceptions import ValidationError
    from knowledge_base.llm import _get_llm_config, configure_llm

    serve("attacker.example.test", _ok)
    kb_conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES "
        "('llm_provider', 'openai_compat'), ('llm_base_url', 'https://llm.example.test'), ('llm_api_key', ?)",
        (spec,),
    )
    kb_conn.commit()
    before = _config_rows(kb_conn)
    with pytest.raises(ValidationError, match="KNOWLEDGE_BASE_LLM_API_KEY"):
        _get_llm_config(kb_conn)
    with pytest.raises(ValidationError, match="KNOWLEDGE_BASE_LLM_API_KEY"):
        configure_llm(kb_conn, "openai_compat", "https://attacker.example.test")
    assert http_requests == []
    assert _config_rows(kb_conn) == before


# --- #662: the LLM key is one Bearer token of visible ASCII, refused before any header ---


@pytest.mark.parametrize("provider", ["openai_compat", "anthropic_compat"])
@pytest.mark.parametrize("padded", ["{k} ", " {k}", "{k}\r", "{k}\nmore", "{k}\u00e9", "{k}\x01"])
def test_a_padded_llm_key_is_refused_naming_the_field(kb_conn, http_requests, serve, provider, padded):
    """A key h11 or httpx cannot put on the wire is refused at configure time, never stored, never sent (#662)."""
    from knowledge_base.exceptions import ValidationError
    from knowledge_base.llm import configure_llm

    serve("llm.example.test", _ok)
    key = secrets.token_hex(16)
    before = _config_rows(kb_conn)
    with pytest.raises(ValidationError, match=r"^llm_api_key must") as refused:
        configure_llm(kb_conn, provider, "https://llm.example.test", api_key=padded.format(k=key))
    assert key not in str(refused.value)
    assert http_requests == []
    assert _config_rows(kb_conn) == before


def test_a_padded_env_llm_key_is_refused_before_any_request(kb_conn, http_requests, serve, monkeypatch):
    """The env key is checked on the resolved bytes at the header, because a config read can be stale (#662)."""
    from knowledge_base.exceptions import ValidationError
    from knowledge_base.llm import _llm_call

    serve("llm.example.test", _ok)
    key = secrets.token_hex(16)
    monkeypatch.setenv("KNOWLEDGE_BASE_LLM_API_KEY", f"{key} ")
    cfg = {
        "provider": "openai_compat",
        "base_url": "https://llm.example.test",
        "model": "m",
        "api_key": "env:KNOWLEDGE_BASE_LLM_API_KEY",
        "allow_loopback": False,
        "floor": True,
    }
    with pytest.raises(ValidationError, match=r"^KNOWLEDGE_BASE_LLM_API_KEY must") as refused:
        _llm_call("prompt", cfg=cfg)
    assert key not in str(refused.value)
    assert http_requests == []


def test_an_unset_llm_key_variable_stays_keyless(kb_conn, http_requests, serve, monkeypatch):
    """A keyless local server keeps working: no variable, no Authorization header, the request still goes out."""
    from knowledge_base.llm import _llm_call

    monkeypatch.delenv("KNOWLEDGE_BASE_LLM_API_KEY", raising=False)
    serve("llm.example.test", lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]}))
    cfg = {
        "provider": "openai_compat",
        "base_url": "https://llm.example.test",
        "model": "m",
        "api_key": "env:KNOWLEDGE_BASE_LLM_API_KEY",
        "allow_loopback": False,
        "floor": True,
    }
    assert _llm_call("prompt", cfg=cfg) == "{}"
    assert len(http_requests) == 1
    assert "Authorization" not in http_requests[0].headers


def test_a_stored_padded_key_is_refused_when_the_provider_switches_to_an_http_family(kb_conn, http_requests, serve):
    """A key stored while it was never sent cannot ride into an HTTP provider's header (#662)."""
    from knowledge_base.exceptions import ValidationError
    from knowledge_base.llm import configure_llm

    serve("llm.example.test", _ok)
    key = secrets.token_hex(16)
    configure_llm(kb_conn, "ollama", "https://llm.example.test", api_key=f"{key} ")
    before = _config_rows(kb_conn)
    sent = len(http_requests)  # the ollama probe above is the only request so far
    with pytest.raises(ValidationError, match=r"^llm_api_key must") as refused:
        configure_llm(kb_conn, "openai_compat", "https://llm.example.test")
    assert key not in str(refused.value)
    assert len(http_requests) == sent
    assert _config_rows(kb_conn) == before


# --- #662: a request-floor failure names the reason, never the LLM host ---


@pytest.mark.parametrize("mode", ["unresolvable", "refused"])
def test_a_request_floor_failure_names_no_host(kb_conn, http_requests, fake_dns, mode):
    """A host that stops resolving, or resolves to a refused address, must not reach the error text (#662).

    The URL resolved when it was configured; the request path floors it again, and that refusal carries the hostname
    in the floor's own message. The LLM boundary renders the reason instead.
    """
    from knowledge_base.exceptions import ValidationError
    from knowledge_base.llm import _llm_call

    kb_conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES "
        "('llm_provider', 'openai_compat'), ('llm_base_url', 'https://llm.example.test'), ('llm_model', 'm')"
    )
    kb_conn.commit()
    if mode == "refused":
        fake_dns["llm.example.test"] = ["10.0.0.7"]

    with pytest.raises(ValidationError) as refused:
        _llm_call("prompt", conn=kb_conn)

    assert "llm.example.test" not in str(refused.value)
    assert http_requests == []
    if mode == "unresolvable":
        assert "did not resolve" in str(refused.value)
    else:
        assert "refused by the request floor" in str(refused.value)
