"""The store lock (#596, #578): one exclusive advisory lock beside the database."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from knowledge_base.exceptions import KnowledgeBaseError
from knowledge_base.store_lock import hold


def _lock_file(db):
    return db.parent / f"{db.name}.store-lock"


def test_a_second_holder_is_refused_naming_the_lock_file(tmp_path):
    db = tmp_path / "kb.db"
    with hold(db), pytest.raises(KnowledgeBaseError) as refused, hold(db):
        pass
    assert str(_lock_file(db)) in str(refused.value)
    assert "in use" in str(refused.value)


def test_the_lock_file_survives_release_with_its_inode(tmp_path):
    db = tmp_path / "kb.db"
    with hold(db):
        inode = _lock_file(db).stat().st_ino
    assert _lock_file(db).stat().st_ino == inode
    with hold(db):
        assert _lock_file(db).stat().st_ino == inode
    assert not db.exists()


def test_a_symlinked_database_shares_its_target_lock(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    db = real / "kb.db"
    db.touch()
    (tmp_path / "file-link.db").symlink_to(db)
    (tmp_path / "dir-link").symlink_to(real)
    with hold(db):
        for alias in (tmp_path / "file-link.db", tmp_path / "dir-link" / "kb.db"):
            with pytest.raises(KnowledgeBaseError), hold(alias):
                pass


def test_the_lock_creates_the_database_directory(tmp_path):
    db = tmp_path / "new" / "dir" / "kb.db"
    with hold(db):
        assert _lock_file(db).is_file()


_PROC_FDS = Path("/proc/self/fd")


@pytest.mark.skipif(not _PROC_FDS.is_dir(), reason="needs /proc")
def test_the_lock_descriptor_is_not_inherited(tmp_path):
    db = tmp_path / "kb.db"
    with hold(db):
        lock_fds = [int(fd.name) for fd in _PROC_FDS.iterdir() if _points_at(fd, _lock_file(db))]
        assert lock_fds, "the lock holds a descriptor on the lock file"
        assert not any(os.get_inheritable(fd) for fd in lock_fds)


def _points_at(fd: Path, path: Path) -> bool:
    try:
        return fd.readlink() == path.resolve()
    except OSError:
        return False


def test_a_killed_holder_frees_the_store_though_its_child_lives(tmp_path):
    db = tmp_path / "kb.db"
    script = textwrap.dedent(
        f"""
        import subprocess, sys, time
        from pathlib import Path
        from knowledge_base.store_lock import hold
        with hold(Path({str(db)!r})):
            child = subprocess.Popen(["sleep", "60"], close_fds=False)
            print(child.pid, flush=True)
            time.sleep(60)
        """
    )
    holder = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)  # noqa: S603
    assert holder.stdout is not None
    child_pid = int(holder.stdout.readline())
    try:
        with pytest.raises(KnowledgeBaseError), hold(db):
            pass
        holder.send_signal(signal.SIGKILL)
        holder.wait(10)
        os.kill(child_pid, 0)  # the child still runs
        with hold(db):
            pass
    finally:
        holder.kill()
        holder.wait(10)
        os.kill(child_pid, signal.SIGKILL)
