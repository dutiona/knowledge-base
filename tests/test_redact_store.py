"""Purging deleted text from the database file, and the ``redact`` maintenance command (#592)."""

from __future__ import annotations

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
