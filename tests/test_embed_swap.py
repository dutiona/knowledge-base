"""Tests for embedding model swap and re-embed."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from knowledge_base.db import get_connection, init_schema
from knowledge_base.embed_swap import backfill_space, create_space, get_embed_config, re_embed
from knowledge_base.ingest import ingest_file
from knowledge_base.utils import serialize_f32 as _serialize_f32
from tests.declared_env import TEST_EMBED_DIM, TEST_EMBED_MODEL


NEW_DIM = 384


def _fake_embed(texts, model="bge-m3", expected_dim=None, **_kwargs):
    dim = expected_dim if expected_dim is not None else TEST_EMBED_DIM
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
    assert config["dim"] == TEST_EMBED_DIM


def _declare_new_model(monkeypatch) -> None:
    """re_embed moves the active identity only to the declaration (#569)."""
    monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_MODEL", "mxbai-embed-large")
    monkeypatch.setenv("KNOWLEDGE_BASE_EMBED_DIM", str(NEW_DIM))


@patch(
    "knowledge_base.embed_swap.get_provider",
    return_value=_mock_provider(_fake_embed_new),
)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.ingest.embed", _fake_embed)
def test_re_embed_changes_model(mock_provider, tmp_path, monkeypatch):
    conn = _setup(tmp_path)

    # Ingest a file with old model
    md = tmp_path / "doc.md"
    md.write_text("Test content for re-embedding.\n")
    ingest_file(conn, md)

    old_count = conn.execute("SELECT COUNT(*) as c FROM chunks_vec").fetchone()["c"]
    assert old_count >= 1

    # Re-embed with new model
    _declare_new_model(monkeypatch)
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
def test_re_embed_preserves_chunk_ids(mock_provider, tmp_path, monkeypatch):
    conn = _setup(tmp_path)

    md = tmp_path / "doc.md"
    md.write_text("Content to re-embed.\n")
    ingest_file(conn, md)

    chunk_ids_before = [r["id"] for r in conn.execute("SELECT id FROM chunks").fetchall()]

    _declare_new_model(monkeypatch)
    re_embed(conn, "mxbai-embed-large", NEW_DIM)

    chunk_ids_after = [r["id"] for r in conn.execute("SELECT id FROM chunks").fetchall()]
    assert chunk_ids_before == chunk_ids_after


@patch(
    "knowledge_base.embed_swap.get_provider",
    return_value=_mock_provider(_fake_embed_new),
)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_re_embed_empty_db(mock_provider, tmp_path, monkeypatch):
    conn = _setup(tmp_path)

    _declare_new_model(monkeypatch)
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
def test_re_embed_includes_folder_summaries(mock_provider, tmp_path, monkeypatch):
    """re_embed re-creates folder_summaries_vec with the new model."""
    conn = _setup(tmp_path)

    folder = tmp_path / "papers"
    folder.mkdir()
    (folder / "a.md").write_text("Paper about attention.\n")
    ingest_file(conn, folder / "a.md")

    # Verify folder summary exists
    assert conn.execute("SELECT count(*) FROM folder_summaries").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM folder_summaries_vec").fetchone()[0] == 1

    _declare_new_model(monkeypatch)
    result = re_embed(conn, "mxbai-embed-large", NEW_DIM)

    assert result["folders_processed"] == 1
    # Vec table rebuilt with new dim
    assert conn.execute("SELECT count(*) FROM folder_summaries_vec").fetchone()[0] == 1


# --- Slice D: producer-side identity hard-reject (AC6, #516) ---


def test_assert_identity_match_passes_when_aligned(tmp_path):
    from knowledge_base.embed_swap import assert_embed_identity_match

    conn = _setup(tmp_path)  # default: active space + config both (ollama, bge-m3)
    assert_embed_identity_match(conn)  # no raise


def test_assert_identity_match_rejects_family_change(tmp_path):
    from knowledge_base.embed_swap import assert_embed_identity_match
    from knowledge_base.exceptions import ValidationError

    conn = _setup(tmp_path)
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('embed_provider', 'openai_compat')")
    conn.commit()
    # active space still (ollama, bge-m3); config now openai_compat → mismatch
    with pytest.raises(ValidationError, match="identity"):
        assert_embed_identity_match(conn)


def test_assert_identity_match_rejects_model_change(tmp_path):
    from knowledge_base.embed_swap import assert_embed_identity_match
    from knowledge_base.exceptions import ValidationError

    conn = _setup(tmp_path)
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('embed_model', 'other-model')")
    conn.commit()
    with pytest.raises(ValidationError, match="identity"):
        assert_embed_identity_match(conn)


def test_assert_identity_match_allows_base_url_swap_same_family(tmp_path):
    """tei→vllm: both openai_compat, same model — only base_url differs → allowed."""
    from knowledge_base.embed_swap import assert_embed_identity_match

    conn = _setup(tmp_path)
    conn.execute("UPDATE embed_spaces SET provider = 'openai_compat' WHERE status = 'active'")
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('embed_provider', 'openai_compat')")
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('embed_base_url', 'https://vllm.example.com')")
    conn.commit()
    assert_embed_identity_match(conn)  # same family + model → no raise


def test_assert_identity_match_noop_without_active_space(tmp_path):
    from knowledge_base.embed_swap import assert_embed_identity_match

    conn = _setup(tmp_path)
    conn.execute("DELETE FROM embed_spaces")
    conn.commit()
    assert_embed_identity_match(conn)  # no active space → nothing to mismatch


def test_embed_with_config_rejects_identity_mismatch(tmp_path):
    from knowledge_base.exceptions import ValidationError
    from knowledge_base.ingest import _embed_with_config

    conn = _setup(tmp_path)
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('embed_provider', 'openai_compat')")
    conn.commit()
    # active space (ollama, bge-m3) vs configured openai_compat → reject before any HTTP call
    with pytest.raises(ValidationError, match="identity"):
        _embed_with_config(conn, ["hello"])


@pytest.mark.parametrize("provider", ["openai_compat", "openai"])
def test_openai_api_key_is_never_an_embedding_key_source(tmp_path, monkeypatch, provider):
    """#569: the declared KNOWLEDGE_BASE_EMBED_API_KEY source is the only embedding key source."""
    conn = _setup(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-only")
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('embed_provider', ?)", (provider,))
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('embed_base_url', 'https://api.openai.com')")
    conn.execute("DELETE FROM config WHERE key = 'embed_api_key'")
    conn.commit()
    assert get_embed_config(conn)["api_key"] is None


# --- #569: every embedding provider comes from the declaration ---


def test_backfill_refuses_a_space_of_another_provider(kb_conn, http_requests):
    from knowledge_base.exceptions import ValidationError

    create_space(kb_conn, "foreign", "m", 8, "openai_compat")
    with pytest.raises(ValidationError, match="not the declared provider 'ollama'"):
        backfill_space(kb_conn, "foreign")
    assert http_requests == []


def test_search_refuses_an_active_space_of_another_provider(kb_conn, http_requests):
    from knowledge_base.exceptions import ValidationError
    from knowledge_base.search import search

    kb_conn.execute("UPDATE embed_spaces SET provider = 'openai_compat' WHERE status = 'active'")
    kb_conn.commit()
    with pytest.raises(ValidationError, match="not the declared provider 'ollama'"):
        search(kb_conn, "attention", mode="vec")
    assert http_requests == []


def test_folder_summary_refuses_an_active_space_of_another_provider(kb_conn, http_requests):
    from knowledge_base.exceptions import ValidationError
    from knowledge_base.folder_summaries import update_folder_summary

    kb_conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES ('h1', 'some text', 'markdown', '/docs/a.md', 0)"
    )
    kb_conn.execute("UPDATE embed_spaces SET provider = 'onnx' WHERE status = 'active'")
    kb_conn.commit()
    with pytest.raises(ValidationError, match="not the declared provider 'ollama'"):
        update_folder_summary(kb_conn, "/docs")
    assert http_requests == []


# --- #569: the embedding identity changes only through the declaration ---


def _space_rows(conn) -> list[tuple]:
    return [tuple(r) for r in conn.execute("SELECT name, status, model FROM embed_spaces ORDER BY name").fetchall()]


def _config_rows(conn) -> dict:
    return dict(conn.execute("SELECT key, value FROM config").fetchall())


def test_promote_refuses_an_undeclared_identity(kb_conn, http_requests):
    import json

    import knowledge_base.routes.embeddings as emb

    create_space(kb_conn, "other", "other-model", TEST_EMBED_DIM, "ollama")
    before = (_config_rows(kb_conn), _space_rows(kb_conn))
    with patch("knowledge_base.routes.embeddings._get_conn", return_value=kb_conn):
        promoted = json.loads(emb.promote_embed_space_tool("other"))
        re_embedded = json.loads(emb.re_embed_tool(model="other-model", dim=8))
    assert "embed_model" in promoted["error"]
    assert "embed_model" in re_embedded["error"]
    assert (_config_rows(kb_conn), _space_rows(kb_conn)) == before
    assert http_requests == []


def _two_folders(conn) -> None:
    conn.execute(
        "INSERT INTO folder_summaries (folder_path, summary, content_hash) VALUES "
        "('/docs/a', 'alpha text', 'h1'), ('/docs/b', 'beta text', 'h2')"
    )
    for path in ("/docs/a", "/docs/b"):
        conn.execute(
            "INSERT INTO folder_summaries_vec (embedding, folder_path) VALUES (?, ?)",
            (_serialize_f32([0.5] * TEST_EMBED_DIM), path),
        )
    conn.commit()


def _vec_state(conn) -> list:
    return sorted(
        (r["folder_path"], bytes(r["embedding"]))
        for r in conn.execute("SELECT folder_path, embedding FROM folder_summaries_vec").fetchall()
    )


def _provider(vector: float = 0.25) -> MagicMock:
    provider = MagicMock()
    provider.embed.side_effect = lambda texts, model=None, expected_dim=TEST_EMBED_DIM: [
        [vector] * expected_dim for _ in texts
    ]
    return provider


@pytest.mark.parametrize("failure", ["embed", "insert", "interrupt"])
def test_promote_is_all_or_nothing(kb_conn, failure):
    from knowledge_base.embed_swap import promote_space

    _two_folders(kb_conn)
    create_space(kb_conn, "same", TEST_EMBED_MODEL, TEST_EMBED_DIM, "ollama")
    before = (_config_rows(kb_conn), _space_rows(kb_conn), _vec_state(kb_conn))
    provider = _provider()
    calls = {"n": 0}
    real_serialize = _serialize_f32

    def _serialize(vec):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt if failure == "interrupt" else RuntimeError("insert failed")
        return real_serialize(vec)

    if failure == "embed":
        provider.embed.side_effect = RuntimeError("embed failed")
    with (
        patch("knowledge_base.embed_swap.get_provider", return_value=provider),
        patch("knowledge_base.embed_swap._serialize_f32", _serialize),
        pytest.raises((RuntimeError, KeyboardInterrupt)),
    ):
        promote_space(kb_conn, "same")

    assert kb_conn.isolation_level == ""
    assert (_config_rows(kb_conn), _space_rows(kb_conn), _vec_state(kb_conn)) == before
    fresh = get_connection(Path(kb_conn.execute("PRAGMA database_list").fetchone()["file"]))
    assert (_config_rows(fresh), _space_rows(fresh), _vec_state(fresh)) == before
    fresh.close()
    with patch("knowledge_base.embed_swap.get_provider", return_value=_provider()):
        promote_space(kb_conn, "same")  # a retry succeeds
    assert ("same", "active", TEST_EMBED_MODEL) in _space_rows(kb_conn)


def test_promote_refuses_a_pending_transaction(kb_conn):
    from knowledge_base.embed_swap import promote_space
    from knowledge_base.exceptions import KnowledgeBaseError

    create_space(kb_conn, "same", TEST_EMBED_MODEL, TEST_EMBED_DIM, "ollama")
    kb_conn.execute("INSERT INTO papers (title) VALUES ('pending')")
    assert kb_conn.in_transaction
    with pytest.raises(KnowledgeBaseError, match="open transaction"):
        promote_space(kb_conn, "same")
    assert kb_conn.in_transaction
    assert kb_conn.execute("SELECT COUNT(*) FROM papers WHERE title = 'pending'").fetchone()[0] == 1
    kb_conn.rollback()


def test_promote_re_embeds_a_summary_changed_after_the_snapshot(kb_conn):
    from knowledge_base.embed_swap import promote_space

    _two_folders(kb_conn)
    create_space(kb_conn, "same", TEST_EMBED_MODEL, TEST_EMBED_DIM, "ollama")
    path = Path(kb_conn.execute("PRAGMA database_list").fetchone()["file"])
    seen: list[list[str]] = []

    def _embed(texts, model=None, expected_dim=TEST_EMBED_DIM):
        seen.append(list(texts))
        if len(seen) == 1:  # between the snapshot and the lock, another writer edits a summary
            other = get_connection(path)
            other.execute("UPDATE folder_summaries SET summary = 'alpha revised' WHERE folder_path = '/docs/a'")
            other.commit()
            other.close()
        return [[0.1 if t == "alpha revised" else 0.25] * expected_dim for t in texts]

    provider = MagicMock()
    provider.embed.side_effect = _embed
    with patch("knowledge_base.embed_swap.get_provider", return_value=provider):
        promote_space(kb_conn, "same")
    assert seen == [["alpha text", "beta text"], ["alpha revised"]]
    stored = dict(_vec_state(kb_conn))
    assert stored["/docs/a"] == _serialize_f32([0.1] * TEST_EMBED_DIM)
    assert stored["/docs/b"] == _serialize_f32([0.25] * TEST_EMBED_DIM)


def test_failed_re_embed_leaves_no_orphan_space(kb_conn):
    before = _space_rows(kb_conn)
    with (
        patch("knowledge_base.embed_swap.backfill_space", side_effect=RuntimeError("embedder down")),
        pytest.raises(RuntimeError, match="embedder down"),
    ):
        re_embed(kb_conn, TEST_EMBED_MODEL, TEST_EMBED_DIM)
    assert _space_rows(kb_conn) == before


def test_a_running_process_embeds_only_at_its_own_declaration(kb_conn, http_requests):
    """Another process rewriting the endpoint rows (a rebind with the server up) cannot redirect this one."""
    import httpx

    from knowledge_base.ingest import _embed_with_config

    kb_conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES "
        "('embed_base_url', 'http://elsewhere.example.test:11434'), ('embed_api_key', 'env:HOME')"
    )
    kb_conn.commit()
    http_requests.handlers["embedder.example.test"] = lambda request: httpx.Response(
        200, json={"embeddings": [[1.0] + [0.0] * (TEST_EMBED_DIM - 1)]}
    )
    _embed_with_config(kb_conn, ["hello"])
    assert http_requests.hosts() == ["embedder.example.test"]
    assert "Authorization" not in http_requests[0].headers


def test_promote_refuses_a_target_removed_while_it_embedded(kb_conn):
    from knowledge_base.embed_swap import cleanup_space, deprecate_space, promote_space

    _two_folders(kb_conn)
    create_space(kb_conn, "same", TEST_EMBED_MODEL, TEST_EMBED_DIM, "ollama")
    path = Path(kb_conn.execute("PRAGMA database_list").fetchone()["file"])
    before = (_config_rows(kb_conn), _space_rows(kb_conn))

    def _embed(texts, model=None, expected_dim=TEST_EMBED_DIM):
        other = get_connection(path)  # another tool removes the target mid-promotion
        deprecate_space(other, "same")
        cleanup_space(other, "same")
        other.close()
        return [[0.25] * expected_dim for _ in texts]

    provider = MagicMock()
    provider.embed.side_effect = _embed
    with (
        patch("knowledge_base.embed_swap.get_provider", return_value=provider),
        pytest.raises(ValueError, match="changed"),
    ):
        promote_space(kb_conn, "same")
    after_config, after_spaces = _config_rows(kb_conn), _space_rows(kb_conn)
    assert after_config == before[0]
    assert after_spaces == [row for row in before[1] if row[0] != "same"]
    assert ("default", "active", TEST_EMBED_MODEL) in after_spaces
