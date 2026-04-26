"""JsonlManifest -- append-only JSONL with fsync(fd) + fsync(dir).

Sprint 4 DoD: every record() call MUST flush, fsync the file's fd, AND
fsync its parent dir. Silent-failure-hunter F3 from v0.2.0 review:
without the dir fsync, the appended bytes can be durable while the
directory entry update (file size, mtime) is not -- the row appears in
the next read but disappears on power-cut.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict
from pathlib import Path

from shokz.application.ports.outbound.manifest import ManifestPort
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

    async def _append(self, path: Path, payload: dict[str, object]) -> None:
        async with self._lock:
            await asyncio.to_thread(_append_with_fsync, path, payload)


def _append_with_fsync(path: Path, payload: dict[str, object]) -> None:
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    # Open with low-level os to get a fd we can fsync.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    # And fsync the parent dir so the (possibly newly-grown) entry is durable.
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    _log.debug("manifest append + dual-fsync: %s (+%d bytes)", path, len(line))
