# Folder-Level Semantic Embeddings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Embed folder-level summaries and use them as a cheap context signal to boost search results from semantically-relevant directories. Fixes #126.

**Architecture:** New `folder_summaries` table stores per-folder summaries with embeddings. After ingestion, the parent folder's summary is recomputed from its document titles/abstracts. During search, the query embedding is compared against folder embeddings, and candidates from high-similarity folders receive a configurable RRF score multiplier (default 1.15). A new `folder_summaries.py` module owns all folder summary logic — both computation and search-time boosting.

**Tech Stack:** SQLite + sqlite-vec (existing), Ollama embeddings (existing `_embed_with_config`), `posixpath` for folder extraction from `source_uri`.

---

## File Structure

| Action | File                                     | Responsibility                                                                        |
| ------ | ---------------------------------------- | ------------------------------------------------------------------------------------- |
| Create | `src/knowledge_base/folder_summaries.py` | Folder summary computation, storage, staleness detection, search boost                |
| Modify | `src/knowledge_base/db.py`               | New `folder_summaries` table + `folder_summaries_vec` in `init_schema`, new migration |
| Modify | `src/knowledge_base/ingest.py`           | Call `update_folder_summary()` after `ingest_file` commits                            |
| Modify | `src/knowledge_base/search.py`           | Apply folder boost to RRF scores before final ranking                                 |
| Modify | `src/knowledge_base/embed_swap.py`       | Re-embed folder summaries during model swap                                           |
| Modify | `src/knowledge_base/server.py`           | Expose `folder_summaries_status` MCP tool                                             |
| Create | `tests/test_folder_summaries.py`         | Unit tests for folder summary logic                                                   |
| Modify | `tests/test_search.py`                   | Add `folder_summaries.embed` mock to existing tests                                   |

---

### Task 1: Schema — `folder_summaries` Table + Vec Table

**Files:**

- Modify: `src/knowledge_base/db.py:188-405` (inside `init_schema`)

- [ ] **Step 1: Write the failing test for schema creation**

Create `tests/test_folder_summaries.py`:

```python
"""Tests for folder-level semantic embeddings."""

from knowledge_base.db import get_connection, init_schema


def test_folder_summaries_table_exists(tmp_path):
    """init_schema creates folder_summaries and folder_summaries_vec tables."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table')"
        ).fetchall()
    }
    assert "folder_summaries" in tables
    assert "folder_summaries_vec" in tables


def test_folder_summaries_columns(tmp_path):
    """folder_summaries has the expected columns."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(folder_summaries)").fetchall()
    }
    assert cols == {"folder_path", "summary", "content_hash", "updated_at"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings && uv run pytest tests/test_folder_summaries.py -v`
Expected: FAIL — `folder_summaries` table does not exist

- [ ] **Step 3: Add schema to `db.py`**

In `init_schema()`, after the `jobs` table block (after line 381), add:

```python
    # --- Folder summaries for search context boosting (#126) ---
    conn.executescript(f"""
    CREATE TABLE IF NOT EXISTS folder_summaries (
        folder_path TEXT PRIMARY KEY,
        summary TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE VIRTUAL TABLE IF NOT EXISTS folder_summaries_vec USING vec0(
        embedding float[{embed_dim}],
        +folder_path TEXT
    );
    """)
```

Note: `embed_dim` is already available in scope (read from config at line 213). The `folder_path` auxiliary column lets us join back to `folder_summaries` without a rowid lookup.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings && uv run pytest tests/test_folder_summaries.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings
git add src/knowledge_base/db.py tests/test_folder_summaries.py
git commit -m "feat(db): add folder_summaries and folder_summaries_vec tables

Schema for folder-level semantic embeddings (#126).
Stores per-folder summaries with content hashes for staleness
detection, plus a sqlite-vec table for cosine similarity lookups."
```

---

### Task 2: Core Module — `folder_summaries.py` (Computation + Storage)

**Files:**

- Create: `src/knowledge_base/folder_summaries.py`
- Modify: `tests/test_folder_summaries.py`

This task implements: content hash computation from folder contents, summary text generation (concatenation of titles/first-chunks), embedding, and upsert into `folder_summaries` + `folder_summaries_vec`.

- [ ] **Step 1: Write failing tests for content hash and staleness detection**

Append to `tests/test_folder_summaries.py`:

```python
from unittest.mock import patch

from knowledge_base.db import DEFAULT_EMBED_DIM
from knowledge_base.ingest import ingest_file
from knowledge_base.folder_summaries import (
    compute_folder_hash,
    update_folder_summary,
)


def _fake_embed(texts, model="bge-m3", expected_dim=None):
    dim = expected_dim if expected_dim is not None else DEFAULT_EMBED_DIM
    return [[0.1] * dim for _ in texts]


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_compute_folder_hash_changes_with_content(tmp_path):
    """Hash changes when folder contents change."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    folder = tmp_path / "papers"
    folder.mkdir()
    (folder / "a.md").write_text("Paper about attention mechanisms.\n")
    ingest_file(conn, folder / "a.md")

    hash1 = compute_folder_hash(conn, str(folder))

    (folder / "b.md").write_text("Paper about transformers.\n")
    ingest_file(conn, folder / "b.md")

    hash2 = compute_folder_hash(conn, str(folder))
    assert hash1 != hash2


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_update_folder_summary_creates_entry(tmp_path):
    """update_folder_summary inserts a new folder summary."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    folder = tmp_path / "papers"
    folder.mkdir()
    (folder / "a.md").write_text("Paper about attention mechanisms.\n")
    ingest_file(conn, folder / "a.md")

    updated = update_folder_summary(conn, str(folder))
    assert updated is True

    row = conn.execute(
        "SELECT * FROM folder_summaries WHERE folder_path = ?",
        (str(folder),),
    ).fetchone()
    assert row is not None
    assert "attention" in row["summary"].lower()


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_update_folder_summary_skips_when_unchanged(tmp_path):
    """update_folder_summary returns False when content hash hasn't changed."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    folder = tmp_path / "papers"
    folder.mkdir()
    (folder / "a.md").write_text("Paper about attention.\n")
    ingest_file(conn, folder / "a.md")

    assert update_folder_summary(conn, str(folder)) is True
    assert update_folder_summary(conn, str(folder)) is False


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_update_folder_summary_updates_stale_entry(tmp_path):
    """update_folder_summary updates when content changes."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    folder = tmp_path / "papers"
    folder.mkdir()
    (folder / "a.md").write_text("Paper about attention.\n")
    ingest_file(conn, folder / "a.md")

    update_folder_summary(conn, str(folder))
    old_row = conn.execute(
        "SELECT summary FROM folder_summaries WHERE folder_path = ?",
        (str(folder),),
    ).fetchone()

    (folder / "b.md").write_text("Paper about diffusion models.\n")
    ingest_file(conn, folder / "b.md")

    assert update_folder_summary(conn, str(folder)) is True
    new_row = conn.execute(
        "SELECT summary FROM folder_summaries WHERE folder_path = ?",
        (str(folder),),
    ).fetchone()
    assert new_row["summary"] != old_row["summary"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings && uv run pytest tests/test_folder_summaries.py -v -k "not table"`
Expected: FAIL — `folder_summaries` module does not exist

- [ ] **Step 3: Implement `folder_summaries.py`**

Create `src/knowledge_base/folder_summaries.py`:

```python
"""Folder-level semantic embeddings for search context boosting.

Computes and stores per-folder summaries with embedding vectors.
Used at search time to boost results from semantically relevant directories.
"""

from __future__ import annotations

import hashlib
import sqlite3
import struct

from .embed_swap import get_embed_config
from .embeddings import embed


def _serialize_f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def compute_folder_hash(conn: sqlite3.Connection, folder_path: str) -> str:
    """Compute a content hash for a folder from its chunks' content hashes.

    The hash is derived from the sorted set of content_hash values for
    all chunks whose source_uri starts with the folder path. If no
    documents changed, the hash stays the same.
    """
    prefix = folder_path.rstrip("/") + "/"
    rows = conn.execute(
        """SELECT DISTINCT content_hash FROM chunks
           WHERE source_uri LIKE ? || '%'
             AND source_uri NOT LIKE ? || '%/%'
           ORDER BY content_hash""",
        (prefix, prefix),
    ).fetchall()
    if not rows:
        return ""
    combined = "|".join(row["content_hash"] for row in rows)
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


def _build_folder_summary(conn: sqlite3.Connection, folder_path: str) -> str:
    """Build a summary string for a folder from its documents.

    Concatenates the first chunk of each unique source_uri in the folder
    (truncated to 200 chars each), separated by newlines.
    """
    prefix = folder_path.rstrip("/") + "/"
    rows = conn.execute(
        """SELECT source_uri, content FROM chunks
           WHERE source_uri LIKE ? || '%'
             AND source_uri NOT LIKE ? || '%/%'
             AND chunk_index = 0
           ORDER BY source_uri""",
        (prefix, prefix),
    ).fetchall()
    parts = []
    for row in rows:
        # Use filename as title + first 200 chars of content
        filename = row["source_uri"].rsplit("/", 1)[-1]
        snippet = row["content"][:200].replace("\n", " ").strip()
        parts.append(f"{filename}: {snippet}")
    return "\n".join(parts)


def update_folder_summary(
    conn: sqlite3.Connection,
    folder_path: str,
) -> bool:
    """Recompute folder summary and embedding if content changed.

    Returns True if the summary was created or updated, False if skipped
    (content unchanged).
    """
    folder_path = folder_path.rstrip("/")
    current_hash = compute_folder_hash(conn, folder_path)

    if not current_hash:
        # No chunks in this folder — clean up stale entry if present
        conn.execute(
            "DELETE FROM folder_summaries WHERE folder_path = ?", (folder_path,)
        )
        conn.execute(
            "DELETE FROM folder_summaries_vec WHERE folder_path = ?",
            (folder_path,),
        )
        conn.commit()
        return False

    # Check for staleness
    existing = conn.execute(
        "SELECT content_hash FROM folder_summaries WHERE folder_path = ?",
        (folder_path,),
    ).fetchone()
    if existing and existing["content_hash"] == current_hash:
        return False

    # Build summary and embed
    summary = _build_folder_summary(conn, folder_path)
    if not summary:
        return False

    cfg = get_embed_config(conn)
    embedding = embed([summary], model=cfg["model"], expected_dim=cfg["dim"])[0]

    # Upsert folder_summaries
    conn.execute(
        """INSERT INTO folder_summaries (folder_path, summary, content_hash, updated_at)
           VALUES (?, ?, ?, datetime('now'))
           ON CONFLICT(folder_path) DO UPDATE SET
               summary = excluded.summary,
               content_hash = excluded.content_hash,
               updated_at = excluded.updated_at""",
        (folder_path, summary, current_hash),
    )

    # Upsert folder_summaries_vec (delete + insert since vec0 has no ON CONFLICT)
    conn.execute(
        "DELETE FROM folder_summaries_vec WHERE folder_path = ?",
        (folder_path,),
    )
    conn.execute(
        "INSERT INTO folder_summaries_vec (embedding, folder_path) VALUES (?, ?)",
        (_serialize_f32(embedding), folder_path),
    )

    conn.commit()
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings && uv run pytest tests/test_folder_summaries.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings && uv run pytest tests/ -q`
Expected: All 362+ tests pass

- [ ] **Step 6: Commit**

```bash
cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings
git add src/knowledge_base/folder_summaries.py tests/test_folder_summaries.py
git commit -m "feat: add folder summary computation and storage

Implements folder_summaries.py with:
- Content hash from sorted chunk hashes (staleness detection)
- Summary built from first chunk of each document in folder
- Embedding via configured model + upsert into vec table
- Skip when content hash unchanged (no wasted LLM calls)"
```

---

### Task 3: Ingest Integration — Trigger Folder Summary After Ingestion

**Files:**

- Modify: `src/knowledge_base/ingest.py:451-585` (`ingest_file` function)
- Modify: `tests/test_folder_summaries.py`

- [ ] **Step 1: Write failing test for ingest triggering folder summary**

Append to `tests/test_folder_summaries.py`:

```python
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_ingest_file_triggers_folder_summary(tmp_path):
    """Ingesting a file automatically creates/updates its folder's summary."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    folder = tmp_path / "papers"
    folder.mkdir()
    (folder / "a.md").write_text("Paper about attention mechanisms.\n")
    ingest_file(conn, folder / "a.md")

    row = conn.execute(
        "SELECT * FROM folder_summaries WHERE folder_path = ?",
        (str(folder),),
    ).fetchone()
    assert row is not None
    assert row["summary"]  # non-empty
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings && uv run pytest tests/test_folder_summaries.py::test_ingest_file_triggers_folder_summary -v`
Expected: FAIL — no folder_summaries row created by ingest_file

- [ ] **Step 3: Add folder summary call to `ingest_file`**

In `src/knowledge_base/ingest.py`, add import at top (after existing imports):

```python
from .folder_summaries import update_folder_summary
```

At the end of `ingest_file()`, after `conn.commit()` (line 580) and before the `return` (line 581), add:

```python
    # Update folder-level summary embedding (#126)
    folder = str(path.parent)
    try:
        update_folder_summary(conn, folder)
    except Exception:
        logger.warning("Failed to update folder summary for %s", folder, exc_info=True)
```

Similarly, at the end of `reingest_file()`, after `conn.commit()` (line 811) and before the `return` (line 812), add the same block:

```python
    # Update folder-level summary embedding (#126)
    folder = str(path.parent)
    try:
        update_folder_summary(conn, folder)
    except Exception:
        logger.warning("Failed to update folder summary for %s", folder, exc_info=True)
```

The `try/except` ensures a folder summary failure never breaks ingestion — the folder summary is a best-effort enhancement.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings && uv run pytest tests/test_folder_summaries.py::test_ingest_file_triggers_folder_summary -v`
Expected: PASS

- [ ] **Step 5: Patch `folder_summaries.embed` in existing test files**

After adding the `update_folder_summary` call to `ingest_file`, every test that patches `knowledge_base.ingest.embed` and calls `ingest_file` will now also trigger `folder_summaries.embed`. Without patching it, these tests will attempt real Ollama HTTP calls (caught by the try/except, but noisy and slow).

In `tests/test_search.py`, add `_fake_embed` if not already present, and add `@patch("knowledge_base.folder_summaries.embed", _fake_embed)` to every test function that already patches `knowledge_base.ingest.embed`. Do the same in `tests/test_ingest.py` and any other test file that calls `ingest_file`.

- [ ] **Step 6: Run full test suite**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings && uv run pytest tests/ -q`
Expected: All tests pass with no Ollama connection warnings.

- [ ] **Step 7: Commit**

```bash
cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings
git add src/knowledge_base/ingest.py tests/
git commit -m "feat(ingest): trigger folder summary update after file ingestion

After ingest_file() and reingest_file() commit, recompute the
parent folder's summary embedding. Wrapped in try/except so
folder summary failures never break ingestion."
```

---

### Task 4: Search Integration — Folder Boost in Hybrid Search

**Files:**

- Modify: `src/knowledge_base/search.py:79-171` (`search` function)
- Modify: `tests/test_search.py`
- Modify: `tests/test_folder_summaries.py`

This is the core search quality improvement. After RRF merge produces `(chunk_id, score)` pairs, we:

1. Look up each candidate's `source_uri` to extract its parent folder
2. Compare the query embedding against folder embeddings
3. Multiply scores for candidates from high-similarity folders

- [ ] **Step 1: Write failing test for folder boost**

Append to `tests/test_folder_summaries.py`:

```python
from knowledge_base.search import search, _folder_boost, _serialize_f32


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", lambda text, model="bge-m3": [0.1] * DEFAULT_EMBED_DIM)
def test_search_folder_summaries_populated(tmp_path):
    """Ingesting files into folders creates folder summaries and vec entries."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    ml_folder = tmp_path / "machine-learning"
    ml_folder.mkdir()
    (ml_folder / "attention.md").write_text(
        "Attention mechanisms in neural networks enable selective focus on input.\n"
    )
    ingest_file(conn, ml_folder / "attention.md")

    bio_folder = tmp_path / "biology"
    bio_folder.mkdir()
    (bio_folder / "cells.md").write_text(
        "Cell biology studies the structure and function of living organisms.\n"
    )
    ingest_file(conn, bio_folder / "cells.md")

    assert conn.execute("SELECT count(*) FROM folder_summaries").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM folder_summaries_vec").fetchone()[0] == 2


def test_folder_boost_multiplies_scores(tmp_path):
    """_folder_boost multiplies scores for chunks in matching folders."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Manually insert two chunks in different folders
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) VALUES (?, ?, ?, ?, ?)",
        ("h1", "attention content", "markdown", "/papers/ml/a.md", 0),
    )
    conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) VALUES (?, ?, ?, ?, ?)",
        ("h2", "biology content", "markdown", "/papers/bio/b.md", 0),
    )
    chunk_ids = [1, 2]
    dim = DEFAULT_EMBED_DIM

    # Insert a folder summary vec for /papers/ml (but not /papers/bio)
    conn.execute(
        "INSERT INTO folder_summaries (folder_path, summary, content_hash) VALUES (?, ?, ?)",
        ("/papers/ml", "ml summary", "hash1"),
    )
    conn.execute(
        "INSERT INTO folder_summaries_vec (embedding, folder_path) VALUES (?, ?)",
        (_serialize_f32([0.1] * dim), "/papers/ml"),
    )
    conn.commit()

    scores = {1: 0.5, 2: 0.5}
    query_embedding = [0.1] * dim
    boosted = _folder_boost(conn, query_embedding, chunk_ids, scores)

    # Chunk 1 (/papers/ml) should be boosted, chunk 2 (/papers/bio) should not
    assert boosted[1] > scores[1]
    assert boosted[2] == scores[2]
```

- [ ] **Step 2: Run tests to verify they pass after boost implementation**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings && uv run pytest tests/test_folder_summaries.py::test_search_folder_summaries_populated tests/test_folder_summaries.py::test_folder_boost_multiplies_scores -v`
Expected: PASS (the plumbing test passes immediately; the boost unit test passes after Step 3)

- [ ] **Step 3: Write the folder boost function in `search.py`**

In `src/knowledge_base/search.py`, add imports at top:

```python
from .folder_summaries import _serialize_f32 as _serialize_f32_folder
```

Wait — `_serialize_f32` is already defined locally in `search.py`. We'll reuse it. Instead, add after `_rrf_merge`:

```python
def _folder_boost(
    conn: sqlite3.Connection,
    query_embedding: list[float],
    chunk_ids: list[int],
    scores: dict[int, float],
    boost_factor: float = 1.15,
    top_folders: int = 5,
) -> dict[int, float]:
    """Apply a score multiplier to chunks from semantically relevant folders.

    Compares query_embedding against folder_summaries_vec embeddings.
    Chunks whose source_uri parent folder matches a top-scoring folder
    get their RRF score multiplied by boost_factor.

    Returns a new scores dict with boosted values.
    """
    if not chunk_ids:
        return scores

    # Check if folder_summaries_vec has any rows
    has_folders = conn.execute(
        "SELECT 1 FROM folder_summaries_vec LIMIT 1"
    ).fetchone()
    if not has_folders:
        return scores

    # Find top matching folders
    folder_rows = conn.execute(
        """SELECT folder_path, distance
           FROM folder_summaries_vec
           WHERE embedding MATCH ?
           ORDER BY distance
           LIMIT ?""",
        (_serialize_f32(query_embedding), top_folders),
    ).fetchall()
    if not folder_rows:
        return scores

    # Use the top folder's distance as threshold — boost folders within 2x of best
    best_distance = folder_rows[0]["distance"]
    boosted_folders = set()
    for row in folder_rows:
        if best_distance == 0 or row["distance"] <= best_distance * 2:
            boosted_folders.add(row["folder_path"])

    if not boosted_folders:
        return scores

    # Look up source_uri for each candidate chunk (batched for safety)
    from .db import _batched_select

    uri_rows = _batched_select(
        conn, "SELECT id, source_uri FROM chunks WHERE id IN ({ph})", chunk_ids
    )

    boosted = dict(scores)
    for row in uri_rows:
        chunk_id = row["id"]
        source_uri = row["source_uri"]
        # Extract parent folder from source_uri
        parent = source_uri.rsplit("/", 1)[0] if "/" in source_uri else ""
        if parent in boosted_folders and chunk_id in boosted:
            boosted[chunk_id] *= boost_factor

    return boosted
```

- [ ] **Step 4: Integrate `_folder_boost` into `search()`**

In the `search()` function, after the merge block (after line 127 `return []`) and before "Fetch chunk details" (line 129), modify to apply folder boost:

Replace this section (lines 129-152):

```python
    # Fetch chunk details
    chunk_ids = [cid for cid, _ in merged[:top_k]]
    if not chunk_ids:
        return []

    placeholders = ",".join("?" * len(chunk_ids))
    type_filter = ""
    params: list = list(chunk_ids)
    if source_type:
        type_filter = " AND source_type = ?"
        params.append(source_type)

    rows = conn.execute(
        f"""
        SELECT id, content, source_type, source_uri, chunk_index
        FROM chunks
        WHERE id IN ({placeholders}){type_filter}
        """,
        params,
    ).fetchall()

    # Build lookup
    chunk_map = {row["id"]: row for row in rows}
    score_map = dict(merged[:top_k])
```

With:

```python
    # --- Folder boost (#126) ---
    # Over-fetch slightly so folder boost can re-rank within a larger window
    boost_window = min(len(merged), top_k * 2)
    pre_boost_ids = [cid for cid, _ in merged[:boost_window]]
    score_map = dict(merged[:boost_window])

    if mode in ("hybrid", "vec") and query_embedding is not None:
        score_map = _folder_boost(conn, query_embedding, pre_boost_ids, score_map)

    # Re-sort after boost and take top_k
    ranked = sorted(score_map.items(), key=lambda x: x[1], reverse=True)
    chunk_ids = [cid for cid, _ in ranked[:top_k]]
    if not chunk_ids:
        return []

    # Fetch chunk details
    placeholders = ",".join("?" * len(chunk_ids))
    type_filter = ""
    params: list = list(chunk_ids)
    if source_type:
        type_filter = " AND source_type = ?"
        params.append(source_type)

    rows = conn.execute(
        f"""
        SELECT id, content, source_type, source_uri, chunk_index
        FROM chunks
        WHERE id IN ({placeholders}){type_filter}
        """,
        params,
    ).fetchall()

    # Build lookup
    chunk_map = {row["id"]: row for row in rows}
    score_map = dict(ranked[:top_k])
```

Also: need to hoist `query_embedding` so it's available after the merge block. Currently `query_embedding` is only computed inside the `if mode in ("hybrid", "vec")` block. Refactor:

At the top of `search()`, after `vec_results: list[...]` initialization, add:

```python
    query_embedding: list[float] | None = None
```

Then change the existing vec block from:

```python
    if mode in ("hybrid", "vec"):
        cfg = get_embed_config(conn)
        query_embedding = embed_single(query, model=cfg["model"])
        vec_results = _vec_search(conn, query_embedding, fetch_limit)
```

(no change needed — `query_embedding` is already assigned here, we just need the type annotation above to satisfy the reference after the merge block)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings && uv run pytest tests/test_folder_summaries.py tests/test_search.py -v`
Expected: PASS

- [ ] **Step 6: Run full test suite**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings && uv run pytest tests/ -q`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings
git add src/knowledge_base/search.py tests/test_folder_summaries.py tests/test_search.py
git commit -m "feat(search): apply folder-level semantic boost to RRF scores

After RRF merge, compare query embedding against folder summary
embeddings. Chunks from high-similarity folders get a 1.15x score
multiplier. Uses a 2x top_k window so boost can promote results
that would otherwise be cut off."
```

---

### Task 5: Migration — Existing Databases

**Files:**

- Modify: `src/knowledge_base/db.py` (new migration function)
- Modify: `tests/test_folder_summaries.py`

Existing databases created before this feature won't have the `folder_summaries` tables. The `CREATE TABLE IF NOT EXISTS` in `init_schema` handles this for the regular table, but the vec0 virtual table needs special handling since `CREATE VIRTUAL TABLE IF NOT EXISTS` may not work for vec0 across all versions.

- [ ] **Step 1: Write failing test for migration idempotency**

Append to `tests/test_folder_summaries.py`:

```python
def test_init_schema_idempotent_for_folder_summaries(tmp_path):
    """Calling init_schema twice doesn't error on folder_summaries."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    init_schema(conn)  # second call should not raise

    row = conn.execute(
        "SELECT count(*) FROM folder_summaries"
    ).fetchone()
    assert row[0] == 0
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings && uv run pytest tests/test_folder_summaries.py::test_init_schema_idempotent_for_folder_summaries -v`
Expected: PASS (IF NOT EXISTS handles this). If it fails, we need to add a migration guard.

- [ ] **Step 3: Commit (if test passes) or fix migration (if test fails)**

```bash
cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings
git add tests/test_folder_summaries.py
git commit -m "test: verify init_schema idempotency for folder_summaries"
```

---

### Task 6: Server — Expose `folder_summaries_status` MCP Tool

**Files:**

- Modify: `src/knowledge_base/server.py`
- Modify: `tests/test_folder_summaries.py`

- [ ] **Step 1: Write failing test for status query**

Append to `tests/test_folder_summaries.py`:

```python
from knowledge_base.folder_summaries import get_folder_summaries_status


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_get_folder_summaries_status(tmp_path):
    """Status reports folder count and staleness."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    status = get_folder_summaries_status(conn)
    assert status["total_folders"] == 0
    assert status["stale_folders"] == 0

    folder = tmp_path / "papers"
    folder.mkdir()
    (folder / "a.md").write_text("Attention paper.\n")
    ingest_file(conn, folder / "a.md")

    status = get_folder_summaries_status(conn)
    assert status["total_folders"] == 1
    assert status["stale_folders"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings && uv run pytest tests/test_folder_summaries.py::test_get_folder_summaries_status -v`
Expected: FAIL — `get_folder_summaries_status` does not exist

- [ ] **Step 3: Implement `get_folder_summaries_status` in `folder_summaries.py`**

Append to `src/knowledge_base/folder_summaries.py`:

```python
def get_folder_summaries_status(conn: sqlite3.Connection) -> dict:
    """Return statistics about folder summaries."""
    total = conn.execute("SELECT count(*) FROM folder_summaries").fetchone()[0]

    # Count stale: folders where stored hash != computed hash
    stale = 0
    rows = conn.execute(
        "SELECT folder_path, content_hash FROM folder_summaries"
    ).fetchall()
    for row in rows:
        current = compute_folder_hash(conn, row["folder_path"])
        if current != row["content_hash"]:
            stale += 1

    return {"total_folders": total, "stale_folders": stale}
```

- [ ] **Step 4: Add MCP tool in `server.py`**

In `src/knowledge_base/server.py`, add import:

```python
from .folder_summaries import get_folder_summaries_status as _folder_status
```

Add tool function (near the other status/config tools):

```python
@mcp.tool()
def folder_summaries_status() -> str:
    """Show folder-level semantic summary statistics.

    Reports total indexed folders and how many have stale summaries.
    """
    conn = _get_conn()
    return json.dumps(_folder_status(conn))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings && uv run pytest tests/test_folder_summaries.py::test_get_folder_summaries_status -v`
Expected: PASS

- [ ] **Step 6: Run full test suite**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings && uv run pytest tests/ -q`
Expected: All tests pass

- [ ] **Step 7: Lint + format**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: No errors

- [ ] **Step 8: Commit**

```bash
cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings
git add src/knowledge_base/folder_summaries.py src/knowledge_base/server.py tests/test_folder_summaries.py
git commit -m "feat(server): expose folder_summaries_status MCP tool

Reports total indexed folders and stale count. Useful for
monitoring folder summary health after batch ingestion."
```

---

### Task 7: Edge Cases and Final Integration Tests

**Files:**

- Modify: `tests/test_folder_summaries.py`

- [ ] **Step 1: Write edge case tests**

Append to `tests/test_folder_summaries.py`:

```python
@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_folder_summary_empty_folder(tmp_path):
    """Folder with no ingested documents produces no summary."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    empty_folder = tmp_path / "empty"
    empty_folder.mkdir()

    result = update_folder_summary(conn, str(empty_folder))
    assert result is False

    row = conn.execute(
        "SELECT * FROM folder_summaries WHERE folder_path = ?",
        (str(empty_folder),),
    ).fetchone()
    assert row is None


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
def test_folder_summary_ignores_subfolders(tmp_path):
    """Folder summary only includes direct children, not nested subfolders."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    parent = tmp_path / "research"
    parent.mkdir()
    child = parent / "subdir"
    child.mkdir()

    (parent / "top.md").write_text("Top-level document.\n")
    (child / "nested.md").write_text("Nested document.\n")
    ingest_file(conn, parent / "top.md")
    ingest_file(conn, child / "nested.md")

    row = conn.execute(
        "SELECT summary FROM folder_summaries WHERE folder_path = ?",
        (str(parent),),
    ).fetchone()
    assert row is not None
    assert "top.md" in row["summary"]
    assert "nested" not in row["summary"].lower()


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.search.embed_single", lambda text, model="bge-m3": [0.1] * DEFAULT_EMBED_DIM)
def test_search_without_folder_summaries_still_works(tmp_path):
    """Search works normally when no folder summaries exist."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Ingest directly without triggering folder summary
    # (simulate pre-existing DB with no folder summaries)
    md = tmp_path / "paper.md"
    md.write_text("Attention mechanisms in neural networks.\n")

    # Delete folder summaries after ingest
    ingest_file(conn, md)
    conn.execute("DELETE FROM folder_summaries")
    conn.execute("DELETE FROM folder_summaries_vec")
    conn.commit()

    results = search(conn, "attention", mode="hybrid")
    assert len(results) >= 1
```

- [ ] **Step 2: Run all new tests**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings && uv run pytest tests/test_folder_summaries.py -v`
Expected: All pass

- [ ] **Step 3: Run full test suite + lint**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings && uv run pytest tests/ -q && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: All pass, no lint errors

- [ ] **Step 4: Commit**

```bash
cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings
git add tests/test_folder_summaries.py
git commit -m "test: edge cases for folder summaries

Tests empty folders, subfolder isolation, and graceful
degradation when no folder summaries exist."
```

---

### Task 8: Embed Swap — Re-embed Folder Summaries on Model Change

**Files:**

- Modify: `src/knowledge_base/embed_swap.py`
- Modify: `tests/test_folder_summaries.py`

When the user swaps embedding models via `re_embed()`, `folder_summaries_vec` is left with stale embeddings of the old dimension. We must drop and recreate it, then re-embed all folder summaries.

- [ ] **Step 1: Write failing test**

Append to `tests/test_folder_summaries.py`:

```python
from knowledge_base.embed_swap import re_embed


@patch("knowledge_base.ingest.embed", _fake_embed)
@patch("knowledge_base.folder_summaries.embed", _fake_embed)
@patch("knowledge_base.embed_swap.embed", _fake_embed)
def test_re_embed_updates_folder_summaries_vec(tmp_path):
    """re_embed() recreates folder_summaries_vec with new dimensions."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    folder = tmp_path / "papers"
    folder.mkdir()
    (folder / "a.md").write_text("Paper about attention.\n")
    ingest_file(conn, folder / "a.md")

    # Verify folder summary vec exists
    assert conn.execute("SELECT count(*) FROM folder_summaries_vec").fetchone()[0] == 1

    # Re-embed with same model (just testing the plumbing)
    re_embed(conn, "bge-m3", DEFAULT_EMBED_DIM)

    # folder_summaries_vec should still have an entry (recreated)
    assert conn.execute("SELECT count(*) FROM folder_summaries_vec").fetchone()[0] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings && uv run pytest tests/test_folder_summaries.py::test_re_embed_updates_folder_summaries_vec -v`
Expected: FAIL — `re_embed` drops `chunks_vec` and recreates it, but doesn't touch `folder_summaries_vec`, which may error or have stale data after dimension change.

- [ ] **Step 3: Update `re_embed()` in `embed_swap.py`**

In the atomic swap phase of `re_embed()` (the section that drops and recreates `chunks_vec`), add handling for `folder_summaries_vec`:

After the existing `chunks_vec` swap logic, add:

```python
    # --- Re-embed folder summaries (#126) ---
    folder_rows = conn.execute(
        "SELECT folder_path, summary FROM folder_summaries"
    ).fetchall()
    if folder_rows:
        conn.execute("DROP TABLE IF EXISTS folder_summaries_vec")
        conn.execute(
            f"CREATE VIRTUAL TABLE folder_summaries_vec USING vec0("
            f"embedding float[{new_dim}], +folder_path TEXT)"
        )
        folder_texts = [row["summary"] for row in folder_rows]
        folder_embeddings = embed(folder_texts, model=new_model, expected_dim=new_dim)
        for row, emb in zip(folder_rows, folder_embeddings):
            conn.execute(
                "INSERT INTO folder_summaries_vec (embedding, folder_path) VALUES (?, ?)",
                (_serialize_f32(emb), row["folder_path"]),
            )
    else:
        # No folder summaries yet — just recreate with new dim
        conn.execute("DROP TABLE IF EXISTS folder_summaries_vec")
        conn.execute(
            f"CREATE VIRTUAL TABLE folder_summaries_vec USING vec0("
            f"embedding float[{new_dim}], +folder_path TEXT)"
        )
```

Add the necessary import at the top of `embed_swap.py`:

```python
import struct

def _serialize_f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings && uv run pytest tests/test_folder_summaries.py::test_re_embed_updates_folder_summaries_vec -v`
Expected: PASS

- [ ] **Step 5: Run full test suite + lint**

Run: `cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings && uv run pytest tests/ -q && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
cd /home/mroynard/dev/knowledge-base/.worktrees/feat-126-folder-embeddings
git add src/knowledge_base/embed_swap.py tests/test_folder_summaries.py
git commit -m "fix(embed_swap): re-embed folder summaries on model change

When re_embed() swaps the embedding model, also drop and recreate
folder_summaries_vec with the new dimensions and re-embed all
folder summaries. Prevents stale/wrong-dimension vectors."
```

---

## Design Decisions

1. **No LLM summarization (for now):** The issue suggests LLM-generated summaries as preferred and concatenation as cheaper fallback. We implement the cheap fallback (filename + first chunk snippet) because it requires no additional LLM calls and is deterministic. LLM summarization can be added later as an enhancement without changing the schema or search integration.

2. **Direct children only:** Folder summaries only include documents directly in the folder, not nested subfolders. Each subfolder gets its own summary. This matches how researchers typically organize papers by topic in flat folder structures.

3. **Multiplicative boost, not additive:** The folder boost multiplies existing RRF scores rather than adding a flat bonus. This preserves the relative ordering from RRF while giving a gentle lift to contextually relevant results. The default 1.15x was chosen to be noticeable but not dominant.

4. **Graceful degradation:** If `folder_summaries_vec` is empty (new DB, no folders indexed yet), the boost is a no-op. Search works exactly as before. Folder summary computation failures never break ingestion.

5. **`source_uri` LIKE prefix matching:** We use SQL LIKE with a trailing `/` to match direct children only. This is O(n) but `folder_summaries` is tiny (one row per folder), so performance is not a concern.
