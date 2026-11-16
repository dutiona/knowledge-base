"""The embedding declaration every test runs under unless it says otherwise.

The values are the pre-#569 bootstrap defaults, so tests written against an
ollama/bge-m3/1024 database keep their meaning. ``.test`` never resolves, so the
URL cannot reach a real service.
"""

from __future__ import annotations

TEST_EMBED_PROVIDER = "ollama"
TEST_EMBED_MODEL = "bge-m3"
TEST_EMBED_DIM = 1024
TEST_EMBED_URL = "http://embedder.example.test:11434"

DECLARATION = {
    "KNOWLEDGE_BASE_EMBED_PROVIDER": TEST_EMBED_PROVIDER,
    "KNOWLEDGE_BASE_EMBED_MODEL": TEST_EMBED_MODEL,
    "KNOWLEDGE_BASE_EMBED_DIM": str(TEST_EMBED_DIM),
    "KNOWLEDGE_BASE_EMBED_URL": TEST_EMBED_URL,
    "KNOWLEDGE_BASE_VISION": "on",
    "KNOWLEDGE_BASE_BROWSER": "on",
    "KNOWLEDGE_BASE_LLM": "on",
}
