"""Tests for embedding provider abstraction."""

from __future__ import annotations

import logging
import math
from unittest.mock import MagicMock, patch

import pytest

from knowledge_base.embeddings import (
    _TURNS,
    EmbeddingProvider,
    OllamaProvider,
    ProviderConfig,
    _provider_cache,
    embed,
    embed_query,
    get_provider,
)


@pytest.fixture(autouse=True)
def _clear_provider_cache():
    """Ensure provider cache and the endpoints' turn queues are fresh for each test."""
    _provider_cache.clear()
    _TURNS.clear()
    yield
    _provider_cache.clear()
    _TURNS.clear()


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

    def test_embed_provider_env_no_longer_selects(self, monkeypatch):
        """#569: the deprecated EMBED_PROVIDER is refused at startup and never read here."""
        monkeypatch.setenv("EMBED_PROVIDER", "ollama")
        with pytest.raises(ValueError, match="Unknown embedding provider 'nonexistent'"):
            get_provider("nonexistent")

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

    @pytest.fixture
    def server(self, http_requests):
        """Answer every embeddings request with one 2-dim vector per input; ``vector`` sets it."""
        import json

        import httpx

        def answer(request: httpx.Request) -> httpx.Response:
            texts = json.loads(request.content)["input"]
            data = [{"index": i, "embedding": server.vector} for i in range(len(texts))]
            return httpx.Response(200, json={"data": data})

        def register(host: str) -> None:
            http_requests.handlers[host] = answer

        server = http_requests
        server.vector = [1.0, 0.0]
        server.register = register
        return server

    def test_embeds_against_base_url(self, server):
        import json

        from knowledge_base.embeddings import OpenAICompatProvider

        server.register("vllm.example.com")
        server.vector = [1.0, 0.0, 0.0]
        provider = OpenAICompatProvider(base_url="http://vllm.example.com", api_key="k")
        result = provider.embed(["hello"], model="bge-m3", expected_dim=3)

        (request,) = server
        assert str(request.url) == "http://vllm.example.com/v1/embeddings"
        assert request.headers["Authorization"] == "Bearer k"
        assert json.loads(request.content)["dimensions"] == 3
        # L2-normalized
        assert result[0] is not None
        assert abs(sum(x * x for x in result[0]) - 1.0) < 1e-6

    def test_a_redirect_is_not_followed(self, server):
        import httpx

        from knowledge_base.embeddings import OpenAICompatProvider

        server.handlers["vllm.example.com"] = lambda request: httpx.Response(
            307, headers={"Location": "http://elsewhere.example.com/v1/embeddings"}
        )
        provider = OpenAICompatProvider(base_url="http://vllm.example.com", api_key="k")
        with pytest.raises(httpx.HTTPStatusError):
            provider.embed(["hello"], model="m", expected_dim=2)
        assert [request.url.host for request in server] == ["vllm.example.com"]

    def test_strips_v1_suffix(self, server):
        from knowledge_base.embeddings import OpenAICompatProvider

        server.register("host")
        provider = OpenAICompatProvider(base_url="http://host/v1", api_key=None)
        provider.embed(["x"], model="m", expected_dim=2)
        # normalize-first means no /v1/v1 doubling
        (request,) = server
        assert str(request.url) == "http://host/v1/embeddings"

    def test_no_auth_header_when_key_none(self, server):
        from knowledge_base.embeddings import OpenAICompatProvider

        server.register("local-tei.example.com")
        provider = OpenAICompatProvider(base_url="http://local-tei.example.com", api_key=None)
        provider.embed(["x"], model="m", expected_dim=2)
        (request,) = server
        assert "Authorization" not in request.headers

    def test_resolves_env_api_key_at_call_time(self, server, monkeypatch):
        from knowledge_base.embeddings import OpenAICompatProvider

        monkeypatch.setenv("KB_VLLM_KEY", "sk-from-env")
        server.register("vllm.example.com")
        # The raw spec 'env:KB_VLLM_KEY' is stored; the secret is resolved only at call time.
        provider = OpenAICompatProvider(base_url="http://vllm.example.com", api_key="env:KB_VLLM_KEY")
        provider.embed(["x"], model="m", expected_dim=2)
        (request,) = server
        assert request.headers["Authorization"] == "Bearer sk-from-env"

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

    def test_loopback_requires_optin(self, server):
        from knowledge_base.embeddings import OpenAICompatProvider
        from knowledge_base.exceptions import ValidationError

        # is_private_ip un-mocked path: localhost rejected without opt-in, accepted with it.
        with patch("knowledge_base.utils.is_private_ip", return_value=True):
            no_optin = OpenAICompatProvider(base_url="http://localhost:11434", api_key=None)
            with pytest.raises(ValidationError, match="private"):
                no_optin.embed(["x"], model="m", expected_dim=2)

        server.register("localhost")
        with patch("knowledge_base.utils.is_private_ip", return_value=True):
            optin = OpenAICompatProvider(base_url="http://localhost:11434/v1", api_key=None, allow_loopback=True)
            optin.embed(["x"], model="m", expected_dim=2)
        (request,) = server
        assert str(request.url) == "http://localhost:11434/v1/embeddings"

    def test_every_request_reuses_the_kept_alive_connection(self, server, monkeypatch):
        import httpx

        from knowledge_base.embeddings import OpenAICompatProvider

        transports: list = []  # the objects themselves, so a collected one cannot lend its id to the next
        answer = httpx.HTTPTransport.handle_request  # the http_requests fixture's stand-in

        def recording(transport, request):
            transports.append(transport)
            return answer(transport, request)

        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", recording)
        server.register("vllm.example.com")
        OpenAICompatProvider(base_url="http://vllm.example.com").embed(["a 0", "a 1", "a 2"], model="m")
        assert [request.url.path for request in server] == ["/v1/embeddings"] * 3
        assert len({id(transport) for transport in transports}) == 1


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

    def test_configs_differing_only_in_batch_size_get_their_own_provider(self):
        from knowledge_base.embeddings import ProviderConfig

        cfg = ProviderConfig(family="openai_compat", base_url="http://host.example.com", api_key="k")
        wide = ProviderConfig(family="openai_compat", base_url="http://host.example.com", api_key="k", batch_size=8)
        assert get_provider(cfg=cfg) is not get_provider(cfg=wide)
        assert get_provider(cfg=wide)._batch_size == 8  # type: ignore[attr-defined]

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
    """Test that module-level embed()/embed_query() dispatch through providers."""

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
    def test_embed_query_sends_the_instruction_then_the_text(self, mock_get):
        mock_provider = MagicMock()
        mock_provider.embed.return_value = [[0.1, 0.2, 0.3]]
        mock_get.return_value = mock_provider

        cfg = ProviderConfig(family="ollama", base_url="http://embedder.example.test:11434")
        result = embed_query("hello", "Query: ", model="bge-m3", expected_dim=3, _provider_cfg=cfg)
        assert result == [0.1, 0.2, 0.3]
        args, kwargs = mock_provider.embed.call_args
        assert args == (["Query: hello"],)
        assert kwargs["expected_dim"] == 3

    @patch("knowledge_base.embeddings.get_provider")
    def test_embed_sends_documents_unchanged(self, mock_get):
        mock_provider = MagicMock()
        mock_provider.embed.return_value = [[0.1, 0.2, 0.3]]
        mock_get.return_value = mock_provider

        embed(["hello"], model="bge-m3")
        args, _ = mock_provider.embed.call_args
        assert args == (["hello"],)


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
        assert result is not None
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


class TestNoImplicitEndpoint:
    """#569: no embedding provider is built without an explicit base URL."""

    @pytest.mark.parametrize("base_url", [None, ""])
    def test_openai_compat_without_base_url_raises(self, base_url):
        from knowledge_base.embeddings import OpenAICompatProvider
        from knowledge_base.exceptions import ValidationError

        with pytest.raises(ValidationError, match="base URL"):
            OpenAICompatProvider(base_url)

    @pytest.mark.parametrize("family", ["openai_compat", "openai", "ollama"])
    def test_config_path_without_base_url_reaches_nobody(self, family, http_requests):
        from knowledge_base.embeddings import ProviderConfig
        from knowledge_base.exceptions import ValidationError

        with (
            patch("knowledge_base.utils.is_private_ip", return_value=False),
            patch("knowledge_base.embeddings._get_ollama_url") as ollama_url,
            pytest.raises(ValidationError, match="base URL"),
        ):
            embed(["x"], model="m", expected_dim=3, _provider_cfg=ProviderConfig(family=family))
        assert http_requests == []
        ollama_url.assert_not_called()

    def test_explicit_openai_provider_is_unchanged(self):
        from knowledge_base.embeddings import OpenAIProvider

        assert OpenAIProvider()._base_url == "https://api.openai.com"
        bare = get_provider("openai")
        assert isinstance(bare, OpenAIProvider)
        assert bare._base_url == "https://api.openai.com"


class TestDeclaredEndpointExemption:
    """#569: the declared host is exempt from the SSRF floor on the embedding path only."""

    @pytest.fixture
    def declared(self, monkeypatch, fake_dns, tmp_path):
        from knowledge_base.db import open_declared_db

        tailnet = ["100.64.0.10", "fd7a:115c:a1e0::10"]
        fake_dns["embedder.example.test"] = tailnet
        fake_dns["other.example.test"] = tailnet
        for name, value in {
            "KNOWLEDGE_BASE_EMBED_PROVIDER": "openai_compat",
            "KNOWLEDGE_BASE_EMBED_MODEL": "qwen3-embedding",
            "KNOWLEDGE_BASE_EMBED_DIM": "8",
            "KNOWLEDGE_BASE_EMBED_URL": "https://embedder.example.test",
            "KNOWLEDGE_BASE_EMBED_API_KEY": "sk-test-only",
        }.items():
            monkeypatch.setenv(name, value)
        conn = open_declared_db(tmp_path / "kb.db")  # no configure_* call anywhere
        yield conn
        conn.close()

    def test_a_blank_database_embeds_against_the_declared_endpoint(self, declared, http_requests):
        import httpx

        from knowledge_base.ingest import _embed_with_config

        http_requests.handlers["embedder.example.test"] = lambda request: httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [1.0] + [0.0] * 7}]}
        )
        vectors = _embed_with_config(declared, ["hello"])
        assert len(vectors) == 1
        assert [str(r.url) for r in http_requests] == ["https://embedder.example.test/v1/embeddings"]
        assert http_requests[0].headers["Authorization"] == "Bearer sk-test-only"

    def test_another_tailnet_host_keeps_the_floor(self, declared, http_requests):
        from knowledge_base.embeddings import OpenAICompatProvider
        from knowledge_base.exceptions import ValidationError

        with pytest.raises(ValidationError, match="private"):
            OpenAICompatProvider("https://other.example.test").embed(["x"], model="m", expected_dim=8)
        assert http_requests == []

    def test_tool_urls_on_the_declared_host_keep_the_floor(self, declared, http_requests):
        from knowledge_base.exceptions import ValidationError
        from knowledge_base.llm import configure_llm
        from knowledge_base.web import _validate_image_url, ingest_url

        with pytest.raises(ValidationError, match="private"):
            ingest_url(declared, "https://embedder.example.test/doc")
        assert _validate_image_url("https://embedder.example.test/a.png") is False
        with pytest.raises(ValidationError, match="private"):
            configure_llm(declared, "openai_compat", "https://embedder.example.test")
        assert http_requests == []


def test_the_key_comes_from_the_declaration_not_the_config_row(monkeypatch, fake_dns, tmp_path, http_requests):
    """#569: a foreign key row written by another process never reaches the request."""
    import httpx

    from knowledge_base.db import open_declared_db
    from knowledge_base.ingest import _embed_with_config

    fake_dns["embedder.example.test"] = ["100.64.0.10"]
    for name, value in {
        "KNOWLEDGE_BASE_EMBED_PROVIDER": "openai_compat",
        "KNOWLEDGE_BASE_EMBED_MODEL": "qwen3-embedding",
        "KNOWLEDGE_BASE_EMBED_DIM": "8",
        "KNOWLEDGE_BASE_EMBED_URL": "https://embedder.example.test",
        "KNOWLEDGE_BASE_EMBED_API_KEY": "sk-test-only",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("OTHER_SECRET", "sk-foreign")
    conn = open_declared_db(tmp_path / "kb.db")
    conn.execute("UPDATE config SET value = 'env:OTHER_SECRET' WHERE key LIKE 'embed_api%'")
    conn.commit()
    http_requests.handlers["embedder.example.test"] = lambda request: httpx.Response(
        200, json={"data": [{"index": 0, "embedding": [1.0] + [0.0] * 7}]}
    )
    _embed_with_config(conn, ["hello"])
    conn.close()
    assert http_requests[0].headers["Authorization"] == "Bearer sk-test-only"


class TestLlamaCppProvider:
    """#573: ADR-0015 §5 pins provider ``llama.cpp``; it speaks the openai_compat wire."""

    def test_the_declaration_accepts_llama_cpp(self, monkeypatch):
        from knowledge_base.settings import load_embed_settings

        monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_PROVIDER", "llama.cpp")
        monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_URL", "https://embedder.example.test/v1/")
        declared = load_embed_settings()
        assert (declared.provider, declared.base_url) == ("llama.cpp", "https://embedder.example.test")

    def test_llama_cpp_posts_the_openai_compat_body(self, monkeypatch, fake_dns, http_requests):
        import json

        import httpx

        from knowledge_base.embeddings import OpenAICompatProvider

        monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_URL", "https://embedder.example.test")
        monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_API_KEY", "sk-test-only")
        fake_dns["embedder.example.test"] = ["100.64.0.10"]
        http_requests.handlers["embedder.example.test"] = lambda request: httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [1.0] + [0.0] * 7}]}
        )
        cfg = ProviderConfig(
            family="llama.cpp", base_url="https://embedder.example.test", api_key="env:KNOWLEDGE_BASE_EMBED_API_KEY"
        )
        provider = get_provider(cfg=cfg)
        assert isinstance(provider, OpenAICompatProvider)
        assert provider.embed(["hello"], model="Qwen/Qwen3-Embedding-0.6B", expected_dim=8) == [[1.0] + [0.0] * 7]
        (request,) = http_requests
        assert str(request.url) == "https://embedder.example.test/v1/embeddings"
        assert request.headers["Authorization"] == "Bearer sk-test-only"
        body = {"model": "Qwen/Qwen3-Embedding-0.6B", "input": ["hello"], "dimensions": 8}
        assert json.loads(request.content) == body


# --- #604: every input bounded by max_input_tokens, counted as the server counts ---


def _words(text: str) -> int:
    """The acceptance stub's tokenizer: one token per whitespace-separated word, plus the EOS the server appends."""
    return len(text.split()) + 1


class TestSplitToLimit:
    def _check(self, text: str, limit: int, count) -> list[str]:
        from knowledge_base.embeddings import split_to_limit

        pieces = split_to_limit(text, limit, count)
        assert "".join(pieces) == text
        assert all(piece and count(piece) <= limit for piece in pieces)
        return pieces

    def test_a_text_that_fits_comes_back_whole(self):
        assert self._check("a b c", 4, _words) == ["a b c"]

    def test_pieces_keep_newlines_indentation_and_trailing_whitespace(self):
        text = "def f():\n    a = 1\n\n    return a  \n\t\n" * 5
        assert len(self._check(text, 6, _words)) > 1

    def test_a_non_monotone_count_still_yields_verified_pieces(self):
        probed: list[int] = []

        def count(text: str) -> int:  # 20 characters count far over the limit, though 20 would fit
            probed.append(len(text))
            return 999 if len(text) == 20 else len(text)

        self._check("a " * 30, 20, count)
        assert 20 in probed

    def test_a_cut_keeps_at_least_half_a_piece_rather_than_a_lone_heading(self):
        pieces = self._check("## References\n\n" + "x" * 100, 50, len)
        assert len(pieces[0]) > len("## References\n\n")

    def test_a_cut_prefers_the_end_of_a_line(self):
        from knowledge_base.embeddings import _fit

        assert _fit("one two three\nfour five", 5, _words) == len("one two three\n")  # not after "four "
        assert self._check("one two three\nfour five six seven", 6, _words) == [
            "one two three\n",
            "four five six seven",
        ]

    @pytest.mark.parametrize("words", [64, 65, 127, 128, 200])
    def test_no_piece_is_a_stray_tail(self, words):
        pieces = self._check(" ".join(["w"] * words), 64, _words)
        assert min(map(_words, pieces)) > 64 // 4

    def test_a_character_too_big_for_the_even_share_still_fits_the_limit(self):
        def count(text: str) -> int:  # "Y" alone counts 8 with its EOS: over the share of 7, within the limit 11
            return text.count("Y") * 7 + text.count("a") + 1

        assert self._check("Yaaaa", 11, count) == ["Yaaa", "a"]

    def test_probes_stay_near_the_piece_they_cut(self):
        probed: list[int] = []

        def count(text: str) -> int:
            probed.append(len(text))
            return _words(text)

        text = "word " * 2000
        pieces = self._check(text, 11, count)
        assert max(probed[1:]) <= 2 * max(map(len, pieces)) + 1  # the first count is the whole text

    def test_a_long_word_after_a_fitting_boundary_is_cut_inside_the_word(self):
        pieces = self._check("aa bb " + "x" * 50 + " cc", 10, len)
        assert pieces[0] == "aa bb "
        assert pieces[1] == "x" * 10

    def test_nothing_fits_names_the_variable(self):
        from knowledge_base.embeddings import split_to_limit
        from knowledge_base.exceptions import ValidationError

        with pytest.raises(ValidationError, match="KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS"):
            split_to_limit("abc", 1, lambda text: len(text) + 1)  # one character and its EOS count 2


class TestBoundedProvider:
    """The provider counts at llama-server's ``/tokenize`` and never sends an input over the limit."""

    @pytest.fixture
    def server(self, monkeypatch, fake_dns, http_requests):
        import json

        import httpx

        monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_URL", "https://embedder.example.test")
        monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_API_KEY", "sk-test-only")
        fake_dns["embedder.example.test"] = ["100.64.0.10"]

        def handle(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if request.url.path == "/tokenize":
                return httpx.Response(200, json={"tokens": [0] * _words(body["content"])})
            vectors = [[1.0] + [0.0] * 7 for _ in body["input"]]
            return httpx.Response(200, json={"data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)]})

        http_requests.handlers["embedder.example.test"] = handle
        return http_requests

    def _provider(self, limit: int | None):
        from knowledge_base.embeddings import OpenAICompatProvider

        return OpenAICompatProvider(
            "https://embedder.example.test", "env:KNOWLEDGE_BASE_EMBED_API_KEY", max_input_tokens=limit
        )

    def test_the_counter_posts_the_server_s_own_count_request(self, server):
        import json

        assert self._provider(8).token_counter()("one two three") == 4
        (request,) = server
        assert str(request.url) == "https://embedder.example.test/tokenize"
        assert request.headers["Authorization"] == "Bearer sk-test-only"
        assert json.loads(request.content) == {"content": "one two three", "add_special": True, "parse_special": True}

    @pytest.mark.parametrize(
        ("status", "body"),
        [
            (404, {"error": "not found"}),
            (422, {"detail": "prompt missing"}),
            (400, {}),
            (200, {"count": 1}),
            (200, None),
        ],
    )
    def test_a_server_without_llama_server_s_tokenize_is_refused(self, server, status, body):
        import httpx

        from knowledge_base.exceptions import ValidationError

        def answer(request: httpx.Request) -> httpx.Response:  # None: an HTML fallback page, not JSON
            return httpx.Response(status, json=body) if body is not None else httpx.Response(status, text="<html>")

        server.handlers["embedder.example.test"] = answer
        with pytest.raises(ValidationError, match=r"KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS.*/tokenize"):
            self._provider(8).token_counter()("one")

    def test_embed_sends_an_input_that_fits_unchanged_and_truncates_one_that_does_not(self, server, caplog):
        import json

        long = "w " * 20
        with caplog.at_level(logging.INFO, logger="knowledge_base.embeddings"):
            self._provider(8).embed(["fits here", long], model="m", expected_dim=8)
        sent = [text for r in server if r.url.path == "/v1/embeddings" for text in json.loads(r.content)["input"]]
        assert sent[0] == "fits here"
        assert long.startswith(sent[1])
        assert _words(sent[1]) <= 8
        assert "of 21 tokens is truncated to 8 tokens" in caplog.text

    def test_without_a_limit_nothing_is_counted(self, server):
        self._provider(None).embed(["w " * 20], model="m", expected_dim=8)
        assert [r.url.path for r in server] == ["/v1/embeddings"]


@pytest.fixture
def embedder(monkeypatch, fake_dns, http_requests):
    """An embedder at the transport: one vector per input, the input's position in its request recorded."""
    import json

    import httpx

    monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_URL", "https://embedder.example.test")
    fake_dns["embedder.example.test"] = ["100.64.0.10"]

    def handle(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        vectors = [[1.0, float(int(text.split()[-1]))] for text in texts]
        return httpx.Response(200, json={"data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)]})

    http_requests.handlers["embedder.example.test"] = handle
    return http_requests


def _inputs_per_request(recorder) -> list[list[str]]:
    import json

    return [json.loads(r.content)["input"] for r in recorder if r.url.path == "/v1/embeddings"]


class TestBatchSize:
    """#614: how many inputs one /v1/embeddings request carries."""

    def _embed(self, texts: list[str], **kwargs) -> list[list[float] | None]:
        from knowledge_base.embeddings import OpenAICompatProvider

        return OpenAICompatProvider("https://embedder.example.test", **kwargs).embed(texts, model="m", expected_dim=2)

    def test_one_input_per_request_by_default(self, embedder):
        texts = [f"text {i}" for i in range(5)]
        vectors = self._embed(texts)
        assert _inputs_per_request(embedder) == [[text] for text in texts]
        assert [v[1] / v[0] for v in vectors if v is not None] == [0.0, 1.0, 2.0, 3.0, 4.0]

    def test_a_declared_batch_size_groups_inputs(self, embedder):
        texts = [f"text {i}" for i in range(5)]
        vectors = self._embed(texts, batch_size=3)
        assert _inputs_per_request(embedder) == [texts[:3], texts[3:]]
        assert [v[1] / v[0] for v in vectors if v is not None] == [0.0, 1.0, 2.0, 3.0, 4.0]


class TestOneRequestInFlight:
    """#614: concurrent tool calls share one /v1/embeddings request in flight per embedder, in arrival order."""

    @pytest.fixture
    def held(self, embedder):
        """The embedder, holding each request until ``release`` and recording entries and the most in flight."""
        import threading

        state = {"order": [], "in_flight": 0, "most": 0, "answer": 200}
        lock, entered, release = threading.Lock(), threading.Semaphore(0), threading.Event()
        answer = embedder.handlers["embedder.example.test"]

        def handle(request):
            import json

            import httpx

            with lock:
                state["order"].extend(json.loads(request.content)["input"])
                state["in_flight"] += 1
                state["most"] = max(state["most"], state["in_flight"])
            entered.release()
            release.wait(30)  # above the test's own deadlines, so a request is never released early
            with lock:
                state["in_flight"] -= 1
                status = state["answer"]
                state["answer"] = 200
            return answer(request) if status == 200 else httpx.Response(status)

        embedder.handlers["embedder.example.test"] = handle
        state.update(entered=entered, release=release)
        return state

    @staticmethod
    def _provider():
        from knowledge_base.embeddings import OpenAICompatProvider

        return OpenAICompatProvider("https://embedder.example.test")

    @staticmethod
    def _queued(provider, n: int) -> None:
        import time

        deadline = time.monotonic() + 5
        while len(provider._turns._queue) < n:
            assert time.monotonic() < deadline, "the waiter never queued"
            time.sleep(0.001)

    def test_concurrent_callers_never_have_two_requests_in_flight(self, held):
        import threading

        provider, errors = self._provider(), []

        def call(name: str) -> None:
            try:
                provider.embed([f"{name} 0", f"{name} 1"], model="m", expected_dim=2)
            except Exception as error:  # surfaced by the assertion below
                errors.append(error)

        threads = [threading.Thread(target=call, args=(name,), daemon=True) for name in "abc"]
        for thread in threads:
            thread.start()
        assert held["entered"].acquire(timeout=5)
        self._queued(provider, 3)  # the two others wait their turn instead of reaching the server
        held["release"].set()
        for thread in threads:
            thread.join(5)
        assert errors == []
        assert held["most"] == 1
        assert sorted(held["order"]) == [f"{name} {i}" for name in "abc" for i in (0, 1)]

    def test_a_caller_that_arrives_during_a_drain_is_served_before_its_next_request(self, held):
        import threading

        provider = self._provider()
        a = threading.Thread(target=provider.embed, args=(["a 0", "a 1", "a 2"],), kwargs={"model": "m"}, daemon=True)
        b = threading.Thread(target=provider.embed, args=(["b 0"],), kwargs={"model": "m"}, daemon=True)
        a.start()
        assert held["entered"].acquire(timeout=5)
        b.start()
        self._queued(provider, 2)
        held["release"].set()
        a.join(5)
        b.join(5)
        assert held["order"] == ["a 0", "b 0", "a 1", "a 2"]

    def test_a_failed_request_releases_its_turn(self, held):
        import threading

        import httpx

        provider, failures = self._provider(), []

        def drain() -> None:
            try:
                provider.embed(["a 0"], model="m")
            except httpx.HTTPStatusError as error:
                failures.append(error.response.status_code)

        held["answer"] = 500
        a = threading.Thread(target=drain, daemon=True)
        a.start()
        assert held["entered"].acquire(timeout=5)
        result: list = []
        b = threading.Thread(
            target=lambda: result.extend(provider.embed(["b 1"], model="m", expected_dim=2)), daemon=True
        )
        b.start()
        self._queued(provider, 2)
        held["release"].set()
        a.join(5)
        b.join(5)
        assert failures == [500]
        assert len(result) == 1
        assert len(provider._turns._queue) == 0

    def test_a_waiter_whose_wait_raises_leaves_the_queue(self):
        import threading

        from knowledge_base.embeddings import _Turns

        turns, raised = _Turns(), []
        with turns.turn():
            real_wait_for = turns._cv.wait_for

            def interrupted(predicate, timeout=None):
                raise KeyboardInterrupt

            turns._cv.wait_for = interrupted  # type: ignore[method-assign]

            def waiter() -> None:
                try:
                    with turns.turn():
                        pass
                except KeyboardInterrupt:
                    raised.append(True)

            thread = threading.Thread(target=waiter, daemon=True)
            thread.start()
            thread.join(5)
            turns._cv.wait_for = real_wait_for  # type: ignore[method-assign]
            assert raised == [True]
            assert len(turns._queue) == 1  # only the holder
        served: list[bool] = []

        def later() -> None:
            with turns.turn():
                served.append(True)

        thread = threading.Thread(target=later, daemon=True)
        thread.start()
        thread.join(5)
        assert served == [True]
        assert len(turns._queue) == 0

    def test_every_provider_of_an_endpoint_shares_its_turns(self):
        from knowledge_base.embeddings import ProviderConfig

        def cfg(**changes) -> ProviderConfig:
            return ProviderConfig(family="llama.cpp", base_url="https://embedder.example.test", api_key="k", **changes)

        providers = [
            get_provider(cfg=cfg()),
            get_provider(cfg=cfg(allow_loopback=True)),  # a loopback opt-in toggled mid-drain
            get_provider(cfg=cfg(batch_size=8)),
        ]
        assert len({id(p) for p in providers}) == 3
        assert len({id(p._turns) for p in providers}) == 1  # type: ignore[attr-defined]
        other = get_provider(cfg=ProviderConfig(family="llama.cpp", base_url="https://other.example.test"))
        assert other._turns is not providers[0]._turns  # type: ignore[attr-defined]

    def test_concurrent_first_calls_build_one_provider(self, monkeypatch):
        import threading
        import time

        from knowledge_base import embeddings
        from knowledge_base.embeddings import ProviderConfig

        real = embeddings._build_provider

        def slow(cfg):
            time.sleep(0.05)  # building takes a while (the client loads its CA bundle), so both calls miss the cache
            return real(cfg)

        monkeypatch.setattr(embeddings, "_build_provider", slow)
        cfg = ProviderConfig(family="llama.cpp", base_url="https://embedder.example.test")
        got: list = []
        threads = [threading.Thread(target=lambda: got.append(get_provider(cfg=cfg)), daemon=True) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        assert len(got) == 2
        assert got[0] is got[1]


def test_the_embedder_url_is_validated_before_every_request(embedder, monkeypatch):
    """One input per request must not leave requests 2..N of a call without the SSRF floor's check."""
    from knowledge_base import embeddings
    from knowledge_base.embeddings import OpenAICompatProvider

    checked: list[str] = []
    real = embeddings.validate_base_url

    def counting(url, **kwargs):
        checked.append(url)
        return real(url, **kwargs)

    monkeypatch.setattr(embeddings, "validate_base_url", counting)
    OpenAICompatProvider("https://embedder.example.test").embed(["a 0", "a 1", "a 2"], model="m")
    assert len(_inputs_per_request(embedder)) == 3
    assert len(checked) == 3


def test_truncate_embedding_returns_none_for_a_zero_prefix():
    from knowledge_base.embeddings import truncate_embedding

    assert truncate_embedding([0.0, 0.0, 3.0, 4.0], 2) is None
    assert truncate_embedding([3.0, 4.0, 0.0], 2) == [0.6, 0.8]
