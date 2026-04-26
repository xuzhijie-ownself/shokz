"""ExpandPlaylistUseCase -- Sprint 5: playlist URL -> per-item URLs."""

from __future__ import annotations

from dataclasses import dataclass

from shokz.application.ports.outbound.video_source import VideoSourcePort
from shokz.domain.errors import SourceUnavailable
from shokz.domain.models import PlaylistInfo


@dataclass(frozen=True, slots=True)
class ExpandPlaylistUseCase:
    """Resolve a playlist URL to a tuple of per-item URLs.

    Routing is identical to BatchDownloadUseCase: pick the source whose
    can_handle(url) returns True, then call resolve_playlist.
    """

    sources: tuple[VideoSourcePort, ...]

    async def execute(self, url: str) -> PlaylistInfo:
        for source in self.sources:
            if source.can_handle(url):
                info = await source.resolve_playlist(url)
                if info is None:
                    raise SourceUnavailable(
                        f"URL is not a playlist: {url}; use `shokz download` for single videos"
                    )
                return info
        raise ValueError(f"no source can handle URL: {url}")
