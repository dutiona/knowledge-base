# BibTeX Export

## Export to String

Export all papers (or a filtered subset) as a BibTeX string:

```json
{ "name": "export_bibtex_tool" }
```

Returns:

```json
{
  "bibtex": "@article{vaswani2017,\n  title = {Attention Is All You Need},\n  ...\n}\n\n@article{...",
  "entries": 5
}
```

If a paper has stored raw BibTeX (provided at registration), that is emitted as-is. Otherwise, an `@article` entry is generated from the paper's metadata.

## Export to File

Write BibTeX to a file by providing `output_path`:

```json
{
  "name": "export_bibtex_tool",
  "arguments": {
    "output_path": "~/bibliography/refs.bib"
  }
}
```

Returns:

```json
{ "written_to": "/home/user/bibliography/refs.bib", "entries": 5 }
```

### Path Validation

The output path must satisfy two constraints:

1. **Extension** -- must be `.bib` or `.bibtex`
2. **Location** -- must resolve to a path under the user's home directory or current working directory

Paths outside these locations are rejected.

## Sync Mode

Append only new papers to an existing `.bib` file, skipping duplicates:

```json
{
  "name": "sync_bibtex_tool",
  "arguments": {
    "output_path": "~/papers/refs.bib"
  }
}
```

Returns:

```json
{ "appended": 3, "skipped": 2, "path": "/home/user/papers/refs.bib" }
```

Sync reads the existing file and extracts all BibTeX keys (e.g., `vaswani2017`). Papers whose key already appears in the file are skipped. For generated entries (no stored raw BibTeX), a `% knowledge-base-id: <id>` comment is inserted above the entry; sync checks for this marker to avoid duplicating generated entries.

The file is created if it does not exist.

## Filtering

Both export and sync support filtering by paper IDs or title pattern:

```json
{
  "name": "export_bibtex_tool",
  "arguments": { "paper_ids": [1, 3, 7] }
}
```

```json
{
  "name": "sync_bibtex_tool",
  "arguments": {
    "output_path": "~/refs.bib",
    "title_pattern": "transformer"
  }
}
```

`title_pattern` performs a case-insensitive substring match (`LIKE %pattern%`).

When no filters are provided, all registered papers are included.

## Citation Keys

Generated BibTeX keys follow the pattern `<first_author_surname><year>`:

- `vaswani2017` for Vaswani et al., 2017
- `unknown_nd` if no authors or year are set

Collision avoidance appends lowercase suffixes: `vaswani2017a`, `vaswani2017b`, etc. The key generator considers both existing file keys (for sync) and stored BibTeX keys (for export) to avoid collisions across the full output.

Author surname extraction handles both "Last, First" and "First Last" formats.

## Typst Integration

Export a `.bib` file and reference it in your Typst document:

```typst
#bibliography("refs.bib")

As shown by @vaswani2017, the Transformer architecture...
```

Use `sync_bibtex_tool` to incrementally update the `.bib` file as new papers are registered, without overwriting existing entries or manual edits.
