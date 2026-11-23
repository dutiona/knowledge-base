"""URL ingest and image downloads floor and pin every redirect hop (#572).

The network is faked at the transport (``http_requests``), names at the resolver (``fake_dns``), and the
documentation ranges play the public internet (``public_net``). ``203.0.113.7`` and ``2001:db8::7`` stand for
the host's own public addresses.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from unittest.mock import patch

import httpx
import pytest

from knowledge_base.exceptions import ValidationError
from knowledge_base.web import _extract_html_images, ingest_url
from tests.conftest import fake_embedding_provider

_ARTICLE = (
    "<html><head><title>Pinned</title></head><body><article><h1>Attention</h1><p>"
    + "The dominant sequence transduction models are based on recurrent networks. " * 12
    + "</p></article></body></html>"
)


@pytest.fixture(autouse=True)
def _world(monkeypatch, fake_dns, public_net) -> Iterator[None]:
    monkeypatch.setenv("KNOWLEDGE_BASE_HOST_ADDRESSES", "203.0.113.7,2001:db8::7")
    monkeypatch.delenv("KNOWLEDGE_BASE_BROWSER")
    fake_dns.update(
        {
            "page.example.test": ["203.0.113.10"],
            "img.example.test": ["203.0.113.11"],
            "cdn.example.test": ["203.0.113.20"],
        }
    )
    with fake_embedding_provider():
        yield


def _routes(paths: dict[str, Callable[[], httpx.Response]]) -> Callable[[httpx.Request], httpx.Response]:
    def _handle(request: httpx.Request) -> httpx.Response:
        return paths[request.url.path]()

    return _handle


def _redirect(location: str, status: int = 302) -> Callable[[], httpx.Response]:
    return lambda: httpx.Response(status, headers={"location": location})


def _article() -> httpx.Response:
    return httpx.Response(200, text=_ARTICLE, headers={"content-type": "text/html"})


def _chunks(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]


# (id, redirect target, extra DNS answers)
_REFUSED = [
    ("tailnet-name", "https://tailnet.example.test/", {"tailnet.example.test": ["100.64.0.7", "fd7a:115c:a1e0::7"]}),
    ("tailnet-literal", "http://100.64.0.7/", {}),
    ("loopback-literal", "http://127.0.0.1:8098/", {}),
    ("loopback-v6", "http://[::1]/", {}),
    ("localhost", "http://localhost:8098/", {"localhost": ["127.0.0.1", "::1"]}),
    ("containers-pasta", "http://host.containers.internal:8095/", {"host.containers.internal": ["169.254.1.2"]}),
    ("containers-slirp", "http://host.containers.internal:8095/", {"host.containers.internal": ["10.0.2.2"]}),
    ("containers-host", "http://host.containers.internal:8095/", {"host.containers.internal": ["203.0.113.7"]}),
    ("host-literal", "https://203.0.113.7/", {}),
    ("host-mapped", "https://[::ffff:203.0.113.7]/", {}),
    ("host-v6-name", "https://self.example.test/", {"self.example.test": ["2001:db8::7"]}),
]


@pytest.mark.parametrize(("target", "answers"), [(t, a) for _i, t, a in _REFUSED], ids=[i for i, _t, _a in _REFUSED])
def test_a_page_redirect_into_a_refused_address_is_never_sent(
    kb_conn, http_requests, fake_dns, monkeypatch, target, answers
):
    monkeypatch.delenv("KNOWLEDGE_BASE_VISION")
    fake_dns.update(answers)
    http_requests.handlers["page.example.test"] = _routes({"/start": _redirect(target)})

    with pytest.raises(ValidationError, match="private"):
        ingest_url(kb_conn, "https://page.example.test/start")

    assert http_requests.targets() == ["203.0.113.10"]
    assert _chunks(kb_conn) == 0


@pytest.mark.parametrize(("target", "answers"), [(t, a) for _i, t, a in _REFUSED], ids=[i for i, _t, _a in _REFUSED])
def test_an_image_redirect_into_a_refused_address_is_never_sent(kb_conn, http_requests, fake_dns, target, answers):
    fake_dns.update(answers)
    http_requests.handlers["img.example.test"] = _routes({"/fig.png": _redirect(target)})
    html = '<html><body><img src="https://img.example.test/fig.png" width="800" height="600" alt="Chart"></body></html>'

    with (
        patch("knowledge_base.vision._get_vision_config", return_value={"base_url": "x", "model": "m"}),
        patch("knowledge_base.vision._vision_call") as vision_call,
    ):
        added = _extract_html_images(kb_conn, html, source_url="https://page.example.test/a")

    assert added == 0
    vision_call.assert_not_called()
    assert http_requests.targets() == ["203.0.113.11"]


def test_a_host_whose_second_resolution_is_tailnet_is_refused(kb_conn, http_requests, fake_dns, monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_BASE_VISION")
    fake_dns["rebind.example.test"] = (["203.0.113.13"], ["100.64.0.7"])
    http_requests.handlers["rebind.example.test"] = _routes({"/a": _redirect("/b", 301), "/b": _article})

    with pytest.raises(ValidationError, match="private"):
        ingest_url(kb_conn, "https://rebind.example.test/a")

    assert http_requests.targets() == ["203.0.113.13"]
    assert fake_dns.lookups.count("rebind.example.test") == 2


def test_a_hop_connects_to_the_address_it_validated(kb_conn, http_requests, fake_dns, monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_BASE_VISION")
    http_requests.handlers["page.example.test"] = _routes({"/article": _article})

    result = ingest_url(kb_conn, "https://page.example.test/article")

    assert result["chunks_added"] >= 1
    assert fake_dns.lookups == ["page.example.test"]
    assert http_requests.targets() == ["203.0.113.10"]
    assert http_requests[0].extensions["sni_hostname"] == "page.example.test"


def test_public_redirects_are_followed(kb_conn, http_requests, monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_BASE_VISION")
    http_requests.handlers["page.example.test"] = _routes(
        {"/start": _redirect("https://page.example.test/moved", 301), "/moved": _redirect("https://cdn.example.test/a")}
    )
    http_requests.handlers["cdn.example.test"] = _routes({"/a": _article})

    result = ingest_url(kb_conn, "http://page.example.test/start")

    assert result["chunks_added"] >= 1
    assert http_requests.targets() == ["203.0.113.10", "203.0.113.10", "203.0.113.20"]
    assert http_requests.urls() == [
        "http://page.example.test/start",
        "https://page.example.test/moved",
        "https://cdn.example.test/a",
    ]


def test_a_redirect_to_a_non_http_scheme_is_refused(kb_conn, http_requests, monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_BASE_VISION")
    http_requests.handlers["page.example.test"] = _routes({"/start": _redirect("file:///etc/passwd")})

    with pytest.raises(ValidationError, match="scheme"):
        ingest_url(kb_conn, "https://page.example.test/start")

    assert len(http_requests) == 1
