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
    # Every term is unconditionally double-quoted
    assert result == '"rust" OR "error" OR "async"'


def test_build_fts_query_empty():
    assert build_fts_query([]) == ""


def test_build_fts_query_single():
    assert build_fts_query(["transformer"]) == '"transformer"'


def test_build_fts_query_quotes_special_chars():
    result = build_fts_query(["self-supervised", "pre-training"])
    assert '"self-supervised"' in result
    assert '"pre-training"' in result


def test_build_fts_query_quotes_fts_operators():
    # FTS5 operators are now safely quoted instead of stripped
    result = build_fts_query(["near", "transformer"])
    assert '"near"' in result
    assert '"transformer"' in result


def test_build_fts_query_operators_not_stripped():
    result = build_fts_query(["and", "or", "not", "near"])
    assert result == '"and" OR "or" OR "not" OR "near"'


def test_build_fts_query_escapes_embedded_quotes():
    result = build_fts_query(['foo"bar'])
    assert result == '"foo""bar"'


def test_build_fts_query_neutralises_prefix_star():
    result = build_fts_query(["transform*"])
    assert result == '"transform*"'


def test_unicode_terms_preserved():
    keywords = extract_keywords("naïve Bayes classifier for Gödel numbering")
    assert "naïve" in keywords
    assert "gödel" in keywords


def test_single_char_language_identifier():
    keywords = extract_keywords("C memory safety")
    assert "c" in keywords
    assert "memory" in keywords
    assert "safety" in keywords


def test_single_char_with_punctuation():
    """C followed by punctuation should still be recognized as an identifier."""
    keywords = extract_keywords("C, memory safety")
    assert "c" in keywords
    assert "memory" in keywords


def test_contraction_fragments_filtered():
    """Contraction fragments like 'won' from 'won't' should be stopwords."""
    keywords = extract_keywords("won something special")
    assert "won" not in keywords
    assert "something" in keywords


def test_single_char_lowercase_not_kept():
    # "a" is a stopword, but even if it weren't, lowercase single chars
    # from within words should not leak through
    keywords = extract_keywords("something about testing")
    assert all(len(k) > 1 for k in keywords)
