"""The Containerfile's archive check: a git archive passes it, a checkout does not."""

import io
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MARKER = ".archive-commit"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (REPO / ".git").exists(), reason="needs git and a git checkout"
)


def _archive_check() -> str:
    """The Containerfile's RUN command that refuses a context without an expanded marker."""
    lines = (REPO / "Containerfile").read_text().splitlines()
    checks = [line.removeprefix("RUN ") for line in lines if line.startswith("RUN ") and MARKER in line]
    assert len(checks) == 1, checks
    return checks[0]


def _run_check(marker: bytes, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    (tmp_path / MARKER).write_bytes(marker)
    return subprocess.run(["sh", "-c", _archive_check()], cwd=tmp_path, capture_output=True, text=True, check=False)


def _archived_marker(*git_config: str) -> bytes:
    tar = subprocess.run(
        ["git", *git_config, "archive", "--format=tar", "HEAD", MARKER], cwd=REPO, capture_output=True, check=True
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as archive:
        member = archive.extractfile(MARKER)
        assert member is not None
        return member.read()


def test_checkout_marker_is_refused_with_the_archive_command(tmp_path: Path) -> None:
    result = _run_check((REPO / MARKER).read_bytes(), tmp_path)
    assert result.returncode != 0
    assert "git archive --format=tar <tag> | podman build" in result.stderr


@pytest.mark.parametrize("git_config", [(), ("-c", "core.autocrlf=true")], ids=["default", "autocrlf"])
def test_archived_marker_passes(git_config: tuple[str, ...], tmp_path: Path) -> None:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True)
    marker = _archived_marker(*git_config)
    assert marker == head.stdout.encode()
    assert _run_check(marker, tmp_path).returncode == 0
