"""Embedding model swap: re-embed all chunks with a new model."""

from __future__ import annotations

import sqlite3
import struct

from .embeddings import embed


def _serialize_f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def get_embed_config(conn: sqlite3.Connection) -> dict:
    """Get current embedding model configuration."""
    model = conn.execute("SELECT value FROM config WHERE key = 'embed_model'").fetchone()
    dim = conn.execute("SELECT value FROM config WHERE key = 'embed_dim'").fetchone()
    return {
        "model": model["value"] if model else "nomic-embed-text",
        "dim": int(dim["value"]) if dim else 768,
    }


def re_embed(
    conn: sqlite3.Connection,
    new_model: str,
    new_dim: int,
    batch_size: int = 32,
) -> dict:
    """Re-embed all chunks with a new model.

    Drops and recreates the chunks_vec table with the new dimension,
    then re-embeds all chunks in batches.
    """
    # Get all chunks
    chunks = conn.execute("SELECT id, content FROM chunks ORDER BY id").fetchall()

    # Drop and recreate vec table with new dimension
    conn.execute("DROP TABLE IF EXISTS chunks_vec")
    conn.execute(f"""
        CREATE VIRTUAL TABLE chunks_vec USING vec0(
            embedding float[{new_dim}],
            +chunk_id INTEGER
        )
    """)

    processed = 0
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        texts = [row["content"] for row in batch]
        ids = [row["id"] for row in batch]

        embeddings = embed(texts, model=new_model)

        for chunk_id, emb_vec in zip(ids, embeddings):
            conn.execute(
                "INSERT INTO chunks_vec (rowid, embedding, chunk_id) VALUES (?, ?, ?)",
                (chunk_id, _serialize_f32(emb_vec), chunk_id),
            )
        processed += len(batch)

    # Update config
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('embed_model', ?)",
        (new_model,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('embed_dim', ?)",
        (str(new_dim),),
    )
    conn.commit()

    return {
        "chunks_processed": processed,
        "model": new_model,
        "dim": new_dim,
    }
