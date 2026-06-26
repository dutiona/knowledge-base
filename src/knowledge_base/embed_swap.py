"""Embedding space lifecycle: create, backfill, promote, deprecate, cleanup.

Manages multiple embedding spaces (each with its own sqlite-vec table)
for zero-downtime model migration, A/B comparison, and rollback.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time

import httpx

from .db import (
    SPACE_NAME_RE,
    _serialize_f32,
    get_active_space,
    space_table_name,
)
from .embeddings import EmbeddingProvider, ProviderConfig, _get_ollama_url, get_provider, truncate_embedding
from .exceptions import ValidationError
from .utils import ELEMENT_INSERT_EXPR, VALID_ELEMENT_TYPES, _resolve_api_key, _sanitize_url, validate_base_url

logger = logging.getLogger(__name__)

# Provider families valid for the embedding capability (anthropic_compat is chat-only).
_EMBED_FAMILIES = ("ollama", "openai_compat", "onnx")
_LOCAL_EMBED_FAMILIES = ("ollama", "onnx")  # no base_url/api_key — stale keys are cleared

_embed_env_deprecation_warned = False


def _warn_embed_env_deprecated() -> None:
    """One-time deprecation notice for the legacy EMBED_PROVIDER/OPENAI_API_KEY env vars."""
    global _embed_env_deprecation_warned
    if not _embed_env_deprecation_warned:
        logger.warning(
            "EMBED_PROVIDER/OPENAI_API_KEY env-var selection is deprecated; configure embeddings "
            "via configure_embeddings() (the config table). Env back-compat will be removed in a "
            "future release."
        )
        _embed_env_deprecation_warned = True


def get_embed_config(conn: sqlite3.Connection) -> dict:
    """Get current embedding provider configuration from the config table.

    Reads the full ``(provider, model, dim, base_url, api_key, allow_loopback)`` tuple.
    ``api_key`` is the **raw** spec (inline or ``env:VARNAME``) — resolved only at call
    time, never returned by tools. During the deprecation window the legacy
    ``EMBED_PROVIDER``/``OPENAI_API_KEY`` env vars still apply *while config is at its
    seeded default*, so an explicit ``configure_embeddings()`` choice wins over env
    (the no-drift guarantee for existing local installs).
    """
    from .db import DEFAULT_EMBED_DIM, DEFAULT_EMBED_MODEL, DEFAULT_EMBED_PROVIDER

    model = conn.execute("SELECT value FROM config WHERE key = 'embed_model'").fetchone()
    dim = conn.execute("SELECT value FROM config WHERE key = 'embed_dim'").fetchone()
    provider_row = conn.execute("SELECT value FROM config WHERE key = 'embed_provider'").fetchone()
    base_url_row = conn.execute("SELECT value FROM config WHERE key = 'embed_base_url'").fetchone()
    api_key_row = conn.execute("SELECT value FROM config WHERE key = 'embed_api_key'").fetchone()
    loopback_row = conn.execute("SELECT value FROM config WHERE key = 'allow_loopback_base_url'").fetchone()

    provider = provider_row["value"] if provider_row else DEFAULT_EMBED_PROVIDER
    base_url = base_url_row["value"] if base_url_row else None
    api_key = api_key_row["value"] if api_key_row else None
    allow_loopback = bool(loopback_row) and loopback_row["value"].strip().lower() == "true"

    # Deprecation back-compat: EMBED_PROVIDER overrides only while embed_provider is at
    # its seeded default — an explicit configure_embeddings() choice takes precedence.
    env_provider = os.environ.get("EMBED_PROVIDER")
    if env_provider and provider == DEFAULT_EMBED_PROVIDER:
        provider = env_provider
        _warn_embed_env_deprecated()
    if api_key is None and provider in ("openai", "openai_compat"):
        env_key = os.environ.get("OPENAI_API_KEY")
        if env_key:
            api_key = env_key
            _warn_embed_env_deprecated()

    return {
        "model": model["value"] if model else DEFAULT_EMBED_MODEL,
        "dim": int(dim["value"]) if dim else DEFAULT_EMBED_DIM,
        "provider": provider,
        "base_url": base_url,
        "api_key": api_key,
        "allow_loopback": allow_loopback,
    }


def embed_provider_config(conn: sqlite3.Connection) -> ProviderConfig:
    """Build the resolved :class:`ProviderConfig` cache key from the embed config."""
    cfg = get_embed_config(conn)
    return ProviderConfig(
        family=cfg["provider"],
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        allow_loopback=cfg["allow_loopback"],
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


_EMBED_CONNECTIVITY_TIMEOUT = 3


def _test_embedding_connectivity(
    provider: str, base_url: str | None, api_key: str | None = None, *, allow_loopback: bool = False
) -> dict:
    """Probe embedding endpoint reachability. Advisory — never raises (mirrors LLM probe)."""
    if provider == "onnx":
        return {"reachable": True}  # local inference, no network endpoint
    target = base_url or _get_ollama_url()
    safe_url = _sanitize_url(target)
    try:
        if provider == "ollama":
            resp = httpx.get(f"{target}/api/tags", timeout=_EMBED_CONNECTIVITY_TIMEOUT, follow_redirects=False)
            resp.raise_for_status()
        else:  # openai_compat
            validate_base_url(target, allow_loopback=allow_loopback)
            headers: dict[str, str] = {}
            resolved = _resolve_api_key(api_key)
            if resolved:
                headers["Authorization"] = f"Bearer {resolved}"
            resp = httpx.get(
                f"{target}/v1/models", headers=headers, timeout=_EMBED_CONNECTIVITY_TIMEOUT, follow_redirects=False
            )
            resp.raise_for_status()
        return {"reachable": True}
    except httpx.ConnectError:
        warning = f"Cannot connect to {safe_url}"
    except httpx.TimeoutException:
        warning = f"Connection timed out to {safe_url} ({_EMBED_CONNECTIVITY_TIMEOUT}s)"
    except httpx.HTTPStatusError as exc:
        warning = (
            "Authentication failed — check api_key"
            if exc.response.status_code in (401, 403)
            else (f"Server returned HTTP {exc.response.status_code}")
        )
    except Exception as exc:
        warning = f"Connectivity test failed: {type(exc).__name__}"
    logger.warning("Embedding connectivity test failed for %s at %s: %s", provider, safe_url, warning)
    return {"reachable": False, "warning": warning}


def configure_embeddings(
    conn: sqlite3.Connection,
    provider: str = "ollama",
    base_url: str | None = None,
    model: str = "bge-m3",
    api_key: str | None = None,
    allow_loopback_base_url: bool | None = None,
) -> dict:
    """Configure the embedding provider (mirrors :func:`llm.configure_llm`).

    Moves embedding-provider selection out of the ``EMBED_PROVIDER``/``OPENAI_API_KEY``
    env vars into the ``config`` table (ADR-0018 §3). ``base_url`` is normalized then
    SSRF-validated **before** it is persisted (the primary gate, ADR-0018 §4); a private
    or loopback host without ``allow_loopback_base_url`` is hard-rejected, never stored.

    Note: ``api_key`` is stored as plain text in the SQLite config table (or as an
    ``env:VARNAME`` indirection, which is preferred and never resolves to the secret on
    disk). Acceptable for local-only use; keyring hardening is deferred.
    """
    if provider not in _EMBED_FAMILIES:
        if provider == "anthropic_compat":
            raise ValidationError("anthropic_compat has no embeddings endpoint; it is a chat-only family.")
        raise ValidationError(f"Unknown embedding provider: {provider!r}. Use one of {_EMBED_FAMILIES}.")
    if provider == "openai_compat" and not base_url:
        raise ValidationError("base_url is required for openai_compat provider")
    # Preserve the shared loopback flag unless the caller set it explicitly (avoids the
    # LLM/embed configure_* calls clobbering each other's opt-in).
    if allow_loopback_base_url is None:
        row = conn.execute("SELECT value FROM config WHERE key = 'allow_loopback_base_url'").fetchone()
        loopback = bool(row) and row["value"].strip().lower() == "true"
    else:
        loopback = allow_loopback_base_url
    if base_url:
        # Normalize FIRST, then validate the normalized value (close the suffix-strip gap).
        base_url = base_url.rstrip("/").removesuffix("/v1")
        validate_base_url(base_url, allow_loopback=loopback)

    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('embed_provider', ?)", (provider,))
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('embed_model', ?)", (model,))
    if base_url:
        conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('embed_base_url', ?)", (base_url,))
    elif provider in _LOCAL_EMBED_FAMILIES:
        conn.execute("DELETE FROM config WHERE key = 'embed_base_url'")
    if api_key:
        # Stored verbatim — for env:VARNAME this is the indirection spec, never the secret.
        conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('embed_api_key', ?)", (api_key,))
    elif provider in _LOCAL_EMBED_FAMILIES:
        conn.execute("DELETE FROM config WHERE key = 'embed_api_key'")
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('allow_loopback_base_url', ?)",
        ("true" if loopback else "false",),
    )
    conn.commit()

    cfg = get_embed_config(conn)
    connectivity = _test_embedding_connectivity(
        cfg["provider"], cfg["base_url"], cfg["api_key"], allow_loopback=cfg["allow_loopback"]
    )
    cfg.pop("api_key", None)  # redact from the returned dict
    cfg.update(connectivity)
    return cfg


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

    # The space row owns (provider, model); the config owns the connection details
    # (base_url/api_key/allow_loopback). Build the provider from both — the frozen
    # ProviderConfig keys the cache so two openai_compat base_urls never collide.
    embed_cfg = get_embed_config(conn)
    embed_provider = get_provider(
        cfg=ProviderConfig(
            family=provider_name,
            base_url=embed_cfg["base_url"],
            api_key=embed_cfg["api_key"],
            allow_loopback=embed_cfg["allow_loopback"],
        )
    )

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


def promote_space(conn: sqlite3.Connection, space_name: str) -> dict:
    """Promote a space to active, deprecating the current active space.

    Atomically updates status and syncs config table (embed_model,
    embed_dim, embed_provider, chunk_strategy).
    """
    space = conn.execute("SELECT * FROM embed_spaces WHERE name = ?", (space_name,)).fetchone()
    if space is None:
        raise ValueError(f"Embedding space {space_name!r} not found")
    if space["status"] not in ("populating", "deprecated"):
        raise ValueError(f"Can only promote spaces in 'populating' or 'deprecated' status, got: {space['status']!r}")
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
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('embed_element_type', ?)",
        (space["element_type"] or "float32",),
    )
    # Invalidate similarity relationships (embedding space changed)
    conn.execute("DELETE FROM relationships WHERE relation_type = 'similar'")
    conn.commit()

    # Always rebuild folder summaries on promotion (model/dim may differ)
    embed_cfg = get_embed_config(conn)
    embed_provider = get_provider(
        cfg=ProviderConfig(
            family=space["provider"],
            base_url=embed_cfg["base_url"],
            api_key=embed_cfg["api_key"],
            allow_loopback=embed_cfg["allow_loopback"],
        )
    )
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
    summaries. The old space is left as 'deprecated' (not auto-cleaned).
    """
    if matryoshka_base_dim is not None and matryoshka_base_dim <= new_dim:
        raise ValueError(f"matryoshka_base_dim ({matryoshka_base_dim}) must be greater than new_dim ({new_dim})")

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
    result = backfill_space(conn, space_name, batch_size)
    promote_space(conn, space_name)

    # Count folder summaries re-embedded (done inside promote_space)
    folders_count = conn.execute("SELECT COUNT(*) FROM folder_summaries_vec").fetchone()[0]

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
    folder_rows = conn.execute("SELECT folder_path, summary FROM folder_summaries").fetchall()
    if not folder_rows:
        conn.commit()
        return 0

    embed_dim = matryoshka_base_dim or dim
    texts = [row["summary"] for row in folder_rows]
    embeddings = embed_provider.embed(texts, model=model, expected_dim=embed_dim)  # type: ignore[union-attr]

    if matryoshka_base_dim:
        from .embeddings import truncate_embedding

        embeddings = [truncate_embedding(e, dim) if e is not None else None for e in embeddings]

    for row, emb in zip(folder_rows, embeddings, strict=True):
        if emb is None:
            continue
        conn.execute(
            "INSERT INTO folder_summaries_vec (embedding, folder_path) VALUES (?, ?)",
            (_serialize_f32(emb), row["folder_path"]),
        )
    conn.commit()
    return len(folder_rows)
