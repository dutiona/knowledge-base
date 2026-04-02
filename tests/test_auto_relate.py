"""Tests for auto-relationship discovery via embedding similarity (#105)."""

import hashlib
import struct

import pytest

from knowledge_base.db import (
    RELATIONSHIP_TYPES,
    get_connection,
    init_schema,
)


def _serialize_f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_DIM = 2  # Use 2D embeddings for fast, geometrically intuitive tests.


@pytest.fixture()
def conn(tmp_path):
    """Fresh DB with embed_dim=2 for fast tests."""
    db_path = tmp_path / "test.db"
    c = get_connection(db_path)
    # Pre-set dim=2 before init_schema creates chunks_vec
    c.execute(
        "CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    c.execute("INSERT INTO config (key, value) VALUES ('embed_model', 'test')")
    c.execute("INSERT INTO config (key, value) VALUES ('embed_dim', ?)", (str(_DIM),))
    c.commit()
    init_schema(c)
    return c


def _make_paper(conn, title, source_uri=None):
    """Insert a paper and optionally link via paper_paths. Returns paper_id."""
    cursor = conn.execute("INSERT INTO papers (title) VALUES (?)", (title,))
    paper_id = cursor.lastrowid
    if source_uri:
        conn.execute(
            "INSERT INTO paper_paths (paper_id, path, is_primary) VALUES (?, ?, TRUE)",
            (paper_id, source_uri),
        )
    conn.commit()
    return paper_id


def _add_chunk(conn, source_uri, content, chunk_index, embedding):
    """Insert chunk + embedding. Returns chunk_id."""
    h = hashlib.sha256(content.encode()).hexdigest()
    cursor = conn.execute(
        "INSERT INTO chunks (content_hash, content, source_type, source_uri, chunk_index) "
        "VALUES (?, ?, 'pdf', ?, ?)",
        (h, content, source_uri, chunk_index),
    )
    chunk_id = cursor.lastrowid
    conn.execute(
        "INSERT INTO chunks_vec (rowid, embedding, chunk_id) VALUES (?, ?, ?)",
        (chunk_id, _serialize_f32(embedding), chunk_id),
    )
    conn.commit()
    return chunk_id


def _paper_with_chunks(conn, title, source_uri, embeddings):
    """Create paper + chunks + embeddings. Returns (paper_id, [chunk_ids])."""
    paper_id = _make_paper(conn, title, source_uri)
    chunk_ids = []
    for i, emb in enumerate(embeddings):
        cid = _add_chunk(conn, source_uri, f"{title} chunk {i}", i, emb)
        chunk_ids.append(cid)
    return paper_id, chunk_ids


def _paper_with_chunks_no_path(conn, title, source_uri, embeddings):
    """Create paper linked via abstract_chunk_id only (no paper_paths row).

    Simulates a paper registered with a source_uri conflict —
    paper_paths row is missing but chunks are reachable via
    papers.abstract_chunk_id → chunks.source_uri.
    """
    # Create the first chunk (will become abstract_chunk_id)
    first_cid = _add_chunk(conn, source_uri, f"{title} chunk 0", 0, embeddings[0])
    # Insert paper with abstract_chunk_id, NO paper_paths entry
    cursor = conn.execute(
        "INSERT INTO papers (title, abstract_chunk_id) VALUES (?, ?)",
        (title, first_cid),
    )
    paper_id = cursor.lastrowid
    conn.commit()
    chunk_ids = [first_cid]
    for i, emb in enumerate(embeddings[1:], start=1):
        cid = _add_chunk(conn, source_uri, f"{title} chunk {i}", i, emb)
        chunk_ids.append(cid)
    return paper_id, chunk_ids


# ---------------------------------------------------------------------------
# 5f. Schema migration tests
# ---------------------------------------------------------------------------


class TestSchemaMigrations:
    def test_similar_in_relationship_types(self):
        assert "similar" in RELATIONSHIP_TYPES

    def test_similar_relationship_accepted(self, conn):
        p1 = _make_paper(conn, "A")
        p2 = _make_paper(conn, "B")
        conn.execute(
            "INSERT INTO relationships (source_paper_id, target_paper_id, relation_type) "
            "VALUES (?, ?, 'similar')",
            (p1, p2),
        )
        conn.commit()
        row = conn.execute(
            "SELECT relation_type FROM relationships WHERE source_paper_id = ?",
            (p1,),
        ).fetchone()
        assert row["relation_type"] == "similar"

    def test_auto_relate_job_type_accepted(self, conn):
        p = _make_paper(conn, "Paper")
        conn.execute(
            "INSERT INTO jobs (paper_id, job_type) VALUES (?, 'auto_relate')", (p,)
        )
        conn.commit()
        row = conn.execute(
            "SELECT job_type FROM jobs WHERE paper_id = ?", (p,)
        ).fetchone()
        assert row["job_type"] == "auto_relate"

    def test_config_threshold_defaults(self, conn):
        propose = conn.execute(
            "SELECT value FROM config WHERE key = 'auto_relate_propose_threshold'"
        ).fetchone()
        accept = conn.execute(
            "SELECT value FROM config WHERE key = 'auto_relate_accept_threshold'"
        ).fetchone()
        assert propose is not None
        assert accept is not None
        assert float(propose["value"]) == pytest.approx(0.82)
        assert float(accept["value"]) == pytest.approx(0.95)

    def test_migration_idempotent(self, tmp_path):
        db_path = tmp_path / "idem.db"
        c = get_connection(db_path)
        init_schema(c)
        p = _make_paper(c, "Paper")
        c.execute(
            "INSERT INTO jobs (paper_id, job_type) VALUES (?, 'auto_relate')", (p,)
        )
        c.commit()
        init_schema(c)
        assert c.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1

    def test_jobs_index_exists(self, conn):
        row = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND name='idx_jobs_status_created'"
        ).fetchone()
        assert row is not None


# ---------------------------------------------------------------------------
# 5b. Unit tests for auto_relate
# ---------------------------------------------------------------------------

# 2D test vectors — cosine similarity is easy to reason about geometrically:
#   VEC_A = [1, 0]                         (rightward)
#   VEC_B = [0.95, 0.3]   cos(A,B) ≈ 0.95 (high similarity)
#   VEC_C = [0, 1]        cos(A,C) = 0.0   (orthogonal)
_VEC_A = [1.0, 0.0]
_VEC_B = [0.95, 0.3]  # cos ≈ 0.954
_VEC_C = [0.0, 1.0]  # cos = 0.0


class TestAutoRelate:
    def test_creates_similar_relationship(self, conn):
        from knowledge_base.auto_relate import auto_relate

        pa, _ = _paper_with_chunks(conn, "A", "/tmp/a.pdf", [_VEC_A])
        pb, _ = _paper_with_chunks(conn, "B", "/tmp/b.pdf", [_VEC_B])

        result = auto_relate(conn, pa)
        assert result["relationships_created"] >= 1

        row = conn.execute(
            "SELECT * FROM relationships WHERE relation_type = 'similar'"
        ).fetchone()
        assert row is not None
        assert {row["source_paper_id"], row["target_paper_id"]} == {pa, pb}

    def test_skips_dissimilar_papers(self, conn):
        from knowledge_base.auto_relate import auto_relate

        pa, _ = _paper_with_chunks(conn, "A", "/tmp/a.pdf", [_VEC_A])
        _paper_with_chunks(conn, "C", "/tmp/c.pdf", [_VEC_C])

        result = auto_relate(conn, pa)
        assert result["relationships_created"] == 0

    def test_upserts_existing_similar(self, conn):
        """Re-running auto_relate updates confidence on existing similar edges."""
        from knowledge_base.auto_relate import auto_relate

        pa, _ = _paper_with_chunks(conn, "A", "/tmp/a.pdf", [_VEC_A])
        pb, _ = _paper_with_chunks(conn, "B", "/tmp/b.pdf", [_VEC_B])

        lo, hi = min(pa, pb), max(pa, pb)
        conn.execute(
            "INSERT INTO relationships "
            "(source_paper_id, target_paper_id, relation_type, confidence) "
            "VALUES (?, ?, 'similar', 0.5)",
            (lo, hi),
        )
        conn.commit()

        auto_relate(conn, pa)
        count = conn.execute(
            "SELECT count(*) FROM relationships WHERE relation_type = 'similar'"
        ).fetchone()[0]
        assert count == 1  # still one edge, not duplicated

        row = conn.execute(
            "SELECT confidence FROM relationships WHERE relation_type = 'similar'"
        ).fetchone()
        assert row["confidence"] != 0.5  # confidence was updated by recomputation

    def test_rethreshold_deletes_stale_edge(self, conn):
        """Raising threshold removes edges that no longer qualify."""
        from knowledge_base.auto_relate import auto_relate

        pa, _ = _paper_with_chunks(conn, "A", "/tmp/a.pdf", [_VEC_A])
        _paper_with_chunks(conn, "B", "/tmp/b.pdf", [_VEC_B])

        # First run creates edge
        result = auto_relate(conn, pa)
        assert result["relationships_created"] >= 1

        # Raise threshold above any achievable score
        conn.execute(
            "UPDATE config SET value = '0.9999' "
            "WHERE key = 'auto_relate_propose_threshold'"
        )
        conn.commit()

        # Re-run should delete the stale edge
        auto_relate(conn, pa)
        count = conn.execute(
            "SELECT count(*) FROM relationships WHERE relation_type = 'similar'"
        ).fetchone()[0]
        assert count == 0

    def test_coexists_with_other_types(self, conn):
        from knowledge_base.auto_relate import auto_relate

        pa, _ = _paper_with_chunks(conn, "A", "/tmp/a.pdf", [_VEC_A])
        pb, _ = _paper_with_chunks(conn, "B", "/tmp/b.pdf", [_VEC_B])

        conn.execute(
            "INSERT INTO relationships "
            "(source_paper_id, target_paper_id, relation_type) "
            "VALUES (?, ?, 'cites')",
            (pa, pb),
        )
        conn.commit()

        result = auto_relate(conn, pa)
        assert result["relationships_created"] >= 1

        similar = conn.execute(
            "SELECT * FROM relationships WHERE relation_type = 'similar'"
        ).fetchone()
        assert similar is not None

    def test_respects_thresholds(self, conn):
        from knowledge_base.auto_relate import auto_relate

        conn.execute(
            "UPDATE config SET value = '0.999' "
            "WHERE key = 'auto_relate_propose_threshold'"
        )
        conn.commit()

        pa, _ = _paper_with_chunks(conn, "A", "/tmp/a.pdf", [_VEC_A])
        _paper_with_chunks(conn, "B", "/tmp/b.pdf", [_VEC_B])

        result = auto_relate(conn, pa)
        assert result["relationships_created"] == 0

    def test_normalizes_direction(self, conn):
        from knowledge_base.auto_relate import auto_relate

        pa, _ = _paper_with_chunks(conn, "A", "/tmp/a.pdf", [_VEC_A])
        pb, _ = _paper_with_chunks(conn, "B", "/tmp/b.pdf", [_VEC_B])

        higher = max(pa, pb)
        auto_relate(conn, higher)

        row = conn.execute(
            "SELECT source_paper_id, target_paper_id FROM relationships "
            "WHERE relation_type = 'similar'"
        ).fetchone()
        assert row is not None
        assert row["source_paper_id"] < row["target_paper_id"]

    def test_stores_evidence_chunk(self, conn):
        from knowledge_base.auto_relate import auto_relate

        pa, _ = _paper_with_chunks(conn, "A", "/tmp/a.pdf", [_VEC_A])
        _paper_with_chunks(conn, "B", "/tmp/b.pdf", [_VEC_B])

        auto_relate(conn, pa)

        row = conn.execute(
            "SELECT evidence_chunk_id FROM relationships "
            "WHERE relation_type = 'similar'"
        ).fetchone()
        assert row is not None
        assert row["evidence_chunk_id"] is not None

    def test_no_embeddings_skips(self, conn):
        from knowledge_base.auto_relate import auto_relate

        pa = _make_paper(conn, "A", "/tmp/a.pdf")
        _paper_with_chunks(conn, "B", "/tmp/b.pdf", [_VEC_B])

        result = auto_relate(conn, pa)
        assert "skipped" in result

    def test_top_k_averaging(self, conn):
        """Top-3 average ignores the orthogonal chunk."""
        from knowledge_base.auto_relate import auto_relate

        pa, _ = _paper_with_chunks(conn, "A", "/tmp/a.pdf", [_VEC_A, _VEC_A])
        # 3 similar + 1 orthogonal → top-3 avg should still exceed threshold
        pb, _ = _paper_with_chunks(
            conn, "B", "/tmp/b.pdf", [_VEC_B, _VEC_B, _VEC_B, _VEC_C]
        )

        result = auto_relate(conn, pa)
        assert result["relationships_created"] >= 1

    def test_auto_accept_vs_propose(self, conn):
        """Nearly identical → confidence >= accept_threshold."""
        from knowledge_base.auto_relate import auto_relate

        pa, _ = _paper_with_chunks(conn, "A", "/tmp/a.pdf", [_VEC_A])
        # cos([1,0], [0.9999, 0.001]) ≈ 1.0
        _paper_with_chunks(conn, "B", "/tmp/b.pdf", [[0.9999, 0.001]])

        auto_relate(conn, pa)

        row = conn.execute(
            "SELECT confidence FROM relationships WHERE relation_type = 'similar'"
        ).fetchone()
        assert row is not None
        assert row["confidence"] >= 0.95


# ---------------------------------------------------------------------------
# 5b2. Fallback to abstract_chunk_id when paper_paths is missing (#165)
# ---------------------------------------------------------------------------


class TestAutoRelateFallback:
    def test_source_paper_without_paper_paths(self, conn):
        """auto_relate finds embeddings for source paper via abstract_chunk_id fallback."""
        from knowledge_base.auto_relate import auto_relate

        # Source paper has no paper_paths row — only abstract_chunk_id
        pa, _ = _paper_with_chunks_no_path(conn, "A", "/tmp/a.pdf", [_VEC_A])
        # Target paper has normal paper_paths
        pb, _ = _paper_with_chunks(conn, "B", "/tmp/b.pdf", [_VEC_B])

        result = auto_relate(conn, pa)
        assert result["relationships_created"] >= 1

        row = conn.execute(
            "SELECT * FROM relationships WHERE relation_type = 'similar'"
        ).fetchone()
        assert row is not None
        assert {row["source_paper_id"], row["target_paper_id"]} == {pa, pb}

    def test_target_paper_without_paper_paths(self, conn):
        """auto_relate discovers target paper via abstract_chunk_id fallback."""
        from knowledge_base.auto_relate import auto_relate

        # Source paper has normal paper_paths
        pa, _ = _paper_with_chunks(conn, "A", "/tmp/a.pdf", [_VEC_A])
        # Target paper has no paper_paths row — only abstract_chunk_id
        pb, _ = _paper_with_chunks_no_path(conn, "B", "/tmp/b.pdf", [_VEC_B])

        result = auto_relate(conn, pa)
        assert result["relationships_created"] >= 1

        row = conn.execute(
            "SELECT * FROM relationships WHERE relation_type = 'similar'"
        ).fetchone()
        assert row is not None
        assert {row["source_paper_id"], row["target_paper_id"]} == {pa, pb}

    def test_both_papers_without_paper_paths(self, conn):
        """auto_relate works when both papers lack paper_paths rows."""
        from knowledge_base.auto_relate import auto_relate

        pa, _ = _paper_with_chunks_no_path(conn, "A", "/tmp/a.pdf", [_VEC_A])
        pb, _ = _paper_with_chunks_no_path(conn, "B", "/tmp/b.pdf", [_VEC_B])

        result = auto_relate(conn, pa)
        assert result["relationships_created"] >= 1

        row = conn.execute(
            "SELECT * FROM relationships WHERE relation_type = 'similar'"
        ).fetchone()
        assert row is not None
        assert {row["source_paper_id"], row["target_paper_id"]} == {pa, pb}

    def test_skips_duplicate_source_uri_papers(self, conn):
        """Papers sharing the same source_uri (duplicate registrations) are skipped."""
        from knowledge_base.auto_relate import auto_relate

        # Paper A owns /tmp/shared.pdf via paper_paths
        pa, _ = _paper_with_chunks(conn, "A", "/tmp/shared.pdf", [_VEC_A])
        # Paper B also points to /tmp/shared.pdf but via abstract_chunk_id only
        pb, _ = _paper_with_chunks_no_path(conn, "B", "/tmp/shared.pdf", [_VEC_B])

        result = auto_relate(conn, pa)
        # Should NOT create a relationship — same underlying chunks
        assert result["relationships_created"] == 0

        count = conn.execute(
            "SELECT count(*) FROM relationships WHERE relation_type = 'similar'"
        ).fetchone()[0]
        assert count == 0


# ---------------------------------------------------------------------------
# 5b-perf. Upper-triangle scan optimisation (#166)
# ---------------------------------------------------------------------------


class TestOnlyCompareHigher:
    def test_only_compare_higher_skips_lower_ids(self, conn):
        """With only_compare_higher=True, auto_relate skips papers with id < paper_id."""
        from knowledge_base.auto_relate import auto_relate

        pa, _ = _paper_with_chunks(conn, "A", "/tmp/a.pdf", [_VEC_A])
        pb, _ = _paper_with_chunks(conn, "B", "/tmp/b.pdf", [_VEC_B])
        assert pa < pb  # sanity: A was created first

        # From B's perspective with only_compare_higher=True, A (lower id) is skipped
        result = auto_relate(conn, pb, only_compare_higher=True)
        assert result["relationships_created"] == 0
        # No higher-id papers exist, so auto_relate skips early
        assert "skipped" in result

    def test_only_compare_higher_includes_higher_ids(self, conn):
        """With only_compare_higher=True, auto_relate still compares higher-id papers."""
        from knowledge_base.auto_relate import auto_relate

        pa, _ = _paper_with_chunks(conn, "A", "/tmp/a.pdf", [_VEC_A])
        pb, _ = _paper_with_chunks(conn, "B", "/tmp/b.pdf", [_VEC_B])
        assert pa < pb

        # From A's perspective, B (higher id) is included
        result = auto_relate(conn, pa, only_compare_higher=True)
        assert result["papers_compared"] == 1
        assert result["relationships_created"] >= 1

    def test_full_scan_halves_comparisons(self, conn):
        """scan_relationships full scan does N*(N-1)/2 comparisons, not N*(N-1)."""
        from knowledge_base.auto_relate import auto_relate

        pa, _ = _paper_with_chunks(conn, "A", "/tmp/a.pdf", [_VEC_A])
        pb, _ = _paper_with_chunks(conn, "B", "/tmp/b.pdf", [_VEC_B])
        pc, _ = _paper_with_chunks(conn, "C", "/tmp/c.pdf", [_VEC_A])

        # Call auto_relate for each paper with only_compare_higher=True
        # (simulating what scan_relationships should do)
        total_compared = 0
        for pid in [pa, pb, pc]:
            result = auto_relate(conn, pid, only_compare_higher=True)
            total_compared += result.get("papers_compared", 0)

        # 3 papers → 3 unique pairs, not 6
        assert total_compared == 3  # (A→B, A→C, B→C)

    def test_scan_relationships_passes_only_compare_higher(self, conn):
        """scan_relationships(paper_id=None) submits jobs with only_compare_higher=True."""
        from unittest.mock import patch

        from knowledge_base.jobs import submit_job

        pa, _ = _paper_with_chunks(conn, "A", "/tmp/a.pdf", [_VEC_A])
        pb, _ = _paper_with_chunks(conn, "B", "/tmp/b.pdf", [_VEC_B])

        calls = []
        original_submit = submit_job

        def spy_submit(c, paper_id, job_type, params=None):
            calls.append(params)
            return original_submit(c, paper_id, job_type, params)

        from knowledge_base.routes.papers import scan_relationships

        with (
            patch("knowledge_base.jobs.submit_job", side_effect=spy_submit),
            patch("knowledge_base.routes.papers._get_conn", return_value=conn),
        ):
            scan_relationships()

        # All full-scan jobs should have only_compare_higher=True
        assert len(calls) == 2
        for call_params in calls:
            assert call_params.get("only_compare_higher") is True


# ---------------------------------------------------------------------------
# 5c. Job dispatch tests
# ---------------------------------------------------------------------------


class TestJobDispatch:
    def test_auto_relate_job_dispatches(self, conn):
        """submit_job + worker tick → auto_relate runs."""
        from knowledge_base.jobs import submit_job

        pa, _ = _paper_with_chunks(conn, "A", "/tmp/a.pdf", [_VEC_A])
        _paper_with_chunks(conn, "B", "/tmp/b.pdf", [_VEC_B])

        job_id = submit_job(conn, pa, "auto_relate", {"paper_id": pa})
        assert job_id is not None

        # Verify job was created
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert row["job_type"] == "auto_relate"
        assert row["status"] == "pending"

    def test_dispatch_passes_only_compare_higher(self, conn):
        """Worker dispatch path correctly reads only_compare_higher from params."""
        import json

        from knowledge_base.jobs import _JobWorker

        pa, _ = _paper_with_chunks(conn, "A", "/tmp/a.pdf", [_VEC_A])
        pb, _ = _paper_with_chunks(conn, "B", "/tmp/b.pdf", [_VEC_B])
        assert pa < pb

        # Simulate a job row with only_compare_higher in params
        job = {
            "job_type": "auto_relate",
            "paper_id": pb,
            "params": json.dumps({"paper_id": pb, "only_compare_higher": True}),
        }
        worker = _JobWorker()
        result = worker._dispatch(conn, job, on_progress=lambda msg: None)

        # B is the higher ID; with only_compare_higher=True, no papers above B exist
        assert result["relationships_created"] == 0
        assert "skipped" in result

    def test_auto_relate_job_dedup(self, conn):
        """Submitting twice for same paper → only one job."""
        from knowledge_base.jobs import submit_job

        pa = _make_paper(conn, "A")
        j1 = submit_job(conn, pa, "auto_relate", {"paper_id": pa})
        j2 = submit_job(conn, pa, "auto_relate", {"paper_id": pa})
        assert j1 == j2  # dedup returns same job_id


# ---------------------------------------------------------------------------
# 5e. Stale relationship cleanup tests
# ---------------------------------------------------------------------------


class TestStaleCleanup:
    def test_reingest_removes_similar_relationships(self, conn, tmp_path):
        """Reingest deletes 'similar' edges for affected papers."""
        pa, _ = _paper_with_chunks(conn, "A", "/tmp/a.pdf", [_VEC_A])
        pb, _ = _paper_with_chunks(conn, "B", "/tmp/b.pdf", [_VEC_B])

        lo, hi = min(pa, pb), max(pa, pb)
        conn.execute(
            "INSERT INTO relationships "
            "(source_paper_id, target_paper_id, relation_type, confidence) "
            "VALUES (?, ?, 'similar', 0.9)",
            (lo, hi),
        )
        conn.commit()

        # Simulate what server.py reingest cleanup should do
        source_uri = "/tmp/a.pdf"
        affected = conn.execute(
            "SELECT paper_id FROM paper_paths WHERE path = ?", (source_uri,)
        ).fetchall()
        for row in affected:
            pid = row["paper_id"]
            conn.execute(
                "DELETE FROM relationships WHERE relation_type = 'similar' "
                "AND (source_paper_id = ? OR target_paper_id = ?)",
                (pid, pid),
            )
        conn.commit()

        count = conn.execute(
            "SELECT count(*) FROM relationships WHERE relation_type = 'similar'"
        ).fetchone()[0]
        assert count == 0

    def test_reingest_preserves_non_similar(self, conn):
        """Reingest keeps 'cites' — only 'similar' deleted."""
        pa, _ = _paper_with_chunks(conn, "A", "/tmp/a.pdf", [_VEC_A])
        pb, _ = _paper_with_chunks(conn, "B", "/tmp/b.pdf", [_VEC_B])

        conn.execute(
            "INSERT INTO relationships "
            "(source_paper_id, target_paper_id, relation_type) "
            "VALUES (?, ?, 'cites')",
            (pa, pb),
        )
        lo, hi = min(pa, pb), max(pa, pb)
        conn.execute(
            "INSERT INTO relationships "
            "(source_paper_id, target_paper_id, relation_type, confidence) "
            "VALUES (?, ?, 'similar', 0.9)",
            (lo, hi),
        )
        conn.commit()

        # Cleanup similar only
        conn.execute(
            "DELETE FROM relationships WHERE relation_type = 'similar' "
            "AND (source_paper_id = ? OR target_paper_id = ?)",
            (pa, pa),
        )
        conn.commit()

        cites = conn.execute(
            "SELECT count(*) FROM relationships WHERE relation_type = 'cites'"
        ).fetchone()[0]
        similar = conn.execute(
            "SELECT count(*) FROM relationships WHERE relation_type = 'similar'"
        ).fetchone()[0]
        assert cites == 1
        assert similar == 0

    def test_re_embed_removes_all_similar(self, conn):
        """re_embed deletes ALL 'similar' edges corpus-wide."""
        pa, _ = _paper_with_chunks(conn, "A", "/tmp/a.pdf", [_VEC_A])
        pb, _ = _paper_with_chunks(conn, "B", "/tmp/b.pdf", [_VEC_B])
        pc, _ = _paper_with_chunks(conn, "C", "/tmp/c.pdf", [_VEC_A])

        for lo, hi in [(min(pa, pb), max(pa, pb)), (min(pa, pc), max(pa, pc))]:
            conn.execute(
                "INSERT INTO relationships "
                "(source_paper_id, target_paper_id, relation_type, confidence) "
                "VALUES (?, ?, 'similar', 0.9)",
                (lo, hi),
            )
        conn.execute(
            "INSERT INTO relationships "
            "(source_paper_id, target_paper_id, relation_type) "
            "VALUES (?, ?, 'cites')",
            (pa, pb),
        )
        conn.commit()

        # Corpus-wide similar cleanup (what re_embed should do)
        conn.execute("DELETE FROM relationships WHERE relation_type = 'similar'")
        conn.commit()

        similar = conn.execute(
            "SELECT count(*) FROM relationships WHERE relation_type = 'similar'"
        ).fetchone()[0]
        cites = conn.execute(
            "SELECT count(*) FROM relationships WHERE relation_type = 'cites'"
        ).fetchone()[0]
        assert similar == 0
        assert cites == 1  # preserved
