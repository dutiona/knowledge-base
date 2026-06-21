"""Papers route: paper metadata, relationships, conclusions, bibtex."""

from __future__ import annotations

import json
from pathlib import Path

from fastmcp import FastMCP

from .._conn import _get_conn
from ..bibtex import export_bibtex, sync_bibtex
from ..conclusions import (
    get_conclusion_chain,
    get_conclusions,
    record_conclusion,
    supersede_conclusion,
)
from ..exceptions import KnowledgeBaseError
from ..papers import (
    add_relationship,
    get_paper,
    get_paper_paths,
    get_relationships,
    register_paper,
    relocate_paper,
    suggest_relationships,
)

mcp = FastMCP("papers-routes")

_ALLOWED_BIB_EXTENSIONS = {".bib", ".bibtex"}


def _validate_bib_path(output_path: str) -> Path:
    """Validate that output_path is a safe .bib file location.

    Raises ValueError if the path has an unsafe extension or resolves
    outside the user's home directory or current working directory.
    """
    p = Path(output_path).expanduser().resolve()
    if p.suffix.lower() not in _ALLOWED_BIB_EXTENSIONS:
        raise ValueError(f"output_path must have a .bib or .bibtex extension, got: {p.suffix!r}")
    home = Path.home().resolve()
    cwd = Path.cwd().resolve()
    if not (p.is_relative_to(home) or p.is_relative_to(cwd)):
        raise ValueError(f"output_path must be under home ({home}) or cwd ({cwd}), got: {p}")
    return p


@mcp.tool()
def register_paper_tool(
    title: str,
    authors: list[str] | None = None,
    year: int | None = None,
    venue: str | None = None,
    doi: str | None = None,
    bibtex: str | None = None,
    source_uri: str | None = None,
    skip_auto_relate: bool = False,
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
        skip_auto_relate: If True, skip auto-scheduling similarity scan (useful for bulk imports).
    """
    conn = _get_conn()
    result = register_paper(conn, title, authors, year, venue, doi, bibtex, source_uri)
    paper_id = result.get("paper_id")
    if paper_id and source_uri and not skip_auto_relate:
        from ..jobs import submit_job

        submit_job(conn, paper_id, "auto_relate", {"paper_id": paper_id})
    return json.dumps(result)


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
        relation_type: One of: extends, contradicts, replicates, cites, compares, applies, implements.
        confidence: Confidence score 0.0-1.0 (default 1.0).
        evidence_chunk_id: Optional chunk ID containing evidence for this relationship.
    """
    conn = _get_conn()
    try:
        return json.dumps(
            add_relationship(
                conn,
                source_paper_id,
                target_paper_id,
                relation_type,
                confidence,
                evidence_chunk_id,
            )
        )
    except KnowledgeBaseError as e:
        err = {"error": str(e), **e.details}
        return json.dumps(err)


@mcp.tool()
def get_relationships_tool(
    paper_id: int,
    relation_type: str | None = None,
    direction: str = "both",
) -> str:
    """Get relationships for a paper.

    Args:
        paper_id: Paper ID to query relationships for.
        relation_type: Filter by type (extends, contradicts, replicates, cites, compares, applies, implements).
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
    try:
        return json.dumps(record_conclusion(conn, claim, confidence, source_chunk_ids, session_context))
    except KnowledgeBaseError as e:
        err = {"error": str(e), **e.details}
        return json.dumps(err)


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
    try:
        return json.dumps(
            supersede_conclusion(
                conn,
                old_conclusion_id,
                new_claim,
                confidence,
                source_chunk_ids,
                session_context,
            )
        )
    except KnowledgeBaseError as e:
        err = {"error": str(e), **e.details}
        return json.dumps(err)


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
    bibtex_str = export_bibtex(conn, paper_ids, title_pattern)
    if output_path:
        try:
            p = _validate_bib_path(output_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(bibtex_str, encoding="utf-8")
            return json.dumps({"written_to": str(p), "entries": bibtex_str.count("@")})
        except (OSError, ValueError) as e:
            return json.dumps({"error": str(e)})
    return json.dumps({"bibtex": bibtex_str, "entries": bibtex_str.count("@")})


@mcp.tool()
def sync_bibtex_tool(
    output_path: str,
    paper_ids: list[int] | None = None,
    title_pattern: str | None = None,
) -> str:
    """Append only new papers to an existing .bib file, skipping duplicates.

    Args:
        output_path: Path to the .bib file (created if it doesn't exist).
        paper_ids: Sync specific papers by ID.
        title_pattern: Sync papers matching title substring.
    """
    try:
        p = _validate_bib_path(output_path)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    conn = _get_conn()
    try:
        result = sync_bibtex(conn, str(p), paper_ids, title_pattern)
        return json.dumps(result)
    except OSError as e:
        return json.dumps({"error": f"Failed to sync {p}: {e}"})


@mcp.tool()
def suggest_relationships_tool(paper_id: int) -> str:
    """Suggest citation relationships by matching DOIs, title words, and author+year.

    Returns suggestions (candidate relationships with confidence scores) and
    unmatched DOIs found in text that don't match any registered paper.

    Args:
        paper_id: Paper ID to analyze for citation references.
    """
    conn = _get_conn()
    return json.dumps(suggest_relationships(conn, paper_id))


@mcp.tool()
def scan_relationships(paper_id: int | None = None) -> str:
    """Scan for embedding-similarity relationships between papers.

    Compares chunk embeddings and creates 'similar' relationships when cosine
    similarity exceeds the configured threshold. Submits background jobs.

    Args:
        paper_id: Scan this paper only (1×M). If omitted, scan all papers (N×M).
    """
    conn = _get_conn()
    from ..jobs import submit_job

    if paper_id is not None:
        job_id = submit_job(conn, paper_id, "auto_relate", {"paper_id": paper_id})
        return json.dumps({"job_id": job_id})

    # Full scan: one job per paper, each comparing only against higher IDs
    # to avoid redundant pairwise comparisons (#166).
    papers = conn.execute("SELECT id FROM papers").fetchall()
    submitted = 0
    for row in papers:
        submit_job(
            conn,
            row["id"],
            "auto_relate",
            {"paper_id": row["id"], "only_compare_higher": True},
        )
        submitted += 1
    return json.dumps({"jobs_submitted": submitted})


@mcp.tool()
def relocate_paper_tool(paper_id: int, new_path: str) -> str:
    """Update a paper's filesystem path after moving/renaming the file.

    Updates all internal references so lookups continue to work.

    Args:
        paper_id: The paper to update.
        new_path: The new absolute path to the file.
    """
    conn = _get_conn()
    try:
        return json.dumps(relocate_paper(conn, paper_id, new_path))
    except KnowledgeBaseError as e:
        err = {"error": str(e), **e.details}
        return json.dumps(err)


@mcp.tool()
def get_paper_paths_tool(paper_id: int) -> str:
    """List all registered filesystem paths for a paper.

    Args:
        paper_id: The paper to look up.
    """
    conn = _get_conn()
    return json.dumps(get_paper_paths(conn, paper_id))
