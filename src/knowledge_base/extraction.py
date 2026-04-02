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

from .exceptions import ExtractionError, NotFoundError
from .llm import _get_llm_config, _llm_call

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------


__all__ = [
    "AVG_SECONDS_PER_CHUNK",
    "MAX_ENTITIES_PER_EXTRACTION",
    "MAX_METRICS_PER_EXTRACTION",
    "SINGLE_PASS_CHAR_LIMIT",
    "_validate_extraction",
    "_validate_resolution",
    "compare_papers",
    "estimate_extraction_time",
    "extract_structure",
    "get_datasets",
    "get_entities",
    "get_methods",
    "get_metrics",
    "record_dataset",
    "record_method",
    "record_metric",
]

# --- Validation constants ---------------------------------------------------

MAX_ENTITY_NAME_LEN = 200
MAX_DESCRIPTION_LEN = 2000
MAX_ENTITIES_PER_EXTRACTION = 50
MAX_METRICS_PER_EXTRACTION = 200

# Control-character pattern (keep tabs, newlines, carriage returns)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize_str(s: str, max_len: int) -> str:
    """Strip control characters and truncate."""
    return _CONTROL_CHAR_RE.sub("", s)[:max_len]


def _validate_extraction(data: object) -> dict:
    """Validate and sanitize LLM extraction output.

    Raises ``ValueError`` for structurally invalid output (non-dict or
    non-list arrays).  Silently drops items with wrong field types and
    truncates overlong strings.
    """
    if not isinstance(data, dict):
        raise ValueError(
            f"LLM extraction output: expected dict, got {type(data).__name__}"
        )

    cleaned: dict[str, list] = {"methods": [], "datasets": [], "metrics": []}

    for key in ("methods", "datasets"):
        items = data.get(key) or []
        if not isinstance(items, list):
            raise ValueError(
                f"LLM extraction output['{key}']: expected list, got {type(items).__name__}"
            )
        for item in items[:MAX_ENTITIES_PER_EXTRACTION]:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            clean_item = {"name": _sanitize_str(name.strip(), MAX_ENTITY_NAME_LEN)}
            desc = item.get("description")
            if isinstance(desc, str):
                clean_item["description"] = _sanitize_str(desc, MAX_DESCRIPTION_LEN)
            # Preserve surface_forms / chunk_id if present
            if "surface_forms" in item and isinstance(item["surface_forms"], list):
                clean_item["surface_forms"] = [
                    _sanitize_str(sf, MAX_ENTITY_NAME_LEN)
                    for sf in item["surface_forms"]
                    if isinstance(sf, str)
                ]
            if "chunk_id" in item:
                clean_item["chunk_id"] = item["chunk_id"]
            cleaned[key].append(clean_item)

    metrics = data.get("metrics") or []
    if not isinstance(metrics, list):
        raise ValueError(
            f"LLM extraction output['metrics']: expected list, got {type(metrics).__name__}"
        )
    for met in metrics[:MAX_METRICS_PER_EXTRACTION]:
        if not isinstance(met, dict):
            continue
        metric_name = met.get("metric")
        value = met.get("value")
        if not isinstance(metric_name, str) or not metric_name.strip():
            continue
        try:
            value = float(value)
        except (ValueError, TypeError):
            continue
        clean_met: dict = {
            "metric": _sanitize_str(metric_name.strip(), MAX_ENTITY_NAME_LEN),
            "value": value,
        }
        if isinstance(met.get("unit"), str):
            clean_met["unit"] = _sanitize_str(met["unit"], MAX_ENTITY_NAME_LEN)
        if isinstance(met.get("method"), str):
            clean_met["method"] = _sanitize_str(met["method"], MAX_ENTITY_NAME_LEN)
        if isinstance(met.get("dataset"), str):
            clean_met["dataset"] = _sanitize_str(met["dataset"], MAX_ENTITY_NAME_LEN)
        if "chunk_id" in met:
            clean_met["chunk_id"] = met["chunk_id"]
        cleaned["metrics"].append(clean_met)

    return cleaned


def _validate_resolution(data: object) -> dict:
    """Validate and sanitize LLM entity resolution output."""
    if not isinstance(data, dict):
        raise ValueError(
            f"LLM resolution output: expected dict, got {type(data).__name__}"
        )

    groups_raw = data.get("groups") or []
    if not isinstance(groups_raw, list):
        return {"groups": []}

    clean_groups = []
    for group in groups_raw:
        if not isinstance(group, dict):
            continue
        canonical = group.get("canonical")
        if not isinstance(canonical, str) or not canonical.strip():
            continue
        clean_group: dict = {
            "canonical": _sanitize_str(canonical.strip(), MAX_ENTITY_NAME_LEN),
        }
        if isinstance(group.get("type"), str):
            clean_group["type"] = group["type"]
        members = group.get("members") or []
        if isinstance(members, list):
            clean_group["members"] = [
                _sanitize_str(m, MAX_ENTITY_NAME_LEN)
                for m in members
                if isinstance(m, str)
            ]
        clean_groups.append(clean_group)

    return {"groups": clean_groups}


_ENTITY_TABLES = frozenset({"methods", "datasets"})


def _record_entity(
    conn: sqlite3.Connection,
    table: str,
    name: str,
    paper_id: int,
    description: str | None = None,
    chunk_id: int | None = None,
    *,
    commit: bool = True,
    source: str = "user",
) -> int:
    """Insert-or-update a named entity (method/dataset) and return its ID."""
    if table not in _ENTITY_TABLES:
        raise ValueError(f"Invalid entity table: {table!r}")
    conn.execute(
        f"INSERT INTO {table} (name, paper_id, description, chunk_id, source)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(name, paper_id)"
        " DO UPDATE SET description = excluded.description, chunk_id = excluded.chunk_id, source = excluded.source",
        (name, paper_id, description, chunk_id, source),
    )
    if commit:
        conn.commit()
    row = conn.execute(
        f"SELECT id FROM {table} WHERE name = ? AND paper_id = ?", (name, paper_id)
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Failed to retrieve {table} entity after upsert")
    return row["id"]


def record_method(
    conn: sqlite3.Connection,
    name: str,
    paper_id: int,
    description: str | None = None,
    chunk_id: int | None = None,
    *,
    commit: bool = True,
    source: str = "user",
) -> dict:
    """Record or update a method for a paper."""
    eid = _record_entity(
        conn,
        "methods",
        name,
        paper_id,
        description,
        chunk_id,
        commit=commit,
        source=source,
    )
    return {"method_id": eid}


def record_dataset(
    conn: sqlite3.Connection,
    name: str,
    paper_id: int,
    description: str | None = None,
    chunk_id: int | None = None,
    *,
    commit: bool = True,
    source: str = "user",
) -> dict:
    """Record or update a dataset for a paper."""
    eid = _record_entity(
        conn,
        "datasets",
        name,
        paper_id,
        description,
        chunk_id,
        commit=commit,
        source=source,
    )
    return {"dataset_id": eid}


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
    source: str = "user",
) -> dict:
    """Record a metric value."""
    cursor = conn.execute(
        "INSERT INTO metrics (name, value, unit, dataset_id, method_id, paper_id, chunk_id, source)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (name, value, unit, dataset_id, method_id, paper_id, chunk_id, source),
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
        "SELECT m.id, m.name, m.value, m.unit, m.chunk_id,"
        " mt.name as method_name, d.name as dataset_name"
        " FROM metrics m"
        " LEFT JOIN methods mt ON m.method_id = mt.id"
        " LEFT JOIN datasets d ON m.dataset_id = d.id"
        " WHERE m.paper_id = ?",
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
        f"SELECT name, COUNT(DISTINCT paper_id) as paper_count"
        f" FROM datasets WHERE paper_id IN ({placeholders})"
        " GROUP BY name HAVING paper_count >= 2",
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
            f"SELECT m.name as metric_name, m.value, m.unit,"
            " mt.name as method_name, p.title as paper_title, m.paper_id"
            " FROM metrics m"
            " JOIN datasets d ON m.dataset_id = d.id"
            " LEFT JOIN methods mt ON m.method_id = mt.id"
            " JOIN papers p ON m.paper_id = p.id"
            f" WHERE d.name = ? AND m.paper_id IN ({ds_placeholders})"
            " ORDER BY m.name, m.value DESC",
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
    cfg: dict,
    *,
    client: httpx.Client | None = None,
) -> dict:
    """Extract structured facts from a single chunk.

    ``cfg`` is a pre-read LLM config dict (from ``_get_llm_config``).
    ``client`` is an optional connection-pooled httpx client for reuse.
    """
    prompt = _MAP_PROMPT.format(
        text=chunk_text,
        chunk_index=chunk_index + 1,
        total_chunks=total_chunks,
    )
    raw = _llm_call(prompt, cfg=cfg, client=client)
    try:
        result = _validate_extraction(json.loads(raw))
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
    cfg: dict,
    *,
    client: httpx.Client | None = None,
) -> dict:
    """Merge entities across chunks by resolving aliases."""
    entity_list = _collect_entity_mentions(all_extractions)
    if not entity_list:
        return {"groups": []}

    prompt = _RESOLVE_PROMPT.format(entities=json.dumps(entity_list, indent=2))
    raw = _llm_call(prompt, cfg=cfg, client=client)
    try:
        return _validate_resolution(json.loads(raw))
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
                "INSERT OR IGNORE INTO entities (canonical_name, entity_type, paper_id, description, source) VALUES (?, ?, ?, ?, ?)",
                (canonical, etype, paper_id, data["description"], "llm_extraction"),
            )
            eid = cursor.lastrowid
            if cursor.rowcount == 0:
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
                    source="llm_extraction",
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
                    source="llm_extraction",
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
                    source="llm_extraction",
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

# Character threshold: docs below this use single-pass extraction
SINGLE_PASS_CHAR_LIMIT = 8000


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
        raise ExtractionError(f"LLM extraction failed: {e}") from e
    try:
        extracted = _validate_extraction(json.loads(raw))
    except (json.JSONDecodeError, ValueError) as e:
        raise ExtractionError(str(e), raw=raw) from e

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
                    source="llm_extraction",
                )
                method_map[name] = result["method_id"]
                methods_added += 1
                # Populate entities table for get_entities_tool consistency
                conn.execute(
                    "INSERT OR IGNORE INTO entities (canonical_name, entity_type, paper_id, description, source) VALUES (?, ?, ?, ?, ?)",
                    (name, "method", paper_id, m.get("description"), "llm_extraction"),
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
                    source="llm_extraction",
                )
                dataset_map[name] = result["dataset_id"]
                datasets_added += 1
                conn.execute(
                    "INSERT OR IGNORE INTO entities (canonical_name, entity_type, paper_id, description, source) VALUES (?, ?, ?, ?, ?)",
                    (name, "dataset", paper_id, d.get("description"), "llm_extraction"),
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
                    source="llm_extraction",
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

    Uses ``ThreadPoolExecutor`` for both sequential and parallel modes —
    ``max_workers=1`` has negligible overhead compared to 4s+ LLM calls.
    A shared ``httpx.Client`` is used for connection pooling across workers.
    """
    cfg = _get_llm_config(conn)
    effective_workers = min(max(max_workers, 1), len(chunks), _MAX_WORKERS_LIMIT)

    # Phase 1: Map
    map_results: list[tuple[int, dict]] = []
    errors: list[dict] = []
    phase_start = time.monotonic()
    completed_count = 0

    logger.info(
        "Starting map phase: %d chunks, %d workers",
        len(chunks),
        effective_workers,
    )
    with (
        httpx.Client(
            limits=httpx.Limits(
                max_connections=effective_workers,
                max_keepalive_connections=effective_workers,
            ),
        ) as client,
        ThreadPoolExecutor(max_workers=effective_workers) as executor,
    ):
        submit_times: dict[int, float] = {}
        futures = {}
        for i, chunk in enumerate(chunks):
            submit_times[i] = time.monotonic()
            fut = executor.submit(
                _map_extract,
                chunk["id"],
                chunk["content"],
                i,
                len(chunks),
                cfg,
                client=client,
            )
            futures[fut] = (i, chunk)

        for future in as_completed(futures):
            i, chunk = futures[future]
            chunk_elapsed = time.monotonic() - submit_times[i]
            wall_elapsed = time.monotonic() - phase_start
            completed_count += 1
            try:
                result = future.result()
                map_results.append((i, result))
                m = len(result.get("methods", []))
                d = len(result.get("datasets", []))
                mt = len(result.get("metrics", []))
                logger.info(
                    "Chunk %3d/%d (%.1f%%) - %.1fs - methods=%d, datasets=%d, metrics=%d",
                    i + 1,
                    len(chunks),
                    completed_count / len(chunks) * 100,
                    chunk_elapsed,
                    m,
                    d,
                    mt,
                )
            except Exception as e:
                errors.append(
                    {"chunk_id": chunk["id"], "chunk_index": i, "error": str(e)}
                )
                logger.info(
                    "Chunk %3d/%d (%.1f%%) - %.1fs - FAILED: %s",
                    i + 1,
                    len(chunks),
                    completed_count / len(chunks) * 100,
                    chunk_elapsed,
                    e,
                )

            if on_progress:
                on_progress(f"chunk {completed_count}/{len(chunks)}")

            if completed_count % 5 == 0 or completed_count == len(chunks):
                avg = wall_elapsed / completed_count
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
        raise ExtractionError("All chunks failed extraction", errors=errors)

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
        raise NotFoundError(f"Paper {paper_id} not found")

    chunks = _get_paper_chunks(conn, paper_id)
    if not chunks:
        raise NotFoundError(f"No chunks found for paper {paper_id}")

    total_chars = sum(len(c["content"]) for c in chunks)
    estimated_seconds = len(chunks) * AVG_SECONDS_PER_CHUNK
    return {
        "total_chars": total_chars,
        "chunk_count": len(chunks),
        "estimated_seconds": estimated_seconds,
        "is_long": total_chars > SINGLE_PASS_CHAR_LIMIT,
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

    For short documents (<SINGLE_PASS_CHAR_LIMIT chars), uses a single LLM call.
    For long documents, uses map-reduce with entity resolution.
    The caller (tool layer) is responsible for ETA confirmation flow.

    ``max_workers`` controls concurrent LLM calls in the map phase (default 1).
    """
    paper = conn.execute("SELECT id FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if not paper:
        raise NotFoundError(f"Paper {paper_id} not found")

    chunks = _prefetched_chunks or _get_paper_chunks(conn, paper_id)
    if not chunks:
        raise NotFoundError(f"No chunks found for paper {paper_id}")

    total_chars = sum(len(c["content"]) for c in chunks)

    # Fast path: short document
    if total_chars <= SINGLE_PASS_CHAR_LIMIT:
        return _extract_single_pass(conn, paper_id, chunks)

    return _extract_map_reduce(
        conn, paper_id, chunks, on_progress=on_progress, max_workers=max_workers
    )


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
