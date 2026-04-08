# Plan: Compressed Vector Indices Foundation (#94)

## Context

sqlite-vec supports `int8` and `bit` element types in vec0 virtual tables, but the codebase hardcodes `float32` everywhere. The multi-space architecture (#99) already supports parallel spaces with independent vec0 tables — we extend it with an `element_type` dimension so spaces can coexist with different quantization, enabling A/B comparison.

Follow-ups: #344 (TurboQuant), #345 (binary+rescoring).

## Review findings addressed (Round 1 — Codex)

### [BLOCKER] float16 not a real storage type in sqlite-vec v0.1.6
**Resolution:** Drop float16 from element_type enum. Only `float32` and `int8` are supported. float16 can be added later when sqlite-vec supports it.

### [BLOCKER] Asymmetric search not supported — int8 table rejects float32 query
**Resolution:** `_vec_search` MUST be updated. For int8 spaces, queries must be quantized and wrapped with `vec_int8(?)` in the SQL. Use sqlite-vec's `vec_quantize_int8(vec_f32(?), 'unit')` for query-side quantization, or quantize in Python and use `vec_int8(?)` SQL wrapper.

### [BLOCKER] Raw int8 bytes rejected — need `vec_int8()` SQL constructor
**Resolution:** Insertion SQL must use typed constructors. For int8: `INSERT INTO [tbl] (rowid, embedding, chunk_id) VALUES (?, vec_int8(?), ?)`. The serialization layer produces bytes, but the SQL template varies by element type. Add `ELEMENT_SQL_TEMPLATES` dict alongside serializers.

### [HIGH] CHECK constraint not retroactively enforced on migrated tables
**Resolution:** Add code-side validation in `create_space()`. The CHECK constraint on fresh DBs is defense-in-depth; the real enforcement is in Python.

### [MEDIUM] Function is `init_schema()` not `init_db()`
**Resolution:** Fixed in plan references.

### [MEDIUM] `batch_compare_spaces` is partial reuse
**Resolution:** Clarified — the benchmark tool wraps `batch_compare_spaces` with query sampling, multi-space iteration, and storage estimates.

### [LOW] int8 quantization contract differs from sqlite-vec's `vec_quantize_int8`
**Resolution:** Use sqlite-vec's own `vec_quantize_int8(vec_f32(?), 'unit')` at both insert and query time instead of a custom Python quantizer. This ensures consistency and removes a maintenance burden. No custom `serialize_int8` needed — we pass float32 bytes and let SQL handle quantization.

## Revised Design

### Key insight: let sqlite-vec handle quantization

Instead of custom Python serializers, use sqlite-vec SQL functions:
- **Insert:** `INSERT INTO [tbl] (rowid, embedding, chunk_id) VALUES (?, vec_quantize_int8(vec_f32(?), 'unit'), ?)`
- **Query:** `WHERE embedding MATCH vec_quantize_int8(vec_f32(?), 'unit')`

This means:
1. Python always produces float32 bytes (`serialize_f32`)
2. SQL templates handle type conversion
3. No custom quantization code to maintain
4. Quantization is consistent between insert and query (same sqlite-vec function)

## Step 1: SQL templates (`src/knowledge_base/utils.py`)

Add alongside existing `serialize_f32` (line 88):

```python
# SQL expression templates for typed vector insertion and querying.
# Each takes a float32 blob parameter (?) and wraps it for the target type.
ELEMENT_INSERT_EXPR: dict[str, str] = {
    "float32": "?",                                    # raw float32 blob
    "int8": "vec_quantize_int8(vec_f32(?), 'unit')",   # sqlite-vec quantizes
}

ELEMENT_QUERY_EXPR: dict[str, str] = {
    "float32": "?",                                    # raw float32 blob
    "int8": "vec_quantize_int8(vec_f32(?), 'unit')",   # sqlite-vec quantizes
}

VALID_ELEMENT_TYPES: frozenset[str] = frozenset(ELEMENT_INSERT_EXPR.keys())
```

## Step 2: Schema migration (`src/knowledge_base/db.py`)

### 2a: Add `element_type` to `embed_spaces` CREATE TABLE (line 786)

Add after `matryoshka_base_dim`:
```sql
element_type TEXT NOT NULL DEFAULT 'float32'
    CHECK(element_type IN ('float32', 'int8'))
```

### 2b: Migration function `_migrate_embed_spaces_element_type`

```python
def _migrate_embed_spaces_element_type(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(embed_spaces)")}
    if "element_type" not in cols:
        conn.execute(
            "ALTER TABLE embed_spaces ADD COLUMN element_type TEXT NOT NULL DEFAULT 'float32'"
        )
        conn.commit()
```

Register in `init_schema()` after `_migrate_embed_spaces_matryoshka` (line 851).

### 2c: Update `insert_chunk_vec` (line 403)

Accept `element_type` parameter (default `"float32"`). Use `ELEMENT_INSERT_EXPR[element_type]` in the SQL:

```python
def insert_chunk_vec(conn, chunk_id, embedding, table_name=None, element_type="float32"):
    tbl = table_name or get_vec_table_name(conn)
    expr = ELEMENT_INSERT_EXPR[element_type]
    conn.execute(
        f"INSERT INTO [{tbl}] (rowid, embedding, chunk_id) VALUES (?, {expr}, ?)",
        (chunk_id, _serialize_f32(embedding), chunk_id),
    )
    ...
```

### 2d: Add `get_space_element_type` helper

```python
def get_space_element_type(conn, table_name=None):
    """Return the element_type for the given (or active) space, default 'float32'."""
    tbl = table_name or get_vec_table_name(conn)
    row = conn.execute(
        "SELECT element_type FROM embed_spaces WHERE table_name = ?", (tbl,)
    ).fetchone()
    return row["element_type"] if row else "float32"
```

## Step 3: Space lifecycle (`src/knowledge_base/embed_swap.py`)

### 3a: `create_space` (line 55)

- Add `element_type: str = "float32"` parameter
- Validate: `if element_type not in VALID_ELEMENT_TYPES: raise ValueError(...)`
- Store in `embed_spaces` INSERT
- Vec0 table creation: `embedding {element_type}[{dim}]`

### 3b: `backfill_space` (line 122)

- Read `element_type` from space row
- Use `ELEMENT_INSERT_EXPR[element_type]` in the INSERT SQL at line 192
- Always pass `_serialize_f32(emb_vec)` as the parameter (SQL handles quantization)

### 3c: `promote_space` (line 211)

- Sync `element_type` to config table

### 3d: `list_spaces`

- Include `element_type` in returned dicts

## Step 4: Search (`src/knowledge_base/search.py`)

### 4a: `_vec_search` (line 95)

Must be updated for int8 spaces. The query blob needs wrapping:

```python
def _vec_search(conn, query_embedding, limit, table_name=None):
    vec_table = table_name or get_vec_table_name(conn)
    element_type = get_space_element_type(conn, vec_table)
    query_expr = ELEMENT_QUERY_EXPR[element_type]
    rows = conn.execute(
        f"SELECT chunk_id, distance FROM [{vec_table}]"
        f" WHERE embedding MATCH {query_expr} ORDER BY distance LIMIT ?",
        (_serialize_f32(query_embedding), limit),
    ).fetchall()
    return [(row["chunk_id"], row["distance"]) for row in rows]
```

### 4b: Folder summaries search (`src/knowledge_base/search.py` line ~156)

Also uses `_serialize_f32` for folder_summaries_vec — this stays float32 (folder summaries are always in the default space). No change.

### 4c: Folder summaries module (`src/knowledge_base/folder_summaries.py` line 153)

Same — stays float32. No change needed.

## Step 5: Route / MCP tool (`src/knowledge_base/routes/embeddings.py`)

### 5a: Update `create_embed_space_tool` (line 78)

Add `element_type: str = "float32"` parameter, pass through to `create_space`.

### 5b: New `benchmark_spaces_tool`

```python
@mcp.tool()
def benchmark_spaces_tool(
    baseline_space: str | None = None,
    sample_queries: int = 50,
    top_k: int = 10,
) -> str:
    """Benchmark all non-deprecated spaces against a baseline.

    Samples random chunk content as queries, runs batch_compare_spaces
    for each space vs baseline, reports aggregated metrics + storage estimates.
    """
```

Implementation:
1. Identify baseline (default: active space, or user-specified)
2. Sample `sample_queries` chunk texts from DB (`ORDER BY RANDOM() LIMIT ?`)
3. For each non-deprecated, non-baseline space: call `batch_compare_spaces`
4. Return: overlap@k, Jaccard, rank correlation per space + storage estimate

Reuses `batch_compare_spaces` from `comparison.py` for the per-space inner loop. Adds query sampling + multi-space iteration + storage estimates as new logic.

## Step 6: Tests (`tests/test_embed_spaces.py` + `tests/test_utils.py`)

### 6a: Unit tests (`tests/test_utils.py`)

- `test_element_type_constants`: verify VALID_ELEMENT_TYPES, INSERT_EXPR, QUERY_EXPR consistency

### 6b: Space lifecycle tests (`tests/test_embed_spaces.py`)

- `test_create_space_with_int8_element_type`: create int8 space, verify vec0 table uses int8
- `test_create_space_default_element_type`: verify backward compat (default=float32)
- `test_create_space_invalid_element_type`: verify ValueError on unsupported type
- `test_backfill_int8_space`: create int8 space, backfill, verify chunks inserted
- `test_search_int8_space`: create + backfill int8 space, verify search returns results
- `test_promote_int8_space`: promote, verify config synced with element_type
- `test_migration_adds_element_type`: verify migration on legacy DB (column added, default float32)
- `test_benchmark_spaces`: create float32 + int8 spaces, run benchmark, verify output structure

### 6c: Integration

- `test_int8_search_returns_same_top_results`: insert same embeddings in float32 and int8 spaces, verify high overlap@k (quality sanity check)

## File Change Summary

| File | Change |
|------|--------|
| `src/knowledge_base/utils.py` | Add `ELEMENT_INSERT_EXPR`, `ELEMENT_QUERY_EXPR`, `VALID_ELEMENT_TYPES` |
| `src/knowledge_base/db.py` | Schema + migration + `get_space_element_type` + update `insert_chunk_vec` |
| `src/knowledge_base/embed_swap.py` | `create_space` + `backfill_space` + `promote_space` + `list_spaces` |
| `src/knowledge_base/search.py` | Update `_vec_search` for element-type-aware query wrapping |
| `src/knowledge_base/routes/embeddings.py` | `create_embed_space_tool` + `benchmark_spaces_tool` |
| `tests/test_utils.py` | SQL template tests |
| `tests/test_embed_spaces.py` | Lifecycle + search + benchmark tests |

## Execution Order

1. Step 1 (SQL templates in utils.py) — no dependencies
2. Step 2 (schema + migration + insert_chunk_vec) — depends on Step 1
3. Step 3 (embed_swap) — depends on Step 2
4. Step 4 (search) — depends on Step 2
5. Step 5 (route/MCP) — depends on Steps 3+4
6. Step 6 (tests) — TDD alongside each step

## Verification

```bash
uv run pytest tests/test_utils.py tests/test_embed_spaces.py -v
uv run pytest tests/ -q          # full suite regression
ruff check src/ tests/
ruff format --check src/ tests/
```

## Operational steps

1. Implementation in worktree `.worktrees/feat-94-compressed-vectors` (branch: `feat/94-compressed-vector-indices`) — already created
2. Post plan summary as GitHub issue comment on #94
3. Commit + push + open PR when implementation complete
4. Invoke `/super-review` for multi-model review
5. `/finish-pr` for pre-merge validation
