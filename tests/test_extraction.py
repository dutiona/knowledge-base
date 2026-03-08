"""Tests for structured extraction (methods, datasets, metrics)."""

import json
import logging
from unittest.mock import patch

from research_index.db import EMBED_DIM, get_connection, init_schema
import pytest

from research_index.extraction import (
    _clear_previous_extraction,
    _collect_entity_mentions,
    _extract_map_reduce,
    _extract_single_pass,
    _get_llm_config,
    _llm_call,
    _map_extract,
    _resolve_entities,
    _store_resolved,
    _strip_think_tags,
    configure_llm,
    get_entities,
    record_method,
    record_dataset,
    record_metric,
    get_methods,
    get_datasets,
    get_metrics,
    compare_papers,
    extract_structure,
)
from research_index.ingest import ingest_file
from research_index.papers import register_paper


def _fake_embed(texts, model="nomic-embed-text", expected_dim=None):
    dim = expected_dim if expected_dim is not None else EMBED_DIM
    return [[0.1] * dim for _ in texts]


def _setup(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    return conn


# --- record_method / get_methods ---


def test_record_and_get_method(tmp_path):
    conn = _setup(tmp_path)
    p = register_paper(conn, "Test Paper")["paper_id"]

    result = record_method(conn, "Transformer", p, "Self-attention based architecture")
    assert "method_id" in result

    methods = get_methods(conn, p)
    assert len(methods) == 1
    assert methods[0]["name"] == "Transformer"
    assert methods[0]["description"] == "Self-attention based architecture"


def test_record_method_upsert(tmp_path):
    conn = _setup(tmp_path)
    p = register_paper(conn, "Test Paper")["paper_id"]

    record_method(conn, "Transformer", p, "V1 description")
    record_method(conn, "Transformer", p, "V2 description")

    methods = get_methods(conn, p)
    assert len(methods) == 1
    assert methods[0]["description"] == "V2 description"


# --- record_dataset / get_datasets ---


def test_record_and_get_dataset(tmp_path):
    conn = _setup(tmp_path)
    p = register_paper(conn, "Test Paper")["paper_id"]

    result = record_dataset(conn, "ImageNet", p, "1M images, 1000 classes")
    assert "dataset_id" in result

    datasets = get_datasets(conn, p)
    assert len(datasets) == 1
    assert datasets[0]["name"] == "ImageNet"


# --- record_metric / get_metrics ---


def test_record_and_get_metric(tmp_path):
    conn = _setup(tmp_path)
    p = register_paper(conn, "Test Paper")["paper_id"]
    m = record_method(conn, "ResNet", p)["method_id"]
    d = record_dataset(conn, "ImageNet", p)["dataset_id"]

    result = record_metric(
        conn, "accuracy", 76.1, p, method_id=m, dataset_id=d, unit="%"
    )
    assert "metric_id" in result

    metrics = get_metrics(conn, p)
    assert len(metrics) == 1
    assert metrics[0]["name"] == "accuracy"
    assert metrics[0]["value"] == 76.1
    assert metrics[0]["unit"] == "%"
    assert metrics[0]["method_name"] == "ResNet"
    assert metrics[0]["dataset_name"] == "ImageNet"


# --- compare_papers ---


def test_compare_papers_shared_dataset(tmp_path):
    conn = _setup(tmp_path)
    p1 = register_paper(conn, "ResNet Paper")["paper_id"]
    p2 = register_paper(conn, "ViT Paper")["paper_id"]

    m1 = record_method(conn, "ResNet", p1)["method_id"]
    m2 = record_method(conn, "ViT", p2)["method_id"]

    d1 = record_dataset(conn, "ImageNet", p1)["dataset_id"]
    d2 = record_dataset(conn, "ImageNet", p2)["dataset_id"]

    record_metric(
        conn, "top-1 accuracy", 76.1, p1, method_id=m1, dataset_id=d1, unit="%"
    )
    record_metric(
        conn, "top-1 accuracy", 81.3, p2, method_id=m2, dataset_id=d2, unit="%"
    )

    comparison = compare_papers(conn, [p1, p2])
    assert len(comparison) >= 1

    # Should find ImageNet as shared dataset with both metrics
    imagenet = next(c for c in comparison if c["dataset"] == "ImageNet")
    assert len(imagenet["results"]) == 2


def test_compare_papers_no_shared(tmp_path):
    conn = _setup(tmp_path)
    p1 = register_paper(conn, "Paper A")["paper_id"]
    p2 = register_paper(conn, "Paper B")["paper_id"]

    m1 = record_method(conn, "Method A", p1)["method_id"]
    d1 = record_dataset(conn, "Dataset A", p1)["dataset_id"]
    record_metric(conn, "accuracy", 90.0, p1, method_id=m1, dataset_id=d1)

    comparison = compare_papers(conn, [p1, p2])
    # No shared datasets
    assert comparison == []


# --- extract_structure ---

FAKE_LLM_RESPONSE = json.dumps(
    {
        "methods": [{"name": "BERT", "description": "Bidirectional encoder"}],
        "datasets": [{"name": "GLUE", "description": "NLU benchmark"}],
        "metrics": [
            {
                "metric": "accuracy",
                "value": 88.5,
                "unit": "%",
                "method": "BERT",
                "dataset": "GLUE",
            }
        ],
    }
)


@patch("research_index.ingest.embed", _fake_embed)
def test_extract_structure_basic(tmp_path):
    conn = _setup(tmp_path)

    md = tmp_path / "paper.md"
    md.write_text("BERT achieves 88.5% accuracy on GLUE benchmark.\n")
    ingest_file(conn, md)

    p = register_paper(conn, "BERT Paper", source_uri=str(md.resolve()))["paper_id"]

    def _mock_llm_call(prompt, *, conn):
        return FAKE_LLM_RESPONSE

    with patch("research_index.extraction._llm_call", _mock_llm_call):
        result = extract_structure(conn, p)

    assert result["methods_added"] == 1
    assert result["datasets_added"] == 1
    assert result["metrics_added"] == 1

    methods = get_methods(conn, p)
    assert methods[0]["name"] == "BERT"

    datasets = get_datasets(conn, p)
    assert datasets[0]["name"] == "GLUE"

    metrics = get_metrics(conn, p)
    assert metrics[0]["value"] == 88.5


@patch("research_index.ingest.embed", _fake_embed)
def test_extract_structure_no_chunks(tmp_path):
    conn = _setup(tmp_path)
    p = register_paper(conn, "Empty Paper")["paper_id"]

    result = extract_structure(conn, p)
    assert "error" in result


def test_entity_tables_exist(tmp_path):
    conn = _setup(tmp_path)
    p = register_paper(conn, "Test")["paper_id"]
    conn.execute(
        "INSERT INTO entities (canonical_name, entity_type, paper_id) VALUES (?, ?, ?)",
        ("CNN-LSTM", "method", p),
    )
    row = conn.execute(
        "SELECT * FROM entities WHERE canonical_name = 'CNN-LSTM'"
    ).fetchone()
    assert row is not None
    assert row["entity_type"] == "method"

    # entity_mentions table — need a real chunk for FK
    from research_index.ingest import ingest_file

    md = tmp_path / "doc.md"
    md.write_text("test content")
    with patch("research_index.ingest.embed", _fake_embed):
        ingest_file(conn, md)
    chunk_id = conn.execute("SELECT id FROM chunks LIMIT 1").fetchone()["id"]
    conn.execute(
        "INSERT INTO entity_mentions (entity_id, surface_form, chunk_id, confidence) VALUES (?, ?, ?, ?)",
        (row["id"], "our method", chunk_id, 0.9),
    )


def test_llm_config_defaults(tmp_path):
    conn = _setup(tmp_path)
    provider = conn.execute(
        "SELECT value FROM config WHERE key = 'llm_provider'"
    ).fetchone()
    model = conn.execute("SELECT value FROM config WHERE key = 'llm_model'").fetchone()
    assert provider["value"] == "ollama"
    assert model["value"] == "qwen3.5:27b"


def test_get_llm_config_defaults(tmp_path):
    conn = _setup(tmp_path)
    cfg = _get_llm_config(conn)
    assert cfg["provider"] == "ollama"
    assert cfg["model"] == "qwen3.5:27b"
    assert "base_url" in cfg


def test_get_llm_config_custom(tmp_path):
    conn = _setup(tmp_path)
    conn.execute("UPDATE config SET value = 'openai_compat' WHERE key = 'llm_provider'")
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('llm_base_url', 'http://192.168.1.41:1234')"
    )
    conn.execute(
        "UPDATE config SET value = 'qwen/qwen3.5-35b-a3b' WHERE key = 'llm_model'"
    )
    conn.commit()
    cfg = _get_llm_config(conn)
    assert cfg["provider"] == "openai_compat"
    assert cfg["base_url"] == "http://192.168.1.41:1234"
    assert cfg["model"] == "qwen/qwen3.5-35b-a3b"


@pytest.mark.parametrize(
    "input_url,expected",
    [
        ("http://host:1234", "http://host:1234"),
        ("http://host:1234/v1", "http://host:1234"),
        ("http://host:1234/v1/", "http://host:1234"),
        ("https://api.openai.com/v1", "https://api.openai.com"),
        ("http://host:1234/", "http://host:1234"),
        ("http://host/v1beta", "http://host/v1beta"),  # not stripped
    ],
)
def test_get_llm_config_strips_v1(tmp_path, input_url, expected):
    conn = _setup(tmp_path)
    conn.execute("UPDATE config SET value = 'openai_compat' WHERE key = 'llm_provider'")
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('llm_base_url', ?)",
        (input_url,),
    )
    conn.commit()
    cfg = _get_llm_config(conn)
    assert cfg["base_url"] == expected


def test_get_llm_config_ollama_preserves_v1(tmp_path):
    """Ollama provider should NOT strip /v1 (proxy path-prefix scenario)."""
    conn = _setup(tmp_path)
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('llm_base_url', 'http://proxy:8080/v1')"
    )
    conn.commit()
    cfg = _get_llm_config(conn)
    assert cfg["provider"] == "ollama"
    assert cfg["base_url"] == "http://proxy:8080/v1"


def test_map_extract_single_chunk(tmp_path):
    conn = _setup(tmp_path)

    fake_response = json.dumps(
        {
            "methods": [
                {
                    "name": "BERT",
                    "description": "Encoder",
                    "surface_forms": ["BERT", "our model"],
                }
            ],
            "datasets": [
                {
                    "name": "GLUE",
                    "description": "NLU benchmark",
                    "surface_forms": ["GLUE"],
                }
            ],
            "metrics": [
                {
                    "metric": "accuracy",
                    "value": 88.5,
                    "unit": "%",
                    "method": "BERT",
                    "dataset": "GLUE",
                }
            ],
        }
    )

    with patch("research_index.extraction._llm_call", return_value=fake_response):
        result = _map_extract(
            chunk_id=1,
            chunk_text="BERT achieves 88.5% on GLUE.",
            chunk_index=0,
            total_chunks=1,
            conn=conn,
        )

    assert len(result["methods"]) == 1
    assert result["methods"][0]["chunk_id"] == 1
    assert result["methods"][0]["surface_forms"] == ["BERT", "our model"]
    assert len(result["metrics"]) == 1
    assert result["metrics"][0]["chunk_id"] == 1


def test_resolve_entities_merges_aliases(tmp_path):
    conn = _setup(tmp_path)

    map_results = [
        {
            "methods": [
                {
                    "name": "CNN-LSTM",
                    "description": "Proposed arch",
                    "surface_forms": ["CNN-LSTM", "our method"],
                    "chunk_id": 1,
                }
            ],
            "datasets": [],
            "metrics": [],
        },
        {
            "methods": [
                {
                    "name": "the proposed approach",
                    "description": "See section 3",
                    "surface_forms": ["the proposed approach"],
                    "chunk_id": 5,
                }
            ],
            "datasets": [],
            "metrics": [],
        },
    ]

    resolve_response = json.dumps(
        {
            "groups": [
                {
                    "canonical": "CNN-LSTM",
                    "type": "method",
                    "members": ["CNN-LSTM", "our method", "the proposed approach"],
                },
            ],
        }
    )

    with patch("research_index.extraction._llm_call", return_value=resolve_response):
        resolution = _resolve_entities(map_results, conn)

    assert len(resolution["groups"]) == 1
    assert resolution["groups"][0]["canonical"] == "CNN-LSTM"
    assert "the proposed approach" in resolution["groups"][0]["members"]


@patch("research_index.ingest.embed", _fake_embed)
def test_store_resolved_writes_entities_and_methods(tmp_path):
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("CNN-LSTM achieves 92% accuracy on CIFAR-10.\n")
    ingest_file(conn, md)
    p = register_paper(conn, "Test Paper", source_uri=str(md.resolve()))["paper_id"]

    # Get actual chunk_id from the DB
    chunk_id = conn.execute("SELECT id FROM chunks LIMIT 1").fetchone()["id"]

    map_results = [
        {
            "methods": [
                {
                    "name": "CNN-LSTM",
                    "description": "Hybrid arch",
                    "surface_forms": ["CNN-LSTM", "our method"],
                    "chunk_id": chunk_id,
                }
            ],
            "datasets": [
                {
                    "name": "CIFAR-10",
                    "description": "Image dataset",
                    "surface_forms": ["CIFAR-10"],
                    "chunk_id": chunk_id,
                }
            ],
            "metrics": [
                {
                    "metric": "accuracy",
                    "value": 92.0,
                    "unit": "%",
                    "method": "CNN-LSTM",
                    "dataset": "CIFAR-10",
                    "chunk_id": chunk_id,
                }
            ],
        },
    ]
    resolution = {
        "groups": [
            {
                "canonical": "CNN-LSTM",
                "type": "method",
                "members": ["CNN-LSTM", "our method"],
            },
            {"canonical": "CIFAR-10", "type": "dataset", "members": ["CIFAR-10"]},
        ],
    }

    result = _store_resolved(conn, p, map_results, resolution)
    assert result["methods_added"] >= 1
    assert result["datasets_added"] >= 1
    assert result["metrics_added"] >= 1

    # Check entities table
    entities = conn.execute(
        "SELECT * FROM entities WHERE paper_id = ?", (p,)
    ).fetchall()
    assert len(entities) == 2

    # Check entity_mentions
    mentions = conn.execute(
        "SELECT em.* FROM entity_mentions em JOIN entities e ON em.entity_id = e.id WHERE e.paper_id = ?",
        (p,),
    ).fetchall()
    assert len(mentions) >= 2  # "CNN-LSTM" + "our method" at minimum


def test_clear_previous_extraction_idempotent(tmp_path):
    """Running extraction twice produces same result, not duplicates."""
    conn = _setup(tmp_path)
    p = register_paper(conn, "Test")["paper_id"]
    record_method(conn, "OldMethod", p, "should be removed")
    assert len(get_methods(conn, p)) == 1

    _clear_previous_extraction(conn, p)
    assert len(get_methods(conn, p)) == 0


@patch("research_index.ingest.embed", _fake_embed)
def test_extract_structure_fast_path_short_doc(tmp_path):
    """Short docs (<8000 chars) use single LLM call, no entity resolution."""
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("BERT achieves 88.5% accuracy on GLUE benchmark.\n")
    ingest_file(conn, md)
    p = register_paper(conn, "BERT Paper", source_uri=str(md.resolve()))["paper_id"]

    def _mock_llm_call(prompt, *, conn):
        return FAKE_LLM_RESPONSE

    with patch("research_index.extraction._llm_call", _mock_llm_call):
        result = extract_structure(conn, p)

    assert result["methods_added"] == 1
    assert result["datasets_added"] == 1
    assert result["metrics_added"] == 1


@patch("research_index.ingest.embed", _fake_embed)
def test_extract_structure_eta_gate_long_doc(tmp_path):
    """Long docs return warning + ETA when not confirmed."""
    conn = _setup(tmp_path)
    md = tmp_path / "long.md"
    md.write_text(
        "\n".join(f"Section {i}: " + f"content-{i} " * 100 for i in range(50))
    )
    ingest_file(conn, md)
    p = register_paper(conn, "Long Paper", source_uri=str(md.resolve()))["paper_id"]

    result = extract_structure(conn, p, confirmed=False)
    assert result.get("confirm_required") is True
    assert "estimated_seconds" in result
    assert "warning" in result


@patch("research_index.ingest.embed", _fake_embed)
def test_extract_structure_map_reduce_confirmed(tmp_path):
    """Long docs with confirmed=True run the full pipeline."""
    conn = _setup(tmp_path)
    md = tmp_path / "long.md"
    md.write_text(
        "\n".join(f"Section {i}: " + f"content-{i} " * 100 for i in range(20))
    )
    ingest_file(conn, md)
    p = register_paper(conn, "Long Paper", source_uri=str(md.resolve()))["paper_id"]

    map_response = json.dumps(
        {
            "methods": [
                {
                    "name": "ResNet",
                    "description": "Deep residual",
                    "surface_forms": ["ResNet"],
                }
            ],
            "datasets": [],
            "metrics": [],
        }
    )
    resolve_response = json.dumps(
        {
            "groups": [
                {"canonical": "ResNet", "type": "method", "members": ["ResNet"]}
            ],
        }
    )
    call_count = {"n": 0}

    def _mock_llm(prompt, *, conn):
        call_count["n"] += 1
        if "Group mentions" in prompt or "canonical" in prompt.lower():
            return resolve_response
        return map_response

    with patch("research_index.extraction._llm_call", _mock_llm):
        result = extract_structure(conn, p, confirmed=True)

    assert "confirm_required" not in result
    assert result["methods_added"] >= 1


def test_configure_llm(tmp_path):
    conn = _setup(tmp_path)

    result = configure_llm(
        conn,
        provider="openai_compat",
        base_url="http://192.168.1.41:1234",
        model="qwen/qwen3.5-35b-a3b",
        api_key="sk-test-123",
    )
    assert result["provider"] == "openai_compat"
    assert "api_key" not in result  # Redacted from response

    cfg = _get_llm_config(conn)
    assert cfg["provider"] == "openai_compat"
    assert cfg["base_url"] == "http://192.168.1.41:1234"
    assert cfg["model"] == "qwen/qwen3.5-35b-a3b"
    assert cfg["api_key"] == "sk-test-123"  # But stored correctly


def test_configure_llm_switch_to_ollama_clears_stale(tmp_path):
    """Switching from openai_compat to ollama clears stale base_url and api_key."""
    conn = _setup(tmp_path)
    # First configure openai_compat with base_url and api_key
    configure_llm(
        conn,
        provider="openai_compat",
        base_url="http://192.168.1.41:1234",
        model="some-model",
        api_key="sk-secret",
    )
    cfg = _get_llm_config(conn)
    assert cfg["base_url"] == "http://192.168.1.41:1234"
    assert cfg["api_key"] == "sk-secret"

    # Switch to ollama without explicit base_url — stale values should be cleared
    configure_llm(conn, provider="ollama", model="qwen3.5:27b")
    cfg = _get_llm_config(conn)
    assert cfg["provider"] == "ollama"
    assert cfg["api_key"] is None
    # base_url should be auto-detected (not the old openai_compat URL)
    assert cfg["base_url"] != "http://192.168.1.41:1234"


def test_configure_llm_remote_ollama_preserves_base_url(tmp_path):
    """Explicitly setting base_url for ollama (remote) is preserved."""
    conn = _setup(tmp_path)
    configure_llm(
        conn,
        provider="ollama",
        base_url="http://remote-ollama:11434",
        model="qwen3.5:27b",
    )
    cfg = _get_llm_config(conn)
    assert cfg["provider"] == "ollama"
    assert cfg["base_url"] == "http://remote-ollama:11434"


@patch("research_index.ingest.embed", _fake_embed)
def test_get_entities(tmp_path):
    conn = _setup(tmp_path)
    p = register_paper(conn, "Test")["paper_id"]

    conn.execute(
        "INSERT INTO entities (canonical_name, entity_type, paper_id, description) VALUES (?, ?, ?, ?)",
        ("CNN-LSTM", "method", p, "Hybrid architecture"),
    )
    eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    md = tmp_path / "doc.md"
    md.write_text("test content")
    ingest_file(conn, md)
    chunk_id = conn.execute("SELECT id FROM chunks LIMIT 1").fetchone()["id"]

    conn.execute(
        "INSERT INTO entity_mentions (entity_id, surface_form, chunk_id, confidence) VALUES (?, ?, ?, ?)",
        (eid, "our method", chunk_id, 0.9),
    )
    conn.commit()

    entities = get_entities(conn, p)
    assert len(entities) == 1
    assert entities[0]["canonical_name"] == "CNN-LSTM"
    assert len(entities[0]["mentions"]) == 1
    assert entities[0]["mentions"][0]["surface_form"] == "our method"


# --- _strip_think_tags ---


@pytest.mark.parametrize(
    "input_text, expected",
    [
        # No tags — passthrough
        ('{"test": "hello"}', '{"test": "hello"}'),
        # Empty <think></think> (Qwen empty-thinking case)
        ('<think></think>{"test": "hello"}', '{"test": "hello"}'),
        # Empty with whitespace inside
        ('<think>  \n</think>{"test": "hello"}', '{"test": "hello"}'),
        # Think tags with content before JSON
        (
            '<think>Let me analyze this carefully...</think>{"methods": []}',
            '{"methods": []}',
        ),
        # <thinking> variant (safety net)
        ('<thinking>reasoning here</thinking>{"data": 1}', '{"data": 1}'),
        # Multiline thinking content
        (
            '<think>\nStep 1: read the text\nStep 2: extract\n</think>\n{"methods": []}',
            '{"methods": []}',
        ),
        # Only thinking tags, no JSON — should return empty string
        ("<think>just thinking</think>", ""),
        # No tags, just whitespace — preserves stripped result
        ('  {"x": 1}  ', '{"x": 1}'),
        # Literal <think> inside JSON field — must NOT be corrupted
        (
            '{"description": "Uses <think>tags</think> for reasoning"}',
            '{"description": "Uses <think>tags</think> for reasoning"}',
        ),
        # Think preamble + literal <think> inside JSON — only preamble stripped
        (
            '<think>reasoning</think>{"description": "model uses <think>mode</think>"}',
            '{"description": "model uses <think>mode</think>"}',
        ),
    ],
    ids=[
        "no_tags",
        "empty_think",
        "empty_think_whitespace",
        "think_with_content",
        "thinking_variant",
        "multiline_thinking",
        "only_thinking_no_json",
        "whitespace_only",
        "literal_think_in_json",
        "preamble_plus_literal_in_json",
    ],
)
def test_strip_think_tags(input_text, expected):
    assert _strip_think_tags(input_text) == expected


# --- _llm_call: empty response raises ---


def test_llm_call_empty_response_raises(tmp_path):
    """_llm_call raises ValueError when LLM returns empty after tag stripping."""
    conn = _setup(tmp_path)

    def _mock_post(*args, **kwargs):
        class FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"response": "<think>internal reasoning</think>"}

        return FakeResp()

    with patch("research_index.extraction.httpx.post", _mock_post):
        with pytest.raises(ValueError, match="empty response"):
            _llm_call("test prompt", conn=conn)


def test_llm_call_strips_tags_returns_json(tmp_path):
    """_llm_call strips think tags and returns the JSON portion."""
    conn = _setup(tmp_path)

    def _mock_post(*args, **kwargs):
        class FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"response": '<think>hmm</think>{"methods": []}'}

        return FakeResp()

    with patch("research_index.extraction.httpx.post", _mock_post):
        result = _llm_call("test prompt", conn=conn)
    assert result == '{"methods": []}'


def test_llm_call_ollama_sends_system_directive(tmp_path):
    """_llm_call sends system JSON directive to Ollama."""
    conn = _setup(tmp_path)
    captured = {}

    def _mock_post(url, **kwargs):
        captured.update(kwargs.get("json", {}))

        class FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"response": '{"ok": true}'}

        return FakeResp()

    with patch("research_index.extraction.httpx.post", _mock_post):
        _llm_call("test prompt", conn=conn)

    assert "system" in captured
    assert "JSON" in captured["system"]


def test_llm_call_openai_sends_system_message(tmp_path):
    """_llm_call sends system message for openai_compat provider."""
    conn = _setup(tmp_path)
    configure_llm(
        conn,
        provider="openai_compat",
        base_url="http://localhost:1234",
        model="test-model",
    )
    captured = {}

    def _mock_post(url, **kwargs):
        captured.update(kwargs.get("json", {}))

        class FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": '{"ok": true}'}}]}

        return FakeResp()

    with patch("research_index.extraction.httpx.post", _mock_post):
        _llm_call("test prompt", conn=conn)

    messages = captured.get("messages", [])
    assert messages[0]["role"] == "system"
    assert "JSON" in messages[0]["content"]


# --- map-reduce error visibility ---


@patch("research_index.ingest.embed", _fake_embed)
def test_map_reduce_all_chunks_fail_reports_errors(tmp_path):
    """When all chunks return empty, the error list is populated."""
    conn = _setup(tmp_path)
    md = tmp_path / "long.md"
    md.write_text(
        "\n".join(f"Section {i}: " + f"content-{i} " * 100 for i in range(20))
    )
    ingest_file(conn, md)
    p = register_paper(conn, "Fail Paper", source_uri=str(md.resolve()))["paper_id"]

    def _mock_llm_empty(prompt, *, conn):
        raise ValueError("LLM returned empty response (possible thinking-mode issue)")

    with patch("research_index.extraction._llm_call", _mock_llm_empty):
        result = extract_structure(conn, p, confirmed=True)

    assert "error" in result
    assert result["error"] == "All chunks failed extraction"
    assert len(result["errors"]) > 0
    assert "empty response" in result["errors"][0]["error"]


@patch("research_index.ingest.embed", _fake_embed)
def test_store_resolved_passes_chunk_id_to_metrics(tmp_path):
    """Map-reduce path: record_metric receives chunk_id from per-chunk extraction."""
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("ResNet gets 95% on ImageNet.\n")
    ingest_file(conn, md)
    p = register_paper(conn, "Test", source_uri=str(md.resolve()))["paper_id"]
    chunk_id = conn.execute("SELECT id FROM chunks LIMIT 1").fetchone()["id"]

    map_results = [
        {
            "methods": [
                {
                    "name": "ResNet",
                    "description": "Deep residual net",
                    "chunk_id": chunk_id,
                }
            ],
            "datasets": [
                {
                    "name": "ImageNet",
                    "description": "Image dataset",
                    "chunk_id": chunk_id,
                }
            ],
            "metrics": [
                {
                    "metric": "accuracy",
                    "value": 95.0,
                    "unit": "%",
                    "method": "ResNet",
                    "dataset": "ImageNet",
                    "chunk_id": chunk_id,
                }
            ],
        }
    ]
    resolution = {"groups": []}

    _store_resolved(conn, p, map_results, resolution)

    row = conn.execute(
        "SELECT chunk_id FROM metrics WHERE paper_id = ?", (p,)
    ).fetchone()
    assert row is not None
    assert row["chunk_id"] == chunk_id


@patch("research_index.ingest.embed", _fake_embed)
def test_single_pass_passes_first_chunk_id_to_metrics(tmp_path):
    """Single-pass path: record_metric receives first_chunk_id as approximate provenance."""
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("BERT achieves 88.5% accuracy on GLUE.\n")
    ingest_file(conn, md)
    p = register_paper(conn, "BERT Paper", source_uri=str(md.resolve()))["paper_id"]

    first_chunk_id = conn.execute("SELECT id FROM chunks LIMIT 1").fetchone()["id"]

    def _mock_llm_call(prompt, *, conn):
        return FAKE_LLM_RESPONSE

    with patch("research_index.extraction._llm_call", _mock_llm_call):
        result = extract_structure(conn, p)

    assert result["metrics_added"] == 1
    row = conn.execute(
        "SELECT chunk_id FROM metrics WHERE paper_id = ?", (p,)
    ).fetchone()
    assert row is not None
    assert row["chunk_id"] == first_chunk_id


@patch("research_index.ingest.embed", _fake_embed)
@pytest.mark.parametrize("table", ["methods", "datasets"])
def test_store_resolved_passes_chunk_id_to_methods_and_datasets(tmp_path, table):
    """Map-reduce path: record_method/record_dataset receive chunk_id from mentions."""
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("ResNet gets 95% on ImageNet.\n")
    ingest_file(conn, md)
    p = register_paper(conn, "Test", source_uri=str(md.resolve()))["paper_id"]
    chunk_id = conn.execute("SELECT id FROM chunks LIMIT 1").fetchone()["id"]

    map_results = [
        {
            "methods": [
                {
                    "name": "ResNet",
                    "description": "Deep residual net",
                    "chunk_id": chunk_id,
                }
            ],
            "datasets": [
                {
                    "name": "ImageNet",
                    "description": "Image dataset",
                    "chunk_id": chunk_id,
                }
            ],
            "metrics": [],
        }
    ]
    resolution = {"groups": []}

    _store_resolved(conn, p, map_results, resolution)

    row = conn.execute(
        f"SELECT chunk_id FROM {table} WHERE paper_id = ?", (p,)
    ).fetchone()
    assert row is not None
    assert row["chunk_id"] == chunk_id


@pytest.mark.parametrize(
    "table,entity_type", [("methods", "methods"), ("datasets", "datasets")]
)
def test_store_resolved_description_and_chunk_id_same_provenance(
    tmp_path, table, entity_type
):
    """Description and chunk_id must originate from the same chunk (#39).

    When an entity is mentioned across multiple chunks but only the second
    chunk provides a description, the stored chunk_id must be the chunk
    that supplied the description — not the first-mention chunk.
    """
    conn = _setup(tmp_path)
    source_uri = str((tmp_path / "paper.md").resolve())
    p = register_paper(conn, "Provenance Test", source_uri=source_uri)["paper_id"]

    # Create two chunks directly — this test targets _store_resolved,
    # not the chunking logic.
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, metadata) "
        "VALUES ('hash1', 'chunk one', 'markdown', ?, 0, '{}')",
        (source_uri,),
    )
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index, metadata) "
        "VALUES ('hash2', 'chunk two', 'markdown', ?, 1, '{}')",
        (source_uri,),
    )
    conn.commit()
    chunks = conn.execute("SELECT id FROM chunks ORDER BY chunk_index").fetchall()
    chunk_1 = chunks[0]["id"]
    chunk_2 = chunks[1]["id"]

    entity_name = "ResNet" if table == "methods" else "ImageNet"
    map_results = [
        {
            entity_type: [
                {
                    "name": entity_name,
                    "chunk_id": chunk_1,
                },  # mention without description
            ],
            "metrics": [],
            # ensure both entity type keys are present
            "methods" if entity_type == "datasets" else "datasets": [],
        },
        {
            entity_type: [
                {
                    "name": entity_name,
                    "description": "Detailed description from chunk 2",
                    "chunk_id": chunk_2,
                },
            ],
            "metrics": [],
            "methods" if entity_type == "datasets" else "datasets": [],
        },
    ]
    resolution = {"groups": []}

    _store_resolved(conn, p, map_results, resolution)

    row = conn.execute(
        f"SELECT description, chunk_id FROM {table} WHERE paper_id = ?", (p,)
    ).fetchone()
    assert row is not None
    assert row["description"] == "Detailed description from chunk 2"
    # chunk_id must match the chunk that provided the description
    assert row["chunk_id"] == chunk_2, (
        f"chunk_id should be {chunk_2} (description source) but got {row['chunk_id']}"
    )


@patch("research_index.ingest.embed", _fake_embed)
@pytest.mark.parametrize("table", ["methods", "datasets"])
def test_single_pass_passes_first_chunk_id_to_methods_and_datasets(tmp_path, table):
    """Single-pass path: record_method/record_dataset receive first_chunk_id."""
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("BERT achieves 88.5% accuracy on GLUE.\n")
    ingest_file(conn, md)
    p = register_paper(conn, "BERT Paper", source_uri=str(md.resolve()))["paper_id"]

    first_chunk_id = conn.execute("SELECT id FROM chunks LIMIT 1").fetchone()["id"]

    def _mock_llm_call(prompt, *, conn):
        return FAKE_LLM_RESPONSE

    with patch("research_index.extraction._llm_call", _mock_llm_call):
        result = extract_structure(conn, p)

    assert result["methods_added"] >= 1
    row = conn.execute(
        f"SELECT chunk_id FROM {table} WHERE paper_id = ?", (p,)
    ).fetchone()
    assert row is not None
    assert row["chunk_id"] == first_chunk_id


# --- commit=False batching (issue #18) ---


@pytest.mark.parametrize(
    "record_func, get_func, record_args",
    [
        (record_method, get_methods, ("Transformer",)),
        (record_dataset, get_datasets, ("ImageNet",)),
        (record_metric, get_metrics, ("accuracy", 0.95)),
    ],
    ids=["method", "dataset", "metric"],
)
def test_record_commit_false_does_not_persist(
    tmp_path, record_func, get_func, record_args
):
    """When commit=False, data is visible in-transaction but not persisted."""
    conn = _setup(tmp_path)
    p = register_paper(conn, "Test Paper")["paper_id"]
    record_func(conn, *record_args, p, commit=False)
    # Visible within same connection (uncommitted read)
    assert len(get_func(conn, p)) == 1
    # Rollback should discard the uncommitted write
    conn.rollback()
    assert len(get_func(conn, p)) == 0


def test_record_method_default_commit_persists(tmp_path):
    """Default commit=True still auto-commits (backward compat)."""
    conn = _setup(tmp_path)
    p = register_paper(conn, "Test Paper")["paper_id"]
    record_method(conn, "ResNet", p)
    conn.rollback()  # Should have no effect — already committed
    assert len(get_methods(conn, p)) == 1


def test_batch_commit_atomicity(tmp_path):
    """Multiple commit=False calls followed by one conn.commit() is atomic."""
    conn = _setup(tmp_path)
    p = register_paper(conn, "Test Paper")["paper_id"]
    record_method(conn, "BERT", p, commit=False)
    record_dataset(conn, "GLUE", p, commit=False)
    m = record_method(conn, "BERT", p, commit=False)  # upsert
    record_metric(conn, "F1", 0.88, p, method_id=m["method_id"], commit=False)
    # All visible pre-commit
    assert len(get_methods(conn, p)) == 1
    assert len(get_datasets(conn, p)) == 1
    assert len(get_metrics(conn, p)) == 1
    # Single commit persists everything
    conn.commit()
    conn.rollback()  # No effect
    assert len(get_methods(conn, p)) == 1
    assert len(get_datasets(conn, p)) == 1
    assert len(get_metrics(conn, p)) == 1


# ── _store_resolved: malformed resolution groups ──────────────────────


def test_store_resolved_skips_group_missing_canonical(tmp_path, caplog):
    """Groups without 'canonical' key are skipped with a warning, not KeyError."""
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("Some method described here.\n")
    ingest_file(conn, md)
    p = register_paper(conn, "Test Paper", source_uri=str(md.resolve()))["paper_id"]
    chunk_id = conn.execute("SELECT id FROM chunks LIMIT 1").fetchone()["id"]

    map_results = [
        {
            "methods": [
                {
                    "name": "MethodA",
                    "description": "A method",
                    "surface_forms": ["MethodA"],
                    "chunk_id": chunk_id,
                }
            ],
            "datasets": [],
            "metrics": [],
        },
    ]
    # One valid group, one missing "canonical"
    resolution = {
        "groups": [
            {"canonical": "MethodA", "type": "method", "members": ["MethodA"]},
            {"type": "method", "members": ["OrphanMethod"]},  # no canonical
        ],
    }

    with caplog.at_level(logging.WARNING, logger="research_index.extraction"):
        result = _store_resolved(conn, p, map_results, resolution)

    assert result["methods_added"] >= 1
    assert any("missing 'canonical'" in msg for msg in caplog.messages)


def test_store_resolved_skips_group_with_empty_canonical(tmp_path, caplog):
    """Groups with empty-string canonical are skipped like missing ones."""
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("Some content.\n")
    ingest_file(conn, md)
    p = register_paper(conn, "Test Paper", source_uri=str(md.resolve()))["paper_id"]

    map_results = [{"methods": [], "datasets": [], "metrics": []}]
    resolution = {
        "groups": [
            {"canonical": "", "type": "method", "members": ["Ghost"]},
        ],
    }

    with caplog.at_level(logging.WARNING, logger="research_index.extraction"):
        result = _store_resolved(conn, p, map_results, resolution)

    assert result["methods_added"] == 0
    assert any("missing 'canonical'" in msg for msg in caplog.messages)


def test_store_resolved_all_groups_malformed(tmp_path, caplog):
    """All groups malformed — function completes without crash, 0 entities."""
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("Content.\n")
    ingest_file(conn, md)
    p = register_paper(conn, "Test Paper", source_uri=str(md.resolve()))["paper_id"]

    map_results = [{"methods": [], "datasets": [], "metrics": []}]
    resolution = {
        "groups": [
            {"type": "method", "members": ["A"]},
            {"members": ["B"]},
            {},
        ],
    }

    with caplog.at_level(logging.WARNING, logger="research_index.extraction"):
        result = _store_resolved(conn, p, map_results, resolution)

    assert result["methods_added"] == 0
    assert result["datasets_added"] == 0
    # All 3 groups should have triggered warnings
    canonical_warnings = [m for m in caplog.messages if "missing 'canonical'" in m]
    assert len(canonical_warnings) == 3


# --- rollback guard tests ---


def test_store_resolved_rollback_on_error(tmp_path):
    """Verify _store_resolved rolls back all writes when an exception occurs mid-transaction."""
    conn = _setup(tmp_path)
    md = tmp_path / "doc.md"
    md.write_text("# Test\nSome content")
    with patch("research_index.ingest.embed", _fake_embed):
        ingest_file(conn, md)
    p = register_paper(conn, "Test Paper", source_uri=str(md.resolve()))["paper_id"]

    # Pre-populate a method so we can verify rollback restores clean state
    record_method(conn, "PreExisting", p, "should survive")

    map_results = [
        {
            "methods": [{"name": "MethodA", "description": "desc"}],
            "datasets": [{"name": "DatasetA", "description": "desc"}],
            "metrics": [
                {
                    "metric": "F1",
                    "value": "0.9",
                    "method": "MethodA",
                    "dataset": "DatasetA",
                }
            ],
        }
    ]
    resolution = {"groups": []}

    # Patch record_metric to raise after methods/datasets have been written
    with patch(
        "research_index.extraction.record_metric",
        side_effect=RuntimeError("simulated failure"),
    ):
        with pytest.raises(RuntimeError, match="simulated failure"):
            _store_resolved(conn, p, map_results, resolution)

    # Verify rollback: no new methods or datasets from the failed transaction
    methods = get_methods(conn, p)
    method_names = [m["name"] for m in methods]
    assert "MethodA" not in method_names
    # _clear_previous_extraction deletes PreExisting, but rollback should restore it
    assert "PreExisting" in method_names

    datasets = get_datasets(conn, p)
    assert len([d for d in datasets if d["name"] == "DatasetA"]) == 0


def test_extract_single_pass_rollback_on_error(tmp_path):
    """Verify _extract_single_pass rolls back all writes when an exception occurs mid-transaction."""
    conn = _setup(tmp_path)
    md = tmp_path / "doc.md"
    md.write_text("# Test\nSome content")
    with patch("research_index.ingest.embed", _fake_embed):
        ingest_file(conn, md)
    p = register_paper(conn, "Test Paper", source_uri=str(md.resolve()))["paper_id"]

    record_method(conn, "PreExisting", p, "should survive")

    chunks = [{"id": 1, "content": "some text"}]

    llm_response = json.dumps(
        {
            "methods": [{"name": "NewMethod", "description": "desc"}],
            "datasets": [],
            "metrics": [{"metric": "Acc", "value": "0.95", "method": "NewMethod"}],
        }
    )

    with patch("research_index.extraction._llm_call", return_value=llm_response):
        with patch(
            "research_index.extraction.record_metric",
            side_effect=RuntimeError("simulated failure"),
        ):
            with pytest.raises(RuntimeError, match="simulated failure"):
                _extract_single_pass(conn, p, chunks)

    methods = get_methods(conn, p)
    method_names = [m["name"] for m in methods]
    assert "NewMethod" not in method_names
    assert "PreExisting" in method_names


# --- Null-safety: LLM returning None for fields (issue #49) ---


def test_collect_entity_mentions_skips_none_name(tmp_path):
    """_collect_entity_mentions must not crash when LLM returns 'name': null."""
    extractions = [
        {
            "methods": [
                {"name": None, "description": "ghost method"},
                {"name": "RealMethod", "description": "valid"},
            ],
            "datasets": [
                {"name": None},
                {"name": "RealDataset", "description": "valid"},
            ],
        }
    ]
    mentions = _collect_entity_mentions(extractions)
    names = {m["name"] for m in mentions}
    assert "RealMethod" in names
    assert "RealDataset" in names
    assert len(mentions) == 2  # None-named items skipped


def test_store_resolved_handles_none_method_and_dataset(tmp_path):
    """_store_resolved must not crash when metric has 'method': null or 'dataset': null."""
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("Some content about models.\n")
    with patch("research_index.ingest.embed", _fake_embed):
        ingest_file(conn, md)
    p = register_paper(conn, "Test Paper", source_uri=str(md.resolve()))["paper_id"]
    chunk_id = conn.execute("SELECT id FROM chunks LIMIT 1").fetchone()["id"]

    map_results = [
        {
            "methods": [
                {
                    "name": "BERT",
                    "description": "encoder",
                    "surface_forms": ["BERT"],
                    "chunk_id": chunk_id,
                }
            ],
            "datasets": [],
            "metrics": [
                {
                    "metric": "F1",
                    "value": 91.0,
                    "method": None,
                    "dataset": None,
                    "chunk_id": chunk_id,
                },
            ],
        }
    ]
    resolution = {
        "groups": [{"canonical": "BERT", "type": "method", "members": ["BERT"]}],
    }
    # Should not raise AttributeError: 'NoneType' has no attribute 'lower'
    result = _store_resolved(conn, p, map_results, resolution)
    assert result["metrics_added"] >= 1


def test_extract_single_pass_handles_none_fields(tmp_path):
    """_extract_single_pass must not crash when LLM returns null for name/metric fields."""
    conn = _setup(tmp_path)
    md = tmp_path / "doc.md"
    md.write_text("# Test\nSome content about methods.")
    with patch("research_index.ingest.embed", _fake_embed):
        ingest_file(conn, md)
    p = register_paper(conn, "Test Paper", source_uri=str(md.resolve()))["paper_id"]

    chunks = [{"id": 1, "content": "some text"}]
    llm_response = json.dumps(
        {
            "methods": [
                {"name": None, "description": "ghost"},
                {"name": "ValidMethod", "description": "real"},
            ],
            "datasets": [
                {"name": None},
                {"name": "ValidDataset", "description": "real"},
            ],
            "metrics": [
                {"metric": None, "value": 0.5},
                {"metric": "Accuracy", "value": 0.95, "method": None, "dataset": None},
            ],
        }
    )
    with patch("research_index.extraction._llm_call", return_value=llm_response):
        result = _extract_single_pass(conn, p, chunks)

    assert "error" not in result
    assert result["methods_added"] == 1  # Only ValidMethod
    assert result["datasets_added"] == 1  # Only ValidDataset


def test_collect_entity_mentions_handles_null_containers():
    """_collect_entity_mentions must not crash when 'methods' or 'datasets' is null."""
    extractions = [
        {"methods": None, "datasets": [{"name": "Valid", "description": "ok"}]},
        {"methods": [{"name": "Also Valid"}], "datasets": None},
    ]
    mentions = _collect_entity_mentions(extractions)
    names = {m["name"] for m in mentions}
    assert names == {"Valid", "Also Valid"}


def test_store_resolved_handles_null_containers(tmp_path):
    """_store_resolved must not crash when 'groups', 'methods', or 'metrics' is null."""
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("Content.\n")
    with patch("research_index.ingest.embed", _fake_embed):
        ingest_file(conn, md)
    p = register_paper(conn, "Test Paper", source_uri=str(md.resolve()))["paper_id"]

    map_results = [{"methods": None, "datasets": None, "metrics": None}]
    resolution = {"groups": None}
    result = _store_resolved(conn, p, map_results, resolution)
    assert result["methods_added"] == 0
    assert result["datasets_added"] == 0
    assert result["metrics_added"] == 0


def test_extract_single_pass_handles_null_containers(tmp_path):
    """_extract_single_pass must not crash when LLM returns null for methods/datasets/metrics."""
    conn = _setup(tmp_path)
    md = tmp_path / "doc.md"
    md.write_text("# Test\nContent.")
    with patch("research_index.ingest.embed", _fake_embed):
        ingest_file(conn, md)
    p = register_paper(conn, "Test Paper", source_uri=str(md.resolve()))["paper_id"]

    chunks = [{"id": 1, "content": "some text"}]
    llm_response = json.dumps({"methods": None, "datasets": None, "metrics": None})
    with patch("research_index.extraction._llm_call", return_value=llm_response):
        result = _extract_single_pass(conn, p, chunks)

    assert "error" not in result
    assert result["methods_added"] == 0
    assert result["datasets_added"] == 0


# --- Progress logging tests (#50) ---


def _make_chunks(n: int) -> list[dict]:
    """Create a list of fake chunk dicts for progress logging tests."""
    return [{"id": i + 1, "content": f"Chunk {i} content"} for i in range(n)]


_MAP_RESPONSE = json.dumps(
    {
        "methods": [
            {
                "name": "ResNet",
                "description": "Deep residual",
                "surface_forms": ["ResNet"],
            }
        ],
        "datasets": [
            {
                "name": "ImageNet",
                "description": "Image dataset",
                "surface_forms": ["ImageNet"],
            }
        ],
        "metrics": [
            {
                "metric": "accuracy",
                "value": 95.0,
                "unit": "%",
                "method": "ResNet",
                "dataset": "ImageNet",
            }
        ],
    }
)

_RESOLVE_RESPONSE = json.dumps(
    {
        "groups": [
            {"canonical": "ResNet", "type": "method", "members": ["ResNet"]},
            {"canonical": "ImageNet", "type": "dataset", "members": ["ImageNet"]},
        ]
    }
)


def _mock_llm_for_progress(prompt, *, conn):
    if "Group mentions" in prompt or "canonical" in prompt.lower():
        return _RESOLVE_RESPONSE
    return _MAP_RESPONSE


@patch("research_index.ingest.embed", _fake_embed)
def test_map_reduce_logs_per_chunk_progress(tmp_path, caplog):
    """Each chunk emits an INFO log with chunk number, percentage, timing, and entity counts."""
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("Content " * 200)
    ingest_file(conn, md)
    p = register_paper(conn, "Test", source_uri=str(md.resolve()))["paper_id"]
    chunks = [
        {"id": row["id"], "content": row["content"]}
        for row in conn.execute(
            "SELECT id, content FROM chunks WHERE source_uri = ? ORDER BY chunk_index",
            (str(md.resolve()),),
        ).fetchall()
    ]

    with caplog.at_level(logging.INFO, logger="research_index.extraction"):
        with patch("research_index.extraction._llm_call", _mock_llm_for_progress):
            _extract_map_reduce(conn, p, chunks)

    chunk_logs = [m for m in caplog.messages if "Chunk" in m and "/" in m]
    assert len(chunk_logs) == len(chunks)
    # First chunk log should contain entity counts
    assert "methods=" in chunk_logs[0]
    assert "datasets=" in chunk_logs[0]
    assert "metrics=" in chunk_logs[0]


@patch("research_index.ingest.embed", _fake_embed)
def test_map_reduce_logs_revised_eta_every_5_chunks(tmp_path, caplog):
    """Revised ETA is logged every 5 chunks."""
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    # Need enough content for >5 chunks
    md.write_text(
        "\n".join(f"Section {i}: " + f"content-{i} " * 100 for i in range(10))
    )
    ingest_file(conn, md)
    p = register_paper(conn, "Test", source_uri=str(md.resolve()))["paper_id"]
    chunks = [
        {"id": row["id"], "content": row["content"]}
        for row in conn.execute(
            "SELECT id, content FROM chunks WHERE source_uri = ? ORDER BY chunk_index",
            (str(md.resolve()),),
        ).fetchall()
    ]
    assert len(chunks) >= 5, f"Need >= 5 chunks for ETA test, got {len(chunks)}"

    with caplog.at_level(logging.INFO, logger="research_index.extraction"):
        with patch("research_index.extraction._llm_call", _mock_llm_for_progress):
            _extract_map_reduce(conn, p, chunks)

    eta_logs = [m for m in caplog.messages if "revised ETA" in m]
    # ETA logged every 5 chunks + on the last chunk (if not already a multiple of 5)
    expected_eta_count = len(chunks) // 5
    if len(chunks) % 5 != 0:
        expected_eta_count += 1  # final chunk also logs ETA
    assert len(eta_logs) == expected_eta_count


@patch("research_index.ingest.embed", _fake_embed)
def test_map_reduce_logs_failed_chunk(tmp_path, caplog):
    """Failed chunks log 'FAILED' instead of entity counts."""
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("Content " * 200)
    ingest_file(conn, md)
    p = register_paper(conn, "Test", source_uri=str(md.resolve()))["paper_id"]
    chunks = [
        {"id": row["id"], "content": row["content"]}
        for row in conn.execute(
            "SELECT id, content FROM chunks WHERE source_uri = ? ORDER BY chunk_index",
            (str(md.resolve()),),
        ).fetchall()
    ]

    call_count = {"n": 0}

    def _mock_llm_fail_first(prompt, *, conn):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ValueError("simulated LLM failure")
        return _mock_llm_for_progress(prompt, conn=conn)

    with caplog.at_level(logging.INFO, logger="research_index.extraction"):
        with patch("research_index.extraction._llm_call", _mock_llm_fail_first):
            _extract_map_reduce(conn, p, chunks)

    chunk_logs = [m for m in caplog.messages if "Chunk" in m and "/" in m]
    assert "FAILED" in chunk_logs[0]
    # Subsequent chunks should have entity counts, not FAILED
    if len(chunk_logs) > 1:
        assert "methods=" in chunk_logs[1]


@patch("research_index.ingest.embed", _fake_embed)
def test_map_reduce_logs_entity_resolution(tmp_path, caplog):
    """Map phase completion and entity resolution are logged."""
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("Content " * 200)
    ingest_file(conn, md)
    p = register_paper(conn, "Test", source_uri=str(md.resolve()))["paper_id"]
    chunks = [
        {"id": row["id"], "content": row["content"]}
        for row in conn.execute(
            "SELECT id, content FROM chunks WHERE source_uri = ? ORDER BY chunk_index",
            (str(md.resolve()),),
        ).fetchall()
    ]

    with caplog.at_level(logging.INFO, logger="research_index.extraction"):
        with patch("research_index.extraction._llm_call", _mock_llm_for_progress):
            _extract_map_reduce(conn, p, chunks)

    assert any("Map phase complete" in m for m in caplog.messages)
    assert any("Starting entity resolution" in m for m in caplog.messages)
    assert any("Entity resolution complete" in m for m in caplog.messages)


@patch("research_index.ingest.embed", _fake_embed)
def test_map_reduce_result_includes_timing(tmp_path):
    """Result dict includes extraction_seconds and resolution_seconds."""
    conn = _setup(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("Content " * 200)
    ingest_file(conn, md)
    p = register_paper(conn, "Test", source_uri=str(md.resolve()))["paper_id"]
    chunks = [
        {"id": row["id"], "content": row["content"]}
        for row in conn.execute(
            "SELECT id, content FROM chunks WHERE source_uri = ? ORDER BY chunk_index",
            (str(md.resolve()),),
        ).fetchall()
    ]

    with patch("research_index.extraction._llm_call", _mock_llm_for_progress):
        result = _extract_map_reduce(conn, p, chunks)

    assert "extraction_seconds" in result
    assert "resolution_seconds" in result
    assert isinstance(result["extraction_seconds"], float)
    assert isinstance(result["resolution_seconds"], float)
    assert result["extraction_seconds"] >= 0
    assert result["resolution_seconds"] >= 0
