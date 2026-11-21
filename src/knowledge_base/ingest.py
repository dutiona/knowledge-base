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
from .exceptions import NotFoundError, ValidationError
from .folder_summaries import drop_summary, update_folder_summary
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
    "remove_document",
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


#: A chunk to store: its index in the document, its text, the text's hash and its metadata JSON.
_Item = tuple[int, str, str, str]


def _text_chunks(conn: sqlite3.Connection, clean: str) -> list[str]:
    """Already-redacted *clean* text as it is stored: windows, each split to the input limit, each piece a fixpoint.

    A window can start mid-word, which a rule reads as a boundary, and the input-limit split cuts anywhere, so each
    window and each piece is scrubbed again (#592).
    """
    windows = [scrub(c) for c in _chunk_text(clean)]
    if not windows:
        return []
    return [scrub(piece) for pieces in _bounded_chunks(conn, windows) for piece in pieces]


def _file_chunks(conn: sqlite3.Connection, path: Path, source_type: str) -> tuple[list[_Item], str]:
    """The chunks of the file at *path*, in document order, and the chunk strategy they were cut with.

    A PDF is cut by its pages and sections and a Python file by its AST, each chunk keeping its own metadata; every
    other file is cut as ``ingest_text`` cuts text.
    """
    strategy = _get_chunk_strategy(conn)
    effective_strategy = "semantic" if source_type == "pdf" and strategy == "semantic" else "mechanical"

    if source_type == "pdf":
        image_dir = pdf_image_dir(path, conn)
        if image_dir.exists():
            shutil.rmtree(image_dir)
        text, page_map = _extract_pdf_markdown(path, image_dir=image_dir)
    else:
        text = _extract_markdown_text(path)
        page_map: dict[int, int] = {}
        image_dir = None

    ast_chunks = None
    if source_type == "code" and path.suffix.lower() == ".py":
        ast_chunks = _chunk_python_ast(text)

    if ast_chunks:
        # A piece keeps its function's line range and is named like chunk_python_ast's own oversize split.
        ast_chunks = [
            {**ac, "text": piece, "name": f"{ac['name']}[{k}]" if len(pieces) > 1 else ac["name"]}
            for ac, pieces in zip(
                ast_chunks, _bounded_chunks(conn, [scrub(ac["text"]) for ac in ast_chunks]), strict=True
            )
            for k, piece in enumerate(pieces)
        ]
        items: list[_Item] = []
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
            md_chunks = _chunk_by_section(text, page_map=page_map, image_dir=image_dir if page_map else None)
        else:
            md_chunks = _chunk_markdown(text, page_map=page_map, image_dir=image_dir if page_map else None)
        if not md_chunks:
            return [], effective_strategy
        # The split counts, and looks up, the text that is stored (#592).
        md_chunks = [(scrub(chunk_text), pages) for chunk_text, pages in md_chunks]
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
        items = [(i, c, _content_hash(c), "{}") for i, c in enumerate(_text_chunks(conn, text))]

    # A cut can start a chunk mid-word, which a rule reads as a boundary: every stored chunk is a fixpoint (#592).
    return [
        (i, (clean := scrub(t)), h if clean == t else _content_hash(clean), m) for i, t, h, m in items
    ], effective_strategy


def _insert_chunks(
    conn: sqlite3.Connection,
    items: list[_Item],
    *,
    source_uri: str,
    source_type: str,
    chunk_strategy: str,
    session_id: str | None = None,
    session_ids: set[str] | None = None,
) -> tuple[int, int, list[tuple[int, int]]]:
    """Insert the *items* no document holds yet, every one embedded before the first write; commits nothing.

    A chunk repeated within the items is stored once (chunks.content_hash is UNIQUE, #553); one already stored under
    any document is skipped and returned, so the caller can link it to its sessions.

    Returns ``(chunks_added, chunks_skipped, stored)``: ``stored`` holds the ``(chunk_index, id)`` of each chunk
    already stored, in document order.
    """
    stored: list[tuple[int, int]] = []
    new_items: list[_Item] = []
    skipped = 0
    seen: set[str] = set()
    for item in items:
        if item[2] in seen:
            skipped += 1
            continue
        # Recorded before the probe, so later repeats of an already-stored chunk skip here: one link and one query
        # per distinct hash.
        seen.add(item[2])
        existing = conn.execute("SELECT id FROM chunks WHERE content_hash = ?", (item[2],)).fetchone()
        if existing:
            stored.append((item[0], existing["id"]))
            skipped += 1
            continue
        new_items.append(item)
    if not new_items:
        return (0, skipped, stored)

    embeddings = _embed_with_config(conn, [item[1] for item in new_items])

    # One vec table for every insert and the single batched chunk_count bump, which counts the vectors inserted (#392).
    vec_table = get_vec_table_name(conn)
    n_inserted = 0
    for (idx, chunk_text, chunk_hash, meta_json), emb_vec in zip(new_items, embeddings, strict=True):
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
            chunk_strategy=chunk_strategy,
            metadata=meta_json,
            vec_table=vec_table,
            bump_count=False,
        )
        if emb_vec is not None:
            n_inserted += 1
    if n_inserted:
        bump_chunk_count(conn, vec_table, n_inserted)
    return (len(new_items), skipped, stored)


def _store_document(
    conn: sqlite3.Connection,
    items: list[_Item],
    *,
    source_uri: str,
    source_type: str,
    chunk_strategy: str,
    session_id: str | None = None,
) -> tuple[int, int]:
    """Store a new document's *items* in one transaction: the one owner of every insert-only document (#597).

    Already-stored chunks are linked to *session_id*. Commits once; any failure rolls back, so the thread's reused
    connection never carries half a document (#554).
    """
    try:
        added, skipped, stored = _insert_chunks(
            conn,
            items,
            source_uri=source_uri,
            source_type=source_type,
            chunk_strategy=chunk_strategy,
            session_id=session_id,
        )
        _flush_deferred_session_links(conn, [cid for _, cid in stored], session_id)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return added, skipped


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

    items, strategy = _file_chunks(conn, path, source_type)
    added, skipped = _store_document(
        conn, items, source_uri=path.as_posix(), source_type=source_type, chunk_strategy=strategy, session_id=session_id
    )

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
    failure rolls back (``_store_document``, which stores files the same way).
    """
    # Redact the whole text before chunking: a 1000-character window can cut a token in two.
    chunks = _text_chunks(conn, scrub(text))
    if not chunks:
        return {"chunks_added": 0, "chunks_skipped": 0}
    meta_json = json.dumps(metadata or {})
    added, skipped = _store_document(
        conn,
        [(i, c, _content_hash(c), meta_json) for i, c in enumerate(chunks)],
        source_uri=source_uri,
        source_type=source_type,
        chunk_strategy="mechanical",
        session_id=session_id,
    )
    return {"chunks_added": added, "chunks_skipped": skipped}


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
    if not path.exists():
        if existing:
            raise NotFoundError(
                f"{source_uri} no longer exists: remove its document with knowledge-base-ingest remove {source_uri} "
                "(server stopped)"
            )
        raise NotFoundError(f"Path does not exist: {source_uri}")
    if not existing:
        raise NotFoundError(f"No chunks found for source_uri: {source_uri}")

    old_ids = [r["id"] for r in existing]

    # Wrap the destructive delete+reinsert so a mid-reingest failure never
    # leaves a dirty transaction on the reused thread-local connection that a
    # later write would commit as a truncated document (#554 review).
    try:
        # The papers and named entities pointing at the old chunks, re-linked to the new ones below;
        # delete_chunks_cascade releases every reference to the old chunks.
        affected_paper_ids = [
            r["id"] for r in _batched_select(conn, "SELECT id FROM papers WHERE abstract_chunk_id IN ({ph})", old_ids)
        ]
        affected_entities: dict[str, list[dict]] = {
            table: [
                {"id": r["id"], "name": r["name"], "old_chunk_id": r["chunk_id"]}
                for r in _batched_select(
                    conn,
                    f"SELECT id, name, chunk_id FROM {table} WHERE chunk_id IN ({{ph}})",  # noqa: S608  # table is a hardcoded literal identifier, not user input
                    old_ids,
                )
            ]
            for table in ("methods", "datasets", "metrics")
        }

        # --- Preserve historical session associations ---
        historical_sessions = {
            r["session_id"]
            for r in conn.execute(
                "SELECT DISTINCT session_id FROM chunk_sessions "
                "WHERE chunk_id IN (SELECT id FROM chunks WHERE source_uri = ?)",
                (source_uri,),
            ).fetchall()
        }

        # --- Delete old chunks (references, vectors in every space, rows) ---
        delete_chunks_cascade(conn, old_ids)

        # --- Re-ingest ---
        if source_type is None:
            source_type = _detect_source_type(path)

        all_sessions = historical_sessions
        if session_id is not None:
            all_sessions = historical_sessions | {session_id}

        # Its own rows are gone, so the probe finds only chunks another document holds (#592: redaction and
        # the redact --rewrite merge make that sharing common); link them instead of inserting them again.
        items, strategy = _file_chunks(conn, path, source_type)
        added, _, shared = _insert_chunks(
            conn,
            items,
            source_uri=source_uri,
            source_type=source_type,
            chunk_strategy=strategy,
            session_id=session_id,
            session_ids=all_sessions,
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


def remove_document(conn: sqlite3.Connection, source_uri: str) -> dict:
    """Remove the document stored under *source_uri* (#595, #631), in one transaction.

    Every chunk under it goes, a web page's figures included, with its vectors in every space and every reference to
    it. A file document is removed only once its file is gone (deleted or moved), with its folder's summary, which
    the folder's next ingest rebuilds; while the file exists it is refused, since ``reingest`` replaces it.
    """
    uri = without_userinfo(source_uri)  # ingest stores a URL without its userinfo
    is_file = not uri.lower().startswith(("http://", "https://", "content://"))
    if is_file and Path(uri).exists():
        raise ValidationError(
            f"{uri} exists: reingest replaces a file document from its file; remove takes a URL, a content:// "
            "document, or a file document whose file is gone"
        )
    ids = [r["id"] for r in conn.execute("SELECT id FROM chunks WHERE source_uri = ?", (uri,))]
    if not ids:
        raise NotFoundError(f"no document is stored under {uri}")
    try:
        delete_chunks_cascade(conn, ids)
        if is_file:
            drop_summary(conn, Path(uri).parent.as_posix())
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"source_uri": uri, "chunks_deleted": len(ids)}


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
