"""Wrapper-level tests for the extraction route MCP tools.

These exercise the *server-only* logic of each tool wrapper in
``knowledge_base.routes.extraction`` — JSON shaping, error mapping, and the
rich branching in ``extract_structure_tool`` — NOT the LLM/extraction
internals (those live in ``tests/test_extraction.py``).

``extract_figures_tool`` already has wrapper tests in ``tests/test_vision.py``
and is deliberately not retested here.

Patching strategy:
* ``_get_conn`` is patched at the route module so wrappers use the test DB.
* Network-bound helpers (``estimate_extraction_time``, ``extract_structure``,
  ``submit_job``, and the connectivity probe behind ``configure_llm``) are
  mocked at the namespace where they are *looked up*. Pure-SQLite domain
  functions run for real against ``kb_conn``.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from knowledge_base.exceptions import ExtractionError, NotFoundError
from knowledge_base.extraction import _MAX_WORKERS_LIMIT
from knowledge_base.papers import register_paper
from knowledge_base.routes.extraction import (
    compare_papers_tool,
    configure_llm_tool,
    configure_omniparser_tool,
    configure_vision_tool,
    extract_structure_tool,
    get_entities_tool,
    record_dataset_tool,
    record_method_tool,
    record_metric_tool,
)

ROUTE = "knowledge_base.routes.extraction"


def _patch_conn(kb_conn):
    """Patch the route's ``_get_conn`` to return the test connection."""
    return patch(f"{ROUTE}._get_conn", return_value=kb_conn)


# ---------------------------------------------------------------------------
# record_method_tool / record_dataset_tool / record_metric_tool (real DB)
# ---------------------------------------------------------------------------


def test_record_method_tool_returns_method_id(kb_conn):
    paper_id = register_paper(kb_conn, "A Paper")["paper_id"]

    with _patch_conn(kb_conn):
        result = json.loads(record_method_tool("Transformer", paper_id, description="attn"))

    assert "method_id" in result
    assert isinstance(result["method_id"], int)
    # Persisted under the right paper.
    row = kb_conn.execute("SELECT name, description FROM methods WHERE id = ?", (result["method_id"],)).fetchone()
    assert row["name"] == "Transformer"
    assert row["description"] == "attn"


def test_record_dataset_tool_returns_dataset_id(kb_conn):
    paper_id = register_paper(kb_conn, "A Paper")["paper_id"]

    with _patch_conn(kb_conn):
        result = json.loads(record_dataset_tool("ImageNet", paper_id))

    assert "dataset_id" in result
    assert isinstance(result["dataset_id"], int)
    row = kb_conn.execute("SELECT name FROM datasets WHERE id = ?", (result["dataset_id"],)).fetchone()
    assert row["name"] == "ImageNet"


def test_record_metric_tool_minimal(kb_conn):
    paper_id = register_paper(kb_conn, "A Paper")["paper_id"]

    with _patch_conn(kb_conn):
        result = json.loads(record_metric_tool("accuracy", 0.92, paper_id))

    assert "metric_id" in result
    assert isinstance(result["metric_id"], int)


def test_record_metric_tool_with_method_dataset_unit(kb_conn):
    """Exercise the optional method_id / dataset_id / unit args."""
    paper_id = register_paper(kb_conn, "A Paper")["paper_id"]

    with _patch_conn(kb_conn):
        method_id = json.loads(record_method_tool("ResNet-50", paper_id))["method_id"]
        dataset_id = json.loads(record_dataset_tool("ImageNet", paper_id))["dataset_id"]
        result = json.loads(
            record_metric_tool(
                "accuracy",
                0.76,
                paper_id,
                method_id=method_id,
                dataset_id=dataset_id,
                unit="%",
            )
        )

    metric_id = result["metric_id"]
    row = kb_conn.execute(
        "SELECT value, unit, method_id, dataset_id FROM metrics WHERE id = ?",
        (metric_id,),
    ).fetchone()
    assert row["value"] == pytest.approx(0.76)
    assert row["unit"] == "%"
    assert row["method_id"] == method_id
    assert row["dataset_id"] == dataset_id


# ---------------------------------------------------------------------------
# compare_papers_tool (real DB) — pass-through, JSON structure
# ---------------------------------------------------------------------------


def test_compare_papers_tool_no_shared_datasets(kb_conn):
    """Two papers with no shared dataset → valid JSON (empty list)."""
    p1 = register_paper(kb_conn, "Paper 1")["paper_id"]
    p2 = register_paper(kb_conn, "Paper 2")["paper_id"]

    with _patch_conn(kb_conn):
        result = json.loads(compare_papers_tool([p1, p2]))

    assert result == []


def test_compare_papers_tool_shared_dataset(kb_conn):
    """Two papers reporting on the same dataset → grouped structure."""
    p1 = register_paper(kb_conn, "Paper 1")["paper_id"]
    p2 = register_paper(kb_conn, "Paper 2")["paper_id"]

    with _patch_conn(kb_conn):
        d1 = json.loads(record_dataset_tool("ImageNet", p1))["dataset_id"]
        d2 = json.loads(record_dataset_tool("ImageNet", p2))["dataset_id"]
        record_metric_tool("accuracy", 0.70, p1, dataset_id=d1)
        record_metric_tool("accuracy", 0.80, p2, dataset_id=d2)
        result = json.loads(compare_papers_tool([p1, p2]))

    assert isinstance(result, list)
    assert len(result) == 1
    group = result[0]
    assert group["dataset"] == "ImageNet"
    assert isinstance(group["results"], list)
    assert {r["paper_id"] for r in group["results"]} == {p1, p2}


# ---------------------------------------------------------------------------
# get_entities_tool (real DB) — pass-through, JSON structure
# ---------------------------------------------------------------------------


def test_get_entities_tool_empty(kb_conn):
    paper_id = register_paper(kb_conn, "A Paper")["paper_id"]

    with _patch_conn(kb_conn):
        result = json.loads(get_entities_tool(paper_id))

    assert result == []


# ---------------------------------------------------------------------------
# configure_llm_tool (real DB write; connectivity probe mocked) — happy + error
# ---------------------------------------------------------------------------


def test_configure_llm_tool_valid_ollama(kb_conn):
    """Valid ollama config → success JSON, api_key redacted."""
    with (
        _patch_conn(kb_conn),
        patch(
            "knowledge_base.llm._test_llm_connectivity",
            return_value={"reachable": True},
        ),
    ):
        result = json.loads(configure_llm_tool(provider="ollama", model="qwen3.5:27b"))

    assert "error" not in result
    assert result["provider"] == "ollama"
    assert result["model"] == "qwen3.5:27b"
    assert result["reachable"] is True
    assert "api_key" not in result


def test_configure_llm_tool_invalid_provider_maps_error(kb_conn):
    """Unknown provider → KnowledgeBaseError → {"error": ..., **details}."""
    with _patch_conn(kb_conn):
        result = json.loads(configure_llm_tool(provider="bogus"))

    assert "error" in result
    assert "bogus" in result["error"]
    # ValidationError carries an empty details dict — merge is a no-op but valid.


def test_configure_llm_tool_openai_compat_missing_base_url(kb_conn):
    """openai_compat without base_url → ValidationError → error mapping."""
    with _patch_conn(kb_conn):
        result = json.loads(configure_llm_tool(provider="openai_compat"))

    assert "error" in result
    assert "base_url" in result["error"]


# ---------------------------------------------------------------------------
# configure_vision_tool (real DB) — pass-through config write
# ---------------------------------------------------------------------------


def test_configure_vision_tool_writes_config(kb_conn):
    with _patch_conn(kb_conn):
        result = json.loads(configure_vision_tool(model="llava:13b", base_url="http://localhost:11434"))

    assert result["model"] == "llava:13b"
    assert result["base_url"] == "http://localhost:11434"
    # Persisted.
    row = kb_conn.execute("SELECT value FROM config WHERE key = 'vision_model'").fetchone()
    assert row["value"] == "llava:13b"


# ---------------------------------------------------------------------------
# configure_omniparser_tool (real DB) — query path + error mapping
# ---------------------------------------------------------------------------


def test_configure_omniparser_tool_query(kb_conn):
    """path=None, server_url=None → query returns current (unset) config."""
    with _patch_conn(kb_conn):
        result = json.loads(configure_omniparser_tool())

    assert result == {"omniparser_path": None, "omniparser_server_url": None}


def test_configure_omniparser_tool_valid_path(kb_conn, tmp_path):
    """A valid faked OmniParser dir is stored and echoed back (resolved)."""
    omni_dir = tmp_path / "omniparser"
    omni_dir.mkdir()
    (omni_dir / "parse.py").write_text("# fake")
    venv_bin = omni_dir / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("# fake")
    (venv_bin / "python").chmod(0o755)

    with _patch_conn(kb_conn):
        result = json.loads(configure_omniparser_tool(path=str(omni_dir)))

    assert "error" not in result
    assert result["omniparser_path"] == str(omni_dir.resolve())


def test_configure_omniparser_tool_invalid_path_maps_error(kb_conn):
    """Invalid path → ValidationError → error mapping."""
    with _patch_conn(kb_conn):
        result = json.loads(configure_omniparser_tool(path="/nonexistent/omniparser"))

    assert "error" in result
    assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# extract_structure_tool — branch coverage (estimate + extract mocked)
# ---------------------------------------------------------------------------


def test_extract_structure_tool_estimate_raises_maps_error(kb_conn):
    """estimate_extraction_time raises KnowledgeBaseError → error mapping."""
    with (
        _patch_conn(kb_conn),
        patch(
            f"{ROUTE}.estimate_extraction_time",
            side_effect=NotFoundError("Paper 99 not found"),
        ),
    ):
        result = json.loads(extract_structure_tool(paper_id=99))

    assert "error" in result
    assert "Paper 99 not found" in result["error"]


def test_extract_structure_tool_short_doc_passthrough(kb_conn):
    """Short doc: extract_structure result passes straight through as JSON.

    Asserts the wrapper forwards confirmed=True and the prefetched chunks.
    """
    est = {
        "total_chars": 100,
        "chunk_count": 1,
        "estimated_seconds": 4,
        "is_long": False,
        "chunks": [{"id": 1, "content": "x"}],
    }
    extracted = {
        "paper_id": 7,
        "methods_added": 1,
        "datasets_added": 0,
        "metrics_added": 2,
    }
    with (
        _patch_conn(kb_conn),
        patch(f"{ROUTE}.estimate_extraction_time", return_value=est),
        patch(f"{ROUTE}.extract_structure", return_value=extracted) as mock_extract,
    ):
        result = json.loads(extract_structure_tool(paper_id=7, confirmed=False, max_workers=3))

    assert result == extracted
    # Short-doc path always runs inline with confirmed=True and prefetched chunks.
    _, kwargs = mock_extract.call_args
    assert kwargs["confirmed"] is True
    assert kwargs["max_workers"] == 3
    assert kwargs["_prefetched_chunks"] == est["chunks"]


def test_extract_structure_tool_short_doc_extraction_error(kb_conn):
    """Short doc + ExtractionError → result carries error, errors, raw."""
    est = {
        "total_chars": 100,
        "chunk_count": 1,
        "estimated_seconds": 4,
        "is_long": False,
        "chunks": [{"id": 1, "content": "x"}],
    }
    exc = ExtractionError(
        "bad json",
        errors=[{"chunk_id": 1, "error": "boom"}],
        raw="{not json",
    )
    with (
        _patch_conn(kb_conn),
        patch(f"{ROUTE}.estimate_extraction_time", return_value=est),
        patch(f"{ROUTE}.extract_structure", side_effect=exc),
    ):
        result = json.loads(extract_structure_tool(paper_id=7))

    assert result["error"] == "bad json"
    assert result["errors"] == [{"chunk_id": 1, "error": "boom"}]
    assert result["raw"] == "{not json"


def test_extract_structure_tool_long_doc_warns_with_worker_clamp(kb_conn):
    """Long doc, not confirmed, wall estimate > 120s → confirm-required warning.

    max_workers (10) exceeds chunk_count (3), so effective_workers clamps to 3.
    estimated_seconds=1200 → wall = 1200 // 3 = 400 > 120.
    """
    est = {
        "total_chars": 50_000,
        "chunk_count": 3,
        "estimated_seconds": 1200,
        "is_long": True,
        "chunks": [{"id": i, "content": "x"} for i in range(3)],
    }
    with (
        _patch_conn(kb_conn),
        patch(f"{ROUTE}.estimate_extraction_time", return_value=est),
        patch(f"{ROUTE}.extract_structure") as mock_extract,
        patch(f"{ROUTE}.submit_job") as mock_submit,
    ):
        result = json.loads(extract_structure_tool(paper_id=7, confirmed=False, max_workers=10))

    assert result["confirm_required"] is True
    assert result["chunk_count"] == 3
    assert result["max_workers"] == 3  # clamped to chunk_count
    assert result["estimated_seconds"] == 400  # 1200 // 3
    # Neither inline extraction nor a job should run on the warning path.
    mock_extract.assert_not_called()
    mock_submit.assert_not_called()


def test_extract_structure_tool_long_doc_clamps_to_max_workers_limit(kb_conn):
    """effective_workers also clamps to _MAX_WORKERS_LIMIT (32)."""
    chunk_count = _MAX_WORKERS_LIMIT + 50
    est = {
        "total_chars": 500_000,
        "chunk_count": chunk_count,
        "estimated_seconds": 100_000,
        "is_long": True,
        "chunks": [{"id": i, "content": "x"} for i in range(chunk_count)],
    }
    with (
        _patch_conn(kb_conn),
        patch(f"{ROUTE}.estimate_extraction_time", return_value=est),
    ):
        result = json.loads(extract_structure_tool(paper_id=7, confirmed=False, max_workers=chunk_count))

    assert result["max_workers"] == _MAX_WORKERS_LIMIT
    assert result["estimated_seconds"] == 100_000 // _MAX_WORKERS_LIMIT


def test_extract_structure_tool_long_doc_confirmed_submits_job_with_params(kb_conn):
    """Long doc, confirmed, effective_workers > 1 → job with normalized params."""
    est = {
        "total_chars": 50_000,
        "chunk_count": 3,
        "estimated_seconds": 1200,
        "is_long": True,
        "chunks": [{"id": i, "content": "x"} for i in range(3)],
    }
    with (
        _patch_conn(kb_conn),
        patch(f"{ROUTE}.estimate_extraction_time", return_value=est),
        patch(f"{ROUTE}.submit_job", return_value=123) as mock_submit,
    ):
        result = json.loads(extract_structure_tool(paper_id=7, confirmed=True, max_workers=10))

    assert result == {
        "deferred": True,
        "job_id": 123,
        "status": "pending",
        "message": "Use get_job_status(job_id) to poll progress.",
    }
    # Dedup key normalized to the clamped effective worker count (3 > 1).
    args, kwargs = mock_submit.call_args
    assert args[1] == 7  # paper_id
    assert args[2] == "extract_structure"
    passed_params = kwargs.get("params", args[3] if len(args) > 3 else None)
    assert passed_params == {"max_workers": 3}


def test_extract_structure_tool_long_doc_confirmed_single_worker_params_none(kb_conn):
    """effective_workers == 1 → params is None (no max_workers dedup key)."""
    est = {
        "total_chars": 50_000,
        "chunk_count": 1,
        "estimated_seconds": 1200,
        "is_long": True,
        "chunks": [{"id": 0, "content": "x"}],
    }
    with (
        _patch_conn(kb_conn),
        patch(f"{ROUTE}.estimate_extraction_time", return_value=est),
        patch(f"{ROUTE}.submit_job", return_value=456) as mock_submit,
    ):
        result = json.loads(extract_structure_tool(paper_id=7, confirmed=True, max_workers=1))

    assert result["deferred"] is True
    assert result["job_id"] == 456
    args, kwargs = mock_submit.call_args
    passed_params = kwargs.get("params", args[3] if len(args) > 3 else None)
    assert passed_params is None
