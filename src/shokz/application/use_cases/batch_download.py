"""BatchDownloadUseCase — Sprint 1 slim version.

Orchestrates: resolve -> download_audio -> encode -> move-to-final, with
bounded asyncio concurrency. Per-track failures are isolated.

Out of scope for Sprint 1 (deferred per docs/sprints/sprint-1.md):
  - Manifest, skip-existing, retry, atomic durability, signal handling,
    title-based filenames, configuration. All hard-coded with sensible defaults.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from shokz.application.ports.outbound.encoder import AudioEncoderPort
from shokz.application.ports.outbound.progress import ProgressReporterPort
from shokz.application.ports.outbound.video_source import VideoSourcePort
from shokz.domain.errors import ShokzError
from shokz.domain.models import AudioSpec, TrackResult, TrackStatus
from shokz.observability.logging import set_track_id

_log = logging.getLogger("shokz.usecase.batch_download")


@dataclass(frozen=True, slots=True)
class BatchDownloadInput:
    urls: tuple[str, ...]
    output_dir: Path
    spec: AudioSpec
    concurrency: int = 3
    keep_raw: bool = False


@dataclass(frozen=True, slots=True)
class BatchDownloadResult:
    results: tuple[TrackResult, ...]
    elapsed_s: float

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.results if r.status is TrackStatus.SUCCESS)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status is TrackStatus.FAILED)


class BatchDownloadUseCase:
    """Resolve N URLs and produce N MP3s in output_dir, bounded concurrency."""

    def __init__(
        self,
        sources: tuple[VideoSourcePort, ...],
        encoder: AudioEncoderPort,
        progress: ProgressReporterPort,
    ) -> None:
        if not sources:
            raise ValueError("at least one VideoSourcePort required")
        self._sources = sources
        self._encoder = encoder
        self._progress = progress

    async def execute(self, inp: BatchDownloadInput) -> BatchDownloadResult:
        tmp_dir = inp.output_dir / ".tmp"
        inp.output_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        sem = asyncio.Semaphore(inp.concurrency)
        started = time.monotonic()

        async def bounded(url: str) -> TrackResult:
            async with sem:
                return await self._process_one(url, inp.output_dir, tmp_dir, inp.spec, inp.keep_raw)

        results = await asyncio.gather(*(bounded(u) for u in inp.urls))
        return BatchDownloadResult(results=tuple(results), elapsed_s=time.monotonic() - started)

    async def _process_one(
        self,
        url: str,
        output_dir: Path,
        tmp_dir: Path,
        spec: AudioSpec,
        keep_raw: bool,
    ) -> TrackResult:
        started = time.monotonic()
        try:
            source = self._select_source(url)
        except ValueError as e:
            _log.warning("no source can handle: %s — %s", url, e)
            self._progress.finish(track_id=url, status=TrackStatus.FAILED, message=str(e))
            return TrackResult(
                track=None,
                status=TrackStatus.FAILED,
                final_path=None,
                error=str(e),
                elapsed_s=time.monotonic() - started,
            )
        try:
            track = await source.resolve(url)
        except ShokzError as e:
            _log.warning("resolve failed: %s — %s", url, e)
            self._progress.finish(track_id=url, status=TrackStatus.FAILED, message=str(e))
            return TrackResult(
                track=None,
                status=TrackStatus.FAILED,
                final_path=None,
                error=f"resolve failed: {e}",
                elapsed_s=time.monotonic() - started,
            )

        set_track_id(track.id)
        try:
            self._progress.start(track_id=track.id, label=track.title)

            raw = await source.download_audio(track, dest_dir=tmp_dir)

            # Sprint 1 filename: {video_id}.mp3 — Sprint 2 swaps to title-based.
            partial = tmp_dir / f"{track.id}.mp3.partial"
            final = output_dir / f"{track.id}.mp3"

            await self._encoder.encode(raw.path, partial, spec)

            # Atomic move (full crash-safety + fsync comes Sprint 4).
            os.replace(partial, final)

            if not keep_raw:
                raw.path.unlink(missing_ok=True)

            self._progress.finish(track_id=track.id, status=TrackStatus.SUCCESS)
            return TrackResult(
                track=track,
                status=TrackStatus.SUCCESS,
                final_path=final,
                error=None,
                elapsed_s=time.monotonic() - started,
            )
        except ShokzError as e:
            _log.warning("download/encode failed: %s — %s", track.id, e)
            self._progress.finish(track_id=track.id, status=TrackStatus.FAILED, message=str(e))
            return TrackResult(
                track=track,
                status=TrackStatus.FAILED,
                final_path=None,
                error=str(e),
                elapsed_s=time.monotonic() - started,
            )
        finally:
            set_track_id(None)

    def _select_source(self, url: str) -> VideoSourcePort:
        for s in self._sources:
            if s.can_handle(url):
                return s
        raise ValueError(f"no source can handle URL: {url}")
