"""Local kernel ownership of a trusted repair job root; no PID/liveness guessing."""

import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def _regular(metadata) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_nlink == 1
        and not (
            getattr(metadata, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
        )
    )


def _lock(descriptor: int, *, release: bool = False) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_UNLCK if release else msvcrt.LK_NBLCK, 1)
    elif os.name == "posix":
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN if release else fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        raise ValueError("Repair root locking is unsupported on this platform")


@contextmanager
def repair_root_lock(root: Path) -> Iterator[Path]:
    """Nonblocking exclusive owner; persistent lock file is never unlinked.

    Local filesystem/parent are trusted. All repair writers must use this protocol.
    Kernel handles, not file existence or a recorded PID, determine ownership.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("An existing repair job root is required for ownership")
    root_identity = root.lstat()
    path = root / ".repair-owner.lock"
    try:
        before = path.lstat()
    except FileNotFoundError:
        before = None
    if before is not None and (not _regular(before) or before.st_size not in (0, 1)):
        raise ValueError("Repair root lock file identity invalid")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    owned = False
    try:
        os.set_inheritable(descriptor, False)
        opened = os.fstat(descriptor)
        if (
            not _regular(opened)
            or opened.st_size not in (0, 1)
            or (
                before is not None
                and (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            )
        ):
            raise ValueError("Repair root lock changed while opening")
        try:
            _lock(descriptor)
        except OSError as error:
            raise ValueError("Repair job root is already owned or cannot be locked") from error
        owned = True
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\x00")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.read(descriptor, 2) != b"\x00":
            raise ValueError("Repair root lock contents invalid")
        current, directory = path.lstat(), root.lstat()
        if (
            not _regular(current)
            or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
            or (directory.st_dev, directory.st_ino) != (root_identity.st_dev, root_identity.st_ino)
        ):
            raise ValueError("Repair root ownership identity changed")
        yield root
    finally:
        try:
            if owned:
                _lock(descriptor, release=True)
        finally:
            os.close(descriptor)
