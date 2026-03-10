"""Embedding client for Ollama.

Auto-detects Ollama URL: OLLAMA_HOST env > WSL2 Windows host > localhost.
Works on both WSL2 (Windows host Ollama) and baremetal Linux (local Ollama).
"""

from __future__ import annotations

import os
import subprocess

import httpx

from .db import DEFAULT_EMBED_DIM

_OLLAMA_URL: str | None = None


def _get_ollama_url() -> str:
    global _OLLAMA_URL
    if _OLLAMA_URL is not None:
        return _OLLAMA_URL

    # 1. Explicit env override
    env_host = os.environ.get("OLLAMA_HOST")
    if env_host:
        _OLLAMA_URL = env_host if env_host.startswith("http") else f"http://{env_host}"
        return _OLLAMA_URL

    # 2. WSL2: try Windows host via default gateway
    if os.environ.get("WSL_DISTRO_NAME"):
        try:
            result = subprocess.run(
                ["ip", "route", "show", "default"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            gateway = result.stdout.split()[2]
            url = f"http://{gateway}:11434"
            resp = httpx.get(f"{url}/api/tags", timeout=3)
            if resp.status_code == 200:
                _OLLAMA_URL = url
                return _OLLAMA_URL
        except Exception:
            pass

    # 3. Localhost (baremetal Linux or WSL2 fallback)
    _OLLAMA_URL = "http://localhost:11434"
    return _OLLAMA_URL


def embed(
    texts: list[str],
    model: str = "bge-m3",
    expected_dim: int | None = None,
) -> list[list[float]]:
    dim = expected_dim if expected_dim is not None else DEFAULT_EMBED_DIM
    url = _get_ollama_url()
    results = []
    # Batch in groups of 32
    for i in range(0, len(texts), 32):
        batch = texts[i : i + 32]
        resp = httpx.post(
            f"{url}/api/embed",
            json={"model": model, "input": batch},
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        embeddings = data["embeddings"]
        for emb in embeddings:
            if len(emb) != dim:
                raise ValueError(f"Expected {dim} dims, got {len(emb)}")
            results.append(emb)
    return results


def embed_single(text: str, model: str = "bge-m3") -> list[float]:
    return embed([text], model)[0]
