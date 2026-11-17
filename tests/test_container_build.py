"""The Containerfile's static guarantees: a git archive passes the archive check and a checkout does not, the backend
is the locked one, and no dependency code runs with network."""

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

needs_checkout = pytest.mark.skipif(
    GIT is None or SH is None or not (REPO / ".git").exists(), reason="needs git, sh and a git checkout"
)

# The one build-stage step with network. Pinned as text, not parsed: any change to it, or to what runs before it, is a
# security-relevant edit and should have to touch this test.
FETCH = (
    "RUN uv sync --locked --no-build --no-dev --group reranker --no-install-project"
    " && UV_PROJECT_ENVIRONMENT=/backend uv sync --locked --no-build --only-group build"
)
MARKER_CHECK = "<the archive check>"
# The build stage up to the fetch, FROM aside.
BEFORE_FETCH = [
    "COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv",
    "ENV UV_PYTHON_DOWNLOADS=never",
    "WORKDIR /app",
    f"COPY {MARKER} ./",
    MARKER_CHECK,
    "COPY pyproject.toml uv.lock .python-version LICENSE-MIT LICENSE-APACHE ./",
    FETCH,
]


def _run(argv: list[str | None], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    # Trusted argv: git and sh resolved above, running this repository's own Containerfile check.
    return subprocess.run([str(a) for a in argv], cwd=cwd, capture_output=True, check=False)  # noqa: S603


def _archive_check() -> str:
    """The Containerfile's RUN command that refuses a context without an expanded marker."""
    lines = (REPO / "Containerfile").read_text().splitlines()
    checks = [i for i, line in enumerate(lines) if line.startswith("RUN ") and MARKER in line]
    assert len(checks) == 1, checks
    assert checks[0] == next(i for i, line in enumerate(lines) if line.startswith("RUN ")), "not the first RUN"
    # Refusing before any source is copied keeps a dirty src/ out of every builder layer.
    sources = [
        i
        for i, line in enumerate(lines)
        if line.startswith("COPY ") and not line.startswith("COPY --from=") and line != f"COPY {MARKER} ./"
    ]
    assert all(checks[0] < i for i in sources), sources
    return lines[checks[0]].removeprefix("RUN ")


def _build_stage() -> list[str]:
    """The build stage's instructions, FROM aside: comments dropped, continuations joined, whitespace collapsed."""
    instructions, current = [], ""
    for line in (REPO / "Containerfile").read_text().splitlines():
        if line.lstrip().startswith("#"):
            continue
        current += " " + line.removesuffix("\\")
        if not line.endswith("\\"):
            if current.strip():
                instructions.append(" ".join(current.split()))
            current = ""
    froms = [i for i, instruction in enumerate(instructions) if instruction.startswith("FROM ")]
    return instructions[froms[0] + 1 : froms[1]]


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


@needs_checkout
def test_checkout_marker_is_refused_with_the_archive_command(tmp_path: Path) -> None:
    result = _run_check((REPO / MARKER).read_bytes(), tmp_path)
    assert result.returncode != 0
    assert b"git archive --format=tar <tag> | podman build" in result.stderr


@needs_checkout
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


def test_dependency_code_runs_without_network() -> None:
    """Only the pinned fetch has network; everything after it is offline.

    The archive check is matched by its text, not pinned: its body is #585's and the archive tests run it. So the
    static guard's residue is the base image and that one line.
    """
    stage = _build_stage()
    assert FETCH in stage, "the fetch is not the pinned wheels-only uv sync"
    fetch = stage.index(FETCH)
    before = [MARKER_CHECK if s.startswith("RUN ") and MARKER in s else s for s in stage[: fetch + 1]]
    assert before == BEFORE_FETCH
    # After the fetch only offline steps: the venv's Python (bytecode compilation runs .pth files) and the backend.
    after = stage[fetch + 1 :]
    assert all(s == "COPY src ./src" or s.startswith("RUN --network=none ") for s in after), after
    assert any(s.startswith("RUN --network=none ") for s in after[after.index("COPY src ./src") + 1 :])
    commands = [c for s in after if s.startswith("RUN ") for c in s.removeprefix("RUN --network=none ").split(" && ")]
    assert all("--offline" in c.split() for c in commands if c.startswith("uv ")), commands
    installs = [c for c in commands if c.startswith("uv pip install ")]
    assert installs and all("--no-deps" in c.split() for c in installs), installs
    # The one setting outside the Containerfile that would make the fetch compile.
    assert "compile-bytecode" not in tomllib.loads((REPO / "pyproject.toml").read_text()).get("tool", {}).get("uv", {})
