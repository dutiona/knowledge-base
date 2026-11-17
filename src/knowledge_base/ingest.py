"""Ingest pipeline for PDF, markdown, and code files."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any, cast

import fitz  # pymupdf

from .chunking import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    IMAGE_REF_RE,
    chunk_by_section as _chunk_by_section,
    chunk_markdown as _chunk_markdown,
    chunk_python_ast as _chunk_python_ast,
    chunk_text as _chunk_text,
)
from .db import (
    _batched_execute,
    _batched_select,
    bump_chunk_count,
    delete_chunks_cascade,
    get_vec_table_name,
    insert_chunk_vec,
    require_active_space,
)
from .migrate import _main_db_path
from .redact import redact_json, scrub
from .embed_swap import declared_provider_config
from .embeddings import embed, split_inputs, truncate_embedding
from .exceptions import NotFoundError
from .folder_summaries import update_folder_summary
from .utils import content_hash as _content_hash
from .utils import without_userinfo

logger = logging.getLogger(__name__)


def _update_folder_summary_safe(conn: sqlite3.Connection, folder: str) -> None:
    """Update *folder*'s summary, swallowing errors."""
    try:
        update_folder_summary(conn, folder)
    except Exception:
        logger.warning("Failed to update folder summary for %s", folder, exc_info=True)


__all__ = [
    "CHUNK_OVERLAP",
    "CHUNK_SIZE",
    "ingest_directory",
    "ingest_file",
    "ingest_text",
    "kb_data_dir",
    "pdf_image_dir",
    "reingest_file",
]


def kb_data_dir(conn: sqlite3.Connection | None = None) -> Path:
    """Base directory for on-disk artifacts (figures, rendered pages).

    Resolution order (#391):

    1. ``KNOWLEDGE_BASE_DATA_DIR`` environment variable (stripped and
       ``expanduser``-ed, mirroring ``resolve_db_path``'s handling of
       ``KNOWLEDGE_BASE_DB``);
    2. the directory of *conn*'s main database file — artifacts live beside
       the DB they belong to (same convention as ``migrate.py`` backups), so
       tests on tmp-path DBs and relocated production DBs are isolated
       structurally, not by remembering a knob;
    3. the default home data dir (conn-less callers, in-memory DBs).

    Resolved at call time, not import time, so a monkeypatched environment
    takes effect immediately.
    """
    env = os.environ.get("KNOWLEDGE_BASE_DATA_DIR")
    if env and env.strip():
        return Path(env).expanduser()
    if conn is not None:
        db_file = _main_db_path(conn)
        if db_file is not None:
            return db_file.parent
    return Path.home() / ".local" / "share" / "knowledge-base"


def _detect_source_type(path: Path) -> str:
    """Detect source_type from file extension."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in (".md", ".txt", ".typ", ".rst"):
        return "markdown"
    if ext in (
        ".py",
        ".rs",
        ".cpp",
        ".c",
        ".h",
        ".hpp",
        ".toml",
        ".yaml",
        ".yml",
        ".json",
    ):
        return "code"
    return "markdown"


def _embed_with_config(conn: sqlite3.Connection, texts: list[str]) -> list[list[float] | None]:
    """Embed document *texts* into the active space, after its identity check (ADR-0015 §3, #573).

    Returns None entries for texts whose embeddings had zero norm.
    """
    active = require_active_space(conn)
    provider_cfg = declared_provider_config(conn, active)
    base_dim = active["matryoshka_base_dim"]
    if base_dim:
        vecs = embed(texts, model=active["model"], expected_dim=base_dim, _provider_cfg=provider_cfg)
        return [truncate_embedding(v, active["dim"]) if v is not None else None for v in vecs]
    return embed(texts, model=active["model"], expected_dim=active["dim"], _provider_cfg=provider_cfg)


def _bounded_chunks(conn: sqlite3.Connection, texts: list[str]) -> list[list[str]]:
    """Each chunk as the consecutive pieces that fit the active space's ``max_input_tokens`` (#604).

    A chunk already stored whole is kept whole without being counted, so dedup skips it and an unchanged
    re-ingest sends the embedder nothing.
    """
    cfg = declared_provider_config(conn, require_active_space(conn))
    if cfg.max_input_tokens is None:
        return [[text] for text in texts]
    stored = [
        conn.execute("SELECT 1 FROM chunks WHERE content_hash = ?", (_content_hash(text),)).fetchone() is not None
        for text in texts
    ]
    split = iter(split_inputs([text for text, known in zip(texts, stored, strict=True) if not known], cfg))
    return [[text] if known else next(split) for text, known in zip(texts, stored, strict=True)]


_WHOLE_URL = re.compile(r"(?i)https?://\S+")


def _metadata_leaf(value: str) -> str:
    """A whole URL is a stored reference and keeps all but its userinfo (owner ruling); other text is scrubbed."""
    return without_userinfo(value) if _WHOLE_URL.fullmatch(value) else scrub(value)


def redact_metadata(metadata: str) -> str:
    """A chunk's metadata JSON with every string value redacted, or ``metadata`` itself when nothing changes."""
    value = json.loads(metadata)
    clean = redact_json(value, _metadata_leaf)
    return metadata if clean == value else json.dumps(clean)


def _flush_deferred_session_links(conn: sqlite3.Connection, chunk_ids: list[int], session_id: str | None) -> None:
    """Batch-insert deferred chunk_sessions rows for deduped chunks."""
    if chunk_ids and session_id is not None:
        conn.executemany(
            "INSERT OR IGNORE INTO chunk_sessions (chunk_id, session_id) VALUES (?, ?)",
            [(cid, session_id) for cid in chunk_ids],
        )


def _insert_chunk(
    conn: sqlite3.Connection,
    *,
    content_hash: str,
    content: str,
    source_type: str,
    source_uri: str,
    chunk_index: int,
    embedding: list[float] | None = None,
    session_id: str | None = None,
    session_ids: set[str] | None = None,
    chunk_strategy: str | None = None,
    metadata: str = "{}",
    vec_table: str | None = None,
    bump_count: bool = True,
) -> int:
    """Insert a single chunk row, its embedding, and session links.

    Returns the new chunk id.

    *bump_count* is threaded straight through to :func:`insert_chunk_vec`: batch
    callers pass ``False`` to skip the per-row ``chunk_count`` UPDATE and instead
    bump once after the loop (#392). It defaults to ``True`` so single/incidental
    callers keep the per-row bookkeeping.
    """
    columns = [
        "content_hash",
        "content",
        "source_type",
        "source_uri",
        "chunk_index",
        "session_id",
        "metadata",
    ]
    values: list[str | int | None] = [
        content_hash,
        content,
        source_type,
        source_uri,
        chunk_index,
        session_id,
        redact_metadata(metadata),  # neither hashed nor embedded, so the insert is its one complete point
    ]
    if chunk_strategy is not None:
        columns.append("chunk_strategy")
        values.append(chunk_strategy)

    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO chunks ({', '.join(columns)}) VALUES ({placeholders})"  # noqa: S608  # columns are hardcoded literal identifiers; values bound via params
    cursor = conn.execute(sql, values)
    chunk_id = cursor.lastrowid
    assert chunk_id is not None  # noqa: S101  # internal invariant: a successful INSERT into a rowid table always sets lastrowid

    # Link to sessions via junction table — union session_id into session_ids
    effective_sessions = set(session_ids) if session_ids else set()
    if session_id is not None:
        effective_sessions.add(session_id)
    if effective_sessions:
        conn.executemany(
            "INSERT OR IGNORE INTO chunk_sessions (chunk_id, session_id) VALUES (?, ?)",
            [(chunk_id, sid) for sid in effective_sessions],
        )

    if embedding is not None:
        insert_chunk_vec(conn, chunk_id, embedding, table_name=vec_table, bump_count=bump_count)

    return chunk_id


_IMAGE_REF_RE = IMAGE_REF_RE  # back-compat alias used in this module

# pdf_image_dir hashing/truncation knobs
_HASH_READ_CHUNK_BYTES = 8192  # streaming read size when hashing the source file
_FILE_HASH_HEX_LEN = 16  # chars of the sha256 hex digest kept for the dir name
_STEM_MAX_LEN = 64  # max chars of the sanitized file stem kept for the dir name

# Separator inserted between per-page texts when joining PDF markdown pages.
_PAGE_SEPARATOR = "\n\n"


def _get_chunk_strategy(conn: sqlite3.Connection) -> str:
    """Read the active chunk strategy from config. Defaults to 'mechanical'."""
    row = conn.execute("SELECT value FROM config WHERE key = 'chunk_strategy'").fetchone()
    return row["value"] if row else "mechanical"


def _extract_pdf_text(path: Path) -> str:
    """Flat text extraction — fallback when pymupdf4llm is unavailable."""
    doc = fitz.open(str(path))
    pages = []
    for page in doc:
        pages.append(scrub(cast("str", page.get_text())))  # "text" mode returns a str; the stub says more
    doc.close()
    return "\n\n".join(pages)


def pdf_image_dir(path: Path, conn: sqlite3.Connection | None = None) -> Path:
    """Content-hash keyed directory for extracted images.

    Pass *conn* where available so the directory is derived from the live
    DB's location (see :func:`kb_data_dir`) — ingest-time writers and
    vision-time readers agree as long as they use the same database.
    """
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_HASH_READ_CHUNK_BYTES):
            h.update(chunk)
    file_hash = h.hexdigest()[:_FILE_HASH_HEX_LEN]
    sanitized = re.sub(r"[^\w\-.]", "_", path.stem)[:_STEM_MAX_LEN]
    return kb_data_dir(conn) / "figures" / f"{sanitized}_{file_hash}" / "extracted"


def _extract_pdf_markdown(path: Path, image_dir: Path | None = None) -> tuple[str, dict[int, int]]:
    """Extract structured markdown from a PDF using pymupdf4llm.

    Returns (markdown_text, page_map) where page_map maps char offsets
    in the returned string to page numbers. Falls back to
    _extract_pdf_text() on ImportError or RuntimeError.
    """
    try:
        import pymupdf4llm
    except ImportError:
        logger.warning("pymupdf4llm not available, falling back to flat extraction")
        return _extract_pdf_text(path), {}
    try:
        if image_dir:
            image_dir.mkdir(parents=True, exist_ok=True)
        # With page_chunks=True, to_markdown returns a list of per-page dicts
        # (the stub annotates it as str), so narrow to the documented shape.
        pages = cast(
            "list[dict[str, Any]]",
            pymupdf4llm.to_markdown(
                str(path),
                write_images=image_dir is not None,
                image_path=str(image_dir) if image_dir else "",
                image_format="png",
                page_chunks=True,
                force_text=True,
            ),
        )
        page_map: dict[int, int] = {}
        offset = 0
        texts = []
        for pno, p in enumerate(pages):
            meta = p.get("metadata", {})
            page_num = meta.get("page_number") or meta.get("page") or pno + 1
            # Redact each page before its length moves the next offset, so page_map stays exact.
            page_text = scrub(p["text"])
            page_map[offset] = page_num
            texts.append(page_text)
            offset += len(page_text) + len(_PAGE_SEPARATOR)
        return _PAGE_SEPARATOR.join(texts), page_map
    except (RuntimeError, OSError) as exc:
        logger.warning("pymupdf4llm failed (%s), falling back to flat extraction", exc)
        return _extract_pdf_text(path), {}


def _extract_markdown_text(path: Path) -> str:
    """Read a markdown/text file as UTF-8, replacing undecodable bytes, and redact it."""
    return scrub(path.read_text(encoding="utf-8", errors="replace"))


def _produce_and_insert_chunks(
    conn: sqlite3.Connection,
    *,
    path: Path,
    source_type: str,
    source_uri: str,
    session_id: str | None = None,
    session_ids: set[str] | None = None,
    deduplicate: bool = True,
) -> tuple[int, int, list[tuple[int, int]]]:
    """Extract text, produce chunks, embed, and insert into the database.

    Does NOT commit the transaction — callers handle commit timing.

    When *deduplicate* is True, existing chunks (by content hash) are
    skipped and returned, so the caller can link them to its sessions.

    Returns ``(chunks_added, chunks_skipped, deduplicated)``: ``deduplicated`` holds the ``(chunk_index, id)`` of
    each chunk already stored under another document, in document order, whatever *session_id* is.
    """
    strategy = _get_chunk_strategy(conn)
    effective_strategy = "semantic" if source_type == "pdf" and strategy == "semantic" else "mechanical"

    # --- Text extraction ---
    if source_type == "pdf":
        image_dir = pdf_image_dir(path, conn)
        if image_dir.exists():
            shutil.rmtree(image_dir)
        text, page_map = _extract_pdf_markdown(path, image_dir=image_dir)
    else:
        text = _extract_markdown_text(path)
        page_map: dict[int, int] = {}
        image_dir = None

    # --- Chunk production ---
    ast_chunks = None
    if source_type == "code" and path.suffix.lower() == ".py":
        ast_chunks = _chunk_python_ast(text)

    if ast_chunks:
        # A piece keeps its function's line range and is named like chunk_python_ast's own oversize split.
        ast_chunks = [
            {**ac, "text": piece, "name": f"{ac['name']}[{k}]" if len(pieces) > 1 else ac["name"]}
            for ac, pieces in zip(ast_chunks, _bounded_chunks(conn, [ac["text"] for ac in ast_chunks]), strict=True)
            for k, piece in enumerate(pieces)
        ]
        items: list[tuple[int, str, str, str]] = []
        for i, ac in enumerate(ast_chunks):
            meta = json.dumps(
                {
                    "name": ac["name"],
                    "type": ac["type"],
                    "start_line": ac["start_line"],
                    "end_line": ac["end_line"],
                }
            )
            items.append((i, ac["text"], _content_hash(ac["text"]), meta))
    elif source_type == "pdf":
        if effective_strategy == "semantic":
            md_chunks = _chunk_by_section(
                text,
                page_map=page_map,
                image_dir=image_dir if page_map else None,
            )
        else:
            md_chunks = _chunk_markdown(
                text,
                page_map=page_map,
                image_dir=image_dir if page_map else None,
            )
        if not md_chunks:
            return (0, 0, [])
        # A piece keeps its parent's pages and lists the images whose reference starts in it. They are read from
        # the whole parent, because a cut can fall inside a reference's caption.
        split: list[tuple[str, list[int], list[str]]] = []
        pieces_of = _bounded_chunks(conn, [chunk_text for chunk_text, _ in md_chunks])
        for (chunk_text, pages), pieces in zip(md_chunks, pieces_of, strict=True):
            refs = [(m.start(), Path(m.group(2)).name) for m in _IMAGE_REF_RE.finditer(chunk_text)]
            start = 0
            for piece in pieces:
                end = start + len(piece)
                split.append((piece, pages, [name for at, name in refs if start <= at < end]))
                start = end
        try:
            import pymupdf4llm

            extractor_tag = f"pymupdf4llm@{pymupdf4llm.__version__}"
        except (ImportError, AttributeError):
            extractor_tag = "pymupdf4llm"
        items = []
        for i, (chunk_text, chunk_pages, images) in enumerate(split):
            chunk_meta: dict = {"extractor": extractor_tag, "pages": chunk_pages}
            if images:
                chunk_meta["images"] = images
            items.append((i, chunk_text, _content_hash(chunk_text), json.dumps(chunk_meta)))
    else:
        fixed_chunks = [piece for pieces in _bounded_chunks(conn, _chunk_text(text)) for piece in pieces]
        if not fixed_chunks:
            return (0, 0, [])
        items = [(i, c, _content_hash(c), "{}") for i, c in enumerate(fixed_chunks)]

    # A cut can start a chunk mid-word, which a rule reads as a boundary: every stored chunk is a fixpoint (#592).
    items = [(i, (clean := scrub(t)), h if clean == t else _content_hash(clean), m) for i, t, h, m in items]

    # --- Deduplication ---
    # The intra-document (in-batch) hash collapse ALWAYS runs: two identical chunks
    # in one document share a content_hash and the second would hit the
    # chunks.content_hash UNIQUE constraint (#553). This is independent of
    # *deduplicate*, which only governs the cross-document DB probe below (#553 review).
    deduplicated: list[tuple[int, int]] = []
    new_items: list[tuple[int, str, str, str]] = []
    skipped = 0
    batch_hashes: set[str] = set()
    for item in items:
        if item[2] in batch_hashes:
            skipped += 1
            continue
        # Record the hash before the DB probe so later in-batch duplicates
        # of an ALREADY-PERSISTED chunk skip here too — one session link
        # and one DB query per distinct hash, not per occurrence.
        batch_hashes.add(item[2])
        if deduplicate:
            existing = conn.execute("SELECT id FROM chunks WHERE content_hash = ?", (item[2],)).fetchone()
            if existing:
                deduplicated.append((item[0], existing["id"]))
                skipped += 1
                continue
        new_items.append(item)
    items = new_items

    if not items:
        return (0, skipped, deduplicated)

    # --- Embed + insert ---
    texts_to_embed = [item[1] for item in items]
    embeddings = _embed_with_config(conn, texts_to_embed)

    # Resolve the active vec table once so every per-chunk insert targets the same
    # table AND the single batched chunk_count bump below matches it (#392). The
    # per-row UPDATE is suppressed (bump_count=False); we tally the embeddings
    # actually inserted and bump once after the loop — N+1 UPDATEs collapse to 1,
    # with a chunk_count identical to the per-row path.
    vec_table = get_vec_table_name(conn)
    n_inserted = 0
    for (idx, chunk_text, chunk_hash, meta_json), emb_vec in zip(items, embeddings, strict=True):
        _insert_chunk(
            conn,
            content_hash=chunk_hash,
            content=chunk_text,
            source_type=source_type,
            source_uri=source_uri,
            chunk_index=idx,
            embedding=emb_vec,
            session_id=session_id,
            session_ids=session_ids,
            chunk_strategy=effective_strategy,
            metadata=meta_json,
            vec_table=vec_table,
            bump_count=False,
        )
        if emb_vec is not None:
            n_inserted += 1

    if n_inserted:
        bump_chunk_count(conn, vec_table, n_inserted)

    return (len(items), skipped, deduplicated)


def ingest_file(
    conn: sqlite3.Connection,
    path: Path,
    source_type: str | None = None,
    session_id: str | None = None,
    _skip_folder_summary: bool = False,
) -> dict:
    path = path.resolve()
    if source_type is None:
        source_type = _detect_source_type(path)

    added, skipped, deferred = _produce_and_insert_chunks(
        conn,
        path=path,
        source_type=source_type,
        source_uri=path.as_posix(),
        session_id=session_id,
        deduplicate=True,
    )

    # Flush deferred session links for deduped chunks (#180).
    _flush_deferred_session_links(conn, [cid for _, cid in deferred], session_id)
    conn.commit()

    result: dict = {
        "file": path.as_posix(),
        "chunks_added": added,
        "chunks_skipped": skipped,
    }

    if added == 0 and skipped > 0:
        from .papers import compute_file_hash

        try:
            file_hash = compute_file_hash(path)
        except OSError:
            file_hash = None
        if file_hash:
            existing_paper = conn.execute(
                "SELECT paper_id FROM paper_paths WHERE content_hash = ?",
                (file_hash,),
            ).fetchone()
            if existing_paper:
                result["duplicate_of_paper_id"] = existing_paper["paper_id"]

    if added > 0 and not _skip_folder_summary:
        _update_folder_summary_safe(conn, path.parent.as_posix())

    return result


def ingest_text(
    conn: sqlite3.Connection,
    text: str,
    *,
    source_uri: str,
    source_type: str,
    session_id: str | None = None,
    metadata: dict | None = None,
) -> dict[str, int]:
    """Store *text* as chunks of one document: the single path for text stored by content or by URL.

    Insert-only: a chunk repeated within *text*, or whose hash is already stored under any source, is
    skipped (a stored one is linked to *session_id*); nothing stored is deleted or updated. Every new
    chunk is embedded before the first write, so an embedder failure stores nothing. Commits once; any
    failure rolls back, so the thread's reused connection never carries half a document (#554).
    """
    # Redact the whole text before chunking (a 1000-character window can cut a token in two), then each chunk:
    # a window can start mid-word, which a rule reads as a boundary, so every stored chunk is a fixpoint.
    chunks = [scrub(c) for c in _chunk_text(scrub(text))]
    if not chunks:
        return {"chunks_added": 0, "chunks_skipped": 0}
    chunks = [piece for pieces in _bounded_chunks(conn, chunks) for piece in pieces]
    try:
        return _store_text_chunks(conn, chunks, source_uri, source_type, session_id, metadata)
    except BaseException:
        conn.rollback()
        raise


def _store_text_chunks(
    conn: sqlite3.Connection,
    chunks: list[str],
    source_uri: str,
    source_type: str,
    session_id: str | None,
    metadata: dict | None,
) -> dict[str, int]:
    # Defer chunk_sessions writes until after embeddings succeed (#180).
    new_chunks: list[tuple[int, str, str]] = []
    skipped = 0
    deferred_session_links: list[int] = []
    seen: set[str] = set()
    for i, chunk in enumerate(chunks):
        h = _content_hash(chunk)
        if h in seen:  # chunks.content_hash is UNIQUE: a repeat within the text is stored once (#553)
            skipped += 1
            continue
        seen.add(h)
        existing = conn.execute("SELECT id FROM chunks WHERE content_hash = ?", (h,)).fetchone()
        if existing:
            if session_id is not None:
                deferred_session_links.append(existing["id"])
            skipped += 1
            continue
        new_chunks.append((i, chunk, h))

    if not new_chunks:
        _flush_deferred_session_links(conn, deferred_session_links, session_id)
        conn.commit()
        return {"chunks_added": 0, "chunks_skipped": skipped}

    embeddings = _embed_with_config(conn, [c[1] for c in new_chunks])

    # One vec table for every insert and the single batched chunk_count bump (#392).
    vec_table = get_vec_table_name(conn)
    meta_json = json.dumps(metadata or {})
    n_inserted = 0
    for (idx, chunk_text, chunk_hash), emb_vec in zip(new_chunks, embeddings, strict=True):
        _insert_chunk(
            conn,
            content_hash=chunk_hash,
            content=chunk_text,
            source_type=source_type,
            source_uri=source_uri,
            chunk_index=idx,
            embedding=emb_vec,
            session_id=session_id,
            metadata=meta_json,
            vec_table=vec_table,
            bump_count=False,
        )
        if emb_vec is not None:
            n_inserted += 1
    if n_inserted:
        bump_chunk_count(conn, vec_table, n_inserted)

    _flush_deferred_session_links(conn, deferred_session_links, session_id)
    conn.commit()
    return {"chunks_added": len(new_chunks), "chunks_skipped": skipped}


def _cleanup_conclusion_refs(conn: sqlite3.Connection, chunk_ids: list[int]) -> None:
    """Remove *chunk_ids* from ``conclusions.source_chunk_ids`` JSON arrays.

    Uses ``json_each()`` to target only conclusions that reference any of the
    given IDs instead of scanning the full table (#277).  Conclusions left
    with an empty array are deleted (zombie cleanup, #160); their
    ``superseded_by`` back-references are cleared first.
    """
    if not chunk_ids:
        return
    id_set = set(chunk_ids)
    rows = _batched_select(
        conn,
        "SELECT DISTINCT c.id, c.source_chunk_ids "
        "FROM conclusions c, json_each(c.source_chunk_ids) j "
        "WHERE j.value IN ({ph})",
        chunk_ids,
    )
    # Deduplicate across batches: a conclusion referencing chunks in
    # different batches would otherwise appear once per batch.
    seen: set[int] = set()
    for row in rows:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        filtered = [cid for cid in json.loads(row["source_chunk_ids"]) if cid not in id_set]
        if filtered:
            conn.execute(
                "UPDATE conclusions SET source_chunk_ids = ? WHERE id = ?",
                (json.dumps(filtered), row["id"]),
            )
        else:
            conn.execute(
                "UPDATE conclusions SET superseded_by = NULL WHERE superseded_by = ?",
                (row["id"],),
            )
            conn.execute("DELETE FROM conclusions WHERE id = ?", (row["id"],))


def reingest_file(
    conn: sqlite3.Connection,
    path: Path,
    source_type: str | None = None,
    session_id: str | None = None,
) -> dict:
    """Delete all chunks for a source_uri, then re-ingest the file.

    Cleans up FK references in papers, relationships, and conclusions
    before deleting chunks.
    """
    path = path.resolve()
    source_uri = path.as_posix()

    # Check that this source_uri was previously ingested
    existing = conn.execute("SELECT id FROM chunks WHERE source_uri = ?", (source_uri,)).fetchall()
    if not existing:
        raise NotFoundError(f"No chunks found for source_uri: {source_uri}")

    old_ids = [r["id"] for r in existing]

    # Wrap the destructive delete+reinsert so a mid-reingest failure never
    # leaves a dirty transaction on the reused thread-local connection that a
    # later write would commit as a truncated document (#554 review).
    try:
        # --- FK cleanup (batched to stay under SQLITE_MAX_VARIABLE_NUMBER) ---
        # 1. papers.abstract_chunk_id → SET NULL (track affected papers for re-linking)
        affected_paper_ids = [
            r["id"] for r in _batched_select(conn, "SELECT id FROM papers WHERE abstract_chunk_id IN ({ph})", old_ids)
        ]
        _batched_execute(
            conn,
            "UPDATE papers SET abstract_chunk_id = NULL WHERE abstract_chunk_id IN ({ph})",
            old_ids,
        )

        # 2. relationships.evidence_chunk_id → SET NULL
        _batched_execute(
            conn,
            "UPDATE relationships SET evidence_chunk_id = NULL WHERE evidence_chunk_id IN ({ph})",
            old_ids,
        )

        # 3. conclusions.source_chunk_ids — targeted json_each() cleanup (#277)
        _cleanup_conclusion_refs(conn, old_ids)

        # 4. methods/datasets/metrics.chunk_id → SET NULL (track for re-linking)
        affected_entities: dict[str, list[dict]] = {}
        for table in ("methods", "datasets", "metrics"):
            affected_entities[table] = [
                {"id": r["id"], "name": r["name"], "old_chunk_id": r["chunk_id"]}
                for r in _batched_select(
                    conn,
                    f"SELECT id, name, chunk_id FROM {table} WHERE chunk_id IN ({{ph}})",  # noqa: S608  # table is a hardcoded literal identifier, not user input
                    old_ids,
                )
            ]
            _batched_execute(
                conn,
                f"UPDATE {table} SET chunk_id = NULL WHERE chunk_id IN ({{ph}})",  # noqa: S608  # table is a hardcoded literal identifier, not user input
                old_ids,
            )

        # 5. entity_mentions.chunk_id — NOT NULL FK, must delete (re-created by extraction)
        _batched_execute(
            conn,
            "DELETE FROM entity_mentions WHERE chunk_id IN ({ph})",
            old_ids,
        )

        # --- Preserve historical session associations ---
        historical_sessions = {
            r["session_id"]
            for r in conn.execute(
                "SELECT DISTINCT session_id FROM chunk_sessions "
                "WHERE chunk_id IN (SELECT id FROM chunks WHERE source_uri = ?)",
                (source_uri,),
            ).fetchall()
        }

        # --- Delete old chunks (vec + chunk rows) ---
        delete_chunks_cascade(conn, old_ids)

        # --- Re-ingest ---
        if source_type is None:
            source_type = _detect_source_type(path)

        all_sessions = historical_sessions
        if session_id is not None:
            all_sessions = historical_sessions | {session_id}

        # Its own rows are gone, so the probe finds only chunks another document holds (#592: redaction and
        # the redact --rewrite merge make that sharing common); link them instead of inserting them again.
        added, _, shared = _produce_and_insert_chunks(
            conn,
            path=path,
            source_type=source_type,
            source_uri=source_uri,
            session_id=session_id,
            session_ids=all_sessions,
            deduplicate=True,
        )
        conn.executemany(
            "INSERT OR IGNORE INTO chunk_sessions (chunk_id, session_id) VALUES (?, ?)",
            [(cid, sid) for _, cid in shared for sid in all_sessions],
        )
        # The document's chunks in document order: its new rows and the shared ones together.
        own = conn.execute("SELECT chunk_index, id, content FROM chunks WHERE source_uri = ?", (source_uri,)).fetchall()
        shared_content = {
            r["id"]: r["content"]
            for r in _batched_select(conn, "SELECT id, content FROM chunks WHERE id IN ({ph})", [c for _, c in shared])
        }
        document = sorted(
            [(r["chunk_index"], r["id"], r["content"]) for r in own]
            + [(idx, cid, shared_content[cid]) for idx, cid in shared]
        )

        # --- Re-link papers whose abstract_chunk_id was nullified ---
        if affected_paper_ids and document:
            _batched_execute(
                conn,
                "UPDATE papers SET abstract_chunk_id = ? WHERE id IN ({ph})",
                affected_paper_ids,
                extra_params=[document[0][1]],
            )

        # --- Re-link methods/datasets/metrics by name search ---
        if document:
            for table, affected in affected_entities.items():
                for entity in affected:
                    # Compile the word-boundary pattern once per entity, then reuse
                    # it across every candidate chunk (avoids per-chunk recompilation).
                    name_pattern = re.compile(r"\b" + re.escape(entity["name"]) + r"\b")
                    for _idx, chunk_id, content in document:
                        if name_pattern.search(content):
                            conn.execute(
                                f"UPDATE {table} SET chunk_id = ? WHERE id = ?",  # noqa: S608  # table is a hardcoded literal identifier, not user input
                                (chunk_id, entity["id"]),
                            )
                            break

        # Update paper_paths content_hash if entry exists
        if path.exists():
            from .papers import compute_file_hash

            new_hash = compute_file_hash(path)
            conn.execute(
                "UPDATE paper_paths SET content_hash = ? WHERE path = ?",
                (new_hash, source_uri),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _update_folder_summary_safe(conn, path.parent.as_posix())

    return {
        "file": source_uri,
        "chunks_deleted": len(old_ids),
        "chunks_added": added,
    }


def ingest_directory(
    conn: sqlite3.Connection,
    directory: Path,
    extensions: set[str] | None = None,
    session_id: str | None = None,
) -> list[dict]:
    import uuid

    if extensions is None:
        extensions = {".pdf", ".md", ".txt", ".typ", ".rst"}
    if session_id is None:
        session_id = str(uuid.uuid4())
    results = []
    affected_folders: set[str] = set()
    for f in sorted(directory.rglob("*")):
        if f.is_file() and f.suffix.lower() in extensions:
            result = ingest_file(conn, f, session_id=session_id, _skip_folder_summary=True)
            results.append(result)
            affected_folders.add(f.resolve().parent.as_posix())

    # Batch-update folder summaries once per affected folder
    for folder in sorted(affected_folders):
        _update_folder_summary_safe(conn, folder)

    return results
