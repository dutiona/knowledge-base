"""The store lock: one exclusive advisory lock beside the database (memory-engine's ADR-0020 §8, #596).

Every KB process that writes a database holds it: ``knowledge-base`` for its whole life, a writing
``knowledge-base-ingest`` command for its run. It is ``flock``: the kernel releases it when its holder exits, however
it exits, so a killed server leaves nothing to clean. It holds between processes of one host, containers sharing the
database's volume included, never across hosts or on a network filesystem. Read-only commands never take it.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .exceptions import KnowledgeBaseError

__all__ = ["hold", "lock_path"]


def _recorded_holder(fd: int) -> str:
    """The holder's line, or nothing when it cannot be read: a refusal never fails for want of a name."""
    try:
        return os.pread(fd, 512, 0).decode(errors="replace").partition("\n")[0].strip()
    except OSError:
        return ""


def lock_path(db: Path) -> Path:
    """``db``, symlinks resolved so every name of one store finds one lock, plus ``.store-lock``.

    Not ``.lock``, which SQLite's unix-dotfile VFS uses.
    """
    return Path(f"{db.expanduser().resolve()}.store-lock")


@contextmanager
def hold(db: Path, holder: str) -> Iterator[None]:
    """Hold the store lock of ``db`` for the block, or raise :class:`KnowledgeBaseError` at once if it is held.

    *holder* names this process in a refusal another process gets (#578): a role such as ``knowledge-base --http``,
    never an argument value, which can carry a secret. The line it writes is only ever quoted: whether the store is
    held is the kernel's answer alone, so it is no PID file, and the next holder overwrites a dead one's line. In the
    instant between taking the lock and writing its line, a refusal still names the previous holder.
    """
    lock = lock_path(db)
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        # Never truncated at open and never unlinked: unlinking a held lock file hands the next process a fresh inode
        # and a second "exclusive" lock. The descriptor is not inheritable (PEP 446), so a child process never keeps
        # a dead holder's lock.
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        raise KnowledgeBaseError(f"cannot lock the store {lock}: {exc.strerror}") from exc
    try:
        try:
            # flock, never lockf: a POSIX lock belongs to the process and drops when it closes any descriptor of
            # the file.
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise KnowledgeBaseError(
                f"the store is in use by {_recorded_holder(fd) or 'another process'}: {lock} is locked; "
                "stop it or let it finish, then rerun"
            ) from exc
        except OSError as exc:
            raise KnowledgeBaseError(f"cannot lock the store {lock}: {exc.strerror}") from exc
        since = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        record = f"{holder} (pid {os.getpid()}, since {since})\n".encode()
        try:
            # Written, then trimmed: a contender reading in between sees this record or the last one, never nothing.
            written = os.pwrite(fd, record, 0)
            os.ftruncate(fd, len(record))
        except OSError as exc:
            raise KnowledgeBaseError(f"cannot record the store lock's holder in {lock}: {exc.strerror}") from exc
        if written != len(record):
            raise KnowledgeBaseError(f"cannot record the store lock's holder in {lock}: short write")
        yield
    finally:
        os.close(fd)
