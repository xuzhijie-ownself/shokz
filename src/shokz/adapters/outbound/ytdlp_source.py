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

from shokz.domain.errors import (
    AuthRequired,
    DownloadFailed,
    FormatUnavailable,
    NetworkError,
    RateLimited,
    ShokzError,
    SourceUnavailable,
)
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


# Sprint 7 §7.1 Error Translation Table.
#
# Substring patterns are matched case-insensitively against the yt-dlp
# error message (or subprocess stderr tail). Order is LOAD-BEARING --
# evaluated top-to-bottom, first-match-wins (Sprint 7 GAN C5). Terminal
# errors come FIRST so an AuthRequired hidden behind a 429 surface ("Sign
# in to confirm your age... HTTP Error 429") classifies as AuthRequired
# (no retry) and not RateLimited (3 retries pointlessly).
#
# When NO pattern matches, _classify_message returns DownloadFailed with
# a WARNING log carrying the FULL raw message (not truncated) so a future
# §7.1 table extension can grep yesterday's logs for unclassified shapes.
_CLASSIFICATION_TABLE: Final[tuple[tuple[tuple[str, ...], type[ShokzError]], ...]] = (
    # AuthRequired -- terminal: cookies / region / sign-in needed
    (
        (
            "sign in to confirm your age",
            "sign in to confirm you're not a bot",
            "this video is not available in your country",
            "is not available in your country",  # prefix-less variant (some extractors)
            "members-only content",
            "this video is private",
        ),
        AuthRequired,
    ),
    # FormatUnavailable -- terminal: format menu won't change between attempts
    (
        (
            "requested format is not available",
            "requested format not available",
            "no audio formats found",
        ),
        FormatUnavailable,
    ),
    # SourceUnavailable -- terminal: video deleted / private / 404 / extractor-side
    (
        (
            "private video",
            "video unavailable",
            "this video has been removed",
            "removed by the uploader",
            "video has been deleted",
            "this content is not available",
            "premiere will begin shortly",  # video exists but unreachable
            "live stream recording not available",
            "failed to extract any player response",  # extractor failure
            "unable to extract initial player response",
        ),
        SourceUnavailable,
    ),
    # RateLimited -- retryable, LONG backoff
    (
        (
            "http error 429",
            "too many requests",
        ),
        RateLimited,
    ),
    # NetworkError -- retryable, SHORT backoff. Catches transient 5xx
    # and connection-level hiccups. Substring "http error 5" matches all
    # of 500..599 (acceptable: each is a server-side transient).
    (
        (
            "http error 5",
            "connection reset",
            "connection refused",
            "name or service not known",
            "temporary failure in name resolution",
            "read operation timed out",
        ),
        NetworkError,
    ),
)


def _classify_message(msg: str) -> ShokzError:
    """Translate a yt-dlp error message (or subprocess stderr line) to a
    domain error per §7.1. Sprint 7 GAN C1: applied at THREE call sites
    (resolve, resolve_playlist, download_audio stderr-tail) so the §7.1
    classifier fires for ALL yt-dlp failure paths, not just metadata.

    First-match-wins by table order (terminal errors first, per C5).
    Unmatched messages default to DownloadFailed AND log a WARNING with
    the FULL raw message so §7.1 drift is visible in logs.
    """
    lowered = msg.lower()
    for patterns, error_class in _CLASSIFICATION_TABLE:
        if any(p in lowered for p in patterns):
            return error_class(msg)
    _log.warning(
        "unclassified yt-dlp error -- please report to extend §7.1: %s", msg
    )
    return DownloadFailed(msg)


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
            # Sprint 7 C1: route through the §7.1 classifier so 429 / auth /
            # format errors during metadata extract get the right class
            # instead of a bare DownloadFailed.
            raise _classify_message(str(e)) from e

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
            # Sprint 7 C1 / U7: same classifier as resolve() -- fixes the
            # pre-Sprint-7 copy-paste drift where this site missed
            # several SourceUnavailable patterns and the AuthRequired/
            # RateLimited cases entirely.
            raise _classify_message(str(e)) from e

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
            # Phase 6 GAN MED#3: log the FULL stderr blob (not just tail[0])
            # so misclassifications can be diagnosed from logs without
            # re-running with --verbose.
            _log.warning(
                "yt-dlp exit %s for %s: %s",
                proc.returncode,
                track.id,
                stderr or "(no stderr)",
            )
            # Sprint 7 GAN review HIGH#2: short-circuit empty-stderr to
            # avoid a false-positive "unclassified §7.1" WARNING on every
            # exit-no-stderr case (which is NOT a §7.1 drift signal).
            if not stderr:
                raise DownloadFailed(
                    f"yt-dlp exit {proc.returncode} (no stderr) for {track.id}"
                )
            # Sprint 7 C1 (THE no-op-fix) + GAN review HIGH#1: classify the
            # FULL stderr blob, not just tail[0]. yt-dlp often emits the
            # actionable error on line N-1 and a generic advisory on the
            # final line -- classifying tail[0] alone would miss the auth
            # / 429 signal and burn a wrong retry. Substring matching on
            # the full blob is the most-robust correct fix.
            raise _classify_message(stderr)

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
