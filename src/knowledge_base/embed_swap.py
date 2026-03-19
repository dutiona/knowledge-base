"""Embedding model swap: re-embed all chunks with a new model."""

from __future__ import annotations

import sqlite3
import struct

from .embeddings import get_provider


def _serialize_f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def get_embed_config(conn: sqlite3.Connection) -> dict:
    """Get current embedding model configuration."""
    from .db import DEFAULT_EMBED_DIM, DEFAULT_EMBED_MODEL, DEFAULT_EMBED_PROVIDER

    model = conn.execute(
        "SELECT value FROM config WHERE key = 'embed_model'"
    ).fetchone()
    dim = conn.execute("SELECT value FROM config WHERE key = 'embed_dim'").fetchone()
    provider = conn.execute(
        "SELECT value FROM config WHERE key = 'embed_provider'"
    ).fetchone()
    return {
        "model": model["value"] if model else DEFAULT_EMBED_MODEL,
        "dim": int(dim["value"]) if dim else DEFAULT_EMBED_DIM,
        "provider": provider["value"] if provider else DEFAULT_EMBED_PROVIDER,
    }


def re_embed(
    conn: sqlite3.Connection,
    new_model: str,
    new_dim: int,
    batch_size: int = 32,
    provider: str | None = None,
) -> dict:
    """Re-embed all chunks with a new model.

    Embeds into a staging table first. Only drops/recreates the real vec table
    after all embeddings succeed, preventing data loss on failure.
    """
    # Resolve provider: explicit override > current config > default
    cfg = get_embed_config(conn)
    embed_provider = get_provider(provider or cfg["provider"])

    # Stage embeddings in a regular table (vec0 tables can't be renamed)
    conn.execute("DROP TABLE IF EXISTS _embed_staging")
    conn.execute("""
        CREATE TABLE _embed_staging (
            chunk_id INTEGER PRIMARY KEY,
            embedding BLOB NOT NULL
        )
    """)

    # Process in batches using ID-based cursor to avoid OOM
    processed = 0
    last_id = 0
    while True:
        batch = conn.execute(
            "SELECT id, content FROM chunks WHERE id > ? ORDER BY id LIMIT ?",
            (last_id, batch_size),
        ).fetchall()
        if not batch:
            break

        texts = [row["content"] for row in batch]
        ids = [row["id"] for row in batch]
        last_id = ids[-1]

        embeddings = embed_provider.embed(texts, model=new_model, expected_dim=new_dim)

        for chunk_id, emb_vec in zip(ids, embeddings):
            conn.execute(
                "INSERT INTO _embed_staging (chunk_id, embedding) VALUES (?, ?)",
                (chunk_id, _serialize_f32(emb_vec)),
            )
        processed += len(batch)

    # All embeddings succeeded — now swap atomically
    conn.execute("DROP TABLE IF EXISTS chunks_vec")
    conn.execute(f"""
        CREATE VIRTUAL TABLE chunks_vec USING vec0(
            embedding float[{new_dim}],
            +chunk_id INTEGER
        )
    """)
    conn.execute("""
        INSERT INTO chunks_vec (rowid, embedding, chunk_id)
        SELECT chunk_id, embedding, chunk_id FROM _embed_staging
    """)
    conn.execute("DROP TABLE _embed_staging")

    # Update config
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('embed_model', ?)",
        (new_model,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('embed_dim', ?)",
        (str(new_dim),),
    )
    if provider:
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES ('embed_provider', ?)",
            (provider,),
        )
    conn.commit()

    return {
        "chunks_processed": processed,
        "model": new_model,
        "dim": new_dim,
    }
