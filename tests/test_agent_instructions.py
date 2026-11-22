"""The three agent instruction files carry one Verify list (#639): an agent reading any of them runs the same gate."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SECTION = re.compile(r"^## Verify\n.*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)


def _verify(name: str) -> str:
    """The file's ``## Verify`` section, heading included, up to the next ``## `` heading."""
    found = _SECTION.search((_ROOT / name).read_bytes().decode("utf-8"))
    assert found is not None, f"{name} has no '## Verify' section"
    assert found.group().partition("\n")[2].strip(), f"{name}'s '## Verify' section is empty"
    return found.group()


@pytest.mark.parametrize("name", ["AGENTS.md", "CLAUDE.md", "GEMINI.md"])
def test_each_instruction_file_has_a_verify_section(name):
    _verify(name)


@pytest.mark.parametrize("name", ["CLAUDE.md", "GEMINI.md"])
def test_the_verify_copy_matches_agents_md(name):
    assert _verify(name) == _verify("AGENTS.md"), (
        f"{name}'s '## Verify' section differs from AGENTS.md's: edit the three files together"
    )
