# Quickstart

Three workflows to get started: ingest a document, search the index, and extract
structured data.

## 1. Ingest a paper

Use the `ingest` tool with an absolute path to a PDF, markdown file, code file,
or directory:

```
ingest(path="/home/user/papers/attention-is-all-you-need.pdf")
```

Expected response:

```json
{
  "chunks_added": 42,
  "chunks_skipped": 0,
  "source_type": "pdf",
  "source_uri": "/home/user/papers/attention-is-all-you-need.pdf"
}
```

The file is split into ~1000-character chunks with 200-character overlap. Each chunk
gets a content hash (for deduplication), an embedding vector, and FTS5 indexing.

For directories, the tool recursively ingests all supported files and returns per-file
results:

```
ingest(path="/home/user/papers/")
```

You can override the auto-detected source type:

```
ingest(path="/home/user/notes/ideas.txt", source_type="note")
```

Valid source types: `pdf`, `markdown`, `code`, `web`, `note`, `figure`.

### Ingest a web page

Use `ingest_url` for web content:

```
ingest_url(url="https://arxiv.org/abs/2301.00001")
```

## 2. Search the index

Use `search_index` with a natural-language query:

```
search_index(query="self-attention mechanism for sequence modeling")
```

Response:

```json
[
  {
    "chunk_id": 17,
    "content": "The Transformer uses multi-head self-attention...",
    "source_type": "pdf",
    "source_uri": "/home/user/papers/attention-is-all-you-need.pdf",
    "chunk_index": 3,
    "score": 0.032787,
    "match_type": "hybrid"
  }
]
```

Result fields:

| Field         | Description                                          |
| ------------- | ---------------------------------------------------- |
| `chunk_id`    | Unique chunk identifier                              |
| `content`     | The chunk text                                       |
| `source_type` | Origin type (pdf, markdown, code, web, note, figure) |
| `source_uri`  | File path or URL                                     |
| `chunk_index` | Position within the source document                  |
| `score`       | Relevance score (higher is better)                   |
| `match_type`  | How the result was found: `hybrid`, `fts`, or `vec`  |

### Filter by source type

```
search_index(query="transformer architecture", source_type="pdf")
```

### Switch search modes

```
# Keyword-only (BM25 via FTS5)
search_index(query="attention mechanism", mode="fts")

# Semantic-only (cosine similarity via sqlite-vec)
search_index(query="attention mechanism", mode="vec")

# Default: hybrid (BM25 + cosine, merged via Reciprocal Rank Fusion)
search_index(query="attention mechanism", mode="hybrid")
```

### Adjust result count

```
search_index(query="attention mechanism", top_k=20)
```

Default is 10 results.

## 3. Extract structure

Structured extraction pulls methods, datasets, and metrics from a paper using an
LLM. First, register the paper with metadata:

```
register_paper_tool(
    title="Attention Is All You Need",
    authors=["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"],
    year=2017,
    venue="NeurIPS",
    source_uri="/home/user/papers/attention-is-all-you-need.pdf"
)
```

The `source_uri` links the paper to its already-ingested chunks.

Response:

```json
{
  "paper_id": 1,
  "title": "Attention Is All You Need",
  "chunks_linked": 42
}
```

Then run extraction:

```
extract_structure_tool(paper_id=1)
```

For short documents (< 8000 characters total), this runs inline. For longer
documents, it returns an ETA warning -- call again with `confirmed=True` to queue
a background job:

```
extract_structure_tool(paper_id=1, confirmed=True)
```

Poll the job:

```
get_job_status_tool(job_id=1)
```

Extraction result:

```json
{
  "paper_id": 1,
  "methods_added": 3,
  "datasets_added": 2,
  "metrics_added": 5,
  "entities_resolved": 5
}
```

Query extracted data individually:

```
record_method_tool(name="Transformer", paper_id=1, description="...")
record_dataset_tool(name="WMT 2014 EN-DE", paper_id=1)
record_metric_tool(name="BLEU", value=28.4, paper_id=1, unit="%")
```

Compare papers side-by-side on shared datasets:

```
compare_papers_tool(paper_ids=[1, 2, 3])
```
