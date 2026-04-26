"""SkipExistingPolicy -- decide whether a track is already done (Sprint 4.5).

Decision rule: skip iff BOTH the manifest has an entry for (source, track_id)
AND the recorded mp3_path actually exists on disk. If either is missing, we
re-download (manifest-only is not enough -- Sprint 4.5 spec scenario).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from shokz.application.ports.outbound.filesystem import FileSystemPort
from shokz.application.ports.outbound.manifest import ManifestPort


class SkipDecision(StrEnum):
    SKIPPED = "skipped"
    RE_DOWNLOAD = "re_download"


@dataclass(frozen=True, slots=True)
class SkipExistingResult:
    decision: SkipDecision
    existing_path: Path | None  # set when SKIPPED


@dataclass(frozen=True, slots=True)
class SkipExistingPolicy:
    """Pure(-ish) decision class. Reads manifest + filesystem; no mutation."""

    manifest: ManifestPort
    filesystem: FileSystemPort
    output_dir: Path

    async def check(self, source: str, track_id: str) -> SkipExistingResult:
        entry = await self.manifest.find_by_track(source, track_id)
        if entry is None:
            return SkipExistingResult(decision=SkipDecision.RE_DOWNLOAD, existing_path=None)

        mp3_path = self.output_dir / entry.mp3_path
        if not self.filesystem.exists(mp3_path):
            # Manifest stale: file was manually deleted or crashed mid-write.
            return SkipExistingResult(decision=SkipDecision.RE_DOWNLOAD, existing_path=None)

        return SkipExistingResult(decision=SkipDecision.SKIPPED, existing_path=mp3_path)
