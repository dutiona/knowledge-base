"""Embedding space lifecycle: create, backfill, promote, deprecate, cleanup.

Manages multiple embedding spaces (each with its own sqlite-vec table)
for zero-downtime model migration, A/B comparison, and rollback.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time


from .db import (
    SPACE_NAME_RE,
    _serialize_f32,
    get_active_space,
    space_table_name,
)
from .embeddings import EmbeddingProvider, ProviderConfig, get_provider, truncate_embedding
from .exceptions import KnowledgeBaseError, ValidationError
from .settings import load_embed_settings
from .utils import ELEMENT_INSERT_EXPR, VALID_ELEMENT_TYPES

logger = logging.getLogger(__name__)


def get_embed_config(conn: sqlite3.Connection) -> dict:
    """Get current embedding provider configuration from the config table.

    Reads the full ``(provider, model, dim, base_url, api_key, allow_loopback)`` tuple.
    ``api_key`` is the **raw** spec (inline or ``env:VARNAME``) — resolved only at call
    time, never returned by tools. The rows record the ``KNOWLEDGE_BASE_EMBED_*``
    declaration: seeded on a blank database (``db._seed_default_config``) and bound to it at
    every start (#569); the key spec is only ever the declared ``env:`` source.
    """
    model = conn.execute("SELECT value FROM config WHERE key = 'embed_model'").fetchone()
    dim = conn.execute("SELECT value FROM config WHERE key = 'embed_dim'").fetchone()
    provider_row = conn.execute("SELECT value FROM config WHERE key = 'embed_provider'").fetchone()
    base_url_row = conn.execute("SELECT value FROM config WHERE key = 'embed_base_url'").fetchone()
    api_key_row = conn.execute("SELECT value FROM config WHERE key = 'embed_api_key'").fetchone()
    loopback_row = conn.execute("SELECT value FROM config WHERE key = 'allow_loopback_base_url'").fetchone()

    base_url = base_url_row["value"] if base_url_row else None
    api_key = api_key_row["value"] if api_key_row else None
    allow_loopback = bool(loopback_row) and loopback_row["value"].strip().lower() == "true"

    provider = provider_row["value"]

    return {
        "model": model["value"],
        "dim": int(dim["value"]),
        "provider": provider,
        "base_url": base_url,
        "api_key": api_key,
        "allow_loopback": allow_loopback,
    }


def declared_provider_config(conn: sqlite3.Connection, family: str) -> ProviderConfig:
    """The provider for embedding with a space of *family*: always the declared endpoint (#569).

    The URL and key source come from this process's own declaration, not from the
    ``config`` rows, so another process rewriting those rows (a ``rebind`` run while the
    server is up) cannot redirect it. A space of another family than the declared provider
    is refused, so no tool-created or deprecated space is embedded with the wrong wire
    format, or against an auto-detected Ollama.
    """
    declared = load_embed_settings()
    if family != declared.provider:
        raise ValidationError(
            f"the space's provider {family!r} is not the declared provider {declared.provider!r}; "
            "re-embed under the declaration"
        )
    return ProviderConfig(
        family=family,
        base_url=declared.base_url,
        api_key=declared.api_key_source,
        allow_loopback=get_embed_config(conn)["allow_loopback"],
    )


def assert_embed_identity_match(conn: sqlite3.Connection) -> None:
    """Reject a configured embed identity that differs from the active space's (AC6).

    The active ``embed_spaces`` row's ``(provider, model)`` is the authoritative
    identity (ADR-0015 / ADR-0018 §5). The ``provider`` field is the API *family*
    (``openai_compat``/``ollama``/``onnx``), not the backend host — so a ``base_url``
    swap within the same family (e.g. ``tei`` → ``vllm``, both ``openai_compat``, same
    model) keeps the identity and is allowed. Changing the family or the model without
    creating a new space (``re_embed``) would silently write mismatched vectors into the
    active space — hard-rejected here, at the producer seam.

    This is the *steady-state ingest* guard only; ``backfill_space`` is deliberately NOT
    guarded — during ``re_embed`` (create → backfill → promote) the config still holds the
    old identity while a new-identity space is backfilled, so a config-vs-space check
    there would break the swap. Provider-only swap does NOT invalidate any metric:
    whitening is gated on *model* change (ADR-0017), and KB ships no whitening, so this is
    satisfied by inaction.
    """
    active = get_active_space(conn)
    if active is None:
        return  # fresh DB / bootstrap — no recorded identity to mismatch
    cfg = get_embed_config(conn)
    if cfg["provider"] != active["provider"] or cfg["model"] != active["model"]:
        raise ValidationError(
            f"Configured embedding identity ({cfg['provider']}, {cfg['model']}) does not match the active "
            f"space's recorded identity ({active['provider']}, {active['model']}). A provider-family or model "
            "change requires a new space — run re_embed() rather than corrupting the active space "
            "(ADR-0015 mismatch rule). A base_url change within the same family is allowed."
        )


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
    element_type: str = "float32",
) -> dict:
    """Create a new embedding space in 'populating' status.

    Creates the registry entry and the backing vec0 virtual table.
    """
    if not SPACE_NAME_RE.match(name):
        raise ValueError(f"Space name must be alphanumeric/underscore only, got: {name!r}")
    if chunk_strategy not in ("mechanical", "semantic"):
        raise ValueError(f"chunk_strategy must be 'mechanical' or 'semantic', got: {chunk_strategy!r}")
    if matryoshka_base_dim is not None and matryoshka_base_dim <= dim:
        raise ValueError(f"matryoshka_base_dim ({matryoshka_base_dim}) must be greater than dim ({dim})")
    if element_type not in VALID_ELEMENT_TYPES:
        raise ValueError(f"element_type must be one of {sorted(VALID_ELEMENT_TYPES)}, got: {element_type!r}")

    existing = conn.execute("SELECT 1 FROM embed_spaces WHERE name = ?", (name,)).fetchone()
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
        "INSERT INTO embed_spaces"
        " (name, model, provider, dim, chunk_strategy, status, table_name,"
        " total_chunks, matryoshka_base_dim, element_type)"
        " VALUES (?, ?, ?, ?, ?, 'populating', ?, ?, ?, ?)",
        (
            name,
            model,
            provider,
            dim,
            chunk_strategy,
            tbl,
            total,
            matryoshka_base_dim,
            element_type,
        ),
    )
    conn.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS [{tbl}] USING vec0(
            embedding {element_type}[{dim}],
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
        "element_type": element_type,
    }


def backfill_space(
    conn: sqlite3.Connection,
    space_name: str,
    batch_size: int = 32,
) -> dict:
    """Embed chunks into a populating space. Resumable via ID-cursor."""
    space = conn.execute("SELECT * FROM embed_spaces WHERE name = ?", (space_name,)).fetchone()
    if space is None:
        raise ValueError(f"Embedding space {space_name!r} not found")
    if space["status"] not in ("populating",):
        raise ValueError(f"Can only backfill spaces in 'populating' status, got: {space['status']!r}")

    tbl = space["table_name"]
    model = space["model"]
    dim = space["dim"]
    provider_name = space["provider"]
    chunk_strategy = space["chunk_strategy"]
    matryoshka_base_dim = space["matryoshka_base_dim"]
    element_type = space["element_type"] or "float32"
    embed_dim = matryoshka_base_dim or dim

    embed_provider = get_provider(cfg=declared_provider_config(conn, provider_name))

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
        params = [*base_params_prefix, last_id, batch_size]
        batch = conn.execute(base_query, params).fetchall()
        if not batch:
            break

        texts = [row["content"] for row in batch]
        ids = [row["id"] for row in batch]
        last_id = ids[-1]

        embeddings = embed_provider.embed(texts, model=model, expected_dim=embed_dim)

        if matryoshka_base_dim:
            embeddings = [truncate_embedding(e, dim) if e is not None else None for e in embeddings]

        insert_expr = ELEMENT_INSERT_EXPR[element_type]
        for chunk_id, emb_vec in zip(ids, embeddings, strict=True):
            if emb_vec is None:
                continue
            conn.execute(
                f"INSERT INTO [{tbl}] (rowid, embedding, chunk_id) VALUES (?, {insert_expr}, ?)",  # noqa: S608  # trusted internal identifier, not user input
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


def _assert_declared_identity(provider: str, model: str, dim: int) -> None:
    """Refuse an active identity other than the declaration (#569): only re-embed moves it."""
    declared = load_embed_settings()
    for key, value, wanted in (
        ("embed_provider", provider, declared.provider),
        ("embed_model", model, declared.model),
        ("embed_dim", dim, declared.dim),
    ):
        if value != wanted:
            raise ValidationError(
                f"{key} {value!r} is not the declared {wanted!r}; only the declared identity can be "
                "active (change the declaration, then run `knowledge-base-ingest re-embed`)"
            )


_FolderVectors = dict[tuple[str, str], "list[float] | None"]


def _folder_rows(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    return [(r["folder_path"], r["summary"]) for r in conn.execute("SELECT folder_path, summary FROM folder_summaries")]


def _embed_folder_summaries(
    provider: EmbeddingProvider,
    rows: list[tuple[str, str]],
    model: str,
    dim: int,
    matryoshka_base_dim: int | None,
) -> _FolderVectors:
    if not rows:
        return {}
    vectors = provider.embed([text for _path, text in rows], model=model, expected_dim=matryoshka_base_dim or dim)
    if matryoshka_base_dim:
        vectors = [truncate_embedding(v, dim) if v is not None else None for v in vectors]
    return dict(zip(rows, vectors, strict=True))


def _write_folder_summaries_vec(conn: sqlite3.Connection, dim: int, vectors: _FolderVectors) -> None:
    """Recreate folder_summaries_vec at *dim* and fill it. Commits nothing: the caller owns the transaction."""
    conn.execute("DROP TABLE IF EXISTS folder_summaries_vec")
    conn.execute(f"""
        CREATE VIRTUAL TABLE folder_summaries_vec USING vec0(
            embedding float[{dim}],
            +folder_path TEXT
        )
    """)
    for (path, _text), vec in vectors.items():
        if vec is not None:
            conn.execute(
                "INSERT INTO folder_summaries_vec (embedding, folder_path) VALUES (?, ?)",
                (_serialize_f32(vec), path),
            )


def promote_space(conn: sqlite3.Connection, space_name: str) -> dict:
    """Promote a space to active, deprecating the current active space.

    All or nothing (#569): the folder summaries are embedded first, outside the write lock,
    then one ``BEGIN IMMEDIATE`` transaction rechecks completeness, re-embeds only the
    summaries that changed meanwhile, swaps the statuses, syncs the config identity rows,
    drops the ``similar`` relationships and rebuilds ``folder_summaries_vec``. A failure,
    Ctrl-C included, changes nothing. Only a space of the declared identity can be promoted.
    """
    space = conn.execute("SELECT * FROM embed_spaces WHERE name = ?", (space_name,)).fetchone()
    if space is None:
        raise ValueError(f"Embedding space {space_name!r} not found")
    if space["status"] not in ("populating", "deprecated"):
        raise ValueError(f"Can only promote spaces in 'populating' or 'deprecated' status, got: {space['status']!r}")
    _assert_declared_identity(space["provider"], space["model"], space["dim"])
    if conn.in_transaction:
        raise KnowledgeBaseError("promote_space needs a connection with no open transaction; commit or roll back first")
    _check_promotable(conn, space)  # fail fast; rechecked under the lock
    provider = get_provider(cfg=declared_provider_config(conn, space["provider"]))
    shape = (space["model"], space["dim"], space["matryoshka_base_dim"])
    snapshot = _embed_folder_summaries(provider, _folder_rows(conn), *shape)

    level = conn.isolation_level
    conn.isolation_level = None  # explicit BEGIN: Python's sqlite3 opens no transaction before DDL
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # The target may have changed or gone while its summaries embedded unlocked.
            current_space = conn.execute("SELECT * FROM embed_spaces WHERE name = ?", (space_name,)).fetchone()
            if current_space is None or dict(current_space) != dict(space):
                raise ValueError(f"Embedding space {space_name!r} changed during promotion; rerun it")
            stale_warning = _check_promotable(conn, space)
            current = _folder_rows(conn)
            changed = [row for row in current if row not in snapshot]
            vectors = {row: snapshot[row] for row in current if row in snapshot}
            vectors.update(_embed_folder_summaries(provider, changed, *shape))
            old_name = _swap_active(conn, space)
            _write_folder_summaries_vec(conn, space["dim"], vectors)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.isolation_level = level

    result: dict = {"promoted": space_name, "deprecated": old_name}
    if stale_warning:
        result["warning"] = stale_warning
    return result


def _check_promotable(conn: sqlite3.Connection, space: sqlite3.Row) -> str | None:
    """Refuse an incompletely backfilled space; return a staleness warning for a deprecated one."""
    space_name = space["name"]
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
    return stale_warning


def _swap_active(conn: sqlite3.Connection, space: sqlite3.Row) -> str | None:
    """Make *space* active and sync the config identity rows; commits nothing. Returns the old active name."""
    space_name = space["name"]
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
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('embed_element_type', ?)",
        (space["element_type"] or "float32",),
    )
    # Invalidate similarity relationships (embedding space changed)
    conn.execute("DELETE FROM relationships WHERE relation_type = 'similar'")
    return old_name


def deprecate_space(conn: sqlite3.Connection, space_name: str) -> dict:
    """Mark a space as deprecated. Cannot deprecate the active space."""
    space = conn.execute("SELECT status FROM embed_spaces WHERE name = ?", (space_name,)).fetchone()
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
        raise ValueError(f"Can only clean up deprecated spaces, got: {space['status']!r}")

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
    summaries. The old space is left as 'deprecated' (not auto-cleaned). The target must be
    the declared identity (#569), checked before anything is created; if the backfill or
    the promotion fails, the new space is removed again and the error re-raised.
    """
    if matryoshka_base_dim is not None and matryoshka_base_dim <= new_dim:
        raise ValueError(f"matryoshka_base_dim ({matryoshka_base_dim}) must be greater than new_dim ({new_dim})")

    resolved_provider = provider or load_embed_settings().provider
    _assert_declared_identity(resolved_provider, new_model, new_dim)

    # Inherit chunk_strategy from current active space
    active = get_active_space(conn)
    strategy = active["chunk_strategy"] if active else "mechanical"

    # Generate unique space name
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", new_model)
    space_name = f"{sanitized}_{new_dim}"

    # Avoid collisions
    existing = conn.execute("SELECT 1 FROM embed_spaces WHERE name = ?", (space_name,)).fetchone()
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
    try:
        result = backfill_space(conn, space_name, batch_size)
        promote_space(conn, space_name)
    except BaseException:
        try:
            deprecate_space(conn, space_name)
            cleanup_space(conn, space_name)
        except Exception:
            logger.exception("could not remove the failed re-embed's space %r", space_name)
        raise

    # Count folder summaries re-embedded (done inside promote_space)
    folders_count = conn.execute("SELECT COUNT(*) FROM folder_summaries_vec").fetchone()[0]

    return {
        "chunks_processed": result["chunks_processed"],
        "folders_processed": folders_count,
        "model": new_model,
        "dim": new_dim,
        "space": space_name,
    }
