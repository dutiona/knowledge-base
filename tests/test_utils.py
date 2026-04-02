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
