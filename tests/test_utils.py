"""Tests for shared utility functions (utils.py)."""

import http.server
import ipaddress
import socket
import ssl
import threading
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import trustme

from knowledge_base import utils
from knowledge_base.exceptions import ValidationError
from knowledge_base.utils import egress_client, is_private_ip, validate_base_url


# --- is_private_ip ---
# Existing tests in test_ingest.py cover the core cases;
# these tests verify the function at its canonical location.


def test_is_private_ip_loopback():
    assert is_private_ip("127.0.0.1") is True


def test_is_private_ip_cloud_metadata():
    assert is_private_ip("169.254.169.254") is True


def test_is_private_ip_localhost():
    assert is_private_ip("localhost") is True


# --- validate_base_url ---


def test_validate_base_url_rejects_bad_scheme():
    with pytest.raises(ValidationError, match="scheme"):
        validate_base_url("ftp://example.com")
    with pytest.raises(ValidationError, match="scheme"):
        validate_base_url("file:///etc/passwd")


def test_validate_base_url_rejects_missing_hostname():
    with pytest.raises(ValidationError, match="hostname"):
        validate_base_url("http://")


def test_validate_base_url_rejects_private_ip():
    with pytest.raises(ValidationError, match="private"):
        validate_base_url("http://127.0.0.1:1234")
    with pytest.raises(ValidationError, match="private"):
        validate_base_url("http://169.254.169.254")
    with pytest.raises(ValidationError, match="private"):
        validate_base_url("http://10.0.0.1:8080")


@patch("knowledge_base.utils.is_private_ip", return_value=False)
def test_validate_base_url_allows_public(_mock):
    validate_base_url("http://api.example.com:1234")  # should not raise
    validate_base_url("https://llm.example.com")  # should not raise


# --- validate_base_url loopback opt-in (ADR-0018 §4) ---


def test_validate_base_url_rejects_loopback_by_default():
    """Without the opt-in, loopback is rejected (fail-closed default)."""
    with pytest.raises(ValidationError, match="private"):
        validate_base_url("http://127.0.0.1:11434")
    with pytest.raises(ValidationError, match="private"):
        validate_base_url("http://localhost:11434/v1")


def test_validate_base_url_allows_loopback_ip_literal_with_optin():
    """allow_loopback accepts loopback IP literals across the whole 127.0.0.0/8 + ::1."""
    validate_base_url("http://127.0.0.1:11434", allow_loopback=True)  # no raise
    validate_base_url("http://127.5.6.7:11434", allow_loopback=True)  # whole /8
    validate_base_url("http://[::1]:11434", allow_loopback=True)  # IPv6 loopback


def test_validate_base_url_allows_localhost_label_with_optin():
    """The exact RFC-6761 reserved label 'localhost' is accepted under the opt-in.

    This is the URL ADR-0018 tells operators to use for a local openai_compat server.
    """
    validate_base_url("http://localhost:11434/v1", allow_loopback=True)  # no raise
    validate_base_url("http://LOCALHOST:11434", allow_loopback=True)  # case-insensitive


@patch("knowledge_base.utils.socket.getaddrinfo")
def test_validate_base_url_rejects_rebind_even_with_optin(mock_gai):
    """A hostname that *resolves* to loopback is NOT a loopback literal — stays rejected.

    This is the DNS-rebind vector (127.0.0.1.nip.io); the opt-in must not save it.
    """
    # Simulate the rebind: the hostname resolves to 127.0.0.1.
    mock_gai.return_value = [(2, 1, 6, "", ("127.0.0.1", 0))]
    with pytest.raises(ValidationError, match="private"):
        validate_base_url("http://127.0.0.1.nip.io", allow_loopback=True)


@patch("knowledge_base.utils.socket.getaddrinfo", side_effect=OSError("offline"))
def test_validate_base_url_rejects_localhost_lookalikes_with_optin(_mock_gai):
    """Only the EXACT label 'localhost' is allowed — look-alikes fall through and are rejected."""
    for url in (
        "http://localhost.attacker.com",
        "http://notlocalhost",
        "http://sub.localhost",
        "http://localhostx",
    ):
        with pytest.raises(ValidationError, match="private"):
            validate_base_url(url, allow_loopback=True)


def test_validate_base_url_rejects_nonloopback_floor_even_with_optin():
    """The non-loopback SSRF floor (metadata / link-local / RFC-1918) is ALWAYS enforced."""
    for url in (
        "http://169.254.169.254",  # cloud metadata
        "http://10.0.0.1",  # RFC-1918
        "http://192.168.1.41:1234",  # RFC-1918
        "http://[fd00::1]",  # IPv6 ULA
    ):
        with pytest.raises(ValidationError, match="private"):
            validate_base_url(url, allow_loopback=True)


def test_validate_base_url_scheme_floor_regardless_of_flag():
    """Scheme / hostname checks fire regardless of the loopback flag."""
    with pytest.raises(ValidationError, match="scheme"):
        validate_base_url("ftp://localhost", allow_loopback=True)
    with pytest.raises(ValidationError, match="hostname"):
        validate_base_url("http://", allow_loopback=True)


# --- _resolve_api_key (env:VARNAME indirection, ADR-0018 §4) ---


def test_resolve_api_key_plain_passthrough():
    from knowledge_base.utils import _resolve_api_key

    assert _resolve_api_key("sk-inline-123") == "sk-inline-123"
    assert _resolve_api_key(None) is None


def test_resolve_api_key_env_indirection(monkeypatch):
    from knowledge_base.utils import _resolve_api_key

    monkeypatch.setenv("KB_TEST_KEY", "sk-from-env")
    assert _resolve_api_key("env:KB_TEST_KEY") == "sk-from-env"


def test_resolve_api_key_env_missing_returns_none(monkeypatch):
    from knowledge_base.utils import _resolve_api_key

    monkeypatch.delenv("KB_MISSING_KEY", raising=False)
    assert _resolve_api_key("env:KB_MISSING_KEY") is None


# --- _sanitize_url relocated to utils (re-exported from llm for back-compat) ---


def test_sanitize_url_strips_userinfo_and_query():
    from knowledge_base.utils import _sanitize_url

    out = _sanitize_url("https://user:secret@host.example.com:443/v1?token=abc#frag")
    assert "secret" not in out
    assert "token" not in out
    assert "host.example.com" in out


# --- element_type constants ---


def test_element_type_constants():
    from knowledge_base.utils import (
        ELEMENT_INSERT_EXPR,
        ELEMENT_QUERY_EXPR,
        VALID_ELEMENT_TYPES,
    )

    assert {"float32", "int8"} == VALID_ELEMENT_TYPES
    assert set(ELEMENT_INSERT_EXPR.keys()) == set(ELEMENT_QUERY_EXPR.keys())
    assert ELEMENT_INSERT_EXPR["float32"] == "?"
    assert ELEMENT_QUERY_EXPR["float32"] == "?"


# --- #569: the declared embedding host, and only it, is exempt from the floor ---

_TAILNET = ["100.64.0.10", "fd7a:115c:a1e0::10"]


def test_the_floor_rejects_a_tailnet_name(fake_dns):
    fake_dns["embedder.example.test"] = _TAILNET
    with pytest.raises(ValidationError, match="private"):
        validate_base_url("https://embedder.example.test")


def test_the_exact_declared_host_is_exempt_without_a_lookup(fake_dns):
    validate_base_url("https://EMBEDDER.example.test.:8443/v1", exempt_host="embedder.example.test")


@pytest.mark.parametrize(
    "url",
    [
        "https://embedder.example.test.evil.example",
        "https://sub.embedder.example.test",
        "https://xembedder.example.test",
        "https://embedder.example.test@evil.example",
        "ftp://embedder.example.test",
    ],
)
def test_look_alikes_keep_the_floor(fake_dns, url):
    for host in (
        "embedder.example.test.evil.example",
        "sub.embedder.example.test",
        "xembedder.example.test",
        "evil.example",
    ):
        fake_dns[host] = _TAILNET
    with pytest.raises(ValidationError):
        validate_base_url(url, exempt_host="embedder.example.test")


# --- #572: the floor refuses the host's own addresses ---

_HOST_LIST = "203.0.113.7,2001:db8:7::/64"


@pytest.fixture
def host_list(monkeypatch, public_net):
    monkeypatch.setenv("KNOWLEDGE_BASE_HOST_ADDRESSES", _HOST_LIST)


@pytest.mark.parametrize("host", ["203.0.113.7", "::ffff:203.0.113.7", "2001:db8:7::abcd", "self.example.test"])
def test_host_address_is_refused(fake_dns, host_list, host):
    fake_dns["self.example.test"] = ["203.0.113.10", "203.0.113.7"]
    assert is_private_ip(host) is True
    url_host = f"[{host}]" if ":" in host else host
    with pytest.raises(ValidationError, match="private"):
        validate_base_url(f"https://{url_host}/")


@pytest.mark.parametrize("host", ["203.0.113.8", "2001:db8:8::1", "public.example.test"])
def test_a_public_address_off_the_list_is_accepted(fake_dns, host_list, host):
    # The control: these differ from the listed ones only by the list, so the list is what refuses those.
    fake_dns["public.example.test"] = ["203.0.113.8", "2001:db8:8::1"]
    assert is_private_ip(host) is False


def test_the_floor_without_a_list_refuses_no_public_address(fake_dns, public_net):
    assert is_private_ip("203.0.113.7") is False


def test_a_failed_lookup_is_refused(fake_dns):
    assert is_private_ip("unknown.example.test") is True


# --- #572: a client that floors and pins every request ---


def _ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="ok")


def test_pinned_request_keeps_the_name_and_targets_the_address(http_requests, fake_dns, public_net):
    fake_dns["page.example.test"] = ["203.0.113.10"]
    http_requests.handlers["page.example.test"] = _ok
    with egress_client(floor=True) as client:
        response = client.get("https://page.example.test:8443/a?b=1")
    (sent,) = http_requests
    assert str(sent.url) == "https://203.0.113.10:8443/a?b=1"
    assert sent.headers["host"] == "page.example.test:8443"
    assert sent.extensions["sni_hostname"] == "page.example.test"
    assert str(response.url) == "https://page.example.test:8443/a?b=1"


def test_pinned_ipv6_answer_is_bracketed(http_requests, fake_dns, public_net):
    fake_dns["page.example.test"] = ["2001:db8::10"]
    http_requests.handlers["page.example.test"] = _ok
    with egress_client(floor=True) as client:
        client.get("https://page.example.test:8443/a")
    assert str(http_requests[0].url) == "https://[2001:db8::10]:8443/a"


def test_pinned_mixed_answer_is_refused_before_sending(http_requests, fake_dns, public_net):
    fake_dns["page.example.test"] = ["203.0.113.10", "100.64.0.7"]
    with egress_client(floor=True) as client, pytest.raises(ValidationError, match="private"):
        client.get("https://page.example.test/")
    assert http_requests == []


def test_pinned_falls_back_to_the_next_accepted_address(http_requests, fake_dns, public_net):
    fake_dns["page.example.test"] = ["2001:db8::10", "203.0.113.10"]

    def _v4_only(request: httpx.Request) -> httpx.Response:
        if request.url.host == "2001:db8::10":
            raise httpx.ConnectError("unreachable", request=request)
        return httpx.Response(200)

    http_requests.handlers["page.example.test"] = _v4_only
    with egress_client(floor=True) as client:
        assert client.get("https://page.example.test/").status_code == 200
    assert http_requests.targets() == ["2001:db8::10", "203.0.113.10"]


def test_pinned_lookup_and_sni_use_the_idna_2008_name(http_requests, fake_dns, public_net):
    fake_dns["xn--fa-hia.example.test"] = ["203.0.113.10"]
    http_requests.handlers["xn--fa-hia.example.test"] = _ok
    with egress_client(floor=True) as client:
        client.get("https://faß.example.test/")
    assert fake_dns.lookups == ["xn--fa-hia.example.test"]
    assert http_requests[0].extensions["sni_hostname"] == "xn--fa-hia.example.test"


def test_pinned_loopback_opt_in(http_requests, fake_dns):
    fake_dns["loop.example.test"] = ["127.0.0.1"]
    http_requests.handlers["localhost"] = _ok
    http_requests.handlers["127.0.0.1"] = _ok
    with egress_client(floor=True, allow_loopback=True) as client:
        client.get("http://localhost:8080/")
        client.get("http://127.0.0.1:8080/")
        with pytest.raises(ValidationError, match="private"):
            client.get("http://loop.example.test:8080/")
    assert http_requests.targets() == ["localhost", "127.0.0.1"]
    assert fake_dns.lookups == ["loop.example.test"]
    with egress_client(floor=True) as client, pytest.raises(ValidationError):
        client.get("http://localhost:8080/")


def test_pinned_redirect_to_another_scheme_is_refused(http_requests, fake_dns, public_net):
    fake_dns["page.example.test"] = ["203.0.113.10"]
    http_requests.handlers["page.example.test"] = lambda _r: httpx.Response(
        302, headers={"location": "ftp://files.example.test/x"}
    )
    with egress_client(floor=True) as client, pytest.raises(ValidationError, match="scheme"):
        client.get("https://page.example.test/", follow_redirects=True)
    assert len(http_requests) == 1


def test_unfloored_client_is_plain(http_requests, fake_dns):
    http_requests.handlers["localhost"] = _ok
    with egress_client(floor=False) as client:
        client.get("http://localhost:11434/")
    assert fake_dns.lookups == []
    assert http_requests.targets() == ["localhost"]


@pytest.mark.parametrize("option", ["proxy", "mounts", "transport", "limits", "verify"])
def test_floored_client_takes_no_option_that_bypasses_the_guard(option):
    bypass: Any = {option: None}  # an option egress_client must not accept, whatever its type
    with pytest.raises(TypeError):
        egress_client(floor=True, **bypass)


def test_floored_client_closes_its_inner_transport(monkeypatch):
    closed: list[httpx.HTTPTransport] = []
    monkeypatch.setattr(httpx.HTTPTransport, "close", lambda self: closed.append(self))
    with egress_client(floor=True):
        pass
    assert len(closed) == 1


# Real sockets on loopback: the TLS name and connection reuse sit below the http_requests patch point.


@pytest.fixture
def pinned_to_loopback(monkeypatch):
    monkeypatch.setattr(utils, "_accepted_addresses", lambda _host: [ipaddress.ip_address("127.0.0.1")])


def _tls_server(cert_name: str, seen: list[str | None]):
    ca = trustme.CA()
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ca.issue_cert(cert_name).configure_cert(server_ctx)
    server_ctx.sni_callback = lambda _sock, name, _ctx: seen.append(name)
    listener = socket.create_server(("127.0.0.1", 0))

    def _serve() -> None:
        conn, _ = listener.accept()
        try:
            with server_ctx.wrap_socket(conn, server_side=True) as tls:
                tls.recv(65536)
                tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        except (ssl.SSLError, OSError):
            pass
        finally:
            listener.close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    return ca, listener.getsockname()[1], thread


def _trust(monkeypatch, tmp_path, ca) -> None:
    ca_file = tmp_path / "ca.pem"
    ca.cert_pem.write_to_path(str(ca_file))
    monkeypatch.setenv("SSL_CERT_FILE", str(ca_file))


def test_pinned_tls_verifies_the_name(monkeypatch, tmp_path, pinned_to_loopback):
    seen: list[str | None] = []
    ca, port, thread = _tls_server("a.example.test", seen)
    _trust(monkeypatch, tmp_path, ca)
    with egress_client(floor=True, timeout=5) as client:
        assert client.get(f"https://a.example.test:{port}/").text == "ok"
    thread.join(5)
    assert seen == ["a.example.test"]


def test_pinned_tls_refuses_a_certificate_for_another_name(monkeypatch, tmp_path, pinned_to_loopback):
    seen: list[str | None] = []
    ca, port, thread = _tls_server("b.example.test", seen)
    _trust(monkeypatch, tmp_path, ca)
    with egress_client(floor=True, timeout=5) as client, pytest.raises(httpx.ConnectError, match="certificate"):
        client.get(f"https://a.example.test:{port}/")
    thread.join(5)


def test_pinned_hosts_never_share_a_connection(pinned_to_loopback):
    seen: list[tuple[int, str]] = []

    class _Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            seen.append((self.client_address[1], self.headers["Host"]))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        with egress_client(floor=True, timeout=5) as client:
            client.get(f"http://a.example.test:{port}/")
            client.get(f"http://b.example.test:{port}/")
    finally:
        server.shutdown()
        server.server_close()
    assert [host for _port, host in seen] == [f"a.example.test:{port}", f"b.example.test:{port}"]
    assert seen[0][0] != seen[1][0]


@pytest.mark.parametrize(
    ("url", "host_header", "expected"),
    [
        ("https://203.0.113.10:8443/a", "page.example.test:8443", "page.example.test"),
        ("https://[2001:db8::10]:8443/a", "[2001:db8::10]:8443", "2001:db8::10"),
        ("https://203.0.113.10/a", "page.example.test", "page.example.test"),
        ("https://203.0.113.10/a", "xn--fa-hia.example.test", "xn--fa-hia.example.test"),
    ],
)
def test_the_recorder_keys_a_request_by_its_host_header(url, host_header, expected):
    """The test recorder's dispatch key: the Host authority in ASCII form, without port or brackets."""
    from tests.conftest import logical_host

    assert logical_host(httpx.Request("GET", url, headers={"host": host_header})) == expected
