"""Tests for embedding model swap and re-embed."""

from unittest.mock import MagicMock, patch

import pytest

from knowledge_base.db import DEFAULT_EMBED_DIM, get_connection, init_schema
from knowledge_base.embed_swap import get_embed_config, re_embed
from knowledge_base.ingest import ingest_file


NEW_DIM = 384


def _fake_embed(texts, model="bge-m3", expected_dim=None, **_kwargs):
    dim = expected_dim if expected_dim is not None else DEFAULT_EMBED_DIM
    return [[0.1] * dim for _ in texts]


def _fake_embed_new(texts, model="mxbai-embed-large", expected_dim=None, **_kwargs):
    dim = expected_dim if expected_dim is not None else NEW_DIM
    return [[0.2] * dim for _ in texts]


def _mock_provider(fake_fn):
    """Wrap a fake embed function in a mock provider."""
    mock = MagicMock()
    mock.embed.side_effect = lambda texts, model=None, expected_dim=None: fake_fn(
        texts, model=model, expected_dim=expected_dim
    )
    return mock


def _setup(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    return conn


def test_get_embed_config(tmp_path):
    conn = _setup(tmp_path)
    config = get_embed_config(conn)
    assert config["model"] == "bge-m3"
    assert config["dim"] == DEFAULT_EMBED_DIM


@patch(
    "knowledge_base.embed_swap.get_provider",
    return_value=_mock_provider(_fake_embed_new),
)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_re_embed_changes_model(mock_provider, tmp_path):
    conn = _setup(tmp_path)

    # Ingest a file with old model
    md = tmp_path / "doc.md"
    md.write_text("Test content for re-embedding.\n")
    ingest_file(conn, md)

    old_count = conn.execute("SELECT COUNT(*) as c FROM chunks_vec").fetchone()["c"]
    assert old_count >= 1

    # Re-embed with new model
    result = re_embed(conn, "mxbai-embed-large", NEW_DIM)

    assert result["chunks_processed"] == old_count

    config = get_embed_config(conn)
    assert config["model"] == "mxbai-embed-large"
    assert config["dim"] == NEW_DIM

    # Vec table should still have same number of rows
    new_count = conn.execute("SELECT COUNT(*) as c FROM chunks_vec").fetchone()["c"]
    assert new_count == old_count


@patch(
    "knowledge_base.embed_swap.get_provider",
    return_value=_mock_provider(_fake_embed_new),
)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_re_embed_preserves_chunk_ids(mock_provider, tmp_path):
    conn = _setup(tmp_path)

    md = tmp_path / "doc.md"
    md.write_text("Content to re-embed.\n")
    ingest_file(conn, md)

    chunk_ids_before = [r["id"] for r in conn.execute("SELECT id FROM chunks").fetchall()]

    re_embed(conn, "mxbai-embed-large", NEW_DIM)

    chunk_ids_after = [r["id"] for r in conn.execute("SELECT id FROM chunks").fetchall()]
    assert chunk_ids_before == chunk_ids_after


@patch(
    "knowledge_base.embed_swap.get_provider",
    return_value=_mock_provider(_fake_embed_new),
)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_re_embed_empty_db(mock_provider, tmp_path):
    conn = _setup(tmp_path)

    result = re_embed(conn, "mxbai-embed-large", NEW_DIM)

    assert result["chunks_processed"] == 0
    assert result["model"] == "mxbai-embed-large"
    assert result["dim"] == NEW_DIM


@patch(
    "knowledge_base.embed_swap.get_provider",
    return_value=_mock_provider(_fake_embed_new),
)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_re_embed_includes_folder_summaries(mock_provider, tmp_path):
    """re_embed re-creates folder_summaries_vec with the new model."""
    conn = _setup(tmp_path)

    folder = tmp_path / "papers"
    folder.mkdir()
    (folder / "a.md").write_text("Paper about attention.\n")
    ingest_file(conn, folder / "a.md")

    # Verify folder summary exists
    assert conn.execute("SELECT count(*) FROM folder_summaries").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM folder_summaries_vec").fetchone()[0] == 1

    result = re_embed(conn, "mxbai-embed-large", NEW_DIM)

    assert result["folders_processed"] == 1
    # Vec table rebuilt with new dim
    assert conn.execute("SELECT count(*) FROM folder_summaries_vec").fetchone()[0] == 1


# --- Slice C: configure_embeddings + env→config migration + env:VAR secrets (#516) ---


def _ok_get(*_args, **_kwargs):
    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": []}

    return FakeResp()


def test_configure_embeddings_validates_and_redacts(tmp_path):
    from knowledge_base.embed_swap import configure_embeddings

    conn = _setup(tmp_path)
    with (
        patch("knowledge_base.utils.is_private_ip", return_value=False),
        patch("knowledge_base.embed_swap.httpx.get", _ok_get),
    ):
        result = configure_embeddings(
            conn,
            provider="openai_compat",
            base_url="https://vllm.example.com/v1",
            model="bge-m3",
            api_key="sk-secret-123",
        )
    assert result["provider"] == "openai_compat"
    assert "api_key" not in result  # redacted
    assert result["base_url"] == "https://vllm.example.com"  # normalized (/v1 stripped)
    # persisted
    cfg = get_embed_config(conn)
    assert cfg["provider"] == "openai_compat"
    assert cfg["base_url"] == "https://vllm.example.com"
    assert cfg["api_key"] == "sk-secret-123"


def test_configure_embeddings_rejects_anthropic_compat(tmp_path):
    from knowledge_base.embed_swap import configure_embeddings
    from knowledge_base.exceptions import ValidationError

    conn = _setup(tmp_path)
    with pytest.raises(ValidationError, match="anthropic_compat"):
        configure_embeddings(conn, provider="anthropic_compat", base_url="https://x.example.com")


def test_configure_embeddings_loopback_requires_flag(tmp_path):
    from knowledge_base.embed_swap import configure_embeddings
    from knowledge_base.exceptions import ValidationError

    conn = _setup(tmp_path)
    # Without the opt-in, a localhost base_url is hard-rejected at config-write.
    with pytest.raises(ValidationError, match="private"):
        configure_embeddings(conn, provider="openai_compat", base_url="http://localhost:11434")
    # With the opt-in, it is accepted (connectivity is advisory).
    with patch("knowledge_base.embed_swap.httpx.get", _ok_get):
        result = configure_embeddings(
            conn,
            provider="openai_compat",
            base_url="http://localhost:11434/v1",
            allow_loopback_base_url=True,
        )
    assert result["provider"] == "openai_compat"
    assert get_embed_config(conn)["base_url"] == "http://localhost:11434"


def test_configure_embeddings_env_indirection_not_persisted(tmp_path, monkeypatch):
    from knowledge_base.embed_swap import configure_embeddings

    conn = _setup(tmp_path)
    monkeypatch.setenv("KB_EMBED_SECRET", "sk-resolved-at-call-time")
    with (
        patch("knowledge_base.utils.is_private_ip", return_value=False),
        patch("knowledge_base.embed_swap.httpx.get", _ok_get),
    ):
        configure_embeddings(
            conn,
            provider="openai_compat",
            base_url="https://vllm.example.com",
            api_key="env:KB_EMBED_SECRET",
        )
    # The raw indirection spec is stored verbatim — never the resolved secret.
    stored = conn.execute("SELECT value FROM config WHERE key = 'embed_api_key'").fetchone()["value"]
    assert stored == "env:KB_EMBED_SECRET"
    all_values = " ".join(r["value"] for r in conn.execute("SELECT value FROM config").fetchall())
    assert "sk-resolved-at-call-time" not in all_values


def test_configure_embeddings_clears_stale_keys_for_ollama(tmp_path):
    from knowledge_base.embed_swap import configure_embeddings

    conn = _setup(tmp_path)
    with (
        patch("knowledge_base.utils.is_private_ip", return_value=False),
        patch("knowledge_base.embed_swap.httpx.get", _ok_get),
    ):
        configure_embeddings(conn, provider="openai_compat", base_url="https://x.example.com", api_key="sk-1")
    with patch("knowledge_base.embed_swap.httpx.get", _ok_get):
        configure_embeddings(conn, provider="ollama", model="bge-m3")
    cfg = get_embed_config(conn)
    assert cfg["provider"] == "ollama"
    assert cfg["base_url"] is None
    assert cfg["api_key"] is None


def test_configure_embeddings_no_key_in_logs(tmp_path, caplog):
    from knowledge_base.embed_swap import configure_embeddings

    conn = _setup(tmp_path)
    with (
        patch("knowledge_base.utils.is_private_ip", return_value=False),
        patch("knowledge_base.embed_swap.httpx.get", _ok_get),
        caplog.at_level("DEBUG"),
    ):
        configure_embeddings(
            conn,
            provider="openai_compat",
            base_url="https://user:hunter2@vllm.example.com",
            api_key="sk-topsecret",
        )
    assert "sk-topsecret" not in caplog.text
    assert "hunter2" not in caplog.text


def test_get_embed_config_env_backcompat(tmp_path, monkeypatch, caplog):
    """Legacy EMBED_PROVIDER env still selects while config is at its seeded default."""
    conn = _setup(tmp_path)
    monkeypatch.setenv("EMBED_PROVIDER", "onnx")
    with caplog.at_level("WARNING"):
        cfg = get_embed_config(conn)
    assert cfg["provider"] == "onnx"
    assert "deprecated" in caplog.text.lower()


def test_get_embed_config_explicit_config_overrides_env(tmp_path, monkeypatch):
    """An explicit configure_embeddings() choice wins over the legacy env var."""
    from knowledge_base.embed_swap import configure_embeddings

    conn = _setup(tmp_path)
    with (
        patch("knowledge_base.utils.is_private_ip", return_value=False),
        patch("knowledge_base.embed_swap.httpx.get", _ok_get),
    ):
        configure_embeddings(conn, provider="openai_compat", base_url="https://x.example.com")
    monkeypatch.setenv("EMBED_PROVIDER", "onnx")
    assert get_embed_config(conn)["provider"] == "openai_compat"
