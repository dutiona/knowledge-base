"""Structured extraction: methods, datasets, metrics from paper chunks."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from .embeddings import _get_ollama_url

logger = logging.getLogger(__name__)


def record_method(
    conn: sqlite3.Connection,
    name: str,
    paper_id: int,
    description: str | None = None,
    chunk_id: int | None = None,
    *,
    commit: bool = True,
) -> dict:
    """Record or update a method for a paper."""
    conn.execute(
        """INSERT INTO methods (name, paper_id, description, chunk_id)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(name, paper_id)
           DO UPDATE SET description = excluded.description, chunk_id = excluded.chunk_id""",
        (name, paper_id, description, chunk_id),
    )
    if commit:
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
    *,
    commit: bool = True,
) -> dict:
    """Record or update a dataset for a paper."""
    conn.execute(
        """INSERT INTO datasets (name, paper_id, description, chunk_id)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(name, paper_id)
           DO UPDATE SET description = excluded.description, chunk_id = excluded.chunk_id""",
        (name, paper_id, description, chunk_id),
    )
    if commit:
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
    *,
    commit: bool = True,
) -> dict:
    """Record a metric value."""
    cursor = conn.execute(
        """INSERT INTO metrics (name, value, unit, dataset_id, method_id, paper_id, chunk_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (name, value, unit, dataset_id, method_id, paper_id, chunk_id),
    )
    if commit:
        conn.commit()
    return {"metric_id": cursor.lastrowid}


def get_methods(conn: sqlite3.Connection, paper_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name, description, chunk_id FROM methods WHERE paper_id = ?",
        (paper_id,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "chunk_id": r["chunk_id"],
        }
        for r in rows
    ]


def get_datasets(conn: sqlite3.Connection, paper_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name, description, chunk_id FROM datasets WHERE paper_id = ?",
        (paper_id,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "chunk_id": r["chunk_id"],
        }
        for r in rows
    ]


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

        results.append(
            {
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
            }
        )

    return results


def _get_llm_config(conn: sqlite3.Connection) -> dict:
    """Read LLM configuration from config table."""
    provider = conn.execute(
        "SELECT value FROM config WHERE key = 'llm_provider'"
    ).fetchone()
    model = conn.execute("SELECT value FROM config WHERE key = 'llm_model'").fetchone()
    base_url_row = conn.execute(
        "SELECT value FROM config WHERE key = 'llm_base_url'"
    ).fetchone()
    api_key_row = conn.execute(
        "SELECT value FROM config WHERE key = 'llm_api_key'"
    ).fetchone()

    prov = provider["value"] if provider else "ollama"

    if base_url_row:
        base_url = base_url_row["value"]
    elif prov == "ollama":
        base_url = _get_ollama_url()
    else:
        raise ValueError(
            "llm_base_url is required when llm_provider is 'openai_compat'"
        )

    return {
        "provider": prov,
        "model": model["value"] if model else "qwen3.5:27b",
        "base_url": base_url.rstrip("/").removesuffix("/v1")
        if prov == "openai_compat"
        else base_url.rstrip("/"),
        "api_key": api_key_row["value"] if api_key_row else None,
    }


_THINK_TAG_RE = re.compile(r"<(think(?:ing)?)>.*?</\1>", re.DOTALL)

_SYSTEM_JSON_DIRECTIVE = (
    "Respond directly with valid JSON. "
    "Output only the JSON object, with no preamble, tags, or commentary."
)


def _strip_think_tags(text: str) -> str:
    """Strip reasoning/thinking tags from the preamble/trailer of LLM responses.

    Only strips tags outside the JSON payload to avoid corrupting literal
    <think> text inside JSON string fields.
    """
    # Find the start of JSON content
    json_start = -1
    for i, ch in enumerate(text):
        if ch in ("{", "["):
            json_start = i
            break

    if json_start == -1:
        # No JSON found — strip tags from entire text
        stripped = _THINK_TAG_RE.sub("", text).strip()
    else:
        # Strip tags only from preamble before JSON
        preamble = text[:json_start]
        json_body = text[json_start:]
        stripped = (_THINK_TAG_RE.sub("", preamble) + json_body).strip()

    if stripped != text.strip():
        logger.debug(
            "Stripped thinking tags from LLM response (%d → %d chars)",
            len(text),
            len(stripped),
        )
    return stripped


def _llm_call(
    prompt: str,
    *,
    conn: sqlite3.Connection | None = None,
    cfg: dict | None = None,
) -> str:
    """Call LLM to extract structured data. Supports Ollama and OpenAI-compatible APIs.

    Accepts either a ``conn`` (reads config from DB) or a pre-read ``cfg`` dict.
    The ``cfg`` path is preferred in hot loops to avoid threading issues.
    """
    if cfg is None:
        if conn is None:
            raise ValueError("Either conn or cfg must be provided to _llm_call")
        cfg = _get_llm_config(conn)

    if cfg["provider"] == "ollama":
        resp = httpx.post(
            f"{cfg['base_url']}/api/generate",
            json={
                "model": cfg["model"],
                "prompt": prompt,
                "system": _SYSTEM_JSON_DIRECTIVE,
                "stream": False,
                "format": "json",
            },
            timeout=120,
        )
        resp.raise_for_status()
        raw = resp.json()["response"]
    else:  # openai_compat
        headers = {}
        if cfg.get("api_key"):
            headers["Authorization"] = f"Bearer {cfg['api_key']}"
        resp = httpx.post(
            f"{cfg['base_url']}/v1/chat/completions",
            headers=headers,
            json={
                "model": cfg["model"],
                "messages": [
                    {"role": "system", "content": _SYSTEM_JSON_DIRECTIVE},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout=120,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]

    raw = _strip_think_tags(raw)

    if not raw:
        raise ValueError("LLM returned empty response (possible thinking-mode issue)")

    return raw


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
    conn_or_cfg: sqlite3.Connection | dict,
) -> dict:
    """Extract structured facts from a single chunk.

    ``conn_or_cfg`` accepts either a Connection (legacy) or a pre-read config
    dict (thread-safe path used by the parallel map phase).
    """
    prompt = _MAP_PROMPT.format(
        text=chunk_text,
        chunk_index=chunk_index + 1,
        total_chunks=total_chunks,
    )
    if isinstance(conn_or_cfg, dict):
        raw = _llm_call(prompt, cfg=conn_or_cfg)
    else:
        raw = _llm_call(prompt, conn=conn_or_cfg)
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned invalid JSON for chunk {chunk_id}: {e}") from e
    for item in result.get("methods") or []:
        item["chunk_id"] = chunk_id
    for item in result.get("datasets") or []:
        item["chunk_id"] = chunk_id
    for item in result.get("metrics") or []:
        item["chunk_id"] = chunk_id
    return result


def _collect_entity_mentions(all_extractions: list[dict]) -> list[dict]:
    """Collect all entity mentions from map results for resolution."""
    seen = set()
    mentions = []
    for extraction in all_extractions:
        for entity_type in ("methods", "datasets"):
            for item in extraction.get(entity_type) or []:
                name = (item.get("name") or "").strip()
                if not name:
                    continue
                key = (name.lower(), entity_type.rstrip("s"))
                if key not in seen:
                    seen.add(key)
                    mentions.append(
                        {
                            "name": name,
                            "type": entity_type.rstrip("s"),
                            "surface_forms": item.get("surface_forms") or [name],
                            "chunk_id": item.get("chunk_id"),
                            "description": item.get("description") or "",
                        }
                    )
                else:
                    for m in mentions:
                        if m["name"].lower() == name.lower() and m[
                            "type"
                        ] == entity_type.rstrip("s"):
                            for sf in item.get("surface_forms") or []:
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


def _resolve_entities(
    all_extractions: list[dict],
    conn_or_cfg: sqlite3.Connection | dict,
) -> dict:
    """Merge entities across chunks by resolving aliases."""
    entity_list = _collect_entity_mentions(all_extractions)
    if not entity_list:
        return {"groups": []}

    prompt = _RESOLVE_PROMPT.format(entities=json.dumps(entity_list, indent=2))
    if isinstance(conn_or_cfg, dict):
        raw = _llm_call(prompt, cfg=conn_or_cfg)
    else:
        raw = _llm_call(prompt, conn=conn_or_cfg)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Entity resolution returned invalid JSON: {e}") from e


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
    try:
        _clear_previous_extraction(conn, paper_id)

        # Build canonical name lookup from resolution groups — keyed by (name, type)
        surface_to_canonical = {}
        for group in resolution.get("groups") or []:
            canon = group.get("canonical")
            if not canon:
                logger.warning(
                    "Skipping resolution group missing 'canonical' key: %s", group
                )
                continue
            etype = group.get("type", "method")
            for member in group.get("members") or []:
                surface_to_canonical[(member.lower(), etype)] = canon

        # Collect all unique entities and their mentions — keyed by (canonical, type)
        entity_data = defaultdict(
            lambda: {
                "type": None,
                "description": None,
                "description_chunk_id": None,
                "mentions": [],
            }
        )
        for extraction in map_results:
            for entity_type_plural in ("methods", "datasets"):
                etype = entity_type_plural.rstrip("s")
                for item in extraction.get(entity_type_plural) or []:
                    name = (item.get("name") or "").strip()
                    if not name:
                        continue
                    canonical = surface_to_canonical.get((name.lower(), etype), name)
                    entity_data[(canonical, etype)]["type"] = etype
                    if item.get("description"):
                        entity_data[(canonical, etype)]["description"] = item[
                            "description"
                        ]
                        entity_data[(canonical, etype)]["description_chunk_id"] = (
                            item.get("chunk_id")
                        )
                    for sf in item.get("surface_forms") or [name]:
                        entity_data[(canonical, etype)]["mentions"].append(
                            {
                                "surface_form": sf,
                                "chunk_id": item.get("chunk_id"),
                            }
                        )

        # Insert entities and mentions
        entity_id_map = {}
        for (canonical, etype), data in entity_data.items():
            cursor = conn.execute(
                "INSERT OR IGNORE INTO entities (canonical_name, entity_type, paper_id, description) VALUES (?, ?, ?, ?)",
                (canonical, etype, paper_id, data["description"]),
            )
            eid = cursor.lastrowid
            if not eid:
                eid = conn.execute(
                    "SELECT id FROM entities WHERE canonical_name = ? AND entity_type = ? AND paper_id = ?",
                    (canonical, etype, paper_id),
                ).fetchone()["id"]
            entity_id_map[(canonical, etype)] = eid
            seen = set()
            for mention in data["mentions"]:
                sf = mention["surface_form"]
                cid = mention.get("chunk_id")
                key = (sf, cid)
                if key not in seen and cid:
                    conn.execute(
                        "INSERT OR IGNORE INTO entity_mentions (entity_id, surface_form, chunk_id) VALUES (?, ?, ?)",
                        (eid, sf, cid),
                    )
                    seen.add(key)

        # Write to methods/datasets tables
        method_map = {}
        dataset_map = {}
        methods_added = 0
        datasets_added = 0

        for (canonical, etype), data in entity_data.items():
            first_chunk_id = next(
                (m["chunk_id"] for m in data["mentions"] if m.get("chunk_id")), None
            )
            # Prefer the chunk that provided the description for provenance
            # consistency (#39); fall back to first-mention chunk.
            chunk_id = data.get("description_chunk_id") or first_chunk_id
            if etype == "method":
                result = record_method(
                    conn,
                    canonical,
                    paper_id,
                    data["description"],
                    chunk_id,
                    commit=False,
                )
                method_map[canonical] = result["method_id"]
                methods_added += 1
            elif etype == "dataset":
                result = record_dataset(
                    conn,
                    canonical,
                    paper_id,
                    data["description"],
                    chunk_id,
                    commit=False,
                )
                dataset_map[canonical] = result["dataset_id"]
                datasets_added += 1

        # Map surface forms to method/dataset IDs for metric attribution
        canonical_to_members: dict[str, list[str]] = {}
        for group in resolution.get("groups") or []:
            canon = group.get("canonical")
            if canon:
                canonical_to_members[canon] = group.get("members") or []

        for canonical, mid in list(method_map.items()):
            for member in canonical_to_members.get(canonical, []):
                method_map[member] = mid
        for canonical, did in list(dataset_map.items()):
            for member in canonical_to_members.get(canonical, []):
                dataset_map[member] = did

        # Write metrics
        metrics_added = 0
        for extraction in map_results:
            for met in extraction.get("metrics") or []:
                metric_name = (met.get("metric") or "").strip()
                value = met.get("value")
                if not metric_name or value is None:
                    continue
                try:
                    value = float(value)
                except (ValueError, TypeError):
                    continue
                method_name = met.get("method") or ""
                dataset_name = met.get("dataset") or ""
                # Try both method and dataset lookups for canonical resolution
                canonical_method = surface_to_canonical.get(
                    (method_name.lower(), "method"), method_name
                )
                canonical_dataset = surface_to_canonical.get(
                    (dataset_name.lower(), "dataset"), dataset_name
                )
                method_id = method_map.get(canonical_method)
                dataset_id = dataset_map.get(canonical_dataset)
                record_metric(
                    conn,
                    metric_name,
                    value,
                    paper_id,
                    method_id=method_id,
                    dataset_id=dataset_id,
                    unit=met.get("unit"),
                    chunk_id=met.get("chunk_id"),
                    commit=False,
                )
                metrics_added += 1

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        "methods_added": methods_added,
        "datasets_added": datasets_added,
        "metrics_added": metrics_added,
        "entities_resolved": len(entity_data),
    }


AVG_SECONDS_PER_CHUNK = 4
_MAX_WORKERS_LIMIT = 32


def _get_paper_chunks(conn: sqlite3.Connection, paper_id: int) -> list[dict]:
    """Get chunks for a paper via paper_paths (includes all chunk types)."""
    from .papers import get_paper_chunks as _papers_get_paper_chunks

    return _papers_get_paper_chunks(conn, paper_id, include_figures=True)


def _extract_single_pass(
    conn: sqlite3.Connection, paper_id: int, chunks: list[dict]
) -> dict:
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

    try:
        _clear_previous_extraction(conn, paper_id)

        # Use first chunk_id for entity mention provenance
        first_chunk_id = chunks[0]["id"] if chunks else None

        method_map = {}
        methods_added = 0
        for m in extracted.get("methods") or []:
            name = (m.get("name") or "").strip()
            if name:
                result = record_method(
                    conn,
                    name,
                    paper_id,
                    m.get("description"),
                    first_chunk_id,
                    commit=False,
                )
                method_map[name] = result["method_id"]
                methods_added += 1
                # Populate entities table for get_entities_tool consistency
                conn.execute(
                    "INSERT OR IGNORE INTO entities (canonical_name, entity_type, paper_id, description) VALUES (?, ?, ?, ?)",
                    (name, "method", paper_id, m.get("description")),
                )
                if first_chunk_id:
                    eid = conn.execute(
                        "SELECT id FROM entities WHERE canonical_name = ? AND entity_type = 'method' AND paper_id = ?",
                        (name, paper_id),
                    ).fetchone()["id"]
                    conn.execute(
                        "INSERT OR IGNORE INTO entity_mentions (entity_id, surface_form, chunk_id) VALUES (?, ?, ?)",
                        (eid, name, first_chunk_id),
                    )

        dataset_map = {}
        datasets_added = 0
        for d in extracted.get("datasets") or []:
            name = (d.get("name") or "").strip()
            if name:
                result = record_dataset(
                    conn,
                    name,
                    paper_id,
                    d.get("description"),
                    first_chunk_id,
                    commit=False,
                )
                dataset_map[name] = result["dataset_id"]
                datasets_added += 1
                conn.execute(
                    "INSERT OR IGNORE INTO entities (canonical_name, entity_type, paper_id, description) VALUES (?, ?, ?, ?)",
                    (name, "dataset", paper_id, d.get("description")),
                )
                if first_chunk_id:
                    eid = conn.execute(
                        "SELECT id FROM entities WHERE canonical_name = ? AND entity_type = 'dataset' AND paper_id = ?",
                        (name, paper_id),
                    ).fetchone()["id"]
                    conn.execute(
                        "INSERT OR IGNORE INTO entity_mentions (entity_id, surface_form, chunk_id) VALUES (?, ?, ?)",
                        (eid, name, first_chunk_id),
                    )

        metrics_added = 0
        for met in extracted.get("metrics") or []:
            metric_name = (met.get("metric") or "").strip()
            value = met.get("value")
            if metric_name and value is not None:
                try:
                    value = float(value)
                except (ValueError, TypeError):
                    continue
                method_id = method_map.get(met.get("method") or "")
                dataset_id = dataset_map.get(met.get("dataset") or "")
                record_metric(
                    conn,
                    metric_name,
                    value,
                    paper_id,
                    method_id=method_id,
                    dataset_id=dataset_id,
                    unit=met.get("unit"),
                    chunk_id=first_chunk_id,
                    commit=False,
                )
                metrics_added += 1

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        "paper_id": paper_id,
        "methods_added": methods_added,
        "datasets_added": datasets_added,
        "metrics_added": metrics_added,
    }


def _extract_map_reduce(
    conn: sqlite3.Connection,
    paper_id: int,
    chunks: list[dict],
    on_progress: Callable[[str], None] | None = None,
    max_workers: int = 1,
) -> dict:
    """Map-reduce path for long documents.

    ``max_workers`` controls the number of concurrent LLM calls during the map
    phase.  Defaults to 1 (sequential, backward-compatible).  Set higher to
    match your LLM server's parallel capacity (e.g. Ollama
    ``OLLAMA_NUM_PARALLEL``, or an OpenAI-compatible endpoint's rate limit).
    """
    cfg = _get_llm_config(conn)
    effective_workers = min(max(max_workers, 1), len(chunks), _MAX_WORKERS_LIMIT)

    # Phase 1: Map
    map_results: list[tuple[int, dict]] = []
    errors: list[dict] = []
    phase_start = time.monotonic()
    completed_count = 0

    if effective_workers == 1:
        # Sequential fast-path: no thread overhead, preserves original logging
        for i, chunk in enumerate(chunks):
            start = time.monotonic()
            try:
                result = _map_extract(
                    chunk["id"], chunk["content"], i, len(chunks), cfg
                )
                map_results.append((i, result))
            except Exception as e:
                errors.append(
                    {"chunk_id": chunk["id"], "chunk_index": i, "error": str(e)}
                )
                result = None

            elapsed = time.monotonic() - start
            completed_count += 1

            if result is not None:
                m = len(result.get("methods", []))
                d = len(result.get("datasets", []))
                mt = len(result.get("metrics", []))
                logger.info(
                    "Chunk %3d/%d (%.1f%%) - %.1fs - methods=%d, datasets=%d, metrics=%d",
                    i + 1,
                    len(chunks),
                    (i + 1) / len(chunks) * 100,
                    elapsed,
                    m,
                    d,
                    mt,
                )
            else:
                logger.info(
                    "Chunk %3d/%d (%.1f%%) - %.1fs - FAILED",
                    i + 1,
                    len(chunks),
                    (i + 1) / len(chunks) * 100,
                    elapsed,
                )

            if on_progress:
                on_progress(f"chunk {i + 1}/{len(chunks)}")

            total_elapsed = time.monotonic() - phase_start
            if completed_count % 5 == 0 or completed_count == len(chunks):
                avg = total_elapsed / completed_count
                remaining = avg * (len(chunks) - completed_count)
                logger.info(
                    "  avg %.1fs/chunk - revised ETA: %.0fmin remaining",
                    avg,
                    remaining / 60,
                )
    else:
        # Parallel path
        logger.info(
            "Starting parallel map phase: %d chunks, %d workers",
            len(chunks),
            effective_workers,
        )
        with ThreadPoolExecutor(max_workers=effective_workers) as executor:
            futures = {
                executor.submit(
                    _map_extract,
                    chunk["id"],
                    chunk["content"],
                    i,
                    len(chunks),
                    cfg,
                ): (i, chunk)
                for i, chunk in enumerate(chunks)
            }
            for future in as_completed(futures):
                i, chunk = futures[future]
                elapsed = time.monotonic() - phase_start  # wall-clock since start
                completed_count += 1
                try:
                    result = future.result()
                    map_results.append((i, result))
                    m = len(result.get("methods", []))
                    d = len(result.get("datasets", []))
                    mt = len(result.get("metrics", []))
                    logger.info(
                        "Chunk %3d/%d (%.1f%%) - methods=%d, datasets=%d, metrics=%d",
                        i + 1,
                        len(chunks),
                        completed_count / len(chunks) * 100,
                        m,
                        d,
                        mt,
                    )
                except Exception as e:
                    errors.append(
                        {"chunk_id": chunk["id"], "chunk_index": i, "error": str(e)}
                    )
                    logger.info(
                        "Chunk %3d/%d (%.1f%%) - FAILED: %s",
                        i + 1,
                        len(chunks),
                        completed_count / len(chunks) * 100,
                        e,
                    )

                if on_progress:
                    on_progress(f"chunk {completed_count}/{len(chunks)}")

                if completed_count % 5 == 0 or completed_count == len(chunks):
                    avg = elapsed / completed_count
                    remaining = avg * (len(chunks) - completed_count)
                    logger.info(
                        "  avg %.1fs/chunk (wall) - revised ETA: %.0fmin remaining",
                        avg,
                        remaining / 60,
                    )

    total_elapsed = time.monotonic() - phase_start

    # Sort map results by chunk index to ensure deterministic ordering
    map_results.sort(key=lambda x: x[0])
    ordered_results = [r for _, r in map_results]

    if not ordered_results:
        return {"error": "All chunks failed extraction", "errors": errors}

    # Phase 2: Resolve
    total_raw = sum(
        len(r.get("methods", [])) + len(r.get("datasets", [])) for r in ordered_results
    )
    logger.info(
        "Map phase complete: %d raw entities from %d chunks (%.1fs, %d workers)",
        total_raw,
        len(ordered_results),
        total_elapsed,
        effective_workers,
    )
    if on_progress:
        on_progress("resolving entities...")
    logger.info("Starting entity resolution...")
    resolve_start = time.monotonic()
    try:
        resolution = _resolve_entities(ordered_results, cfg)
    except Exception as e:
        logger.warning("Entity resolution failed, proceeding without: %s", e)
        resolution = {"groups": []}
    resolve_elapsed = time.monotonic() - resolve_start
    n_groups = len(resolution.get("groups", []))
    logger.info(
        "Entity resolution complete: %d -> %d canonical entities (%.1fs)",
        total_raw,
        n_groups,
        resolve_elapsed,
    )

    # Phase 3: Store
    if on_progress:
        on_progress("storing results...")
    result = _store_resolved(conn, paper_id, ordered_results, resolution)
    result["paper_id"] = paper_id
    result["chunks_processed"] = len(ordered_results)
    result["chunks_failed"] = len(errors)
    result["errors"] = errors
    result["extraction_seconds"] = total_elapsed
    result["resolution_seconds"] = resolve_elapsed
    return result


def estimate_extraction_time(conn: sqlite3.Connection, paper_id: int) -> dict:
    """Estimate extraction time for a paper without running it.

    Returns dict with total_chars, chunk_count, estimated_seconds, is_long.
    Returns {"error": ...} if paper or chunks not found.
    """
    paper = conn.execute("SELECT id FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if not paper:
        return {"error": f"Paper {paper_id} not found"}

    chunks = _get_paper_chunks(conn, paper_id)
    if not chunks:
        return {"error": f"No chunks found for paper {paper_id}"}

    total_chars = sum(len(c["content"]) for c in chunks)
    estimated_seconds = len(chunks) * AVG_SECONDS_PER_CHUNK
    return {
        "total_chars": total_chars,
        "chunk_count": len(chunks),
        "estimated_seconds": estimated_seconds,
        "is_long": total_chars > 8000,
        "chunks": chunks,
    }


def extract_structure(
    conn: sqlite3.Connection,
    paper_id: int,
    confirmed: bool = False,
    on_progress: Callable[[str], None] | None = None,
    *,
    max_workers: int = 1,
    _prefetched_chunks: list[dict] | None = None,
) -> dict:
    """Extract methods, datasets, and metrics from a paper's chunks using LLM.

    For short documents (<8000 chars), uses a single LLM call.
    For long documents, uses map-reduce with entity resolution.
    The caller (tool layer) is responsible for ETA confirmation flow.

    ``max_workers`` controls concurrent LLM calls in the map phase (default 1).
    """
    paper = conn.execute("SELECT id FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if not paper:
        return {"error": f"Paper {paper_id} not found"}

    chunks = _prefetched_chunks or _get_paper_chunks(conn, paper_id)
    if not chunks:
        return {"error": f"No chunks found for paper {paper_id}"}

    total_chars = sum(len(c["content"]) for c in chunks)

    # Fast path: short document
    if total_chars <= 8000:
        return _extract_single_pass(conn, paper_id, chunks)

    return _extract_map_reduce(
        conn, paper_id, chunks, on_progress=on_progress, max_workers=max_workers
    )


def _sanitize_url(url: str) -> str:
    """Strip query parameters and userinfo from a URL for safe logging."""
    from urllib.parse import urlparse, urlunparse

    try:
        parsed = urlparse(url)
        host = parsed.hostname or "unknown"
        try:
            port = f":{parsed.port}" if parsed.port else ""
        except ValueError:
            port = ""
        return urlunparse((parsed.scheme, f"{host}{port}", parsed.path, "", "", ""))
    except Exception:
        # Conservative fallback: strip everything that could contain credentials
        # Remove userinfo (before @), query params (?), and fragments (#)
        safe = url.split("://", 1)[-1] if "://" in url else url
        safe = safe.split("@")[-1]  # drop userinfo
        safe = safe.split("?")[0]  # drop query
        safe = safe.split("#")[0]  # drop fragment
        scheme = url.split("://", 1)[0] if "://" in url else "http"
        return f"{scheme}://{safe}"


_CONNECTIVITY_TIMEOUT = 3


def _test_llm_connectivity(
    provider: str, base_url: str, api_key: str | None = None
) -> dict:
    """Probe LLM endpoint reachability. Returns advisory status, never raises."""
    safe_url = _sanitize_url(base_url)
    try:
        if provider == "ollama":
            resp = httpx.get(f"{base_url}/api/tags", timeout=_CONNECTIVITY_TIMEOUT)
            resp.raise_for_status()
        else:
            headers: dict[str, str] = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            resp = httpx.get(
                f"{base_url}/v1/models",
                headers=headers,
                timeout=_CONNECTIVITY_TIMEOUT,
            )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    # Some providers don't implement /v1/models — fall back
                    fallback = httpx.get(
                        f"{base_url}/v1/chat/completions",
                        headers=headers,
                        timeout=_CONNECTIVITY_TIMEOUT,
                    )
                    # Any non-connection response (even 405) means reachable
                    if fallback.status_code in (401, 403):
                        raise httpx.HTTPStatusError(
                            f"HTTP {fallback.status_code}",
                            request=fallback.request,
                            response=fallback,
                        )
                else:
                    raise
        return {"reachable": True}
    except httpx.ConnectError:
        warning = f"Cannot connect to {safe_url}"
    except httpx.TimeoutException:
        warning = f"Connection timed out to {safe_url} ({_CONNECTIVITY_TIMEOUT}s)"
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            warning = "Authentication failed \u2014 check api_key"
        else:
            warning = f"Server returned HTTP {exc.response.status_code}"
    except Exception as exc:
        warning = f"Connectivity test failed: {type(exc).__name__}"
    logger.warning(
        "LLM connectivity test failed for %s at %s: %s", provider, safe_url, warning
    )
    return {"reachable": False, "warning": warning}


def configure_llm(
    conn: sqlite3.Connection,
    provider: str = "ollama",
    base_url: str | None = None,
    model: str = "qwen3.5:27b",
    api_key: str | None = None,
) -> dict:
    """Configure LLM provider settings.

    Note: ``api_key`` is stored as plain text in the SQLite config table.
    Acceptable for local-only use; consider system keyring integration
    (e.g. ``keyring`` library) before exposing this tool over a network.
    """
    if provider not in ("ollama", "openai_compat"):
        return {
            "error": f"Unknown provider: {provider}. Use 'ollama' or 'openai_compat'."
        }
    if provider == "openai_compat" and not base_url:
        return {"error": "base_url is required for openai_compat provider"}
    if base_url:
        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https"):
            return {"error": f"Invalid URL scheme: {parsed.scheme}. Use http or https."}

    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('llm_provider', ?)",
        (provider,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('llm_model', ?)", (model,)
    )
    if base_url:
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES ('llm_base_url', ?)",
            (base_url,),
        )
    elif provider == "ollama":
        # Clear stale base_url from previous provider to use auto-detection
        conn.execute("DELETE FROM config WHERE key = 'llm_base_url'")
    if api_key:
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES ('llm_api_key', ?)",
            (api_key,),
        )
    elif provider == "ollama":
        # Clear stale api_key — Ollama doesn't use auth
        conn.execute("DELETE FROM config WHERE key = 'llm_api_key'")
    conn.commit()
    cfg = _get_llm_config(conn)
    connectivity = _test_llm_connectivity(
        cfg["provider"], cfg["base_url"], cfg.get("api_key")
    )
    # Redact sensitive fields from response
    cfg.pop("api_key", None)
    cfg.update(connectivity)
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
        result.append(
            {
                "id": e["id"],
                "canonical_name": e["canonical_name"],
                "type": e["entity_type"],
                "description": e["description"],
                "mentions": [
                    {
                        "surface_form": m["surface_form"],
                        "chunk_id": m["chunk_id"],
                        "confidence": m["confidence"],
                    }
                    for m in mentions
                ],
            }
        )
    return result
