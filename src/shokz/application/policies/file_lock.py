"""FileLockPolicy -- Sprint 8 cross-process advisory lock.

Wraps `filelock.FileLock` with a sibling `.shokz.lock.meta` JSON file that
embeds (pid, started_at, iso_started) so the 5-step classification on
contention can distinguish:

  1. corrupt JSON meta (truncated by SIGKILL) -> StaleLock
  2. PID dead per psutil.NoSuchProcess        -> StaleLock
  3. PID alive but PermissionError on signal  -> LockOwnerUnknown
  4. PID alive but create_time mismatch       -> StaleLock (PID reused)
  5. PID alive AND start_time matches         -> AnotherRunInProgress

Usage (from CLI command, BEFORE asyncio.run -- Sprint 8 GAN M1):

    lock = FileLockPolicy(state_dir / "locks/shokz.lock", timeout_s=5.0)
    with lock:  # __enter__ acquires; raises StaleLock/Another/Owner on contention
        asyncio.run(use_case.execute(...))

Sprint 8 GAN B2: meta written via os.replace AFTER acquire (atomic).
Sprint 8 GAN L3: macOS/Linux only (os.kill(pid, 0) is unsupported on Win).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

import filelock
import psutil

from shokz.domain.errors import (
    AnotherRunInProgress,
    LockOwnerUnknown,
    StaleLock,
)

# Sprint 8 GAN L3: os.kill(pid, 0) is unsupported on Windows; project targets
# macOS/Linux per CLAUDE.md.
assert sys.platform != "win32", (
    "FileLockPolicy uses os.kill(pid, 0) for stale-PID detection; "
    "Windows is not supported (project targets macOS/Linux per CLAUDE.md)"
)

_log = logging.getLogger("shokz.policy.file_lock")

# Tolerance for matching the recorded meta `started_at` against the live
# `psutil.Process(pid).create_time()`. Small enough to detect PID reuse
# (process death + new PID assignment takes >> 2s in practice); large
# enough to absorb clock-skew jitter on the recorded timestamp.
_START_TIME_TOLERANCE_S: float = 2.0


def _meta_path_for(lock_path: Path) -> Path:
    """`/path/to/shokz.lock` -> `/path/to/shokz.lock.meta`."""
    return lock_path.with_suffix(lock_path.suffix + ".meta")


def _read_meta(meta_path: Path) -> tuple[int, float, str]:
    """Read and parse the sibling meta file. Returns (pid, started_at, iso).
    Raises StaleLock with raw bytes attached when JSON is corrupt (B2c)."""
    try:
        raw = meta_path.read_bytes()
    except FileNotFoundError as e:
        raise StaleLock(
            f"lock held but meta file {meta_path} missing -- prior shokz "
            "process likely SIGKILLed before write; remove "
            f"{meta_path.parent}/* lock files to proceed"
        ) from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        _log.warning(
            "lock meta corrupt at %s; raw bytes: %r", meta_path, raw[:512]
        )
        raise StaleLock(
            f"lock meta corrupt (truncated write); remove "
            f"{meta_path.parent}/* lock files to proceed",
            raw_meta_bytes=raw,
        ) from e
    return int(data["pid"]), float(data["started_at"]), str(data.get("iso_started", ""))


def _classify_holder(
    meta_pid: int,
    meta_started_at: float,
    meta_iso: str,
    lock_path: Path,
) -> Exception:
    """Apply the 5-step priority list (steps 2..5; step 1 was JSON-parse)."""
    # Step 2: dead PID per psutil
    try:
        proc = psutil.Process(meta_pid)
    except psutil.NoSuchProcess:
        return StaleLock(
            f"stale lock from dead PID {meta_pid}; "
            f"remove {lock_path} to proceed"
        )

    # Step 3: alive but cannot signal (other user / sandboxed)
    try:
        os.kill(meta_pid, 0)
    except PermissionError:
        return LockOwnerUnknown(
            f"lock holder PID {meta_pid} is alive but owned by another user; "
            "refusing to assume stale (would risk corrupting their run)"
        )
    except ProcessLookupError:
        # Race: alive at psutil.Process(...) check, dead by now.
        return StaleLock(
            f"lock holder PID {meta_pid} died between checks; "
            f"remove {lock_path} to proceed"
        )

    # Step 4: alive but start_time mismatch == PID reuse
    try:
        actual_start = proc.create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return StaleLock(
            f"lock holder PID {meta_pid} stat unavailable; "
            f"remove {lock_path} to proceed"
        )
    if abs(meta_started_at - actual_start) > _START_TIME_TOLERANCE_S:
        return StaleLock(
            f"PID {meta_pid} reused since previous shokz invocation "
            f"(meta start={meta_started_at:.2f}, actual={actual_start:.2f}); "
            f"remove {lock_path} to proceed"
        )

    # Step 5: same PID, same start_time -> genuine contention
    return AnotherRunInProgress(
        f"another shokz process is downloading to this output_dir "
        f"(PID {meta_pid}, started {meta_iso}, lock {lock_path})"
    )


class FileLockPolicy:
    """Sync context-manager wrapping filelock.FileLock + sibling meta JSON."""

    def __init__(self, lock_path: Path, timeout_s: float = 5.0) -> None:
        self.lock_path = lock_path
        self.timeout_s = timeout_s
        self._flock: filelock.FileLock | None = None

    def __enter__(self) -> Self:
        # Ensure parent dir exists (CLI may not have called mkdir yet).
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        flock = filelock.FileLock(str(self.lock_path), timeout=self.timeout_s)
        try:
            flock.acquire()
        except filelock.Timeout as e:
            # Phase 2 GAN HIGH#2: TOCTOU between filelock.Timeout and our
            # meta read -- the holder may have released JUST before our
            # timeout fired. Re-attempt acquire with timeout=0 once. If
            # it succeeds, the holder released; proceed normally. Only on
            # second failure do we read the meta + classify.
            try:
                flock.acquire(timeout=0)
            except filelock.Timeout:
                meta_path = _meta_path_for(self.lock_path)
                # _read_meta raises StaleLock with diagnostic on corrupt/missing.
                pid, started_at, iso = _read_meta(meta_path)
                raise _classify_holder(pid, started_at, iso, self.lock_path) from e
            # else: second-attempt acquire succeeded; fall through to meta write.

        # Acquired. Write meta atomically via os.replace.
        self._flock = flock
        self._write_meta()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # Phase 2 GAN MED#3: release the flock BEFORE unlinking meta. The
        # reverse order leaves a window where the lock is held but the meta
        # is missing -- any concurrent reader sees "meta gone" and raises
        # StaleLock against an alive holder. The corrected order: release
        # first, then unlink. If the process dies between those, the next
        # holder sees stale meta + dead-PID -> classified as StaleLock
        # (correct outcome).
        if self._flock is not None:
            self._flock.release()
            self._flock = None
        meta_path = _meta_path_for(self.lock_path)
        try:
            meta_path.unlink(missing_ok=True)
        except OSError:
            _log.warning("failed to remove lock meta %s", meta_path)

    def _write_meta(self) -> None:
        """Write sibling meta atomically via os.replace (Sprint 8 GAN B2a).

        Phase 2 GAN HIGH#1: own-PID psutil.NoSuchProcess is impossible by
        construction; we crash hard rather than write a corrupt time.time()
        value that would mis-classify as PID-reuse on every subsequent read.

        Phase 2 GAN MED#4: wrap in try/except OSError so a disk-full /
        permission-denied during meta write cleanly releases the flock
        instead of leaking it (this matters because Sprint 8's OWN disk
        guard sometimes fires on the same disk as the lock files)."""
        pid = os.getpid()
        try:
            started_at = psutil.Process(pid).create_time()
        except psutil.NoSuchProcess:  # pragma: no cover - we ARE this PID
            raise RuntimeError(
                f"psutil.NoSuchProcess on own PID {pid} -- this should be "
                "impossible; refusing to write a fallback time.time() value "
                "that would mis-classify as PID-reuse"
            ) from None
        meta = {
            "pid": pid,
            "started_at": started_at,
            "iso_started": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        meta_path = _meta_path_for(self.lock_path)
        tmp_path = meta_path.with_suffix(meta_path.suffix + ".tmp")
        try:
            tmp_path.write_text(json.dumps(meta))
            os.replace(tmp_path, meta_path)
        except OSError:
            # Cleanup our temp file; release the flock so we don't leak.
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)
            if self._flock is not None:
                self._flock.release()
                self._flock = None
            raise
