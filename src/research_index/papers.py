"""Paper registration, retrieval, relationships, and BibTeX export."""

from __future__ import annotations

import json
import re
import sqlite3

from .db import RELATIONSHIP_TYPES


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
    valid_types = set(RELATIONSHIP_TYPES)
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


def _generate_bibtex(paper: dict) -> str:
    """Generate a BibTeX entry from paper metadata."""
    key = _bibtex_key(paper["authors"], paper["year"])
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


def export_bibtex(
    conn: sqlite3.Connection,
    paper_ids: list[int] | None = None,
    title_pattern: str | None = None,
) -> str:
    """Export papers as BibTeX. Filter by IDs or title pattern, or export all."""
    if paper_ids:
        placeholders = ",".join("?" * len(paper_ids))
        rows = conn.execute(
            f"SELECT * FROM papers WHERE id IN ({placeholders})", paper_ids
        ).fetchall()
    elif title_pattern:
        rows = conn.execute(
            "SELECT * FROM papers WHERE title LIKE ?", (f"%{title_pattern}%",)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM papers").fetchall()

    entries = []
    for row in rows:
        if row["bibtex"]:
            entries.append(row["bibtex"])
        else:
            paper = {
                "title": row["title"],
                "authors": json.loads(row["authors"]),
                "year": row["year"],
                "venue": row["venue"],
                "doi": row["doi"],
            }
            entries.append(_generate_bibtex(paper))
    return "\n\n".join(entries)


def _extract_author_year_citations(text: str) -> list[tuple[str, int]]:
    """Extract (surname, year) pairs from parenthetical and narrative citations.

    Handles:
      - (Vaswani et al., 2017)
      - (Vaswani, 2017)
      - (Vaswani & Shazeer, 2017)
      - Vaswani et al. (2017)
      - Vaswani (2017)
    """
    patterns = [
        # Parenthetical: (Surname et al., YYYY) or (Surname, YYYY) or (Surname & Other, YYYY)
        r"\(([A-Z][a-z]+)(?:\s+(?:et\s+al\.|&\s+[A-Z][a-z]+))?,\s*((?:19|20)\d{2})\)",
        # Narrative: Surname et al. (YYYY) or Surname (YYYY)
        r"([A-Z][a-z]+)(?:\s+et\s+al\.)?\s+\(((?:19|20)\d{2})\)",
    ]
    results = []
    for pat in patterns:
        for m in re.finditer(pat, text):
            results.append((m.group(1).lower(), int(m.group(2))))
    return results


def _surname_from_author(author: str) -> str:
    """Extract lowercase surname from 'Last, First' or 'First Last' format."""
    if "," in author:
        return author.split(",")[0].strip().lower()
    parts = author.split()
    return parts[-1].lower() if parts else ""


_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "and",
        "or",
        "is",
        "are",
        "was",
        "were",
        "be",
        "by",
        "with",
        "from",
        "as",
        "its",
        "this",
        "that",
        "not",
        "but",
        "no",
        "via",
        "using",
        "based",
    }
)


def suggest_relationships(
    conn: sqlite3.Connection,
    paper_id: int,
) -> dict:
    """Suggest citation relationships by matching DOIs, titles, and author+year.

    Returns dict with 'suggestions' (list of candidate relationships) and
    'unmatched' (list of DOIs found in text that don't match any registered paper).
    """
    empty = {"suggestions": [], "unmatched": []}
    paper = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if not paper:
        return empty

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
        return empty

    full_text = " ".join(c["content"] for c in chunks)
    suggestions = []
    suggested_ids: set[int] = set()
    unmatched = []

    # Prefetch existing cites to avoid N+1 queries
    existing_cites = {
        row["target_paper_id"]
        for row in conn.execute(
            "SELECT target_paper_id FROM relationships WHERE source_paper_id = ? AND relation_type = 'cites'",
            (paper_id,),
        ).fetchall()
    }

    # Strategy 1: DOI matching (highest precision)
    doi_pattern = r"10\.\d{4,}/[^\s,;}\])\"']+"
    found_dois = set(re.findall(doi_pattern, full_text))
    for doi in found_dois:
        if doi == paper["doi"]:
            continue
        match = conn.execute(
            "SELECT id, title FROM papers WHERE doi = ?", (doi,)
        ).fetchone()
        if match:
            if match["id"] not in existing_cites:
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
                suggested_ids.add(match["id"])
        else:
            unmatched.append({"doi": doi})

    # Strategy 2: Title word-ratio matching (skip stopwords and short words)
    other_papers = conn.execute(
        "SELECT id, title, authors, year FROM papers WHERE id != ?", (paper_id,)
    ).fetchall()
    text_lower = full_text.lower()
    for other in other_papers:
        if other["id"] in suggested_ids or other["id"] in existing_cites:
            continue
        title_words = other["title"].split()
        if len(title_words) < 3:
            continue
        # Only count meaningful words (skip stopwords and very short words)
        meaningful = [
            w for w in title_words if w.lower() not in _STOPWORDS and len(w) > 2
        ]
        if len(meaningful) < 2:
            continue
        matched_words = sum(1 for w in meaningful if w.lower() in text_lower)
        match_ratio = matched_words / len(meaningful)

        if match_ratio >= 0.6:
            # Scale confidence: 0.6 ratio → 0.3, 1.0 ratio → 0.6
            confidence = round(0.3 + (match_ratio - 0.6) * 0.75, 2)
            suggestions.append(
                {
                    "target_paper_id": other["id"],
                    "target_title": other["title"],
                    "relation_type": "cites",
                    "confidence": confidence,
                    "match_method": "title_words",
                }
            )
            suggested_ids.add(other["id"])

    # Strategy 3: Author surname + year heuristic
    author_year_refs = _extract_author_year_citations(full_text)
    # Group papers by year for O(citations) instead of O(citations × papers)
    papers_by_year: dict[int, list] = {}
    for other in other_papers:
        if other["year"] is not None:
            papers_by_year.setdefault(other["year"], []).append(other)

    for surname, year in author_year_refs:
        for other in papers_by_year.get(year, []):
            if other["id"] in suggested_ids or other["id"] in existing_cites:
                continue
            authors = json.loads(other["authors"]) if other["authors"] else []
            if not authors:
                continue
            if any(_surname_from_author(a) == surname for a in authors):
                suggestions.append(
                    {
                        "target_paper_id": other["id"],
                        "target_title": other["title"],
                        "relation_type": "cites",
                        "confidence": 0.4,
                        "match_method": "author_year",
                        "matched_author": surname,
                        "matched_year": year,
                    }
                )
                suggested_ids.add(other["id"])

    return {"suggestions": suggestions, "unmatched": unmatched}
