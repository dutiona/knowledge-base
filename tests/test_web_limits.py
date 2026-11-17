"""URL ingest and image downloads stop at the byte cap, the redirect limit and the deadline (#588).

The network is faked at the transport (``http_requests``), names at the resolver (``fake_dns``), the documentation
ranges play the public internet (``public_net``), and a fake clock drives the deadline (``utils.monotonic``).
"""

from __future__ import annotations

import gzip
import io
import json
import logging
from collections.abc import Callable, Iterator
from unittest.mock import patch

import httpx
import pytest

from knowledge_base import utils
from knowledge_base.exceptions import ValidationError
from knowledge_base.web import _extract_html_images, ingest_url
from tests.declared_env import TEST_EMBED_DIM

_URL = "https://page.example.test/start"
_ARTICLE = (
    "<html><head><title>Capped</title></head><body><article><h1>Attention</h1><p>"
    + "The dominant sequence transduction models are based on recurrent networks. " * 12
    + "</p></article></body></html>"
).encode()


def _fake_embed(texts, model="bge-m3", expected_dim=None, **_kwargs):
    return [[0.1] * (expected_dim or TEST_EMBED_DIM) for _ in texts]


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(utils, "monotonic", fake)
    return fake


@pytest.fixture(autouse=True)
def _world(monkeypatch, fake_dns, public_net) -> Iterator[None]:
    monkeypatch.delenv("KNOWLEDGE_BASE_BROWSER")
    monkeypatch.delenv("KNOWLEDGE_BASE_VISION")
    fake_dns.update({"page.example.test": ["203.0.113.10"], "img.example.test": ["203.0.113.11"]})
    with patch("knowledge_base.ingest.embed", _fake_embed), patch("knowledge_base.folder_summaries.embed", _fake_embed):
        yield


def _serve(http_requests, paths: dict[str, Callable[[], httpx.Response]], host: str = "page.example.test") -> None:
    http_requests.handlers[host] = lambda request: paths[request.url.path]()


def _redirect(location: str) -> Callable[[], httpx.Response]:
    return lambda: httpx.Response(302, headers={"location": location})


def _page(body: bytes = _ARTICLE, headers: dict[str, str] | None = None) -> Callable[[], httpx.Response]:
    """A streamed page: httpx decodes a bytes body when the response is built, a real response only as it is read."""
    return lambda: httpx.Response(200, content=iter([body]), headers={"content-type": "text/html", **(headers or {})})


def _chain(hops: int) -> dict[str, Callable[[], httpx.Response]]:
    """``/start`` then ``hops`` redirects in all, the last one landing on ``/article``."""
    paths = [f"/r{i}" for i in range(1, hops)]
    stops = ["/start", *paths]
    routes = {stop: _redirect(nxt) for stop, nxt in zip(stops, [*paths, "/article"], strict=True)}
    return {**routes, "/article": _page()}


def _chunks(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]


class _Endless:
    """An endless body of ``size``-byte chunks; ``drawn`` counts the chunks read, ``tick`` runs before each."""

    def __init__(self, size: int = 1024, tick: Callable[[], None] = lambda: None) -> None:
        self.size, self.tick, self.drawn = size, tick, 0

    def __iter__(self) -> Iterator[bytes]:
        while True:
            self.tick()
            self.drawn += 1
            yield b"x" * self.size


def test_a_body_of_exactly_the_cap_is_ingested(kb_conn, http_requests, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_BASE_FETCH_MAX_BYTES", str(len(_ARTICLE)))
    _serve(http_requests, {"/start": _page()})
    assert ingest_url(kb_conn, _URL)["chunks_added"] >= 1


def test_a_body_over_the_cap_is_refused_and_nothing_is_stored(kb_conn, http_requests, monkeypatch):
    cap = len(_ARTICLE) - 1
    monkeypatch.setenv("KNOWLEDGE_BASE_FETCH_MAX_BYTES", str(cap))
    _serve(http_requests, {"/start": _page()})
    with pytest.raises(ValidationError) as refused:
        ingest_url(kb_conn, _URL)
    assert str(refused.value) == (
        f"Failed to fetch {_URL}: the response is over the {cap}-byte cap (KNOWLEDGE_BASE_FETCH_MAX_BYTES)"
    )
    assert _chunks(kb_conn) == 0


def test_an_endless_body_is_not_read_past_the_cap(kb_conn, http_requests, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_BASE_FETCH_MAX_BYTES", "4096")
    body = _Endless(1024)
    http_requests.handlers["page.example.test"] = lambda request: httpx.Response(200, content=body)
    with pytest.raises(ValidationError, match="4096-byte cap"):
        ingest_url(kb_conn, _URL)
    assert body.drawn <= 4096 // 1024 + 1


def test_redirects_up_to_the_limit_are_followed(kb_conn, http_requests, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_BASE_FETCH_MAX_REDIRECTS", "2")
    _serve(http_requests, _chain(2))
    assert ingest_url(kb_conn, _URL)["chunks_added"] >= 1
    assert len(http_requests) == 3


def test_a_redirect_past_the_limit_is_refused_and_not_sent(kb_conn, http_requests, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_BASE_FETCH_MAX_REDIRECTS", "2")
    _serve(http_requests, _chain(3))
    with pytest.raises(ValidationError) as refused:
        ingest_url(kb_conn, _URL)
    assert str(refused.value) == f"Failed to fetch {_URL}: more than 2 redirects (KNOWLEDGE_BASE_FETCH_MAX_REDIRECTS)"
    assert len(http_requests) == 3
    assert _chunks(kb_conn) == 0


def test_no_redirect_is_followed_at_zero(kb_conn, http_requests, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_BASE_FETCH_MAX_REDIRECTS", "0")
    _serve(http_requests, _chain(1))
    with pytest.raises(ValidationError, match="more than 0 redirects"):
        ingest_url(kb_conn, _URL)
    assert len(http_requests) == 1


def test_the_default_follows_five_redirects_and_refuses_a_sixth(kb_conn, http_requests):
    _serve(http_requests, _chain(5))
    assert ingest_url(kb_conn, _URL)["chunks_added"] >= 1
    assert len(http_requests) == 6

    http_requests.clear()
    _serve(http_requests, _chain(6))
    with pytest.raises(ValidationError, match="more than 5 redirects"):
        ingest_url(kb_conn, _URL)
    assert len(http_requests) == 6


def test_a_redirect_body_is_never_read(kb_conn, http_requests):
    body = _Endless()
    _serve(
        http_requests,
        {
            "/start": lambda: httpx.Response(302, headers={"location": "/article"}, content=body),
            "/article": _page(),
        },
    )
    assert ingest_url(kb_conn, _URL)["chunks_added"] >= 1
    assert body.drawn == 0


def test_a_slow_drip_stops_at_the_deadline(kb_conn, http_requests, clock):
    def _tick() -> None:
        clock.now += 7

    body = _Endless(10, _tick)
    http_requests.handlers["page.example.test"] = lambda request: httpx.Response(200, content=body)
    with pytest.raises(ValidationError) as refused:
        ingest_url(kb_conn, _URL)
    assert str(refused.value) == (
        f"Failed to fetch {_URL}: not done within the 60 s deadline (KNOWLEDGE_BASE_FETCH_DEADLINE)"
    )
    assert body.drawn == 9  # 63 s: the first chunk past 60 s
    assert _chunks(kb_conn) == 0


def test_an_empty_answer_after_the_deadline_is_refused(kb_conn, http_requests, clock):
    def _late(request: httpx.Request) -> httpx.Response:
        clock.now += 61
        return httpx.Response(200, content=b"")

    http_requests.handlers["page.example.test"] = _late
    with pytest.raises(ValidationError, match="60 s deadline"):
        ingest_url(kb_conn, _URL)


def test_every_hop_asks_for_an_uncompressed_body(kb_conn, http_requests):
    _serve(http_requests, _chain(2))
    ingest_url(kb_conn, _URL)
    assert [request.headers["accept-encoding"] for request in http_requests] == ["identity"] * 3


def test_a_gzip_body_is_decoded_and_its_decoded_size_counts(kb_conn, http_requests, monkeypatch):
    _serve(http_requests, {"/start": _page(gzip.compress(_ARTICLE), headers={"content-encoding": "gzip"})})
    assert ingest_url(kb_conn, _URL)["chunks_added"] >= 1

    monkeypatch.setenv("KNOWLEDGE_BASE_FETCH_MAX_BYTES", "4096")
    bomb = gzip.compress(b" " * 100_000)
    assert len(bomb) < 4096
    _serve(http_requests, {"/start": _page(bomb, headers={"content-encoding": "gzip"})})
    with pytest.raises(ValidationError, match="4096-byte cap"):
        ingest_url(kb_conn, _URL)


def test_an_identity_encoding_is_read_as_is(kb_conn, http_requests):
    _serve(http_requests, {"/start": _page(headers={"content-encoding": "identity"})})
    assert ingest_url(kb_conn, _URL)["chunks_added"] >= 1


@pytest.mark.parametrize("encoding", ["gzip, gzip", "gzip, identity", "identity, gzip", "br", "x-gzip"])
def test_a_stacked_or_unknown_encoding_is_refused(kb_conn, http_requests, encoding):
    _serve(http_requests, {"/start": _page(headers={"content-encoding": encoding})})
    with pytest.raises(ValidationError) as refused:
        ingest_url(kb_conn, _URL)
    assert str(refused.value) == f"Failed to fetch {_URL}: unsupported Content-Encoding {encoding!r}"


def test_an_http_error_keeps_its_message(kb_conn, http_requests):
    http_requests.handlers["page.example.test"] = lambda request: httpx.Response(404)
    with pytest.raises(ValidationError, match=rf"^Failed to fetch {_URL}: Client error '404 Not Found'"):
        ingest_url(kb_conn, _URL)


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (200, 200), color=(128, 128, 128)).save(buf, format="PNG")
    return buf.getvalue()


def test_images_are_downloaded_under_the_same_limits(kb_conn, http_requests, monkeypatch, clock, caplog):
    png = _png()
    monkeypatch.setenv("KNOWLEDGE_BASE_FETCH_MAX_BYTES", str(len(png)))
    monkeypatch.setenv("KNOWLEDGE_BASE_FETCH_MAX_REDIRECTS", "1")

    def _tick() -> None:
        clock.now += 7

    _serve(
        http_requests,
        {
            "/ok.png": lambda: httpx.Response(200, content=iter([png])),
            "/big.png": lambda: httpx.Response(200, content=iter([png + b"x"])),
            "/far.png": _redirect("/r1"),
            "/r1": _redirect("/ok.png"),
            "/drip.png": lambda: httpx.Response(200, content=_Endless(10, _tick)),
        },
        host="img.example.test",
    )
    names = ("ok", "big", "far", "drip")
    html = (
        "<html><body>"
        + "".join(
            f'<img src="https://img.example.test/{name}.png" width="800" height="600" alt="Chart {name}">'
            for name in names
        )
        + "</body></html>"
    )
    figure = [{"description": "A chart.", "figure_type": "chart", "title": "Chart"}]

    with (
        patch("knowledge_base.vision._get_vision_config", return_value={"base_url": "x", "model": "m"}),
        patch("knowledge_base.vision._vision_call", return_value=figure) as vision_call,
        caplog.at_level(logging.WARNING, logger="knowledge_base.web"),
    ):
        added = _extract_html_images(kb_conn, html, source_url="https://page.example.test/a")

    assert added == 1
    assert vision_call.call_count == 1
    row = kb_conn.execute("SELECT metadata FROM chunks WHERE source_type = 'figure'").fetchone()
    assert json.loads(row["metadata"])["image_url"] == "https://img.example.test/ok.png"
    warned = " ".join(record.getMessage() for record in caplog.records)
    for name in ("big", "far", "drip"):
        assert f"https://img.example.test/{name}.png" in warned


def test_a_body_in_tiny_chunks_costs_about_its_own_size(http_requests):
    import tracemalloc

    from knowledge_base.utils import egress_client
    from knowledge_base.web import _fetch

    size = 1024 * 1024
    http_requests.handlers["page.example.test"] = lambda request: httpx.Response(
        200, content=(b"xx" for _ in range(size // 2))
    )
    with egress_client(floor=True) as client:
        tracemalloc.start()
        try:
            body = _fetch(client, _URL).body
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
    assert len(body) == size
    assert peak < 4 * size  # one list item per 2-byte chunk would cost about 25 times the body


def test_the_redirect_limit_holds_for_a_client_that_follows_redirects(http_requests, monkeypatch):
    from knowledge_base.web import _fetch

    monkeypatch.setenv("KNOWLEDGE_BASE_FETCH_MAX_REDIRECTS", "0")
    _serve(http_requests, _chain(1))
    with httpx.Client(follow_redirects=True) as client, pytest.raises(ValidationError, match="more than 0 redirects"):
        _fetch(client, _URL)
    assert len(http_requests) == 1


@pytest.mark.parametrize("charset", ["base64", "rot13", "idna", "undefined", "no-such-charset"])
def test_a_charset_that_is_no_text_encoding_reads_as_utf8(kb_conn, http_requests, charset):
    _serve(http_requests, {"/start": _page(headers={"content-type": f"text/html; charset={charset}"})})
    assert ingest_url(kb_conn, _URL)["chunks_added"] >= 1


@pytest.mark.parametrize("url", ["https://page.example.test:abc/", "https://page.example.test/\x00", "http://[::1/"])
def test_an_invalid_url_is_a_validation_error(kb_conn, http_requests, url):
    with pytest.raises(ValidationError):
        ingest_url(kb_conn, url)
    assert http_requests == []


@pytest.mark.parametrize("charset", ["punycode", "idna"])
def test_a_hostname_codec_charset_reads_the_page_as_utf8(kb_conn, http_requests, charset):
    # punycode decodes a run of digits in quadratic time, after the fetch and outside its deadline
    _serve(http_requests, {"/start": _page(headers={"content-type": f"text/html; charset={charset}"})})
    assert ingest_url(kb_conn, _URL)["chunks_added"] >= 1
