"""Purging deleted text from the database file, and the ``redact`` maintenance command (#592)."""

from __future__ import annotations

import json
import random
import sqlite3
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
