"""Tests for shared utility functions (utils.py)."""

from unittest.mock import patch

import pytest

from knowledge_base.exceptions import ValidationError
from knowledge_base.utils import is_private_ip, validate_base_url


# --- is_private_ip ---
# Existing tests in test_ingest.py cover the core cases;
# these tests verify the function at its canonical location.


def test_is_private_ip_loopback():
    assert is_private_ip("127.0.0.1") is True


def test_is_private_ip_cloud_metadata():
    assert is_private_ip("169.254.169.254") is True


def test_is_private_ip_localhost():
    assert is_private_ip("localhost") is True


# --- validate_base_url ---


def test_validate_base_url_rejects_bad_scheme():
    with pytest.raises(ValidationError, match="scheme"):
        validate_base_url("ftp://example.com")
    with pytest.raises(ValidationError, match="scheme"):
        validate_base_url("file:///etc/passwd")


def test_validate_base_url_rejects_missing_hostname():
    with pytest.raises(ValidationError, match="hostname"):
        validate_base_url("http://")


def test_validate_base_url_rejects_private_ip():
    with pytest.raises(ValidationError, match="private"):
        validate_base_url("http://127.0.0.1:1234")
    with pytest.raises(ValidationError, match="private"):
        validate_base_url("http://169.254.169.254")
    with pytest.raises(ValidationError, match="private"):
        validate_base_url("http://10.0.0.1:8080")


@patch("knowledge_base.utils.is_private_ip", return_value=False)
def test_validate_base_url_allows_public(_mock):
    validate_base_url("http://api.example.com:1234")  # should not raise
    validate_base_url("https://llm.example.com")  # should not raise


# --- validate_base_url loopback opt-in (ADR-0018 §4) ---


def test_validate_base_url_rejects_loopback_by_default():
    """Without the opt-in, loopback is rejected (fail-closed default)."""
    with pytest.raises(ValidationError, match="private"):
        validate_base_url("http://127.0.0.1:11434")
    with pytest.raises(ValidationError, match="private"):
        validate_base_url("http://localhost:11434/v1")


def test_validate_base_url_allows_loopback_ip_literal_with_optin():
    """allow_loopback accepts loopback IP literals across the whole 127.0.0.0/8 + ::1."""
    validate_base_url("http://127.0.0.1:11434", allow_loopback=True)  # no raise
    validate_base_url("http://127.5.6.7:11434", allow_loopback=True)  # whole /8
    validate_base_url("http://[::1]:11434", allow_loopback=True)  # IPv6 loopback


def test_validate_base_url_allows_localhost_label_with_optin():
    """The exact RFC-6761 reserved label 'localhost' is accepted under the opt-in.

    This is the URL ADR-0018 tells operators to use for a local openai_compat server.
    """
    validate_base_url("http://localhost:11434/v1", allow_loopback=True)  # no raise
    validate_base_url("http://LOCALHOST:11434", allow_loopback=True)  # case-insensitive


@patch("knowledge_base.utils.socket.getaddrinfo")
def test_validate_base_url_rejects_rebind_even_with_optin(mock_gai):
    """A hostname that *resolves* to loopback is NOT a loopback literal — stays rejected.

    This is the DNS-rebind vector (127.0.0.1.nip.io); the opt-in must not save it.
    """
    # Simulate the rebind: the hostname resolves to 127.0.0.1.
    mock_gai.return_value = [(2, 1, 6, "", ("127.0.0.1", 0))]
    with pytest.raises(ValidationError, match="private"):
        validate_base_url("http://127.0.0.1.nip.io", allow_loopback=True)


@patch("knowledge_base.utils.socket.getaddrinfo", side_effect=OSError("offline"))
def test_validate_base_url_rejects_localhost_lookalikes_with_optin(_mock_gai):
    """Only the EXACT label 'localhost' is allowed — look-alikes fall through and are rejected."""
    for url in (
        "http://localhost.attacker.com",
        "http://notlocalhost",
        "http://sub.localhost",
        "http://localhostx",
    ):
        with pytest.raises(ValidationError, match="private"):
            validate_base_url(url, allow_loopback=True)


def test_validate_base_url_rejects_nonloopback_floor_even_with_optin():
    """The non-loopback SSRF floor (metadata / link-local / RFC-1918) is ALWAYS enforced."""
    for url in (
        "http://169.254.169.254",  # cloud metadata
        "http://10.0.0.1",  # RFC-1918
        "http://192.168.1.41:1234",  # RFC-1918
        "http://[fd00::1]",  # IPv6 ULA
    ):
        with pytest.raises(ValidationError, match="private"):
            validate_base_url(url, allow_loopback=True)


def test_validate_base_url_scheme_floor_regardless_of_flag():
    """Scheme / hostname checks fire regardless of the loopback flag."""
    with pytest.raises(ValidationError, match="scheme"):
        validate_base_url("ftp://localhost", allow_loopback=True)
    with pytest.raises(ValidationError, match="hostname"):
        validate_base_url("http://", allow_loopback=True)


# --- _resolve_api_key (env:VARNAME indirection, ADR-0018 §4) ---


def test_resolve_api_key_plain_passthrough():
    from knowledge_base.utils import _resolve_api_key

    assert _resolve_api_key("sk-inline-123") == "sk-inline-123"
    assert _resolve_api_key(None) is None


def test_resolve_api_key_env_indirection(monkeypatch):
    from knowledge_base.utils import _resolve_api_key

    monkeypatch.setenv("KB_TEST_KEY", "sk-from-env")
    assert _resolve_api_key("env:KB_TEST_KEY") == "sk-from-env"


def test_resolve_api_key_env_missing_returns_none(monkeypatch):
    from knowledge_base.utils import _resolve_api_key

    monkeypatch.delenv("KB_MISSING_KEY", raising=False)
    assert _resolve_api_key("env:KB_MISSING_KEY") is None


# --- _sanitize_url relocated to utils (re-exported from llm for back-compat) ---


def test_sanitize_url_strips_userinfo_and_query():
    from knowledge_base.utils import _sanitize_url

    out = _sanitize_url("https://user:secret@host.example.com:443/v1?token=abc#frag")
    assert "secret" not in out
    assert "token" not in out
    assert "host.example.com" in out


# --- element_type constants ---


def test_element_type_constants():
    from knowledge_base.utils import (
        ELEMENT_INSERT_EXPR,
        ELEMENT_QUERY_EXPR,
        VALID_ELEMENT_TYPES,
    )

    assert {"float32", "int8"} == VALID_ELEMENT_TYPES
    assert set(ELEMENT_INSERT_EXPR.keys()) == set(ELEMENT_QUERY_EXPR.keys())
    assert ELEMENT_INSERT_EXPR["float32"] == "?"
    assert ELEMENT_QUERY_EXPR["float32"] == "?"
