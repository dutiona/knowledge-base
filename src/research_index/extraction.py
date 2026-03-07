"""Structured extraction: methods, datasets, metrics from paper chunks."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

import httpx

from .embeddings import _get_ollama_url


def record_method(
    conn: sqlite3.Connection,
    name: str,
    paper_id: int,
    description: str | None = None,
    chunk_id: int | None = None,
) -> dict:
    """Record or update a method for a paper."""
    conn.execute(
        """INSERT INTO methods (name, paper_id, description, chunk_id)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(name, paper_id)
           DO UPDATE SET description = excluded.description, chunk_id = excluded.chunk_id""",
        (name, paper_id, description, chunk_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM methods WHERE name = ? AND paper_id = ?", (name, paper_id)
    ).fetchone()
    return {"method_id": row["id"]}


def record_dataset(
    conn: sqlite3.Connection,
    name: str,
    paper_id: int,
    description: str | None = None,
    chunk_id: int | None = None,
) -> dict:
    """Record or update a dataset for a paper."""
    conn.execute(
        """INSERT INTO datasets (name, paper_id, description, chunk_id)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(name, paper_id)
           DO UPDATE SET description = excluded.description, chunk_id = excluded.chunk_id""",
        (name, paper_id, description, chunk_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM datasets WHERE name = ? AND paper_id = ?", (name, paper_id)
    ).fetchone()
    return {"dataset_id": row["id"]}


def record_metric(
    conn: sqlite3.Connection,
    name: str,
    value: float,
    paper_id: int,
    method_id: int | None = None,
    dataset_id: int | None = None,
    unit: str | None = None,
    chunk_id: int | None = None,
) -> dict:
    """Record a metric value."""
    cursor = conn.execute(
        """INSERT INTO metrics (name, value, unit, dataset_id, method_id, paper_id, chunk_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (name, value, unit, dataset_id, method_id, paper_id, chunk_id),
    )
    conn.commit()
    return {"metric_id": cursor.lastrowid}


def get_methods(conn: sqlite3.Connection, paper_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name, description, chunk_id FROM methods WHERE paper_id = ?",
        (paper_id,),
    ).fetchall()
    return [{"id": r["id"], "name": r["name"], "description": r["description"],
             "chunk_id": r["chunk_id"]} for r in rows]


def get_datasets(conn: sqlite3.Connection, paper_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name, description, chunk_id FROM datasets WHERE paper_id = ?",
        (paper_id,),
    ).fetchall()
    return [{"id": r["id"], "name": r["name"], "description": r["description"],
             "chunk_id": r["chunk_id"]} for r in rows]


def get_metrics(conn: sqlite3.Connection, paper_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT m.id, m.name, m.value, m.unit, m.chunk_id,
                  mt.name as method_name, d.name as dataset_name
           FROM metrics m
           LEFT JOIN methods mt ON m.method_id = mt.id
           LEFT JOIN datasets d ON m.dataset_id = d.id
           WHERE m.paper_id = ?""",
        (paper_id,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "value": r["value"],
            "unit": r["unit"],
            "method_name": r["method_name"],
            "dataset_name": r["dataset_name"],
            "chunk_id": r["chunk_id"],
        }
        for r in rows
    ]


def compare_papers(
    conn: sqlite3.Connection,
    paper_ids: list[int],
) -> list[dict]:
    """Compare metrics across papers on shared datasets.

    Returns results grouped by dataset, showing each paper's metrics.
    Only includes datasets that appear in 2+ of the given papers.
    """
    if len(paper_ids) < 2:
        return []

    placeholders = ",".join("?" * len(paper_ids))

    # Find shared dataset names
    rows = conn.execute(
        f"""SELECT name, COUNT(DISTINCT paper_id) as paper_count
            FROM datasets WHERE paper_id IN ({placeholders})
            GROUP BY name HAVING paper_count >= 2""",
        paper_ids,
    ).fetchall()

    shared_datasets = [r["name"] for r in rows]
    if not shared_datasets:
        return []

    results = []
    for ds_name in shared_datasets:
        # Get all metrics for this dataset across the given papers
        ds_placeholders = ",".join("?" * len(paper_ids))
        metrics = conn.execute(
            f"""SELECT m.name as metric_name, m.value, m.unit,
                       mt.name as method_name, p.title as paper_title, m.paper_id
                FROM metrics m
                JOIN datasets d ON m.dataset_id = d.id
                LEFT JOIN methods mt ON m.method_id = mt.id
                JOIN papers p ON m.paper_id = p.id
                WHERE d.name = ? AND m.paper_id IN ({ds_placeholders})
                ORDER BY m.name, m.value DESC""",
            [ds_name] + paper_ids,
        ).fetchall()

        results.append({
            "dataset": ds_name,
            "results": [
                {
                    "paper_id": r["paper_id"],
                    "paper_title": r["paper_title"],
                    "method": r["method_name"],
                    "metric": r["metric_name"],
                    "value": r["value"],
                    "unit": r["unit"],
                }
                for r in metrics
            ],
        })

    return results


def _get_llm_config(conn: sqlite3.Connection) -> dict:
    """Read LLM configuration from config table."""
    provider = conn.execute("SELECT value FROM config WHERE key = 'llm_provider'").fetchone()
    model = conn.execute("SELECT value FROM config WHERE key = 'llm_model'").fetchone()
    base_url_row = conn.execute("SELECT value FROM config WHERE key = 'llm_base_url'").fetchone()
    api_key_row = conn.execute("SELECT value FROM config WHERE key = 'llm_api_key'").fetchone()

    prov = provider["value"] if provider else "ollama"

    if base_url_row:
        base_url = base_url_row["value"]
    elif prov == "ollama":
        base_url = _get_ollama_url()
    else:
        raise ValueError("llm_base_url is required when llm_provider is 'openai_compat'")

    return {
        "provider": prov,
        "model": model["value"] if model else "qwen3.5:27b",
        "base_url": base_url,
        "api_key": api_key_row["value"] if api_key_row else None,
    }


def _llm_call(prompt: str, *, conn: sqlite3.Connection) -> str:
    """Call LLM to extract structured data. Supports Ollama and OpenAI-compatible APIs."""
    cfg = _get_llm_config(conn)

    if cfg["provider"] == "ollama":
        resp = httpx.post(
            f"{cfg['base_url']}/api/generate",
            json={"model": cfg["model"], "prompt": prompt, "stream": False, "format": "json"},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["response"]
    else:  # openai_compat
        headers = {}
        if cfg.get("api_key"):
            headers["Authorization"] = f"Bearer {cfg['api_key']}"
        resp = httpx.post(
            f"{cfg['base_url']}/v1/chat/completions",
            headers=headers,
            json={
                "model": cfg["model"],
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


_EXTRACT_PROMPT = """Extract structured information from this research paper text.
Return a JSON object with three arrays:

1. "methods": array of {{"name": "method name", "description": "brief description"}}
2. "datasets": array of {{"name": "dataset name", "description": "brief description"}}
3. "metrics": array of {{"metric": "metric name", "value": number, "unit": "unit string", "method": "method name", "dataset": "dataset name"}}

Only extract information that is explicitly stated. Do not infer or hallucinate.
For metrics, only include numeric values that are clearly reported results.

Text:
{text}

JSON:"""


_MAP_PROMPT = """Extract structured information from this text chunk ({chunk_index}/{total_chunks}).
Return a JSON object with three arrays:

1. "methods": array of {{"name": "method name", "description": "brief description", "surface_forms": ["name1", "alias1", ...]}}
2. "datasets": array of {{"name": "dataset name", "description": "brief description", "surface_forms": ["name1", "alias1", ...]}}
3. "metrics": array of {{"metric": "metric name", "value": number, "unit": "unit string", "method": "method name", "dataset": "dataset name"}}

For surface_forms, include ALL names/aliases used to refer to the entity in this chunk
(e.g., ["our method", "CNN-LSTM", "the proposed approach"]).

Only extract information that is explicitly stated. Do not infer or hallucinate.
For metrics, only include numeric values that are clearly reported results.

Text:
{text}

JSON:"""


def _map_extract(
    chunk_id: int,
    chunk_text: str,
    chunk_index: int,
    total_chunks: int,
    conn: sqlite3.Connection,
) -> dict:
    """Extract structured facts from a single chunk."""
    prompt = _MAP_PROMPT.format(
        text=chunk_text,
        chunk_index=chunk_index + 1,
        total_chunks=total_chunks,
    )
    raw = _llm_call(prompt, conn=conn)
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned invalid JSON for chunk {chunk_id}: {e}") from e
    for item in result.get("methods", []):
        item["chunk_id"] = chunk_id
    for item in result.get("datasets", []):
        item["chunk_id"] = chunk_id
    for item in result.get("metrics", []):
        item["chunk_id"] = chunk_id
    return result


def extract_structure(
    conn: sqlite3.Connection,
    paper_id: int,
) -> dict:
    """Extract methods, datasets, and metrics from a paper's chunks using LLM."""
    # Get paper chunks
    paper = conn.execute("SELECT id, title FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if not paper:
        return {"error": f"Paper {paper_id} not found"}

    # Get chunks linked to this paper via source_uri
    chunks = conn.execute(
        """SELECT c.id, c.content FROM chunks c
           JOIN papers p ON c.source_uri = (
               SELECT source_uri FROM chunks WHERE id = p.abstract_chunk_id LIMIT 1
           )
           WHERE p.id = ?""",
        (paper_id,),
    ).fetchall()

    if not chunks:
        # Fallback: try to get chunks via any source_uri linked to the paper
        chunks = conn.execute(
            """SELECT c.id, c.content FROM chunks c
               WHERE c.source_uri IN (
                   SELECT DISTINCT source_uri FROM chunks
                   WHERE id IN (SELECT abstract_chunk_id FROM papers WHERE id = ?)
               )""",
            (paper_id,),
        ).fetchall()

    if not chunks:
        return {"error": f"No chunks found for paper {paper_id}"}

    # Concatenate chunk content (limit to avoid token overflow)
    full_text = "\n\n".join(r["content"] for r in chunks)[:8000]

    prompt = _EXTRACT_PROMPT.format(text=full_text)
    try:
        raw = _llm_call(prompt, conn=conn)
    except Exception as e:
        return {"error": f"LLM extraction failed: {e}"}

    try:
        extracted = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "LLM returned invalid JSON", "raw": raw}

    methods_added = 0
    datasets_added = 0
    metrics_added = 0

    # Record methods
    method_map = {}  # name -> method_id
    for m in extracted.get("methods", []):
        name = m.get("name", "").strip()
        if name:
            result = record_method(conn, name, paper_id, m.get("description"))
            method_map[name] = result["method_id"]
            methods_added += 1

    # Record datasets
    dataset_map = {}  # name -> dataset_id
    for d in extracted.get("datasets", []):
        name = d.get("name", "").strip()
        if name:
            result = record_dataset(conn, name, paper_id, d.get("description"))
            dataset_map[name] = result["dataset_id"]
            datasets_added += 1

    # Record metrics
    for met in extracted.get("metrics", []):
        metric_name = met.get("metric", "").strip()
        value = met.get("value")
        if metric_name and value is not None:
            try:
                value = float(value)
            except (ValueError, TypeError):
                continue
            method_id = method_map.get(met.get("method", ""))
            dataset_id = dataset_map.get(met.get("dataset", ""))
            record_metric(
                conn, metric_name, value, paper_id,
                method_id=method_id, dataset_id=dataset_id,
                unit=met.get("unit"),
            )
            metrics_added += 1

    return {
        "paper_id": paper_id,
        "methods_added": methods_added,
        "datasets_added": datasets_added,
        "metrics_added": metrics_added,
    }
