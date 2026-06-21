"""BibTeX key generation, export, and file synchronisation."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .db import _batched_select, escape_like


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------


def _bibtex_key(authors: list[str], year: int | None) -> str:
    """Generate a BibTeX key from first author surname + year."""
    if authors:
        name = authors[0]
        # Handle "Last, First" format
        surname = name.split(",")[0].strip() if "," in name else name.split()[-1]
        surname = re.sub(r"[^a-zA-Z]", "", surname).lower()
    else:
        surname = "unknown"
    return f"{surname}{year or 'nd'}"


def _unique_bibtex_key(authors: list[str], year: int | None, used_keys: set[str]) -> str:
    """Generate a collision-free BibTeX key, appending a/b/c... suffixes."""
    base = _bibtex_key(authors, year)
    if base not in used_keys:
        used_keys.add(base)
        return base
    for suffix in "abcdefghijklmnopqrstuvwxyz":
        candidate = f"{base}{suffix}"
        if candidate not in used_keys:
            used_keys.add(candidate)
            return candidate
    i = 2
    while f"{base}{i}" in used_keys:
        i += 1
    candidate = f"{base}{i}"
    used_keys.add(candidate)
    return candidate


def _extract_bibtex_keys(text: str) -> set[str]:
    """Extract all BibTeX keys from a .bib file's content."""
    return set(re.findall(r"@\w+\s*\{\s*([^,\s]+)", text))


# ---------------------------------------------------------------------------
# Entry generation
# ---------------------------------------------------------------------------


def _generate_bibtex(paper: dict, used_keys: set[str] | None = None, paper_id: int | None = None) -> str:
    """Generate a BibTeX entry from paper metadata."""
    if used_keys is None:
        used_keys = set()
    key = _unique_bibtex_key(paper["authors"], paper["year"], used_keys)
    lines = []
    if paper_id is not None:
        lines.append(f"% knowledge-base-id: {paper_id}")
    lines.append(f"@article{{{key},")
    lines.append(f"  title = {{{paper['title']}}},")
    if paper["authors"]:
        lines.append(f"  author = {{{' and '.join(paper['authors'])}}},")
    if paper["year"]:
        lines.append(f"  year = {{{paper['year']}}},")
    if paper.get("venue"):
        lines.append(f"  journal = {{{paper['venue']}}},")
    if paper.get("doi"):
        lines.append(f"  doi = {{{paper['doi']}}},")
    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Queries (shared by export & sync)
# ---------------------------------------------------------------------------


def _query_papers(
    conn: sqlite3.Connection,
    paper_ids: list[int] | None = None,
    title_pattern: str | None = None,
) -> list:
    """Query papers with optional filters. Shared by export and sync."""
    if paper_ids:
        return _batched_select(conn, "SELECT * FROM papers WHERE id IN ({ph})", paper_ids)
    if title_pattern:
        return conn.execute(
            "SELECT * FROM papers WHERE title LIKE ? ESCAPE '\\'",
            (f"%{escape_like(title_pattern)}%",),
        ).fetchall()
    return conn.execute("SELECT * FROM papers").fetchall()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def export_bibtex(
    conn: sqlite3.Connection,
    paper_ids: list[int] | None = None,
    title_pattern: str | None = None,
) -> str:
    """Export papers as BibTeX. Filter by IDs or title pattern, or export all."""
    rows = _query_papers(conn, paper_ids, title_pattern)

    # Pre-seed used_keys with all stored BibTeX keys to avoid collisions
    used_keys: set[str] = set()
    for row in rows:
        if row["bibtex"]:
            used_keys.update(_extract_bibtex_keys(row["bibtex"]))

    entries = []
    seen_stored_keys: set[str] = set()
    for row in rows:
        if row["bibtex"]:
            entry_keys = _extract_bibtex_keys(row["bibtex"])
            if entry_keys & seen_stored_keys:
                continue
            seen_stored_keys.update(entry_keys)
            entries.append(row["bibtex"])
        else:
            paper = {
                "title": row["title"],
                "authors": json.loads(row["authors"]),
                "year": row["year"],
                "venue": row["venue"],
                "doi": row["doi"],
            }
            entries.append(_generate_bibtex(paper, used_keys))
    return "\n\n".join(entries)


def sync_bibtex(
    conn: sqlite3.Connection,
    output_path: str,
    paper_ids: list[int] | None = None,
    title_pattern: str | None = None,
) -> dict:
    """Append only new papers to an existing .bib file.

    Reads the file at output_path, extracts existing BibTeX keys,
    and appends entries for papers whose keys are not yet present.
    Creates the file if it does not exist.
    """
    p = Path(output_path).expanduser().resolve()
    existing_text = p.read_text(encoding="utf-8") if p.exists() else ""
    file_keys = _extract_bibtex_keys(existing_text)

    rows = _query_papers(conn, paper_ids, title_pattern)

    # Collect stored BibTeX keys
    stored_keys: set[str] = set()
    for row in rows:
        if row["bibtex"]:
            stored_keys.update(_extract_bibtex_keys(row["bibtex"]))
    # all_keys: file + stored — for key generation with full collision awareness
    all_keys = file_keys | stored_keys

    new_entries = []
    accepted_stored_keys: set[str] = set()
    skipped = 0
    for row in rows:
        if row["bibtex"]:
            entry_keys = _extract_bibtex_keys(row["bibtex"])
            if entry_keys & (file_keys | accepted_stored_keys):
                skipped += 1
                continue
            accepted_stored_keys.update(entry_keys)
            new_entries.append(row["bibtex"])
        else:
            # Idempotency: skip if this paper's ID marker is in the file
            # Accept both old (research-index-id) and new (knowledge-base-id) markers
            paper_id = row["id"]
            if (
                f"% knowledge-base-id: {paper_id}" in existing_text
                or f"% research-index-id: {paper_id}" in existing_text
            ):
                skipped += 1
                continue
            paper = {
                "title": row["title"],
                "authors": json.loads(row["authors"]),
                "year": row["year"],
                "venue": row["venue"],
                "doi": row["doi"],
            }
            entry = _generate_bibtex(paper, all_keys, paper_id=paper_id)
            new_entries.append(entry)

    if new_entries:
        separator = "\n\n" if existing_text.rstrip() else ""
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(separator + "\n\n".join(new_entries) + "\n")

    return {"appended": len(new_entries), "skipped": skipped, "path": str(p)}
