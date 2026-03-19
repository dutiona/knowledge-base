"""Tests for keyword intent extraction."""

from knowledge_base.keywords import build_fts_query, extract_keywords


def test_extracts_intent_keywords():
    query = "What are the best practices for Rust error handling in async code?"
    keywords = extract_keywords(query)
    assert "rust" in keywords
    assert "error" in keywords or "error handling" in keywords
    assert "async" in keywords
    # Stopwords stripped
    assert "what" not in keywords
    assert "are" not in keywords
    assert "the" not in keywords
    assert "for" not in keywords
    assert "in" not in keywords


def test_short_query_returns_all_content_words():
    keywords = extract_keywords("transformer attention")
    assert "transformer" in keywords
    assert "attention" in keywords


def test_empty_query_returns_empty():
    assert extract_keywords("") == []
    assert extract_keywords("   ") == []


def test_stopword_only_query_returns_empty():
    assert extract_keywords("what is the") == []


def test_preserves_technical_terms():
    keywords = extract_keywords("ResNet-50 accuracy on ImageNet")
    assert any("resnet" in k for k in keywords)
    assert "accuracy" in keywords
    assert any("imagenet" in k for k in keywords)


def test_max_keywords_limit():
    query = "deep learning neural network transformer attention mechanism self-supervised contrastive"
    keywords = extract_keywords(query, max_keywords=3)
    assert len(keywords) <= 3


def test_hyphenated_terms_kept():
    keywords = extract_keywords("self-supervised pre-training methods")
    # Hyphenated compounds should survive as single terms
    assert any("self-supervised" in k or "self" in k for k in keywords)


def test_numeric_terms_preserved():
    keywords = extract_keywords("GPT-4 vs GPT-3.5 performance")
    assert any("gpt" in k for k in keywords)
    assert "performance" in keywords


def test_build_fts_query_from_keywords():
    result = build_fts_query(["rust", "error", "async"])
    # Should produce OR-joined FTS5 query
    assert result == "rust OR error OR async"


def test_build_fts_query_empty():
    assert build_fts_query([]) == ""


def test_build_fts_query_single():
    assert build_fts_query(["transformer"]) == "transformer"


def test_build_fts_query_escapes_special_chars():
    # FTS5 special: AND, OR, NOT, NEAR, quotes, parens, asterisk, caret
    result = build_fts_query(["self-supervised", "pre-training"])
    # Hyphens inside terms should be quoted to prevent FTS5 treating them as operators
    assert '"self-supervised"' in result
    assert '"pre-training"' in result


def test_build_fts_query_strips_fts_operators():
    # If a keyword happens to be an FTS operator, skip it
    result = build_fts_query(["near", "transformer"])
    assert "transformer" in result
    assert "near" not in result.split()


def test_build_fts_query_all_operators_returns_empty():
    assert build_fts_query(["and", "or", "not", "near"]) == ""
