"""Tests for embedding provider abstraction."""

from __future__ import annotations

import logging
import math
from unittest.mock import MagicMock, patch

import pytest

from knowledge_base.embeddings import (
    EmbeddingProvider,
    OllamaProvider,
    _provider_cache,
    embed,
    embed_single,
    get_provider,
)


@pytest.fixture(autouse=True)
def _clear_provider_cache():
    """Ensure provider cache is fresh for each test."""
    _provider_cache.clear()
    yield
    _provider_cache.clear()


class TestOllamaProvider:
    """Test OllamaProvider implements the Protocol correctly."""

    def test_implements_protocol(self):
        provider = OllamaProvider()
        assert isinstance(provider, EmbeddingProvider)

    @patch("knowledge_base.embeddings.httpx.post")
    def test_embed_batch(self, mock_post):
        raw = [1.0, 0.0, 0.0]
        mock_post.return_value = MagicMock(
            json=lambda: {"embeddings": [raw]},
            raise_for_status=lambda: None,
        )
        provider = OllamaProvider()
        result = provider.embed(["hello"], model="bge-m3", expected_dim=3)
        assert len(result) == 1
        assert result[0] is not None
        assert len(result[0]) == 3

    @patch("knowledge_base.embeddings.httpx.post")
    def test_embed_normalizes_vectors(self, mock_post):
        # Non-unit vector — provider should L2-normalize
        raw = [3.0, 4.0, 0.0]  # norm = 5.0
        mock_post.return_value = MagicMock(
            json=lambda: {"embeddings": [raw]},
            raise_for_status=lambda: None,
        )
        provider = OllamaProvider()
        result = provider.embed(["hello"], model="bge-m3", expected_dim=3)
        assert result[0] is not None
        norm = math.sqrt(sum(x * x for x in result[0]))
        assert abs(norm - 1.0) < 1e-6

    @patch("knowledge_base.embeddings.httpx.post")
    def test_embed_rejects_wrong_dim(self, mock_post):
        mock_post.return_value = MagicMock(
            json=lambda: {"embeddings": [[0.1, 0.2]]},
            raise_for_status=lambda: None,
        )
        provider = OllamaProvider()
        with pytest.raises(ValueError, match="Expected 3 dims"):
            provider.embed(["hello"], model="bge-m3", expected_dim=3)

    @patch("knowledge_base.embeddings.httpx.post")
    def test_embed_batches_large_inputs(self, mock_post):
        """Inputs > 32 texts should be batched."""

        def _mock_response(*args, **kwargs):
            n = len(kwargs.get("json", {}).get("input", []))
            return MagicMock(
                json=lambda: {"embeddings": [[0.1] * 3] * n},
                raise_for_status=lambda: None,
            )

        mock_post.side_effect = _mock_response
        provider = OllamaProvider()
        texts = [f"text {i}" for i in range(50)]
        result = provider.embed(texts, model="bge-m3", expected_dim=3)
        assert len(result) == 50
        # Should have made 2 HTTP calls (32 + 18)
        assert mock_post.call_count == 2


class TestGetProvider:
    def test_returns_ollama_by_default(self):
        provider = get_provider("ollama")
        assert isinstance(provider, OllamaProvider)

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown embedding provider"):
            get_provider("nonexistent")

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("EMBED_PROVIDER", "ollama")
        provider = get_provider("nonexistent_ignored")
        assert isinstance(provider, OllamaProvider)

    def test_env_var_override_disabled(self, monkeypatch):
        """allow_env_override=False ignores EMBED_PROVIDER."""
        monkeypatch.setenv("EMBED_PROVIDER", "ollama")
        with pytest.raises(ValueError, match="Unknown embedding provider 'nonexistent'"):
            get_provider("nonexistent", allow_env_override=False)

    def test_provider_caching(self):
        """get_provider returns the same instance for the same name."""
        p1 = get_provider("ollama")
        p2 = get_provider("ollama")
        assert p1 is p2

    def test_provider_caching_different_names(self):
        """Different provider names get different instances."""
        p_ollama = get_provider("ollama")
        p_openai = get_provider("openai")
        assert p_ollama is not p_openai


class TestOpenAICompatProvider:
    """OpenAICompatProvider reaches any OpenAI-compatible backend via base_url (AC1)."""

    @pytest.fixture(autouse=True)
    def _allow_public(self):
        # These tests target embed mechanics, not SSRF; keep them offline.
        with patch("knowledge_base.utils.is_private_ip", return_value=False):
            yield

    def test_embeds_against_base_url(self):
        from knowledge_base.embeddings import OpenAICompatProvider

        captured = {}

        def _mock_post(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)

            class FakeResp:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}]}

            return FakeResp()

        provider = OpenAICompatProvider(base_url="http://vllm.example.com", api_key="k")
        with patch("knowledge_base.embeddings.httpx.post", _mock_post):
            result = provider.embed(["hello"], model="bge-m3", expected_dim=3)

        assert captured["url"] == "http://vllm.example.com/v1/embeddings"
        assert captured["headers"]["Authorization"] == "Bearer k"
        assert captured["follow_redirects"] is False
        assert captured["json"]["dimensions"] == 3
        # L2-normalized
        assert result[0] is not None
        assert abs(sum(x * x for x in result[0]) - 1.0) < 1e-6

    def test_strips_v1_suffix(self):
        from knowledge_base.embeddings import OpenAICompatProvider

        captured = {}

        def _mock_post(url, **kwargs):
            captured["url"] = url

            class FakeResp:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}

            return FakeResp()

        provider = OpenAICompatProvider(base_url="http://host/v1", api_key=None)
        with patch("knowledge_base.embeddings.httpx.post", _mock_post):
            provider.embed(["x"], model="m", expected_dim=2)
        # normalize-first means no /v1/v1 doubling
        assert captured["url"] == "http://host/v1/embeddings"

    def test_no_auth_header_when_key_none(self):
        from knowledge_base.embeddings import OpenAICompatProvider

        captured = {}

        def _mock_post(url, **kwargs):
            captured.update(kwargs)

            class FakeResp:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}

            return FakeResp()

        provider = OpenAICompatProvider(base_url="http://local-tei.example.com", api_key=None)
        with patch("knowledge_base.embeddings.httpx.post", _mock_post):
            provider.embed(["x"], model="m", expected_dim=2)
        assert "Authorization" not in captured.get("headers", {})

    def test_resolves_env_api_key_at_call_time(self, monkeypatch):
        from knowledge_base.embeddings import OpenAICompatProvider

        monkeypatch.setenv("KB_VLLM_KEY", "sk-from-env")
        captured = {}

        def _mock_post(url, **kwargs):
            captured.update(kwargs)

            class FakeResp:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}

            return FakeResp()

        # The raw spec 'env:KB_VLLM_KEY' is stored; the secret is resolved only at call time.
        provider = OpenAICompatProvider(base_url="http://vllm.example.com", api_key="env:KB_VLLM_KEY")
        with patch("knowledge_base.embeddings.httpx.post", _mock_post):
            provider.embed(["x"], model="m", expected_dim=2)
        assert captured["headers"]["Authorization"] == "Bearer sk-from-env"

    def test_call_time_guard_rejects_private_base_url(self):
        from knowledge_base.exceptions import ValidationError

        # Override the autouse public mock for this one: a genuinely private host must reject.
        from knowledge_base.embeddings import OpenAICompatProvider

        provider = OpenAICompatProvider(base_url="http://169.254.169.254", api_key="k")
        with (
            patch("knowledge_base.utils.is_private_ip", return_value=True),
            pytest.raises(ValidationError, match="private"),
        ):
            provider.embed(["x"], model="m", expected_dim=2)

    def test_loopback_requires_optin(self):
        from knowledge_base.embeddings import OpenAICompatProvider
        from knowledge_base.exceptions import ValidationError

        # is_private_ip un-mocked path: localhost rejected without opt-in, accepted with it.
        with patch("knowledge_base.utils.is_private_ip", return_value=True):
            no_optin = OpenAICompatProvider(base_url="http://localhost:11434", api_key=None)
            with pytest.raises(ValidationError, match="private"):
                no_optin.embed(["x"], model="m", expected_dim=2)

        captured = {}

        def _mock_post(url, **kwargs):
            captured["url"] = url

            class FakeResp:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}

            return FakeResp()

        with (
            patch("knowledge_base.utils.is_private_ip", return_value=True),
            patch("knowledge_base.embeddings.httpx.post", _mock_post),
        ):
            optin = OpenAICompatProvider(base_url="http://localhost:11434/v1", api_key=None, allow_loopback=True)
            optin.embed(["x"], model="m", expected_dim=2)
        assert captured["url"] == "http://localhost:11434/v1/embeddings"


class TestProviderConfigCache:
    """The frozen-ProviderConfig cache key fixes the openai_compat name-collision bug (AC1)."""

    def test_distinct_base_urls_do_not_collide(self):
        from knowledge_base.embeddings import OpenAICompatProvider, ProviderConfig

        cfg_a = ProviderConfig(family="openai_compat", base_url="http://host-a.example.com", api_key="k")
        cfg_b = ProviderConfig(family="openai_compat", base_url="http://host-b.example.com", api_key="k")
        pa = get_provider(cfg=cfg_a)
        pb = get_provider(cfg=cfg_b)
        assert pa is not pb
        assert isinstance(pa, OpenAICompatProvider)
        assert isinstance(pb, OpenAICompatProvider)
        assert pa._base_url == "http://host-a.example.com"
        assert pb._base_url == "http://host-b.example.com"

    def test_same_config_reuses_instance(self):
        from knowledge_base.embeddings import ProviderConfig

        cfg = ProviderConfig(family="openai_compat", base_url="http://host.example.com", api_key="k")
        assert get_provider(cfg=cfg) is get_provider(cfg=cfg)

    def test_provider_config_repr_redacts_key(self):
        from knowledge_base.embeddings import ProviderConfig

        cfg = ProviderConfig(family="openai_compat", base_url="https://u:secret@host.example.com", api_key="sk-xyz")
        text = repr(cfg)
        assert "sk-xyz" not in text
        assert "secret" not in text


class TestEmbedDispatch:
    """Test that module-level embed()/embed_single() dispatch through providers."""

    @patch("knowledge_base.embeddings.get_provider")
    def test_embed_dispatches_to_named_provider(self, mock_get):
        mock_provider = MagicMock()
        mock_provider.embed.return_value = [[0.1, 0.2, 0.3]]
        mock_get.return_value = mock_provider

        result = embed(["hello"], model="bge-m3", expected_dim=3, _provider_name="openai")

        mock_get.assert_called_once_with("openai")
        mock_provider.embed.assert_called_once()
        assert result == [[0.1, 0.2, 0.3]]

    @patch("knowledge_base.embeddings.get_provider")
    def test_embed_defaults_to_ollama(self, mock_get):
        mock_provider = MagicMock()
        mock_provider.embed.return_value = [[0.1] * 3]
        mock_get.return_value = mock_provider

        embed(["hello"])
        mock_get.assert_called_once_with("ollama")

    @patch("knowledge_base.embeddings.get_provider")
    def test_embed_single_dispatches(self, mock_get):
        mock_provider = MagicMock()
        mock_provider.embed.return_value = [[0.1, 0.2, 0.3]]
        mock_get.return_value = mock_provider

        result = embed_single("hello", model="bge-m3", _provider_name="openai")
        assert result == [0.1, 0.2, 0.3]

    @patch("knowledge_base.embeddings.get_provider")
    def test_embed_single_passes_expected_dim(self, mock_get):
        mock_provider = MagicMock()
        mock_provider.embed.return_value = [[0.1, 0.2, 0.3]]
        mock_get.return_value = mock_provider

        embed_single("hello", model="bge-m3", expected_dim=3, _provider_name="ollama")
        _, kwargs = mock_provider.embed.call_args
        assert kwargs["expected_dim"] == 3


class TestOpenAIProvider:
    @pytest.fixture(autouse=True)
    def _set_api_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        # OpenAICompatProvider.embed now runs a call-time validate_base_url; the OpenAI
        # literal host is public but resolving it would hit the network — keep offline.
        with patch("knowledge_base.utils.is_private_ip", return_value=False):
            yield

    def test_implements_protocol(self):
        from knowledge_base.embeddings import OpenAIProvider

        provider = OpenAIProvider()
        assert isinstance(provider, EmbeddingProvider)

    @patch("knowledge_base.embeddings.OpenAIProvider._call_api")
    def test_embed_batch(self, mock_api):
        from knowledge_base.embeddings import OpenAIProvider

        mock_api.return_value = [[1.0, 0.0, 0.0]]
        provider = OpenAIProvider()
        result = provider.embed(["hello"], model="text-embedding-3-large", expected_dim=3)
        assert len(result) == 1
        assert result[0] is not None
        assert len(result[0]) == 3

    @patch("knowledge_base.embeddings.OpenAIProvider._call_api")
    def test_embed_normalizes(self, mock_api):
        from knowledge_base.embeddings import OpenAIProvider

        mock_api.return_value = [[3.0, 4.0, 0.0]]
        provider = OpenAIProvider()
        result = provider.embed(["hello"], model="text-embedding-3-large", expected_dim=3)
        assert result[0] is not None
        norm = math.sqrt(sum(x * x for x in result[0]))
        assert abs(norm - 1.0) < 1e-6

    @patch("knowledge_base.embeddings.OpenAIProvider._call_api")
    def test_embed_rejects_wrong_dim(self, mock_api):
        from knowledge_base.embeddings import OpenAIProvider

        mock_api.return_value = [[0.1, 0.2]]
        provider = OpenAIProvider()
        with pytest.raises(ValueError, match="Expected 3 dims"):
            provider.embed(["hello"], model="text-embedding-3-large", expected_dim=3)

    def test_missing_api_key_raises(self, monkeypatch):
        from knowledge_base.embeddings import OpenAIProvider

        monkeypatch.delenv("OPENAI_API_KEY")
        provider = OpenAIProvider()
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            provider.embed(["hello"], model="text-embedding-3-large")

    def test_get_provider_returns_openai(self):
        from knowledge_base.embeddings import OpenAIProvider

        provider = get_provider("openai")
        assert isinstance(provider, OpenAIProvider)


class TestONNXProvider:
    def test_implements_protocol(self):
        from knowledge_base.embeddings import ONNXProvider

        provider = ONNXProvider()
        assert isinstance(provider, EmbeddingProvider)

    @patch("knowledge_base.embeddings.ONNXProvider._get_session")
    def test_embed_batch(self, mock_session_fn):
        np = pytest.importorskip("numpy")
        from knowledge_base.embeddings import ONNXProvider

        mock_session = MagicMock()
        mock_session.run.return_value = [np.array([[1.0, 0.0, 0.0]])]
        mock_session_fn.return_value = mock_session
        provider = ONNXProvider()
        result = provider.embed(["hello"], model="bge-m3", expected_dim=3)
        assert len(result) == 1
        assert result[0] is not None
        assert len(result[0]) == 3

    @patch("knowledge_base.embeddings.ONNXProvider._get_session")
    def test_embed_normalizes(self, mock_session_fn):
        np = pytest.importorskip("numpy")
        from knowledge_base.embeddings import ONNXProvider

        mock_session = MagicMock()
        mock_session.run.return_value = [np.array([[3.0, 4.0, 0.0]])]
        mock_session_fn.return_value = mock_session
        provider = ONNXProvider()
        result = provider.embed(["hello"], model="bge-m3", expected_dim=3)
        assert result[0] is not None
        norm = math.sqrt(sum(x * x for x in result[0]))
        assert abs(norm - 1.0) < 1e-6

    def test_missing_model_path_raises(self, monkeypatch):
        from knowledge_base.embeddings import ONNXProvider

        monkeypatch.delenv("ONNX_EMBED_MODEL_PATH", raising=False)
        provider = ONNXProvider()
        with pytest.raises(RuntimeError, match="ONNX_EMBED_MODEL_PATH"):
            provider._get_session("bge-m3")

    def test_get_provider_returns_onnx(self):
        from knowledge_base.embeddings import ONNXProvider

        provider = get_provider("onnx")
        assert isinstance(provider, ONNXProvider)


class TestTruncateEmbedding:
    def test_truncate_embedding(self):
        from knowledge_base.embeddings import truncate_embedding

        vec = [0.5, 0.5, 0.5, 0.5]  # unit-ish vector
        result = truncate_embedding(vec, 2)
        assert len(result) == 2
        # Should be L2-normalized
        norm = math.sqrt(sum(x * x for x in result))
        assert abs(norm - 1.0) < 1e-6


class TestL2Normalize:
    def test_zero_vector_raises(self):
        """Zero-norm vectors must raise ZeroNormError, not silently pass through."""
        from knowledge_base.embeddings import ZeroNormError, _l2_normalize

        with pytest.raises(ZeroNormError):
            _l2_normalize([0.0, 0.0, 0.0])

    def test_normal_vector_unchanged(self):
        """Non-zero vectors should still normalize correctly."""
        from knowledge_base.embeddings import _l2_normalize

        result = _l2_normalize([3.0, 4.0, 0.0])
        norm = math.sqrt(sum(x * x for x in result))
        assert abs(norm - 1.0) < 1e-6


class TestProviderZeroNormHandling:
    """Providers must return None for embeddings that produce zero-norm vectors."""

    @patch("knowledge_base.embeddings.httpx.post")
    def test_ollama_zero_norm_returns_none(self, mock_post):
        """OllamaProvider returns None for zero-norm embeddings in a batch."""
        mock_post.return_value = MagicMock(
            json=lambda: {"embeddings": [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 1.0, 0.0]]},
            raise_for_status=lambda: None,
        )
        provider = OllamaProvider()
        result = provider.embed(["good", "bad", "good2"], model="bge-m3", expected_dim=3)
        assert len(result) == 3
        assert result[0] is not None
        assert result[1] is None
        assert result[2] is not None

    @patch("knowledge_base.embeddings.httpx.post")
    def test_ollama_zero_norm_logs_warning(self, mock_post, caplog):
        """Zero-norm embeddings should log a warning with context."""
        mock_post.return_value = MagicMock(
            json=lambda: {"embeddings": [[0.0, 0.0, 0.0]]},
            raise_for_status=lambda: None,
        )
        provider = OllamaProvider()
        with caplog.at_level(logging.WARNING):
            result = provider.embed(["empty"], model="bge-m3", expected_dim=3)
        assert result[0] is None
        assert "zero" in caplog.text.lower()


# --- Slice review fixes: OllamaProvider honors a configured base_url (#524 review) ---


class TestOllamaBaseUrl:
    def test_honors_configured_base_url(self):
        from knowledge_base.embeddings import OllamaProvider

        captured = {}

        def _mock_post(url, **kwargs):
            captured["url"] = url

            class FakeResp:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"embeddings": [[1.0, 0.0, 0.0]]}

            return FakeResp()

        provider = OllamaProvider(base_url="http://remote-ollama:11434")
        with patch("knowledge_base.embeddings.httpx.post", _mock_post):
            provider.embed(["x"], model="bge-m3", expected_dim=3)
        assert captured["url"] == "http://remote-ollama:11434/api/embed"

    def test_falls_back_to_autodetect_without_base_url(self):
        from knowledge_base.embeddings import OllamaProvider

        captured = {}

        def _mock_post(url, **kwargs):
            captured["url"] = url

            class FakeResp:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"embeddings": [[1.0, 0.0, 0.0]]}

            return FakeResp()

        with (
            patch("knowledge_base.embeddings._get_ollama_url", return_value="http://auto:11434"),
            patch("knowledge_base.embeddings.httpx.post", _mock_post),
        ):
            OllamaProvider().embed(["x"], model="bge-m3", expected_dim=3)
        assert captured["url"] == "http://auto:11434/api/embed"
