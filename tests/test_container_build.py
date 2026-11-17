"""The Containerfile's archive check: a git archive passes it, a checkout does not."""

import io
import shutil
import subprocess
import tarfile
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MARKER = ".archive-commit"
GIT = shutil.which("git")
SH = shutil.which("sh")

pytestmark = pytest.mark.skipif(
    GIT is None or SH is None or not (REPO / ".git").exists(), reason="needs git, sh and a git checkout"
)


def _run(argv: list[str | None], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    # Trusted argv: git and sh resolved above, running this repository's own Containerfile check.
    return subprocess.run([str(a) for a in argv], cwd=cwd, capture_output=True, check=False)  # noqa: S603


def _archive_check() -> str:
    """The Containerfile's RUN command that refuses a context without an expanded marker."""
    lines = (REPO / "Containerfile").read_text().splitlines()
    checks = [i for i, line in enumerate(lines) if line.startswith("RUN ") and MARKER in line]
    assert len(checks) == 1, checks
    # Refusing before any source is copied keeps a dirty src/ out of every builder layer.
    assert checks[0] < lines.index("COPY src ./src")
    return lines[checks[0]].removeprefix("RUN ")


def _run_check(marker: bytes, tmp_path: Path) -> subprocess.CompletedProcess[bytes]:
    (tmp_path / MARKER).write_bytes(marker)
    return _run([SH, "-c", _archive_check()], tmp_path)


def _archived_marker(*git_config: str) -> bytes:
    tar = _run([GIT, *git_config, "archive", "--format=tar", "HEAD", MARKER], REPO)
    assert tar.returncode == 0, tar.stderr
    with tarfile.open(fileobj=io.BytesIO(tar.stdout)) as archive:
        member = archive.extractfile(MARKER)
        assert member is not None
        return member.read()


def test_checkout_marker_is_refused_with_the_archive_command(tmp_path: Path) -> None:
    result = _run_check((REPO / MARKER).read_bytes(), tmp_path)
    assert result.returncode != 0
    assert b"git archive --format=tar <tag> | podman build" in result.stderr


@pytest.mark.parametrize("git_config", [(), ("-c", "core.autocrlf=true")], ids=["default", "autocrlf"])
def test_archived_marker_passes(git_config: tuple[str, ...], tmp_path: Path) -> None:
    head = _run([GIT, "rev-parse", "HEAD"], REPO).stdout
    marker = _archived_marker(*git_config)
    assert marker == head
    assert _run_check(marker, tmp_path).returncode == 0


def test_build_group_is_the_declared_backend() -> None:
    # The image builds the project with the locked build group, every other build with [build-system]; uv lock --check
    # never reads [build-system], so only this keeps a raised floor from missing the image.
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert data["dependency-groups"]["build"] == data["build-system"]["requires"]
