"""Pure chunking functions for text, markdown, and Python source code.

All functions in this module are stateless and have no database or I/O
dependencies.  They accept text and return structured chunk data.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

__all__ = [
    "CHUNK_OVERLAP",
    "CHUNK_SIZE",
    "IMAGE_REF_RE",
    "chunk_by_section",
    "chunk_markdown",
    "chunk_python_ast",
    "chunk_text",
    "heading_level",
    "pages_for_range",
    "sanitize_image_refs",
]

CHUNK_SIZE = 1000  # characters
CHUNK_OVERLAP = 200

# ---------------------------------------------------------------------------
# Compiled regex patterns
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6}) ", re.MULTILINE)
_TABLE_LINE_RE = re.compile(r"^\|.*\|", re.MULTILINE)
IMAGE_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

_SECTION_HEADING_RE = re.compile(r"^(#{1,2}) ", re.MULTILINE)
_SUBSECTION_HEADING_RE = re.compile(r"^(###) ", re.MULTILINE)
# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def heading_level(section: str) -> int | None:
    """Return heading level (1-6) of a section's first line, or None."""
    m = _HEADING_RE.match(section)
    return len(m.group(1)) if m else None


def pages_for_range(start: int, end: int, page_map: dict[int, int]) -> list[int]:
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


def sanitize_image_refs(text: str, image_dir: Path | None = None) -> str:
    """Replace absolute image paths with basenames in ![](…) refs."""

    def _replace(m: re.Match) -> str:
        alt, path_str = m.group(1), m.group(2)
        basename = Path(path_str).name
        if image_dir and not (image_dir / basename).exists():
            return m.group(0)  # keep original if file not found
        return f"![{alt}]({basename})"

    return IMAGE_REF_RE.sub(_replace, text)


# ---------------------------------------------------------------------------
# Fixed-size chunking
# ---------------------------------------------------------------------------


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if size <= 0:
        size = CHUNK_SIZE
    if overlap >= size:
        overlap = 0
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


# ---------------------------------------------------------------------------
# Markdown-aware chunking
# ---------------------------------------------------------------------------


def _split_at_pattern(text: str, pattern: re.Pattern) -> list[tuple[str, int]]:
    """Split text at heading boundaries, returning (section_text, char_offset)."""
    raw = pattern.split(text)
    sections: list[tuple[str, int]] = []
    offset = 0

    if raw:
        preamble = raw[0]
        if preamble.strip():
            sections.append((preamble, 0))
        offset = len(preamble)

        i = 1
        while i < len(raw) - 1:
            hashes = raw[i]
            rest = raw[i + 1]
            section_text = hashes + " " + rest
            sections.append((section_text, offset))
            offset += len(section_text)
            i += 2

    return sections


def _paragraph_split(
    text: str,
    max_size: int,
    base_offset: int,
    page_map: dict[int, int],
    image_dir: Path | None,
) -> list[tuple[str, list[int]]]:
    """Split text at paragraph boundaries, respecting atomic tables."""
    paragraphs = text.split("\n\n")
    result: list[tuple[str, list[int]]] = []
    buf = ""
    buf_offset = base_offset
    # Track cursor through the original text for accurate page provenance
    cursor = 0

    def _flush() -> None:
        nonlocal buf, buf_offset
        stripped = buf.strip()
        if stripped:
            sanitized = sanitize_image_refs(stripped, image_dir)
            pages = pages_for_range(buf_offset, buf_offset + len(buf), page_map)
            result.append((sanitized, pages))
        buf = ""

    for i, para in enumerate(paragraphs):
        # Advance cursor past the "\n\n" separator (except for the first paragraph)
        para_offset = base_offset + cursor
        if not para.strip():
            cursor += len(para) + 2  # +2 for "\n\n"
            continue
        candidate = (buf + "\n\n" + para) if buf else para
        if buf and len(candidate) > max_size:
            _flush()
            buf_offset = para_offset
            buf = para
        else:
            if not buf:
                buf_offset = para_offset
            buf = candidate
        cursor += len(para) + (2 if i < len(paragraphs) - 1 else 0)

    _flush()
    return result


def _split_prose_table_segments(body_lines: list[str]) -> list[tuple[str, str]]:
    """Walk lines in order, grouping them into prose/table segments.

    Returns a list of ``(segment_type, segment_text)`` tuples where
    ``segment_type`` is ``"prose"`` or ``"table"``, preserving document order.
    """
    segments: list[tuple[str, str]] = []
    prose_buf: list[str] = []
    table_buf: list[str] = []
    in_table = False

    def flush_prose() -> None:
        if prose_buf:
            segments.append(("prose", "\n".join(prose_buf)))
            prose_buf.clear()

    def flush_table() -> None:
        if table_buf:
            segments.append(("table", "\n".join(table_buf)))
            table_buf.clear()

    for line in body_lines:
        if _TABLE_LINE_RE.match(line):
            if not in_table:
                flush_prose()
                in_table = True
            table_buf.append(line)
        else:
            if in_table:
                flush_table()
                in_table = False
            prose_buf.append(line)
    flush_prose()
    flush_table()
    return segments


def chunk_markdown(
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

    # Use finditer to get exact heading positions — no offset arithmetic needed
    heading_matches = list(_HEADING_RE.finditer(text))
    sections: list[tuple[str, int]] = []  # (section_text, char_offset)

    if heading_matches:
        # Text before the first heading (preamble)
        preamble = text[: heading_matches[0].start()]
        if preamble.strip():
            sections.append((preamble, 0))

        # Each heading runs from its match.start() to the next heading's start()
        for i, m in enumerate(heading_matches):
            end = heading_matches[i + 1].start() if i + 1 < len(heading_matches) else len(text)
            sections.append((text[m.start() : end], m.start()))
    else:
        # No headings — treat entire text as one section
        if text.strip():
            sections.append((text, 0))

    if not sections:
        # No headings at all — fall back to chunk_text
        chunks = chunk_text(text, max_chunk_size, overlap)
        pm = page_map or {}
        return [(sanitize_image_refs(c, image_dir), pages_for_range(0, len(text), pm)) for c in chunks]

    # Process sections: handle tables, oversized, and merging
    result: list[tuple[str, list[int]]] = []
    merge_buffer = ""
    merge_offset = 0
    merge_level: int | None = None
    pm = page_map or {}

    def _flush_buffer() -> None:
        nonlocal merge_buffer, merge_offset, merge_level
        if merge_buffer.strip():
            sanitized = sanitize_image_refs(merge_buffer.strip(), image_dir)
            pages = pages_for_range(merge_offset, merge_offset + len(merge_buffer), pm)
            result.append((sanitized, pages))
        merge_buffer = ""
        merge_level = None

    for section_text, sec_offset in sections:
        level = heading_level(section_text)

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
                sanitized = sanitize_image_refs(section_text.strip(), image_dir)
                pages = pages_for_range(sec_offset, sec_offset + len(section_text), pm)
                result.append((sanitized, pages))
            continue

        # Oversized section — split carefully, preserving document order
        lines = section_text.split("\n")
        heading_line = lines[0] if level is not None else ""
        body_lines = lines[1:] if heading_line else lines
        sec_pages = pages_for_range(sec_offset, sec_offset + len(section_text), pm)

        # Walk lines in order, alternating between prose and table segments
        segments = _split_prose_table_segments(body_lines)

        heading_emitted = False
        for seg_type, seg_text in segments:
            if seg_type == "table":
                table_text = f"{heading_line}\n{seg_text}" if heading_line and not heading_emitted else seg_text
                heading_emitted = True
                sanitized = sanitize_image_refs(table_text.strip(), image_dir)
                result.append((sanitized, sec_pages))
            else:
                if not seg_text.strip():
                    continue
                sub_chunks = chunk_text(seg_text, max_chunk_size, overlap)
                for i_sc, sc in enumerate(sub_chunks):
                    if i_sc == 0 and heading_line and not heading_emitted:
                        sc = f"{heading_line}\n{sc}"
                        heading_emitted = True
                    sanitized = sanitize_image_refs(sc.strip(), image_dir)
                    result.append((sanitized, sec_pages))

    # Flush remaining buffer
    _flush_buffer()

    return result


def chunk_by_section(
    text: str,
    max_section_size: int = 8000,
    page_map: dict[int, int] | None = None,
    image_dir: Path | None = None,
) -> list[tuple[str, list[int]]]:
    """Split markdown into section-level chunks for 32K-context embedding models.

    Primary split at H1/H2 headings. Oversized sections split at H3, then
    paragraph boundaries. No overlap between chunks.

    Returns list of (chunk_text, page_numbers) matching chunk_markdown signature.
    """
    if not text.strip():
        return []

    pm = page_map or {}

    # Split at H1/H2 boundaries
    sections = _split_at_pattern(text, _SECTION_HEADING_RE)

    if not sections:
        # No headings — single chunk or paragraph split if oversized
        stripped = text.strip()
        if len(stripped) <= max_section_size:
            sanitized = sanitize_image_refs(stripped, image_dir)
            pages = pages_for_range(0, len(text), pm)
            return [(sanitized, pages)]
        return _paragraph_split(text, max_section_size, 0, pm, image_dir)

    result: list[tuple[str, list[int]]] = []

    for section_text, sec_offset in sections:
        stripped = section_text.strip()
        if not stripped:
            continue

        # Skip sections that are heading-only (no body text)
        lines = stripped.split("\n", 1)
        body = lines[1].strip() if len(lines) > 1 else ""
        if _SECTION_HEADING_RE.match(stripped) and not body:
            continue

        # Small enough — emit as-is
        if len(stripped) <= max_section_size:
            sanitized = sanitize_image_refs(stripped, image_dir)
            pages = pages_for_range(sec_offset, sec_offset + len(section_text), pm)
            result.append((sanitized, pages))
            continue

        # Oversized — try splitting at H3 boundaries
        subsections = _split_at_pattern(section_text, _SUBSECTION_HEADING_RE)

        if len(subsections) <= 1:
            # No H3 sub-headings — paragraph fallback
            result.extend(_paragraph_split(section_text, max_section_size, sec_offset, pm, image_dir))
            continue

        # Process H3 sub-sections
        for sub_text, sub_rel_offset in subsections:
            sub_stripped = sub_text.strip()
            if not sub_stripped:
                continue
            # Skip preamble that is just the parent H2 heading with no body
            sub_lines = sub_stripped.split("\n", 1)
            sub_body = sub_lines[1].strip() if len(sub_lines) > 1 else ""
            if _SECTION_HEADING_RE.match(sub_stripped) and not sub_body:
                continue
            sub_offset = sec_offset + sub_rel_offset

            if len(sub_stripped) <= max_section_size:
                sanitized = sanitize_image_refs(sub_stripped, image_dir)
                pages = pages_for_range(sub_offset, sub_offset + len(sub_text), pm)
                result.append((sanitized, pages))
            else:
                # Still too large — paragraph fallback
                result.extend(_paragraph_split(sub_text, max_section_size, sub_offset, pm, image_dir))

    return result


# ---------------------------------------------------------------------------
# AST-aware Python chunking
# ---------------------------------------------------------------------------


def chunk_python_ast(source: str, max_chunk_chars: int = CHUNK_SIZE) -> list[dict]:
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
            sub_texts = chunk_text(chunk["text"], size=max_chunk_chars)
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
