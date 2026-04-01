"""Paper registration, retrieval, relationships, and BibTeX export."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

from .db import RELATIONSHIP_TYPES, escape_like, get_vec_table_name


def compute_file_hash(path: Path) -> str:
    """SHA-256 hex digest of file bytes."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8192), b""):
            h.update(block)
    return h.hexdigest()


def get_paper_paths(conn: sqlite3.Connection, paper_id: int) -> list[dict]:
    """Get all registered paths for a paper."""
    rows = conn.execute(
        "SELECT id, path, content_hash, is_primary, added_at "
        "FROM paper_paths WHERE paper_id = ? ORDER BY is_primary DESC, added_at",
        (paper_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_paper_source_uri(conn: sqlite3.Connection, paper_id: int) -> str | None:
    """Get primary source path for a paper via paper_paths.

    Falls back to legacy abstract_chunk_id -> chunks.source_uri
    if no paper_paths entry exists.
    """
    row = conn.execute(
        "SELECT path FROM paper_paths WHERE paper_id = ? AND is_primary = TRUE LIMIT 1",
        (paper_id,),
    ).fetchone()
    if row:
        return row["path"]

    # Legacy fallback
    row = conn.execute(
        "SELECT source_uri FROM chunks WHERE id = "
        "(SELECT abstract_chunk_id FROM papers WHERE id = ?)",
        (paper_id,),
    ).fetchone()
    return row["source_uri"] if row else None


def get_paper_chunks(
    conn: sqlite3.Connection,
    paper_id: int,
    include_figures: bool = False,
) -> list[dict]:
    """Get chunks for a paper via paper_paths.

    Args:
        include_figures: If True, include figure chunks. Default False
            (matches extraction.py usage). get_paper passes True to
            include all chunk types.
    """
    source_uri = get_paper_source_uri(conn, paper_id)
    if not source_uri:
        return []
    query = "SELECT id, content, chunk_index FROM chunks WHERE source_uri = ?"
    if not include_figures:
        query += " AND source_type != 'figure'"
    query += " ORDER BY chunk_index"
    chunks = conn.execute(query, (source_uri,)).fetchall()
    return [
        {"id": c["id"], "content": c["content"], "chunk_index": c["chunk_index"]}
        for c in chunks
    ]


def relocate_paper(
    conn: sqlite3.Connection,
    paper_id: int,
    new_path: str,
    content_hash: str | None = None,
) -> dict:
    """Update a paper's filesystem path after a move/rename.

    Updates both paper_paths and chunks.source_uri atomically.
    Validates: new_path must exist, must not be owned by another paper,
    and content hash must match if previous hash is known.
    """
    new_path = str(Path(new_path).resolve())

    current = conn.execute(
        "SELECT id, path, content_hash FROM paper_paths "
        "WHERE paper_id = ? AND is_primary = TRUE LIMIT 1",
        (paper_id,),
    ).fetchone()
    if not current:
        return {"error": f"No paper_paths entry for paper {paper_id}"}

    old_path = current["path"]

    p = Path(new_path)
    if not p.exists():
        return {"error": f"New path does not exist: {new_path}"}

    conflict = conn.execute(
        "SELECT paper_id FROM paper_paths WHERE path = ? AND paper_id != ?",
        (new_path, paper_id),
    ).fetchone()
    if conflict:
        return {
            "error": f"Path already owned by paper {conflict['paper_id']}: {new_path}"
        }

    if content_hash is None:
        try:
            content_hash = compute_file_hash(p)
        except OSError as e:
            return {"error": f"Cannot read file for hashing: {e}"}

    if current["content_hash"] and content_hash != current["content_hash"]:
        return {
            "error": "Content hash mismatch — file at new_path has different content",
            "expected": current["content_hash"],
            "actual": content_hash,
        }

    try:
        conn.execute(
            "UPDATE paper_paths SET path = ?, content_hash = ? WHERE id = ?",
            (new_path, content_hash, current["id"]),
        )
        conn.execute(
            "UPDATE chunks SET source_uri = ? WHERE source_uri = ?",
            (new_path, old_path),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"paper_id": paper_id, "old_path": old_path, "new_path": new_path}


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

    # Canonicalize source_uri before any lookups
    if source_uri:
        source_uri = str(Path(source_uri).resolve())

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

    # Populate paper_paths for the new paper
    path_conflict = None  # hoisted: used after the if-block
    if source_uri:
        file_hash = None
        p = Path(source_uri)
        if p.exists():
            try:
                file_hash = compute_file_hash(p)
            except OSError:
                pass  # Hash is best-effort; paper is still registered

        # Check if path is already owned by another paper
        existing_path = conn.execute(
            "SELECT paper_id FROM paper_paths WHERE path = ?", (source_uri,)
        ).fetchone()
        if not existing_path:
            conn.execute(
                "INSERT INTO paper_paths (paper_id, path, content_hash, is_primary) "
                "VALUES (?, ?, ?, TRUE)",
                (cursor.lastrowid, source_uri, file_hash),
            )
        else:
            path_conflict = existing_path["paper_id"]

    conn.commit()
    result = {"paper_id": cursor.lastrowid, "abstract_chunk_id": abstract_chunk_id}
    if source_uri and path_conflict is not None:
        result["path_conflict"] = path_conflict
    return result


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
            "SELECT * FROM papers WHERE title LIKE ? ESCAPE '\\'",
            (f"%{escape_like(title_pattern)}%",),
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

        # Fetch related chunks via paper_paths (with legacy fallback)
        paper["chunks"] = get_paper_chunks(conn, row["id"], include_figures=True)

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


def _get_paper_embeddings(
    conn: sqlite3.Connection, paper_id: int
) -> list[tuple[int, bytes]]:
    """Return [(chunk_id, embedding_blob), ...] for a paper's non-figure chunks.

    Primary path: join through paper_paths to find chunks by source_uri.
    Fallback: if no paper_paths row exists (e.g. duplicate source_uri conflict),
    resolve source_uri via papers.abstract_chunk_id → chunks.source_uri.
    """
    vec_table = get_vec_table_name(conn)
    rows = conn.execute(
        f"""SELECT cv.chunk_id, cv.embedding
           FROM [{vec_table}] cv
           JOIN chunks c ON c.id = cv.chunk_id
           JOIN paper_paths pp ON pp.path = c.source_uri
           WHERE pp.paper_id = ?
             AND c.source_type != 'figure'""",
        (paper_id,),
    ).fetchall()
    if rows:
        return rows

    # Fallback: resolve source_uri via abstract_chunk_id
    uri_row = conn.execute(
        "SELECT source_uri FROM chunks WHERE id = "
        "(SELECT abstract_chunk_id FROM papers WHERE id = ?)",
        (paper_id,),
    ).fetchone()
    if not uri_row:
        return []
    return conn.execute(
        f"""SELECT cv.chunk_id, cv.embedding
           FROM [{vec_table}] cv
           JOIN chunks c ON c.id = cv.chunk_id
           WHERE c.source_uri = ?
             AND c.source_type != 'figure'""",
        (uri_row["source_uri"],),
    ).fetchall()


def auto_relate(
    conn: sqlite3.Connection,
    paper_id: int,
    on_progress: object = None,
    *,
    only_compare_higher: bool = False,
) -> dict:
    """Discover 'similar' relationships by comparing chunk embeddings."""
    import heapq
    import numpy as np

    _TOP_K = 3

    # Read thresholds from config
    propose_row = conn.execute(
        "SELECT value FROM config WHERE key = 'auto_relate_propose_threshold'"
    ).fetchone()
    propose_threshold = float(propose_row["value"]) if propose_row else 0.82

    # Fetch source paper embeddings
    source_rows = _get_paper_embeddings(conn, paper_id)
    if not source_rows:
        return {"skipped": "no embeddings", "relationships_created": 0}

    source_chunk_ids = {row["chunk_id"] for row in source_rows}
    source_vecs = [
        (row["chunk_id"], np.frombuffer(bytes(row["embedding"]), dtype=np.float32))
        for row in source_rows
    ]
    # Pre-normalize source vectors
    source_normed = []
    for cid, vec in source_vecs:
        norm = np.linalg.norm(vec)
        if norm > 0:
            source_normed.append((cid, vec / norm))

    if not source_normed:
        return {"skipped": "no valid embeddings", "relationships_created": 0}

    # Fetch candidate paper IDs.  When only_compare_higher is set (full-scan
    # mode), restrict to id > paper_id so each pair is compared exactly once.
    query = (
        "SELECT id FROM papers WHERE id > ?"
        if only_compare_higher
        else "SELECT id FROM papers WHERE id != ?"
    )
    other_papers = conn.execute(query, (paper_id,)).fetchall()

    if not other_papers:
        return {"skipped": "no other papers", "relationships_created": 0}

    created = 0
    compared = 0
    skipped = 0

    for other_row in other_papers:
        other_id = other_row["id"]

        # Direction normalization: always source < target for "similar"
        lo, hi = min(paper_id, other_id), max(paper_id, other_id)

        # Fetch other paper embeddings
        other_rows = _get_paper_embeddings(conn, other_id)
        if not other_rows:
            skipped += 1
            continue

        # Skip papers that share chunks (e.g. duplicate source_uri registrations)
        other_chunk_ids = {row["chunk_id"] for row in other_rows}
        if source_chunk_ids & other_chunk_ids:
            skipped += 1
            continue

        other_vecs = []
        for row in other_rows:
            vec = np.frombuffer(bytes(row["embedding"]), dtype=np.float32)
            norm = np.linalg.norm(vec)
            if norm > 0:
                other_vecs.append((row["chunk_id"], vec / norm))

        if not other_vecs:
            skipped += 1
            continue

        compared += 1

        # Stream top-k via bounded heap — O(_TOP_K) memory, not O(n×m)
        top_k: list[tuple[float, int, int]] = []
        for s_cid, s_vec in source_normed:
            for o_cid, o_vec in other_vecs:
                sim = float(np.dot(s_vec, o_vec))
                if len(top_k) < _TOP_K:
                    heapq.heappush(top_k, (sim, s_cid, o_cid))
                elif sim > top_k[0][0]:
                    heapq.heapreplace(top_k, (sim, s_cid, o_cid))

        avg_score = sum(s for s, _, _ in top_k) / len(top_k)

        if avg_score < propose_threshold:
            # Below threshold: delete any stale "similar" edge from a
            # previous run with a lower threshold
            conn.execute(
                "DELETE FROM relationships WHERE relation_type = 'similar' "
                "AND source_paper_id = ? AND target_paper_id = ?",
                (lo, hi),
            )
            continue

        # Best-matching chunk for evidence (max of the heap)
        best_sim, best_s_cid, best_o_cid = max(top_k)
        evidence_chunk_id = best_s_cid

        # Upsert: creates new edge or updates confidence/evidence on re-run
        confidence = avg_score
        add_relationship(conn, lo, hi, "similar", confidence, evidence_chunk_id)
        created += 1

        if on_progress is not None:
            on_progress(
                f"Compared paper {paper_id} vs {other_id}: score={avg_score:.3f}"
            )

    return {
        "relationships_created": created,
        "papers_compared": compared,
        "papers_skipped": skipped,
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
    i = 2
    while f"{base}{i}" in used_keys:
        i += 1
    candidate = f"{base}{i}"
    used_keys.add(candidate)
    return candidate


def _generate_bibtex(
    paper: dict, used_keys: set[str] | None = None, paper_id: int | None = None
) -> str:
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


def _query_papers(
    conn: sqlite3.Connection,
    paper_ids: list[int] | None = None,
    title_pattern: str | None = None,
) -> list:
    """Query papers with optional filters. Shared by export and sync."""
    if paper_ids:
        from .db import _batched_select

        return _batched_select(
            conn, "SELECT * FROM papers WHERE id IN ({ph})", paper_ids
        )
    if title_pattern:
        return conn.execute(
            "SELECT * FROM papers WHERE title LIKE ? ESCAPE '\\'",
            (f"%{escape_like(title_pattern)}%",),
        ).fetchall()
    return conn.execute("SELECT * FROM papers").fetchall()


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
        r"\(([A-Z][a-zA-Z'\-]+)(?:\s+(?:et\s+al\.|&\s+[A-Z][a-zA-Z'\-]+))?,\s*((?:19|20)\d{2})\)",
        # Narrative: Surname et al. (YYYY) or Surname (YYYY)
        r"([A-Z][a-zA-Z'\-]+)(?:\s+et\s+al\.)?\s+\(((?:19|20)\d{2})\)",
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

    # Get all chunks for this paper via paper_paths (with legacy fallback)
    source_uri = get_paper_source_uri(conn, paper_id)
    chunks = []
    if source_uri:
        chunks = conn.execute(
            "SELECT content FROM chunks WHERE source_uri = ?",
            (source_uri,),
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
    text_words = set(re.findall(r"\b\w+\b", full_text.lower()))
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
        matched_words = sum(1 for w in meaningful if w.lower() in text_words)
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
