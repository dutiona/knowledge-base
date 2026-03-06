"""FastMCP server exposing research-index tools."""

from __future__ import annotations

import json
from pathlib import Path

from fastmcp import FastMCP

from .conclusions import (
    get_conclusion_chain,
    get_conclusions,
    record_conclusion,
    supersede_conclusion,
)
from .db import DEFAULT_DB_PATH, get_connection, init_schema
from .ingest import ingest_directory, ingest_file
from .papers import (
    add_relationship,
    export_bibtex,
    get_paper,
    get_relationships,
    register_paper,
    suggest_relationships,
)
from .search import search

mcp = FastMCP(
    "research-index",
    instructions=(
        "Hybrid semantic search over research papers, code, and notes. "
        "Use 'search' to find relevant content by concept or keyword. "
        "Use 'ingest' to add new files. Use 'status' for index statistics. "
        "Use 'register_paper_tool' and 'get_paper_tool' to manage paper metadata. "
        "Use 'add_relationship_tool' to create typed edges between papers. "
        "Use 'record_conclusion_tool' to record evidence-chained claims. "
        "Use 'export_bibtex_tool' to export papers for Typst bibliography. "
        "Use 'suggest_relationships_tool' to auto-detect citations."
    ),
)

_conn = None


def _get_conn():
    global _conn
    if _conn is None:
        _conn = get_connection()
        init_schema(_conn)
    return _conn


@mcp.tool()
def ingest(path: str, source_type: str | None = None) -> str:
    """Ingest a file or directory into the research index.

    Args:
        path: Absolute path to a file or directory.
        source_type: Override auto-detection. One of: pdf, markdown, code, web, note.
    """
    conn = _get_conn()
    p = Path(path).expanduser().resolve()

    if not p.exists():
        return json.dumps({"error": f"Path does not exist: {p}"})

    if p.is_dir():
        results = ingest_directory(conn, p)
        total_added = sum(r["chunks_added"] for r in results)
        total_skipped = sum(r["chunks_skipped"] for r in results)
        return json.dumps({
            "files_processed": len(results),
            "chunks_added": total_added,
            "chunks_skipped": total_skipped,
            "details": results,
        })
    else:
        result = ingest_file(conn, p, source_type)
        return json.dumps(result)


@mcp.tool()
def search_index(
    query: str,
    top_k: int = 10,
    source_type: str | None = None,
    mode: str = "hybrid",
) -> str:
    """Search the research index using hybrid semantic + keyword search.

    Args:
        query: Natural language search query.
        top_k: Number of results to return (default 10).
        source_type: Filter results by type (pdf, markdown, code, web, note).
        mode: Search mode - 'hybrid' (default), 'fts' (keyword only), 'vec' (semantic only).
    """
    conn = _get_conn()
    results = search(conn, query, top_k=top_k, source_type=source_type, mode=mode)

    return json.dumps([
        {
            "chunk_id": r.chunk_id,
            "content": r.content,
            "source_type": r.source_type,
            "source_uri": r.source_uri,
            "chunk_index": r.chunk_index,
            "score": round(r.score, 6),
            "match_type": r.match_type,
        }
        for r in results
    ])


@mcp.tool()
def status() -> str:
    """Get index statistics: chunk counts by type, recent ingestions, DB size."""
    conn = _get_conn()

    type_counts = conn.execute(
        "SELECT source_type, COUNT(*) as count FROM chunks GROUP BY source_type"
    ).fetchall()

    total = conn.execute("SELECT COUNT(*) as count FROM chunks").fetchone()["count"]

    paper_count = conn.execute("SELECT COUNT(*) as count FROM papers").fetchone()["count"]
    conclusion_count = conn.execute("SELECT COUNT(*) as count FROM conclusions").fetchone()["count"]
    relationship_count = conn.execute("SELECT COUNT(*) as count FROM relationships").fetchone()["count"]

    recent = conn.execute(
        """SELECT source_uri, source_type, COUNT(*) as chunks, created_at
           FROM chunks GROUP BY source_uri
           ORDER BY created_at DESC LIMIT 5"""
    ).fetchall()

    db_size_bytes = DEFAULT_DB_PATH.stat().st_size if DEFAULT_DB_PATH.exists() else 0

    return json.dumps({
        "total_chunks": total,
        "by_type": {row["source_type"]: row["count"] for row in type_counts},
        "papers": paper_count,
        "conclusions": conclusion_count,
        "relationships": relationship_count,
        "recent_ingestions": [
            {
                "source_uri": row["source_uri"],
                "source_type": row["source_type"],
                "chunks": row["chunks"],
                "created_at": row["created_at"],
            }
            for row in recent
        ],
        "db_size_mb": round(db_size_bytes / (1024 * 1024), 2),
        "db_path": str(DEFAULT_DB_PATH),
    })


@mcp.tool()
def register_paper_tool(
    title: str,
    authors: list[str] | None = None,
    year: int | None = None,
    venue: str | None = None,
    doi: str | None = None,
    bibtex: str | None = None,
    source_uri: str | None = None,
) -> str:
    """Register a research paper. Optionally link to already-ingested chunks via source_uri.

    Args:
        title: Paper title.
        authors: List of author names.
        year: Publication year.
        venue: Conference or journal name.
        doi: Digital Object Identifier.
        bibtex: Raw BibTeX entry (stored as-is for export).
        source_uri: Path of an already-ingested file to link chunks to this paper.
    """
    conn = _get_conn()
    return json.dumps(register_paper(conn, title, authors, year, venue, doi, bibtex, source_uri))


@mcp.tool()
def get_paper_tool(
    paper_id: int | None = None,
    title_pattern: str | None = None,
    doi: str | None = None,
) -> str:
    """Retrieve paper metadata, related chunks, and relationships.

    Args:
        paper_id: Lookup by paper ID.
        title_pattern: Lookup by title substring match.
        doi: Lookup by DOI.
    """
    conn = _get_conn()
    return json.dumps(get_paper(conn, paper_id, title_pattern, doi))


@mcp.tool()
def add_relationship_tool(
    source_paper_id: int,
    target_paper_id: int,
    relation_type: str,
    confidence: float = 1.0,
    evidence_chunk_id: int | None = None,
) -> str:
    """Add a typed relationship between two papers. Upserts on conflict.

    Args:
        source_paper_id: ID of the source paper.
        target_paper_id: ID of the target paper.
        relation_type: One of: extends, contradicts, replicates, cites, compares.
        confidence: Confidence score 0.0-1.0 (default 1.0).
        evidence_chunk_id: Optional chunk ID containing evidence for this relationship.
    """
    conn = _get_conn()
    return json.dumps(add_relationship(conn, source_paper_id, target_paper_id, relation_type, confidence, evidence_chunk_id))


@mcp.tool()
def get_relationships_tool(
    paper_id: int,
    relation_type: str | None = None,
    direction: str = "both",
) -> str:
    """Get relationships for a paper.

    Args:
        paper_id: Paper ID to query relationships for.
        relation_type: Filter by type (extends, contradicts, replicates, cites, compares).
        direction: 'outgoing', 'incoming', or 'both' (default).
    """
    conn = _get_conn()
    return json.dumps(get_relationships(conn, paper_id, relation_type, direction))


@mcp.tool()
def record_conclusion_tool(
    claim: str,
    confidence: float = 1.0,
    source_chunk_ids: list[int] | None = None,
    session_context: str | None = None,
) -> str:
    """Record an analytical conclusion with evidence links to source chunks.

    Args:
        claim: The conclusion claim text.
        confidence: Confidence score 0.0-1.0.
        source_chunk_ids: List of chunk IDs serving as evidence.
        session_context: Context about why this conclusion was drawn.
    """
    conn = _get_conn()
    return json.dumps(record_conclusion(conn, claim, confidence, source_chunk_ids, session_context))


@mcp.tool()
def get_conclusions_tool(
    keyword: str | None = None,
    min_confidence: float = 0.0,
    include_superseded: bool = False,
) -> str:
    """Search conclusions by keyword and confidence threshold.

    Args:
        keyword: Search term for claim text.
        min_confidence: Minimum confidence threshold (default 0.0).
        include_superseded: Include conclusions that have been superseded (default false).
    """
    conn = _get_conn()
    return json.dumps(get_conclusions(conn, keyword, min_confidence, include_superseded))


@mcp.tool()
def supersede_conclusion_tool(
    old_conclusion_id: int,
    new_claim: str,
    confidence: float = 1.0,
    source_chunk_ids: list[int] | None = None,
    session_context: str | None = None,
) -> str:
    """Supersede an old conclusion with a new one, maintaining the chain.

    Args:
        old_conclusion_id: ID of the conclusion to supersede.
        new_claim: The updated conclusion claim.
        confidence: Confidence score for the new conclusion.
        source_chunk_ids: Updated evidence chunk IDs.
        session_context: Context for why the conclusion changed.
    """
    conn = _get_conn()
    return json.dumps(supersede_conclusion(conn, old_conclusion_id, new_claim, confidence, source_chunk_ids, session_context))


@mcp.tool()
def get_conclusion_chain_tool(conclusion_id: int) -> str:
    """Follow the supersession chain for a conclusion (oldest to newest).

    Args:
        conclusion_id: Any conclusion ID in the chain.
    """
    conn = _get_conn()
    return json.dumps(get_conclusion_chain(conn, conclusion_id))


@mcp.tool()
def export_bibtex_tool(
    paper_ids: list[int] | None = None,
    title_pattern: str | None = None,
    output_path: str | None = None,
) -> str:
    """Export papers as BibTeX for Typst citation workflow.

    Args:
        paper_ids: Export specific papers by ID.
        title_pattern: Export papers matching title substring.
        output_path: Optional file path to write .bib file (returns content if omitted).
    """
    conn = _get_conn()
    bibtex = export_bibtex(conn, paper_ids, title_pattern)
    if output_path:
        try:
            p = Path(output_path).expanduser().resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(bibtex, encoding="utf-8")
            return json.dumps({"written_to": str(p), "entries": bibtex.count("@")})
        except OSError as e:
            return json.dumps({"error": f"Failed to write {output_path}: {e}"})
    return json.dumps({"bibtex": bibtex, "entries": bibtex.count("@")})


@mcp.tool()
def suggest_relationships_tool(paper_id: int) -> str:
    """Suggest citation relationships by parsing DOIs and titles from paper chunks.

    Args:
        paper_id: Paper ID to analyze for citation references.
    """
    conn = _get_conn()
    return json.dumps(suggest_relationships(conn, paper_id))


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
