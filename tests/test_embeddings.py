"""Tests for embedding provider abstraction."""

from __future__ import annotations

import logging
import math
from unittest.mock import MagicMock, patch

import pytest

from knowledge_base.embeddings import (
    EmbeddingProvider,
    OllamaProvider,
    embed,
    embed_single,
    get_provider,
)


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


class TestEmbedDispatch:
    """Test that module-level embed()/embed_single() dispatch through providers."""

    @patch("knowledge_base.embeddings.get_provider")
    def test_embed_dispatches_to_named_provider(self, mock_get):
        mock_provider = MagicMock()
        mock_provider.embed.return_value = [[0.1, 0.2, 0.3]]
        mock_get.return_value = mock_provider

        result = embed(
            ["hello"], model="bge-m3", expected_dim=3, _provider_name="openai"
        )

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


class TestL2Normalize:
    def test_zero_vector_warns(self, caplog):
        """Zero vectors should log a warning (indicates upstream problem)."""
        from knowledge_base.embeddings import _l2_normalize

        with caplog.at_level(logging.WARNING):
            result = _l2_normalize([0.0, 0.0, 0.0])
        assert result == [0.0, 0.0, 0.0]
        assert "zero" in caplog.text.lower()
