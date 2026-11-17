"""Every path that stores a chunk or embeds one redacts first (#592).

A leak check looks at what was stored, what the FTS index answers and every text the embedder received, by
12-character windows, so a fragment of a secret fails as surely as the whole of it.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
from unittest.mock import MagicMock, patch

import fitz
import httpx
import pytest

from knowledge_base import ingest
from knowledge_base.db import get_connection, resolve_db_path
from knowledge_base.redact import scrub
from knowledge_base.utils import content_hash
from tests.conftest import StubEmbedder
from tests.declared_env import TEST_EMBED_DIM

# Example shapes built at run time, so no token literal is committed.
GITHUB_TOKEN = "ghp_" + "Zq7Xw3Rt9Lp2Vb6Nm4Kc8Hd1Fs5Gj0Ay" + "Ab3d"
AWS_KEY = "AKIA" + "QX7RT9LP2VB6NM4K"


def _windows(text: str, size: int = 12) -> set[str]:
    return {text[i : i + size] for i in range(len(text) - size + 1)}


def _leaks(secret: str, *texts: str) -> bool:
    return any(_windows(secret) & _windows(t) for t in texts)


def _assert_clean(conn: sqlite3.Connection, embedded: list[str], *secrets: str) -> None:
    rows = conn.execute("SELECT content, content_hash FROM chunks").fetchall()
    assert rows
    for secret in secrets:
        assert not _leaks(secret, *(r["content"] for r in rows), *embedded)
        term = secret.split("_", 1)[-1]  # unicode61 splits on "_": the body is the searchable term
        assert not conn.execute("SELECT 1 FROM chunks_fts WHERE chunks_fts MATCH ?", (f'"{term}"',)).fetchone()
    for r in rows:
        assert r["content_hash"] == content_hash(r["content"])


@pytest.fixture
def folder_embedder(monkeypatch) -> StubEmbedder:
    texts = StubEmbedder()

    def _embed(batch, *_args, expected_dim=None, **_kwargs):
        texts.extend(batch)
        return [[0.1] * (expected_dim or TEST_EMBED_DIM) for _ in batch]

    monkeypatch.setattr("knowledge_base.folder_summaries.embed", _embed)
    return texts


def _document(secret: str) -> str:
    return f"# Notes\n\nThe deploy reads {secret} from the vault.\n\nThe rest is ordinary prose about attention.\n"


def test_ingest_text_neither_stores_nor_embeds_a_secret(kb_conn, stub_document_embedder):
    from knowledge_base.routes.ingestion import ingest_text

    with patch("knowledge_base.routes.ingestion._get_conn", return_value=kb_conn):
        result = json.loads(ingest_text("notes/deploy.md", _document(GITHUB_TOKEN) + f"aws {AWS_KEY}\n"))
    assert result["chunks_added"] == 1
    _assert_clean(kb_conn, stub_document_embedder, GITHUB_TOKEN, AWS_KEY)
    assert "[REDACTED:github-pat]" in kb_conn.execute("SELECT content FROM chunks").fetchone()["content"]


def test_ingest_text_over_http_neither_stores_nor_embeds_a_secret(kb_http, stub_document_embedder):
    arguments = {"name": "notes/deploy.md", "content": _document(GITHUB_TOKEN)}
    response = kb_http.rpc("tools/call", {"name": "ingest_text", "arguments": arguments})
    assert response.status_code == 200
    assert not response.json()["result"].get("isError")
    conn = get_connection(resolve_db_path())
    try:
        _assert_clean(conn, stub_document_embedder, GITHUB_TOKEN)
    finally:
        conn.close()


def test_ingest_url_neither_stores_nor_embeds_a_secret(
    kb_conn, stub_document_embedder, http_requests, fake_dns, public_net, monkeypatch
):
    from knowledge_base.web import ingest_url

    monkeypatch.delenv("KNOWLEDGE_BASE_BROWSER")
    monkeypatch.delenv("KNOWLEDGE_BASE_VISION")
    fake_dns.update({"page.example.test": ["203.0.113.10"]})
    body = (
        "<html><head><title>Deploy</title></head><body><article><h1>Deploy</h1><p>"
        + "The deploy reads the token from the vault and ships the service to production. " * 6
        + f"The token is {GITHUB_TOKEN} today."
        + "</p></article></body></html>"
    ).encode()
    http_requests.handlers["page.example.test"] = lambda request: httpx.Response(
        200, content=body, headers={"content-type": "text/html"}
    )
    assert ingest_url(kb_conn, "https://page.example.test/deploy")["chunks_added"] >= 1
    _assert_clean(kb_conn, stub_document_embedder, GITHUB_TOKEN)


def test_a_token_across_a_chunk_window_never_survives(kb_conn, stub_document_embedder):
    """``chunk_text`` cuts fixed 1000-character windows: redacting each chunk would keep the token's head."""
    text = "a" * 985 + " " + GITHUB_TOKEN + " " + "b" * 1200
    assert ingest.ingest_text(kb_conn, text, source_uri="content:///cut", source_type="markdown")["chunks_added"] >= 2
    _assert_clean(kb_conn, stub_document_embedder, GITHUB_TOKEN)


def test_documents_identical_after_redaction_store_one_chunk(kb_conn, stub_document_embedder):
    other = "ghp_" + "Yq7Xw3Rt9Lp2Vb6Nm4Kc8Hd1Fs5Gj0Ay" + "Ab3d"
    first = ingest.ingest_text(kb_conn, _document(GITHUB_TOKEN), source_uri="content:///a", source_type="markdown")
    second = ingest.ingest_text(kb_conn, _document(other), source_uri="content:///b", source_type="markdown")
    assert (first["chunks_added"], second["chunks_added"], second["chunks_skipped"]) == (1, 0, 1)


def _pdf_module(pages: list[dict]) -> MagicMock:
    module = MagicMock()
    module.__version__ = "1.27.2.1"
    module.to_markdown.return_value = pages
    return module


def test_file_ingest_redacts_markdown_code_and_pdf(kb_conn, stub_document_embedder, folder_embedder, tmp_path):
    md = tmp_path / "deploy.md"
    md.write_text(_document(GITHUB_TOKEN))
    code = tmp_path / "settings.py"
    code.write_text(f'API_KEY = "{GITHUB_TOKEN}"\n\n\ndef region():\n    return "eu-west-1"\n')
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    pages = [{"metadata": {"page": 1}, "text": f"## Setup\nThe key is {AWS_KEY}.\n"}]
    ingest.ingest_file(kb_conn, md)
    ingest.ingest_file(kb_conn, code)
    with patch.dict(sys.modules, {"pymupdf4llm": _pdf_module(pages)}):
        ingest.ingest_file(kb_conn, pdf, source_type="pdf")
    _assert_clean(kb_conn, [*stub_document_embedder, *folder_embedder], GITHUB_TOKEN, AWS_KEY)
    names = [json.loads(r["metadata"]).get("name") for r in kb_conn.execute("SELECT metadata FROM chunks")]
    assert "region" in names  # the redacted code still parses into its AST chunks


def test_flat_pdf_fallback_is_redacted(tmp_path):
    pdf = tmp_path / "flat.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), f"The key is {AWS_KEY} here")
    doc.save(str(pdf))
    doc.close()
    text = ingest._extract_pdf_text(pdf)
    assert "[REDACTED:aws-access-key]" in text
    assert not _leaks(AWS_KEY, text)


def test_pdf_page_map_counts_redacted_text(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    page1 = f"## Setup\nThe key is {AWS_KEY}.\n"
    pages = [{"metadata": {"page": 1}, "text": page1}, {"metadata": {"page": 2}, "text": "## Results\nGood.\n"}]
    with patch.dict(sys.modules, {"pymupdf4llm": _pdf_module(pages)}):
        text, page_map = ingest._extract_pdf_markdown(pdf)
    assert page_map == {0: 1, len(scrub(page1)) + 2: 2}
    assert text[len(scrub(page1)) + 2 :].startswith("## Results")
    assert not _leaks(AWS_KEY, text)


QUERY_VALUE = "Xk9Lp2Rt5Yv8Bn1Cx4Df6Qw7Zz0Aa"  # a dotless high-entropy value, as a signed URL carries


def test_chunk_metadata_is_redacted_keys_kept(kb_conn, stub_document_embedder):
    meta = {"title": f"Deploy {GITHUB_TOKEN}", "pages": [1, 2], "nested": {"alt": f"aws {AWS_KEY}"}}
    ingest.ingest_text(kb_conn, "ordinary prose", source_uri="content:///m", source_type="markdown", metadata=meta)
    stored = json.loads(kb_conn.execute("SELECT metadata FROM chunks").fetchone()["metadata"])
    assert stored == {
        "title": "Deploy [REDACTED:github-pat]",
        "pages": [1, 2],
        "nested": {"alt": "aws [REDACTED:aws-access-key]"},
    }


def test_clean_metadata_is_stored_byte_for_byte(kb_conn):
    raw = '{"extractor": "pymupdf4llm@1.27.2.1", "pages": [3]}'
    ingest._insert_chunk(
        kb_conn, content_hash="h", content="c", source_type="pdf", source_uri="/p.pdf", chunk_index=0, metadata=raw
    )
    assert kb_conn.execute("SELECT metadata FROM chunks").fetchone()["metadata"] == raw


def test_metadata_urls_keep_their_query(kb_conn):
    """Ruling 2026-11-17: a stored URL is a reference; it loses its userinfo and keeps the rest."""
    meta = {
        "source_url": f"https://reader:example-pass@docs.example.test/a?sig={QUERY_VALUE}",
        "image_url": f"HTTPS://{GITHUB_TOKEN}@docs.example.test/i.png?sig={QUERY_VALUE}",
        "other_url": "Http://reader:example-pass@x.example.test/p?k=v",
        "url_with_newline": "https://reader:example-pass@x.example.test/p\n",
    }
    ingest._insert_chunk(
        kb_conn,
        content_hash="h",
        content="c",
        source_type="figure",
        source_uri="https://docs.example.test/a",
        chunk_index=0,
        metadata=json.dumps(meta),
    )
    stored = json.loads(kb_conn.execute("SELECT metadata FROM chunks").fetchone()["metadata"])
    assert stored["source_url"] == f"https://docs.example.test/a?sig={QUERY_VALUE}"
    assert stored["image_url"] == f"HTTPS://docs.example.test/i.png?sig={QUERY_VALUE}"
    assert stored["other_url"] == "Http://x.example.test/p?k=v"
    # Not a whole URL (a trailing newline): ordinary text, whose link keeps all but its password.
    assert stored["url_with_newline"] == "https://reader:[REDACTED:connection-string-password]@x.example.test/p\n"


def _serve_article(http_requests, fake_dns, text: str) -> None:
    fake_dns.update({"docs.example.test": ["203.0.113.12"]})
    body = (
        "<html><head><title>Docs</title></head><body><article><h1>Docs</h1><p>"
        + "The service reads its settings from the environment and logs to the journal. " * 6
        + text
        + "</p></article></body></html>"
    ).encode()
    http_requests.handlers["docs.example.test"] = lambda request: httpx.Response(
        200, content=body, headers={"content-type": "text/html"}
    )


@pytest.mark.parametrize("userinfo", ["reader:example-pass@", GITHUB_TOKEN + "@"])
def test_ingest_url_stores_the_url_without_userinfo(
    kb_conn, stub_document_embedder, http_requests, fake_dns, public_net, monkeypatch, userinfo
):
    from knowledge_base import web

    monkeypatch.delenv("KNOWLEDGE_BASE_BROWSER")
    monkeypatch.delenv("KNOWLEDGE_BASE_VISION")
    seen: list[str] = []
    monkeypatch.setattr(
        web, "_extract_html_images", lambda conn, html, *, source_url, **_kw: seen.append(source_url) or 0
    )
    _serve_article(http_requests, fake_dns, "Plain text.")
    url = f"https://{userinfo}docs.example.test/page?v=2"
    result = web.ingest_url(kb_conn, url)
    assert result["url"] == url  # the caller's own input, echoed
    assert result["source_uri"] == "https://docs.example.test/page?v=2"
    assert seen == ["https://docs.example.test/page?v=2"]
    uris = {r[0] for r in kb_conn.execute("SELECT source_uri FROM chunks")}
    assert uris == {"https://docs.example.test/page?v=2"}
    again = web.ingest_url(kb_conn, url)
    assert (again["chunks_added"], again["source_uri"]) == (0, "https://docs.example.test/page?v=2")


def test_source_uris_keep_their_identity(
    kb_conn, stub_document_embedder, folder_embedder, http_requests, fake_dns, public_net, monkeypatch, tmp_path
):
    """Ruling 2026-11-17: links are not redacted; file paths and document names are identity."""
    from knowledge_base.routes.ingestion import ingest_text
    from knowledge_base.web import ingest_url

    monkeypatch.delenv("KNOWLEDGE_BASE_BROWSER")
    monkeypatch.delenv("KNOWLEDGE_BASE_VISION")
    _serve_article(http_requests, fake_dns, "Plain text.")
    url = f"https://docs.example.test/d?id={QUERY_VALUE}"
    assert ingest_url(kb_conn, url)["source_uri"] == url
    folder = tmp_path / "sk-abcdefghijklmnopqrstuv"
    folder.mkdir()
    doc = folder / f"{QUERY_VALUE}"
    doc.write_text("ordinary notes")
    ingest.ingest_file(kb_conn, doc, source_type="markdown")
    with patch("knowledge_base.routes.ingestion._get_conn", return_value=kb_conn):
        stored = json.loads(ingest_text(f"notes/{GITHUB_TOKEN}.md", "other notes"))["source_uri"]
    uris = {r[0] for r in kb_conn.execute("SELECT source_uri FROM chunks")}
    assert uris == {url, doc.resolve().as_posix(), stored}
    assert ingest.reingest_file(kb_conn, doc, source_type="markdown")["chunks_added"] == 1


_VISION = {"base_url": "http://vision.example.test:11434", "model": "llava"}


def _figure_said() -> list[dict]:
    return [
        {
            "description": f"A dashboard showing the token {GITHUB_TOKEN}",
            "figure_type": "chart",
            "title": f"Key {AWS_KEY}",
        }
    ]


def _run_html_images(conn, tmp_path, http_requests):
    from knowledge_base.web import _extract_html_images
    from tests.test_ingest import _make_test_png

    http_requests.handlers["img.example.test"] = lambda request: httpx.Response(200, content=_make_test_png())
    html = '<html><body><img src="https://img.example.test/d.png" alt="diagram"></body></html>'
    return _extract_html_images(conn, html, source_url="https://docs.example.test/page")


def _run_web_figures(conn, tmp_path, _http):
    from knowledge_base.web import _extract_web_figures

    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\nFAKE")
    return _extract_web_figures(conn, "https://docs.example.test/page", shot)


def _run_element_captures(conn, tmp_path, _http):
    from knowledge_base.web import _extract_element_captures
    from tests.test_ingest import _make_test_png

    png = tmp_path / "element_0.png"
    png.write_bytes(_make_test_png(400, 300))
    return _extract_element_captures(
        conn, "https://docs.example.test/page", [{"path": png, "tag": "canvas", "width": 400, "height": 300}]
    )


def _run_pdf_figures(conn, _tmp_path, _http):
    from knowledge_base.vision import CaptionMap, _persist_figures

    figure = {**_figure_said()[0], "entities_mentioned": [], "_ocr_text": f"terminal: export TOKEN={GITHUB_TOKEN}"}
    return _persist_figures(
        conn,
        source_uri="/papers/p.pdf",
        pages=None,
        page_results={0: [figure]},
        caption_map=CaptionMap(by_image={}, by_page={0: [f"Figure 1: key {AWS_KEY}"]}),
        model="llava",
        on_progress=None,
    )


@pytest.mark.parametrize(
    "site",
    [_run_html_images, _run_web_figures, _run_element_captures, _run_pdf_figures],
    ids=["html-images", "web-figures", "element-captures", "pdf-figures"],
)
def test_figure_text_is_neither_stored_nor_embedded(
    kb_conn, stub_document_embedder, http_requests, fake_dns, public_net, tmp_path, site
):
    fake_dns.update({"img.example.test": ["203.0.113.11"]})
    with (
        patch("knowledge_base.vision._get_vision_config", return_value=_VISION),
        patch("knowledge_base.vision._vision_call", return_value=_figure_said()),
    ):
        assert site(kb_conn, tmp_path, http_requests) == 1
    _assert_clean(kb_conn, stub_document_embedder, GITHUB_TOKEN, AWS_KEY)
    meta = kb_conn.execute("SELECT metadata FROM chunks").fetchone()["metadata"]
    assert not _leaks(GITHUB_TOKEN, meta) and not _leaks(AWS_KEY, meta)


def test_folder_summary_is_a_fixpoint(kb_conn, stub_document_embedder, folder_embedder, tmp_path):
    """A summary joins file names (kept as identity), 200-character cuts and joined lines: each can make a match."""
    from tests.conftest import machine_token

    folder = tmp_path / "notes"
    folder.mkdir()
    (folder / f"{machine_token('laptop')}.md").write_text("ordinary prose about attention")
    (folder / "keys.md").write_text("api_key:\n" + "a3f1b2c4d5e6f7a8b9c0d1e2f3a4b5c6" + "\n")
    (folder / "cut.md").write_text("word " * 36 + "Ab3dE5fG7hI9jK1lM2nO.txt and more")
    for f in sorted(folder.iterdir()):
        ingest.ingest_file(kb_conn, f)
    summary = kb_conn.execute("SELECT summary FROM folder_summaries").fetchone()["summary"]
    assert summary == scrub(summary)
    assert folder_embedder and all(t == scrub(t) for t in folder_embedder)
    assert "[REDACTED:machine-token]" in summary and "[REDACTED:hex-secret]" in summary
    assert "[REDACTED:high-entropy]" in summary


#: Every function that inserts a chunk, and the test proving it redacts before it hashes or embeds.
INSERT_SITES = {
    "ingest._store_text_chunks": "test_ingest_text_neither_stores_nor_embeds_a_secret",
    "ingest._produce_and_insert_chunks": "test_file_ingest_redacts_markdown_code_and_pdf",
    "web._extract_html_images": "test_figure_text_is_neither_stored_nor_embedded[html-images]",
    "web._extract_web_figures": "test_figure_text_is_neither_stored_nor_embedded[web-figures]",
    "web._extract_element_captures": "test_figure_text_is_neither_stored_nor_embedded[element-captures]",
    "vision._persist_figures": "test_figure_text_is_neither_stored_nor_embedded[pdf-figures]",
}


def test_every_chunk_insert_site_is_covered():
    """A new caller of ``_insert_chunk`` (or a new raw INSERT) must scrub before it hashes and get a leak case here."""
    import ast
    import re

    package = Path(ingest.__file__).parent
    callers: set[str] = set()
    raw_inserts: list[str] = []
    for source in sorted(package.rglob("*.py")):
        text = source.read_text(encoding="utf-8")
        module = source.relative_to(package).with_suffix("").as_posix().replace("/", ".")
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for call in ast.walk(node):
                    if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "_insert_chunk":
                        callers.add(f"{module}.{node.name}")
        for line in text.splitlines():
            if re.search(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+\[?chunks\b", line):
                raw_inserts.append(module)  # only _insert_chunk's own statement
    assert callers == set(INSERT_SITES), (
        "chunk insert sites changed: scrub the text before it is hashed or embedded and add its leak case to "
        f"INSERT_SITES (new: {sorted(callers - set(INSERT_SITES))}, gone: {sorted(set(INSERT_SITES) - callers)})"
    )
    assert raw_inserts == ["ingest"], raw_inserts
    assert all(name.split("[")[0] in globals() for name in INSERT_SITES.values())


def test_a_link_across_a_chunk_window_is_kept_whole_and_the_report_stays_clean(
    kb_conn, stub_document_embedder, tmp_path, capsys
):
    """Ruling 2026-11-17: a link is not redacted. A window cut inside one would leave a fragment, no longer a link,
    that the report flags and the rewrite redacts; so no window starts or ends inside a link."""
    from knowledge_base.chunking import chunk_text
    from knowledge_base.indexer import main

    link = "https://data.example.org/get?api_key=a3f1b2c4d5e6f7a8b9c0d1e2f3a4b5c6&set=7"
    # The second window starts at 800, which is where this link's query begins.
    query_at = len("https://data.example.org/get?")
    text = "a" * (800 - query_at - 1) + " " + link + " " + "b" * 1600
    assert text.index(link) + query_at == 800
    for chunk in chunk_text(text):
        assert link[query_at:] not in chunk.replace(link, ""), "a window cut the link"
    ingest.ingest_text(kb_conn, text, source_uri="content:///links", source_type="markdown")
    contents = [r[0] for r in kb_conn.execute("SELECT content FROM chunks")]
    assert all(c == scrub(c) for c in contents)
    assert any(link in c for c in contents)
    kb_conn.close()
    capsys.readouterr()
    with pytest.raises(SystemExit) as done:
        main(["--db", str(tmp_path / "test.db"), "redact"])
    assert done.value.code == 0


def test_every_stored_chunk_is_a_fixpoint(kb_conn, stub_document_embedder):
    """A window can start mid-word, which a rule then reads as a boundary: each chunk is scrubbed again."""
    zero = "me_" + "0" * 32
    text = "x" * 799 + "_" + zero + " " + "y" * 1500
    ingest.ingest_text(kb_conn, text, source_uri="content:///edge", source_type="markdown")
    assert all(r[0] == scrub(r[0]) for r in kb_conn.execute("SELECT content FROM chunks"))


@pytest.mark.parametrize("status", [401, 500])
def test_a_failed_fetch_names_the_url_without_userinfo(
    kb_conn, stub_document_embedder, http_requests, fake_dns, public_net, monkeypatch, status
):
    from knowledge_base.exceptions import ValidationError
    from knowledge_base.web import ingest_url

    monkeypatch.delenv("KNOWLEDGE_BASE_BROWSER")
    monkeypatch.delenv("KNOWLEDGE_BASE_VISION")
    fake_dns.update({"docs.example.test": ["203.0.113.12"]})
    http_requests.handlers["docs.example.test"] = lambda request: httpx.Response(status)
    with pytest.raises(ValidationError) as failed:
        ingest_url(kb_conn, "https://reader:example-pass@docs.example.test/page")
    assert "example-pass" not in str(failed.value) and "reader" not in str(failed.value)
    assert "https://docs.example.test/page" in str(failed.value)


def test_a_link_longer_than_four_windows_is_still_cut():
    """A link stretches its window to its end, but a 200 KB URL must not become one 200 KB chunk for the embedder."""
    from knowledge_base.chunking import CHUNK_SIZE, chunk_text

    text = "see https://data.example.org/" + "a" * (10 * CHUNK_SIZE) + " end"
    assert max(len(c) for c in chunk_text(text)) <= 4 * CHUNK_SIZE
