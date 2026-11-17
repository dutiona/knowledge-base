"""Machine tokens (memory-engine ADR-0020 §4): the token-hash file and the Bearer check.

The file holds one ``<machine> <sha256-hex>`` line per machine. A request names its machine only
through the token it presents, so a machine name only ever comes out of ``TokenTable.authenticate``.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

from .exceptions import ValidationError

__all__ = [
    "TOKEN_SHAPE",
    "CallingMachine",
    "RedactingFormatter",
    "RequireMachine",
    "TokenTable",
    "calling_machine",
    "log_to_stderr",
]

logger = logging.getLogger(__name__)

#: ``me_`` followed by 32 base62 characters.
TOKEN_SHAPE = "me_[0-9A-Za-z]{32}"  # noqa: S105 -- a pattern, not a secret
_TOKEN = re.compile(TOKEN_SHAPE.encode())
_LINE = re.compile(rb"([^ ]*) ([^ ]*)")
_MACHINE = re.compile(rb"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_HASH = re.compile(rb"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class TokenTable:
    """The parsed token-hash file: the SHA-256 digest of each machine's token."""

    entries: tuple[tuple[bytes, str], ...]

    @classmethod
    def parse(cls, data: bytes) -> TokenTable:
        """Parse the file's bytes. A message names the line but never quotes it: it may hold a pasted token."""
        entries: list[tuple[bytes, str]] = []
        *lines, rest = data.split(b"\n")
        if rest:
            lines.append(rest)
        for number, line in enumerate(lines, start=1):
            reason = None
            fields = _LINE.fullmatch(line)
            if number == len(lines) and rest:
                reason = "no line feed at its end"
            elif fields is None:
                reason = "not two fields separated by one space"
            elif not _MACHINE.fullmatch(fields[1]):
                reason = "the machine is not a lowercase DNS label"
            elif not _HASH.fullmatch(fields[2]):
                reason = "the hash is not 64 lowercase hex digits"
            else:
                digest, machine = bytes.fromhex(fields[2].decode()), fields[1].decode()
                if any(m == machine for _, m in entries):
                    reason = "a repeated machine"
                elif any(d == digest for d, _ in entries):
                    reason = "a repeated hash"
                else:
                    entries.append((digest, machine))
            if reason:
                raise ValidationError(f"token-hash file line {number}: {reason}")
        return cls(tuple(entries))

    @classmethod
    def load(cls, path: Path) -> TokenTable:
        """Read and parse the file; a missing, unreadable or malformed file is a configuration error."""
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ValidationError(f"cannot read the token-hash file {path}: {exc.strerror}") from exc
        try:
            return cls.parse(data)
        except ValidationError as exc:
            raise ValidationError(f"{path}: {exc}") from exc

    def authenticate(self, authorization: bytes) -> str | None:
        """The machine whose token an ``Authorization`` value presents, if any.

        A value off the token shape is refused before it is hashed; otherwise every entry is compared
        in constant time, with no early exit.
        """
        token = authorization.removeprefix(b"Bearer ")
        if token == authorization or not _TOKEN.fullmatch(token):
            return None
        digest = hashlib.sha256(token).digest()
        found = None
        for known, machine in self.entries:
            if hmac.compare_digest(known, digest):
                found = machine
        return found


_REFUSAL_HEADERS = [(b"www-authenticate", b"Bearer"), (b"content-length", b"0"), (b"connection", b"close")]


class RequireMachine:
    """The ASGI application uvicorn serves: refuses every request without exactly one valid machine token.

    It wraps the whole FastMCP app, so it covers every route FastMCP has or adds, Starlette's 404 and 405
    answers, and FastMCP's own middleware. An admitted request reaches the app without its ``Authorization``
    header, with its machine in ``scope["state"]["machine"]``.
    """

    def __init__(self, app: Any, tokens: TokenTable) -> None:
        self.app = app
        self.tokens = tokens

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        values = [value for name, value in scope["headers"] if name == b"authorization"]
        machine = self.tokens.authenticate(values[0]) if len(values) == 1 else None
        if machine is None:
            if scope["type"] == "http":
                await send({"type": "http.response.start", "status": 401, "headers": _REFUSAL_HEADERS})
                await send({"type": "http.response.body", "body": b""})
            else:
                await send({"type": "websocket.close"})
            return
        inner = dict(scope)
        inner["headers"] = [(name, value) for name, value in scope["headers"] if name != b"authorization"]
        inner["state"] = {**scope.get("state", {}), "machine": machine}
        await self.app(inner, receive, send)


def calling_machine() -> str | None:
    """The machine ``RequireMachine`` put on this call's HTTP request; ``None`` without one (stdio)."""
    try:
        request = get_http_request()
    except RuntimeError:
        return None
    return getattr(request.state, "machine", None)


class CallingMachine(Middleware):
    """Log the machine of every tool call over HTTP; refuse a call that reached the hub over HTTP without one.

    The refusal makes serving the hub any other way than through ``RequireMachine`` (``fastmcp run`` over
    HTTP, a refactor that drops the gate) run no tool. A call with no HTTP request behind it (stdio,
    in-memory) runs unlogged.
    """

    async def on_call_tool(self, context: MiddlewareContext, call_next: CallNext) -> Any:
        try:
            request = get_http_request()
        except RuntimeError:
            return await call_next(context)
        machine = getattr(request.state, "machine", None)
        if machine is None:
            raise ToolError("refused: this call reached KB over HTTP without a machine token")
        logger.info("machine %s called %r", machine, context.message.name)
        return await call_next(context)


_TOKEN_TEXT = re.compile(TOKEN_SHAPE)
# Every C0 control but tab and LF, and DEL: journald also ends a record at NUL.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
# journald forces a record boundary after LineMax (48K); escaped text keeps a piece at most 8,000 bytes.
_LINE_MAX = 2000


class RedactingFormatter(logging.Formatter):
    """One record, one line at column 0, with no token in it: whatever library logged it, traceback included.

    Client data reaches the log through code KB does not own (the MCP SDK logs an unknown tool's raw
    name; FastMCP logs argument values in validation tracebacks). Every token shape is redacted (more
    widely than ADR-0020's boundary rule: in a log, over-redaction is the safe side), controls are
    escaped, long lines are broken, and every continuation line is indented, so no client text can
    start what looks like a record.
    """

    def __init__(self) -> None:
        super().__init__("%(levelname)s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        text = _TOKEN_TEXT.sub("me_[redacted]", super().format(record))
        text = _CONTROL.sub(lambda m: f"\\x{ord(m[0]):02x}", text)
        pieces = [line[i : i + _LINE_MAX] for line in text.split("\n") for i in range(0, max(len(line), 1), _LINE_MAX)]
        return "\n  ".join(pieces)


def log_to_stderr() -> None:
    """HTTP mode's logging: one redacting stderr handler on the root, FastMCP's records routed through it."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(RedactingFormatter())
    logging.basicConfig(handlers=[handler], force=True)
    logging.getLogger(__name__).setLevel(logging.INFO)
    fastmcp_logger = logging.getLogger("fastmcp")
    for existing in fastmcp_logger.handlers[:]:
        fastmcp_logger.removeHandler(existing)
    fastmcp_logger.propagate = True
