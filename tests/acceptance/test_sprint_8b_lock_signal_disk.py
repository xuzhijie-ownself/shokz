"""Sprint 8b acceptance: cross-process lock + SIGINT shielding + disk
guard wired through BatchDownloadUseCase.

Pure unit-style acceptance (no network) using fake ports + monkeypatched
ENOSPC raises. Integration via real ffmpeg/yt-dlp lives in INTEGRATION=1
gated tests; this file proves the wiring.

Scenarios:

  1. DiskGuardPolicy pre-flight (after resolve-all) raises DiskFull when
     sum(filesize_approx) * safety_multiplier > free.
  2. Mid-batch DiskFull (e.g. ENOSPC at ffmpeg) aborts the rest of the
     batch -- subsequent tracks return FAILED with the
     "aborted by prior DiskFull" reason and `disk_full_count` reflects N.
  3. _process_one finally-block cleans up raw .tmp/<id>.* on failure
     (Sprint 8b GAN B6) so a CLI retry doesn't see stale corrupt files.
  4. FileLockPolicy: AnotherRunInProgress on contention with a same-process
     held lock (proves the wiring runs the 5-step classifier).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from shokz.application.policies.disk_guard import DiskGuardPolicy
from shokz.application.policies.file_lock import FileLockPolicy
from shokz.application.policies.filename_resolver import FilenameResolver
from shokz.application.policies.reconciliation import ReconciliationPolicy
from shokz.application.policies.skip_existing import SkipExistingPolicy
from shokz.application.use_cases.batch_download import (
    BatchDownloadInput,
    BatchDownloadUseCase,
)
from shokz.domain.errors import (
    AnotherRunInProgress,
    DiskFull,
    EncodingFailed,
)
from shokz.domain.models import (
    AudioSpec,
    EncodedFile,
    FailureEntry,
    ManifestEntry,
    RawDownload,
    Track,
    TrackStatus,
)

# ---------- fakes ----------


@dataclass
class _FakeSource:
    name: str = "youtube"
    raise_on_resolve: dict[str, BaseException] | None = None
    raise_on_download: dict[str, BaseException] | None = None
    estimates: dict[str, int | None] | None = None

    def can_handle(self, url: str) -> bool:
        return True

    async def resolve(self, url: str) -> Track:
        if self.raise_on_resolve and url in self.raise_on_resolve:
            raise self.raise_on_resolve[url]
        track_id = url.rsplit("/", 1)[-1]
        size = (self.estimates or {}).get(url)
        return Track(
            id=track_id,
            title=track_id,
            uploader="u",
            duration_s=10,
            source_url=url,
            source_name=self.name,
            filesize_approx=size,
        )

    async def download_audio(self, track: Track, dest_dir: Path) -> RawDownload:
        if self.raise_on_download and track.source_url in self.raise_on_download:
            raise self.raise_on_download[track.source_url]
        # Write a fake raw file > MIN_RAW_BYTES (1024)
        raw = dest_dir / f"{track.id}.webm"
        raw.write_bytes(b"X" * 2048)
        return RawDownload(path=raw, container="webm", track=track)


@dataclass
class _FakeEncoder:
    raise_on_encode_for: set[str] | None = None

    async def encode(self, src: Path, dest: Path, spec: AudioSpec) -> EncodedFile:
        track_id = src.stem  # "<id>"
        if self.raise_on_encode_for and track_id in self.raise_on_encode_for:
            raise DiskFull(f"disk full during ffmpeg encode of {src}")
        # Pretend-encode: write a couple KB to dest.
        dest.write_bytes(b"M" * 4096)
        return EncodedFile(
            path=dest,
            bitrate_kbps=spec.bitrate_kbps,
            channels=spec.channels,
            duration_s=10.0,
            size_bytes=4096,
        )

    async def probe_duration(self, path: Path) -> float:
        return 10.0


class _FakeManifest:
    def __init__(self) -> None:
        self.records: list[ManifestEntry] = []
        self.failures: list[FailureEntry] = []

    async def record(self, entry: ManifestEntry) -> None:
        self.records.append(entry)

    async def record_failure(self, entry: FailureEntry) -> None:
        self.failures.append(entry)

    async def find_by_track(self, source: str, track_id: str) -> ManifestEntry | None:
        return None

    async def iter_all(self) -> AsyncIterator[ManifestEntry]:
        for r in self.records:
            yield r


class _FakeFS:
    def atomic_move(self, src: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dest)

    def exists(self, path: Path) -> bool:
        return path.exists()

    def mkdir_p(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    def remove(self, path: Path) -> None:
        path.unlink(missing_ok=True)


class _NullProgress:
    def start(self, **_: Any) -> None: ...
    def update(self, **_: Any) -> None: ...
    def finish(self, **_: Any) -> None: ...


def _build_use_case(
    *,
    sources: tuple[_FakeSource, ...],
    encoder: _FakeEncoder,
    manifest: _FakeManifest,
    fs: _FakeFS,
    output_dir: Path,
    disk_guard: DiskGuardPolicy | None = None,
) -> BatchDownloadUseCase:
    skip = SkipExistingPolicy(manifest=manifest, filesystem=fs, output_dir=output_dir)  # type: ignore[arg-type]
    recon = ReconciliationPolicy(manifest=manifest, filesystem=fs, output_dir=output_dir)  # type: ignore[arg-type]

    def _resolver_factory(out: Path) -> FilenameResolver:
        return FilenameResolver(output_dir=out, template="{id}")

    return BatchDownloadUseCase(
        sources=sources,
        encoder=encoder,
        progress=_NullProgress(),
        filename_resolver_factory=_resolver_factory,
        manifest=manifest,
        filesystem=fs,
        skip_existing=skip,
        reconciliation=recon,
        retry_policy=None,
        disk_guard=disk_guard,
    )


# ---------- scenarios ----------


@pytest.mark.asyncio
async def test_disk_guard_preflight_blocks_when_estimates_exceed_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-flight: sum(filesize_approx) * 2.0 > free -> DiskFull RAISED
    BEFORE any download starts (zero raw files in tmp_dir)."""
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    one_gib = 1024 * 1024 * 1024
    src = _FakeSource(estimates={
        "https://x/y/A": one_gib,
        "https://x/y/B": one_gib,
    })
    enc = _FakeEncoder()
    man = _FakeManifest()
    fs = _FakeFS()

    import shutil

    def _fake_disk_usage(_path: Any) -> Any:
        return shutil._ntuple_diskusage(  # type: ignore[attr-defined]
            total=10 * one_gib, used=9 * one_gib, free=1 * one_gib
        )

    monkeypatch.setattr(
        "shokz.application.policies.disk_guard.shutil.disk_usage",
        _fake_disk_usage,
    )

    uc = _build_use_case(
        sources=(src,), encoder=enc, manifest=man, fs=fs,
        output_dir=output_dir,
        disk_guard=DiskGuardPolicy(safety_multiplier=2.0),
    )

    inp = BatchDownloadInput(
        urls=("https://x/y/A", "https://x/y/B"),
        output_dir=output_dir,
        spec=AudioSpec(codec="mp3", bitrate_kbps=64, channels=1, sample_rate_hz=44100),
        concurrency=1,
    )
    with pytest.raises(DiskFull, match="insufficient disk"):
        await uc.execute(inp)
    tmp_dir = output_dir / ".tmp"
    assert not list(tmp_dir.iterdir())


@pytest.mark.asyncio
async def test_first_diskfull_mid_batch_aborts_rest(tmp_path: Path) -> None:
    """Track A succeeds; track B raises DiskFull at encode; remaining
    tracks short-circuit with `aborted by prior DiskFull` and the result's
    disk_full_count reflects ALL N (B trigger + the aborted ones)."""
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    src = _FakeSource()
    enc = _FakeEncoder(raise_on_encode_for={"B"})
    man = _FakeManifest()
    fs = _FakeFS()

    uc = _build_use_case(
        sources=(src,), encoder=enc, manifest=man, fs=fs,
        output_dir=output_dir,
        disk_guard=None,
    )

    inp = BatchDownloadInput(
        urls=("https://x/y/A", "https://x/y/B", "https://x/y/C", "https://x/y/D"),
        output_dir=output_dir,
        spec=AudioSpec(codec="mp3", bitrate_kbps=64, channels=1, sample_rate_hz=44100),
        concurrency=1,
    )
    result = await uc.execute(inp)

    statuses = [r.status for r in result.results]
    assert statuses[0] is TrackStatus.SUCCESS
    assert statuses[1] is TrackStatus.FAILED
    assert statuses[2] is TrackStatus.FAILED
    assert statuses[3] is TrackStatus.FAILED

    assert "aborted by prior DiskFull" in (result.results[2].error or "")
    assert "aborted by prior DiskFull" in (result.results[3].error or "")

    # B (trigger) + C + D = 3.
    assert result.disk_full_count == 3


@pytest.mark.asyncio
async def test_raw_tmp_cleaned_on_encode_failure(tmp_path: Path) -> None:
    """Sprint 8b GAN B6: when encode fails, the raw .webm in tmp_dir/
    must be removed so a retry doesn't see stale corrupt source."""
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    tmp_dir = output_dir / ".tmp"

    class _BadEncoder(_FakeEncoder):
        async def encode(self, src: Path, dest: Path, spec: AudioSpec) -> EncodedFile:
            raise EncodingFailed("synthetic")

    src = _FakeSource()
    man = _FakeManifest()
    fs = _FakeFS()
    uc = _build_use_case(
        sources=(src,), encoder=_BadEncoder(), manifest=man, fs=fs,
        output_dir=output_dir,
    )

    inp = BatchDownloadInput(
        urls=("https://x/y/E",),
        output_dir=output_dir,
        spec=AudioSpec(codec="mp3", bitrate_kbps=64, channels=1, sample_rate_hz=44100),
        concurrency=1,
        keep_raw=False,
    )
    result = await uc.execute(inp)
    assert result.results[0].status is TrackStatus.FAILED

    leftover = list(tmp_dir.glob("E.*"))
    assert leftover == [], f"expected no leftover; found {leftover}"


def test_filelock_contention_raises_another_run_in_progress(tmp_path: Path) -> None:
    """FileLockPolicy: a second-acquire while the first is still held
    classifies as AnotherRunInProgress (step 5: same PID, matching
    start_time)."""
    lock_path = tmp_path / "shokz.lock"
    holder = FileLockPolicy(lock_path=lock_path, timeout_s=0.1)
    second = FileLockPolicy(lock_path=lock_path, timeout_s=0.1)

    with holder, pytest.raises(AnotherRunInProgress), second:
        pass
