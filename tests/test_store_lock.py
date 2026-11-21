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
    with hold(db, "test"), pytest.raises(KnowledgeBaseError) as refused, hold(db, "test"):
        pass
    assert str(_lock_file(db)) in str(refused.value)
    assert "in use" in str(refused.value)


def test_the_lock_file_survives_release_with_its_inode(tmp_path):
    db = tmp_path / "kb.db"
    with hold(db, "test"):
        inode = _lock_file(db).stat().st_ino
    assert _lock_file(db).stat().st_ino == inode
    with hold(db, "test"):
        assert _lock_file(db).stat().st_ino == inode
    assert not db.exists()


def test_a_symlinked_database_shares_its_target_lock(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    db = real / "kb.db"
    db.touch()
    (tmp_path / "file-link.db").symlink_to(db)
    (tmp_path / "dir-link").symlink_to(real)
    with hold(db, "test"):
        for alias in (tmp_path / "file-link.db", tmp_path / "dir-link" / "kb.db"):
            with pytest.raises(KnowledgeBaseError), hold(alias, "test"):
                pass


def test_the_lock_creates_the_database_directory(tmp_path):
    db = tmp_path / "new" / "dir" / "kb.db"
    with hold(db, "test"):
        assert _lock_file(db).is_file()


_PROC_FDS = Path("/proc/self/fd")


@pytest.mark.skipif(not _PROC_FDS.is_dir(), reason="needs /proc")
def test_the_lock_descriptor_is_not_inherited(tmp_path):
    db = tmp_path / "kb.db"
    with hold(db, "test"):
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
        with hold(Path({str(db)!r}), "test"):
            child = subprocess.Popen(["sleep", "60"], close_fds=False)
            print(child.pid, flush=True)
            time.sleep(60)
        """
    )
    holder = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)  # noqa: S603
    assert holder.stdout is not None
    child_pid = int(holder.stdout.readline())
    try:
        with pytest.raises(KnowledgeBaseError), hold(db, "test"):
            pass
        holder.send_signal(signal.SIGKILL)
        holder.wait(10)
        os.kill(child_pid, 0)  # the child still runs
        with hold(db, "test"):
            pass
    finally:
        holder.kill()
        holder.wait(10)
        os.kill(child_pid, signal.SIGKILL)


def test_a_refusal_names_the_holder(tmp_path):
    db = tmp_path / "kb.db"
    with hold(db, "knowledge-base --http"), pytest.raises(KnowledgeBaseError) as refused, hold(db, "b"):
        pass
    assert f"knowledge-base --http (pid {os.getpid()}, since " in str(refused.value)
    with hold(db, "b"), pytest.raises(KnowledgeBaseError) as refused, hold(db, "c"):
        pass
    assert f"in use by b (pid {os.getpid()}, since " in str(refused.value)
    assert "knowledge-base --http" not in str(refused.value)


def test_a_lock_directory_that_cannot_be_made_is_a_clear_error(tmp_path):
    (tmp_path / "file").write_text("not a directory")
    with pytest.raises(KnowledgeBaseError, match="cannot lock the store"), hold(tmp_path / "file" / "kb.db", "test"):
        pass


def test_a_holder_record_that_cannot_be_written_is_a_clear_error_and_frees_the_lock(tmp_path, monkeypatch):
    db = tmp_path / "kb.db"

    def _full(*_args):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "pwrite", _full)
    with pytest.raises(KnowledgeBaseError, match="No space left on device"), hold(db, "test"):
        pass
    monkeypatch.undo()
    with hold(db, "test"):
        pass


def test_a_new_holder_never_leaves_the_record_empty(tmp_path, monkeypatch):
    """Written before it is trimmed, the record is never empty between the lock and the write (#578)."""
    db = tmp_path / "kb.db"
    with hold(db, "a-much-longer-first-holder-name"):
        pass
    seen: list[bytes] = []
    real = os.ftruncate

    def _spy(fd, length):
        seen.append(lock_file_bytes(db))
        real(fd, length)

    monkeypatch.setattr(os, "ftruncate", _spy)
    with hold(db, "b"):
        assert lock_file_bytes(db).startswith(b"b (pid ")
    assert seen and seen[0].startswith(b"b (pid ")


def lock_file_bytes(db) -> bytes:
    return _lock_file(db).read_bytes()
