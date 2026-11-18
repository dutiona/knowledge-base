"""The embedding declaration and egress switches read from the environment (#569)."""

from __future__ import annotations

import itertools

import pytest

from knowledge_base.exceptions import EmbeddingModelMismatchError, ValidationError
from knowledge_base.settings import (
    FETCH_DEADLINE_VAR,
    FETCH_MAX_BYTES_VAR,
    FETCH_MAX_REDIRECTS_VAR,
    HOST_ADDRESSES_VAR,
    IDENTITY_FIELDS,
    EmbedSettings,
    FetchLimits,
    check_environment,
    declared_embed_host,
    declared_identity,
    fetch_limits,
    host_networks,
    identity_mismatch,
    load_embed_settings,
    require_identity,
    routes_by_model,
    serving_identity,
    switch_on,
)

_SERVED = ("artifact_sha256", "pooling", "normalization", "max_input_tokens")
_EMBED_VARS = (
    "KNOWLEDGE_BASE_EMBED_PROVIDER",
    "KNOWLEDGE_BASE_EMBED_MODEL",
    "KNOWLEDGE_BASE_EMBED_DIM",
    "KNOWLEDGE_BASE_EMBED_URL",
    "KNOWLEDGE_BASE_EMBED_API_KEY",
    "KNOWLEDGE_BASE_EMBED_MATRYOSHKA_BASE_DIM",
    "KNOWLEDGE_BASE_EMBED_BATCH_SIZE",
    *(f"KNOWLEDGE_BASE_EMBED_{field.upper()}" for field in _SERVED),
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


_FETCH_VARS = (FETCH_MAX_BYTES_VAR, FETCH_MAX_REDIRECTS_VAR, FETCH_DEADLINE_VAR)


@pytest.mark.parametrize("value", [None, "", "  "])
def test_fetch_limits_default(monkeypatch, value):
    for name in _FETCH_VARS:
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert fetch_limits() == FetchLimits(max_bytes=52428800, max_redirects=5, deadline=60)


def test_fetch_limits_parse(monkeypatch):
    monkeypatch.setenv(FETCH_MAX_BYTES_VAR, "4096")
    monkeypatch.setenv(FETCH_MAX_REDIRECTS_VAR, "0")
    monkeypatch.setenv(FETCH_DEADLINE_VAR, " 007 ")  # surrounding blanks are dropped, as for every variable
    assert fetch_limits() == FetchLimits(max_bytes=4096, max_redirects=0, deadline=7)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        *((name, value) for name in _FETCH_VARS for value in ("-1", "+3", "1.5", "abc", "\u0663", "1e3")),
        (FETCH_MAX_BYTES_VAR, "0"),
        (FETCH_DEADLINE_VAR, "0"),
    ],
)
def test_fetch_limits_refuse_malformed(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError, match=rf"^{name}"):
        fetch_limits()


def test_check_environment_validates_fetch_limits(monkeypatch):
    monkeypatch.setenv(FETCH_DEADLINE_VAR, "soon")
    with pytest.raises(ValidationError, match=rf"^{FETCH_DEADLINE_VAR}"):
        check_environment()


# --- the whole ADR-0015 tuple (#573) ----------------------------------------------

_ARTIFACT = {"artifact_sha256": "a" * 64, "pooling": "last", "normalization": "l2", "max_input_tokens": "2048"}
_OLLAMA = {"provider": "ollama", "model": "bge-m3", "dim": "1024", "url": "http://embedder.example.test:11434"}
_LLAMA = {"provider": "llama.cpp", "model": "bge-m3", "dim": "1024", "url": "http://embedder.example.test:8010"}


def test_the_whole_identity_is_declared(declare):
    declare(**_LLAMA, matryoshka_base_dim="2048", **_ARTIFACT)
    settings = load_embed_settings()
    assert declared_identity(settings) == {
        "model": "bge-m3",
        "provider": "llama.cpp",
        "dim": 1024,
        "matryoshka_base_dim": 2048,
        "element_type": "float32",
        "artifact_sha256": "a" * 64,
        "pooling": "last",
        "normalization": "l2",
        "max_input_tokens": 2048,
    }
    assert list(declared_identity(settings)) == list(IDENTITY_FIELDS)


@pytest.mark.parametrize("provider", ["llama.cpp", "openai_compat"])
def test_a_served_artifact_is_declared_for_an_endpoint_that_counts_tokens(declare, provider):
    declare(**{**_LLAMA, "provider": provider}, **_ARTIFACT)
    assert load_embed_settings().max_input_tokens == 2048


@pytest.mark.parametrize(
    "values",
    [
        _OLLAMA,
        {"provider": "openai", "model": "m", "dim": "8", "url": "https://api.openai.com"},
        {"provider": "onnx", "model": "local", "dim": "8"},
    ],
    ids=["ollama", "openai", "onnx"],
)
def test_a_served_artifact_is_refused_where_no_endpoint_counts_tokens(declare, values, tmp_path, monkeypatch):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"")
    monkeypatch.setenv("ONNX_EMBED_MODEL_PATH", str(model))
    declare(**values, **_ARTIFACT)
    with pytest.raises(ValidationError, match=r"^KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS") as error:
        load_embed_settings()
    assert "llama.cpp" in str(error.value)
    assert "openai_compat" in str(error.value)


def test_declaring_nothing_new_gives_the_unset_values(declare):
    declare(**_OLLAMA)
    identity = declared_identity(load_embed_settings())
    assert [identity[field] for field in ("matryoshka_base_dim", "element_type", *_SERVED)] == [
        None,
        "float32",
        None,
        None,
        None,
        None,
    ]


@pytest.mark.parametrize(
    "present",
    [subset for size in (1, 2, 3) for subset in itertools.combinations(_SERVED, size)],
)
def test_a_partial_served_artifact_names_every_missing_variable(declare, present):
    declare(**_OLLAMA, **{field: _ARTIFACT[field] for field in present})
    missing = [f"KNOWLEDGE_BASE_EMBED_{field.upper()}" for field in _SERVED if field not in present]
    with pytest.raises(ValidationError, match=rf"^{missing[0]}") as error:
        load_embed_settings()
    assert all(name in str(error.value) for name in missing)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("artifact_sha256", "A" * 64),
        ("artifact_sha256", "a" * 63),
        ("artifact_sha256", "g" * 64),
        ("max_input_tokens", "0"),
        ("max_input_tokens", "-1"),
        ("max_input_tokens", "2k"),
        ("matryoshka_base_dim", "abc"),
        ("matryoshka_base_dim", "1024"),
        ("matryoshka_base_dim", "512"),
    ],
)
def test_an_invalid_identity_value_names_its_variable(declare, field, value):
    declare(**_LLAMA, **{**_ARTIFACT, field: value})
    with pytest.raises(ValidationError, match=rf"^KNOWLEDGE_BASE_EMBED_{field.upper()}"):
        load_embed_settings()


def _identity(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "model": "bge-m3",
        "provider": "ollama",
        "dim": 1024,
        "matryoshka_base_dim": None,
        "element_type": "float32",
        "artifact_sha256": "a" * 64,
        "pooling": "last",
        "normalization": "l2",
        "max_input_tokens": 2048,
    }
    return {**base, **changes}


_OTHER = {
    "model": "other",
    "provider": "onnx",
    "dim": 768,
    "matryoshka_base_dim": 2048,
    "element_type": "int8",
    "artifact_sha256": "b" * 64,
    "pooling": "mean",
    "normalization": "none",
    "max_input_tokens": 512,
}


def test_identity_mismatch_compares_field_by_field():
    assert identity_mismatch(_identity(), _identity()) == []
    for field in IDENTITY_FIELDS:
        assert identity_mismatch(_identity(**{field: _OTHER[field]}), _identity()) == [field]
    absent = _identity(**dict.fromkeys(_SERVED))
    assert identity_mismatch(absent, _identity()) == list(_SERVED)
    assert identity_mismatch(_identity(), absent) == list(_SERVED)


def test_require_identity_names_every_differing_field_and_its_variable():
    require_identity(_identity(), _identity(), subject="s", remedy="r")
    with pytest.raises(EmbeddingModelMismatchError) as error:
        drifted = _identity(model="other", pooling="mean")
        require_identity(drifted, _identity(), subject="the space 'x'", remedy="fix it")
    message = str(error.value)
    assert message.startswith("the space 'x' records ")
    assert message.endswith("; fix it")
    assert "KNOWLEDGE_BASE_EMBED_MODEL" in message
    assert "KNOWLEDGE_BASE_EMBED_POOLING" in message
    with pytest.raises(EmbeddingModelMismatchError, match="KB declares 'float32'"):
        require_identity(_identity(element_type="int8"), _identity(), subject="s", remedy="r")


@pytest.mark.parametrize(
    ("values", "routes"),
    [
        (_OLLAMA, True),
        ({"provider": "openai", "model": "m", "dim": "8", "url": "https://api.openai.com"}, True),
        ({"provider": "openai", "model": "m", "dim": "8", "url": "https://api.openai.com/v1/"}, True),
        ({"provider": "openai", "model": "m", "dim": "8", "url": "http://api.openai.com"}, False),
        ({"provider": "openai", "model": "m", "dim": "8", "url": "https://api.openai.com:8443"}, False),
        ({"provider": "openai", "model": "m", "dim": "8", "url": "https://api.openai.com/proxy"}, False),
        ({"provider": "openai", "model": "m", "dim": "8", "url": "https://embedder.example.test"}, False),
        ({**_OPENAI_COMPAT, "url": "https://embedder.example.test"}, False),
    ],
)
def test_only_a_model_routing_endpoint_serves_other_models(declare, values, routes):
    declare(**values)
    settings = load_embed_settings()
    assert routes_by_model(settings) is routes
    space = {**declared_identity(settings), "model": "another-model", "dim": 8, "matryoshka_base_dim": 16}
    serving = serving_identity(settings, space)
    assert serving["model"] == ("another-model" if routes else settings.model)
    assert (serving["dim"], serving["matryoshka_base_dim"]) == (8, 16)
    assert {field: serving[field] for field in ("provider", "element_type", *_SERVED)} == {
        field: declared_identity(settings)[field] for field in ("provider", "element_type", *_SERVED)
    }


def test_onnx_never_routes_by_model(declare, tmp_path, monkeypatch):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"")
    monkeypatch.setenv("ONNX_EMBED_MODEL_PATH", str(model))
    declare(provider="onnx", model="local", dim="8")
    assert routes_by_model(load_embed_settings()) is False


def test_the_batch_size_defaults_to_one_input_per_request(declare):
    declare(**_OPENAI_COMPAT, url="https://embedder.example.test")
    assert load_embed_settings().batch_size == 1


@pytest.mark.parametrize("provider", ["openai_compat", "openai", "llama.cpp"])
def test_the_batch_size_is_declared_for_the_openai_wire(declare, provider):
    declare(provider=provider, model="m", dim="8", url="https://embedder.example.test", batch_size="8")
    assert load_embed_settings().batch_size == 8


@pytest.mark.parametrize("value", ["0", "-1", "x", "1.5", "٣"])
def test_an_invalid_batch_size_names_its_variable(declare, value):
    declare(**_OPENAI_COMPAT, url="https://embedder.example.test", batch_size=value)
    with pytest.raises(ValidationError, match=r"^KNOWLEDGE_BASE_EMBED_BATCH_SIZE"):
        load_embed_settings()


@pytest.mark.parametrize(
    "values",
    [_OLLAMA, {"provider": "onnx", "model": "local", "dim": "8"}],
    ids=["ollama", "onnx"],
)
def test_a_batch_size_is_refused_where_it_would_do_nothing(declare, values, tmp_path, monkeypatch):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"")
    monkeypatch.setenv("ONNX_EMBED_MODEL_PATH", str(model))
    declare(**values, batch_size="4")
    with pytest.raises(ValidationError, match=r"^KNOWLEDGE_BASE_EMBED_BATCH_SIZE") as error:
        load_embed_settings()
    assert values["provider"] in str(error.value)
