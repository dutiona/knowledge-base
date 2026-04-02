"""Tests for the chunking module's public API.

Detailed tests for chunk_markdown and chunk_by_section live in
test_markdown_chunking.py and test_section_chunking.py respectively.
chunk_text and chunk_python_ast are also exercised in test_ingest.py.
This file validates the public surface and import paths.
"""

from __future__ import annotations

from knowledge_base.chunking import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    IMAGE_REF_RE,
    chunk_by_section,
    chunk_markdown,
    chunk_python_ast,
    chunk_text,
    heading_level,
    pages_for_range,
    sanitize_image_refs,
)


# --- Constants ---


def test_constants():
    assert CHUNK_SIZE == 1000
    assert CHUNK_OVERLAP == 200


# --- chunk_text ---


def test_chunk_text_basic():
    text = "a" * 2000
    chunks = chunk_text(text, size=1000, overlap=200)
    assert len(chunks) == 3
    assert len(chunks[0]) == 1000


def test_chunk_text_empty():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


# --- heading_level ---


def test_heading_level():
    assert heading_level("# Title\nBody") == 1
    assert heading_level("### Sub\nBody") == 3
    assert heading_level("No heading here") is None


# --- pages_for_range ---


def test_pages_for_range_empty_map():
    assert pages_for_range(0, 100, {}) == []


def test_pages_for_range_basic():
    page_map = {0: 1, 500: 2, 1000: 3}
    assert pages_for_range(0, 400, page_map) == [1]
    assert pages_for_range(400, 600, page_map) == [1, 2]


# --- sanitize_image_refs ---


def test_sanitize_image_refs_no_dir():
    text = "![fig](/abs/path/image.png)"
    assert sanitize_image_refs(text) == "![fig](image.png)"


# --- IMAGE_REF_RE ---


def test_image_ref_re():
    m = IMAGE_REF_RE.search("![alt](path/to/img.png)")
    assert m is not None
    assert m.group(1) == "alt"
    assert m.group(2) == "path/to/img.png"


# --- chunk_markdown ---


def test_chunk_markdown_empty():
    assert chunk_markdown("") == []


# --- chunk_by_section ---


def test_chunk_by_section_empty():
    assert chunk_by_section("") == []


# --- chunk_python_ast ---


def test_chunk_python_ast_empty():
    assert chunk_python_ast("") == []


def test_chunk_python_ast_syntax_error():
    assert chunk_python_ast("def broken(:\n    pass\n") == []
