"""VideoSourcePort — abstracts URL resolution and raw audio download."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from shokz.domain.models import PlaylistInfo, RawDownload, Track


@runtime_checkable
class VideoSourcePort(Protocol):
    """Resolve a URL to track metadata, then fetch its raw audio stream."""

    name: str

    def can_handle(self, url: str) -> bool:
        """Return True if this source recognizes the URL pattern."""

    async def resolve(self, url: str) -> Track:
        """Fetch metadata only — no bytes downloaded."""

    async def resolve_playlist(self, url: str) -> PlaylistInfo | None:
        """If url is a playlist: return PlaylistInfo (title + per-item URLs).

        Returns None if the URL is NOT a playlist (caller should treat as
        single-video). Sprint 5 review F3: if the URL is playlist-shaped but
        yt-dlp returns no playlist marker, raise DownloadFailed instead of
        silently returning None.
        """

    async def download_audio(
        self,
        track: Track,
        dest_dir: Path,
    ) -> RawDownload:
        """Download the best available audio stream into dest_dir.

        The returned path lives inside dest_dir (typically `downloads/.tmp/`).
        Sprint 1 uses no-progress mode; Sprint 6 wires the ProgressReporterPort.
        """
