"""Unit tests for LocalFileSystem -- Sprint 4."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from shokz.adapters.outbound.local_filesystem import LocalFileSystem


def test_atomic_move_via_os_replace_plus_dual_fsync(tmp_path: Path) -> None:
    """Sprint 4 AC: 'Atomic-write integrity (unit-level)'.

    LocalFileSystem.atomic_move MUST call os.fsync TWICE per move:
      - once on the moved-file's fd
      - once on the parent-dir's fd
    Plus os.replace must be called (the actual atomic rename).
    """
    fs = LocalFileSystem()
    src = tmp_path / "tmp" / "x.partial"
    src.parent.mkdir()
    src.write_bytes(b"hello world")
    dest = tmp_path / "x.mp3"

    fsync_calls: list[int] = []
    real_fsync = os.fsync
    real_replace = os.replace
    replace_calls: list[tuple[Path, Path]] = []

    def counting_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        real_fsync(fd)

    def counting_replace(s: str | os.PathLike[str], d: str | os.PathLike[str]) -> None:
        replace_calls.append((Path(s), Path(d)))
        real_replace(s, d)

    with (
        patch("shokz.adapters.outbound.local_filesystem.os.fsync", counting_fsync),
        patch("shokz.adapters.outbound.local_filesystem.os.replace", counting_replace),
    ):
        fs.atomic_move(src, dest)

    assert dest.exists()
    assert dest.read_bytes() == b"hello world"
    assert not src.exists()
    assert len(replace_calls) == 1
    assert replace_calls[0] == (src, dest)
    # Exactly 2 fsyncs: file fd + parent dir fd.
    assert len(fsync_calls) == 2, f"expected 2 fsync calls, got {len(fsync_calls)}"
