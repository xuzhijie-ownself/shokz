"""VideoSourcePort — abstracts URL resolution and raw audio download."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from shokz.domain.models import RawDownload, Track


@runtime_checkable
class VideoSourcePort(Protocol):
    """Resolve a URL to track metadata, then fetch its raw audio stream."""

    name: str

    def can_handle(self, url: str) -> bool:
        """Return True if this source recognizes the URL pattern."""

    async def resolve(self, url: str) -> Track:
        """Fetch metadata only — no bytes downloaded."""

    async def download_audio(
        self,
        track: Track,
        dest_dir: Path,
    ) -> RawDownload:
        """Download the best available audio stream into dest_dir.

        The returned path lives inside dest_dir (typically `downloads/.tmp/`).
        Sprint 1 uses no-progress mode; Sprint 6 wires the ProgressReporterPort.
        """
