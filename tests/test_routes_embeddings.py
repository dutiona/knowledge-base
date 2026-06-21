"""Wrapper-level unit tests for the embeddings route module.

These exercise the *server-only* logic of each MCP tool wrapper in
``knowledge_base.routes.embeddings`` — error mapping (``ValueError`` →
``{"error": str(e)}``), orchestration side-effects (similarity-relationship
invalidation, ``note`` augmentation), JSON shaping, and the rich branching of
``benchmark_spaces_tool``. The embedding/search internals are NOT tested here:
anything that would reach Ollama or the search index (``re_embed``,
``backfill_space``, ``compare_spaces``, ``batch_compare_spaces``,
``promote_space``) is mocked at the route namespace. Pure-SQLite registry
functions (``create_space``, ``deprecate_space``, ``cleanup_space``,
``list_spaces``, ``get_embed_config``, ``get_active_space``) run for real
against an isolated temp DB via the ``kb_conn`` fixture.
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import knowledge_base.routes.embeddings as emb


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _insert_paper(conn: sqlite3.Connection, title: str = "P") -> int:
    """Insert a minimal paper row and return its id (for relationship FKs)."""
    cur = conn.execute("INSERT INTO papers (title) VALUES (?)", (title,))
    conn.commit()
    rowid = cur.lastrowid
    assert rowid is not None  # lastrowid is set after a successful INSERT
    return rowid


def _insert_similar_relationship(conn: sqlite3.Connection) -> None:
    """Insert one 'similar' relationship between two fresh papers."""
    src = _insert_paper(conn, "src")
    tgt = _insert_paper(conn, "tgt")
    conn.execute(
        "INSERT INTO relationships (source_paper_id, target_paper_id, relation_type)"
        " VALUES (?, ?, 'similar')",
        (src, tgt),
    )
    conn.commit()


def _count_similar(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM relationships WHERE relation_type = 'similar'"
    ).fetchone()[0]


def _drop_default_active(conn: sqlite3.Connection) -> None:
    """Demote the seeded 'default' active space so another can become active.

    ``init_schema`` seeds one active space named 'default' and a partial unique
    index allows at most one row with status='active'. Tests that need a *custom*
    active space deprecate the seeded one first.
    """
    conn.execute("UPDATE embed_spaces SET status = 'deprecated' WHERE name = 'default'")
    conn.commit()


def _insert_space(
    conn: sqlite3.Connection,
    name: str = "active_sp",
    *,
    model: str = "test-model",
    dim: int = 256,
    chunk_strategy: str = "semantic",
    element_type: str = "float32",
    matryoshka_base_dim: int | None = None,
    status: str = "active",
) -> None:
    """Directly insert an embed_spaces row (pure SQLite, no embedding calls).

    For status='active', the caller must ensure no other active space exists
    (call ``_drop_default_active`` first) — the DB enforces a single active row.
    """
    conn.execute(
        "INSERT INTO embed_spaces"
        " (name, model, provider, dim, chunk_strategy, status, table_name,"
        " element_type, matryoshka_base_dim)"
        " VALUES (?, ?, 'ollama', ?, ?, ?, ?, ?, ?)",
        (
            name,
            model,
            dim,
            chunk_strategy,
            status,
            f"chunks_vec_{name}",
            element_type,
            matryoshka_base_dim,
        ),
    )
    conn.commit()


def _insert_chunk(conn: sqlite3.Connection, content: str = "hello world") -> None:
    """Insert a minimal chunk row so benchmark query-sampling finds rows.

    ``benchmark_spaces_tool`` samples queries via ``ORDER BY RANDOM() LIMIT ?``.
    The benchmark tests insert exactly ONE chunk, so that ordering is
    deterministic by construction (a single-element set) — keep it that way:
    adding a second chunk would make any assertion on the sampled query
    order/content flaky.
    """
    n = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    conn.execute(
        "INSERT INTO chunks"
        " (content_hash, content, source_type, source_uri, chunk_index)"
        " VALUES (?, ?, 'note', 'mem://x', ?)",
        (f"hash{n}", content, n),
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# 1. embed_config
# --------------------------------------------------------------------------- #


def test_embed_config_no_active_space(kb_conn):
    """No active space → bare model/dim/provider, no augmentation keys.

    ``init_schema`` seeds a 'default' active space, so the no-active branch is
    reached by mocking ``get_active_space`` → None.
    """
    with (
        patch.object(emb, "_get_conn", return_value=kb_conn),
        patch.object(emb, "get_active_space", return_value=None),
    ):
        result = json.loads(emb.embed_config())
    assert result["model"] == "bge-m3"
    assert result["dim"] == 1024
    assert result["provider"] == "ollama"
    # No active space → augmentation keys absent.
    assert "active_space" not in result
    assert "chunk_strategy" not in result
    assert "element_type" not in result


def test_embed_config_default_active_space_augments(kb_conn):
    """The seeded 'default' active space augments the config out of the box."""
    with patch.object(emb, "_get_conn", return_value=kb_conn):
        result = json.loads(emb.embed_config())
    assert result["active_space"] == "default"
    assert result["chunk_strategy"] == "mechanical"
    assert result["element_type"] == "float32"
    # default space carries no matryoshka_base_dim.
    assert "matryoshka_base_dim" not in result


def test_embed_config_with_custom_active_space_augments(kb_conn):
    """A custom active space augments active_space/chunk_strategy/element_type/matryoshka."""
    _drop_default_active(kb_conn)
    _insert_space(
        kb_conn,
        name="qwen_512",
        chunk_strategy="semantic",
        element_type="int8",
        dim=512,
        matryoshka_base_dim=1024,
        status="active",
    )
    with patch.object(emb, "_get_conn", return_value=kb_conn):
        result = json.loads(emb.embed_config())
    assert result["active_space"] == "qwen_512"
    assert result["chunk_strategy"] == "semantic"
    assert result["element_type"] == "int8"
    assert result["matryoshka_base_dim"] == 1024


# --------------------------------------------------------------------------- #
# 2. re_embed_tool
# --------------------------------------------------------------------------- #


def test_re_embed_tool_deletes_similar_and_adds_note(kb_conn):
    """re_embed mocked → wrapper DELETEs all 'similar' rels and adds 'note'."""
    _insert_similar_relationship(kb_conn)
    assert _count_similar(kb_conn) == 1

    with (
        patch.object(emb, "_get_conn", return_value=kb_conn),
        patch.object(
            emb, "re_embed", return_value={"chunks_processed": 3, "space": "m_512"}
        ) as mock_re_embed,
    ):
        result = json.loads(emb.re_embed_tool("m", 512, matryoshka_base_dim=1024))

    mock_re_embed.assert_called_once()
    # matryoshka_base_dim forwarded as keyword.
    assert mock_re_embed.call_args.kwargs["matryoshka_base_dim"] == 1024
    assert result["chunks_processed"] == 3
    assert "note" in result
    assert "similar" in result["note"]
    # Side-effect: relationships purged.
    assert _count_similar(kb_conn) == 0


# --------------------------------------------------------------------------- #
# 3. list_embed_spaces_tool
# --------------------------------------------------------------------------- #


def test_list_embed_spaces_seeded_default(kb_conn):
    """Fresh schema → exactly the seeded 'default' space as valid JSON."""
    with patch.object(emb, "_get_conn", return_value=kb_conn):
        result = json.loads(emb.list_embed_spaces_tool())
    assert isinstance(result, list)
    assert [s["name"] for s in result] == ["default"]


def test_list_embed_spaces_passthrough(kb_conn):
    """list_spaces pass-through → JSON list including a newly added space."""
    _insert_space(kb_conn, name="sp_a", status="populating")
    with patch.object(emb, "_get_conn", return_value=kb_conn):
        result = json.loads(emb.list_embed_spaces_tool())
    assert isinstance(result, list)
    names = {s["name"] for s in result}
    assert names == {"default", "sp_a"}


# --------------------------------------------------------------------------- #
# 4. create_embed_space_tool
# --------------------------------------------------------------------------- #


def test_create_embed_space_success(kb_conn):
    """Valid new space → success JSON with the documented shape."""
    with patch.object(emb, "_get_conn", return_value=kb_conn):
        result = json.loads(
            emb.create_embed_space_tool(
                name="new_sp", model="m", dim=128, provider="ollama"
            )
        )
    assert result["space"] == "new_sp"
    assert result["status"] == "populating"
    assert result["element_type"] == "float32"
    # Registry row really created.
    row = kb_conn.execute(
        "SELECT status FROM embed_spaces WHERE name = 'new_sp'"
    ).fetchone()
    assert row["status"] == "populating"


def test_create_embed_space_duplicate_name_error(kb_conn):
    """Duplicate name → create_space raises ValueError → error mapping."""
    with patch.object(emb, "_get_conn", return_value=kb_conn):
        emb.create_embed_space_tool(name="dup", model="m", dim=128, provider="ollama")
        result = json.loads(
            emb.create_embed_space_tool(
                name="dup", model="m", dim=128, provider="ollama"
            )
        )
    assert "error" in result
    assert "already exists" in result["error"]


def test_create_embed_space_invalid_name_error(kb_conn):
    """Non-alphanumeric name → ValueError → error mapping."""
    with patch.object(emb, "_get_conn", return_value=kb_conn):
        result = json.loads(
            emb.create_embed_space_tool(
                name="bad name!", model="m", dim=128, provider="ollama"
            )
        )
    assert "error" in result
    assert "alphanumeric" in result["error"]


# --------------------------------------------------------------------------- #
# 5. backfill_embed_space_tool
# --------------------------------------------------------------------------- #


def test_backfill_embed_space_unknown_name_error(kb_conn):
    """Unknown space → real backfill_space raises ValueError → error mapping."""
    with patch.object(emb, "_get_conn", return_value=kb_conn):
        result = json.loads(emb.backfill_embed_space_tool("nope"))
    assert "error" in result
    assert "not found" in result["error"]


def test_backfill_embed_space_happy_passthrough(kb_conn):
    """backfill_space mocked → JSON pass-through of its dict."""
    payload = {"space": "sp", "chunks_processed": 7, "total_chunks": 7}
    with (
        patch.object(emb, "_get_conn", return_value=kb_conn),
        patch.object(emb, "backfill_space", return_value=payload) as mock_bf,
    ):
        result = json.loads(emb.backfill_embed_space_tool("sp", batch_size=16))
    mock_bf.assert_called_once_with(kb_conn, "sp", 16)
    assert result == payload


# --------------------------------------------------------------------------- #
# 6. promote_embed_space_tool
# --------------------------------------------------------------------------- #


def test_promote_embed_space_unknown_name_error(kb_conn):
    """Unknown space → real promote_space raises ValueError → error mapping."""
    with patch.object(emb, "_get_conn", return_value=kb_conn):
        result = json.loads(emb.promote_embed_space_tool("ghost"))
    assert "error" in result
    assert "not found" in result["error"]


def test_promote_embed_space_happy_deletes_similar_and_adds_note(kb_conn):
    """promote_space mocked → wrapper DELETEs 'similar' rels and adds 'note'."""
    _insert_similar_relationship(kb_conn)
    assert _count_similar(kb_conn) == 1

    with (
        patch.object(emb, "_get_conn", return_value=kb_conn),
        patch.object(
            emb,
            "promote_space",
            return_value={"promoted": "sp_new", "deprecated": "sp_old"},
        ) as mock_promote,
    ):
        result = json.loads(emb.promote_embed_space_tool("sp_new"))

    mock_promote.assert_called_once_with(kb_conn, "sp_new")
    assert result["promoted"] == "sp_new"
    assert result["deprecated"] == "sp_old"
    assert "note" in result
    assert "similar" in result["note"]
    assert _count_similar(kb_conn) == 0


# --------------------------------------------------------------------------- #
# 7. deprecate_embed_space_tool
# --------------------------------------------------------------------------- #


def test_deprecate_embed_space_active_error(kb_conn):
    """Deprecating the active space (seeded 'default') → ValueError → error mapping."""
    with patch.object(emb, "_get_conn", return_value=kb_conn):
        result = json.loads(emb.deprecate_embed_space_tool("default"))
    assert "error" in result
    assert "active" in result["error"]


def test_deprecate_embed_space_success(kb_conn):
    """Deprecating a non-active (populating) space → success JSON."""
    _insert_space(kb_conn, name="pop_sp", status="populating")
    with patch.object(emb, "_get_conn", return_value=kb_conn):
        result = json.loads(emb.deprecate_embed_space_tool("pop_sp"))
    assert result == {"deprecated": "pop_sp"}
    row = kb_conn.execute(
        "SELECT status FROM embed_spaces WHERE name = 'pop_sp'"
    ).fetchone()
    assert row["status"] == "deprecated"


# --------------------------------------------------------------------------- #
# 8. cleanup_embed_space_tool
# --------------------------------------------------------------------------- #


def test_cleanup_embed_space_not_deprecated_error(kb_conn):
    """Cleaning a non-deprecated (populating) space → ValueError → error mapping."""
    _insert_space(kb_conn, name="pop2", status="populating")
    with patch.object(emb, "_get_conn", return_value=kb_conn):
        result = json.loads(emb.cleanup_embed_space_tool("pop2"))
    assert "error" in result
    assert "deprecated" in result["error"]


def test_cleanup_embed_space_success(kb_conn):
    """Deprecated space → cleanup succeeds and removes the registry entry."""
    _insert_space(kb_conn, name="dep_sp", status="deprecated")
    with patch.object(emb, "_get_conn", return_value=kb_conn):
        result = json.loads(emb.cleanup_embed_space_tool("dep_sp"))
    assert result == {"cleaned": "dep_sp"}
    row = kb_conn.execute("SELECT 1 FROM embed_spaces WHERE name = 'dep_sp'").fetchone()
    assert row is None


# --------------------------------------------------------------------------- #
# 9. compare_spaces_tool
# --------------------------------------------------------------------------- #


def test_compare_spaces_happy_passthrough(kb_conn):
    """compare_spaces mocked → JSON pass-through."""
    payload = {"query": "q", "metrics": {"overlap_at_k": 0.5}}
    with (
        patch.object(emb, "_get_conn", return_value=kb_conn),
        patch.object(emb, "compare_spaces", return_value=payload) as mock_cmp,
    ):
        result = json.loads(emb.compare_spaces_tool("q", "a", "b"))
    mock_cmp.assert_called_once_with(kb_conn, "q", "a", "b", 10, "vec")
    assert result == payload


def test_compare_spaces_value_error_mapping(kb_conn):
    """compare_spaces raising ValueError → error mapping."""
    with (
        patch.object(emb, "_get_conn", return_value=kb_conn),
        patch.object(
            emb, "compare_spaces", side_effect=ValueError("space x not found")
        ),
    ):
        result = json.loads(emb.compare_spaces_tool("q", "a", "b"))
    assert result == {"error": "space x not found"}


# --------------------------------------------------------------------------- #
# 10. batch_compare_spaces_tool
# --------------------------------------------------------------------------- #


def test_batch_compare_spaces_happy_passthrough(kb_conn):
    """batch_compare_spaces mocked → JSON pass-through."""
    payload = {"space_a": "a", "space_b": "b", "queries_analyzed": 2}
    with (
        patch.object(emb, "_get_conn", return_value=kb_conn),
        patch.object(emb, "batch_compare_spaces", return_value=payload) as mock_bcs,
    ):
        result = json.loads(
            emb.batch_compare_spaces_tool("a", "b", ["q1", "q2"], top_k=5)
        )
    mock_bcs.assert_called_once_with(kb_conn, "a", "b", ["q1", "q2"], 5, "vec")
    assert result == payload


def test_batch_compare_spaces_value_error_mapping(kb_conn):
    """batch_compare_spaces raising ValueError → error mapping."""
    with (
        patch.object(emb, "_get_conn", return_value=kb_conn),
        patch.object(emb, "batch_compare_spaces", side_effect=ValueError("bad space")),
    ):
        result = json.loads(emb.batch_compare_spaces_tool("a", "b", ["q"]))
    assert result == {"error": "bad space"}


# --------------------------------------------------------------------------- #
# 11. benchmark_spaces_tool — branch coverage
# --------------------------------------------------------------------------- #


def test_benchmark_no_spaces(kb_conn):
    """list_spaces empty → 'No embedding spaces found'."""
    with (
        patch.object(emb, "_get_conn", return_value=kb_conn),
        patch.object(emb, "list_spaces", return_value=[]),
    ):
        result = json.loads(emb.benchmark_spaces_tool())
    assert result == {"error": "No embedding spaces found"}


def test_benchmark_no_active_no_baseline(kb_conn):
    """baseline_space=None and no active space → error."""
    spaces = [
        {"name": "sp", "dim": 256, "status": "populating", "element_type": "float32"}
    ]
    with (
        patch.object(emb, "_get_conn", return_value=kb_conn),
        patch.object(emb, "list_spaces", return_value=spaces),
        patch.object(emb, "get_active_space", return_value=None),
    ):
        result = json.loads(emb.benchmark_spaces_tool(baseline_space=None))
    assert result == {"error": "No active space and no baseline specified"}


def test_benchmark_explicit_baseline_not_found(kb_conn):
    """Explicit baseline not among spaces → 'Baseline space ... not found'."""
    spaces = [
        {"name": "sp", "dim": 256, "status": "populating", "element_type": "float32"}
    ]
    with (
        patch.object(emb, "_get_conn", return_value=kb_conn),
        patch.object(emb, "list_spaces", return_value=spaces),
    ):
        result = json.loads(emb.benchmark_spaces_tool(baseline_space="missing"))
    assert "error" in result
    assert "missing" in result["error"]
    assert "not found" in result["error"]


def test_benchmark_no_chunks(kb_conn):
    """Baseline resolves but DB has no chunks → 'No chunks ...' error."""
    spaces = [
        {"name": "base", "dim": 256, "status": "active", "element_type": "float32"}
    ]
    with (
        patch.object(emb, "_get_conn", return_value=kb_conn),
        patch.object(emb, "list_spaces", return_value=spaces),
    ):
        result = json.loads(emb.benchmark_spaces_tool(baseline_space="base"))
    assert "error" in result
    assert "No chunks" in result["error"]


def test_benchmark_happy_storage_ratio_int8_quarter(kb_conn):
    """Happy path: int8 space at same dim as float32 baseline → ratio 0.25.

    Verifies the storage-ratio math against the source:
    (bpe_space * dim_space) / (bpe_baseline * dim_baseline)
    = (1 * 256) / (4 * 256) = 0.25.
    """
    _insert_chunk(kb_conn, "some content to sample as a query")

    spaces = [
        {
            "name": "base",
            "dim": 256,
            "status": "active",
            "element_type": "float32",
            "chunk_count": 10,
        },
        {
            "name": "quant",
            "dim": 256,
            "status": "populating",
            "element_type": "int8",
            "chunk_count": 10,
        },
    ]
    comparison = {
        "overlap_at_k": {"mean": 0.8},
        "jaccard": {"mean": 0.7},
        "rank_correlation": {"mean": 0.9, "valid_count": 1},
        "warnings": ["w1"],
    }
    with (
        patch.object(emb, "_get_conn", return_value=kb_conn),
        patch.object(emb, "list_spaces", return_value=spaces),
        patch.object(emb, "batch_compare_spaces", return_value=comparison) as mock_bcs,
    ):
        result = json.loads(emb.benchmark_spaces_tool(baseline_space="base"))

    assert result["baseline"] == "base"
    assert result["baseline_element_type"] == "float32"
    assert result["queries_sampled"] == 1
    assert result["top_k"] == 10
    assert len(result["comparisons"]) == 1

    comp = result["comparisons"][0]
    assert comp["space"] == "quant"
    assert comp["element_type"] == "int8"
    assert comp["dim"] == 256
    # int8(1 byte) vs float32(4 bytes) at equal dim → 0.25.
    assert comp["storage_ratio_vs_baseline"] == 0.25
    assert comp["metrics"] == {
        "overlap_at_k": {"mean": 0.8},
        "jaccard": {"mean": 0.7},
        "rank_correlation": {"mean": 0.9, "valid_count": 1},
    }
    assert comp["warnings"] == ["w1"]
    mock_bcs.assert_called_once()


def test_benchmark_skips_deprecated_and_baseline(kb_conn):
    """Deprecated spaces and the baseline itself are excluded from comparisons."""
    _insert_chunk(kb_conn, "query content")

    spaces = [
        {"name": "base", "dim": 128, "status": "active", "element_type": "float32"},
        {"name": "dep", "dim": 128, "status": "deprecated", "element_type": "float32"},
        {"name": "cand", "dim": 128, "status": "populating", "element_type": "float32"},
    ]
    comparison = {
        "overlap_at_k": {"mean": 1.0},
        "jaccard": {"mean": 1.0},
        "rank_correlation": {"mean": 1.0, "valid_count": 1},
        "warnings": [],
    }
    with (
        patch.object(emb, "_get_conn", return_value=kb_conn),
        patch.object(emb, "list_spaces", return_value=spaces),
        patch.object(emb, "batch_compare_spaces", return_value=comparison),
    ):
        result = json.loads(emb.benchmark_spaces_tool(baseline_space="base"))

    names = [c["space"] for c in result["comparisons"]]
    assert names == ["cand"]
    # Same element_type + dim as baseline → ratio 1.0.
    assert result["comparisons"][0]["storage_ratio_vs_baseline"] == 1.0


def test_benchmark_per_space_comparison_error_captured(kb_conn):
    """A space whose batch_compare_spaces raises → per-space {'error': ...} entry."""
    _insert_chunk(kb_conn, "query content")

    spaces = [
        {"name": "base", "dim": 128, "status": "active", "element_type": "float32"},
        {"name": "cand", "dim": 128, "status": "populating", "element_type": "float32"},
    ]
    with (
        patch.object(emb, "_get_conn", return_value=kb_conn),
        patch.object(emb, "list_spaces", return_value=spaces),
        patch.object(emb, "batch_compare_spaces", side_effect=ValueError("boom")),
    ):
        result = json.loads(emb.benchmark_spaces_tool(baseline_space="base"))

    assert result["comparisons"] == [{"space": "cand", "error": "boom"}]


def test_benchmark_defaults_to_active_space(kb_conn):
    """baseline_space=None + an active space → that space is used as baseline."""
    _insert_chunk(kb_conn, "query content")

    spaces = [
        {"name": "base", "dim": 64, "status": "active", "element_type": "float32"},
        {"name": "cand", "dim": 64, "status": "populating", "element_type": "float32"},
    ]
    comparison = {
        "overlap_at_k": {"mean": 0.5},
        "jaccard": {"mean": 0.4},
        "rank_correlation": {"mean": 0.6, "valid_count": 1},
        "warnings": [],
    }
    with (
        patch.object(emb, "_get_conn", return_value=kb_conn),
        patch.object(emb, "list_spaces", return_value=spaces),
        patch.object(emb, "get_active_space", return_value={"name": "base"}),
        patch.object(emb, "batch_compare_spaces", return_value=comparison),
    ):
        result = json.loads(emb.benchmark_spaces_tool(baseline_space=None))

    assert result["baseline"] == "base"
    assert [c["space"] for c in result["comparisons"]] == ["cand"]
