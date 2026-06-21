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
