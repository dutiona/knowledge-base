"""Tests for structured extraction (methods, datasets, metrics)."""

import json
from unittest.mock import patch

from research_index.db import EMBED_DIM, get_connection, init_schema
from research_index.extraction import (
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

    result = record_metric(conn, "accuracy", 76.1, p, method_id=m, dataset_id=d, unit="%")
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

    record_metric(conn, "top-1 accuracy", 76.1, p1, method_id=m1, dataset_id=d1, unit="%")
    record_metric(conn, "top-1 accuracy", 81.3, p2, method_id=m2, dataset_id=d2, unit="%")

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

FAKE_LLM_RESPONSE = json.dumps({
    "methods": [{"name": "BERT", "description": "Bidirectional encoder"}],
    "datasets": [{"name": "GLUE", "description": "NLU benchmark"}],
    "metrics": [
        {"metric": "accuracy", "value": 88.5, "unit": "%", "method": "BERT", "dataset": "GLUE"}
    ],
})


@patch("research_index.ingest.embed", _fake_embed)
def test_extract_structure_basic(tmp_path):
    conn = _setup(tmp_path)

    md = tmp_path / "paper.md"
    md.write_text("BERT achieves 88.5% accuracy on GLUE benchmark.\n")
    ingest_file(conn, md)

    p = register_paper(conn, "BERT Paper", source_uri=str(md.resolve()))["paper_id"]

    def _mock_llm_extract(prompt):
        return FAKE_LLM_RESPONSE

    with patch("research_index.extraction._llm_extract", _mock_llm_extract):
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
