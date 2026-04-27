"""Sprint 8 Phase 2: FileLockPolicy unit tests.

Drives the 5-step classification on lock contention:
  1. corrupt JSON meta -> StaleLock (carries raw bytes)
  2. dead PID per psutil.NoSuchProcess -> StaleLock
  3. PermissionError on os.kill -> LockOwnerUnknown
  4. PID alive but create_time mismatch -> StaleLock (PID reused)
  5. PID alive AND start_time matches -> AnotherRunInProgress

Plus happy-path: acquire writes meta atomically, release removes it.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import filelock  # type: ignore[import-untyped]
import psutil  # type: ignore[import-untyped]
import pytest

from shokz.application.policies.file_lock import (
    FileLockPolicy,
    _classify_holder,
    _meta_path_for,
    _read_meta,
)
from shokz.domain.errors import (
    AnotherRunInProgress,
    LockOwnerUnknown,
    StaleLock,
)

# --- happy path ----------------------------------------------------------


def test_acquire_writes_meta_with_current_pid_and_start_time(tmp_path: Path) -> None:
    """Sprint 8 happy path: acquiring writes a sibling .meta JSON."""
    lock_path = tmp_path / "locks" / "shokz.lock"
    policy = FileLockPolicy(lock_path, timeout_s=1.0)
    with policy:
        meta_path = _meta_path_for(lock_path)
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta["pid"] == os.getpid()
        # Match psutil.Process(self).create_time() within tight tolerance.
        actual_start = psutil.Process(os.getpid()).create_time()
        assert abs(meta["started_at"] - actual_start) < 0.1
        assert "T" in meta["iso_started"]  # ISO-8601-ish


def test_release_removes_meta(tmp_path: Path) -> None:
    """Best-effort cleanup on __exit__."""
    lock_path = tmp_path / "locks" / "shokz.lock"
    with FileLockPolicy(lock_path, timeout_s=1.0):
        assert _meta_path_for(lock_path).exists()
    # After context exit, meta should be gone.
    assert not _meta_path_for(lock_path).exists()


def test_meta_written_atomically_via_os_replace(tmp_path: Path) -> None:
    """No partial JSON visible during write -- os.replace is atomic."""
    lock_path = tmp_path / "locks" / "shokz.lock"
    with FileLockPolicy(lock_path, timeout_s=1.0):
        meta_path = _meta_path_for(lock_path)
        # The .tmp sibling used during write must not linger after replace.
        assert not meta_path.with_suffix(meta_path.suffix + ".tmp").exists()


# --- step 1: corrupt JSON meta -> StaleLock + raw_meta_bytes ------------


def test_corrupt_meta_json_raises_stale_lock_with_raw_bytes(tmp_path: Path) -> None:
    """Sprint 8 GAN B2c: truncated meta JSON -> StaleLock with bytes."""
    meta_path = tmp_path / "shokz.lock.meta"
    truncated = b'{"pid": 1234, "started_at": 1714137'  # truncated mid-write
    meta_path.write_bytes(truncated)
    with pytest.raises(StaleLock) as exc_info:
        _read_meta(meta_path)
    assert exc_info.value.raw_meta_bytes == truncated
    assert "corrupt" in str(exc_info.value).lower()


def test_missing_meta_file_raises_stale_lock(tmp_path: Path) -> None:
    """Lock present but meta missing -> StaleLock (prior SIGKILL before write)."""
    meta_path = tmp_path / "nonexistent.meta"
    with pytest.raises(StaleLock, match="meta file"):
        _read_meta(meta_path)


# --- step 2: dead PID -> StaleLock ---------------------------------------


def test_dead_pid_raises_stale_lock(tmp_path: Path) -> None:
    """Sprint 8 step 2: psutil.NoSuchProcess -> StaleLock."""
    # Pick a PID guaranteed not to exist (PIDs > 4M are reserved/unused).
    dead_pid = 4_000_001
    err = _classify_holder(
        meta_pid=dead_pid,
        meta_started_at=time.time(),
        meta_iso="2026-04-27T12:00:00Z",
        lock_path=tmp_path / "shokz.lock",
    )
    assert isinstance(err, StaleLock)
    assert f"PID {dead_pid}" in str(err)


# --- step 3: PermissionError -> LockOwnerUnknown -------------------------


def test_permission_error_on_signal_raises_lock_owner_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sprint 8 step 3: alive but PermissionError on os.kill -> LockOwnerUnknown."""
    my_pid = os.getpid()

    def _kill_perm_denied(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("not allowed to signal")

    # psutil.Process(my_pid) succeeds (we're alive); os.kill fails.
    monkeypatch.setattr(
        "shokz.application.policies.file_lock.os.kill", _kill_perm_denied
    )
    err = _classify_holder(
        meta_pid=my_pid,
        meta_started_at=psutil.Process(my_pid).create_time(),
        meta_iso="2026-04-27T12:00:00Z",
        lock_path=tmp_path / "shokz.lock",
    )
    assert isinstance(err, LockOwnerUnknown)
    assert f"PID {my_pid}" in str(err)
    assert "another user" in str(err)


# --- step 4: PID reuse (start_time mismatch) -> StaleLock ---------------


def test_pid_reuse_start_time_mismatch_raises_stale_lock(tmp_path: Path) -> None:
    """Sprint 8 step 4: alive PID + recorded start_time differs -> StaleLock."""
    my_pid = os.getpid()
    # Record a start time 1 hour ago -- definitely not THIS process's start.
    fake_old_start = time.time() - 3600.0
    err = _classify_holder(
        meta_pid=my_pid,
        meta_started_at=fake_old_start,
        meta_iso="2025-04-27T12:00:00Z",
        lock_path=tmp_path / "shokz.lock",
    )
    assert isinstance(err, StaleLock)
    assert "reused" in str(err).lower()


# --- step 5: matching PID + start_time -> AnotherRunInProgress -----------


def test_alive_pid_matching_start_time_raises_another_run_in_progress(
    tmp_path: Path,
) -> None:
    """Sprint 8 step 5: same PID, same start_time -> genuine contention."""
    my_pid = os.getpid()
    actual_start = psutil.Process(my_pid).create_time()
    err = _classify_holder(
        meta_pid=my_pid,
        meta_started_at=actual_start,
        meta_iso="2026-04-27T12:00:00Z",
        lock_path=tmp_path / "shokz.lock",
    )
    assert isinstance(err, AnotherRunInProgress)
    assert f"PID {my_pid}" in str(err)


# --- contention end-to-end via two FileLockPolicy instances --------------


def test_second_acquire_with_dead_pid_meta_raises_stale_lock(
    tmp_path: Path,
) -> None:
    """End-to-end: write a stale meta, manually hold the underlying flock,
    confirm a second policy.__enter__ raises StaleLock."""
    lock_path = tmp_path / "locks" / "shokz.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # Hold the underlying filelock with a dead-PID meta beside it.
    held = filelock.FileLock(str(lock_path))
    held.acquire()
    try:
        meta = {
            "pid": 4_000_002,  # dead
            "started_at": time.time() - 60.0,
            "iso_started": "2026-04-27T12:00:00Z",
        }
        _meta_path_for(lock_path).write_text(json.dumps(meta))
        # Second acquirer should classify as StaleLock.
        policy = FileLockPolicy(lock_path, timeout_s=0.1)
        with pytest.raises(StaleLock), policy:
            pass  # pragma: no cover - should not enter
    finally:
        held.release()


# --- Phase 2 GAN review fixes (HIGH#1, HIGH#2, MED#3, MED#4) ------------


def test_write_meta_raises_runtime_error_on_own_pid_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 2 GAN HIGH#1: if psutil.NoSuchProcess fires on our own PID
    (impossible by construction), we MUST crash with RuntimeError rather
    than silently write a fallback time.time() value that would make every
    subsequent reader misclassify as PID-reuse."""

    class _RaisingProcess:
        def __init__(self, _pid: int) -> None:
            raise psutil.NoSuchProcess(_pid)

    monkeypatch.setattr(
        "shokz.application.policies.file_lock.psutil.Process", _RaisingProcess
    )
    lock_path = tmp_path / "locks" / "shokz.lock"
    with pytest.raises(RuntimeError, match="impossible"), FileLockPolicy(
        lock_path, timeout_s=1.0
    ):
        pass  # pragma: no cover


def test_release_happens_before_meta_unlink(tmp_path: Path) -> None:
    """Phase 2 GAN MED#3: __exit__ releases the flock BEFORE unlinking meta.
    The reverse order leaves a window where the lock is held but meta is
    missing -- concurrent reader sees 'meta gone' and raises StaleLock
    against an alive holder.

    Test by inspection: after exit, the flock is releasable by another
    holder (ie was actually released). Hard to test the ordering directly
    without instrumentation; we rely on code-review + the spec assertion."""
    lock_path = tmp_path / "locks" / "shokz.lock"
    with FileLockPolicy(lock_path, timeout_s=1.0):
        pass
    # After exit, meta should be gone.
    assert not _meta_path_for(lock_path).exists()
    # And another acquire should succeed immediately.
    with FileLockPolicy(lock_path, timeout_s=0.5):
        pass


def test_meta_write_failure_releases_flock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 2 GAN MED#4: an OSError during _write_meta MUST release the
    flock (otherwise it leaks for the lifetime of the FileLock object)."""
    lock_path = tmp_path / "locks" / "shokz.lock"
    # Force os.replace to raise OSError (simulating disk-full).
    real_replace = os.replace

    def _explode_on_replace(src: str, dst: str) -> None:
        raise OSError("simulated ENOSPC")

    monkeypatch.setattr(
        "shokz.application.policies.file_lock.os.replace", _explode_on_replace
    )
    with pytest.raises(OSError, match="simulated"), FileLockPolicy(lock_path, timeout_s=1.0):
        pass  # pragma: no cover

    # Restore os.replace and confirm a fresh acquire succeeds (proves the
    # leaked-flock scenario didn't happen).
    monkeypatch.setattr(
        "shokz.application.policies.file_lock.os.replace", real_replace
    )
    with FileLockPolicy(lock_path, timeout_s=0.5):
        pass


def test_second_acquire_with_alive_self_pid_raises_another_run_in_progress(
    tmp_path: Path,
) -> None:
    """End-to-end: write meta with our own (alive) PID + matching start_time;
    second acquire raises AnotherRunInProgress."""
    lock_path = tmp_path / "locks" / "shokz.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    held = filelock.FileLock(str(lock_path))
    held.acquire()
    try:
        my_pid = os.getpid()
        meta = {
            "pid": my_pid,
            "started_at": psutil.Process(my_pid).create_time(),
            "iso_started": "2026-04-27T12:00:00Z",
        }
        _meta_path_for(lock_path).write_text(json.dumps(meta))
        with pytest.raises(AnotherRunInProgress), FileLockPolicy(lock_path, timeout_s=0.1):
            pass  # pragma: no cover
    finally:
        held.release()
