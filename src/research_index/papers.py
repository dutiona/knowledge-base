"""Paper registration, retrieval, relationships, and BibTeX export."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path


def register_paper(
    conn: sqlite3.Connection,
    title: str,
    authors: list[str] | None = None,
    year: int | None = None,
    venue: str | None = None,
    doi: str | None = None,
    bibtex: str | None = None,
    source_uri: str | None = None,
) -> dict:
    """Register a paper. Optionally link to already-ingested chunks via source_uri."""
    authors_json = json.dumps(authors or [])

    # Link abstract to first chunk from this source_uri if available
    abstract_chunk_id = None
    if source_uri:
        row = conn.execute(
            "SELECT id FROM chunks WHERE source_uri = ? ORDER BY chunk_index LIMIT 1",
            (source_uri,),
        ).fetchone()
        if row:
            abstract_chunk_id = row["id"]

    cursor = conn.execute(
        """INSERT INTO papers (title, authors, year, venue, doi, bibtex, abstract_chunk_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (title, authors_json, year, venue, doi, bibtex, abstract_chunk_id),
    )
    conn.commit()
    return {"paper_id": cursor.lastrowid, "abstract_chunk_id": abstract_chunk_id}


def get_paper(
    conn: sqlite3.Connection,
    paper_id: int | None = None,
    title_pattern: str | None = None,
    doi: str | None = None,
) -> list[dict]:
    """Retrieve papers by ID, title substring, or DOI."""
    if paper_id is not None:
        rows = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchall()
    elif doi is not None:
        rows = conn.execute("SELECT * FROM papers WHERE doi = ?", (doi,)).fetchall()
    elif title_pattern is not None:
        rows = conn.execute(
            "SELECT * FROM papers WHERE title LIKE ?", (f"%{title_pattern}%",)
        ).fetchall()
    else:
        return []

    results = []
    for row in rows:
        paper = {
            "id": row["id"],
            "title": row["title"],
            "authors": json.loads(row["authors"]),
            "year": row["year"],
            "venue": row["venue"],
            "doi": row["doi"],
            "bibtex": row["bibtex"],
            "abstract_chunk_id": row["abstract_chunk_id"],
            "added_at": row["added_at"],
        }

        # Fetch related chunks
        chunks = (
            conn.execute(
                """SELECT id, content, chunk_index FROM chunks
               WHERE source_uri IN (
                   SELECT source_uri FROM chunks WHERE id = ?
               ) ORDER BY chunk_index""",
                (row["abstract_chunk_id"],),
            ).fetchall()
            if row["abstract_chunk_id"]
            else []
        )
        paper["chunks"] = [
            {"id": c["id"], "content": c["content"], "chunk_index": c["chunk_index"]}
            for c in chunks
        ]

        # Fetch relationships
        rels = conn.execute(
            """SELECT r.*, p.title as related_title
               FROM relationships r
               JOIN papers p ON (
                   CASE WHEN r.source_paper_id = ? THEN r.target_paper_id
                        ELSE r.source_paper_id END
               ) = p.id
               WHERE r.source_paper_id = ? OR r.target_paper_id = ?""",
            (row["id"], row["id"], row["id"]),
        ).fetchall()
        paper["relationships"] = [
            {
                "id": r["id"],
                "source_paper_id": r["source_paper_id"],
                "target_paper_id": r["target_paper_id"],
                "relation_type": r["relation_type"],
                "confidence": r["confidence"],
                "direction": "outgoing"
                if r["source_paper_id"] == row["id"]
                else "incoming",
                "related_title": r["related_title"],
            }
            for r in rels
        ]

        results.append(paper)
    return results


def add_relationship(
    conn: sqlite3.Connection,
    source_paper_id: int,
    target_paper_id: int,
    relation_type: str,
    confidence: float = 1.0,
    evidence_chunk_id: int | None = None,
) -> dict:
    """Add a typed relationship between two papers. Upserts on conflict."""
    valid_types = {
        "extends",
        "contradicts",
        "replicates",
        "cites",
        "compares",
        "applies",
        "implements",
    }
    if relation_type not in valid_types:
        return {"error": f"Invalid relation_type. Must be one of: {valid_types}"}

    if not 0.0 <= confidence <= 1.0:
        return {"error": "confidence must be between 0.0 and 1.0"}

    conn.execute(
        """INSERT INTO relationships (source_paper_id, target_paper_id, relation_type, confidence, evidence_chunk_id)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(source_paper_id, target_paper_id, relation_type)
           DO UPDATE SET confidence = excluded.confidence, evidence_chunk_id = excluded.evidence_chunk_id""",
        (
            source_paper_id,
            target_paper_id,
            relation_type,
            confidence,
            evidence_chunk_id,
        ),
    )
    conn.commit()
    return {
        "source_paper_id": source_paper_id,
        "target_paper_id": target_paper_id,
        "relation_type": relation_type,
        "confidence": confidence,
    }


def get_relationships(
    conn: sqlite3.Connection,
    paper_id: int,
    relation_type: str | None = None,
    direction: str = "both",
) -> list[dict]:
    """Get relationships for a paper. direction: 'outgoing', 'incoming', or 'both'."""
    if direction not in ("outgoing", "incoming", "both"):
        return []

    conditions = []
    params: list = []

    if direction in ("outgoing", "both"):
        conditions.append("r.source_paper_id = ?")
        params.append(paper_id)
    if direction in ("incoming", "both"):
        conditions.append("r.target_paper_id = ?")
        params.append(paper_id)

    where = " OR ".join(conditions)
    if relation_type:
        where = f"({where}) AND r.relation_type = ?"
        params.append(relation_type)

    rows = conn.execute(
        f"""SELECT r.*, sp.title as source_title, tp.title as target_title
            FROM relationships r
            JOIN papers sp ON r.source_paper_id = sp.id
            JOIN papers tp ON r.target_paper_id = tp.id
            WHERE {where}""",
        params,
    ).fetchall()

    results = []
    for r in rows:
        entry = {
            "id": r["id"],
            "source_paper_id": r["source_paper_id"],
            "source_title": r["source_title"],
            "target_paper_id": r["target_paper_id"],
            "target_title": r["target_title"],
            "relation_type": r["relation_type"],
            "confidence": r["confidence"],
            "evidence_chunk_id": r["evidence_chunk_id"],
        }
        if r["evidence_chunk_id"]:
            chunk = conn.execute(
                "SELECT content FROM chunks WHERE id = ?", (r["evidence_chunk_id"],)
            ).fetchone()
            entry["evidence_content"] = chunk["content"] if chunk else None
        results.append(entry)
    return results


def _bibtex_key(authors: list[str], year: int | None) -> str:
    """Generate a BibTeX key from first author surname + year."""
    if authors:
        name = authors[0]
        # Handle "Last, First" format
        if "," in name:
            surname = name.split(",")[0].strip()
        else:
            surname = name.split()[-1]
        surname = re.sub(r"[^a-zA-Z]", "", surname).lower()
    else:
        surname = "unknown"
    return f"{surname}{year or 'nd'}"


def _unique_bibtex_key(
    authors: list[str], year: int | None, used_keys: set[str]
) -> str:
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
    # Fallback: use numeric suffix
    i = 2
    while f"{base}{i}" in used_keys:
        i += 1
    candidate = f"{base}{i}"
    used_keys.add(candidate)
    return candidate


def _generate_bibtex(paper: dict, used_keys: set[str] | None = None) -> str:
    """Generate a BibTeX entry from paper metadata."""
    if used_keys is None:
        used_keys = set()
    key = _unique_bibtex_key(paper["authors"], paper["year"], used_keys)
    lines = [f"@article{{{key},"]
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


def _query_papers(
    conn: sqlite3.Connection,
    paper_ids: list[int] | None = None,
    title_pattern: str | None = None,
) -> list:
    """Query papers with optional filters. Shared by export and sync."""
    if paper_ids:
        placeholders = ",".join("?" * len(paper_ids))
        return conn.execute(
            f"SELECT * FROM papers WHERE id IN ({placeholders})", paper_ids
        ).fetchall()
    elif title_pattern:
        return conn.execute(
            "SELECT * FROM papers WHERE title LIKE ?", (f"%{title_pattern}%",)
        ).fetchall()
    else:
        return conn.execute("SELECT * FROM papers").fetchall()


def export_bibtex(
    conn: sqlite3.Connection,
    paper_ids: list[int] | None = None,
    title_pattern: str | None = None,
) -> str:
    """Export papers as BibTeX. Filter by IDs or title pattern, or export all."""
    rows = _query_papers(conn, paper_ids, title_pattern)

    used_keys: set[str] = set()
    entries = []
    for row in rows:
        if row["bibtex"]:
            entries.append(row["bibtex"])
            used_keys.update(_extract_bibtex_keys(row["bibtex"]))
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


def _extract_bibtex_keys(text: str) -> set[str]:
    """Extract all BibTeX keys from a .bib file's content."""
    return set(re.findall(r"@\w+\s*\{\s*([^,\s]+)", text))


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
    # Track all keys (file + newly generated) for collision avoidance
    all_keys = set(file_keys)

    # Get all candidate entries
    rows = _query_papers(conn, paper_ids, title_pattern)
    new_entries = []
    skipped = 0
    for row in rows:
        if row["bibtex"]:
            entry_keys = _extract_bibtex_keys(row["bibtex"])
            if entry_keys & file_keys:
                skipped += 1
                continue
            all_keys.update(entry_keys)
            new_entries.append(row["bibtex"])
        else:
            # Skip if base key was already in the original file
            authors = json.loads(row["authors"])
            base_key = _bibtex_key(authors, row["year"])
            if base_key in file_keys:
                skipped += 1
                continue
            paper = {
                "title": row["title"],
                "authors": authors,
                "year": row["year"],
                "venue": row["venue"],
                "doi": row["doi"],
            }
            # Generate with collision-safe keys across all keys
            entry = _generate_bibtex(paper, all_keys)
            new_entries.append(entry)

    if new_entries:
        separator = "\n\n" if existing_text.rstrip() else ""
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(separator + "\n\n".join(new_entries) + "\n")

    return {"appended": len(new_entries), "skipped": skipped, "path": str(p)}


def suggest_relationships(
    conn: sqlite3.Connection,
    paper_id: int,
) -> list[dict]:
    """Suggest relationships by matching DOIs and titles from paper chunks."""
    paper = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if not paper:
        return []

    # Get all chunks for this paper
    chunks = []
    if paper["abstract_chunk_id"]:
        source_uri_row = conn.execute(
            "SELECT source_uri FROM chunks WHERE id = ?", (paper["abstract_chunk_id"],)
        ).fetchone()
        if source_uri_row:
            chunks = conn.execute(
                "SELECT content FROM chunks WHERE source_uri = ?",
                (source_uri_row["source_uri"],),
            ).fetchall()

    if not chunks:
        return []

    full_text = " ".join(c["content"] for c in chunks)
    suggestions = []

    # Strategy 1: DOI matching
    doi_pattern = r"10\.\d{4,}/[^\s,;}\])\"']+"
    found_dois = set(re.findall(doi_pattern, full_text))
    for doi in found_dois:
        if doi == paper["doi"]:
            continue
        match = conn.execute(
            "SELECT id, title FROM papers WHERE doi = ?", (doi,)
        ).fetchone()
        if match:
            # Check if relationship already exists
            existing = conn.execute(
                """SELECT id FROM relationships
                   WHERE source_paper_id = ? AND target_paper_id = ? AND relation_type = 'cites'""",
                (paper_id, match["id"]),
            ).fetchone()
            if not existing:
                suggestions.append(
                    {
                        "target_paper_id": match["id"],
                        "target_title": match["title"],
                        "relation_type": "cites",
                        "confidence": 0.9,
                        "match_method": "doi",
                        "matched_doi": doi,
                    }
                )

    # Strategy 2: Title substring matching
    other_papers = conn.execute(
        "SELECT id, title FROM papers WHERE id != ?", (paper_id,)
    ).fetchall()
    for other in other_papers:
        # Skip if already suggested via DOI
        if any(s["target_paper_id"] == other["id"] for s in suggestions):
            continue
        # Check if other paper's title appears in our text
        title_words = other["title"].lower().split()
        if len(title_words) < 3:
            continue
        # Require at least 3 consecutive words to match
        title_lower = other["title"].lower()
        if title_lower in full_text.lower():
            existing = conn.execute(
                """SELECT id FROM relationships
                   WHERE source_paper_id = ? AND target_paper_id = ? AND relation_type = 'cites'""",
                (paper_id, other["id"]),
            ).fetchone()
            if not existing:
                suggestions.append(
                    {
                        "target_paper_id": other["id"],
                        "target_title": other["title"],
                        "relation_type": "cites",
                        "confidence": 0.5,
                        "match_method": "title",
                    }
                )

    return suggestions
