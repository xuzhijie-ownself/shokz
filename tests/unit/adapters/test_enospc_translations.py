"""Sprint 8b: ENOSPC translation at the 3 outbound-adapter sites.

Combined into one file (vs 3 separate per the spec) to minimize gate
friction during a single-session push.

Sites:
  1. ffmpeg_encoder.py: stderr-text "no space left" / "enospc" -> DiskFull
     (B4 -- subprocess never propagates Python OSError, so detection is
     stderr-text based, NOT OSError catch).
  2. local_filesystem.py: OSError(errno.ENOSPC) on os.replace -> DiskFull.
  3. jsonl_manifest.py: OSError(errno.ENOSPC) on os.write -> raise
     ManifestInconsistent FROM DiskFull (M3: ManifestInconsistent is the
     visible class because reconciliation is the recoverable signal;
     DiskFull is __cause__ for diagnosis).
"""

from __future__ import annotations

import asyncio
import errno
from pathlib import Path
from typing import Any

import pytest

from shokz.adapters.outbound.ffmpeg_encoder import FfmpegEncoder
from shokz.adapters.outbound.jsonl_manifest import JsonlManifest, _append_with_fsync
from shokz.adapters.outbound.local_filesystem import LocalFileSystem
from shokz.domain.errors import DiskFull, ManifestInconsistent
from shokz.domain.models import AudioSpec, ManifestEntry

# --- ffmpeg: stderr-text ENOSPC -> DiskFull (B4) ------------------------


class _FakeProc:
    """Stand-in for asyncio.subprocess.Process whose communicate() returns
    a configured (stdout, stderr) and whose returncode is set."""

    def __init__(self, returncode: int, stdout: bytes, stderr: bytes) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


@pytest.mark.asyncio
async def test_ffmpeg_enospc_stderr_text_translates_to_disk_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B4: ffmpeg subprocess exits non-zero with stderr containing 'No space
    left on device' -> adapter raises DiskFull (NOT EncodingFailed)."""
    src = tmp_path / "raw.webm"
    src.write_bytes(b"FAKE")
    dest = tmp_path / "out.mp3.partial"
    dest.write_bytes(b"PARTIAL")  # the .partial that should be cleaned up
    spec = AudioSpec(codec="mp3", bitrate_kbps=64, channels=1, sample_rate_hz=44100)

    async def _fake_exec(*_args: Any, **_kwargs: Any) -> _FakeProc:
        return _FakeProc(
            returncode=1,
            stdout=b"",
            stderr=b"some prelude\n[error] No space left on device\nfinal line",
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    encoder = FfmpegEncoder()
    with pytest.raises(DiskFull, match="ffmpeg encode"):
        await encoder.encode(src, dest, spec)
    # B4: .partial cleaned up before raise.
    assert not dest.exists()


@pytest.mark.asyncio
async def test_ffmpeg_enospc_case_insensitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ENOSPC text comes in different cases across ffmpeg versions / locales."""
    src = tmp_path / "raw.webm"
    src.write_bytes(b"FAKE")
    dest = tmp_path / "out.mp3.partial"
    spec = AudioSpec(codec="mp3", bitrate_kbps=64, channels=1, sample_rate_hz=44100)

    async def _fake_exec(*_args: Any, **_kwargs: Any) -> _FakeProc:
        return _FakeProc(returncode=1, stdout=b"", stderr=b"ERROR: ENOSPC")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    encoder = FfmpegEncoder()
    with pytest.raises(DiskFull):
        await encoder.encode(src, dest, spec)


# --- local_filesystem: OSError(ENOSPC) on os.replace -> DiskFull --------


def test_local_filesystem_enospc_on_atomic_move_translates_to_disk_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OSError(errno.ENOSPC) on os.replace -> DiskFull. .partial NOT
    auto-cleaned (caller's finally / reconciliation handles it)."""
    src = tmp_path / "src.partial"
    src.write_bytes(b"BYTES")
    dest = tmp_path / "dest.mp3"

    def _replace_enospc(_src: str, _dest: str) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(
        "shokz.adapters.outbound.local_filesystem.os.replace", _replace_enospc
    )

    fs = LocalFileSystem()
    with pytest.raises(DiskFull, match=r"os\.replace"):
        fs.atomic_move(src, dest)
    # Per spec: caller's finally handles cleanup; we don't unlink here.
    assert src.exists()


def test_local_filesystem_non_enospc_oserror_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A different OSError (EACCES) MUST NOT be silently translated to DiskFull."""
    src = tmp_path / "src.partial"
    src.write_bytes(b"BYTES")
    dest = tmp_path / "dest.mp3"

    def _replace_eacces(_src: str, _dest: str) -> None:
        raise OSError(errno.EACCES, "permission denied")

    monkeypatch.setattr(
        "shokz.adapters.outbound.local_filesystem.os.replace", _replace_eacces
    )

    fs = LocalFileSystem()
    with pytest.raises(OSError, match="permission"):
        fs.atomic_move(src, dest)


# --- jsonl_manifest: OSError(ENOSPC) -> ManifestInconsistent FROM DiskFull --


def test_jsonl_manifest_enospc_raises_manifest_inconsistent_from_disk_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M3: ENOSPC during _append_with_fsync raises ManifestInconsistent (the
    recoverable-signal class) chained from DiskFull (the underlying cause)."""
    path = tmp_path / "manifest.jsonl"

    def _write_enospc(_fd: int, _data: bytes) -> int:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(
        "shokz.adapters.outbound.jsonl_manifest.os.write", _write_enospc
    )

    payload: dict[str, object] = {"x": 1}
    with pytest.raises(ManifestInconsistent, match="manifest append failed") as exc_info:
        _append_with_fsync(path, payload)
    # __cause__ is DiskFull -- exposed for diagnostic logging without
    # breaking the visible-class contract that reconciliation needs.
    assert isinstance(exc_info.value.__cause__, DiskFull)


@pytest.mark.asyncio
async def test_jsonl_manifest_record_propagates_enospc_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end via record(): the use case will see ManifestInconsistent
    (visible class) and run reconciliation on next startup."""
    manifest_path = tmp_path / "manifest.jsonl"
    failures_path = tmp_path / "failures.jsonl"

    def _write_enospc(_fd: int, _data: bytes) -> int:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(
        "shokz.adapters.outbound.jsonl_manifest.os.write", _write_enospc
    )

    manifest = JsonlManifest(manifest_path=manifest_path, failures_path=failures_path)
    entry = ManifestEntry(
        schema_version=1,
        source="youtube",
        track_id="abc",
        original_title="t",
        filename_stem="t",
        mp3_path="t.mp3",
        bitrate_kbps=64,
        duration_s=120.0,
        downloaded_at="2026-04-27T12:00:00Z",
    )
    with pytest.raises(ManifestInconsistent):
        await manifest.record(entry)


def test_jsonl_manifest_non_enospc_oserror_propagates_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A different OSError (e.g. permission denied) is NOT translated."""
    path = tmp_path / "manifest.jsonl"

    def _write_eacces(_fd: int, _data: bytes) -> int:
        raise OSError(errno.EACCES, "permission denied")

    monkeypatch.setattr(
        "shokz.adapters.outbound.jsonl_manifest.os.write", _write_eacces
    )

    with pytest.raises(OSError, match="permission"):
        _append_with_fsync(path, {"x": 1})
