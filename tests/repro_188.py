import json
from unittest.mock import patch

import pytest
from knowledge_base.db import get_connection, init_schema
from knowledge_base.exceptions import ValidationError
from knowledge_base.routes.search import search_index
from knowledge_base.search import search


def test_search_validation_exception_type(tmp_path):
    """search() should raise ValidationError, not ValueError (Issue #188)."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    with pytest.raises(ValidationError):
        search(conn, "test", mode="invalid")


def test_search_validates_source_type(tmp_path):
    """search() should reject invalid source_type values."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    with pytest.raises(ValidationError, match="source_type"):
        search(conn, "test", source_type="invalid_type")


def test_search_validates_chunk_strategy(tmp_path):
    """search() should reject invalid chunk_strategy values."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    with pytest.raises(ValidationError, match="chunk_strategy"):
        search(conn, "test", chunk_strategy="invalid_strategy")


def test_search_index_tool_returns_json_error(tmp_path):
    """search_index tool should return a JSON error instead of crashing on ValidationError."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    with patch("knowledge_base.routes.search._get_conn", return_value=conn):
        response_str = search_index("test", mode="invalid")
        response = json.loads(response_str)
        assert "error" in response
        assert "mode must be one of" in response["error"]


def test_search_validates_top_k_range(tmp_path):
    """search() should raise ValidationError for out-of-range top_k."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    with pytest.raises(ValidationError, match="top_k"):
        search(conn, "test", top_k=0)
    with pytest.raises(ValidationError, match="top_k"):
        search(conn, "test", top_k=501)
