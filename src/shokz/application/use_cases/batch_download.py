"""BatchDownloadUseCase — Sprint 1 slim version.

Orchestrates: resolve -> download_audio -> encode -> move-to-final, with
bounded asyncio concurrency. Per-track failures are isolated.

Out of scope for Sprint 1 (deferred per docs/sprints/sprint-1.md):
  - Manifest, skip-existing, retry, atomic durability, signal handling,
    title-based filenames, configuration. All hard-coded with sensible defaults.
"""

from __future__ import annotations

import asyncio
import contextlib
import glob as _glob
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeAlias, TypeVar

from shokz.application.policies.disk_guard import DiskGuardPolicy
from shokz.application.policies.filename_resolver import FilenameResolver
from shokz.application.policies.reconciliation import ReconciliationPolicy
from shokz.application.policies.retry import RetryPolicy
from shokz.application.policies.skip_existing import SkipDecision, SkipExistingPolicy
from shokz.application.ports.outbound.encoder import AudioEncoderPort
from shokz.application.ports.outbound.filesystem import FileSystemPort
from shokz.application.ports.outbound.manifest import ManifestPort
from shokz.application.ports.outbound.progress import ProgressReporterPort
from shokz.application.ports.outbound.video_source import VideoSourcePort
from shokz.domain.errors import (
    AuthRequired,
    DiskFull,
    DownloadFailed,
    EncodingFailed,
    FormatUnavailable,
    ManifestInconsistent,
    NameAmbiguous,
    NameInvalid,
    NameOutsideOutputDir,
    NetworkError,
    RateLimited,
    ShokzError,
    SourceFileCorrupt,
    SourceUnavailable,
)
from shokz.domain.models import (
    AudioSpec,
    FailureEntry,
    ManifestEntry,
    RawDownload,
    Track,
    TrackResult,
    TrackStatus,
)
from shokz.observability.logging import set_track_id

_T = TypeVar("_T")

_log = logging.getLogger("shokz.usecase.batch_download")

# Sprint 7 GAN U4: was a `dict[str, str]` keyed on `type(err).__name__` --
# subclass-fragile (a future class subclassing RateLimited would miss the
# map and land as "UNEXPECTED_ERROR"). Now ordered tuple matched via
# isinstance, most-specific-first.
_ERROR_CLASS_MAP: tuple[tuple[type[BaseException], str], ...] = (
    # Sprint 7 classified source/network errors (most specific first)
    (AuthRequired, "AUTH_REQUIRED"),
    (FormatUnavailable, "FORMAT_UNAVAILABLE"),
    (RateLimited, "RATE_LIMITED"),
    (NetworkError, "NETWORK_ERROR"),
    # Pre-Sprint-7 taxonomy
    (SourceUnavailable, "SOURCE_UNAVAILABLE"),
    (SourceFileCorrupt, "SOURCE_FILE_CORRUPT"),
    (EncodingFailed, "ENCODING_FAILED"),
    (NameOutsideOutputDir, "NAME_OUTSIDE_OUTPUT_DIR"),
    (NameInvalid, "NAME_INVALID"),
    (NameAmbiguous, "NAME_AMBIGUOUS"),
    (ManifestInconsistent, "MANIFEST_INCONSISTENT"),
    # Sprint 8b: ENOSPC at any of the 3 outbound-adapter sites surfaces as
    # DiskFull (or ManifestInconsistent FROM DiskFull for the manifest
    # site -- which classifies under MANIFEST_INCONSISTENT above).
    (DiskFull, "DISK_FULL"),
    # DownloadFailed is the catch-all default for ShokzError-typed errors
    # we couldn't classify more specifically; MUST come LAST so subclasses
    # of DownloadFailed (none today, future-proof) classify first.
    (DownloadFailed, "DOWNLOAD_FAILED"),
)


def _stable_error_class(err: BaseException) -> str:
    """Return a stable error_class string for failures.jsonl, classifying
    by isinstance (Sprint 7 GAN U4 -- subclass-safe)."""
    for exc_class, label in _ERROR_CLASS_MAP:
        if isinstance(err, exc_class):
            return label
    return "UNEXPECTED_ERROR"


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
    force: bool = False  # Sprint 4.5: bypass skip-existing
    target_dir: Path | None = None  # Sprint 5: where files land (defaults to output_dir)


@dataclass(frozen=True, slots=True)
class BatchDownloadResult:
    results: tuple[TrackResult, ...]
    elapsed_s: float
    # Sprint 7 GAN U8: counts yt-dlp errors that fell through to
    # DownloadFailed (no §7.1 pattern matched). Surfaced in the CLI
    # summary line so users know to report novel error shapes for
    # the §7.1 table to grow.
    unclassified_yt_dlp_errors: int = 0
    # Sprint 7 GAN C4: True when the per-batch RateLimited circuit
    # breaker tripped (3 consecutive RateLimited tracks); the rest of
    # the batch downgraded to no-retry to avoid hours of pointless waits.
    rate_limit_circuit_tripped: bool = False
    # Sprint 8b: count of tracks aborted because the FIRST DiskFull tripped
    # the disk-full circuit (the per-batch first-DiskFull-aborts-rest
    # invariant). Surfaced in the CLI summary so users know remaining
    # tracks were not attempted (vs failed individually).
    disk_full_count: int = 0

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.results if r.status is TrackStatus.SUCCESS)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status is TrackStatus.FAILED)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status is TrackStatus.SKIPPED)


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
        skip_existing: SkipExistingPolicy,
        reconciliation: ReconciliationPolicy,
        *,
        retry_policy: RetryPolicy | None = None,
        disk_guard: DiskGuardPolicy | None = None,
    ) -> None:
        if not sources:
            raise ValueError("at least one VideoSourcePort required")
        self._sources = sources
        self._encoder = encoder
        self._progress = progress
        self._resolver_factory = filename_resolver_factory
        self._manifest = manifest
        self._filesystem = filesystem
        self._skip_existing = skip_existing
        self._reconciliation = reconciliation
        # Sprint 7: optional ISP retry policy. None = no retry (existing
        # tests / library callers green by default).
        self._retry_policy = retry_policy
        # Sprint 8b: optional batch-level disk pre-flight. None = no check
        # (existing tests green by default).
        self._disk_guard = disk_guard

    async def execute(self, inp: BatchDownloadInput) -> BatchDownloadResult:
        # F6: reject symlinked output_dir BEFORE any work.
        if inp.output_dir.is_symlink():
            raise NameOutsideOutputDir(
                f"output directory {inp.output_dir} is a symlink; refusing to write through it"
            )

        # F2 (Sprint 5 review): target_dir MUST be under output_dir.
        # Without this guard, every per-track _build_manifest_entry would
        # raise ManifestInconsistent on relative_to() failure -- expensive,
        # late, and after raw downloads already landed in .tmp/.
        if inp.target_dir is not None:
            if inp.target_dir.is_symlink():
                raise NameOutsideOutputDir(
                    f"target_dir {inp.target_dir} is a symlink; refusing to write through it"
                )
            try:
                inp.target_dir.resolve().relative_to(inp.output_dir.resolve())
            except ValueError as e:
                raise NameOutsideOutputDir(
                    f"target_dir {inp.target_dir} is not under output_dir {inp.output_dir}"
                ) from e

        # Sprint 2: --name only valid with exactly one URL.
        if inp.name_override is not None and len(inp.urls) != 1:
            raise NameAmbiguous(f"--name requires exactly one URL, got {len(inp.urls)}")

        # Sprint 5: target_dir is where files actually land (e.g. a playlist
        # subdir); output_dir stays the top-level for manifest path computation
        # and .tmp / .shokz state.
        target_dir = inp.target_dir or inp.output_dir
        tmp_dir = inp.output_dir / ".tmp"
        inp.output_dir.mkdir(parents=True, exist_ok=True)
        target_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # Sprint 4.5 review fix #4: launch reconciliation AFTER guards pass.
        # Internal try/except in _reconcile_warn means exceptions never escape,
        # so storing the task reference (RUF006) is purely cosmetic.
        self._reconcile_task = asyncio.create_task(self._reconcile_warn())

        # Build the per-run resolver from the configured output_dir.
        resolver = self._resolver_factory(target_dir)

        # Sprint 8b GAN B3: ONE disk pre-flight per execute(), AFTER
        # resolving all metadata so we have filesize_approx. Skipped when
        # `disk_guard is None` (existing tests / library callers green).
        # Pre-resolved tracks are cached in `self._track_cache` and reused
        # by _process_one to avoid resolving twice.
        self._track_cache: dict[str, Track] = {}
        if self._disk_guard is not None:
            estimates = await self._pre_resolve_for_disk_guard(inp.urls, target_dir)
            try:
                self._disk_guard.check_batch(inp.output_dir, estimates)
            except DiskFull:
                # Pre-flight failure -- abort the whole batch BEFORE any
                # downloads start. Caller (CLI) translates to user-visible
                # error + non-zero exit.
                raise

        sem = asyncio.Semaphore(inp.concurrency)
        started = time.monotonic()

        # Sprint 7 GAN C4: per-batch circuit breaker + GAN U8: counter.
        # These are mutated from inside _process_one via instance attributes
        # so they survive across the gather. They reset per execute() call.
        self._consecutive_rate_limits = 0
        self._unclassified_yt_dlp_errors = 0
        self._circuit_tripped = False
        # Sprint 8b: first-DiskFull-aborts-rest invariant. Once any track
        # raises DiskFull, every subsequent _process_one short-circuits to
        # a synthesised TrackResult(status=FAILED, error="aborted by prior
        # DiskFull") so we don't keep encoding into an already-full disk.
        # GAN MED#3 caveat: at concurrency>1 multiple in-flight tracks
        # may pass the line-272 guard check before any of them flips the
        # flag, so several can independently hit ENOSPC. The summary in
        # _summary.py distinguishes "triggered" vs "short-circuited" so
        # the user-visible message is correct in both regimes.
        self._disk_full_tripped = False
        self._disk_full_count = 0

        async def bounded(url: str) -> TrackResult:
            async with sem:
                return await self._process_one(
                    url,
                    inp.output_dir,
                    target_dir,
                    tmp_dir,
                    inp.spec,
                    inp.keep_raw,
                    resolver,
                    inp.name_override,
                    inp.force,
                )

        results = await asyncio.gather(*(bounded(u) for u in inp.urls))
        return BatchDownloadResult(
            results=tuple(results),
            elapsed_s=time.monotonic() - started,
            unclassified_yt_dlp_errors=self._unclassified_yt_dlp_errors,
            rate_limit_circuit_tripped=self._circuit_tripped,
            disk_full_count=self._disk_full_count,
        )

    async def _pre_resolve_for_disk_guard(
        self, urls: tuple[str, ...], target_dir: Path
    ) -> list[int | None]:
        """Sprint 8b GAN B3: resolve all tracks BEFORE the disk pre-flight
        so we have filesize_approx in hand. Cache the resolved Track per
        url so `_process_one` reuses it (no double-resolve). On per-url
        resolve failure, treat the estimate as None (best-effort) -- the
        actual download path will surface the error normally.

        Parallelised under the same semaphore-style cap (4) as the rest
        of yt-dlp work, so a 60-track playlist's pre-flight isn't 60x
        slower than its concurrent-download phase.
        """
        sem = asyncio.Semaphore(4)

        async def _resolve_one(url: str) -> int | None:
            async with sem:
                try:
                    source = self._select_source(url)
                    track = await source.resolve(url)
                    self._track_cache[url] = track
                    return track.filesize_approx
                except Exception as e:
                    # Non-fatal: per-track download will surface the error.
                    _log.debug(
                        "pre-resolve for disk guard failed for %s: %s "
                        "(estimate=None; per-track resolve will retry)",
                        url, e,
                    )
                    return None

        return list(await asyncio.gather(*(_resolve_one(u) for u in urls)))

    async def _process_one(
        self,
        url: str,
        output_dir: Path,
        target_dir: Path,
        tmp_dir: Path,
        spec: AudioSpec,
        keep_raw: bool,
        resolver: FilenameResolver,
        name_override: str | None,
        force: bool,
    ) -> TrackResult:
        started = time.monotonic()
        # Sprint 8b: first-DiskFull-aborts-rest. Short-circuit BEFORE any
        # work (no resolve, no download) so we don't keep hammering a
        # full disk -- and the count surfaces in BatchDownloadResult.
        if self._disk_full_tripped:
            self._disk_full_count += 1
            return TrackResult(
                track=None,
                status=TrackStatus.FAILED,
                final_path=None,
                error="aborted by prior DiskFull (first-DiskFull aborts batch)",
                elapsed_s=time.monotonic() - started,
            )
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
            # Sprint 8b: reuse the pre-resolved Track from the disk-guard
            # pass when present (avoids resolving twice for the common
            # disk-guard-on path). Otherwise resolve now with retry.
            cached = self._track_cache.pop(url, None)
            if cached is not None:
                track = cached
            else:
                # Sprint 7 C3: wrap resolve in RetryPolicy. Retry-class
                # classification happens at adapter via _classify_message;
                # a RateLimited or NetworkError on metadata extract retries
                # exactly the same way as on download.
                async def _do_resolve() -> Track:
                    return await source.resolve(url)

                track = await self._maybe_retry(_do_resolve)
        except ShokzError as e:
            _log.warning("resolve failed: %s -- %s", url, e)
            self._update_circuit_state(e)
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
            # Phase 4 GAN HIGH#4: unexpected non-ShokzError = adapter bug,
            # NOT rate-limit pressure. Reset the counter so a sequence of
            # RateLimited + adapter-bug + RateLimited doesn't trip the breaker.
            self._consecutive_rate_limits = 0
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
            # Sprint 4.5: skip-existing check (manifest + filesystem both required).
            if not force:
                skip_result = await self._skip_existing.check(track.source_name, track.id)
                if skip_result.decision is SkipDecision.SKIPPED:
                    self._progress.finish(
                        track_id=track.id,
                        status=TrackStatus.SKIPPED,
                        message=str(skip_result.existing_path),
                    )
                    return TrackResult(
                        track=track,
                        status=TrackStatus.SKIPPED,
                        final_path=skip_result.existing_path,
                        error=None,
                        elapsed_s=time.monotonic() - started,
                    )

            self._progress.start(track_id=track.id, label=track.title)

            # Sprint 7 C3 + C6: wrap download + size-check (the retry unit
            # per spec U1) in RetryPolicy. on_retry cleans up partial bytes
            # so yt-dlp doesn't resume against a corrupt .webm and produce
            # a merged-corrupt MP3 the size check would silently pass.
            async def _do_download() -> RawDownload:
                downloaded = await source.download_audio(track, dest_dir=tmp_dir)
                # Integrity check #1 inside the retry unit: a 0-byte raw is
                # a SourceFileCorrupt that DOES retry (with cleanup).
                if (
                    not downloaded.path.exists()
                    or downloaded.path.stat().st_size < MIN_RAW_BYTES
                ):
                    size = (
                        downloaded.path.stat().st_size
                        if downloaded.path.exists()
                        else 0
                    )
                    raise SourceFileCorrupt(
                        f"raw download for {track.id} is {size} bytes "
                        f"(< {MIN_RAW_BYTES})"
                    )
                return downloaded

            async def _cleanup_partial(
                _err: BaseException, _attempt: int
            ) -> None:
                """Sprint 7 C6: delete any tmp_dir/{track.id}.* before the
                next retry attempt so yt-dlp can't resume against the
                partial / corrupt file. Phase 4 GAN MED#3: log on
                OSError so cleanup failures don't silently masquerade as
                downstream SourceFileCorrupt. Sprint 8b GAN MED#2:
                glob.escape so a future non-YouTube source whose track.id
                contains glob metacharacters (`?*[]`) doesn't unlink
                unrelated files."""
                for p in tmp_dir.glob(f"{_glob.escape(track.id)}.*"):
                    try:
                        p.unlink()
                    except OSError as exc:
                        _log.warning(
                            "cleanup_partial: could not delete %s for retry: %s",
                            p,
                            exc,
                        )

            raw = await self._maybe_retry(
                _do_download, on_retry=_cleanup_partial
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
            #
            # Sprint 8b GAN B1: shield + drain pattern.
            # If SIGINT cancels us BETWEEN os.replace (final file landed)
            # and the manifest row write, we'd orphan the mp3. asyncio.shield
            # protects the manifest task from cancellation; on CancelledError
            # we still await the (possibly already-running) task in the
            # except block so the manifest row durably lands before we
            # propagate the cancellation. _record_failure later (or
            # reconciliation on next startup) handles the half-state.
            manifest_task = asyncio.create_task(
                self._manifest.record(
                    _build_manifest_entry(
                        track, final, output_dir, spec, measured_duration_s
                    )
                )
            )
            try:
                await asyncio.shield(manifest_task)
            except asyncio.CancelledError:
                # Drain: wait for the protected task to finish so the
                # manifest row lands before we re-raise CancelledError.
                with contextlib.suppress(BaseException):
                    await manifest_task
                raise

            if not keep_raw:
                self._filesystem.remove(raw.path)

            # Sprint 7 GAN C4 circuit breaker: any SUCCESS resets the
            # consecutive-RateLimited counter (one good run means we
            # haven't actually been throttled out of the network).
            self._consecutive_rate_limits = 0
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
            self._update_circuit_state(e)
            # Sprint 8b: first-DiskFull-aborts-rest. Trip the per-batch
            # flag so subsequent _process_one calls short-circuit. We also
            # count THIS track as the first DiskFull (so the summary
            # disk_full_count includes the trigger, not just the aborts).
            if isinstance(e, DiskFull):
                self._disk_full_tripped = True
                self._disk_full_count += 1
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
            # Phase 4 GAN HIGH#4 (download path): same rationale as resolve.
            self._consecutive_rate_limits = 0
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
            # Sprint 8b GAN B6: opportunistic raw .tmp/<id>.* cleanup on
            # any failure exit path. The success branch already removes
            # raw via `_filesystem.remove(raw.path)` and atomic_moves the
            # .partial to final; this glob picks up the leftover .webm /
            # .mp3.partial when an exception unwinds before those happen
            # (so a retry-by-CLI doesn't see a stale corrupt source).
            # `track` is always bound here (resolve-failed paths return
            # earlier, before entering this try). keep_raw respected.
            # Sprint 8b GAN MED#2: glob.escape so non-YouTube source IDs
            # with `?*[]` don't unlink unrelated files.
            if not keep_raw:
                for p in tmp_dir.glob(f"{_glob.escape(track.id)}.*"):
                    with contextlib.suppress(OSError):
                        p.unlink()
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

    async def _reconcile_warn(self) -> None:
        """Sprint 4.5: scan for orphan files and log a WARNING if any found."""
        try:
            report = await self._reconciliation.scan()
        except Exception:
            _log.exception("reconciliation scan failed (non-blocking)")
            return
        if report.orphan_files:
            _log.warning(
                "reconciliation: %d orphan file(s) detected in downloads/ "
                "(no manifest entry); run `shokz library verify` for details. "
                "Likely cause: process killed between os.replace and manifest "
                "record (Sprint 4 SF-4 window).",
                len(report.orphan_files),
            )

    def _select_source(self, url: str) -> VideoSourcePort:
        for s in self._sources:
            if s.can_handle(url):
                return s
        raise ValueError(f"no source can handle URL: {url}")

    async def _maybe_retry(
        self,
        coro_factory: Callable[[], Awaitable[_T]],
        on_retry: Callable[[BaseException, int], Awaitable[None]] | None = None,
    ) -> _T:
        """Sprint 7: route through RetryPolicy if non-None AND the per-batch
        circuit breaker hasn't tripped. Otherwise call coro_factory once
        (preserves no-retry behavior for tests / library callers and for
        the rest of a batch after the breaker fires).

        Phase 6 GAN MED#4: parameterised with TypeVar so call sites get
        proper static typing (track: Track, raw: RawDownload) instead of
        an Any silently escaping through both branches.
        """
        if self._retry_policy is None or self._circuit_tripped:
            return await coro_factory()
        return await self._retry_policy.run(coro_factory, on_retry=on_retry)

    def _update_circuit_state(self, err: BaseException) -> None:
        """Sprint 7 GAN C4 + U8: track per-batch state on each FAILED track.

        - Consecutive RateLimited counter (any non-RateLimited resets it);
          trip the circuit at 3 to disable retries for the rest of the
          batch (avoids a 60-track playlist becoming a 3-hour wait).
        - Unclassified DownloadFailed counter (U8) so the CLI summary can
          surface §7.1 drift to the user.

        Sprint 7 Phase 6 GAN HIGH#2 disclaimer: with `--concurrency > 1`
        (cap is 4), multiple `_process_one` coroutines update these
        counters concurrently. asyncio is single-threaded and this method
        contains no `await`, so each individual `_update_circuit_state`
        call is atomic from asyncio's perspective. However, the SUCCESS-
        path reset and FAILURE-path increment can interleave at scheduling
        boundaries between coroutines. The breaker may trip slightly
        earlier or later than a strict consecutive count would predict.
        Sprint 6 default is `concurrency = 1` (no concurrency, exact
        semantics). For higher concurrency, treat the breaker as
        "best-effort: trips when consecutive RateLimited pressure is
        sustained, not on a strict count of 3."
        """
        if isinstance(err, RateLimited):
            self._consecutive_rate_limits += 1
            if self._consecutive_rate_limits >= 3 and not self._circuit_tripped:
                self._circuit_tripped = True
                _log.warning(
                    "rate-limit circuit breaker tripped: 3 consecutive "
                    "RateLimited tracks; remaining tracks in this batch "
                    "will run with retries=0 (waited a long time for nothing)"
                )
        else:
            self._consecutive_rate_limits = 0
        # U8: count only EXACT DownloadFailed (not subclasses) so we don't
        # double-count classified errors that happen to inherit from it.
        if type(err) is DownloadFailed:
            self._unclassified_yt_dlp_errors += 1


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
            f"refusing to record manifest entry for {final}: not under output_dir {output_dir}"
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
