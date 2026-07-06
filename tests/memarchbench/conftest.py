"""Fixtures for the MemArch-Bench KB slice (#360).

Two execution tiers:

- **Hermetic suites** (supersession, entity stability, prediction errors):
  build their own tmp-path DB via ``mb_conn`` — run on every PR.
- **Corpus-bound suite** (retrieval quality): needs a real ingested corpus.
  Points at it via the ``MEMARCH_CORPUS_DB`` environment variable and SKIPS
  when unset — CI runs it only where a corpus artifact is available
  (resolves #360 open question 3: per-PR for hermetic suites, corpus-gated
  for retrieval quality).

Golden-set fixtures live in ``tests/memarchbench/fixtures/`` (resolves #360
open question 1: in-repo, no submodule). Format locked on #250:
``golden_queries.jsonl`` — see ``load_golden_set``.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from knowledge_base.db import get_connection, init_schema

FIXTURES_DIR = Path(__file__).parent / "fixtures"
GOLDEN_SET = FIXTURES_DIR / "golden_queries.jsonl"


@pytest.fixture
def mb_conn(tmp_path) -> Iterator[sqlite3.Connection]:
    """Fresh schema-initialized tmp DB for the hermetic suites."""
    conn = get_connection(tmp_path / "memarchbench.db")
    init_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="session")
def corpus_conn() -> Iterator[sqlite3.Connection]:
    """Read-only connection to the real ingested corpus (retrieval quality only).

    Skips the requesting test when MEMARCH_CORPUS_DB is unset or missing —
    the hermetic suites never request this fixture.
    """
    db = os.environ.get("MEMARCH_CORPUS_DB")
    if not db or not Path(db).is_file():
        pytest.skip("MEMARCH_CORPUS_DB not set — retrieval-quality suite is corpus-gated (#360/#250)")
    conn = get_connection(Path(db))
    try:
        yield conn
    finally:
        conn.close()


def load_golden_set() -> list[dict]:
    """Load the #250 golden set (JSONL, one query per line).

    Schema per the locked format (#250):
    id, query, source (query_log|seeded), type (single_hop|multi_hop|
    comparison|temporal), judgments[{doc, quote, content_hash?, relevance}],
    plus optional query_log_id / answer_note / curated_at.
    """
    if not GOLDEN_SET.is_file():
        return []
    with GOLDEN_SET.open() as f:
        return [json.loads(line) for line in f if line.strip()]


@pytest.fixture(scope="session")
def golden_set() -> list[dict]:
    queries = load_golden_set()
    if not queries:
        pytest.skip("golden_queries.jsonl absent or empty — curation pending (#250)")
    return queries
