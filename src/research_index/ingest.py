"""Ingest pipeline for PDF, markdown, code, and web files."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sqlite3
import struct
from pathlib import Path

import fitz  # pymupdf
import httpx
import trafilatura

from .db import EMBED_DIM
from .embed_swap import get_embed_config
from .embeddings import embed

CHUNK_SIZE = 1000  # characters
CHUNK_OVERLAP = 200

_ALLOWED_URL_SCHEMES = {"http", "https"}

# SQLite's default SQLITE_MAX_VARIABLE_NUMBER is 999.
# We use 900 to leave headroom for other parameters in the same statement.
_SQL_BATCH_SIZE = 900


def _batched_execute(
    conn: sqlite3.Connection,
    sql_template: str,
    ids: list,
    extra_params: list | None = None,
) -> None:
    """Execute a SQL statement with an IN clause in batches.

    sql_template must contain a single ``{ph}`` placeholder where the
    ``IN (?,?,...)`` list will be substituted.  extra_params (if given)
    are prepended to each batch's parameter list.
    """
    for i in range(0, len(ids), _SQL_BATCH_SIZE):
        batch = ids[i : i + _SQL_BATCH_SIZE]
        placeholders = ",".join("?" * len(batch))
        params = (extra_params or []) + batch
        conn.execute(sql_template.format(ph=placeholders), params)


def _batched_select(
    conn: sqlite3.Connection, sql_template: str, ids: list
) -> list[sqlite3.Row]:
    """Execute a SELECT with an IN clause in batches, returning all rows."""
    results: list[sqlite3.Row] = []
    for i in range(0, len(ids), _SQL_BATCH_SIZE):
        batch = ids[i : i + _SQL_BATCH_SIZE]
        placeholders = ",".join("?" * len(batch))
        results.extend(
            conn.execute(sql_template.format(ph=placeholders), batch).fetchall()
        )
    return results


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


def _embed_with_config(conn: sqlite3.Connection, texts: list[str]) -> list[list[float]]:
    """Call embed() using the model and dim from the config table."""
    cfg = get_embed_config(conn)
    return embed(texts, model=cfg["model"], expected_dim=cfg["dim"])


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _chunk_text(
    text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP
) -> list[str]:
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


def _chunk_python_ast(source: str, max_chunk_chars: int = CHUNK_SIZE) -> list[dict]:
    """Split Python source into semantic chunks using the ast module.

    Returns list of dicts with keys: text, name, type, start_line, end_line.
    Oversized chunks (> max_chunk_chars) are split using fixed-size chunking.
    Returns empty list on syntax error (caller should fall back to fixed-size).
    """
    if not source.strip():
        return []

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    lines = source.splitlines(keepends=True)
    chunks = []

    # Collect top-level node line ranges
    top_level_ranges: list[tuple[int, int, str, str]] = []  # (start, end, name, type)
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end_line = node.end_lineno or node.lineno
            top_level_ranges.append((node.lineno, end_line, node.name, "function"))
        elif isinstance(node, ast.ClassDef):
            end_line = node.end_lineno or node.lineno
            top_level_ranges.append((node.lineno, end_line, node.name, "class"))

    # Collect module-level code (lines not covered by any function/class)
    if top_level_ranges:
        covered = set()
        for start, end, _, _ in top_level_ranges:
            for i in range(start, end + 1):
                covered.add(i)

        module_lines = []
        for i, line in enumerate(lines, 1):
            if i not in covered:
                module_lines.append(line)

        module_text = "".join(module_lines).strip()
        if module_text:
            chunks.append(
                {
                    "text": module_text,
                    "name": "<module>",
                    "type": "module",
                    "start_line": 1,
                    "end_line": len(lines),
                }
            )

    # Add function/class chunks
    for start, end, name, node_type in top_level_ranges:
        text = "".join(lines[start - 1 : end]).rstrip()
        if text:
            chunks.append(
                {
                    "text": text,
                    "name": name,
                    "type": node_type,
                    "start_line": start,
                    "end_line": end,
                }
            )

    # Split oversized chunks to stay within embedding model token limits
    bounded = []
    for chunk in chunks:
        if len(chunk["text"]) <= max_chunk_chars:
            bounded.append(chunk)
        else:
            sub_texts = _chunk_text(chunk["text"], size=max_chunk_chars)
            for i, sub in enumerate(sub_texts):
                bounded.append(
                    {
                        "text": sub,
                        "name": f"{chunk['name']}[{i}]",
                        "type": chunk["type"],
                        "start_line": chunk["start_line"],
                        "end_line": chunk["end_line"],
                    }
                )

    return bounded


def ingest_file(
    conn: sqlite3.Connection,
    path: Path,
    source_type: str | None = None,
) -> dict:
    path = path.resolve()
    if source_type is None:
        source_type = _detect_source_type(path)

    if source_type == "pdf":
        text = _extract_pdf_text(path)
    else:
        text = _extract_markdown_text(path)

    # Try AST-aware chunking for Python files
    ast_chunks = None
    if source_type == "code" and path.suffix.lower() == ".py":
        ast_chunks = _chunk_python_ast(text)

    if ast_chunks:
        # AST-aware path: each chunk has metadata
        new_chunks = []
        skipped = 0
        for i, ac in enumerate(ast_chunks):
            h = _content_hash(ac["text"])
            existing = conn.execute(
                "SELECT id FROM chunks WHERE content_hash = ?", (h,)
            ).fetchone()
            if existing:
                skipped += 1
                continue
            meta = {
                "name": ac["name"],
                "type": ac["type"],
                "start_line": ac["start_line"],
                "end_line": ac["end_line"],
            }
            new_chunks.append((i, ac["text"], h, json.dumps(meta)))
    else:
        # Fixed-size chunking path
        fixed_chunks = _chunk_text(text)
        if not fixed_chunks:
            return {"file": str(path), "chunks_added": 0, "chunks_skipped": 0}
        new_chunks = []
        skipped = 0
        for i, chunk in enumerate(fixed_chunks):
            h = _content_hash(chunk)
            existing = conn.execute(
                "SELECT id FROM chunks WHERE content_hash = ?", (h,)
            ).fetchone()
            if existing:
                skipped += 1
                continue
            new_chunks.append((i, chunk, h, "{}"))

    if not new_chunks:
        return {"file": str(path), "chunks_added": 0, "chunks_skipped": skipped}

    # Embed all new chunks using configured model
    texts_to_embed = [c[1] for c in new_chunks]
    embeddings = _embed_with_config(conn, texts_to_embed)

    # Insert
    source_uri = str(path)
    for (idx, chunk_text, chunk_hash, meta_json), emb_vec in zip(
        new_chunks, embeddings
    ):
        cursor = conn.execute(
            """INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, metadata)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (chunk_hash, chunk_text, source_type, source_uri, idx, meta_json),
        )
        chunk_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO chunks_vec (rowid, embedding, chunk_id) VALUES (?, ?, ?)",
            (chunk_id, _serialize_f32(emb_vec), chunk_id),
        )

    conn.commit()
    return {
        "file": str(path),
        "chunks_added": len(new_chunks),
        "chunks_skipped": skipped,
    }


def reingest_file(
    conn: sqlite3.Connection,
    path: Path,
    source_type: str | None = None,
) -> dict:
    """Delete all chunks for a source_uri, then re-ingest the file.

    Cleans up FK references in papers, relationships, and conclusions
    before deleting chunks.
    """
    path = path.resolve()
    source_uri = str(path)

    # Check that this source_uri was previously ingested
    existing = conn.execute(
        "SELECT id FROM chunks WHERE source_uri = ?", (source_uri,)
    ).fetchall()
    if not existing:
        return {"error": f"No chunks found for source_uri: {source_uri}"}

    old_ids = [r["id"] for r in existing]
    old_id_set = set(old_ids)

    # --- FK cleanup (batched to stay under SQLITE_MAX_VARIABLE_NUMBER) ---
    # 1. papers.abstract_chunk_id → SET NULL (track affected papers for re-linking)
    affected_paper_ids = [
        r["id"]
        for r in _batched_select(
            conn, "SELECT id FROM papers WHERE abstract_chunk_id IN ({ph})", old_ids
        )
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

    # 3. conclusions.source_chunk_ids — JSON array, remove deleted IDs
    rows = conn.execute("SELECT id, source_chunk_ids FROM conclusions").fetchall()
    for row in rows:
        chunk_ids = json.loads(row["source_chunk_ids"])
        filtered = [cid for cid in chunk_ids if cid not in old_id_set]
        if len(filtered) != len(chunk_ids):
            conn.execute(
                "UPDATE conclusions SET source_chunk_ids = ? WHERE id = ?",
                (json.dumps(filtered), row["id"]),
            )

    # 4. methods/datasets/metrics.chunk_id → SET NULL (track for re-linking)
    affected_entities: dict[str, list[dict]] = {}
    for table in ("methods", "datasets", "metrics"):
        affected_entities[table] = [
            {"id": r["id"], "name": r["name"], "old_chunk_id": r["chunk_id"]}
            for r in _batched_select(
                conn,
                f"SELECT id, name, chunk_id FROM {table} WHERE chunk_id IN ({{ph}})",
                old_ids,
            )
        ]
        _batched_execute(
            conn,
            f"UPDATE {table} SET chunk_id = NULL WHERE chunk_id IN ({{ph}})",
            old_ids,
        )

    # 5. entity_mentions.chunk_id — NOT NULL FK, must delete (re-created by extraction)
    _batched_execute(
        conn,
        "DELETE FROM entity_mentions WHERE chunk_id IN ({ph})",
        old_ids,
    )

    # --- Delete old chunks (triggers handle FTS cleanup) ---
    # Delete from vec table first (no trigger)
    _batched_execute(conn, "DELETE FROM chunks_vec WHERE chunk_id IN ({ph})", old_ids)
    # Delete from chunks (triggers clean up FTS)
    conn.execute(
        "DELETE FROM chunks WHERE source_uri = ?",
        (source_uri,),
    )

    # --- Re-ingest ---
    if source_type is None:
        source_type = _detect_source_type(path)

    if source_type == "pdf":
        text = _extract_pdf_text(path)
    else:
        text = _extract_markdown_text(path)

    # Try AST-aware chunking for Python files
    ast_chunks = None
    if source_type == "code" and path.suffix.lower() == ".py":
        ast_chunks = _chunk_python_ast(text)

    if ast_chunks:
        insert_items = []
        for i, ac in enumerate(ast_chunks):
            meta = json.dumps(
                {
                    "name": ac["name"],
                    "type": ac["type"],
                    "start_line": ac["start_line"],
                    "end_line": ac["end_line"],
                }
            )
            insert_items.append((i, ac["text"], _content_hash(ac["text"]), meta))
    else:
        fixed_chunks = _chunk_text(text)
        if not fixed_chunks:
            conn.commit()
            return {
                "file": source_uri,
                "chunks_deleted": len(old_ids),
                "chunks_added": 0,
            }
        insert_items = [
            (i, c, _content_hash(c), "{}") for i, c in enumerate(fixed_chunks)
        ]

    if not insert_items:
        conn.commit()
        return {"file": source_uri, "chunks_deleted": len(old_ids), "chunks_added": 0}

    texts_to_embed = [item[1] for item in insert_items]
    embeddings = _embed_with_config(conn, texts_to_embed)

    for (idx, chunk_text, chunk_hash, meta_json), emb_vec in zip(
        insert_items, embeddings
    ):
        cursor = conn.execute(
            """INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, metadata)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (chunk_hash, chunk_text, source_type, source_uri, idx, meta_json),
        )
        chunk_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO chunks_vec (rowid, embedding, chunk_id) VALUES (?, ?, ?)",
            (chunk_id, _serialize_f32(emb_vec), chunk_id),
        )

    # --- Re-link papers whose abstract_chunk_id was nullified ---
    if affected_paper_ids:
        new_first = conn.execute(
            "SELECT id FROM chunks WHERE source_uri = ? ORDER BY chunk_index LIMIT 1",
            (source_uri,),
        ).fetchone()
        if new_first:
            _batched_execute(
                conn,
                "UPDATE papers SET abstract_chunk_id = ? WHERE id IN ({ph})",
                affected_paper_ids,
                extra_params=[new_first["id"]],
            )

    # --- Re-link methods/datasets/metrics by name search ---
    new_chunks = conn.execute(
        "SELECT id, content FROM chunks WHERE source_uri = ? ORDER BY chunk_index",
        (source_uri,),
    ).fetchall()
    if new_chunks:
        for table, affected in affected_entities.items():
            for entity in affected:
                for nc in new_chunks:
                    if re.search(
                        r"\b" + re.escape(entity["name"]) + r"\b",
                        nc["content"],
                    ):
                        conn.execute(
                            f"UPDATE {table} SET chunk_id = ? WHERE id = ?",
                            (nc["id"], entity["id"]),
                        )
                        break

    conn.commit()
    return {
        "file": source_uri,
        "chunks_deleted": len(old_ids),
        "chunks_added": len(insert_items),
    }


def ingest_url(
    conn: sqlite3.Connection,
    url: str,
) -> dict:
    """Fetch a web page, extract content, and ingest as chunks.

    Uses trafilatura for content extraction (strips boilerplate, extracts main content).
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_URL_SCHEMES:
        return {"error": f"URL scheme must be http or https, got: {parsed.scheme!r}"}
    if not parsed.hostname:
        return {"error": "URL must include a hostname"}

    try:
        response = httpx.get(url, follow_redirects=True, timeout=30.0)
        response.raise_for_status()
    except httpx.HTTPError as e:
        return {"error": f"Failed to fetch {url}: {e}"}

    html = response.text
    text = trafilatura.extract(html, include_links=False, include_tables=True) or ""
    extracted_title = None
    metadata = trafilatura.extract_metadata(html)
    if metadata and metadata.title:
        extracted_title = metadata.title

    if not text.strip():
        return {
            "url": url,
            "chunks_added": 0,
            "chunks_skipped": 0,
            "source_uri": url,
            "source_type": "web",
        }

    chunks = _chunk_text(text)
    if not chunks:
        return {
            "url": url,
            "chunks_added": 0,
            "chunks_skipped": 0,
            "source_uri": url,
            "source_type": "web",
        }

    # Compute content hashes, skip duplicates
    new_chunks = []
    skipped = 0
    meta_json = json.dumps({"title": extracted_title} if extracted_title else {})
    for i, chunk in enumerate(chunks):
        h = _content_hash(chunk)
        existing = conn.execute(
            "SELECT id FROM chunks WHERE content_hash = ?", (h,)
        ).fetchone()
        if existing:
            skipped += 1
            continue
        new_chunks.append((i, chunk, h))

    if not new_chunks:
        return {
            "url": url,
            "chunks_added": 0,
            "chunks_skipped": skipped,
            "source_uri": url,
            "source_type": "web",
        }

    texts_to_embed = [c[1] for c in new_chunks]
    embeddings = _embed_with_config(conn, texts_to_embed)

    for (idx, chunk_text, chunk_hash), emb_vec in zip(new_chunks, embeddings):
        cursor = conn.execute(
            """INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, metadata)
               VALUES (?, ?, 'web', ?, ?, ?)""",
            (chunk_hash, chunk_text, url, idx, meta_json),
        )
        chunk_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO chunks_vec (rowid, embedding, chunk_id) VALUES (?, ?, ?)",
            (chunk_id, _serialize_f32(emb_vec), chunk_id),
        )

    conn.commit()
    return {
        "url": url,
        "chunks_added": len(new_chunks),
        "chunks_skipped": skipped,
        "source_uri": url,
        "source_type": "web",
        "title": extracted_title,
    }


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
