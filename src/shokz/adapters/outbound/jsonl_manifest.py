"""JsonlManifest -- append-only JSONL with fsync(fd) + fsync(dir).

Sprint 4 DoD: every record() call MUST flush, fsync the file's fd, AND
fsync its parent dir. Silent-failure-hunter F3 from v0.2.0 review:
without the dir fsync, the appended bytes can be durable while the
directory entry update (file size, mtime) is not -- the row appears in
the next read but disappears on power-cut.
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import asdict
from pathlib import Path
from typing import Any, TypeVar

from shokz.application.ports.outbound.manifest import ManifestPort
from shokz.domain.errors import DiskFull, ManifestInconsistent, ManifestReadError
from shokz.domain.models import FailureEntry, ManifestEntry

_log = logging.getLogger("shokz.adapter.manifest")


class JsonlManifest(ManifestPort):
    """Append-only JSONL ManifestPort with fsync chain.

    SF-1: asyncio.Lock serialises only WITHIN one process. Sprint 8 will
    add a cross-process filelock; for v0.4.0 SINGLE-PROCESS-SAFE ONLY.

    SF-7: parent dir mkdir + grandparent fsync run ONCE at __init__.
    """

    def __init__(self, manifest_path: Path, failures_path: Path) -> None:
        self._manifest_path = manifest_path
        self._failures_path = failures_path
        self._lock = asyncio.Lock()
        for path in (manifest_path, failures_path):
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                grandparent_fd = os.open(path.parent.parent, os.O_RDONLY)
                try:
                    os.fsync(grandparent_fd)
                finally:
                    os.close(grandparent_fd)
            except OSError:
                pass

    async def record(self, entry: ManifestEntry) -> None:
        await self._append(self._manifest_path, asdict(entry))

    async def record_failure(self, entry: FailureEntry) -> None:
        await self._append(self._failures_path, asdict(entry))

    async def find_by_track(self, source: str, track_id: str) -> ManifestEntry | None:
        """Linear scan; returns the LAST matching entry (append-only -> latest wins).

        Sprint 4.5: linear scan is fine for ~1000 entries (< 50ms). SQLite
        backend is v2 territory.
        """
        latest: ManifestEntry | None = None
        async for entry in self.iter_all():
            if entry.source == source and entry.track_id == track_id:
                latest = entry
        return latest

    async def iter_all(self) -> AsyncIterator[ManifestEntry]:
        # Sprint 8.5 Phase A GAN HIGH#2: wrap exists() so an EPERM/EIO
        # at stat-time surfaces as ManifestReadError (not a raw OSError
        # routed through the unexpected-error catch-all).
        try:
            if not self._manifest_path.exists():
                return
        except OSError as e:
            raise ManifestReadError(
                f"cannot stat {self._manifest_path}: {e}; "
                "check file permissions and existence"
            ) from e
        # Read in a thread to avoid blocking the event loop on large manifests.
        # Sprint 8.5 C1: _read_jsonl wraps OSError as ManifestReadError.
        rows = await asyncio.to_thread(_read_jsonl, self._manifest_path)
        for row in rows:
            entry = _safe_construct(ManifestEntry, row, self._manifest_path)
            if entry is not None:
                yield entry

    async def iter_failures(self) -> AsyncIterator[FailureEntry]:
        """Sprint 8.5: async iterator over failures.jsonl, in append order.

        Mirrors iter_all. Caller (RetryFailedUseCase) is responsible for
        the lock-acquired-before-iter ordering (spec C4); this method
        does no locking on its own.
        """
        try:
            if not self._failures_path.exists():
                return
        except OSError as e:
            raise ManifestReadError(
                f"cannot stat {self._failures_path}: {e}; "
                "check file permissions and existence"
            ) from e
        rows = await asyncio.to_thread(_read_jsonl, self._failures_path)
        for row in rows:
            entry = _safe_construct(FailureEntry, row, self._failures_path)
            if entry is not None:
                yield entry

    async def _append(self, path: Path, payload: dict[str, object]) -> None:
        async with self._lock:
            await asyncio.to_thread(_append_with_fsync, path, payload)


def _append_with_fsync(path: Path, payload: dict[str, object]) -> None:
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    # Open with low-level os to get a fd we can fsync.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        except OSError as e:
            if e.errno == errno.ENOSPC:
                # Sprint 8b GAN M3: ENOSPC during manifest append is a
                # specific kind of inconsistency -- the final mp3 has
                # already landed (Sprint 4 atomic-move runs BEFORE this),
                # but the manifest row didn't. Reconciliation will catch
                # the orphan file. Raise ManifestInconsistent (the
                # recoverable signal) FROM DiskFull (the underlying cause).
                raise ManifestInconsistent(
                    f"manifest append failed for {path} -- final file landed "
                    "but manifest row did not; run `shokz library verify`"
                ) from DiskFull(f"disk full during manifest append at {path}")
            raise
    finally:
        os.close(fd)
    # And fsync the parent dir so the (possibly newly-grown) entry is durable.
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    _log.debug("manifest append + dual-fsync: %s (+%d bytes)", path, len(line))


_T = TypeVar("_T")


def _safe_construct(
    cls: type[_T], row: dict[str, Any], path: Path
) -> _T | None:
    """Sprint 8.5 Phase A GAN HIGH#3: build a frozen dataclass from a JSONL
    row, returning None + WARNING on any TypeError (unknown / missing /
    typo'd field). Without this, a partial-write that produces a complete
    JSON object with truncated content (e.g. `{}` or a row from a future
    schema_version with extra keys) would raise an unhandled TypeError
    from the iterator and route through the CLI's unexpected-error
    catch-all. The WARNING also surfaces silent schema drift.
    """
    try:
        return cls(**row)
    except TypeError as e:
        _log.warning(
            "skipping structurally invalid row in %s: %s (row keys: %s)",
            path, e, sorted(row.keys()),
        )
        return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read all rows from a JSONL file, skipping malformed lines with WARNING.

    Sprint 8.5 C1: OSError (e.g. EPERM, EIO) wrapped as ManifestReadError so
    the CLI can surface a named, actionable message instead of routing
    through the unexpected-error catch-all.

    Sprint 8.5 U1: malformed-row log was DEBUG; raised to WARNING so
    partial-write races during concurrent appends are visible in default
    log output (the row IS silently dropped from the result set; making
    the log level WARNING is the user's only signal that this happened).
    """
    rows: list[dict[str, object]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for n, raw_line in enumerate(f, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    _log.warning(
                        "skipping malformed jsonl row %d in %s "
                        "(possible partial write from concurrent appender)",
                        n, path,
                    )
    except OSError as e:
        raise ManifestReadError(
            f"cannot read {path}: {e}; check file permissions and existence"
        ) from e
    return rows
