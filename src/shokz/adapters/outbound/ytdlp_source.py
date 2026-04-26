"""YouTubeSource — VideoSourcePort backed by yt-dlp.

Per plan §11: resolve uses the yt_dlp Python module (typed dict, fast, no
subprocess parse), download uses subprocess (process isolation, robust under
asyncio.gather concurrency).

EJS challenge solver is enabled by default via --remote-components ejs:github
(plan §11; first run downloads the solver from GitHub and caches it).
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlparse

from yt_dlp import YoutubeDL  # type: ignore[import-untyped]
from yt_dlp.utils import DownloadError as YtDlpDownloadError  # type: ignore[import-untyped]

from shokz.domain.errors import DownloadFailed, SourceUnavailable
from shokz.domain.models import PlaylistInfo, RawDownload, Track

_log = logging.getLogger("shokz.adapter.ytdlp")

_YOUTUBE_HOSTS: Final[frozenset[str]] = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}
)

_RESOLVE_OPTS: Final[dict[str, Any]] = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "extract_flat": False,
    "noplaylist": True,
}


class YouTubeSource:
    """yt-dlp-backed source for YouTube URLs."""

    name: str = "youtube"

    def __init__(self, ejs_source: str = "ejs:github") -> None:
        self._ejs_source = ejs_source

    def can_handle(self, url: str) -> bool:
        try:
            host = urlparse(url).netloc.lower()
        except ValueError:
            return False
        return host in _YOUTUBE_HOSTS

    async def resolve(self, url: str) -> Track:
        """Pull metadata via yt_dlp.YoutubeDL — single Python call, no subprocess."""

        def _extract() -> dict[str, Any]:
            with YoutubeDL(_RESOLVE_OPTS) as ydl:
                info = ydl.extract_info(url, download=False)
                if info is None:
                    raise SourceUnavailable(f"no info returned for {url}")
                # Playlists arrive as a dict with "entries"; Sprint 5 handles them.
                if "entries" in info and info.get("_type") == "playlist":
                    raise SourceUnavailable(f"playlist URLs not supported until Sprint 5: {url}")
                return dict(info)

        try:
            info = await asyncio.to_thread(_extract)
        except YtDlpDownloadError as e:
            msg = str(e)
            if any(p in msg for p in ("Private video", "Video unavailable", "removed", "deleted")):
                raise SourceUnavailable(msg) from e
            raise DownloadFailed(msg) from e

        return Track(
            id=str(info["id"]),
            title=str(info.get("title") or info["id"]),
            uploader=info.get("uploader"),
            duration_s=int(info["duration"]) if info.get("duration") is not None else None,
            source_url=str(info.get("webpage_url") or url),
            source_name=self.name,
        )

    async def resolve_playlist(self, url: str) -> PlaylistInfo | None:
        """Sprint 5: expand a YouTube playlist URL via extract_flat (no per-video metadata).

        Returns:
            tuple[str, ...]: per-item URLs if `url` is a playlist
            None: if the URL is a single video (not a playlist)
        """
        flat_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": True,
        }

        def _extract_flat() -> dict[str, Any]:
            with YoutubeDL(flat_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info is None:
                    raise SourceUnavailable(f"no info returned for {url}")
                return dict(info)

        try:
            info = await asyncio.to_thread(_extract_flat)
        except YtDlpDownloadError as e:
            msg = str(e)
            if any(p in msg for p in ("Private", "Video unavailable", "removed", "deleted")):
                raise SourceUnavailable(msg) from e
            raise DownloadFailed(msg) from e

        # F3: yt-dlp doesn't always set _type. If URL has 'list=' AND we got
        # a dict back, treat as playlist regardless of _type. If URL has no
        # list= and no _type='playlist', it's genuinely a single video.
        is_playlist_type = info.get("_type") == "playlist"
        has_entries = isinstance(info.get("entries"), list)
        url_is_playlist_shaped = "list=" in url
        if not (is_playlist_type or has_entries):
            if url_is_playlist_shaped:
                # Looks like a playlist URL but yt-dlp returned something weird
                # (likely throttled / partial). Fail loudly instead of silently
                # claiming "not a playlist".
                raise DownloadFailed(
                    f"playlist URL {url} returned no _type and no entries -- "
                    f"likely throttled or rate-limited (try again later)"
                )
            return None

        entries = info.get("entries") or []
        item_urls: list[str] = []
        for entry in entries:
            if entry is None:
                continue
            entry_url = entry.get("url") or entry.get("webpage_url")
            if entry_url:
                item_urls.append(str(entry_url))
        playlist_title = str(info.get("title") or info.get("id") or "playlist")
        return PlaylistInfo(title=playlist_title, item_urls=tuple(item_urls))

    async def download_audio(self, track: Track, dest_dir: Path) -> RawDownload:
        """Download bestaudio via yt-dlp subprocess.

        Returns the raw container file (.webm / .m4a). Encoding to MP3 is the
        encoder's job (separate port).
        """
        out_template = str(dest_dir / f"{track.id}.%(ext)s")
        cmd = [
            "yt-dlp",
            "-f",
            "bestaudio",
            "-o",
            out_template,
            "--no-playlist",
            "--no-progress",
            "--no-warnings",
            "--remote-components",
            self._ejs_source,
            "--print",
            "after_move:filepath",
            track.source_url,
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        stdout = stdout_b.decode(errors="replace").strip()
        stderr = stderr_b.decode(errors="replace").strip()

        if proc.returncode != 0:
            tail = stderr.splitlines()[-1:] or ["yt-dlp failed (no stderr)"]
            _log.warning("yt-dlp exit %s for %s: %s", proc.returncode, track.id, tail[0])
            raise DownloadFailed(tail[0])

        path_str = stdout.splitlines()[-1] if stdout else ""
        if not path_str:
            raise DownloadFailed("yt-dlp produced no filepath line")

        raw_path = Path(path_str)
        if not raw_path.exists() or raw_path.stat().st_size == 0:
            # Sprint 1 minimal sanity check; Sprint 4 expands per GAN audit.
            raise DownloadFailed(f"raw download missing or empty: {raw_path}")

        # Strip leading '.' from suffix; fall back to "unknown" if missing.
        container = re.sub(r"^\.", "", raw_path.suffix) or "unknown"
        return RawDownload(path=raw_path, container=container, track=track)
