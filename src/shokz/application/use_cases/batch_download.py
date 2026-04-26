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
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeAlias

from shokz.application.policies.filename_resolver import FilenameResolver
from shokz.application.ports.outbound.encoder import AudioEncoderPort
from shokz.application.ports.outbound.filesystem import FileSystemPort
from shokz.application.ports.outbound.manifest import ManifestPort
from shokz.application.ports.outbound.progress import ProgressReporterPort
from shokz.application.ports.outbound.video_source import VideoSourcePort
from shokz.domain.errors import (
    EncodingFailed,
    ManifestInconsistent,
    NameAmbiguous,
    NameOutsideOutputDir,
    ShokzError,
    SourceFileCorrupt,
)
from shokz.domain.models import (
    AudioSpec,
    FailureEntry,
    ManifestEntry,
    Track,
    TrackResult,
    TrackStatus,
)
from shokz.observability.logging import set_track_id

_log = logging.getLogger("shokz.usecase.batch_download")

_ERROR_CLASS_MAP: dict[str, str] = {
    "SourceUnavailable": "SOURCE_UNAVAILABLE",
    "DownloadFailed": "DOWNLOAD_FAILED",
    "SourceFileCorrupt": "SOURCE_FILE_CORRUPT",
    "EncodingFailed": "ENCODING_FAILED",
    "FilenameCollision": "FILENAME_COLLISION",
    "NameOutsideOutputDir": "NAME_OUTSIDE_OUTPUT_DIR",
    "NameInvalid": "NAME_INVALID",
    "NameAmbiguous": "NAME_AMBIGUOUS",
    "ManifestInconsistent": "MANIFEST_INCONSISTENT",
}


def _stable_error_class(err: BaseException) -> str:
    return _ERROR_CLASS_MAP.get(type(err).__name__, "UNEXPECTED_ERROR")


# Sprint 4: integrity-check thresholds.
MIN_RAW_BYTES: int = 1024  # below this we treat as corrupt download
DURATION_TOLERANCE: float = 0.02  # encoded must be within 2% of source

# Sprint 2: factory so each invocation gets a resolver bound to the
# requested output_dir (which can vary per call via --output).
FilenameResolverFactory: TypeAlias = Callable[[Path], FilenameResolver]


@dataclass(frozen=True, slots=True)
class BatchDownloadInput:
    urls: tuple[str, ...]
    output_dir: Path
    spec: AudioSpec
    concurrency: int = 3
    keep_raw: bool = False
    name_override: str | None = None  # Sprint 2: --name flag for single URL


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
        filename_resolver_factory: FilenameResolverFactory,
        manifest: ManifestPort,
        filesystem: FileSystemPort,
    ) -> None:
        if not sources:
            raise ValueError("at least one VideoSourcePort required")
        self._sources = sources
        self._encoder = encoder
        self._progress = progress
        self._resolver_factory = filename_resolver_factory
        self._manifest = manifest
        self._filesystem = filesystem

    async def execute(self, inp: BatchDownloadInput) -> BatchDownloadResult:
        tmp_dir = inp.output_dir / ".tmp"
        inp.output_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # F6 (Sprint 2 silent-failure fix): reject symlinked output_dir.
        # If output_dir is a symlink, an attacker (or a misconfigured user) could
        # redirect writes outside the intended tree -- the resolver's traversal
        # guard would not catch this because both child.resolve() and
        # parent.resolve() collapse through the same symlink.
        if inp.output_dir.is_symlink():
            raise NameOutsideOutputDir(
                f"output directory {inp.output_dir} is a symlink; refusing to write through it"
            )

        # Sprint 2: --name only valid with exactly one URL.
        if inp.name_override is not None and len(inp.urls) != 1:
            raise NameAmbiguous(f"--name requires exactly one URL, got {len(inp.urls)}")

        # Build the per-run resolver from the configured output_dir.
        resolver = self._resolver_factory(inp.output_dir)

        sem = asyncio.Semaphore(inp.concurrency)
        started = time.monotonic()

        async def bounded(url: str) -> TrackResult:
            async with sem:
                return await self._process_one(
                    url,
                    inp.output_dir,
                    tmp_dir,
                    inp.spec,
                    inp.keep_raw,
                    resolver,
                    inp.name_override,
                )

        results = await asyncio.gather(*(bounded(u) for u in inp.urls))
        return BatchDownloadResult(results=tuple(results), elapsed_s=time.monotonic() - started)

    async def _process_one(
        self,
        url: str,
        output_dir: Path,
        tmp_dir: Path,
        spec: AudioSpec,
        keep_raw: bool,
        resolver: FilenameResolver,
        name_override: str | None,
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
            _log.warning("resolve failed: %s -- %s", url, e)
            self._progress.finish(track_id=url, status=TrackStatus.FAILED, message=str(e))
            await self._record_failure(url, None, None, e)
            return TrackResult(
                track=None,
                status=TrackStatus.FAILED,
                final_path=None,
                error=f"resolve failed: {e}",
                elapsed_s=time.monotonic() - started,
            )
        except Exception as e:  # Sprint 1 isolation; Sprint 7 narrows the taxonomy.
            _log.exception("unexpected resolve exception for %s", url)
            self._progress.finish(track_id=url, status=TrackStatus.FAILED, message=str(e))
            await self._record_failure(url, None, None, e)
            return TrackResult(
                track=None,
                status=TrackStatus.FAILED,
                final_path=None,
                error=f"resolve failed (unexpected): {e!r}",
                elapsed_s=time.monotonic() - started,
            )

        set_track_id(track.id)
        try:
            self._progress.start(track_id=track.id, label=track.title)

            raw = await source.download_audio(track, dest_dir=tmp_dir)

            # Sprint 4 integrity check #1: post-download size sanity.
            # yt-dlp can exit 0 with a 0-byte / truncated raw file (silent-
            # failure-hunter F1 from v0.2.0 plan review). Catch it BEFORE
            # ffmpeg silently produces a 0-byte mp3.
            if not raw.path.exists() or raw.path.stat().st_size < MIN_RAW_BYTES:
                size = raw.path.stat().st_size if raw.path.exists() else 0
                raise SourceFileCorrupt(
                    f"raw download for {track.id} is {size} bytes (< {MIN_RAW_BYTES})"
                )

            partial = tmp_dir / f"{track.id}.mp3.partial"
            await self._encoder.encode(raw.path, partial, spec)

            # Sprint 4 integrity check #2: post-encode duration tolerance.
            # NOTE (SF-6): yt-dlp duration is trusted; doesn't catch source-corrupt.
            measured_duration_s: float = 0.0
            if track.duration_s is not None:  # SF-2: explicit
                measured_duration_s = await self._encoder.probe_duration(partial)
                expected = float(track.duration_s)
                deviation = abs(measured_duration_s - expected) / expected
                if deviation > DURATION_TOLERANCE:
                    raise EncodingFailed(
                        f"encoded duration {measured_duration_s:.1f}s deviates "
                        f"{deviation * 100:.1f}% from source {expected:.1f}s "
                        f"(tolerance {DURATION_TOLERANCE * 100:.0f}%)"
                    )

            # Resolve the FINAL path immediately before atomic move, NOT before
            # the multi-second encode (Sprint 2 review R1 — TOCTOU shrink).
            final = resolver.resolve(
                track,
                name_override=name_override,
                exists=self._filesystem.exists,
            )
            if self._filesystem.exists(final):
                _log.warning(
                    "race: %s appeared between resolve() and atomic_move; "
                    "overwriting (Sprint 8 will block via filelock)",
                    final,
                )

            # Sprint 4 atomic protocol: os.replace + fsync(file) + fsync(dir).
            self._filesystem.atomic_move(partial, final)

            # SF-4: record manifest BEFORE removing raw.
            # py-rev Issue 1: record measured_duration_s (actual), not source-claimed.
            await self._manifest.record(
                _build_manifest_entry(track, final, output_dir, spec, measured_duration_s)
            )

            if not keep_raw:
                self._filesystem.remove(raw.path)

            self._progress.finish(track_id=track.id, status=TrackStatus.SUCCESS)
            return TrackResult(
                track=track,
                status=TrackStatus.SUCCESS,
                final_path=final,
                error=None,
                elapsed_s=time.monotonic() - started,
            )
        except ShokzError as e:
            _log.warning("download/encode failed: %s -- %s", track.id, e)
            self._progress.finish(track_id=track.id, status=TrackStatus.FAILED, message=str(e))
            await self._record_failure(track.source_url, track.source_name, track.id, e)
            return TrackResult(
                track=track,
                status=TrackStatus.FAILED,
                final_path=None,
                error=str(e),
                elapsed_s=time.monotonic() - started,
            )
        except Exception as e:  # Sprint 1 isolation; Sprint 7 narrows the taxonomy.
            _log.exception("unexpected download/encode exception for %s", track.id)
            self._progress.finish(track_id=track.id, status=TrackStatus.FAILED, message=str(e))
            await self._record_failure(track.source_url, track.source_name, track.id, e)
            return TrackResult(
                track=track,
                status=TrackStatus.FAILED,
                final_path=None,
                error=f"unexpected: {e!r}",
                elapsed_s=time.monotonic() - started,
            )
        finally:
            set_track_id(None)

    async def _record_failure(
        self,
        url: str,
        source_name: str | None,
        track_id: str | None,
        err: BaseException,
    ) -> None:

        try:
            await self._manifest.record_failure(
                FailureEntry(
                    schema_version=1,
                    source=source_name,
                    track_id=track_id,
                    url=url,
                    error_class=_stable_error_class(err),
                    error_message=str(err),
                    failed_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
            )
        except Exception:
            _log.exception("failed to write failure-log entry for %s", url)

    def _select_source(self, url: str) -> VideoSourcePort:
        for s in self._sources:
            if s.can_handle(url):
                return s
        raise ValueError(f"no source can handle URL: {url}")


def encoded_duration_or(track: Track) -> float:
    """Default to source duration when we didn't probe (e.g. duration_s was None)."""
    return float(track.duration_s) if track.duration_s else 0.0


def _build_manifest_entry(
    track: Track,
    final: Path,
    output_dir: Path,
    spec: AudioSpec,
    duration_s: float,
) -> ManifestEntry:

    # SF-5: do NOT silently fall back to absolute paths.
    try:
        rel_path = final.relative_to(output_dir).as_posix()
    except ValueError as e:
        raise ManifestInconsistent(
            f"refusing to record manifest entry for {final}: not under "
            f"output_dir {output_dir}"
        ) from e
    return ManifestEntry(
        schema_version=1,
        source=track.source_name,
        track_id=track.id,
        original_title=track.original_title or track.title,
        filename_stem=final.stem,
        mp3_path=rel_path,
        bitrate_kbps=spec.bitrate_kbps,
        duration_s=duration_s,
        downloaded_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
