"""Ingest pipeline for PDF, markdown, code, and web files."""

from __future__ import annotations

import ast
import hashlib
import html.parser
import ipaddress
import json
import logging
import re
import shutil
import socket
import sqlite3
import struct
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

import fitz  # pymupdf
import httpx
import trafilatura

from .db import _batched_execute, _batched_select
from .embed_swap import get_embed_config
from .embeddings import embed
from .folder_summaries import update_folder_summary


def _update_folder_summary_safe(conn: sqlite3.Connection, path: Path) -> None:
    """Update folder summary for the parent directory, swallowing errors."""
    try:
        update_folder_summary(conn, str(path.parent))
    except Exception:
        logger.warning(
            "Failed to update folder summary for %s", path.parent, exc_info=True
        )


logger = logging.getLogger(__name__)

CHUNK_SIZE = 1000  # characters
CHUNK_OVERLAP = 200

_ALLOWED_URL_SCHEMES = {"http", "https"}


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
    """Call embed() using the model, dim, and provider from the config table."""
    cfg = get_embed_config(conn)
    return embed(
        texts,
        model=cfg["model"],
        expected_dim=cfg["dim"],
        _provider_name=cfg["provider"],
    )


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


_HEADING_RE = re.compile(r"^(#{1,6}) ", re.MULTILINE)
_TABLE_LINE_RE = re.compile(r"^\|.*\|", re.MULTILINE)
_IMAGE_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _heading_level(section: str) -> int | None:
    """Return heading level (1-6) of a section's first line, or None."""
    m = _HEADING_RE.match(section)
    return len(m.group(1)) if m else None


def _pages_for_range(start: int, end: int, page_map: dict[int, int]) -> list[int]:
    """Look up which pages a char range [start, end) spans."""
    if not page_map:
        return []
    import bisect

    offsets = sorted(page_map.keys())
    pages: list[int] = []
    idx = bisect.bisect_right(offsets, start) - 1
    if idx < 0:
        idx = 0
    for i in range(idx, len(offsets)):
        off = offsets[i]
        if off >= end:
            break
        next_off = offsets[i + 1] if i + 1 < len(offsets) else float("inf")
        if next_off > start:
            pages.append(page_map[off])
    return pages


def _sanitize_image_refs(text: str, image_dir: Path | None = None) -> str:
    """Replace absolute image paths with basenames in ![](…) refs."""

    def _replace(m: re.Match) -> str:
        alt, path_str = m.group(1), m.group(2)
        basename = Path(path_str).name
        if image_dir and not (image_dir / basename).exists():
            return m.group(0)  # keep original if file not found
        return f"![{alt}]({basename})"

    return _IMAGE_REF_RE.sub(_replace, text)


def _chunk_markdown(
    text: str,
    max_chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
    page_map: dict[int, int] | None = None,
    image_dir: Path | None = None,
) -> list[tuple[str, list[int]]]:
    """Split markdown into chunks respecting structure boundaries.

    Returns list of (chunk_text, page_numbers).
    """
    if not text.strip():
        return []

    # Split on heading boundaries, keeping the heading with its section
    raw_sections = _HEADING_RE.split(text)
    # re.split with a group produces: [pre, hashes1, rest1, hashes2, rest2, ...]
    # Reconstruct sections
    sections: list[tuple[str, int]] = []  # (section_text, char_offset)
    offset = 0

    if raw_sections:
        # First element is text before the first heading (preamble)
        preamble = raw_sections[0]
        if preamble.strip():
            sections.append((preamble, 0))
        offset = len(preamble)

        # Pairs: (hashes, rest_of_section)
        i = 1
        while i < len(raw_sections) - 1:
            hashes = raw_sections[i]
            rest = raw_sections[i + 1]
            section_text = hashes + " " + rest
            sections.append((section_text, offset))
            offset += len(section_text)
            i += 2

    if not sections:
        # No headings at all — fall back to _chunk_text
        chunks = _chunk_text(text, max_chunk_size, overlap)
        pm = page_map or {}
        return [
            (_sanitize_image_refs(c, image_dir), _pages_for_range(0, len(text), pm))
            for c in chunks
        ]

    # Process sections: handle tables, oversized, and merging
    result: list[tuple[str, list[int]]] = []
    merge_buffer = ""
    merge_offset = 0
    merge_level: int | None = None
    pm = page_map or {}

    def _flush_buffer() -> None:
        nonlocal merge_buffer, merge_offset, merge_level
        if merge_buffer.strip():
            sanitized = _sanitize_image_refs(merge_buffer.strip(), image_dir)
            pages = _pages_for_range(merge_offset, merge_offset + len(merge_buffer), pm)
            result.append((sanitized, pages))
        merge_buffer = ""
        merge_level = None

    for section_text, sec_offset in sections:
        level = _heading_level(section_text)

        # Check if this section should merge into the buffer
        if (
            merge_buffer
            and len(merge_buffer) + len(section_text) <= max_chunk_size
            and level is not None
            and merge_level is not None
            and level > merge_level  # strictly deeper
        ):
            merge_buffer += section_text
            continue

        # Flush previous buffer if non-empty
        if merge_buffer:
            _flush_buffer()

        # Check if section fits in one chunk
        if len(section_text) <= max_chunk_size:
            # Start new merge buffer if section is tiny
            if len(section_text) < max_chunk_size // 4:
                merge_buffer = section_text
                merge_offset = sec_offset
                merge_level = level
            else:
                sanitized = _sanitize_image_refs(section_text.strip(), image_dir)
                pages = _pages_for_range(sec_offset, sec_offset + len(section_text), pm)
                result.append((sanitized, pages))
            continue

        # Oversized section — split carefully, preserving document order
        lines = section_text.split("\n")
        heading_line = lines[0] if level is not None else ""
        body_lines = lines[1:] if heading_line else lines
        sec_pages = _pages_for_range(sec_offset, sec_offset + len(section_text), pm)

        # Walk lines in order, alternating between prose and table segments
        segments: list[tuple[str, str]] = []  # (type, text)
        prose_buf: list[str] = []
        table_buf: list[str] = []
        in_table = False

        def _flush_prose() -> None:
            if prose_buf:
                segments.append(("prose", "\n".join(prose_buf)))
                prose_buf.clear()

        def _flush_table() -> None:
            if table_buf:
                segments.append(("table", "\n".join(table_buf)))
                table_buf.clear()

        for line in body_lines:
            if _TABLE_LINE_RE.match(line):
                if not in_table:
                    _flush_prose()
                    in_table = True
                table_buf.append(line)
            else:
                if in_table:
                    _flush_table()
                    in_table = False
                prose_buf.append(line)
        _flush_prose()
        _flush_table()

        heading_emitted = False
        for seg_type, seg_text in segments:
            if seg_type == "table":
                table_text = (
                    f"{heading_line}\n{seg_text}"
                    if heading_line and not heading_emitted
                    else seg_text
                )
                heading_emitted = True
                sanitized = _sanitize_image_refs(table_text.strip(), image_dir)
                result.append((sanitized, sec_pages))
            else:
                if not seg_text.strip():
                    continue
                sub_chunks = _chunk_text(seg_text, max_chunk_size, overlap)
                for i_sc, sc in enumerate(sub_chunks):
                    if i_sc == 0 and heading_line and not heading_emitted:
                        sc = f"{heading_line}\n{sc}"
                        heading_emitted = True
                    sanitized = _sanitize_image_refs(sc.strip(), image_dir)
                    result.append((sanitized, sec_pages))

    # Flush remaining buffer
    _flush_buffer()

    return result


def _serialize_f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _extract_pdf_text(path: Path) -> str:
    """Flat text extraction — fallback when pymupdf4llm is unavailable."""
    doc = fitz.open(str(path))
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return "\n\n".join(pages)


def pdf_image_dir(path: Path) -> Path:
    """Content-hash keyed directory for extracted images."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    file_hash = h.hexdigest()[:16]
    sanitized = re.sub(r"[^\w\-.]", "_", path.stem)[:64]
    return (
        Path.home()
        / ".local"
        / "share"
        / "knowledge-base"
        / "figures"
        / f"{sanitized}_{file_hash}"
        / "extracted"
    )


def _extract_pdf_markdown(
    path: Path, image_dir: Path | None = None
) -> tuple[str, dict[int, int]]:
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
        pages = pymupdf4llm.to_markdown(
            str(path),
            write_images=image_dir is not None,
            image_path=str(image_dir) if image_dir else "",
            image_format="png",
            page_chunks=True,
            force_text=True,
        )
        page_map: dict[int, int] = {}
        offset = 0
        texts = []
        for pno, p in enumerate(pages):
            meta = p.get("metadata", {})
            page_num = meta.get("page_number") or meta.get("page") or pno + 1
            page_map[offset] = page_num
            texts.append(p["text"])
            offset += len(p["text"]) + 2  # +2 for "\n\n" separator
        return "\n\n".join(texts), page_map
    except (RuntimeError, OSError) as exc:
        logger.warning("pymupdf4llm failed (%s), falling back to flat extraction", exc)
        return _extract_pdf_text(path), {}


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
    session_id: str | None = None,
    _skip_folder_summary: bool = False,
) -> dict:
    path = path.resolve()
    if source_type is None:
        source_type = _detect_source_type(path)

    if source_type == "pdf":
        image_dir = pdf_image_dir(path)
        if image_dir.exists():
            shutil.rmtree(image_dir)
        text, page_map = _extract_pdf_markdown(path, image_dir=image_dir)
    else:
        text = _extract_markdown_text(path)
        page_map = {}

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
    elif source_type == "pdf":
        # Markdown-aware chunking for PDFs
        md_chunks = _chunk_markdown(
            text,
            page_map=page_map,
            image_dir=image_dir if page_map else None,
        )
        if not md_chunks:
            return {"file": str(path), "chunks_added": 0, "chunks_skipped": 0}
        new_chunks = []
        skipped = 0
        # Build extractor version string
        try:
            import pymupdf4llm

            extractor_tag = f"pymupdf4llm@{pymupdf4llm.__version__}"
        except (ImportError, AttributeError):
            extractor_tag = "pymupdf4llm"
        for i, (chunk_text, chunk_pages) in enumerate(md_chunks):
            h = _content_hash(chunk_text)
            existing = conn.execute(
                "SELECT id FROM chunks WHERE content_hash = ?", (h,)
            ).fetchone()
            if existing:
                skipped += 1
                continue
            # Collect verified image basenames from chunk text
            images = [Path(m.group(2)).name for m in _IMAGE_REF_RE.finditer(chunk_text)]
            meta = {"extractor": extractor_tag, "pages": chunk_pages}
            if images:
                meta["images"] = images
            new_chunks.append((i, chunk_text, h, json.dumps(meta)))
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
        result = {"file": str(path), "chunks_added": 0, "chunks_skipped": skipped}
        if skipped > 0:
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
        return result

    # Embed all new chunks using configured model
    texts_to_embed = [c[1] for c in new_chunks]
    embeddings = _embed_with_config(conn, texts_to_embed)

    # Insert
    source_uri = str(path)
    for (idx, chunk_text, chunk_hash, meta_json), emb_vec in zip(
        new_chunks, embeddings
    ):
        cursor = conn.execute(
            "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, session_id, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                chunk_hash,
                chunk_text,
                source_type,
                source_uri,
                idx,
                session_id,
                meta_json,
            ),
        )
        chunk_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO chunks_vec (rowid, embedding, chunk_id) VALUES (?, ?, ?)",
            (chunk_id, _serialize_f32(emb_vec), chunk_id),
        )

    conn.commit()
    if not _skip_folder_summary:
        _update_folder_summary_safe(conn, path)

    return {
        "file": str(path),
        "chunks_added": len(new_chunks),
        "chunks_skipped": skipped,
    }


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
        image_dir = pdf_image_dir(path)
        if image_dir.exists():
            shutil.rmtree(image_dir)
        text, page_map = _extract_pdf_markdown(path, image_dir=image_dir)
    else:
        text = _extract_markdown_text(path)
        page_map = {}

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
    elif source_type == "pdf":
        md_chunks = _chunk_markdown(
            text,
            page_map=page_map,
            image_dir=image_dir if page_map else None,
        )
        if not md_chunks:
            conn.commit()
            _update_folder_summary_safe(conn, path)
            return {
                "file": source_uri,
                "chunks_deleted": len(old_ids),
                "chunks_added": 0,
            }
        try:
            import pymupdf4llm

            extractor_tag = f"pymupdf4llm@{pymupdf4llm.__version__}"
        except (ImportError, AttributeError):
            extractor_tag = "pymupdf4llm"
        insert_items = []
        for i, (chunk_text, chunk_pages) in enumerate(md_chunks):
            images = [Path(m.group(2)).name for m in _IMAGE_REF_RE.finditer(chunk_text)]
            meta: dict = {"extractor": extractor_tag, "pages": chunk_pages}
            if images:
                meta["images"] = images
            insert_items.append(
                (i, chunk_text, _content_hash(chunk_text), json.dumps(meta))
            )
    else:
        fixed_chunks = _chunk_text(text)
        if not fixed_chunks:
            conn.commit()
            _update_folder_summary_safe(conn, path)
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
        _update_folder_summary_safe(conn, path)
        return {"file": source_uri, "chunks_deleted": len(old_ids), "chunks_added": 0}

    texts_to_embed = [item[1] for item in insert_items]
    embeddings = _embed_with_config(conn, texts_to_embed)

    for (idx, chunk_text, chunk_hash, meta_json), emb_vec in zip(
        insert_items, embeddings
    ):
        cursor = conn.execute(
            "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, session_id, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                chunk_hash,
                chunk_text,
                source_type,
                source_uri,
                idx,
                session_id,
                meta_json,
            ),
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

    # Update paper_paths content_hash if entry exists
    if path.exists():
        from .papers import compute_file_hash

        new_hash = compute_file_hash(path)
        conn.execute(
            "UPDATE paper_paths SET content_hash = ? WHERE path = ?",
            (new_hash, source_uri),
        )

    conn.commit()
    _update_folder_summary_safe(conn, path)

    return {
        "file": source_uri,
        "chunks_deleted": len(old_ids),
        "chunks_added": len(insert_items),
    }


_BROWSER_FALLBACK_MIN_CHARS = 200
"""Below this character count, trafilatura output is likely boilerplate/nav-only."""

_WEB_FIGURE_CHUNK_INDEX_START = 1_000_000
"""Chunk index offset for web figure chunks (avoids collision with text chunks)."""

_WEB_IMAGE_CHUNK_INDEX_START = 2_000_000
"""Chunk index offset for inline web image figures (avoids collision with screenshot figures)."""

_MIN_IMAGE_DIMENSION = 100
"""Minimum width/height in pixels for an image to be considered non-decorative."""

_MAX_IMAGES_PER_PAGE = 10
"""Maximum number of inline images to extract per web page."""

_MAX_IMAGE_DOWNLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
"""Maximum size of a single image download."""

_DECORATIVE_URL_PATTERNS = re.compile(
    r"[/\-_](logo|favicon|avatar|banner|sprite|spacer|badge)[/\-_.]"
    r"|[/\-_]ads?[/\-_]"
    r"|[/\-_](tracking[_\-]?pixel|1x1)[/\-_.]",
    re.IGNORECASE,
)

_DECORATIVE_ALT_PATTERNS = re.compile(
    r"\b(logo|icon|avatar|banner|advertisement|ad|spacer)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# HTML image extraction helpers
# ---------------------------------------------------------------------------


class _ImgTagParser(html.parser.HTMLParser):
    """Extract <img> tag attributes from HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.images: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "img":
            d = {k: v for k, v in attrs if v is not None}
            if "src" in d:
                self.images.append(d)


def _is_private_ip(hostname: str) -> bool:
    """Reject private/loopback/link-local IPs to prevent SSRF.

    Resolves hostnames to IPs to catch DNS rebinding (e.g., 127.0.0.1.nip.io).
    """
    try:
        addr = ipaddress.ip_address(hostname)
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except ValueError:
        pass  # Not an IP literal — fall through to DNS resolution
    # Resolve hostname to IP
    try:
        resolved = socket.gethostbyname(hostname)
        addr = ipaddress.ip_address(resolved)
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except (socket.gaierror, ValueError):
        return True  # Can't resolve → reject


def _validate_image_url(url: str) -> bool:
    """Validate an image URL is safe to fetch (scheme + SSRF check)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.hostname:
        return False
    return not _is_private_ip(parsed.hostname)


def _cleanup_figure_fk_refs(conn: sqlite3.Connection, chunk_ids: list[int]) -> None:
    """Clean FK references to figure chunks before deletion.

    Mirrors the FK cleanup in ``reingest_file`` (papers, relationships,
    conclusions, methods, datasets, metrics, entity_mentions).
    """
    if not chunk_ids:
        return
    id_set = set(chunk_ids)

    _batched_execute(
        conn,
        "UPDATE papers SET abstract_chunk_id = NULL WHERE abstract_chunk_id IN ({ph})",
        chunk_ids,
    )
    _batched_execute(
        conn,
        "UPDATE relationships SET evidence_chunk_id = NULL "
        "WHERE evidence_chunk_id IN ({ph})",
        chunk_ids,
    )

    rows = conn.execute("SELECT id, source_chunk_ids FROM conclusions").fetchall()
    for row in rows:
        cids = json.loads(row["source_chunk_ids"])
        filtered = [cid for cid in cids if cid not in id_set]
        if len(filtered) != len(cids):
            conn.execute(
                "UPDATE conclusions SET source_chunk_ids = ? WHERE id = ?",
                (json.dumps(filtered), row["id"]),
            )

    for table in ("methods", "datasets", "metrics"):
        _batched_execute(
            conn,
            f"UPDATE {table} SET chunk_id = NULL WHERE chunk_id IN ({{ph}})",
            chunk_ids,
        )
    _batched_execute(
        conn,
        "DELETE FROM entity_mentions WHERE chunk_id IN ({ph})",
        chunk_ids,
    )


def _extract_html_images(
    conn: sqlite3.Connection,
    html_content: str,
    source_url: str,
    base_url: str | None = None,
) -> int:
    """Extract inline ``<img>`` tags from HTML, describe via vision, store as figure chunks.

    *source_url* is used as ``source_uri`` for storage and stale cleanup (must
    match the key used for text chunks — typically the original requested URL).

    *base_url* is used for resolving relative ``<img src>`` attributes via
    ``urljoin``.  Defaults to *source_url* when not provided.  Pass
    ``str(response.url)`` when the page redirected so relative paths resolve
    against the final location.

    Returns the number of figure chunks added.  Returns 0 if vision is not
    configured or no qualifying images are found.
    """
    if base_url is None:
        base_url = source_url
    import base64
    import io

    from PIL import Image

    from .vision import _get_vision_config, _vision_call

    try:
        vision_config = _get_vision_config(conn)
    except Exception:
        return 0

    vis_base_url = vision_config["base_url"]
    vis_model = vision_config["model"]

    # --- Parse <img> tags ---
    parser = _ImgTagParser()
    try:
        parser.feed(html_content)
    except Exception:
        logger.warning("HTML parsing failed for %s", base_url, exc_info=True)
        return 0

    if not parser.images:
        return 0

    # --- Pre-filter ---
    seen_urls: set[str] = set()
    candidates: list[tuple[str, str]] = []  # (resolved_url, alt_text)

    for img in parser.images:
        src = img["src"]

        # Skip data URIs and SVGs
        if src.startswith("data:"):
            continue
        if src.lower().endswith((".svg", ".svgz")):
            continue

        resolved = urljoin(base_url, src)

        if not _validate_image_url(resolved):
            continue

        # Decorative URL patterns
        if _DECORATIVE_URL_PATTERNS.search(resolved):
            continue

        alt = img.get("alt", "")
        if alt and _DECORATIVE_ALT_PATTERNS.search(alt):
            continue

        # HTML dimension pre-filter
        w_str = img.get("width", "")
        h_str = img.get("height", "")
        try:
            w = int(w_str) if w_str else None
            h = int(h_str) if h_str else None
        except ValueError:
            w, h = None, None
        if w is not None and h is not None:
            if w < _MIN_IMAGE_DIMENSION or h < _MIN_IMAGE_DIMENSION:
                continue

        # URL dedup
        if resolved in seen_urls:
            continue
        seen_urls.add(resolved)

        candidates.append((resolved, alt))

    if not candidates:
        return 0

    # Cap
    candidates = candidates[:_MAX_IMAGES_PER_PAGE]

    # --- Download, convert, describe ---
    collected: list[tuple[str, dict]] = []  # (description, metadata_dict)

    for image_url, alt_text in candidates:
        try:
            with httpx.stream(
                "GET", image_url, timeout=15.0, follow_redirects=True
            ) as resp:
                resp.raise_for_status()

                # Post-redirect SSRF check
                final_url = str(resp.url)
                if not _validate_image_url(final_url):
                    logger.warning(
                        "SSRF: image redirected to private address %s", final_url
                    )
                    continue

                # Stream with byte counter
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_bytes(chunk_size=65536):
                    total += len(chunk)
                    if total > _MAX_IMAGE_DOWNLOAD_BYTES:
                        logger.warning(
                            "Image too large (>%d bytes): %s",
                            _MAX_IMAGE_DOWNLOAD_BYTES,
                            image_url,
                        )
                        break
                    chunks.append(chunk)
                else:
                    # Loop completed without break — download OK
                    pass

                if total > _MAX_IMAGE_DOWNLOAD_BYTES:
                    continue

                image_bytes = b"".join(chunks)
        except Exception:
            logger.warning("Image download failed: %s", image_url, exc_info=True)
            continue

        # Open with Pillow, check dimensions, convert to PNG
        try:
            img_obj = Image.open(io.BytesIO(image_bytes))
            w, h = img_obj.size
            if w < _MIN_IMAGE_DIMENSION or h < _MIN_IMAGE_DIMENSION:
                continue

            buf = io.BytesIO()
            img_obj.convert("RGB").save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:
            logger.warning("Image decode failed: %s", image_url, exc_info=True)
            continue

        # Vision call
        prompt = (
            "Describe this image from a web page. Identify what it shows — "
            "diagrams, charts, schematics, photographs, or other visual content. "
            "Respond with a JSON list of objects with keys: "
            '"description", "figure_type", "title".'
        )
        try:
            figures = _vision_call(b64, prompt, base_url=vis_base_url, model=vis_model)
        except Exception:
            logger.warning("Vision call failed for image %s", image_url, exc_info=True)
            continue

        for fig in figures:
            desc = fig.get("description", "")
            if not desc:
                continue
            meta = {
                "figure_type": "web_image",
                "image_url": image_url,
                "alt_text": alt_text,
                "original_source_type": "web",
                "source_url": source_url,
                "vision_model": vis_model,
                "title": fig.get("title", ""),
            }
            collected.append((desc, meta))

    if not collected:
        return 0

    # --- Compute embeddings (last fallible step) ---
    texts = [desc for desc, _ in collected]
    embeddings = _embed_with_config(conn, texts)

    # --- Delete stale inline image chunks (only after embeddings succeed) ---
    old_ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM chunks WHERE source_uri = ? "
            "AND source_type = 'figure' AND chunk_index >= ?",
            (source_url, _WEB_IMAGE_CHUNK_INDEX_START),
        ).fetchall()
    ]
    if old_ids:
        _cleanup_figure_fk_refs(conn, old_ids)
        _batched_execute(
            conn, "DELETE FROM chunks_vec WHERE chunk_id IN ({ph})", old_ids
        )
        _batched_execute(conn, "DELETE FROM chunks WHERE id IN ({ph})", old_ids)

    # --- Insert new figure chunks ---
    figures_added = 0
    for idx, ((desc, meta), emb_vec) in enumerate(zip(collected, embeddings)):
        chunk_hash = _content_hash(desc)
        existing = conn.execute(
            "SELECT id FROM chunks WHERE content_hash = ?", (chunk_hash,)
        ).fetchone()
        if existing:
            continue

        meta_json = json.dumps(meta)
        chunk_index = _WEB_IMAGE_CHUNK_INDEX_START + idx
        cursor = conn.execute(
            """INSERT INTO chunks (content_hash, content, source_type, source_uri,
               chunk_index, metadata)
               VALUES (?, ?, 'figure', ?, ?, ?)""",
            (chunk_hash, desc, source_url, chunk_index, meta_json),
        )
        chunk_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO chunks_vec (rowid, embedding, chunk_id) VALUES (?, ?, ?)",
            (chunk_id, _serialize_f32(emb_vec), chunk_id),
        )
        figures_added += 1

    if figures_added or old_ids:
        conn.commit()
    return figures_added


_RENDER_SCRIPT = Path(__file__).parent / "browser" / "render_page.py"


# ---------------------------------------------------------------------------
# Browser rendering configuration
# ---------------------------------------------------------------------------


def _find_venv_python(venv_path: str | Path) -> Path | None:
    """Locate the Python executable in a venv (cross-platform)."""
    venv = Path(venv_path)
    for candidate in (venv / "bin" / "python", venv / "Scripts" / "python.exe"):
        if candidate.is_file():
            return candidate
    return None


def _get_browser_config(conn: sqlite3.Connection) -> dict | None:
    """Read browser rendering configuration from config table.

    Returns a dict with mode/endpoint/venv keys, or None when unconfigured.
    """
    rows = conn.execute(
        "SELECT key, value FROM config "
        "WHERE key IN ('browser_mode', 'browser_venv', 'browser_endpoint')"
    ).fetchall()
    config_map = {row["key"]: row["value"] for row in rows}

    mode = config_map.get("browser_mode")
    venv = config_map.get("browser_venv")
    if not mode or not venv:
        return None

    config: dict = {"mode": mode, "venv": venv}
    if mode == "cdp":
        endpoint = config_map.get("browser_endpoint")
        if endpoint:
            config["endpoint"] = endpoint

    return config


def configure_browser(
    conn: sqlite3.Connection,
    cdp_endpoint: str | None = None,
    venv_path: str | None = None,
) -> dict:
    """Configure browser rendering for JS-heavy web pages.

    Args:
        cdp_endpoint: WebSocket CDP endpoint (ws:// or wss://).
                      Requires venv_path too.
        venv_path: Absolute path to Python venv with playwright installed.
        Pass both as empty string to disable.  Both None to query.
    """
    # Query mode
    if cdp_endpoint is None and venv_path is None:
        cfg = _get_browser_config(conn)
        return {"browser": cfg}

    # Disable mode
    if cdp_endpoint == "" and venv_path == "":
        for key in ("browser_mode", "browser_endpoint", "browser_venv"):
            conn.execute("DELETE FROM config WHERE key = ?", (key,))
        conn.commit()
        return {"browser": None}

    # CDP without venv is an error
    if cdp_endpoint and not venv_path:
        return {
            "error": "venv_path is required (playwright Python client must be installed)"
        }

    # Validate venv
    if venv_path:
        resolved = Path(venv_path).resolve()
        if not resolved.is_absolute():
            return {"error": "venv_path must be an absolute path"}
        venv_python = _find_venv_python(resolved)
        if not venv_python:
            return {"error": f"Python executable not found in venv at {venv_path}"}

    # Determine mode
    if cdp_endpoint:
        from urllib.parse import urlparse

        parsed = urlparse(cdp_endpoint)
        if parsed.scheme not in ("ws", "wss"):
            return {
                "error": f"CDP endpoint must use ws:// or wss://, got {parsed.scheme}://"
            }
        mode = "cdp"
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES ('browser_endpoint', ?)",
            (cdp_endpoint,),
        )
    else:
        mode = "local"
        # Clear any stale CDP endpoint
        conn.execute("DELETE FROM config WHERE key = 'browser_endpoint'")

    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('browser_mode', ?)",
        (mode,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('browser_venv', ?)",
        (venv_path,),
    )
    conn.commit()
    return {"browser": _get_browser_config(conn)}


def _render_with_browser(
    url: str,
    browser_config: dict,
    timeout: int = 60,
) -> dict | None:
    """Render a URL via Playwright subprocess.

    Returns ``{"html": str, "screenshot_path": Path, "tmpdir": Path}``
    on success, or ``None`` on failure.  Caller owns tmpdir cleanup on
    success; tmpdir is cleaned on failure.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="ri-browser-"))
    venv_python_path = _find_venv_python(browser_config["venv"])
    if not venv_python_path:
        logger.warning(
            "Python executable not found in configured venv: %s",
            browser_config["venv"],
        )
        shutil.rmtree(tmpdir, ignore_errors=True)
        return None
    venv_python = str(venv_python_path)

    cmd = [venv_python, str(_RENDER_SCRIPT), url, str(tmpdir)]
    if browser_config.get("mode") == "cdp" and browser_config.get("endpoint"):
        cmd.extend(["--cdp", browser_config["endpoint"]])

    try:
        subprocess.run(
            cmd,
            timeout=timeout,
            capture_output=True,
            check=True,
        )
    except (
        subprocess.TimeoutExpired,
        subprocess.CalledProcessError,
        OSError,
    ) as exc:
        logger.warning("Browser rendering failed for %s: %s", url, exc)
        shutil.rmtree(tmpdir, ignore_errors=True)
        return None

    html_path = tmpdir / "page.html"
    screenshot_path = tmpdir / "screenshot.png"

    if not html_path.exists():
        logger.warning("Browser produced no HTML for %s", url)
        shutil.rmtree(tmpdir, ignore_errors=True)
        return None

    html = html_path.read_text(encoding="utf-8")
    return {
        "html": html,
        "screenshot_path": screenshot_path if screenshot_path.exists() else None,
        "tmpdir": tmpdir,
    }


def _extract_web_figures(
    conn: sqlite3.Connection,
    source_url: str,
    screenshot_path: Path,
) -> int:
    """Extract figures from a browser-rendered web page screenshot.

    Feeds the screenshot through the existing vision pipeline:
    - Vision model describes the page screenshot
    - OmniParser segments into text/icon regions (if configured)
    Stores as figure chunks with ``source_type='figure'`` and metadata
    indicating web origin.  Returns number of figures added.

    Returns 0 if vision is not configured or on any failure.
    """
    import base64

    from .vision import (
        _get_omniparser_config,
        _get_vision_config,
        _merge_omniparser_elements,
        _run_omniparser,
        _vision_call,
    )

    try:
        vision_config = _get_vision_config(conn)
    except Exception:
        return 0  # Vision not configured or misconfigured

    base_url = vision_config["base_url"]
    model = vision_config["model"]

    # Describe the screenshot via the vision model
    png_bytes = screenshot_path.read_bytes()
    b64 = base64.b64encode(png_bytes).decode("ascii")

    prompt = (
        "Describe this web page screenshot. Identify any figures, diagrams, "
        "charts, or schematics visible. Respond with a JSON list of objects "
        'with keys: "description", "figure_type", "title".'
    )

    try:
        figures = _vision_call(b64, prompt, base_url=base_url, model=model)
    except Exception:
        logger.warning(
            "Vision call failed for web screenshot %s", source_url, exc_info=True
        )
        return 0

    if not figures:
        return 0

    # Optional: OmniParser enrichment
    omniparser_path = _get_omniparser_config(conn)
    omni_elements: list[dict] | None = None
    if omniparser_path:
        omni_result = _run_omniparser(screenshot_path, omniparser_path)
        if omni_result and omni_result.get("elements"):
            omni_elements = omni_result["elements"]

    # Embed and store figure chunks
    texts: list[str] = []
    valid_figures: list[dict] = []
    for fig in figures:
        desc = fig.get("description", "")
        if not desc:
            continue
        # Enrich with OmniParser if available and single figure
        if omni_elements and len(figures) == 1:
            enriched = _merge_omniparser_elements(fig, omni_elements)
            desc = enriched.get("description", desc)
        texts.append(desc)
        valid_figures.append(fig)

    if not texts:
        return 0

    embeddings = _embed_with_config(conn, texts)

    # Remove stale screenshot figure chunks only (scope to < _WEB_IMAGE_CHUNK_INDEX_START
    # to avoid deleting inline image figures managed by _extract_html_images)
    old_fig_ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM chunks WHERE source_uri = ? AND source_type = 'figure' "
            "AND chunk_index < ?",
            (source_url, _WEB_IMAGE_CHUNK_INDEX_START),
        ).fetchall()
    ]
    if old_fig_ids:
        _cleanup_figure_fk_refs(conn, old_fig_ids)
        _batched_execute(
            conn, "DELETE FROM chunks_vec WHERE chunk_id IN ({ph})", old_fig_ids
        )
        _batched_execute(conn, "DELETE FROM chunks WHERE id IN ({ph})", old_fig_ids)

    figures_added = 0

    for idx, (fig, desc, emb_vec) in enumerate(zip(valid_figures, texts, embeddings)):
        chunk_hash = _content_hash(desc)
        existing = conn.execute(
            "SELECT id FROM chunks WHERE content_hash = ?", (chunk_hash,)
        ).fetchone()
        if existing:
            continue

        meta_json = json.dumps(
            {
                "figure_type": fig.get("figure_type", "web_screenshot"),
                "title": fig.get("title", ""),
                "original_source_type": "web",
                "source_url": source_url,
                "vision_model": model,
            }
        )

        chunk_index = _WEB_FIGURE_CHUNK_INDEX_START + idx
        cursor = conn.execute(
            """INSERT INTO chunks (content_hash, content, source_type, source_uri,
               chunk_index, metadata)
               VALUES (?, ?, 'figure', ?, ?, ?)""",
            (chunk_hash, desc, source_url, chunk_index, meta_json),
        )
        chunk_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO chunks_vec (rowid, embedding, chunk_id) VALUES (?, ?, ?)",
            (chunk_id, _serialize_f32(emb_vec), chunk_id),
        )
        figures_added += 1

    if figures_added:
        conn.commit()
    return figures_added


def ingest_url(
    conn: sqlite3.Connection,
    url: str,
    session_id: str | None = None,
) -> dict:
    """Fetch a web page, extract content, and ingest as chunks.

    Uses trafilatura for content extraction (strips boilerplate, extracts main content).
    Falls back to browser rendering when trafilatura extracts insufficient content.
    """
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

    browser_rendered = False
    figures_extracted = 0

    # Browser fallback: if trafilatura got insufficient content, try rendering
    if len(text.strip()) < _BROWSER_FALLBACK_MIN_CHARS:
        browser_config = _get_browser_config(conn)
        if browser_config:
            render_result = _render_with_browser(url, browser_config)
            if render_result:
                try:
                    rendered_text = (
                        trafilatura.extract(
                            render_result["html"],
                            include_links=False,
                            include_tables=True,
                        )
                        or ""
                    )
                    meta2 = trafilatura.extract_metadata(render_result["html"])
                    if meta2 and meta2.title and not extracted_title:
                        extracted_title = meta2.title
                    # Only use rendered content if it's actually better
                    if len(rendered_text.strip()) > len(text.strip()):
                        text = rendered_text
                        browser_rendered = True

                    # Extract figures from screenshot (isolated from text ingest)
                    screenshot = render_result.get("screenshot_path")
                    if screenshot and screenshot.exists():
                        try:
                            figures_extracted = _extract_web_figures(
                                conn, url, screenshot
                            )
                        except Exception:
                            logger.warning(
                                "Figure extraction failed for %s",
                                url,
                                exc_info=True,
                            )
                            figures_extracted = 0
                finally:
                    tmpdir = render_result.get("tmpdir")
                    if tmpdir:
                        shutil.rmtree(tmpdir, ignore_errors=True)

    # Extract inline images from HTML (skip when screenshot figures already extracted)
    if figures_extracted == 0:
        try:
            inline_figures = _extract_html_images(
                conn, html, source_url=url, base_url=str(response.url)
            )
            figures_extracted += inline_figures
        except Exception:
            logger.warning("Inline image extraction failed for %s", url, exc_info=True)

    _base_result: dict = {
        "url": url,
        "source_uri": url,
        "source_type": "web",
        "browser_rendered": browser_rendered,
        "figures_extracted": figures_extracted,
    }

    if not text.strip():
        return {**_base_result, "chunks_added": 0, "chunks_skipped": 0}

    chunks = _chunk_text(text)
    if not chunks:
        return {**_base_result, "chunks_added": 0, "chunks_skipped": 0}

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
        return {**_base_result, "chunks_added": 0, "chunks_skipped": skipped}

    texts_to_embed = [c[1] for c in new_chunks]
    embeddings = _embed_with_config(conn, texts_to_embed)

    for (idx, chunk_text, chunk_hash), emb_vec in zip(new_chunks, embeddings):
        cursor = conn.execute(
            "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, session_id, metadata) VALUES (?, ?, 'web', ?, ?, ?, ?)",
            (chunk_hash, chunk_text, url, idx, session_id, meta_json),
        )
        chunk_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO chunks_vec (rowid, embedding, chunk_id) VALUES (?, ?, ?)",
            (chunk_id, _serialize_f32(emb_vec), chunk_id),
        )

    conn.commit()
    return {
        **_base_result,
        "chunks_added": len(new_chunks),
        "chunks_skipped": skipped,
        "title": extracted_title,
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
            result = ingest_file(
                conn, f, session_id=session_id, _skip_folder_summary=True
            )
            results.append(result)
            affected_folders.add(str(f.resolve().parent))

    # Batch-update folder summaries once per affected folder
    for folder in sorted(affected_folders):
        _update_folder_summary_safe(conn, Path(folder))

    return results
