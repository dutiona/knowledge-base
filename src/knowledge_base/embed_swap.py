"""Embedding space lifecycle: create, backfill, promote, deprecate, cleanup.

Manages multiple embedding spaces (each with its own sqlite-vec table)
for zero-downtime model migration, A/B comparison, and rollback.
"""

from __future__ import annotations

import re
import sqlite3
import time

from .db import (
    _SPACE_NAME_RE,
    _serialize_f32,
    get_active_space,
    space_table_name,
)
from .embeddings import EmbeddingProvider, get_provider, truncate_embedding


def get_embed_config(conn: sqlite3.Connection) -> dict:
    """Get current embedding model configuration from the config table.

    Backward-compatible: reads from config key-value pairs, which are
    kept in sync with the active space by promote_space().
    """
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


# ---------------------------------------------------------------------------
# Space lifecycle
# ---------------------------------------------------------------------------


def _has_chunk_strategy_column(conn: sqlite3.Connection) -> bool:
    """Check if the chunks table has a chunk_strategy column (#100)."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()}
    return "chunk_strategy" in columns


def create_space(
    conn: sqlite3.Connection,
    name: str,
    model: str,
    dim: int,
    provider: str,
    chunk_strategy: str = "mechanical",
    matryoshka_base_dim: int | None = None,
) -> dict:
    """Create a new embedding space in 'populating' status.

    Creates the registry entry and the backing vec0 virtual table.
    """
    if not _SPACE_NAME_RE.match(name):
        raise ValueError(
            f"Space name must be alphanumeric/underscore only, got: {name!r}"
        )
    if chunk_strategy not in ("mechanical", "semantic"):
        raise ValueError(
            f"chunk_strategy must be 'mechanical' or 'semantic', got: {chunk_strategy!r}"
        )
    if matryoshka_base_dim is not None and matryoshka_base_dim <= dim:
        raise ValueError(
            f"matryoshka_base_dim ({matryoshka_base_dim}) must be greater than dim ({dim})"
        )

    existing = conn.execute(
        "SELECT 1 FROM embed_spaces WHERE name = ?", (name,)
    ).fetchone()
    if existing:
        raise ValueError(f"Embedding space {name!r} already exists")

    tbl = space_table_name(name)

    # Count target chunks for progress tracking
    if _has_chunk_strategy_column(conn):
        total = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE chunk_strategy = ?",
            (chunk_strategy,),
        ).fetchone()[0]
    else:
        total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    conn.execute(
        """INSERT INTO embed_spaces
           (name, model, provider, dim, chunk_strategy, status, table_name,
            total_chunks, matryoshka_base_dim)
           VALUES (?, ?, ?, ?, ?, 'populating', ?, ?, ?)""",
        (name, model, provider, dim, chunk_strategy, tbl, total, matryoshka_base_dim),
    )
    conn.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS [{tbl}] USING vec0(
            embedding float[{dim}],
            +chunk_id INTEGER
        )
    """)
    conn.commit()

    return {
        "space": name,
        "table_name": tbl,
        "status": "populating",
        "total_chunks": total,
        "matryoshka_base_dim": matryoshka_base_dim,
    }


def backfill_space(
    conn: sqlite3.Connection,
    space_name: str,
    batch_size: int = 32,
) -> dict:
    """Embed chunks into a populating space. Resumable via ID-cursor."""
    space = conn.execute(
        "SELECT * FROM embed_spaces WHERE name = ?", (space_name,)
    ).fetchone()
    if space is None:
        raise ValueError(f"Embedding space {space_name!r} not found")
    if space["status"] not in ("populating",):
        raise ValueError(
            f"Can only backfill spaces in 'populating' status, got: {space['status']!r}"
        )

    tbl = space["table_name"]
    model = space["model"]
    dim = space["dim"]
    provider_name = space["provider"]
    chunk_strategy = space["chunk_strategy"]
    matryoshka_base_dim = space["matryoshka_base_dim"]
    embed_dim = matryoshka_base_dim or dim

    embed_provider = get_provider(provider_name, allow_env_override=False)

    # Build chunk selection query — strategy-aware
    has_strategy = _has_chunk_strategy_column(conn)
    if has_strategy:
        base_query = (
            "SELECT c.id, c.content FROM chunks c "
            "LEFT JOIN [{tbl}] v ON c.id = v.chunk_id "
            "WHERE c.chunk_strategy = ? AND c.id > ? AND v.chunk_id IS NULL "
            "ORDER BY c.id LIMIT ?"
        ).replace("{tbl}", tbl)
        base_params_prefix = [chunk_strategy]
    else:
        base_query = (
            "SELECT c.id, c.content FROM chunks c "
            "LEFT JOIN [{tbl}] v ON c.id = v.chunk_id "
            "WHERE c.id > ? AND v.chunk_id IS NULL "
            "ORDER BY c.id LIMIT ?"
        ).replace("{tbl}", tbl)
        base_params_prefix = []

    processed = 0
    last_id = 0

    while True:
        params = base_params_prefix + [last_id, batch_size]
        batch = conn.execute(base_query, params).fetchall()
        if not batch:
            break

        texts = [row["content"] for row in batch]
        ids = [row["id"] for row in batch]
        last_id = ids[-1]

        embeddings = embed_provider.embed(texts, model=model, expected_dim=embed_dim)

        if matryoshka_base_dim:
            embeddings = [truncate_embedding(e, dim) for e in embeddings]

        for chunk_id, emb_vec in zip(ids, embeddings):
            conn.execute(
                f"INSERT INTO [{tbl}] (rowid, embedding, chunk_id) VALUES (?, ?, ?)",
                (chunk_id, _serialize_f32(emb_vec), chunk_id),
            )
        processed += len(batch)

        # Update progress counter
        conn.execute(
            "UPDATE embed_spaces SET chunk_count = chunk_count + ? WHERE name = ?",
            (len(batch), space_name),
        )
        conn.commit()

    return {
        "space": space_name,
        "chunks_processed": processed,
        "total_chunks": space["total_chunks"],
    }


def promote_space(conn: sqlite3.Connection, space_name: str) -> dict:
    """Promote a space to active, deprecating the current active space.

    Atomically updates status and syncs config table (embed_model,
    embed_dim, embed_provider, chunk_strategy).
    """
    space = conn.execute(
        "SELECT * FROM embed_spaces WHERE name = ?", (space_name,)
    ).fetchone()
    if space is None:
        raise ValueError(f"Embedding space {space_name!r} not found")
    if space["status"] not in ("populating", "deprecated"):
        raise ValueError(
            f"Can only promote spaces in 'populating' or 'deprecated' status, "
            f"got: {space['status']!r}"
        )
    # Block promotion of incompletely backfilled spaces.
    # Recount live corpus to catch chunks ingested after backfill.
    count = space["chunk_count"] or 0
    if _has_chunk_strategy_column(conn):
        live_total = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE chunk_strategy = ?",
            (space["chunk_strategy"],),
        ).fetchone()[0]
    else:
        live_total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    if live_total > 0 and count < live_total:
        raise ValueError(
            f"Cannot promote space {space_name!r}: only {count} of "
            f"{live_total} chunks backfilled (run backfill_space to sync)"
        )

    # Staleness check: warn if promoting a deprecated space whose chunk_count
    # doesn't match the current corpus (chunks ingested after deprecation).
    stale_warning = None
    if space["status"] == "deprecated":
        has_strategy = _has_chunk_strategy_column(conn)
        if has_strategy:
            current_total = conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE chunk_strategy = ?",
                (space["chunk_strategy"],),
            ).fetchone()[0]
        else:
            current_total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        if current_total != count:
            stale_warning = (
                f"Space {space_name!r} has {count} embeddings but corpus now "
                f"has {current_total} chunks. Run backfill_space() to sync."
            )

    old_active = get_active_space(conn)
    old_name = old_active["name"] if old_active else None

    # Atomic status swap
    if old_name:
        conn.execute(
            "UPDATE embed_spaces SET status = 'deprecated' WHERE name = ?",
            (old_name,),
        )
    conn.execute(
        "UPDATE embed_spaces SET status = 'active' WHERE name = ?",
        (space_name,),
    )

    # Sync config table
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('embed_model', ?)",
        (space["model"],),
    )
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('embed_dim', ?)",
        (str(space["dim"]),),
    )
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('embed_provider', ?)",
        (space["provider"],),
    )
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('chunk_strategy', ?)",
        (space["chunk_strategy"],),
    )
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('embed_matryoshka_base_dim', ?)",
        (str(space["matryoshka_base_dim"] or ""),),
    )
    # Invalidate similarity relationships (embedding space changed)
    conn.execute("DELETE FROM relationships WHERE relation_type = 'similar'")
    conn.commit()

    # Always rebuild folder summaries on promotion (model/dim may differ)
    embed_provider = get_provider(space["provider"], allow_env_override=False)
    _re_embed_folder_summaries(
        conn,
        embed_provider,
        space["model"],
        space["dim"],
        matryoshka_base_dim=space["matryoshka_base_dim"],
    )

    result: dict = {"promoted": space_name, "deprecated": old_name}
    if stale_warning:
        result["warning"] = stale_warning
    return result


def deprecate_space(conn: sqlite3.Connection, space_name: str) -> dict:
    """Mark a space as deprecated. Cannot deprecate the active space."""
    space = conn.execute(
        "SELECT status FROM embed_spaces WHERE name = ?", (space_name,)
    ).fetchone()
    if space is None:
        raise ValueError(f"Embedding space {space_name!r} not found")
    if space["status"] == "active":
        raise ValueError("Cannot deprecate the active embedding space")

    conn.execute(
        "UPDATE embed_spaces SET status = 'deprecated' WHERE name = ?",
        (space_name,),
    )
    conn.commit()
    return {"deprecated": space_name}


def cleanup_space(conn: sqlite3.Connection, space_name: str) -> dict:
    """Drop a deprecated space's vec table and registry entry."""
    space = conn.execute(
        "SELECT status, table_name FROM embed_spaces WHERE name = ?",
        (space_name,),
    ).fetchone()
    if space is None:
        raise ValueError(f"Embedding space {space_name!r} not found")
    if space["status"] != "deprecated":
        raise ValueError(
            f"Can only clean up deprecated spaces, got: {space['status']!r}"
        )

    tbl = space["table_name"]
    conn.execute(f"DROP TABLE IF EXISTS [{tbl}]")
    conn.execute("DELETE FROM embed_spaces WHERE name = ?", (space_name,))
    conn.commit()
    return {"cleaned": space_name}


def list_spaces(conn: sqlite3.Connection) -> list[dict]:
    """Return all embedding spaces as dicts."""
    rows = conn.execute("SELECT * FROM embed_spaces ORDER BY created_at").fetchall()
    return [dict(row) for row in rows]


def get_space(conn: sqlite3.Connection, name: str) -> dict:
    """Look up an embedding space by name. Raises ValueError if not found."""
    row = conn.execute("SELECT * FROM embed_spaces WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise ValueError(f"Embedding space {name!r} not found")
    return dict(row)


# ---------------------------------------------------------------------------
# Convenience wrapper (backward compat)
# ---------------------------------------------------------------------------


def re_embed(
    conn: sqlite3.Connection,
    new_model: str,
    new_dim: int,
    batch_size: int = 32,
    provider: str | None = None,
    matryoshka_base_dim: int | None = None,
) -> dict:
    """Re-embed all chunks with a new model via the space lifecycle.

    Creates a new space, backfills it, promotes it, and re-embeds folder
    summaries. The old space is left as 'deprecated' (not auto-cleaned).
    """
    if matryoshka_base_dim is not None and matryoshka_base_dim <= new_dim:
        raise ValueError(
            f"matryoshka_base_dim ({matryoshka_base_dim}) must be greater than "
            f"new_dim ({new_dim})"
        )

    # Resolve provider
    cfg = get_embed_config(conn)
    resolved_provider = provider or cfg["provider"]

    # Inherit chunk_strategy from current active space
    active = get_active_space(conn)
    strategy = active["chunk_strategy"] if active else "mechanical"

    # Generate unique space name
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", new_model)
    space_name = f"{sanitized}_{new_dim}"

    # Avoid collisions
    existing = conn.execute(
        "SELECT 1 FROM embed_spaces WHERE name = ?", (space_name,)
    ).fetchone()
    if existing:
        space_name = f"{space_name}_{int(time.time())}"

    create_space(
        conn,
        space_name,
        new_model,
        new_dim,
        resolved_provider,
        strategy,
        matryoshka_base_dim=matryoshka_base_dim,
    )
    result = backfill_space(conn, space_name, batch_size)
    promote_space(conn, space_name)

    # Count folder summaries re-embedded (done inside promote_space)
    folders_count = conn.execute(
        "SELECT COUNT(*) FROM folder_summaries_vec"
    ).fetchone()[0]

    return {
        "chunks_processed": result["chunks_processed"],
        "folders_processed": folders_count,
        "model": new_model,
        "dim": new_dim,
        "space": space_name,
    }


def _re_embed_folder_summaries(
    conn: sqlite3.Connection,
    embed_provider: EmbeddingProvider,
    model: str,
    dim: int,
    matryoshka_base_dim: int | None = None,
) -> int:
    """Recreate folder_summaries_vec with new dimensions and re-embed."""
    conn.execute("DROP TABLE IF EXISTS folder_summaries_vec")
    conn.execute(f"""
        CREATE VIRTUAL TABLE folder_summaries_vec USING vec0(
            embedding float[{dim}],
            +folder_path TEXT
        )
    """)
    folder_rows = conn.execute(
        "SELECT folder_path, summary FROM folder_summaries"
    ).fetchall()
    if not folder_rows:
        conn.commit()
        return 0

    embed_dim = matryoshka_base_dim or dim
    texts = [row["summary"] for row in folder_rows]
    embeddings = embed_provider.embed(texts, model=model, expected_dim=embed_dim)  # type: ignore[union-attr]

    if matryoshka_base_dim:
        from .embeddings import truncate_embedding

        embeddings = [truncate_embedding(e, dim) for e in embeddings]

    for row, emb in zip(folder_rows, embeddings):
        conn.execute(
            "INSERT INTO folder_summaries_vec (embedding, folder_path) VALUES (?, ?)",
            (_serialize_f32(emb), row["folder_path"]),
        )
    conn.commit()
    return len(folder_rows)
