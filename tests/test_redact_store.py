"""Purging deleted text from the database file, and the ``redact`` maintenance command (#592)."""

from __future__ import annotations

import json
import random
import sqlite3
from itertools import pairwise
from pathlib import Path

import pytest

from knowledge_base.db import insert_chunk_vec, purge_deleted_content
from knowledge_base.embed_swap import create_space
from knowledge_base.exceptions import KnowledgeBaseError
from knowledge_base.ingest import _insert_chunk
from knowledge_base.utils import content_hash, serialize_f32
from tests.declared_env import TEST_EMBED_DIM, TEST_EMBED_MODEL

SECRET = "ghp_" + "Zq7Xw3Rt9Lp2Vb6Nm4Kc8Hd1Fs5Gj0Ay"  # an example shape, built here and nowhere committed as one


def _vector(seed: int) -> list[float]:
    rng = random.Random(seed)
    return [rng.uniform(-1, 1) for _ in range(TEST_EMBED_DIM)]


def _windows(data: bytes, size: int) -> set[bytes]:
    return {data[i : i + size] for i in range(len(data) - size + 1)}


def _file_bytes(db: Path) -> bytes:
    wal = db.with_name(db.name + "-wal")
    return db.read_bytes() + (wal.read_bytes() if wal.exists() else b"")


def _seed(conn: sqlite3.Connection) -> dict[str, list[tuple[int, list[float]]]]:
    """Chunks in two spaces and folder summaries, with deletion gaps in every vec table."""
    create_space(conn, "alt", TEST_EMBED_MODEL, TEST_EMBED_DIM)
    kept: dict[str, list[tuple[int, list[float]]]] = {
        "chunks_vec": [],
        "chunks_vec_alt": [],
        "folder_summaries_vec": [],
    }
    for i in range(12):
        text = f"passage {i} " + ("the key is " + SECRET if i == 5 else "nothing to see")
        cid = _insert_chunk(
            conn,
            content_hash=content_hash(text),
            content=text,
            source_type="markdown",
            source_uri=f"/d/{i}.md",
            chunk_index=0,
        )
        for table, seed in (("chunks_vec", cid), ("chunks_vec_alt", 1000 + cid)):
            insert_chunk_vec(conn, cid, _vector(seed), table_name=table)
            kept[table].append((cid, _vector(seed)))
    for i in range(6):
        path = f"/folder{i}"
        conn.execute(
            "INSERT INTO folder_summaries (folder_path, summary, content_hash) VALUES (?, ?, ?)", (path, path, path)
        )
        conn.execute(
            "INSERT INTO folder_summaries_vec (embedding, folder_path) VALUES (?, ?)",
            (serialize_f32(_vector(2000 + i)), path),
        )
        kept["folder_summaries_vec"].append((i, _vector(2000 + i)))
    conn.commit()
    return kept


def test_purge_leaves_no_deleted_text_in_the_file(kb_conn, tmp_path):
    db = tmp_path / "test.db"
    kb_conn.execute("PRAGMA wal_autocheckpoint=0")
    kept = _seed(kb_conn)
    secret_id = kb_conn.execute("SELECT id FROM chunks WHERE content LIKE ?", (f"%{SECRET}%",)).fetchone()["id"]
    old_vector = serialize_f32(_vector(secret_id))
    gaps = [2, 7, 9]
    for table in ("chunks_vec", "chunks_vec_alt"):
        kb_conn.execute(f"DELETE FROM [{table}] WHERE chunk_id IN (2, 7, 9, {secret_id})")  # noqa: S608
        kept[table] = [(cid, v) for cid, v in kept[table] if cid not in (*gaps, secret_id)]
    kb_conn.execute("DELETE FROM folder_summaries_vec WHERE folder_path IN ('/folder1', '/folder4')")
    kept["folder_summaries_vec"] = [(i, v) for i, v in kept["folder_summaries_vec"] if i not in (1, 4)]
    redacted = "passage 5 the key is [REDACTED:github-pat]"
    kb_conn.execute(
        "UPDATE chunks SET content = ?, content_hash = ? WHERE id = ?", (redacted, content_hash(redacted), secret_id)
    )
    insert_chunk_vec(kb_conn, secret_id, _vector(5000), table_name="chunks_vec")
    kept["chunks_vec"].append((secret_id, _vector(5000)))
    kb_conn.commit()
    assert _windows(SECRET.encode(), 12) & _windows(_file_bytes(db), 12), "precondition: the residue is real"

    purge_deleted_content(kb_conn)

    data = _file_bytes(db)
    assert not _windows(SECRET.encode(), 12) & _windows(data, 12)
    assert old_vector[:16] not in data and old_vector[-16:] not in data
    assert kb_conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    for table, rows in kept.items():
        key = "folder_path" if table == "folder_summaries_vec" else "chunk_id"
        for ident, vector in rows:
            top = kb_conn.execute(
                f"SELECT {key} FROM [{table}] WHERE embedding MATCH ? ORDER BY distance LIMIT 1",  # noqa: S608
                (serialize_f32(vector),),
            ).fetchone()[0]
            assert top == (f"/folder{ident}" if table == "folder_summaries_vec" else ident)


def test_purge_refuses_while_another_connection_reads(kb_conn, tmp_path):
    _seed(kb_conn)
    reader = sqlite3.connect(tmp_path / "test.db")
    reader.execute("BEGIN")
    reader.execute("SELECT count(*) FROM chunks").fetchone()
    before = list(kb_conn.iterdump())
    try:
        with pytest.raises(KnowledgeBaseError, match="stop the server"):
            purge_deleted_content(kb_conn)
    finally:
        reader.close()
    assert list(kb_conn.iterdump()) == before


def test_purge_refuses_an_idle_open_connection(kb_conn, tmp_path):
    """An idle server connection is the case a checkpoint cannot see; the exclusive file lock can."""
    _seed(kb_conn)
    idle = sqlite3.connect(tmp_path / "test.db")
    idle.execute("SELECT 1 FROM chunks").fetchone()
    try:
        with pytest.raises(KnowledgeBaseError, match="stop the server"):
            purge_deleted_content(kb_conn)
    finally:
        idle.close()
    purge_deleted_content(kb_conn)  # the lock was released: alone, it runs


def test_purge_refuses_an_open_transaction(kb_conn):
    kb_conn.execute("UPDATE chunks SET content = content")
    with pytest.raises(KnowledgeBaseError, match="no open transaction"):
        purge_deleted_content(kb_conn)


# ---------------------------------------------------------------------------
# knowledge-base-ingest redact: the report
# ---------------------------------------------------------------------------

AWS_KEY = "AKIA" + "QX7RT9LP2VB6NM4K"
QUERY_VALUE = "Xk9Lp2Rt5Yv8Bn1Cx4Df6Qw7Zz0Aa"


def _raw_chunk(conn, content: str, *, uri: str = "/d/doc.md", metadata: str = "{}", index: int = 0) -> int:
    """A row as stored before #592: no redaction on the way in."""
    cur = conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, metadata) "
        "VALUES (?, ?, 'markdown', ?, ?, ?)",
        (content_hash(content), content, uri, index, metadata),
    )
    return int(cur.lastrowid or 0)


def _run(db: Path, *argv: str) -> int:
    from knowledge_base.indexer import main

    with pytest.raises(SystemExit) as done:
        main(["--db", str(db), *argv])
    return int(done.value.code or 0)


def _seed_dirty(conn) -> dict[str, int]:
    ids = {
        "content": _raw_chunk(conn, f"the deploy key is {SECRET}", uri="/d/a.md"),
        "metadata": _raw_chunk(conn, "plain one", uri="/d/b.md", metadata=json.dumps({"title": f"aws {AWS_KEY}"})),
        "uri": _raw_chunk(conn, "plain two", uri="https://reader:example-pass@docs.example.test/p"),
        "clean": _raw_chunk(conn, "plain three", uri="/d/c.md"),
    }
    conn.execute(
        "INSERT INTO folder_summaries (folder_path, summary, content_hash) VALUES ('/d', ?, 'x')", (f"a.md: {SECRET}",)
    )
    conn.commit()
    return ids


def test_report_counts_without_printing_matches(kb_conn, tmp_path, capsys):
    ids = _seed_dirty(kb_conn)
    before = list(kb_conn.iterdump())
    assert _run(tmp_path / "test.db", "redact") == 1
    out, err = capsys.readouterr()
    report = json.loads(out)
    assert report["rows_scanned"] == 4
    assert report["rows_to_rewrite"] == {"content": 1, "metadata": 1, "source_uri": 1}
    assert report["chunk_ids"] == sorted([ids["content"], ids["metadata"], ids["uri"]])
    assert report["spans"] == {"github-pat": 1, "aws-access-key": 1}
    assert report["folder_summaries_to_rebuild"] == 1
    assert report["rewrite_incomplete"] is False
    for secret in (SECRET, AWS_KEY, "example-pass"):
        assert not _windows(secret.encode(), 8) & _windows((out + err).encode(), 8)
    assert list(kb_conn.iterdump()) == before


def test_report_on_a_clean_database_exits_zero(kb_conn, tmp_path, capsys):
    meta = json.dumps({"image_url": f"https://docs.example.test/i.png?sig={QUERY_VALUE}"})
    _insert_chunk(
        kb_conn,
        content_hash=content_hash("c"),
        content="c",
        source_type="figure",
        source_uri="https://docs.example.test/p",
        chunk_index=0,
        metadata=meta,
    )
    kb_conn.commit()
    assert _run(tmp_path / "test.db", "redact") == 0
    assert json.loads(capsys.readouterr().out)["chunk_ids"] == []


def test_report_flags_a_metadata_url_with_userinfo(kb_conn, tmp_path, capsys):
    meta = json.dumps({"image_url": f"https://reader:example-pass@docs.example.test/i.png?sig={QUERY_VALUE}"})
    cid = _raw_chunk(kb_conn, "c", uri="https://docs.example.test/p", metadata=meta)
    kb_conn.commit()
    assert _run(tmp_path / "test.db", "redact") == 1
    report = json.loads(capsys.readouterr().out)
    assert (report["chunk_ids"], report["rows_to_rewrite"]["metadata"]) == ([cid], 1)


# ---------------------------------------------------------------------------
# knowledge-base-ingest redact --rewrite (server stopped)
# ---------------------------------------------------------------------------

SECRET_2 = "ghp_" + "Bq7Xw3Rt9Lp2Vb6Nm4Kc8Hd1Fs5Gj0Ay"
SECRET_3 = "ghp_" + "Cq7Xw3Rt9Lp2Vb6Nm4Kc8Hd1Fs5Gj0Ay"


def _hash_vector(text: str) -> list[float]:
    return _vector(int(content_hash(text)[:12], 16))


class _Embedder(list):
    """Every text embedded, by any path; while ``down`` it fails as an unreachable endpoint does."""

    down = False

    def __call__(self, texts, *_args, expected_dim=None, **_kwargs):
        from tests.conftest import embedder_error

        if self.down:
            raise embedder_error(503)
        self.extend(texts)
        return [_hash_vector(t)[: expected_dim or TEST_EMBED_DIM] for t in texts]


@pytest.fixture
def vectors(monkeypatch) -> _Embedder:
    from unittest.mock import MagicMock

    embedder = _Embedder()
    provider = MagicMock()
    provider.embed.side_effect = lambda texts, model=None, expected_dim=TEST_EMBED_DIM: embedder(texts)
    monkeypatch.setattr("knowledge_base.ingest.embed", embedder)
    monkeypatch.setattr("knowledge_base.folder_summaries.embed", embedder)
    monkeypatch.setattr("knowledge_base.embed_swap.get_provider", lambda **_kw: provider)
    return embedder


def _with_vector(conn, content: str, **kw) -> int:
    cid = _raw_chunk(conn, content, **kw)
    insert_chunk_vec(conn, cid, _hash_vector(content))
    return cid


def _rows(db: Path) -> list[str]:
    conn = _open(db)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def _open(db: Path) -> sqlite3.Connection:
    from knowledge_base.db import get_connection

    return get_connection(db)


def test_rewrite_merges_rows_identical_after_redaction(kb_conn, tmp_path, vectors):
    db = tmp_path / "test.db"
    keep = _with_vector(kb_conn, "token [REDACTED:github-pat] x", uri="/d/k.md")
    dup = _with_vector(kb_conn, f"token {SECRET} x", uri="/d/dup.md")
    first = _with_vector(kb_conn, f"pw {SECRET_2} y", uri="/d/f.md")
    second = _with_vector(kb_conn, f"pw {SECRET_3} y", uri="/d/s.md")
    p1 = kb_conn.execute("INSERT INTO papers (title, abstract_chunk_id) VALUES ('P1', ?)", (dup,)).lastrowid
    p2 = kb_conn.execute("INSERT INTO papers (title) VALUES ('P2')").lastrowid
    kb_conn.execute(
        "INSERT INTO relationships (source_paper_id, target_paper_id, relation_type, evidence_chunk_id) "
        "VALUES (?, ?, 'cites', ?)",
        (p1, p2, second),
    )
    kb_conn.execute("INSERT INTO methods (name, paper_id, chunk_id) VALUES ('attn', ?, ?)", (p1, dup))
    eid = kb_conn.execute(
        "INSERT INTO entities (canonical_name, entity_type, paper_id) VALUES ('attn', 'method', ?)", (p1,)
    ).lastrowid
    for cid in (dup, keep):
        kb_conn.execute(
            "INSERT INTO entity_mentions (entity_id, surface_form, chunk_id) VALUES (?, 'attn', ?)", (eid, cid)
        )
    kb_conn.execute(
        "INSERT INTO prediction_errors (query, query_hash, top_chunk_id, error_type)"
        " VALUES ('q', 'h', ?, 'low_confidence')",
        (second,),
    )
    cid = kb_conn.execute(
        "INSERT INTO conclusions (claim, source_chunk_ids) VALUES ('c', ?)", (json.dumps([dup, keep, second]),)
    ).lastrowid
    for chunk, session in ((dup, "s1"), (keep, "s1"), (keep, "s2"), (second, "s3")):
        kb_conn.execute("INSERT INTO chunk_sessions (chunk_id, session_id) VALUES (?, ?)", (chunk, session))
    kb_conn.commit()
    kb_conn.close()

    assert _run(db, "redact", "--rewrite") == 0

    conn = _open(db)
    assert sorted(r[0] for r in conn.execute("SELECT id FROM chunks")) == sorted([keep, first])
    assert conn.execute("SELECT abstract_chunk_id FROM papers WHERE id = ?", (p1,)).fetchone()[0] == keep
    assert conn.execute("SELECT evidence_chunk_id FROM relationships").fetchone()[0] == first
    assert conn.execute("SELECT chunk_id FROM methods").fetchone()[0] == keep
    assert [r[0] for r in conn.execute("SELECT chunk_id FROM entity_mentions")] == [keep]
    assert conn.execute("SELECT top_chunk_id FROM prediction_errors").fetchone()[0] == first
    assert json.loads(conn.execute("SELECT source_chunk_ids FROM conclusions WHERE id = ?", (cid,)).fetchone()[0]) == [
        keep,
        first,
    ]
    sessions = {(r[0], r[1]) for r in conn.execute("SELECT chunk_id, session_id FROM chunk_sessions")}
    assert sessions == {(keep, "s1"), (keep, "s2"), (first, "s3")}
    conn.close()


def test_a_merge_drops_the_duplicate_s_truncation_record_with_its_vector(kb_conn, tmp_path, vectors):
    """#620: a cut describes the duplicate's own vector, which the merge deletes; the survivor's whole vector stays
    unrecorded."""
    db = tmp_path / "test.db"
    keep = _with_vector(kb_conn, "token [REDACTED:github-pat] x", uri="/d/k.md")
    dup = _with_vector(kb_conn, f"token {SECRET} x", uri="/d/dup.md")
    space = kb_conn.execute("SELECT name FROM embed_spaces WHERE status = 'active'").fetchone()[0]
    kb_conn.execute(
        "INSERT INTO embed_truncations (chunk_id, space_name, tokens_before, tokens_after) VALUES (?, ?, 9, 4)",
        (dup, space),
    )
    kb_conn.commit()
    kb_conn.close()

    assert _run(db, "redact", "--rewrite") == 0

    conn = _open(db)
    assert [r[0] for r in conn.execute("SELECT id FROM chunks")] == [keep]
    assert conn.execute("SELECT COUNT(*) FROM embed_truncations").fetchone()[0] == 0
    conn.close()


def test_rewrite_reembeds_from_redacted_text(kb_conn, tmp_path, vectors):
    from knowledge_base.embed_swap import _check_promotable, backfill_space

    db = tmp_path / "test.db"
    dirty = _with_vector(kb_conn, f"the key is {SECRET}", uri="/d/a.md")
    meta_only = _with_vector(kb_conn, "meta only", uri="/d/b.md", metadata=json.dumps({"title": f"aws {AWS_KEY}"}))
    uri_only = _with_vector(kb_conn, "uri only", uri="https://reader:example-pass@docs.example.test/p")
    clean = _with_vector(kb_conn, "clean", uri="/d/c.md")
    kb_conn.commit()
    for name, status in (("alt", "populating"), ("old", "deprecated")):
        create_space(kb_conn, name, TEST_EMBED_MODEL, TEST_EMBED_DIM)
        backfill_space(kb_conn, name)
        kb_conn.execute("UPDATE embed_spaces SET status = ? WHERE name = ?", (status, name))
    kb_conn.commit()
    kb_conn.close()
    vectors.clear()

    assert _run(db, "redact", "--rewrite") == 0

    assert vectors and not any(_windows(SECRET.encode(), 12) & _windows(t.encode(), 12) for t in vectors)
    assert not {"meta only", "uri only", "clean"} & set(vectors)
    conn = _open(db)
    content = conn.execute("SELECT content FROM chunks WHERE id = ?", (dirty,)).fetchone()[0]
    top = conn.execute(
        "SELECT chunk_id FROM chunks_vec WHERE embedding MATCH ? ORDER BY distance LIMIT 1",
        (serialize_f32(_hash_vector(content)),),
    ).fetchone()[0]
    assert top == dirty
    for table in ("chunks_vec_alt", "chunks_vec_old"):
        ids = {r[0] for r in conn.execute(f"SELECT chunk_id FROM [{table}]")}  # noqa: S608
        assert ids == {meta_only, uri_only, clean}

    def counts_match() -> bool:
        return all(
            row["chunk_count"] == conn.execute(f"SELECT COUNT(*) FROM [{row['table_name']}]").fetchone()[0]  # noqa: S608
            for row in conn.execute("SELECT table_name, chunk_count FROM embed_spaces").fetchall()
        )

    assert counts_match()
    alt = conn.execute("SELECT * FROM embed_spaces WHERE name = 'alt'").fetchone()
    with pytest.raises(ValueError, match="backfilled"):
        _check_promotable(conn, alt)
    backfill_space(conn, "alt")
    assert counts_match()
    _check_promotable(conn, conn.execute("SELECT * FROM embed_spaces WHERE name = 'alt'").fetchone())
    conn.close()


def test_rewrite_leaves_no_secret_bytes(kb_conn, tmp_path, vectors, capsys):
    db = tmp_path / "test.db"
    _with_vector(kb_conn, f"the deploy key is {SECRET}", uri="/d/a.md")
    _with_vector(kb_conn, "plain", uri="/d/b.md")
    kb_conn.execute(
        "INSERT INTO folder_summaries (folder_path, summary, content_hash) VALUES ('/d', ?, 'x')", (f"a.md: {SECRET}",)
    )
    kb_conn.commit()
    old_vector = serialize_f32(_hash_vector(f"the deploy key is {SECRET}"))
    kb_conn.close()

    assert _run(db, "redact", "--rewrite") == 0

    data = _file_bytes(db)
    assert not _windows(SECRET.encode(), 12) & _windows(data, 12)
    assert old_vector[:16] not in data
    conn = _open(db)
    assert not conn.execute("SELECT 1 FROM chunks_fts WHERE chunks_fts MATCH ?", (SECRET.split("_", 1)[1],)).fetchone()
    assert "[REDACTED:github-pat]" in conn.execute("SELECT summary FROM folder_summaries").fetchone()[0]
    conn.close()
    capsys.readouterr()
    assert _run(db, "redact") == 0
    before = _rows(db)
    assert _run(db, "redact", "--rewrite") == 0
    assert [r for r in _rows(db) if "redact_rewrite_pending" not in r] == before


def test_rewrite_strips_metadata_userinfo_and_keeps_query(kb_conn, tmp_path, vectors):
    db = tmp_path / "test.db"
    url = f"https://reader:example-pass@docs.example.test/i.png?sig={QUERY_VALUE}"
    cid = _with_vector(kb_conn, "c", uri="https://docs.example.test/p", metadata=json.dumps({"image_url": url}))
    kb_conn.commit()
    kb_conn.close()
    vectors.clear()
    assert _run(db, "redact", "--rewrite") == 0
    conn = _open(db)
    meta = json.loads(conn.execute("SELECT metadata FROM chunks WHERE id = ?", (cid,)).fetchone()[0])
    assert meta["image_url"] == f"https://docs.example.test/i.png?sig={QUERY_VALUE}"
    assert conn.execute("SELECT COUNT(*) FROM chunks_vec WHERE chunk_id = ?", (cid,)).fetchone()[0] == 1
    conn.close()
    assert vectors == []
    assert _run(db, "redact") == 0


def test_rewrite_embedder_failure_changes_nothing(kb_conn, tmp_path, vectors):
    db = tmp_path / "test.db"
    _with_vector(kb_conn, f"the key is {SECRET}")
    kb_conn.commit()
    kb_conn.close()
    before = _rows(db)
    vectors.down = True
    assert _run(db, "redact", "--rewrite") == 1
    assert _rows(db) == before


def test_rewrite_refuses_while_another_connection_reads(kb_conn, tmp_path, vectors):
    db = tmp_path / "test.db"
    _with_vector(kb_conn, f"the key is {SECRET}")
    kb_conn.commit()
    before = list(kb_conn.iterdump())
    assert _run(db, "redact", "--rewrite") == 1  # kb_conn is still open: the server's connection
    assert list(kb_conn.iterdump()) == before


@pytest.mark.parametrize("stage", ["after-commit", "summaries", "before-vacuum", "after-vacuum"])
def test_rewrite_resumes_after_an_interruption(kb_conn, tmp_path, vectors, monkeypatch, capsys, stage):
    from knowledge_base import redact_store
    from knowledge_base.exceptions import KnowledgeBaseError

    db = tmp_path / "test.db"
    _with_vector(kb_conn, f"the deploy key is {SECRET}", uri="/d/a.md")
    kb_conn.execute(
        "INSERT INTO folder_summaries (folder_path, summary, content_hash) VALUES ('/d', ?, 'x')", (SECRET,)
    )
    kb_conn.commit()
    kb_conn.close()

    def boom(*_a, **_k):
        raise KnowledgeBaseError(f"injected at {stage}")

    real_purge = redact_store.purge_deleted_content
    with monkeypatch.context() as m:
        if stage == "after-commit":
            m.setattr(redact_store, "_rebuild_summaries", boom)
        elif stage == "summaries":
            m.setattr("knowledge_base.folder_summaries.embed", boom)
        elif stage == "before-vacuum":
            m.setattr(redact_store, "purge_deleted_content", boom)
        else:
            m.setattr(redact_store, "purge_deleted_content", lambda conn: (real_purge(conn), boom()))
        assert _run(db, "redact", "--rewrite") == 1
    capsys.readouterr()
    assert _run(db, "redact") == 1
    assert json.loads(capsys.readouterr().out)["rewrite_incomplete"] is True
    assert _run(db, "redact", "--rewrite") == 0
    assert _run(db, "redact") == 0
    assert not _windows(SECRET.encode(), 12) & _windows(_file_bytes(db), 12)


def test_rewrite_rebuilds_a_summary_that_alone_holds_a_secret(kb_conn, tmp_path, vectors):
    from knowledge_base.folder_summaries import compute_folder_hash

    db = tmp_path / "test.db"
    _with_vector(kb_conn, "plain prose", uri="/d/a.md")
    kb_conn.execute(
        "INSERT INTO folder_summaries (folder_path, summary, content_hash) VALUES ('/d', ?, ?)",
        (f"a.md: {SECRET}", compute_folder_hash(kb_conn, "/d")),  # current, so only its text makes it stale
    )
    kb_conn.commit()
    kb_conn.close()
    assert _run(db, "redact", "--rewrite") == 0
    assert _run(db, "redact") == 0


def _legacy_windows(text: str, size: int = 1000, overlap: int = 200) -> list[str]:
    """The fixed windows KB's chunker stored before #592, cut wherever they ended."""
    return [text[start : start + size] for start in range(0, len(text) - overlap, size - overlap)]


@pytest.mark.parametrize(
    "at", [988, 996, 1580], ids=["fragment-ends-a-window", "prefix-ends-a-window", "fragment-starts-one"]
)
def test_rewrite_redacts_a_secret_the_old_chunker_cut_between_windows(kb_conn, tmp_path, vectors, capsys, at):
    """#592: a window can end, or start, inside a secret; that row's fragment matches no rule alone."""
    db = tmp_path / "test.db"
    text = ("lorem ipsum " * 200)[:at] + SECRET + " " + "dolor sit amet " * 80
    windows = _legacy_windows(text)
    holding = [i for i, w in enumerate(windows) if 800 * i < at + len(SECRET) and at < 800 * i + len(w)]
    assert sum(SECRET in windows[i] for i in holding) < len(holding), "a window must cut the secret"
    for i, window in enumerate(windows):
        _with_vector(kb_conn, window, uri="/d/long.md", index=i)
    kb_conn.commit()
    kb_conn.close()

    assert _run(db, "redact") == 1
    assert len(json.loads(capsys.readouterr().out)["chunk_ids"]) == len(holding)
    assert _run(db, "redact", "--rewrite") == 0
    assert not _windows(SECRET.encode(), 8) & _windows(_file_bytes(db), 8)
    capsys.readouterr()
    assert _run(db, "redact") == 0


@pytest.mark.parametrize("argv", [["redact"], ["redact", "--rewrite"]])
def test_redact_refuses_a_missing_database_instead_of_creating_it(tmp_path, capsys, argv):
    db = tmp_path / "typo.db"
    assert _run(db, *argv) == 1
    assert "no database" in capsys.readouterr().err
    assert not db.exists()


def test_rewrite_pairs_a_window_with_its_own_revision(kb_conn, tmp_path, vectors, capsys):
    """#592: a re-ingested source keeps several revisions under one URI, and their windows interleave by index."""
    db = tmp_path / "test.db"
    first = ("lorem ipsum " * 200)[:988] + SECRET + " " + "dolor sit amet " * 80
    second = "an edited revision " * 150
    for text in (first, second):
        for i, window in enumerate(_legacy_windows(text)):
            _with_vector(kb_conn, window, uri="content://notes/edited", index=i)
    kb_conn.commit()
    kb_conn.close()

    assert _run(db, "redact", "--rewrite") == 0
    assert not _windows(SECRET.encode(), 8) & _windows(_file_bytes(db), 8)


def test_report_reads_a_document_stored_by_the_new_chunker_as_clean(kb_conn, tmp_path, capsys):
    """#592: reading rows between their neighbours finds nothing in text ingest already redacted whole."""
    from knowledge_base.ingest import ingest_text

    db = tmp_path / "test.db"
    text = " ".join(f"word{i} https://data.example.test/t?api_key={'ab' * 16}&n={i} {SECRET}" for i in range(60))
    with pytest.MonkeyPatch.context() as m:
        m.setattr("knowledge_base.ingest._embed_with_config", lambda conn, texts: [None] * len(texts))
        ingest_text(kb_conn, text, source_uri="content://notes/long", source_type="markdown")
    contents = [r[0] for r in kb_conn.execute("SELECT content FROM chunks ORDER BY chunk_index")]
    assert any(a[-200:] == b[:200] for a, b in pairwise(contents)), "some rows must be read in context"
    kb_conn.close()
    assert _run(db, "redact") == 0


def test_rewrite_reads_a_shared_window_against_every_revision_after_it(kb_conn, tmp_path, vectors, capsys):
    """#592: two revisions can share a window and the start of the next one; each continuation is read."""
    db = tmp_path / "test.db"
    head = ("lorem ipsum " * 200)[:988] + SECRET[:12]
    first = head + SECRET[12:] + " " + "dolor sit amet " * 80
    second = head + "-- rewritten after here " * 60
    shared, first_next, second_next = _legacy_windows(first)[0], _legacy_windows(first)[1], _legacy_windows(second)[1]
    assert shared == _legacy_windows(second)[0] and first_next[:200] == second_next[:200]
    _with_vector(kb_conn, second_next, uri="content://notes/edited", index=1)  # the lower id, read first
    shared_id = _with_vector(kb_conn, shared, uri="content://notes/edited", index=0)
    _with_vector(kb_conn, first_next, uri="content://notes/edited", index=1)
    kb_conn.commit()
    kb_conn.close()

    assert _run(db, "redact", "--rewrite") == 0
    conn = _open(db)
    stored = conn.execute("SELECT content FROM chunks WHERE id = ?", (shared_id,)).fetchone()[0]
    conn.close()
    assert SECRET[:12] not in stored  # read against the second revision alone, its 12 characters are no secret


def test_a_window_shared_by_many_revisions_is_read_one_side_at_a_time(kb_conn, tmp_path, vectors, capsys):
    """#592: past 64 (before, after) pairs each side is read alone, and a fragment at either edge is still found."""
    db = tmp_path / "test.db"
    common = "lorem ipsum " * 300

    def revision(k: int) -> str:  # revisions differ only outside the shared window's reach
        return (
            f"revision {k} ".ljust(700, ".") + common[700:783] + SECRET + common[819:1790] + SECRET + f" tail {k}" * 99
        )

    windows = [_legacy_windows(revision(k)) for k in range(9)]
    assert all(w[1] == windows[0][1] for w in windows)
    for w in windows:
        _with_vector(kb_conn, w[0], uri="content://status", index=0)
        _with_vector(kb_conn, w[2], uri="content://status", index=2)
    shared_id = _with_vector(kb_conn, windows[0][1], uri="content://status", index=1)
    kb_conn.commit()
    kb_conn.close()

    assert _run(db, "redact", "--rewrite") == 0
    conn = _open(db)
    stored = conn.execute("SELECT content FROM chunks WHERE id = ?", (shared_id,)).fetchone()[0]
    conn.close()
    assert SECRET[17:] not in stored and SECRET[:10] not in stored  # 19 and 10 characters: each needs its side


def test_rewrite_pairs_windows_the_markdown_chunker_stored_stripped(kb_conn, tmp_path, vectors, capsys):
    """#592: an oversized markdown section's windows were stored stripped, so they overlap by less than 200."""
    db = tmp_path / "test.db"
    text = ("word " * 200)[:798] + "    " + ("word " * 40)[:186] + SECRET + " " + "dolor sit amet " * 80
    windows = [w.strip() for w in _legacy_windows(text)]
    assert not windows[1].startswith(windows[0][-200:]), "the second window must have lost its leading spaces"
    for i, window in enumerate(windows):
        _with_vector(kb_conn, window, uri="/d/report.pdf", index=i)
    kb_conn.commit()
    kb_conn.close()

    assert _run(db, "redact", "--rewrite") == 0
    assert not _windows(SECRET.encode(), 8) & _windows(_file_bytes(db), 8)
