"""Secret redaction: memory-engine's rule set, ported detector for detector (#592).

The reference is ``crates/memory-engine/src/bootstrap/redact.rs`` at 97c8e77, with Michaël's rulings of
2026-11-17: no email detector (an address is not a secret), the ADR-0020 §4 machine-token shape first, and
links exempt from every rule but url-credential. ``tests/fixtures/redaction-cases.jsonl``, which memory-engine
carries byte for byte, is the contract; one pass of :func:`redact` must reproduce it.

Where Python and Rust differ, the port follows Rust: whitespace is ``\\p{White_Space}`` (``str.isspace`` adds
U+001C..U+001F), alphanumeric is ``\\p{Alphabetic}`` or a numeric category (``str.isalnum`` misses
Other_Alphabetic marks, which changes whether a token reaches its byte minimum), lowercasing is ASCII-only,
and the prefix minimum counts UTF-8 bytes. Indices are code points, so the URL scan steps past a multibyte
terminator where memory-engine panics. The only residual is a character whose Unicode properties differ
between the versions of Rust's standard library and of ``regex``.

memory-engine#1024 added sixteen provider prefixes, some with a body of ASCII alphanumerics only, and made
``SG.`` and ``eyJ`` need a dot past the prefix; ``SECRET_PREFIXES`` carries both.

Each detector stays linear: overlap checks bisect the sorted spans, and the prefix body, its last ``.``, the
end of a ``-----BEGIN`` line and the hex-context word are read from precomputed runs, never rescanned.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any

import regex

from .exceptions import KnowledgeBaseError

__all__ = ["TOKEN_SHAPE", "findings", "link_extents", "redact", "redact_json", "scrub"]

#: ``me_`` followed by 32 base62 characters (ADR-0020 §4).
TOKEN_SHAPE = "me_[0-9A-Za-z]{32}"  # noqa: S105 -- a pattern, not a secret

#: (rule, prefix, body): the body runs from the prefix to the end of a ``token`` run (``_BODY_RUN``) or of a
#: ``base62`` run (ASCII alphanumerics only); the first ten are the original set, the rest memory-engine#1024's.
SECRET_PREFIXES = (
    ("aws-access-key", "AKIA", "token"),
    ("github-pat", "ghp_", "token"),
    ("github-fine-pat", "github_pat_", "token"),
    ("openai-key", "sk-", "token"),
    ("google-api-key", "AIza", "token"),
    ("slack-token", "xoxb-", "token"),
    ("slack-token", "xoxp-", "token"),
    ("private-key-header", "-----BEGIN", "token"),
    ("jwt", "eyJ", "token"),
    ("sendgrid", "SG.", "token"),
    ("huggingface-token", "hf_", "base62"),
    ("tailscale-key", "tskey-", "token"),
    ("gitlab-pat", "glpat-", "token"),
    ("github-token", "gho_", "base62"),
    ("github-token", "ghu_", "base62"),
    ("github-token", "ghs_", "base62"),
    ("github-token", "ghr_", "base62"),
    ("stripe-key", "sk_live_", "base62"),
    ("stripe-key", "rk_live_", "base62"),
    ("npm-token", "npm_", "base62"),
    ("pypi-token", "pypi-", "token"),
    ("age-secret-key", "AGE-SECRET-KEY-1", "token"),
    ("slack-token", "xapp-", "token"),
    ("supabase-token", "sbp_", "base62"),
    ("docker-pat", "dckr_pat_", "token"),
    ("digitalocean-token", "dop_v1_", "base62"),
)
PREFIX_BOUNDARY_CHARS = frozenset("\"'`(){}[]:,;=<>?&/@")
HEX_CONTEXT_KEYS = frozenset(
    {"secret", "token", "key", "apikey", "password", "passwd", "pwd", "auth", "credential", "cred"}
)
HEX_SECRET_MIN_LEN = 32
MIN_PREFIXED_SECRET_LEN = 20
HIGH_ENTROPY_MIN_LEN = 20
HIGH_ENTROPY_BITS = 4.3
_MAX_SCRUB_PASSES = 8
_LONGEST_KEY = max(map(len, HEX_CONTEXT_KEYS))

_ALNUM = r"\p{Alphabetic}\p{Nd}\p{Nl}\p{No}"
_WS = regex.compile(r"\p{White_Space}")
_BODY_RUN = regex.compile(rf"[{_ALNUM}._\-+/=]+")
_BASE62_RUN = regex.compile(r"[0-9A-Za-z]+")
_WORD_RUN = regex.compile(rf"[{_ALNUM}_]+")
_HEX_RUN = regex.compile(r"[0-9A-Fa-f]+")
_AUTHORITY_END = regex.compile(r"[/?#\p{White_Space}]")
_ENTROPY_TOKEN = regex.compile(r"[^\p{White_Space}\"'`{}\[\]():,;=]+")
_SECRET_CHARSET = regex.compile(r"[A-Za-z0-9+/=_\-]+")
# RFC 3986 characters except ' ( ) , ;, which in prose and code end a link more often than they continue one.
_LINK_RUN = regex.compile(r"[A-Za-z0-9\-._~:/?#\[\]@!$&*+=%]*")
_MACHINE_TOKEN = regex.compile(rf"(?<![0-9A-Za-z_]){TOKEN_SHAPE}(?![0-9A-Za-z_])")


class _Spans:
    """Sorted, non-overlapping half-open spans with a logarithmic overlap test.

    A detector adds its spans in increasing order to a pending run, merged in by :meth:`flush`, so no span is
    ever inserted mid-list (that memmove made interleaved detectors quadratic).
    """

    def __init__(self) -> None:
        self.starts: list[int] = []
        self.ends: list[int] = []
        self.rules: list[str] = []
        self._pending: list[tuple[int, int, str]] = []
        self.extents: list[tuple[int, int]] = []  # whole links, userinfo included (only _links fills it)

    def overlaps(self, start: int, end: int) -> bool:
        i = bisect_left(self.starts, end)
        if i > 0 and self.ends[i - 1] > start:
            return True
        return bool(self._pending) and self._pending[-1][1] > start and self._pending[-1][0] < end

    def add(self, start: int, end: int, rule: str) -> None:
        self._pending.append((start, end, rule))

    def flush(self) -> None:
        if self._pending:
            merged = sorted([*zip(self.starts, self.ends, self.rules, strict=True), *self._pending])
            self.starts = [m[0] for m in merged]
            self.ends = [m[1] for m in merged]
            self.rules = [m[2] for m in merged]
            self._pending = []


class _Runs:
    """Maximal runs of one character class, looked up by position."""

    def __init__(self, pattern: regex.Pattern, text: str) -> None:
        self.starts: list[int] = []
        self.ends: list[int] = []
        for m in pattern.finditer(text):
            self.starts.append(m.start())
            self.ends.append(m.end())

    def run_at(self, pos: int) -> tuple[int, int] | None:
        """The run containing ``pos``, if any."""
        i = bisect_right(self.starts, pos) - 1
        if i >= 0 and self.ends[i] > pos:
            return self.starts[i], self.ends[i]
        return None


def _is_ws(c: str) -> bool:
    return _WS.match(c) is not None


def _links(text: str) -> _Spans:
    """``scheme://`` plus link characters: no rule but url-credential redacts inside one.

    The userinfo is no reference, so it is left out and stays subject to every rule.
    """
    links = _Spans()
    pos = 0
    while (sep := text.find("://", pos)) >= 0:
        start = sep
        while start and text[start - 1].isascii() and (text[start - 1].isalnum() or text[start - 1] in "+.-"):
            start -= 1
        while start < sep and not (text[start].isascii() and text[start].isalpha()):
            start += 1
        run = _LINK_RUN.match(text, sep + 3)
        end = run.end() if run else sep + 3
        if start < sep:
            authority_end = next((k for k in range(sep + 3, end) if text[k] in "/?#"), end)
            at = text.rfind("@", sep + 3, authority_end)
            if at >= 0:
                links.add(start, sep + 3, "link")
                links.add(at, end, "link")
            else:
                links.add(start, end, "link")
            links.extents.append((start, end))
        pos = max(end, sep + 3)
    links.flush()
    return links


def _detect_machine_token(text: str, spans: _Spans) -> None:
    """The exact ADR-0020 shape, inside a link too: it is never a published reference."""
    for m in _MACHINE_TOKEN.finditer(text):
        if not spans.overlaps(m.start(), m.end()):
            spans.add(m.start(), m.end(), "machine-token")
    spans.flush()


def _detect_provider_prefix(text: str, spans: _Spans, links: _Spans) -> None:
    bodies = {"token": _Runs(_BODY_RUN, text), "base62": _Runs(_BASE62_RUN, text)}
    dots = [m.start() for m in regex.finditer(r"\.", text)]
    newlines = [m.start() for m in regex.finditer("\n", text)]
    for rule, prefix, body_class in SECRET_PREFIXES:
        pos = 0
        while (start := text.find(prefix, pos)) >= 0:
            pos = start + 1
            if start and not (_is_ws(text[start - 1]) or text[start - 1] in PREFIX_BOUNDARY_CHARS):
                continue
            if prefix == "-----BEGIN":
                j = bisect_left(newlines, start)
                end = newlines[j] if j < len(newlines) else len(text)
                if end > start and text[end - 1] == "\r":
                    end -= 1
                k = bisect_right(links.starts, start)  # a link on the line ends the header's span
                if k < len(links.starts) and links.starts[k] < end:
                    end = links.starts[k]
            else:
                body = start + len(prefix)
                run = bodies[body_class].run_at(body)
                end = run[1] if run else body
                short = (
                    end - start < MIN_PREFIXED_SECRET_LEN and len(text[start:end].encode()) < MIN_PREFIXED_SECRET_LEN
                )
                # A SendGrid key and a JWT are dotted: the dot must lie past the prefix, whose own dot does not count.
                if short or (prefix in ("SG.", "eyJ") and bisect_left(dots, end) == bisect_left(dots, body)):
                    continue
            if not spans.overlaps(start, end) and not links.overlaps(start, end):
                spans.add(start, end, rule)
        spans.flush()  # each prefix's matches come in order; prefixes interleave


def _detect_url_credential(text: str, spans: _Spans) -> list[tuple[int, int]]:
    """Password spans of ``user:pass@`` authorities; links do not block this rule."""
    found: list[tuple[int, int]] = []
    pos = 0
    while pos < len(text):
        sep = text.find("://", pos)
        if sep < 0:
            break
        auth_start = sep + 3
        m = _AUTHORITY_END.search(text, auth_start)
        auth_end = m.start() if m else len(text)
        authority = text[auth_start:auth_end]
        at = authority.rfind("@")
        if at >= 0:
            colon = authority.find(":", 0, at)
            if colon >= 0:
                start, end = auth_start + colon + 1, auth_start + at
                if start < end and not spans.overlaps(start, end):
                    found.append((start, end))
        # Resume inside the authority, not past it: an authority without a path runs to the
        # ``/`` of the next ``://``, whose password the next search must still reach. Each
        # search starts past the last ``://``, so the scan stays linear.
        pos = auth_start
    return found


def _is_key(part: str) -> bool:
    return len(part) <= _LONGEST_KEY and part.isascii() and part.lower() in HEX_CONTEXT_KEYS


class _Words:
    """Word runs (alphanumeric or ``_``) with, per ``_``, whether a complete part of its word before it is a key."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.runs = _Runs(_WORD_RUN, text)
        self.underscores: list[int] = []
        self.key_before: list[bool] = []
        for start, end in zip(self.runs.starts, self.runs.ends, strict=True):
            prev, flag = start, False
            while (u := text.find("_", prev, end)) >= 0:
                flag = flag or _is_key(text[prev:u])
                self.underscores.append(u)
                self.key_before.append(flag)
                prev = u + 1

    def ends_with_key(self, word_start: int, end: int) -> bool:
        """Whether ``text[word_start:end]``, split on ``_``, has a part that is a key."""
        i = bisect_left(self.underscores, end) - 1
        if i >= 0 and self.underscores[i] >= word_start:
            tail = self.underscores[i] + 1
            return self.key_before[i] or (end - tail <= _LONGEST_KEY and _is_key(self.text[tail:end]))
        return end - word_start <= _LONGEST_KEY and _is_key(self.text[word_start:end])


def _detect_hex_in_context(text: str, spans: _Spans, links: _Spans) -> None:
    words = _Words(text)
    newlines = [m.start() for m in regex.finditer("\n", text)]
    for m in _HEX_RUN.finditer(text):
        start, end = m.start(), m.end()
        if end - start < HEX_SECRET_MIN_LEN:
            continue
        i = bisect_left(newlines, start) - 1
        line_start = newlines[i] + 1 if i >= 0 else 0
        if _has_hex_context_key(text, start, line_start, words) and not (
            spans.overlaps(start, end) or links.overlaps(start, end)
        ):
            spans.add(start, end, "hex-secret")
    spans.flush()


def _has_hex_context_key(text: str, hex_start: int, line_start: int, words: _Words) -> bool:
    """The word right before the hex run, on its line, has a ``_``-separated part that is a context key."""
    p = hex_start
    while p > line_start and (_is_ws(text[p - 1]) or text[p - 1] in "=:\"'`"):
        p -= 1
    run = words.runs.run_at(p - 1) if p > line_start else None
    return run is not None and words.ends_with_key(max(run[0], line_start), p)


def _shannon_entropy(token: str) -> float:
    n = len(token)
    return -sum(c / n * math.log2(c / n) for c in Counter(token).values())


def _detect_high_entropy(text: str, spans: _Spans, links: _Spans) -> None:
    for m in _ENTROPY_TOKEN.finditer(text):
        token = m.group()
        if (
            len(token) >= HIGH_ENTROPY_MIN_LEN
            and _SECRET_CHARSET.fullmatch(token)
            and any(c.isdigit() or c in "+/=_-" for c in token)
            and _shannon_entropy(token) >= HIGH_ENTROPY_BITS
            and not spans.overlaps(m.start(), m.end())
            and not links.overlaps(m.start(), m.end())
        ):
            spans.add(m.start(), m.end(), "high-entropy")
    spans.flush()


def _collect(text: str) -> _Spans:
    links = _links(text)
    spans = _Spans()
    _detect_machine_token(text, spans)
    _detect_provider_prefix(text, spans, links)
    for start, end in _detect_url_credential(text, spans):
        spans.add(start, end, "connection-string-password")
    spans.flush()
    _detect_hex_in_context(text, spans, links)
    _detect_high_entropy(text, spans, links)
    return spans


def redact(text: str) -> tuple[str, list[str]]:
    """One pass: ``text`` with each match replaced by ``[REDACTED:<rule>]``, and the rules in text order."""
    spans = _collect(text)
    out: list[str] = []
    cursor = 0
    for start, end, rule in zip(spans.starts, spans.ends, spans.rules, strict=True):
        out.append(text[cursor:start])
        out.append(f"[REDACTED:{rule}]")
        cursor = end
    out.append(text[cursor:])
    return "".join(out), list(spans.rules)


def redact_in_context(text: str, contexts: Iterable[tuple[str, str]]) -> tuple[str, list[str]]:
    """One pass over *text* read in each of its *contexts*, the document text before and after it, and the rules.

    A chunk cut mid-word (KB's chunker did so before #592) can hold a fragment of a secret that no rule matches
    alone, or a secret whose context word is in the text before it; the part of each match inside *text* is
    redacted, and matches that overlap across contexts share one placeholder.
    """
    found: list[tuple[int, int, str]] = []
    for before, after in contexts:
        wide = before + text + after
        lo = len(before)
        spans = _collect(wide)
        for start, end, rule in zip(spans.starts, spans.ends, spans.rules, strict=True):
            s, e = max(start - lo, 0), min(end - lo, len(text))
            if s < e and wide[start:end] != f"[REDACTED:{rule}]":
                found.append((s, e, rule))
    out: list[str] = []
    rules: list[str] = []
    cursor = 0
    for s, e, rule in sorted(found):
        if s < cursor:
            cursor = max(cursor, e)
            continue
        out += [text[cursor:s], f"[REDACTED:{rule}]"]
        rules.append(rule)
        cursor = e
    out.append(text[cursor:])
    return "".join(out), rules


def link_extents(text: str) -> list[tuple[int, int]]:
    """Each link's span, userinfo included, in order: what a chunker must not cut."""
    return _links(text).extents


def findings(text: str) -> list[str]:
    """The rules of one pass's matches, leaving out a match that is already its own placeholder."""
    spans = _collect(text)
    return [
        rule
        for start, end, rule in zip(spans.starts, spans.ends, spans.rules, strict=True)
        if text[start:end] != f"[REDACTED:{rule}]"
    ]


def scrub(text: str) -> str:
    """Redact until a pass changes nothing: one pass can leave a remainder the next one catches."""
    for _ in range(_MAX_SCRUB_PASSES):
        again, _rules = redact(text)
        if again == text:
            return text
        text = again
    raise KnowledgeBaseError("redaction did not settle; nothing was stored")


def redact_json(value: Any, leaf: Callable[[str], str] = scrub) -> Any:
    """``leaf`` applied to every string value of a JSON document; keys and non-strings are kept."""
    if isinstance(value, str):
        return leaf(value)
    if isinstance(value, list):
        return [redact_json(v, leaf) for v in value]
    if isinstance(value, dict):
        return {k: redact_json(v, leaf) for k, v in value.items()}
    return value
