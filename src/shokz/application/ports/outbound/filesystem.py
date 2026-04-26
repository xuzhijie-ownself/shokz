"""FileSystemPort -- atomic move + dual fsync (Sprint 4)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class FileSystemPort(Protocol):
    """Filesystem operations the use case needs.

    Sprint 4 ships `atomic_move` (the load-bearing crash-safety primitive).
    Sprint 8 will add free_space_bytes for the disk guard.
    """

    def atomic_move(self, src: Path, dest: Path) -> None:
        """Move src to dest atomically.

        On the SAME filesystem: os.replace + fsync(dest) + fsync(parent_dir).
        Cross-FS would need a copy+rename fallback, but plan §3 guarantees
        .tmp/ is INSIDE downloads/ so we never cross FS.
        """

    def exists(self, path: Path) -> bool: ...

    def mkdir_p(self, path: Path) -> None: ...

    def remove(self, path: Path) -> None: ...
