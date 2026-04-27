"""LocalFileSystem -- FileSystemPort backed by os/pathlib (Sprint 4).

The atomic-write protocol is the load-bearing claim of v0.4.0:
  1. os.replace(src, dest)        -- POSIX-atomic same-FS rename
  2. fd = os.open(dest, O_RDONLY) -- get the moved file's fd
  3. os.fsync(fd)                 -- flush its data + metadata to platter
  4. os.close(fd)
  5. dir_fd = os.open(parent, O_RDONLY)
  6. os.fsync(dir_fd)             -- flush the directory entry itself
  7. os.close(dir_fd)

Without step 5-7, the directory entry can be lost on a power-cut even when
the file's data is durable -- a real bug class (silent-failure-hunter F3
genealogy from v0.2.0 review).
"""

from __future__ import annotations

import errno
import logging
import os
from pathlib import Path

from shokz.domain.errors import DiskFull

_log = logging.getLogger("shokz.adapter.fs")


class LocalFileSystem:
    """FileSystemPort implementation using os.replace + fsync chain."""

    def atomic_move(self, src: Path, dest: Path) -> None:
        # Sprint 8b: ENOSPC during os.replace -> DiskFull (.partial stays
        # in tmp_dir for next-run cleanup; we don't unlink here because
        # the caller's finally block / Sprint 5 reconciliation handles it).
        try:
            os.replace(src, dest)
        except OSError as e:
            if e.errno == errno.ENOSPC:
                raise DiskFull(
                    f"disk full during os.replace({src} -> {dest})"
                ) from e
            raise
        # fsync the file we just moved
        fd = os.open(dest, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        # fsync the parent dir entry so the rename itself is durable
        dir_fd = os.open(dest.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        _log.debug("atomic_move: %s -> %s (fsync chain complete)", src, dest)

    def exists(self, path: Path) -> bool:
        return path.exists()

    def mkdir_p(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    def remove(self, path: Path) -> None:
        path.unlink(missing_ok=True)
