"""Wrapper-level tests for the papers route MCP tools.

These exercise the *server-only* logic in ``knowledge_base.routes.papers``:
error mapping (``KnowledgeBaseError`` -> ``{"error": str(e), **e.details}``),
orchestration (auto_relate job submission), and JSON shaping. Domain
internals live in ``papers.py`` / ``conclusions.py`` / ``bibtex.py`` and are
tested via real ``kb_conn`` round-trips rather than re-asserted here.

Already covered elsewhere (NOT duplicated):
    - ``scan_relationships``                     -> tests/test_auto_relate.py
    - ``_validate_bib_path`` + the validated-write
      branches of export/sync                    -> tests/test_validate_bib_path.py
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from knowledge_base.routes import papers as papers_routes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_conn(kb_conn):
    """Patch the route module's ``_get_conn`` to return the test connection."""
    return patch.object(papers_routes, "_get_conn", return_value=kb_conn)


def _register(kb_conn, title: str, **kwargs) -> int:
    """Register a paper through the wrapper, return its paper_id.

    Defaults ``skip_auto_relate=True``: this helper only sets up papers (often
    with a ``source_uri`` so a ``paper_paths`` row exists) for OTHER wrappers'
    tests. Without it, ``register_paper_tool(source_uri=...)`` would submit a
    real ``auto_relate`` job and start the singleton background worker mid-test
    (a non-hermetic worker that could reach embeddings). The auto_relate
    orchestration itself is covered directly in the register_paper_tool tests
    above, which call the wrapper without this helper. Callers may override.
    """
    kwargs.setdefault("skip_auto_relate", True)
    with _patch_conn(kb_conn):
        result = json.loads(papers_routes.register_paper_tool(title, **kwargs))
    return result["paper_id"]


# ---------------------------------------------------------------------------
# 1. register_paper_tool — success shape + auto_relate orchestration
# ---------------------------------------------------------------------------


def test_register_paper_tool_no_source_uri_queues_no_job(kb_conn):
    """Without source_uri the wrapper must not submit an auto_relate job."""
    with _patch_conn(kb_conn):
        result = json.loads(papers_routes.register_paper_tool("Attention Is All"))

    assert result["paper_id"] >= 1
    assert "abstract_chunk_id" in result
    # No source_uri -> the orchestration branch is skipped -> jobs table empty.
    jobs = kb_conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()
    assert jobs["n"] == 0


def test_register_paper_tool_source_uri_queues_auto_relate(kb_conn):
    """source_uri + skip_auto_relate=False must submit one auto_relate job.

    submit_job is lazily imported inside the wrapper via
    ``from ..jobs import submit_job`` -> patch it at its definition module so
    no background worker / embedding network call is triggered.
    """
    with _patch_conn(kb_conn), patch("knowledge_base.jobs.submit_job") as mock_submit:
        result = json.loads(
            papers_routes.register_paper_tool(
                "Linked Paper", source_uri="/tmp/linked.pdf"
            )
        )

    paper_id = result["paper_id"]
    mock_submit.assert_called_once_with(
        kb_conn, paper_id, "auto_relate", {"paper_id": paper_id}
    )


def test_register_paper_tool_skip_auto_relate_queues_no_job(kb_conn):
    """source_uri but skip_auto_relate=True must not submit a job."""
    with _patch_conn(kb_conn), patch("knowledge_base.jobs.submit_job") as mock_submit:
        papers_routes.register_paper_tool(
            "Bulk Import", source_uri="/tmp/bulk.pdf", skip_auto_relate=True
        )

    mock_submit.assert_not_called()


# ---------------------------------------------------------------------------
# 2. get_paper_tool — JSON shape via real round-trip
# ---------------------------------------------------------------------------


def test_get_paper_tool_by_id(kb_conn):
    paper_id = _register(kb_conn, "Deep Residual Learning", authors=["He"], year=2015)

    with _patch_conn(kb_conn):
        papers = json.loads(papers_routes.get_paper_tool(paper_id=paper_id))

    assert isinstance(papers, list)
    assert len(papers) == 1
    paper = papers[0]
    assert paper["id"] == paper_id
    assert paper["title"] == "Deep Residual Learning"
    assert paper["authors"] == ["He"]
    assert paper["year"] == 2015
    assert paper["chunks"] == []
    assert paper["relationships"] == []


def test_get_paper_tool_by_title_pattern(kb_conn):
    _register(kb_conn, "Generative Adversarial Networks", year=2014)

    with _patch_conn(kb_conn):
        papers = json.loads(papers_routes.get_paper_tool(title_pattern="Adversarial"))

    assert len(papers) == 1
    assert papers[0]["title"] == "Generative Adversarial Networks"


# ---------------------------------------------------------------------------
# 3. add_relationship_tool — success + error mapping
# ---------------------------------------------------------------------------


def test_add_relationship_tool_success(kb_conn):
    src = _register(kb_conn, "Source Paper")
    tgt = _register(kb_conn, "Target Paper")

    with _patch_conn(kb_conn):
        result = json.loads(
            papers_routes.add_relationship_tool(src, tgt, "cites", confidence=0.8)
        )

    assert result == {
        "source_paper_id": src,
        "target_paper_id": tgt,
        "relation_type": "cites",
        "confidence": 0.8,
    }


def test_add_relationship_tool_invalid_type_maps_to_error(kb_conn):
    src = _register(kb_conn, "S")
    tgt = _register(kb_conn, "T")

    with _patch_conn(kb_conn):
        result = json.loads(
            papers_routes.add_relationship_tool(src, tgt, "bogus_relation")
        )

    # ValidationError carries no details -> error dict is just {"error": ...}.
    assert set(result.keys()) == {"error"}
    assert "Invalid relation_type" in result["error"]


# ---------------------------------------------------------------------------
# 4. get_relationships_tool — JSON shape
# ---------------------------------------------------------------------------


def test_get_relationships_tool(kb_conn):
    src = _register(kb_conn, "Citing Paper")
    tgt = _register(kb_conn, "Cited Paper")
    with _patch_conn(kb_conn):
        papers_routes.add_relationship_tool(src, tgt, "cites")
        rels = json.loads(
            papers_routes.get_relationships_tool(src, direction="outgoing")
        )

    assert len(rels) == 1
    rel = rels[0]
    assert rel["source_paper_id"] == src
    assert rel["target_paper_id"] == tgt
    assert rel["source_title"] == "Citing Paper"
    assert rel["target_title"] == "Cited Paper"
    assert rel["relation_type"] == "cites"


# ---------------------------------------------------------------------------
# 5. record_conclusion_tool — success + error branch
# ---------------------------------------------------------------------------


def test_record_conclusion_tool_success(kb_conn):
    with _patch_conn(kb_conn):
        result = json.loads(
            papers_routes.record_conclusion_tool("Transformers scale well", 0.9)
        )

    assert result["conclusion_id"] >= 1


def test_record_conclusion_tool_invalid_confidence_maps_to_error(kb_conn):
    with _patch_conn(kb_conn):
        result = json.loads(
            papers_routes.record_conclusion_tool("Bad confidence", confidence=1.5)
        )

    assert set(result.keys()) == {"error"}
    assert "confidence must be between" in result["error"]


# ---------------------------------------------------------------------------
# 6. get_conclusions_tool — keyword + min_confidence filtering
# ---------------------------------------------------------------------------


def test_get_conclusions_tool_filters(kb_conn):
    with _patch_conn(kb_conn):
        papers_routes.record_conclusion_tool("Alpha claim about cats", 0.9)
        papers_routes.record_conclusion_tool("Beta claim about dogs", 0.3)

        # No filter -> both returned.
        allc = json.loads(papers_routes.get_conclusions_tool())
        assert len(allc) == 2

        # Keyword filter.
        cats = json.loads(papers_routes.get_conclusions_tool(keyword="cats"))
        assert len(cats) == 1
        assert "cats" in cats[0]["claim"]

        # Confidence floor excludes the 0.3 claim.
        high = json.loads(papers_routes.get_conclusions_tool(min_confidence=0.5))
        assert len(high) == 1
        assert high[0]["confidence"] == 0.9


# ---------------------------------------------------------------------------
# 7. supersede_conclusion_tool — success + missing-id error mapping
# ---------------------------------------------------------------------------


def test_supersede_conclusion_tool_success(kb_conn):
    with _patch_conn(kb_conn):
        old = json.loads(papers_routes.record_conclusion_tool("Old claim", 0.7))[
            "conclusion_id"
        ]
        result = json.loads(
            papers_routes.supersede_conclusion_tool(old, "New claim", 0.95)
        )

    assert result["old_conclusion_id"] == old
    assert result["new_conclusion_id"] != old


def test_supersede_conclusion_tool_missing_id_maps_to_error(kb_conn):
    with _patch_conn(kb_conn):
        result = json.loads(
            papers_routes.supersede_conclusion_tool(9999, "Replacement claim")
        )

    assert set(result.keys()) == {"error"}
    assert "9999 not found" in result["error"]


# ---------------------------------------------------------------------------
# 8. get_conclusion_chain_tool — oldest->newest ordering
# ---------------------------------------------------------------------------


def test_get_conclusion_chain_tool_ordering(kb_conn):
    with _patch_conn(kb_conn):
        first = json.loads(papers_routes.record_conclusion_tool("v1", 0.5))[
            "conclusion_id"
        ]
        sup = json.loads(papers_routes.supersede_conclusion_tool(first, "v2", 0.6))
        second = sup["new_conclusion_id"]

        # Query from any node in the chain.
        chain = json.loads(papers_routes.get_conclusion_chain_tool(second))

    assert [c["id"] for c in chain] == [first, second]
    assert [c["claim"] for c in chain] == ["v1", "v2"]
    assert chain[0]["superseded_by"] == second


# ---------------------------------------------------------------------------
# 9. export_bibtex_tool — output_path=None branch + write error branch
# ---------------------------------------------------------------------------


def test_export_bibtex_tool_returns_content_when_no_path(kb_conn):
    _register(kb_conn, "Paper One", authors=["Smith"], year=2020)
    _register(kb_conn, "Paper Two", authors=["Jones"], year=2021)

    with _patch_conn(kb_conn):
        result = json.loads(papers_routes.export_bibtex_tool())

    assert "bibtex" in result
    assert result["entries"] == result["bibtex"].count("@")
    assert result["entries"] == 2
    assert "@article" in result["bibtex"]


def test_export_bibtex_tool_write_error_maps_to_error(kb_conn, tmp_path, monkeypatch):
    """A ValueError/OSError from the write branch maps to {"error": ...}."""
    _register(kb_conn, "Paper One", authors=["Smith"], year=2020)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    with _patch_conn(kb_conn):
        # Bad extension -> _validate_bib_path raises ValueError, caught -> error.
        result = json.loads(
            papers_routes.export_bibtex_tool(output_path=str(tmp_path / "bad.txt"))
        )

    assert set(result.keys()) == {"error"}
    assert "extension" in result["error"]


# ---------------------------------------------------------------------------
# 10. sync_bibtex_tool — bad-extension + OSError error branches
# ---------------------------------------------------------------------------


def test_sync_bibtex_tool_bad_extension_maps_to_error(kb_conn, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    with _patch_conn(kb_conn):
        result = json.loads(
            papers_routes.sync_bibtex_tool(output_path=str(tmp_path / "refs.txt"))
        )

    assert set(result.keys()) == {"error"}
    assert "extension" in result["error"]


def test_sync_bibtex_tool_oserror_maps_to_error(kb_conn, tmp_path, monkeypatch):
    """An OSError from sync_bibtex maps to a {"error": ...} dict (path passes
    validation first)."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    out = str(tmp_path / "refs.bib")

    with (
        _patch_conn(kb_conn),
        patch.object(papers_routes, "sync_bibtex", side_effect=OSError("disk full")),
    ):
        result = json.loads(papers_routes.sync_bibtex_tool(output_path=out))

    assert set(result.keys()) == {"error"}
    assert "Failed to sync" in result["error"]
    assert "disk full" in result["error"]


# ---------------------------------------------------------------------------
# 11. suggest_relationships_tool — pass-through JSON shape
# ---------------------------------------------------------------------------


def test_suggest_relationships_tool_shape(kb_conn):
    paper_id = _register(kb_conn, "Lonely Paper")

    with _patch_conn(kb_conn):
        result = json.loads(papers_routes.suggest_relationships_tool(paper_id))

    # No chunks -> empty suggestions/unmatched, but the keys must be present.
    assert result == {"suggestions": [], "unmatched": []}


# ---------------------------------------------------------------------------
# 12. relocate_paper_tool — success + missing-paper error mapping
# ---------------------------------------------------------------------------


def test_relocate_paper_tool_success(kb_conn, tmp_path):
    old_file = tmp_path / "old.pdf"
    old_file.write_text("content", encoding="utf-8")
    new_file = tmp_path / "new.pdf"
    new_file.write_text("content", encoding="utf-8")

    paper_id = _register(kb_conn, "Movable Paper", source_uri=str(old_file))

    with _patch_conn(kb_conn):
        result = json.loads(papers_routes.relocate_paper_tool(paper_id, str(new_file)))

    assert result["paper_id"] == paper_id
    assert result["new_path"] == Path(new_file).resolve().as_posix()


def test_relocate_paper_tool_missing_paper_maps_to_error(kb_conn, tmp_path):
    target = tmp_path / "somewhere.pdf"
    target.write_text("x", encoding="utf-8")

    with _patch_conn(kb_conn):
        result = json.loads(papers_routes.relocate_paper_tool(4242, str(target)))

    assert set(result.keys()) == {"error"}
    assert "4242" in result["error"]


# ---------------------------------------------------------------------------
# 13. get_paper_paths_tool — path list JSON
# ---------------------------------------------------------------------------


def test_get_paper_paths_tool(kb_conn, tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_text("body", encoding="utf-8")
    paper_id = _register(kb_conn, "Pathful Paper", source_uri=str(src))

    with _patch_conn(kb_conn):
        paths = json.loads(papers_routes.get_paper_paths_tool(paper_id))

    assert isinstance(paths, list)
    assert len(paths) == 1
    entry = paths[0]
    assert entry["path"] == Path(src).resolve().as_posix()
    assert entry["is_primary"] == 1


def test_get_paper_paths_tool_empty_for_no_path(kb_conn):
    paper_id = _register(kb_conn, "Pathless Paper")

    with _patch_conn(kb_conn):
        paths = json.loads(papers_routes.get_paper_paths_tool(paper_id))

    assert paths == []
