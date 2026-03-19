"""FastMCP server exposing knowledge-base tools."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from fastmcp import FastMCP

from .conclusions import (
    get_conclusion_chain,
    get_conclusions,
    record_conclusion,
    supersede_conclusion,
)
from .db import DEFAULT_DB_PATH, co_occurrence_pairs, get_connection, init_schema
from .embed_swap import get_embed_config, re_embed
from .extraction import (
    _MAX_WORKERS_LIMIT,
    compare_papers,
    configure_llm,
    estimate_extraction_time,
    extract_structure,
    get_entities,
    record_dataset,
    record_method,
    record_metric,
)
from .jobs import get_job, list_jobs as _list_jobs, submit_job
from .ingest import (
    configure_browser,
    ingest_directory,
    ingest_file,
    ingest_url as _ingest_url,
    reingest_file,
)
from .papers import (
    add_relationship,
    export_bibtex,
    get_paper,
    get_paper_paths,
    get_relationships,
    register_paper,
    relocate_paper,
    suggest_relationships,
    sync_bibtex,
)
from .prediction_errors import (
    detect_and_log,
    get_prediction_error_count,
    list_prediction_errors as _list_prediction_errors,
    resolve_prediction_error,
)
from .search import search
from .vision import configure_omniparser, configure_vision, estimate_figures_time

mcp = FastMCP(
    "knowledge-base",
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

_ALLOWED_BIB_EXTENSIONS = {".bib", ".bibtex"}


def _validate_bib_path(output_path: str) -> Path:
    """Validate that output_path is a safe .bib file location.

    Raises ValueError if the path has an unsafe extension or resolves
    outside the user's home directory or current working directory.
    """
    p = Path(output_path).expanduser().resolve()
    if p.suffix.lower() not in _ALLOWED_BIB_EXTENSIONS:
        raise ValueError(
            f"output_path must have a .bib or .bibtex extension, got: {p.suffix!r}"
        )
    home = Path.home().resolve()
    cwd = Path.cwd().resolve()
    if not (str(p).startswith(str(home)) or str(p).startswith(str(cwd))):
        raise ValueError(
            f"output_path must be under home ({home}) or cwd ({cwd}), got: {p}"
        )
    return p


_local = threading.local()
_schema_lock = threading.Lock()
_schema_ready = False


def _get_conn():
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = get_connection()
        global _schema_ready
        if not _schema_ready:
            with _schema_lock:
                if not _schema_ready:
                    init_schema(conn)
                    _schema_ready = True
        _local.conn = conn
    return conn


@mcp.tool()
def ingest(
    path: str,
    source_type: str | None = None,
    session_id: str | None = None,
) -> str:
    """Ingest a file or directory into the knowledge base.

    Args:
        path: Absolute path to a file or directory.
        source_type: Override auto-detection. One of: pdf, markdown, code, web, note, figure.
        session_id: Optional session ID to group co-ingested documents.
            For directories, a shared session ID is auto-generated.
            For files, pass the same session_id across calls to mark co-occurrence.
    """
    conn = _get_conn()
    p = Path(path).expanduser().resolve()

    if not p.exists():
        return json.dumps({"error": f"Path does not exist: {p}"})

    if p.is_dir():
        results = ingest_directory(conn, p)
        total_added = sum(r["chunks_added"] for r in results)
        total_skipped = sum(r["chunks_skipped"] for r in results)
        return json.dumps(
            {
                "files_processed": len(results),
                "chunks_added": total_added,
                "chunks_skipped": total_skipped,
                "details": results,
            }
        )
    else:
        result = ingest_file(conn, p, source_type, session_id=session_id)
        return json.dumps(result)


@mcp.tool()
def reingest(
    path: str,
    source_type: str | None = None,
    session_id: str | None = None,
) -> str:
    """Force re-ingest of a previously ingested file. Deletes old chunks and inserts new ones.

    Cleans up FK references in papers, relationships, and conclusions.

    Args:
        path: Absolute path to the file to re-ingest.
        source_type: Override auto-detection. One of: pdf, markdown, code, web, note, figure.
        session_id: Optional session ID for co-occurrence tracking.
    """
    conn = _get_conn()
    p = Path(path).expanduser().resolve()

    if not p.exists():
        return json.dumps({"error": f"Path does not exist: {p}"})

    result = reingest_file(conn, p, source_type, session_id=session_id)
    return json.dumps(result)


@mcp.tool()
def ingest_url(url: str, session_id: str | None = None) -> str:
    """Ingest a web page by URL. Fetches the page, extracts main content, and indexes it.

    Uses content-hash dedup — unchanged pages are skipped on re-ingest.
    Use the reingest tool with the URL as path to force re-ingest.

    Args:
        url: The URL to fetch and ingest.
        session_id: Optional session ID for co-occurrence tracking.
    """
    conn = _get_conn()
    return json.dumps(_ingest_url(conn, url, session_id=session_id))


@mcp.tool()
def search_index(
    query: str,
    top_k: int = 10,
    source_type: str | None = None,
    mode: str = "hybrid",
    keyword_prefilter: bool = False,
) -> str:
    """Search the knowledge base using hybrid semantic + keyword search.

    Args:
        query: Natural language search query.
        top_k: Number of results to return (default 10).
        source_type: Filter results by type (pdf, markdown, code, web, note, figure).
        mode: Search mode - 'hybrid' (default), 'fts' (keyword only), 'vec' (semantic only).
        keyword_prefilter: Extract intent keywords for FTS matching instead of
            using the raw query. Improves precision for verbose natural language
            queries by stripping stopwords and filler. Default false.
    """
    conn = _get_conn()
    results = search(
        conn,
        query,
        top_k=top_k,
        source_type=source_type,
        mode=mode,
        keyword_prefilter=keyword_prefilter,
    )
    detect_and_log(conn, query, results, source_type_filter=source_type, mode=mode)

    return json.dumps(
        [
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
        ]
    )


@mcp.tool()
def co_occurrence(min_sessions: int = 1) -> str:
    """Find document pairs that were ingested together in the same session.

    Co-ingestion is a behavioral signal: documents ingested together share
    research context at ingestion time. This complements embedding similarity
    by capturing relationships that no query could surface via BM25 or cosine.

    Args:
        min_sessions: Minimum number of shared sessions to include a pair (default 1).
    """
    conn = _get_conn()
    return json.dumps(co_occurrence_pairs(conn, min_sessions))


@mcp.tool()
def status() -> str:
    """Get index statistics: chunk counts by type, recent ingestions, DB size."""
    conn = _get_conn()

    type_counts = conn.execute(
        "SELECT source_type, COUNT(*) as count FROM chunks GROUP BY source_type"
    ).fetchall()

    total = conn.execute("SELECT COUNT(*) as count FROM chunks").fetchone()["count"]

    paper_count = conn.execute("SELECT COUNT(*) as count FROM papers").fetchone()[
        "count"
    ]
    conclusion_count = conn.execute(
        "SELECT COUNT(*) as count FROM conclusions"
    ).fetchone()["count"]
    relationship_count = conn.execute(
        "SELECT COUNT(*) as count FROM relationships"
    ).fetchone()["count"]
    folder_summary_count = conn.execute(
        "SELECT COUNT(*) as count FROM folder_summaries"
    ).fetchone()["count"]
    method_count = conn.execute("SELECT COUNT(*) as count FROM methods").fetchone()[
        "count"
    ]
    dataset_count = conn.execute("SELECT COUNT(*) as count FROM datasets").fetchone()[
        "count"
    ]
    metric_count = conn.execute("SELECT COUNT(*) as count FROM metrics").fetchone()[
        "count"
    ]

    recent = conn.execute(
        """SELECT source_uri, source_type, COUNT(*) as chunks, created_at
           FROM chunks GROUP BY source_uri
           ORDER BY created_at DESC LIMIT 5"""
    ).fetchall()

    db_size_bytes = DEFAULT_DB_PATH.stat().st_size if DEFAULT_DB_PATH.exists() else 0

    job_counts = {}
    for row in conn.execute(
        "SELECT status, COUNT(*) as count FROM jobs GROUP BY status"
    ).fetchall():
        job_counts[row["status"]] = row["count"]

    return json.dumps(
        {
            "total_chunks": total,
            "by_type": {row["source_type"]: row["count"] for row in type_counts},
            "papers": paper_count,
            "conclusions": conclusion_count,
            "relationships": relationship_count,
            "folder_summaries": folder_summary_count,
            "methods": method_count,
            "datasets": dataset_count,
            "metrics": metric_count,
            "prediction_errors": get_prediction_error_count(conn),
            "jobs": job_counts,
            "embed_config": get_embed_config(conn),
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
        }
    )


@mcp.tool()
def embed_config() -> str:
    """Get current embedding model configuration (model name and dimension)."""
    conn = _get_conn()
    return json.dumps(get_embed_config(conn))


@mcp.tool()
def re_embed_tool(model: str, dim: int) -> str:
    """Re-embed all chunks with a new embedding model.

    Drops and recreates the vector table with new dimensions, then re-embeds
    all existing chunks. This is expensive — use only when switching models.

    Args:
        model: Ollama model name (e.g. 'mxbai-embed-large', 'nomic-embed-text').
        dim: Embedding dimension for the new model.
    """
    conn = _get_conn()
    return json.dumps(re_embed(conn, model, dim))


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
    return json.dumps(
        register_paper(conn, title, authors, year, venue, doi, bibtex, source_uri)
    )


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
    return json.dumps(
        record_conclusion(conn, claim, confidence, source_chunk_ids, session_context)
    )


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
    return json.dumps(
        get_conclusions(conn, keyword, min_confidence, include_superseded)
    )


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
            _validate_bib_path(output_path)
            p = Path(output_path).expanduser().resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(bibtex, encoding="utf-8")
            return json.dumps({"written_to": str(p), "entries": bibtex.count("@")})
        except (OSError, ValueError) as e:
            return json.dumps({"error": str(e)})
    return json.dumps({"bibtex": bibtex, "entries": bibtex.count("@")})


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
        _validate_bib_path(output_path)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    conn = _get_conn()
    try:
        result = sync_bibtex(conn, output_path, paper_ids, title_pattern)
        return json.dumps(result)
    except OSError as e:
        return json.dumps({"error": f"Failed to sync {output_path}: {e}"})


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
def relocate_paper_tool(paper_id: int, new_path: str) -> str:
    """Update a paper's filesystem path after moving/renaming the file.

    Updates all internal references so lookups continue to work.

    Args:
        paper_id: The paper to update.
        new_path: The new absolute path to the file.
    """
    conn = _get_conn()
    return json.dumps(relocate_paper(conn, paper_id, new_path))


@mcp.tool()
def get_paper_paths_tool(paper_id: int) -> str:
    """List all registered filesystem paths for a paper.

    Args:
        paper_id: The paper to look up.
    """
    conn = _get_conn()
    return json.dumps(get_paper_paths(conn, paper_id))


@mcp.tool()
def record_method_tool(
    name: str,
    paper_id: int,
    description: str | None = None,
) -> str:
    """Record a method used in a paper.

    Args:
        name: Method name (e.g. 'Transformer', 'ResNet-50').
        paper_id: Paper that uses this method.
        description: Brief description of the method.
    """
    conn = _get_conn()
    return json.dumps(record_method(conn, name, paper_id, description))


@mcp.tool()
def record_dataset_tool(
    name: str,
    paper_id: int,
    description: str | None = None,
) -> str:
    """Record a dataset used in a paper.

    Args:
        name: Dataset name (e.g. 'ImageNet', 'GLUE').
        paper_id: Paper that uses this dataset.
        description: Brief description of the dataset.
    """
    conn = _get_conn()
    return json.dumps(record_dataset(conn, name, paper_id, description))


@mcp.tool()
def record_metric_tool(
    name: str,
    value: float,
    paper_id: int,
    method_id: int | None = None,
    dataset_id: int | None = None,
    unit: str | None = None,
) -> str:
    """Record a metric value from a paper.

    Args:
        name: Metric name (e.g. 'accuracy', 'F1', 'BLEU').
        value: Numeric value of the metric.
        paper_id: Paper reporting this metric.
        method_id: Method that achieved this metric.
        dataset_id: Dataset the metric was measured on.
        unit: Unit of measurement (e.g. '%', 'ms').
    """
    conn = _get_conn()
    return json.dumps(
        record_metric(conn, name, value, paper_id, method_id, dataset_id, unit)
    )


@mcp.tool()
def compare_papers_tool(paper_ids: list[int]) -> str:
    """Compare metrics across papers on shared datasets.

    Shows side-by-side results for papers that report metrics on the same datasets.

    Args:
        paper_ids: List of 2+ paper IDs to compare.
    """
    conn = _get_conn()
    return json.dumps(compare_papers(conn, paper_ids))


@mcp.tool()
def extract_structure_tool(
    paper_id: int,
    confirmed: bool = False,
    max_workers: int = 1,
) -> str:
    """Extract methods, datasets, and metrics from a paper using LLM.

    For short papers, runs inline. For long papers (>2min estimated),
    returns a warning with ETA — call again with confirmed=True to queue
    a background job. Use get_job_status(job_id) to poll progress.

    Args:
        paper_id: Paper ID to extract structure from.
        confirmed: Set True to skip the ETA warning for long documents.
        max_workers: Number of concurrent LLM calls for the map phase (default 1).
            Increase to match your LLM server's parallel capacity.
    """
    conn = _get_conn()
    est = estimate_extraction_time(conn, paper_id)
    if "error" in est:
        return json.dumps(est)

    # Short doc: run inline (fast path) — reuse chunks from estimate
    if not est["is_long"]:
        return json.dumps(
            extract_structure(
                conn,
                paper_id,
                confirmed=True,
                max_workers=max_workers,
                _prefetched_chunks=est["chunks"],
            )
        )

    # Long doc, not confirmed: ETA warning (adjust for parallelism)
    effective_workers = min(max(max_workers, 1), est["chunk_count"], _MAX_WORKERS_LIMIT)
    wall_estimate = est["estimated_seconds"] // effective_workers
    if wall_estimate > 120 and not confirmed:
        return json.dumps(
            {
                "warning": (
                    f"Extraction will take ~{wall_estimate // 60}min "
                    f"for {est['chunk_count']} chunks"
                    + (
                        f" ({effective_workers} workers)"
                        if effective_workers > 1
                        else ""
                    )
                ),
                "estimated_seconds": wall_estimate,
                "chunk_count": est["chunk_count"],
                "max_workers": effective_workers,
                "confirm_required": True,
            }
        )

    # Long doc, confirmed: submit background job
    # Normalize max_workers to effective value so dedup key is stable
    # (e.g., max_workers=10 and max_workers=20 on a 3-chunk paper both clamp to 3)
    params = {"max_workers": effective_workers} if effective_workers > 1 else None
    job_id = submit_job(conn, paper_id, "extract_structure", params=params)
    return json.dumps(
        {
            "deferred": True,
            "job_id": job_id,
            "status": "pending",
            "message": "Use get_job_status(job_id) to poll progress.",
        }
    )


@mcp.tool()
def configure_llm_tool(
    provider: str = "ollama",
    base_url: str | None = None,
    model: str = "qwen3.5:27b",
    api_key: str | None = None,
) -> str:
    """Configure the LLM used for structured extraction.

    Args:
        provider: 'ollama' (native API) or 'openai_compat' (OpenAI-compatible API).
        base_url: Base URL (e.g. 'http://192.168.1.41:1234'). Required for openai_compat.
        model: Model name (e.g. 'qwen3.5:27b', 'qwen/qwen3.5-35b-a3b').
        api_key: Optional API key for authenticated endpoints.
    """
    conn = _get_conn()
    return json.dumps(configure_llm(conn, provider, base_url, model, api_key))


@mcp.tool()
def get_entities_tool(paper_id: int) -> str:
    """List resolved entities for a paper with their surface forms and chunk mentions.

    Args:
        paper_id: Paper ID to get entities for.
    """
    conn = _get_conn()
    return json.dumps(get_entities(conn, paper_id))


@mcp.tool()
def extract_figures_tool(
    paper_id: int, pages: list[int] | None = None, confirmed: bool = False
) -> str:
    """Extract figures from a paper's PDF using a vision model.

    Renders candidate pages as images, sends them to the configured vision model,
    and stores figure descriptions as searchable 'figure' chunks.

    Always queues a background job (figure extraction involves PDF rendering +
    vision API calls). Returns an ETA warning first; call with confirmed=True
    to submit the job. Use get_job_status(job_id) to poll progress.

    Args:
        paper_id: Paper ID.
        pages: 1-based page numbers to process (optional, auto-detects if omitted).
        confirmed: Skip ETA warning for long documents.
    """
    conn = _get_conn()
    # Convert 1-based (user-facing) to 0-based (internal)
    pages_0 = None
    if pages is not None:
        invalid = [p for p in pages if p <= 0]
        if invalid:
            return json.dumps({"error": f"Pages must be >= 1 (got {invalid})"})
        pages_0 = [p - 1 for p in pages]

    est = estimate_figures_time(conn, paper_id, pages=pages_0)
    if "error" in est:
        return json.dumps(est)

    # ETA gate
    if est["estimated_seconds"] > 120 and not confirmed:
        return json.dumps(
            {
                "confirm_required": True,
                "estimated_seconds": est["estimated_seconds"],
                "extracted_images": est["extracted_images"],
                "vector_pages": est["vector_pages"],
            }
        )

    # Submit background job
    job_id = submit_job(conn, paper_id, "extract_figures", {"pages": pages_0})
    return json.dumps(
        {
            "deferred": True,
            "job_id": job_id,
            "status": "pending",
            "message": "Use get_job_status(job_id) to poll progress.",
        }
    )


@mcp.tool()
def configure_vision_tool(
    model: str | None = None,
    base_url: str | None = None,
) -> str:
    """Configure the vision model used for figure extraction.

    Args:
        model: Vision model name (e.g. 'gemma3:27b', 'llava:13b').
        base_url: Base URL for the vision API (e.g. 'http://localhost:11434').
    """
    conn = _get_conn()
    return json.dumps(configure_vision(conn, model=model, base_url=base_url))


@mcp.tool()
def configure_omniparser_tool(path: str | None = None) -> str:
    """Configure OmniParser for figure enrichment.

    OmniParser adds OCR text and icon detection to figure descriptions.
    Requires a local OmniParser installation with its own venv.
    This is a local-only tool — the path is executed as a subprocess.

    Args:
        path: Absolute path to OmniParser directory (None to query, empty string to disable).
    """
    conn = _get_conn()
    return json.dumps(configure_omniparser(conn, path))


@mcp.tool()
def configure_browser_tool(
    cdp_endpoint: str | None = None,
    venv_path: str | None = None,
) -> str:
    """Configure browser rendering for JS-heavy web pages.

    Enables fallback rendering when trafilatura extracts insufficient content
    from a URL (< 200 chars). Also captures screenshots for figure extraction
    via the vision pipeline. Only http/https URLs are rendered.

    Note: browser rendering executes page JavaScript, which can issue secondary
    requests beyond the original URL. This is an accepted trade-off for an
    optional feature. Each render uses an ephemeral browser context (no shared state).

    Two modes (both require a venv with ``playwright`` Python client installed):
    - **CDP (recommended for Docker):** Connect to a running Playwright container.
      Provide both cdp_endpoint and venv_path.
    - **Local:** Launch headless Chromium from the venv.
      Provide venv_path only.

    Local venv setup::

        python -m venv /path/to/venv
        /path/to/venv/bin/pip install playwright
        /path/to/venv/bin/playwright install --with-deps chromium

    CDP venv setup (Chromium not needed locally)::

        python -m venv /path/to/venv
        /path/to/venv/bin/pip install playwright

    Args:
        cdp_endpoint: WebSocket CDP endpoint (ws:// or wss://). Requires venv_path too.
        venv_path: Absolute path to Python venv with playwright installed.
            Pass both as empty string to disable browser rendering.
    """
    conn = _get_conn()
    return json.dumps(
        configure_browser(conn, cdp_endpoint=cdp_endpoint, venv_path=venv_path)
    )


@mcp.tool()
def get_job_status_tool(job_id: int) -> str:
    """Get the status and progress of a background extraction job.

    Args:
        job_id: Job ID returned by extract_structure_tool or extract_figures_tool.
    """
    conn = _get_conn()
    job = get_job(conn, job_id)
    if job is None:
        return json.dumps({"error": f"Job {job_id} not found"})
    return json.dumps(job)


@mcp.tool()
def list_jobs_tool(status: str | None = None, paper_id: int | None = None) -> str:
    """List background extraction jobs.

    Args:
        status: Filter by status: pending, running, completed, failed.
        paper_id: Filter by paper ID.
    """
    conn = _get_conn()
    return json.dumps(_list_jobs(conn, status=status, paper_id=paper_id))


@mcp.tool()
def list_prediction_errors_tool(
    since: str | None = None,
    unresolved_only: bool = True,
) -> str:
    """List prediction errors (queries with low-confidence or missing results).

    Prediction errors are logged automatically when search returns poor results.
    Use this to identify gaps in the knowledge base — queries that need better coverage.

    Args:
        since: ISO 8601 timestamp to filter errors after (e.g. '2025-01-01').
        unresolved_only: Only show unresolved errors (default true).
    """
    conn = _get_conn()
    return json.dumps(
        _list_prediction_errors(conn, since=since, unresolved_only=unresolved_only)
    )


@mcp.tool()
def resolve_prediction_error_tool(error_id: int) -> str:
    """Mark a prediction error as resolved (e.g. after ingesting content that fills the gap).

    Args:
        error_id: ID of the prediction error to resolve.
    """
    conn = _get_conn()
    return json.dumps(resolve_prediction_error(conn, error_id))


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
