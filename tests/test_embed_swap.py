"""Tests for embedding model swap and re-embed."""

from unittest.mock import MagicMock, patch

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
    assert result["model"] == "mxbai-embed-large"
    assert result["dim"] == NEW_DIM

    # Config should be updated
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
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_re_embed_preserves_chunk_ids(mock_provider, tmp_path):
    conn = _setup(tmp_path)

    md = tmp_path / "doc.md"
    md.write_text("Content to re-embed.\n")
    ingest_file(conn, md)

    chunk_ids_before = [
        r["id"] for r in conn.execute("SELECT id FROM chunks").fetchall()
    ]

    re_embed(conn, "mxbai-embed-large", NEW_DIM)

    chunk_ids_after = [
        r["id"] for r in conn.execute("SELECT id FROM chunks").fetchall()
    ]
    assert chunk_ids_before == chunk_ids_after


@patch(
    "knowledge_base.embed_swap.get_provider",
    return_value=_mock_provider(_fake_embed_new),
)
def test_re_embed_empty_db(mock_provider, tmp_path):
    conn = _setup(tmp_path)

    result = re_embed(conn, "mxbai-embed-large", NEW_DIM)

    assert result["chunks_processed"] == 0
    config = get_embed_config(conn)
    assert config["model"] == "mxbai-embed-large"


def test_get_embed_config_includes_provider(tmp_path):
    conn = _setup(tmp_path)
    config = get_embed_config(conn)
    assert config["provider"] == "ollama"


@patch("knowledge_base.embed_swap.get_provider")
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_re_embed_with_explicit_provider(mock_get_provider, tmp_path):
    """re_embed with provider= should use that provider and persist it."""
    conn = _setup(tmp_path)

    md = tmp_path / "doc.md"
    md.write_text("Test content.\n")
    ingest_file(conn, md)

    mock_provider = MagicMock()
    mock_provider.embed.return_value = [[0.5] * NEW_DIM]
    mock_get_provider.return_value = mock_provider

    re_embed(conn, "text-embedding-3-large", NEW_DIM, provider="openai")

    mock_get_provider.assert_called_with("openai")
    mock_provider.embed.assert_called()

    # Provider should be persisted in config
    config = get_embed_config(conn)
    assert config["provider"] == "openai"
