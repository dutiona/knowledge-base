"""Ingest pipeline for PDF and markdown files."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
from pathlib import Path

import fitz  # pymupdf

from .db import EMBED_DIM
from .embeddings import embed

CHUNK_SIZE = 1000  # characters
CHUNK_OVERLAP = 200


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if len(text) <= size:
        return [text] if text.strip() else []
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def _serialize_f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _extract_pdf_text(path: Path) -> str:
    doc = fitz.open(str(path))
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return "\n\n".join(pages)


def _extract_markdown_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def ingest_file(
    conn: sqlite3.Connection,
    path: Path,
    source_type: str | None = None,
) -> dict:
    path = path.resolve()
    if source_type is None:
        ext = path.suffix.lower()
        source_type = {"pdf": "pdf", ".md": "markdown", ".txt": "markdown"}.get(
            ext if ext != ".pdf" else "pdf", "markdown"
        )
        if ext == ".pdf":
            source_type = "pdf"
        elif ext in (".md", ".txt", ".typ", ".rst"):
            source_type = "markdown"
        elif ext in (".py", ".rs", ".cpp", ".c", ".h", ".hpp", ".toml", ".yaml", ".yml", ".json"):
            source_type = "code"
        else:
            source_type = "markdown"

    if source_type == "pdf":
        text = _extract_pdf_text(path)
    else:
        text = _extract_markdown_text(path)

    chunks = _chunk_text(text)
    if not chunks:
        return {"file": str(path), "chunks_added": 0, "chunks_skipped": 0}

    # Compute content hashes, skip duplicates
    new_chunks = []
    skipped = 0
    for i, chunk in enumerate(chunks):
        h = _content_hash(chunk)
        existing = conn.execute("SELECT id FROM chunks WHERE content_hash = ?", (h,)).fetchone()
        if existing:
            skipped += 1
            continue
        new_chunks.append((i, chunk, h))

    if not new_chunks:
        return {"file": str(path), "chunks_added": 0, "chunks_skipped": skipped}

    # Embed all new chunks
    texts_to_embed = [c[1] for c in new_chunks]
    embeddings = embed(texts_to_embed)

    # Insert
    source_uri = str(path)
    for (idx, chunk_text, chunk_hash), emb_vec in zip(new_chunks, embeddings):
        cursor = conn.execute(
            """INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index)
               VALUES (?, ?, ?, ?, ?)""",
            (chunk_hash, chunk_text, source_type, source_uri, idx),
        )
        chunk_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO chunks_vec (rowid, embedding, chunk_id) VALUES (?, ?, ?)",
            (chunk_id, _serialize_f32(emb_vec), chunk_id),
        )

    conn.commit()
    return {"file": str(path), "chunks_added": len(new_chunks), "chunks_skipped": skipped}


def ingest_directory(
    conn: sqlite3.Connection,
    directory: Path,
    extensions: set[str] | None = None,
) -> list[dict]:
    if extensions is None:
        extensions = {".pdf", ".md", ".txt", ".typ", ".rst"}
    results = []
    for f in sorted(directory.rglob("*")):
        if f.is_file() and f.suffix.lower() in extensions:
            result = ingest_file(conn, f)
            results.append(result)
    return results
