"""The redaction engine against the cases it shares with memory-engine.

``tests/fixtures/redaction-cases.jsonl`` is carried byte for byte by dutiona/knowledge-base and
dutiona/memory-engine (memory-engine#1024). Its outputs were produced by memory-engine's own code at
97c8e77 (``crates/memory-engine/src/bootstrap/redact.rs``) with Michaël's rulings of 2026-11-17 applied: no
email detector, the ADR-0020 machine-token detector first, and links exempt from every rule but
url-credential. memory-engine#1024 then added sixteen provider prefixes (the ``new-prefix-`` cases) and made
SendGrid's ``SG.`` and ``eyJ`` need a dot past the prefix. Only ``url-authority-ends-at-multibyte-space`` is
written by hand: memory-engine panics on it.
Change the file only through a linked pair of PRs, memory-engine first, both quoting the same ``sha256sum``.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

import pytest

from knowledge_base.redact import TOKEN_SHAPE, redact, redact_json, scrub

FIXTURE = Path(__file__).parent / "fixtures" / "redaction-cases.jsonl"
FIXTURE_SHA256 = "c4fb2a1f41e6dfeea4dfbeaea1cb28145c8d04390003dc04440061c868e22c7d"
_LINES = FIXTURE.read_text(encoding="utf-8").split("\n")
HEADER = json.loads(_LINES[0])
CASES = [json.loads(line) for line in _LINES[1:] if line]
# Example shapes built at run time, so no token literal is committed.
AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
GITHUB_TOKEN = "ghp_" + "0123456789abcdefghij" * 2
OPENAI_KEY = "sk-" + "abcdefghijklmnopqrstuvwxyz0123"
_SHORT = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _string(value: str, *, hide: bool) -> str:
    """JSON string as both serializers write it; ``hide`` escapes ``@`` and each ASCII-alphanumeric run's first char."""
    out = []
    for i, c in enumerate(value):
        prev = value[i - 1] if i else ""
        starts_run = c.isascii() and c.isalnum() and not (prev.isascii() and prev.isalnum())
        if c in _SHORT:
            out.append(_SHORT[c])
        elif ord(c) < 0x20 or (hide and (c == "@" or starts_run)):
            out.append(f"\\u{ord(c):04x}")
        else:
            out.append(c)
    return '"' + "".join(out) + '"'


def encode_line(case: dict) -> str:
    """One fixture line in its canonical form (the header when ``case`` has only ``about``)."""
    if set(case) == {"about"}:
        return '{"about":' + _string(case["about"], hide=False) + "}"
    rules = ",".join(_string(r, hide=False) for r in case["rules"])
    return (
        f'{{"name":{_string(case["name"], hide=False)},"input":{_string(case["input"], hide=True)},'
        f'"output":{_string(case["output"], hide=True)},"rules":[{rules}]}}'
    )


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_redact_matches_the_shared_case(case):
    assert redact(case["input"]) == (case["output"], case["rules"])


def test_fixture_is_the_pinned_one():
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert digest == FIXTURE_SHA256, (
        "the fixture changed: that is a linked PR pair, memory-engine first, then this repo"
    )


def test_fixture_is_canonical():
    names = [c["name"] for c in CASES]
    assert len(names) == len(set(names))
    assert all(re.fullmatch(r"[a-z0-9]+(?:[-.][a-z0-9]+)*", n) for n in names)
    expected = "\n".join([encode_line(HEADER), *(encode_line(c) for c in CASES)]) + "\n"
    assert FIXTURE.read_text(encoding="utf-8") == expected


def test_fixture_holds_no_secret():
    for line in _LINES:
        assert scrub(line) == line


def test_no_email_case_and_no_email_rule():
    """Owner ruling, 2026-11-17: an email address is not a secret."""
    assert not any("@" in c["input"] and "://" not in c["input"] for c in CASES)
    for text in ("contact craig@example.com please", '{"email":"a@b.com"}', f"aws {AWS_KEY} and a@b.com"):
        out, rules = redact(text)
        assert "@b.com" in out or "@example.com" in out
        assert "email" not in rules


def test_url_authority_ending_at_a_multibyte_space_does_not_crash():
    for space in (" ", "　"):
        out, rules = redact(f"see https://u:pw@example.com{space}more https://v:pw2@example.org")
        assert rules == ["connection-string-password", "connection-string-password"]
        assert "pw" not in out.replace("[REDACTED:connection-string-password]", "")


def test_redact_json_keeps_keys_and_non_strings():
    value = {
        "description": f"leaked {AWS_KEY} here",
        "nested": {"note": f"token {GITHUB_TOKEN}"},
        "list": [OPENAI_KEY, "harmless"],
        "count": 7,
        "flag": True,
    }
    out = redact_json(value)
    assert set(out) == set(value)
    assert "AKIA" not in json.dumps(out)
    assert out["list"][1] == "harmless"
    assert out["count"] == 7
    assert out["flag"] is True
    clean = {"a": "nothing secret", "b": [1, 2, 3]}
    assert redact_json(clean) == clean


def test_scrub_reaches_the_fixpoint_one_pass_misses():
    text = "api_key=a3f1b2c4d5e6f7a8b9c0d1e2f3a4b5c6qWeRtYuIoPaSdFgHjKl9876"
    once, _ = redact(text)
    assert once != scrub(text)
    assert scrub(text) == "api_key=[REDACTED:hex-secret][REDACTED:high-entropy]"
    assert scrub(scrub(text)) == scrub(text)


def test_token_shape_is_the_adr_shape():
    assert re.fullmatch(TOKEN_SHAPE, "me_" + "0" * 32)
    assert not re.fullmatch(TOKEN_SHAPE, "me_types")


@pytest.mark.parametrize(
    "text",
    [
        "=eyJ" * (2**21 // 4),
        "://u:p@" * (2**21 // 7),
        "token=a3f1b2c4d5e6f7a8b9c0d1e2f3a4b5c6 " * (2**21 // 39),
        "-----BEGIN " * (2**21 // 11),
        "_a3f1b2c4d5e6f7a8b9c0d1e2f3a4b5c6" * (2**21 // 33),
        # One word, one underscore: a copied context-word tail made this quadratic (15 s at 8 MiB, now 0.6 s).
        "_" + ("g" + "0" * 32) * (2**23 // 33),
        ("me_" + "0" * 32 + " Ab3dE5fG7hI9jK1lM2nO ") * (2**21 // 57),  # two detectors' spans interleave
    ],
    ids=["jwt-prefix", "url-authority", "hex-context", "pem-line", "hex-words", "one-long-word", "interleaved"],
)
def test_redaction_is_linear_on_hostile_input(text):
    """``ingest_text`` takes 4 MiB over HTTP. At 2 MiB a quadratic shape that costs 2.5 s at 1 MiB takes 10 s."""
    started = time.monotonic()
    scrub(text)
    assert time.monotonic() - started < 6


def test_a_text_is_read_between_its_neighbours():
    """#592: a fragment no rule matches alone, or a secret whose context word precedes it, is redacted in context."""
    from knowledge_base.redact import redact_in_context

    token = "ghp_" + "Zq7Xw3Rt9Lp2Vb6Nm4Kc8Hd1Fs5Gj0Ay"
    assert redact_in_context("see " + token[:12], [("", token[12:] + " end")]) == (
        "see [REDACTED:github-pat]",
        ["github-pat"],
    )
    hexed = "0123456789abcdef" * 2
    assert redact_in_context(hexed + " ok", [("the api_key=", "")]) == ("[REDACTED:hex-secret] ok", ["hex-secret"])
    assert redact_in_context("plain words", [("before ", " after")]) == ("plain words", [])
    # A row two revisions share: each context finds its own continuation's secret.
    assert (
        redact_in_context("tail " + token[:12], [("", "x plain"), ("", token[12:])])[0] == "tail [REDACTED:github-pat]"
    )
