"""The embedding declaration and egress switches read from the environment (#569)."""

from __future__ import annotations

import pytest

from knowledge_base.exceptions import ValidationError
from knowledge_base.settings import (
    HOST_ADDRESSES_VAR,
    EmbedSettings,
    check_environment,
    declared_embed_host,
    host_networks,
    load_embed_settings,
    switch_on,
)

_EMBED_VARS = (
    "KNOWLEDGE_BASE_EMBED_PROVIDER",
    "KNOWLEDGE_BASE_EMBED_MODEL",
    "KNOWLEDGE_BASE_EMBED_DIM",
    "KNOWLEDGE_BASE_EMBED_URL",
    "KNOWLEDGE_BASE_EMBED_API_KEY",
)


@pytest.fixture
def declare(monkeypatch):
    """Replace the suite declaration with exactly the given variables."""

    def _declare(**values: str) -> None:
        for name in _EMBED_VARS:
            monkeypatch.delenv(name, raising=False)
        for name, value in values.items():
            monkeypatch.setenv(f"KNOWLEDGE_BASE_EMBED_{name.upper()}", value)

    return _declare


_OPENAI_COMPAT = {"provider": "openai_compat", "model": "qwen3-embedding", "dim": "768"}


def test_complete_openai_compat_declaration(declare):
    declare(**_OPENAI_COMPAT, url="https://embedder.example.test/v1/", api_key="sk-test-only")
    settings = load_embed_settings()
    assert settings == EmbedSettings(
        provider="openai_compat",
        model="qwen3-embedding",
        dim=768,
        base_url="https://embedder.example.test",
        api_key_source="env:KNOWLEDGE_BASE_EMBED_API_KEY",
    )
    assert "sk-test-only" not in repr(settings)


def test_no_key_means_no_key_source(declare):
    declare(**_OPENAI_COMPAT, url="https://embedder.example.test")
    assert load_embed_settings().api_key_source is None


def test_ollama_keeps_a_v1_suffix(declare):
    declare(provider="ollama", model="bge-m3", dim="1024", url="http://embedder.example.test:11434/v1/")
    assert load_embed_settings().base_url == "http://embedder.example.test:11434/v1"


@pytest.mark.parametrize(
    "missing",
    ["provider", "model", "dim", "url"],
)
def test_each_required_variable_names_itself(declare, missing):
    values = {**_OPENAI_COMPAT, "url": "https://embedder.example.test"}
    del values[missing]
    declare(**values)
    with pytest.raises(ValidationError, match=rf"^KNOWLEDGE_BASE_EMBED_{missing.upper()}"):
        load_embed_settings()


def test_empty_value_counts_as_unset(declare):
    declare(**{**_OPENAI_COMPAT, "model": "   "}, url="https://embedder.example.test")
    with pytest.raises(ValidationError, match=r"^KNOWLEDGE_BASE_EMBED_MODEL"):
        load_embed_settings()


@pytest.mark.parametrize("provider", ["anthropic_compat", "OpenAI_Compat", "tei"])
def test_invalid_provider(declare, provider):
    declare(**{**_OPENAI_COMPAT, "provider": provider}, url="https://embedder.example.test")
    with pytest.raises(ValidationError, match=r"^KNOWLEDGE_BASE_EMBED_PROVIDER"):
        load_embed_settings()


@pytest.mark.parametrize("dim", ["0", "-1", "1_024", "abc", "1.5", "０"])
def test_invalid_dim(declare, dim):
    declare(**{**_OPENAI_COMPAT, "dim": dim}, url="https://embedder.example.test")
    with pytest.raises(ValidationError, match=r"^KNOWLEDGE_BASE_EMBED_DIM"):
        load_embed_settings()


@pytest.mark.parametrize(
    "url",
    [
        "ftp://h.example.test",
        "https://",
        "https://u:p@h.example.test",
        "https://h.example.test/?q=1",
        "https://h.example.test/#f",
    ],
)
def test_invalid_url(declare, url):
    declare(**_OPENAI_COMPAT, url=url)
    with pytest.raises(ValidationError, match=r"^KNOWLEDGE_BASE_EMBED_URL"):
        load_embed_settings()


def test_onnx_needs_its_model_file_and_refuses_a_url(declare, monkeypatch, tmp_path):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"")
    declare(provider="onnx", model="bge-m3", dim="1024")
    monkeypatch.delenv("ONNX_EMBED_MODEL_PATH", raising=False)
    with pytest.raises(ValidationError, match=r"^ONNX_EMBED_MODEL_PATH"):
        load_embed_settings()
    monkeypatch.setenv("ONNX_EMBED_MODEL_PATH", str(tmp_path / "absent.onnx"))
    with pytest.raises(ValidationError, match=r"^ONNX_EMBED_MODEL_PATH"):
        load_embed_settings()
    monkeypatch.setenv("ONNX_EMBED_MODEL_PATH", str(model))
    assert load_embed_settings().base_url is None
    declare(provider="onnx", model="bge-m3", dim="1024", url="http://embedder.example.test")
    with pytest.raises(ValidationError, match=r"^KNOWLEDGE_BASE_EMBED_URL"):
        load_embed_settings()


def test_the_deprecated_embed_provider_is_refused(declare, monkeypatch):
    declare(**_OPENAI_COMPAT, url="https://embedder.example.test")
    monkeypatch.setenv("EMBED_PROVIDER", "onnx")
    with pytest.raises(ValidationError, match=r"^EMBED_PROVIDER.*KNOWLEDGE_BASE_EMBED_PROVIDER"):
        load_embed_settings()


@pytest.mark.parametrize(("value", "expected"), [(None, False), ("", False), ("off", False), ("on", True)])
def test_switch_values(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("KNOWLEDGE_BASE_VISION", raising=False)
    else:
        monkeypatch.setenv("KNOWLEDGE_BASE_VISION", value)
    assert switch_on("KNOWLEDGE_BASE_VISION") is expected


@pytest.mark.parametrize("value", ["ON", "true", "1", "yes"])
def test_switch_refuses_other_values(monkeypatch, value):
    monkeypatch.setenv("KNOWLEDGE_BASE_BROWSER", value)
    with pytest.raises(ValidationError, match=r"^KNOWLEDGE_BASE_BROWSER"):
        switch_on("KNOWLEDGE_BASE_BROWSER")


def test_check_environment_validates_the_switches(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_BASE_LLM", "yes")
    with pytest.raises(ValidationError, match=r"^KNOWLEDGE_BASE_LLM"):
        check_environment()


def test_declared_embed_host(declare):
    declare(**_OPENAI_COMPAT, url="https://Embedder.Example.Test.:8443/v1")
    assert declared_embed_host() == "embedder.example.test"
    declare(**_OPENAI_COMPAT)
    assert declared_embed_host() is None


@pytest.mark.parametrize("url", ["htps://user:TOKEN@h.example.test", "https://user:TOKEN@h.example.test/?q=TOKEN"])
def test_a_refused_url_never_echoes_its_credentials(declare, url):
    declare(**_OPENAI_COMPAT, url=url)
    with pytest.raises(ValidationError) as refused:
        load_embed_settings()
    assert "TOKEN" not in str(refused.value)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_host_addresses_unset_is_empty(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(HOST_ADDRESSES_VAR, raising=False)
    else:
        monkeypatch.setenv(HOST_ADDRESSES_VAR, value)
    assert host_networks() == ()


def test_host_addresses_parse(monkeypatch):
    monkeypatch.setenv(HOST_ADDRESSES_VAR, " 198.51.100.7 , 2001:db8:7::/64 ")
    assert [str(n) for n in host_networks()] == ["198.51.100.7/32", "2001:db8:7::/64"]


@pytest.mark.parametrize(
    "value",
    ["198.51.100.7,,2001:db8::7", "self.example.test", "198.51.100.7/24", "::ffff:198.51.100.7", "300.1.2.3"],
)
def test_host_addresses_refuse_malformed(monkeypatch, value):
    monkeypatch.setenv(HOST_ADDRESSES_VAR, value)
    with pytest.raises(ValidationError, match=r"^KNOWLEDGE_BASE_HOST_ADDRESSES"):
        host_networks()


def test_check_environment_validates_host_addresses(monkeypatch):
    monkeypatch.setenv(HOST_ADDRESSES_VAR, "198.51.100.7/24")
    with pytest.raises(ValidationError, match=r"^KNOWLEDGE_BASE_HOST_ADDRESSES"):
        check_environment()
