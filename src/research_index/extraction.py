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

    if prov == "ollama":
        # Always auto-detect for Ollama — ignore stale base_url from previous provider
        base_url = _get_ollama_url()
    elif base_url_row:
        base_url = base_url_row["value"]
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


def _collect_entity_mentions(all_extractions: list[dict]) -> list[dict]:
    """Collect all entity mentions from map results for resolution."""
    seen = set()
    mentions = []
    for extraction in all_extractions:
        for entity_type in ("methods", "datasets"):
            for item in extraction.get(entity_type, []):
                name = item.get("name", "").strip()
                if not name:
                    continue
                key = (name.lower(), entity_type.rstrip("s"))
                if key not in seen:
                    seen.add(key)
                    mentions.append({
                        "name": name,
                        "type": entity_type.rstrip("s"),
                        "surface_forms": item.get("surface_forms", [name]),
                        "chunk_id": item.get("chunk_id"),
                        "description": item.get("description", ""),
                    })
                else:
                    for m in mentions:
                        if m["name"].lower() == item["name"].lower() and m["type"] == entity_type.rstrip("s"):
                            for sf in item.get("surface_forms", []):
                                if sf not in m["surface_forms"]:
                                    m["surface_forms"].append(sf)
                            break
    return mentions


_RESOLVE_PROMPT = """You are given a list of entity mentions extracted from different chunks of a document.
Group mentions that refer to the same real-world entity. Return a JSON object:

{{"groups": [
    {{"canonical": "best name for this entity", "type": "method|dataset", "members": ["name1", "alias1", ...]}},
    ...
]}}

Rules:
- Every mention name must appear in exactly one group's members list.
- The canonical name should be the most specific/formal name.
- Only group mentions that clearly refer to the same entity.
- If unsure, keep them separate.

Mentions:
{entities}

JSON:"""


def _resolve_entities(all_extractions: list[dict], conn: sqlite3.Connection) -> dict:
    """Merge entities across chunks by resolving aliases."""
    entity_list = _collect_entity_mentions(all_extractions)
    if not entity_list:
        return {"groups": []}

    prompt = _RESOLVE_PROMPT.format(entities=json.dumps(entity_list, indent=2))
    raw = _llm_call(prompt, conn=conn)
    return json.loads(raw)


def _clear_previous_extraction(conn: sqlite3.Connection, paper_id: int) -> None:
    """Delete previous extraction data for idempotency. FK-dependency order."""
    conn.execute("DELETE FROM metrics WHERE paper_id = ?", (paper_id,))
    conn.execute(
        "DELETE FROM entity_mentions WHERE entity_id IN (SELECT id FROM entities WHERE paper_id = ?)",
        (paper_id,),
    )
    conn.execute("DELETE FROM entities WHERE paper_id = ?", (paper_id,))
    conn.execute("DELETE FROM datasets WHERE paper_id = ?", (paper_id,))
    conn.execute("DELETE FROM methods WHERE paper_id = ?", (paper_id,))


def _store_resolved(
    conn: sqlite3.Connection,
    paper_id: int,
    map_results: list[dict],
    resolution: dict,
) -> dict:
    """Store resolved entities, methods, datasets, and metrics."""
    _clear_previous_extraction(conn, paper_id)

    # Build canonical name lookup from resolution groups
    surface_to_canonical = {}
    canonical_type = {}
    for group in resolution.get("groups", []):
        canon = group["canonical"]
        etype = group.get("type", "method")
        canonical_type[canon] = etype
        for member in group.get("members", []):
            surface_to_canonical[member.lower()] = canon

    # Collect all unique entities and their mentions
    entity_data = defaultdict(lambda: {"type": None, "description": None, "mentions": []})
    for extraction in map_results:
        for entity_type_plural in ("methods", "datasets"):
            etype = entity_type_plural.rstrip("s")
            for item in extraction.get(entity_type_plural, []):
                name = item.get("name", "").strip()
                if not name:
                    continue
                canonical = surface_to_canonical.get(name.lower(), name)
                entity_data[canonical]["type"] = etype
                if item.get("description"):
                    entity_data[canonical]["description"] = item["description"]
                for sf in item.get("surface_forms", [name]):
                    entity_data[canonical]["mentions"].append({
                        "surface_form": sf,
                        "chunk_id": item.get("chunk_id"),
                    })

    # Insert entities and mentions
    entity_id_map = {}
    for canonical, data in entity_data.items():
        cursor = conn.execute(
            "INSERT INTO entities (canonical_name, entity_type, paper_id, description) VALUES (?, ?, ?, ?)",
            (canonical, data["type"], paper_id, data["description"]),
        )
        eid = cursor.lastrowid
        entity_id_map[canonical] = eid
        seen = set()
        for mention in data["mentions"]:
            sf = mention["surface_form"]
            cid = mention.get("chunk_id")
            key = (sf, cid)
            if key not in seen and cid:
                conn.execute(
                    "INSERT INTO entity_mentions (entity_id, surface_form, chunk_id) VALUES (?, ?, ?)",
                    (eid, sf, cid),
                )
                seen.add(key)

    # Write to methods/datasets tables
    method_map = {}
    dataset_map = {}
    methods_added = 0
    datasets_added = 0

    for canonical, data in entity_data.items():
        if data["type"] == "method":
            result = record_method(conn, canonical, paper_id, data["description"])
            method_map[canonical] = result["method_id"]
            methods_added += 1
        elif data["type"] == "dataset":
            result = record_dataset(conn, canonical, paper_id, data["description"])
            dataset_map[canonical] = result["dataset_id"]
            datasets_added += 1

    # Map surface forms to method/dataset IDs for metric attribution
    for canonical, mid in list(method_map.items()):
        for group in resolution.get("groups", []):
            if group["canonical"] == canonical:
                for member in group.get("members", []):
                    method_map[member] = mid
    for canonical, did in list(dataset_map.items()):
        for group in resolution.get("groups", []):
            if group["canonical"] == canonical:
                for member in group.get("members", []):
                    dataset_map[member] = did

    # Write metrics
    metrics_added = 0
    for extraction in map_results:
        for met in extraction.get("metrics", []):
            metric_name = met.get("metric", "").strip()
            value = met.get("value")
            if not metric_name or value is None:
                continue
            try:
                value = float(value)
            except (ValueError, TypeError):
                continue
            method_name = met.get("method", "")
            dataset_name = met.get("dataset", "")
            canonical_method = surface_to_canonical.get(method_name.lower(), method_name)
            canonical_dataset = surface_to_canonical.get(dataset_name.lower(), dataset_name)
            method_id = method_map.get(canonical_method)
            dataset_id = dataset_map.get(canonical_dataset)
            record_metric(conn, metric_name, value, paper_id,
                          method_id=method_id, dataset_id=dataset_id,
                          unit=met.get("unit"))
            metrics_added += 1

    conn.commit()
    return {
        "methods_added": methods_added,
        "datasets_added": datasets_added,
        "metrics_added": metrics_added,
        "entities_resolved": len(entity_data),
    }


AVG_SECONDS_PER_CHUNK = 4


def _get_paper_chunks(conn: sqlite3.Connection, paper_id: int) -> list[dict]:
    """Get chunks for a paper via its source_uri."""
    # Find source_uri from the paper's linked chunks
    row = conn.execute(
        "SELECT source_uri FROM chunks WHERE id = (SELECT abstract_chunk_id FROM papers WHERE id = ?)",
        (paper_id,),
    ).fetchone()
    if row:
        source_uri = row["source_uri"]
    else:
        source_uri = None

    if not source_uri:
        return []

    chunks = conn.execute(
        "SELECT id, content FROM chunks WHERE source_uri = ? ORDER BY chunk_index",
        (source_uri,),
    ).fetchall()
    return [{"id": c["id"], "content": c["content"]} for c in chunks]


def _extract_single_pass(conn: sqlite3.Connection, paper_id: int, chunks: list[dict]) -> dict:
    """Fast path: single LLM call for short documents."""
    full_text = "\n\n".join(c["content"] for c in chunks)
    prompt = _EXTRACT_PROMPT.format(text=full_text)
    try:
        raw = _llm_call(prompt, conn=conn)
    except Exception as e:
        return {"error": f"LLM extraction failed: {e}"}
    try:
        extracted = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "LLM returned invalid JSON", "raw": raw}

    _clear_previous_extraction(conn, paper_id)

    # Use first chunk_id for entity mention provenance
    first_chunk_id = chunks[0]["id"] if chunks else None

    method_map = {}
    methods_added = 0
    for m in extracted.get("methods", []):
        name = m.get("name", "").strip()
        if name:
            result = record_method(conn, name, paper_id, m.get("description"))
            method_map[name] = result["method_id"]
            methods_added += 1
            # Populate entities table for get_entities_tool consistency
            cursor = conn.execute(
                "INSERT INTO entities (canonical_name, entity_type, paper_id, description) VALUES (?, ?, ?, ?)",
                (name, "method", paper_id, m.get("description")),
            )
            if first_chunk_id:
                conn.execute(
                    "INSERT INTO entity_mentions (entity_id, surface_form, chunk_id) VALUES (?, ?, ?)",
                    (cursor.lastrowid, name, first_chunk_id),
                )

    dataset_map = {}
    datasets_added = 0
    for d in extracted.get("datasets", []):
        name = d.get("name", "").strip()
        if name:
            result = record_dataset(conn, name, paper_id, d.get("description"))
            dataset_map[name] = result["dataset_id"]
            datasets_added += 1
            cursor = conn.execute(
                "INSERT INTO entities (canonical_name, entity_type, paper_id, description) VALUES (?, ?, ?, ?)",
                (name, "dataset", paper_id, d.get("description")),
            )
            if first_chunk_id:
                conn.execute(
                    "INSERT INTO entity_mentions (entity_id, surface_form, chunk_id) VALUES (?, ?, ?)",
                    (cursor.lastrowid, name, first_chunk_id),
                )

    metrics_added = 0
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
            record_metric(conn, metric_name, value, paper_id,
                          method_id=method_id, dataset_id=dataset_id, unit=met.get("unit"))
            metrics_added += 1

    conn.commit()
    return {"paper_id": paper_id, "methods_added": methods_added,
            "datasets_added": datasets_added, "metrics_added": metrics_added}


def _extract_map_reduce(conn: sqlite3.Connection, paper_id: int, chunks: list[dict]) -> dict:
    """Map-reduce path for long documents."""
    # Phase 1: Map
    map_results = []
    errors = []
    for i, chunk in enumerate(chunks):
        try:
            result = _map_extract(chunk["id"], chunk["content"], i, len(chunks), conn)
            map_results.append(result)
        except Exception as e:
            errors.append({"chunk_id": chunk["id"], "chunk_index": i, "error": str(e)})

    if not map_results:
        return {"error": "All chunks failed extraction", "errors": errors}

    # Phase 2: Resolve
    try:
        resolution = _resolve_entities(map_results, conn)
    except Exception:
        resolution = {"groups": []}

    # Phase 3: Store
    result = _store_resolved(conn, paper_id, map_results, resolution)
    result["paper_id"] = paper_id
    result["chunks_processed"] = len(map_results)
    result["chunks_failed"] = len(errors)
    result["errors"] = errors
    return result


def extract_structure(
    conn: sqlite3.Connection,
    paper_id: int,
    confirmed: bool = False,
) -> dict:
    """Extract methods, datasets, and metrics from a paper's chunks using LLM.

    For short documents (<8000 chars), uses a single LLM call.
    For long documents, uses map-reduce with entity resolution.
    Long documents require confirmation if estimated time > 2 minutes.
    """
    paper = conn.execute("SELECT id, title FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if not paper:
        return {"error": f"Paper {paper_id} not found"}

    chunks = _get_paper_chunks(conn, paper_id)
    if not chunks:
        return {"error": f"No chunks found for paper {paper_id}"}

    total_chars = sum(len(c["content"]) for c in chunks)

    # Fast path: short document
    if total_chars <= 8000:
        return _extract_single_pass(conn, paper_id, chunks)

    # Long document: ETA gate
    estimated_seconds = len(chunks) * AVG_SECONDS_PER_CHUNK
    if estimated_seconds > 120 and not confirmed:
        return {
            "warning": f"Extraction will take ~{estimated_seconds // 60}min for {len(chunks)} chunks",
            "estimated_seconds": estimated_seconds,
            "chunk_count": len(chunks),
            "confirm_required": True,
        }

    return _extract_map_reduce(conn, paper_id, chunks)


def configure_llm(
    conn: sqlite3.Connection,
    provider: str = "ollama",
    base_url: str | None = None,
    model: str = "qwen3.5:27b",
    api_key: str | None = None,
) -> dict:
    """Configure LLM provider settings."""
    if provider not in ("ollama", "openai_compat"):
        return {"error": f"Unknown provider: {provider}. Use 'ollama' or 'openai_compat'."}
    if provider == "openai_compat" and not base_url:
        return {"error": "base_url is required for openai_compat provider"}
    if base_url:
        from urllib.parse import urlparse
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https"):
            return {"error": f"Invalid URL scheme: {parsed.scheme}. Use http or https."}

    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('llm_provider', ?)", (provider,))
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('llm_model', ?)", (model,))
    if base_url:
        conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('llm_base_url', ?)", (base_url,))
    if api_key:
        conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('llm_api_key', ?)", (api_key,))
    conn.commit()
    cfg = _get_llm_config(conn)
    # Redact sensitive fields from response
    cfg.pop("api_key", None)
    return cfg


def get_entities(conn: sqlite3.Connection, paper_id: int) -> list[dict]:
    """List resolved entities for a paper with their mentions."""
    entities = conn.execute(
        "SELECT id, canonical_name, entity_type, description FROM entities WHERE paper_id = ?",
        (paper_id,),
    ).fetchall()

    result = []
    for e in entities:
        mentions = conn.execute(
            "SELECT surface_form, chunk_id, confidence FROM entity_mentions WHERE entity_id = ?",
            (e["id"],),
        ).fetchall()
        result.append({
            "id": e["id"],
            "canonical_name": e["canonical_name"],
            "type": e["entity_type"],
            "description": e["description"],
            "mentions": [{"surface_form": m["surface_form"], "chunk_id": m["chunk_id"],
                          "confidence": m["confidence"]} for m in mentions],
        })
    return result
