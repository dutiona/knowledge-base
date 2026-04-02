"""Tests for folder-level semantic embeddings."""

from unittest.mock import patch

from knowledge_base.db import DEFAULT_EMBED_DIM, get_connection, init_schema
from knowledge_base.folder_summaries import compute_folder_hash, update_folder_summary
from knowledge_base.ingest import ingest_file


def _fake_embed(texts, model="bge-m3", expected_dim=None, **_kwargs):
    dim = expected_dim if expected_dim is not None else DEFAULT_EMBED_DIM
    return [[0.1] * dim for _ in texts]


def test_folder_summaries_table_exists(tmp_path):
    """init_schema creates folder_summaries and folder_summaries_vec tables."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table')"
        ).fetchall()
    }
    assert "folder_summaries" in tables
    assert "folder_summaries_vec" in tables


def test_init_schema_idempotent_folder_summaries(tmp_path):
    """Calling init_schema twice doesn't error on folder tables."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    # Insert a row to verify data survives the second init
    conn.execute(
        "INSERT INTO folder_summaries (folder_path, summary, content_hash) VALUES (?, ?, ?)",
        ("/test", "test summary", "abc123"),
    )
    conn.commit()

    init_schema(conn)  # should not raise or drop data

    row = conn.execute(
        "SELECT * FROM folder_summaries WHERE folder_path = '/test'"
    ).fetchone()
    assert row is not None
    assert row["summary"] == "test summary"


def test_folder_summaries_columns(tmp_path):
    """folder_summaries has the expected columns."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    cols = {
        row[1] for row in conn.execute("PRAGMA table_info(folder_summaries)").fetchall()
    }
    assert cols == {"folder_path", "summary", "content_hash", "updated_at"}


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_compute_folder_hash_changes_with_content(tmp_path):
    """Hash changes when folder contents change."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    folder = tmp_path / "papers"
    folder.mkdir()
    (folder / "a.md").write_text("Paper about attention mechanisms.\n")
    ingest_file(conn, folder / "a.md")

    hash1 = compute_folder_hash(conn, str(folder))

    (folder / "b.md").write_text("Paper about transformers.\n")
    ingest_file(conn, folder / "b.md")

    hash2 = compute_folder_hash(conn, str(folder))
    assert hash1 != hash2


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_update_folder_summary_creates_entry(tmp_path):
    """update_folder_summary inserts a new folder summary."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    folder = tmp_path / "papers"
    folder.mkdir()
    (folder / "a.md").write_text("Paper about attention mechanisms.\n")
    ingest_file(conn, folder / "a.md")

    # ingest_file auto-triggers update_folder_summary, so entry already exists
    row = conn.execute(
        "SELECT * FROM folder_summaries WHERE folder_path = ?",
        (str(folder),),
    ).fetchone()
    assert row is not None
    assert "attention" in row["summary"].lower()

    # Calling again with unchanged content returns False (no-op)
    assert update_folder_summary(conn, str(folder)) is False


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_update_folder_summary_skips_when_unchanged(tmp_path):
    """update_folder_summary returns False when content hash hasn't changed."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    folder = tmp_path / "papers"
    folder.mkdir()
    (folder / "a.md").write_text("Paper about attention.\n")
    ingest_file(conn, folder / "a.md")

    # ingest_file already triggered update, so second call is a no-op
    assert update_folder_summary(conn, str(folder)) is False


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_update_folder_summary_updates_stale_entry(tmp_path):
    """update_folder_summary updates when content changes."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    folder = tmp_path / "papers"
    folder.mkdir()
    (folder / "a.md").write_text("Paper about attention.\n")
    ingest_file(conn, folder / "a.md")

    # ingest_file auto-triggers, so summary exists
    old_row = conn.execute(
        "SELECT summary FROM folder_summaries WHERE folder_path = ?",
        (str(folder),),
    ).fetchone()
    assert old_row is not None

    (folder / "b.md").write_text("Paper about diffusion models.\n")
    ingest_file(conn, folder / "b.md")

    # ingest_file auto-updated, verify content changed
    new_row = conn.execute(
        "SELECT summary FROM folder_summaries WHERE folder_path = ?",
        (str(folder),),
    ).fetchone()
    assert new_row["summary"] != old_row["summary"]


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_ingest_file_triggers_folder_summary(tmp_path):
    """Ingesting a file automatically creates/updates its folder's summary."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    folder = tmp_path / "papers"
    folder.mkdir()
    (folder / "a.md").write_text("Paper about attention mechanisms.\n")
    ingest_file(conn, folder / "a.md")

    row = conn.execute(
        "SELECT * FROM folder_summaries WHERE folder_path = ?",
        (str(folder),),
    ).fetchone()
    assert row is not None
    assert row["summary"]  # non-empty


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch(
    "knowledge_base.search.embed_single",
    lambda text, model="bge-m3", **_kw: [0.1] * DEFAULT_EMBED_DIM,
)
def test_search_folder_summaries_populated(tmp_path):
    """Ingesting files into folders creates folder summaries and vec entries."""
    from knowledge_base.search import search

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    ml_folder = tmp_path / "machine-learning"
    ml_folder.mkdir()
    (ml_folder / "attention.md").write_text(
        "Attention mechanisms in neural networks enable selective focus on input.\n"
    )
    ingest_file(conn, ml_folder / "attention.md")

    bio_folder = tmp_path / "biology"
    bio_folder.mkdir()
    (bio_folder / "cells.md").write_text(
        "Cell biology studies the structure and function of living organisms.\n"
    )
    ingest_file(conn, bio_folder / "cells.md")

    assert conn.execute("SELECT count(*) FROM folder_summaries").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM folder_summaries_vec").fetchone()[0] == 2

    # Search still works with folder summaries present
    results = search(conn, "attention", mode="hybrid")
    assert len(results) >= 1


def test_folder_boost_multiplies_scores(tmp_path):
    """_folder_boost multiplies scores for chunks in matching folders."""
    from knowledge_base.search import _folder_boost, _serialize_f32

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Manually insert two chunks in different folders
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index)"
        " VALUES (?, ?, ?, ?, ?)",
        ("h1", "attention content", "markdown", "/papers/ml/a.md", 0),
    )
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index)"
        " VALUES (?, ?, ?, ?, ?)",
        ("h2", "biology content", "markdown", "/papers/bio/b.md", 0),
    )
    chunk_ids = [1, 2]
    dim = DEFAULT_EMBED_DIM

    # Insert a folder summary vec for /papers/ml (but not /papers/bio)
    conn.execute(
        "INSERT INTO folder_summaries (folder_path, summary, content_hash) VALUES (?, ?, ?)",
        ("/papers/ml", "ml summary", "hash1"),
    )
    conn.execute(
        "INSERT INTO folder_summaries_vec (embedding, folder_path) VALUES (?, ?)",
        (_serialize_f32([0.1] * dim), "/papers/ml"),
    )
    conn.commit()

    scores = {1: 0.5, 2: 0.5}
    query_embedding = [0.1] * dim
    boosted = _folder_boost(conn, query_embedding, chunk_ids, scores)

    # Chunk 1 (/papers/ml) should be boosted, chunk 2 (/papers/bio) should not
    assert boosted[1] > scores[1]
    assert boosted[2] == scores[2]


def test_folder_boost_zero_distance_does_not_boost_all(tmp_path):
    """When best_distance==0, only near-exact folder matches should be boosted."""
    from knowledge_base.search import _folder_boost, _serialize_f32

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    dim = DEFAULT_EMBED_DIM
    # Query and matching folder share the same vector → L2 distance = 0
    exact_vec = [0.1] * dim
    # Distant folder uses a very different vector → large L2 distance
    distant_vec = [0.9] * dim

    # Two chunks in different folders
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index)"
        " VALUES (?, ?, ?, ?, ?)",
        ("h1", "ml content", "markdown", "/papers/ml/a.md", 0),
    )
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index)"
        " VALUES (?, ?, ?, ?, ?)",
        ("h2", "bio content", "markdown", "/papers/bio/b.md", 0),
    )

    # Two folder summaries: one exact match, one distant
    conn.execute(
        "INSERT INTO folder_summaries (folder_path, summary, content_hash) VALUES (?, ?, ?)",
        ("/papers/ml", "ml summary", "hash_ml"),
    )
    conn.execute(
        "INSERT INTO folder_summaries_vec (embedding, folder_path) VALUES (?, ?)",
        (_serialize_f32(exact_vec), "/papers/ml"),
    )
    conn.execute(
        "INSERT INTO folder_summaries (folder_path, summary, content_hash) VALUES (?, ?, ?)",
        ("/papers/bio", "bio summary", "hash_bio"),
    )
    conn.execute(
        "INSERT INTO folder_summaries_vec (embedding, folder_path) VALUES (?, ?)",
        (_serialize_f32(distant_vec), "/papers/bio"),
    )
    conn.commit()

    scores = {1: 0.5, 2: 0.5}
    boosted = _folder_boost(conn, exact_vec, [1, 2], scores)

    # Only the exact-match folder (/papers/ml) should be boosted
    assert boosted[1] > scores[1], "exact-match folder chunk should be boosted"
    assert boosted[2] == scores[2], "distant folder chunk should NOT be boosted"


def test_folder_boost_no_folders_is_noop(tmp_path):
    """_folder_boost returns original scores when no folder summaries exist."""
    from knowledge_base.search import _folder_boost

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    scores = {1: 0.5}
    result = _folder_boost(conn, [0.1] * DEFAULT_EMBED_DIM, [1], scores)
    assert result == scores


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_update_folder_summary_empty_folder(tmp_path):
    """update_folder_summary returns False for a folder with no indexed chunks."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    empty_folder = tmp_path / "empty"
    empty_folder.mkdir()

    assert update_folder_summary(conn, str(empty_folder)) is False


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_folder_summary_ignores_subdirectory_files(tmp_path):
    """Folder summary only includes direct children, not files in subdirectories."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    parent = tmp_path / "research"
    parent.mkdir()
    child = parent / "subdir"
    child.mkdir()

    (parent / "top.md").write_text("Top-level paper about attention.\n")
    (child / "nested.md").write_text("Nested paper about transformers.\n")

    ingest_file(conn, parent / "top.md")
    ingest_file(conn, child / "nested.md")

    # Parent folder summary should only contain top.md
    row = conn.execute(
        "SELECT summary FROM folder_summaries WHERE folder_path = ?",
        (str(parent),),
    ).fetchone()
    assert row is not None
    assert "top.md" in row["summary"]
    assert "nested.md" not in row["summary"]

    # Child folder summary should only contain nested.md
    child_row = conn.execute(
        "SELECT summary FROM folder_summaries WHERE folder_path = ?",
        (str(child),),
    ).fetchone()
    assert child_row is not None
    assert "nested.md" in child_row["summary"]
    assert "top.md" not in child_row["summary"]


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_folder_hash_escapes_like_wildcards(tmp_path):
    """Underscores in folder names must not act as LIKE wildcards."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Folder with underscore: without LIKE escaping, "a_c/" matches "aXc/"
    folder_a = tmp_path / "a_c"
    folder_a.mkdir()
    (folder_a / "file.md").write_text("Content in underscore folder.\n")
    ingest_file(conn, folder_a / "file.md")

    # Record hash BEFORE adding a similarly-named folder
    hash_before = compute_folder_hash(conn, str(folder_a))

    # Add a folder that only differs at the _ position
    folder_b = tmp_path / "aXc"
    folder_b.mkdir()
    (folder_b / "file.md").write_text("Different content in X folder.\n")
    ingest_file(conn, folder_b / "file.md")

    # Hash must not change — folder_a's query should not pick up folder_b's chunks
    hash_after = compute_folder_hash(conn, str(folder_a))
    assert hash_before == hash_after, (
        "Folder hash changed after adding unrelated folder — "
        "_ in folder name is being treated as LIKE wildcard"
    )


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_ingest_directory_batches_folder_summaries(tmp_path):
    """ingest_directory updates folder summaries once per folder, not per file."""
    from knowledge_base.ingest import ingest_directory

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    folder = tmp_path / "papers"
    folder.mkdir()
    (folder / "a.md").write_text("Paper about attention.\n")
    (folder / "b.md").write_text("Paper about diffusion.\n")
    (folder / "c.md").write_text("Paper about transformers.\n")

    with patch("knowledge_base.ingest.update_folder_summary") as mock_update:
        ingest_directory(conn, folder)

    # Should be called exactly once for the folder (batch), not 3 times (per-file)
    assert mock_update.call_count == 1


# ---------- Windows path normalization (#158) ----------


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_source_uri_uses_forward_slashes(tmp_path):
    """source_uri stored in the DB must always use forward slashes."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    folder = tmp_path / "papers"
    folder.mkdir()
    (folder / "a.md").write_text("Paper about attention.\n")
    ingest_file(conn, folder / "a.md")

    row = conn.execute("SELECT source_uri FROM chunks LIMIT 1").fetchone()
    assert "\\" not in row["source_uri"], (
        f"source_uri contains backslashes: {row['source_uri']}"
    )
    assert row["source_uri"] == (folder / "a.md").as_posix()


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_folder_summary_path_uses_forward_slashes(tmp_path):
    """Folder summary folder_path must use forward slashes to match source_uris."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    folder = tmp_path / "papers"
    folder.mkdir()
    (folder / "a.md").write_text("Paper about attention.\n")
    ingest_file(conn, folder / "a.md")

    row = conn.execute("SELECT folder_path FROM folder_summaries LIMIT 1").fetchone()
    assert row is not None
    assert "\\" not in row["folder_path"], (
        f"folder_path contains backslashes: {row['folder_path']}"
    )
    assert row["folder_path"] == folder.as_posix()


def test_backslash_uris_break_folder_summary_matching(tmp_path):
    """Backslash source_uris would not be matched by LIKE queries (regression guard)."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Simulate a Windows-style backslash source_uri in the DB
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index)"
        " VALUES (?, ?, ?, ?, ?)",
        ("h1", "some content", "markdown", "C:\\Users\\foo\\papers\\a.md", 0),
    )
    conn.commit()

    # Forward-slash LIKE query (what folder_summaries uses) should NOT match
    fwd_hash = compute_folder_hash(conn, "C:/Users/foo/papers")
    assert fwd_hash == "", (
        "Forward-slash folder query should not match backslash source_uri"
    )

    # Backslash LIKE query would match, but we never generate one —
    # the point is normalization at ingestion time prevents the mismatch
