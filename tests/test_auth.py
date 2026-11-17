"""Machine tokens (ADR-0020 §4): the token-hash file and the Bearer check."""

from __future__ import annotations

import hashlib

import pytest

from knowledge_base.auth import TokenTable
from knowledge_base.exceptions import ValidationError
from tests.conftest import machine_token, token_line

ADR_VECTOR = "f7ca4ebc8eb419b37dea09411716d9aa5506b9e5572f1a5e8b3170e815ee13aa"


def _bearer(token: str) -> bytes:
    return f"Bearer {token}".encode()


def _line_error(data: str) -> str:
    with pytest.raises(ValidationError) as info:
        TokenTable.parse(data.encode())
    return str(info.value)


def test_the_adr_test_vector():
    token = "me_" + "0" * 32
    assert hashlib.sha256(token.encode()).hexdigest() == ADR_VECTOR
    table = TokenTable.parse(f"laptop {ADR_VECTOR}\n".encode())
    assert table.authenticate(_bearer(token)) == "laptop"


def test_each_machine_is_named_by_its_own_token():
    table = TokenTable.parse((token_line("laptop") + token_line("desktop")).encode())
    assert table.authenticate(_bearer(machine_token("laptop"))) == "laptop"
    assert table.authenticate(_bearer(machine_token("desktop"))) == "desktop"
    assert table.authenticate(_bearer(machine_token("stranger"))) is None


def test_an_empty_file_admits_no_one():
    assert TokenTable.parse(b"").authenticate(_bearer(machine_token("laptop"))) is None


def test_a_missing_file_names_its_path(tmp_path):
    missing = tmp_path / "absent-token-hashes"
    with pytest.raises(ValidationError) as info:
        TokenTable.load(missing)
    assert str(missing) in str(info.value)


def test_a_line_error_from_a_file_names_the_path_and_the_line(tmp_path):
    path = tmp_path / "token-hashes"
    path.write_text(token_line("laptop") + token_line("Desktop"))
    with pytest.raises(ValidationError) as info:
        TokenTable.load(path)
    assert str(path) in str(info.value)
    assert "line 2" in str(info.value)


_HASH = hashlib.sha256(machine_token("desktop").encode()).hexdigest()


@pytest.mark.parametrize(
    "bad",
    [
        f"desktop {_HASH.upper()}\n",
        f"Desktop {_HASH}\n",
        f"desktop  {_HASH}\n",
        f"desktop {_HASH}\r\n",
        f"desktop {_HASH}",
        f"laptop {_HASH}\n",
        token_line("desktop", machine_token("laptop")),
        "\n",
        f"# desktop {_HASH}\n",
        f"desktop\t{_HASH}\n",
        f"-desktop {_HASH}\n",
        f"desktop- {_HASH}\n",
        f"{'a' * 64} {_HASH}\n",
        f"desktop {_HASH[:63]}\n",
        f"desktop {_HASH}0\n",
        f"desktop {_HASH} \n",
        f"desktopé {_HASH}\n",
    ],
    ids=[
        "uppercase-hex",
        "uppercase-machine",
        "two-spaces",
        "cr-before-lf",
        "no-final-lf",
        "repeated-machine",
        "repeated-hash",
        "blank-line",
        "comment",
        "tab",
        "leading-dash",
        "trailing-dash",
        "64-char-label",
        "63-digit-hash",
        "65-digit-hash",
        "trailing-space",
        "non-ascii",
    ],
)
def test_a_malformed_line_is_refused_with_its_number(bad):
    assert "line 2" in _line_error(token_line("laptop") + bad)


def test_a_63_character_label_is_accepted():
    TokenTable.parse(token_line("a" * 63).encode())


def test_a_pasted_token_is_refused_without_being_quoted():
    token = machine_token("secret")
    message = _line_error(f"laptop {token}\n")
    assert token not in message


@pytest.mark.parametrize(
    "value",
    [
        "bearer {t}",
        "Basic {t}",
        "Bearer  {t}",
        "Bearer {t} ",
        "Bearer {short}",
        "Bearer {t}L",
        "Bearer ME_{tail}",
        "Bearer me_{thirty}é",
        "Bearer me_{thirtyone}-",
        "{t}",
        "",
    ],
)
def test_an_off_shape_value_is_refused(value):
    token = machine_token("laptop")
    table = TokenTable.parse(token_line("laptop").encode())
    presented = value.format(t=token, short=token[:34], tail=token[3:], thirty="L" * 30, thirtyone="L" * 31)
    assert table.authenticate(presented.encode()) is None
    assert table.authenticate(_bearer(token)) == "laptop"


def test_an_off_shape_value_is_never_hashed():
    off_shape = "me_" + "0" * 31
    table = TokenTable.parse(token_line("ghost", off_shape).encode())
    assert table.authenticate(_bearer(off_shape)) is None


# --- the Bearer gate ---


class _Recorder:
    """An inner ASGI app that records the scope it was called with and answers 204."""

    def __init__(self) -> None:
        self.scopes: list[dict] = []

    async def __call__(self, scope, receive, send):
        self.scopes.append(scope)
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})


def _call_gate(headers: list[tuple[bytes, bytes]], *, scope_type: str = "http") -> tuple[_Recorder, list[dict], dict]:
    import asyncio

    from knowledge_base.auth import RequireMachine

    inner = _Recorder()
    gate = RequireMachine(inner, TokenTable.parse((token_line("laptop") + token_line("desktop")).encode()))
    scope = {"type": scope_type, "method": "POST", "path": "/mcp", "headers": headers, "state": {"kept": 1}}
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(gate(scope, receive, send))
    return inner, sent, scope


def _auth(machine: str) -> tuple[bytes, bytes]:
    return (b"authorization", f"Bearer {machine_token(machine)}".encode())


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"authorization", b"Bearer not-a-token")],
        [_auth("laptop"), _auth("laptop")],
        [_auth("laptop"), _auth("desktop")],
        [(b"authorization", "Bearer me_é".encode())],
    ],
    ids=["none", "off-shape", "twice", "two-machines", "non-ascii"],
)
def test_the_gate_refuses_without_exactly_one_valid_token(headers):
    inner, sent, _ = _call_gate(headers)
    assert inner.scopes == []
    start, body = sent
    assert start["status"] == 401
    assert (b"www-authenticate", b"Bearer") in start["headers"]
    assert (b"connection", b"close") in start["headers"]
    assert (b"content-length", b"0") in start["headers"]
    assert body == {"type": "http.response.body", "body": b""}


def test_the_gate_admits_a_valid_token_and_hands_on_the_machine_without_the_header():
    headers = [(b"accept", b"application/json"), _auth("laptop"), (b"content-type", b"application/json")]
    inner, sent, scope = _call_gate(headers)
    (seen,) = inner.scopes
    assert seen["headers"] == [(b"accept", b"application/json"), (b"content-type", b"application/json")]
    assert seen["state"] == {"kept": 1, "machine": "laptop"}
    assert scope["headers"] == headers
    assert scope["state"] == {"kept": 1}
    assert sent[0]["status"] == 204


def test_the_gate_forwards_a_lifespan_scope_untouched():
    inner, sent, scope = _call_gate([], scope_type="lifespan")
    assert inner.scopes == [scope]
    assert sent == []


# --- the log formatter ---


def _format(msg: str, *args, exc: BaseException | None = None) -> str:
    import logging

    from knowledge_base.auth import RedactingFormatter

    record = logging.LogRecord("lib", logging.WARNING, __file__, 1, msg, args, None)
    if exc is not None:
        try:
            raise exc
        except BaseException:
            import sys

            record.exc_info = sys.exc_info()
    return RedactingFormatter().format(record)


def test_the_formatter_redacts_every_token_shape():
    token = machine_token("laptop")
    assert token not in _format(f"name {token}")
    assert token not in _format("name %s", token)
    traced = _format("failed", exc=ValueError(f"input_value='{token}'"))
    assert token not in traced
    assert traced.count("me_[redacted]") == 1
    assert _format("ok %s", "me_[redacted]") == "WARNING lib: ok me_[redacted]"


def test_the_formatter_keeps_one_record_per_line():
    out = _format("a\nINFO knowledge_base.auth: forged\rb\x00c\x1bd\x7fe\tf")
    first, second = out.split("\n")
    assert first == "WARNING lib: a"
    assert second == "  INFO knowledge_base.auth: forged\\x0db\\x00c\\x1bd\\x7fe\tf"


def test_the_formatter_breaks_long_lines_after_redacting():
    token = machine_token("laptop")
    out = _format("x" * 1980 + token + "y" * 2500)
    lines = out.split("\n")
    # Broken first, the token would be split, escape the shape and survive in the rejoined text.
    rejoined = out.replace("\n  ", "")
    assert token not in rejoined and "me_[redacted]" in rejoined
    assert all(len(line) <= 2002 for line in lines)
    assert all(line.startswith("  ") for line in lines[1:])
    assert len(lines) == 3
