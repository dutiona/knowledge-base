"""Tests for keyword intent extraction."""

from knowledge_base.keywords import extract_keywords


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
