"""Tests for conclusion recording, retrieval, supersession, and chains."""

from unittest.mock import patch

from knowledge_base.db import DEFAULT_EMBED_DIM, get_connection, init_schema
from knowledge_base.ingest import ingest_file
from knowledge_base.conclusions import (
    get_conclusion_chain,
    get_conclusions,
    record_conclusion,
    supersede_conclusion,
)


def _fake_embed(texts, model="bge-m3", expected_dim=None, **_kwargs):
    dim = expected_dim if expected_dim is not None else DEFAULT_EMBED_DIM
    return [[0.1] * dim for _ in texts]


def _setup(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    return conn


# --- record_conclusion ---


def test_record_conclusion_basic(tmp_path):
    conn = _setup(tmp_path)
    result = record_conclusion(
        conn, "Transformers outperform RNNs on translation tasks", 0.9
    )
    assert "conclusion_id" in result

    conclusions = get_conclusions(conn)
    assert len(conclusions) == 1
    assert (
        conclusions[0]["claim"] == "Transformers outperform RNNs on translation tasks"
    )
    assert conclusions[0]["confidence"] == 0.9


@patch("knowledge_base.ingest.embed", _fake_embed)
def test_record_conclusion_with_evidence(tmp_path):
    conn = _setup(tmp_path)
    md = tmp_path / "evidence.md"
    md.write_text("BLEU score improved from 28.4 to 41.0 with transformers.\n")
    ingest_file(conn, md)
    chunk_id = conn.execute("SELECT id FROM chunks LIMIT 1").fetchone()["id"]

    result = record_conclusion(
        conn, "Transformers improve BLEU", 0.95, [chunk_id], "Comparing Table 2 results"
    )
    assert "conclusion_id" in result

    conclusions = get_conclusions(conn)
    assert conclusions[0]["source_chunk_ids"] == [chunk_id]
    assert len(conclusions[0]["source_chunks"]) == 1
    assert "BLEU" in conclusions[0]["source_chunks"][0]["content"]
    assert conclusions[0]["session_context"] == "Comparing Table 2 results"


def test_record_conclusion_invalid_chunk_ids(tmp_path):
    conn = _setup(tmp_path)
    result = record_conclusion(conn, "Bad claim", source_chunk_ids=[999, 1000])
    assert "error" in result
    assert "999" in result["error"]


# --- get_conclusions ---


def test_get_conclusions_keyword_filter(tmp_path):
    conn = _setup(tmp_path)
    record_conclusion(conn, "Attention improves accuracy")
    record_conclusion(conn, "Dropout prevents overfitting")

    results = get_conclusions(conn, keyword="Attention")
    assert len(results) == 1
    assert "Attention" in results[0]["claim"]


def test_get_conclusions_confidence_filter(tmp_path):
    conn = _setup(tmp_path)
    record_conclusion(conn, "High confidence claim", 0.9)
    record_conclusion(conn, "Low confidence claim", 0.3)

    results = get_conclusions(conn, min_confidence=0.5)
    assert len(results) == 1
    assert results[0]["claim"] == "High confidence claim"


# --- supersede_conclusion ---


def test_supersede_conclusion(tmp_path):
    conn = _setup(tmp_path)
    r1 = record_conclusion(conn, "Initial finding", 0.7)
    old_id = r1["conclusion_id"]

    r2 = supersede_conclusion(conn, old_id, "Revised finding with more data", 0.9)
    assert r2["old_conclusion_id"] == old_id
    assert "new_conclusion_id" in r2

    # Old conclusion should be filtered by default
    active = get_conclusions(conn)
    assert len(active) == 1
    assert active[0]["claim"] == "Revised finding with more data"

    # Include superseded
    all_conclusions = get_conclusions(conn, include_superseded=True)
    assert len(all_conclusions) == 2


def test_supersede_already_superseded(tmp_path):
    conn = _setup(tmp_path)
    r1 = record_conclusion(conn, "Original", 0.5)
    supersede_conclusion(conn, r1["conclusion_id"], "Updated", 0.8)
    result = supersede_conclusion(conn, r1["conclusion_id"], "Double supersede attempt")
    assert "error" in result
    assert "already superseded" in result["error"]


def test_conclusion_confidence_range(tmp_path):
    conn = _setup(tmp_path)
    result = record_conclusion(conn, "Too confident", confidence=1.5)
    assert "error" in result

    result = record_conclusion(conn, "Negative", confidence=-0.1)
    assert "error" in result


def test_supersede_nonexistent(tmp_path):
    conn = _setup(tmp_path)
    result = supersede_conclusion(conn, 999, "New claim")
    assert "error" in result


# --- get_conclusion_chain ---


def test_conclusion_chain(tmp_path):
    conn = _setup(tmp_path)
    r1 = record_conclusion(conn, "V1: initial observation", 0.5)
    r2 = supersede_conclusion(
        conn, r1["conclusion_id"], "V2: refined with more data", 0.7
    )
    r3 = supersede_conclusion(
        conn, r2["new_conclusion_id"], "V3: confirmed by replication", 0.95
    )

    chain = get_conclusion_chain(conn, r1["conclusion_id"])
    assert len(chain) == 3
    assert chain[0]["claim"].startswith("V1")
    assert chain[1]["claim"].startswith("V2")
    assert chain[2]["claim"].startswith("V3")

    # Same chain regardless of which ID we start from
    chain_from_middle = get_conclusion_chain(conn, r2["new_conclusion_id"])
    assert len(chain_from_middle) == 3

    chain_from_end = get_conclusion_chain(conn, r3["new_conclusion_id"])
    assert len(chain_from_end) == 3


def test_conclusion_chain_single(tmp_path):
    conn = _setup(tmp_path)
    r = record_conclusion(conn, "Standalone conclusion")
    chain = get_conclusion_chain(conn, r["conclusion_id"])
    assert len(chain) == 1
    assert chain[0]["claim"] == "Standalone conclusion"
