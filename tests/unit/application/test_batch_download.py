"""Unit tests for BatchDownloadUseCase — Sprint 1 scenarios using fakes.

Sprint 7 extends with retry-policy, classification, and circuit-breaker tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from shokz.application.policies.filename_resolver import FilenameResolver
from shokz.application.policies.reconciliation import ReconciliationPolicy
from shokz.application.policies.retry import RetryPolicy
from shokz.application.policies.skip_existing import SkipExistingPolicy
from shokz.application.use_cases.batch_download import (
    BatchDownloadInput,
    BatchDownloadUseCase,
)
from shokz.config.schema import RetrySection
from shokz.domain.errors import (
    AuthRequired,
    DownloadFailed,
    RateLimited,
)
from shokz.domain.models import RawDownload, TrackStatus
from shokz.domain.presets import SWIM_STANDARD
from tests.fakes import (
    FakeAudioEncoder,
    FakeFileSystem,
    FakeManifest,
    FakeProgressReporter,
    FakeVideoSource,
)


def _resolver_factory(output_dir: Path) -> FilenameResolver:
    return FilenameResolver(output_dir=output_dir)


_URL_A = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
_URL_B = "https://www.youtube.com/watch?v=bbbbbbbbbbb"
_URL_C = "https://www.youtube.com/watch?v=ccccccccccc"


@pytest.mark.asyncio
async def test_use_case_orchestration_three_urls_all_succeed(tmp_path: Path) -> None:
    """Sprint 1 AC: 3 URLs → 3 succeeded; ports called the right number of times."""
    source = FakeVideoSource()
    encoder = FakeAudioEncoder()
    progress = FakeProgressReporter()
    _m = FakeManifest()
    _fs = FakeFileSystem()
    use_case = BatchDownloadUseCase(
        sources=(source,),
        encoder=encoder,
        progress=progress,
        filename_resolver_factory=_resolver_factory,
        manifest=_m,
        filesystem=_fs,
        skip_existing=SkipExistingPolicy(
            manifest=_m, filesystem=_fs, output_dir=tmp_path / "downloads"
        ),
        reconciliation=ReconciliationPolicy(
            manifest=_m, filesystem=_fs, output_dir=tmp_path / "downloads"
        ),
    )

    result = await use_case.execute(
        BatchDownloadInput(
            urls=(_URL_A, _URL_B, _URL_C),
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=3,
        )
    )

    assert result.succeeded == 3
    assert result.failed == 0
    assert len(source.resolve_calls) == 3
    assert len(source.download_calls) == 3
    assert len(encoder.encode_calls) == 3

    # Each encode received the raw path emitted by the source for the same track.
    encoded_dest_names = {dest.name for _, dest, _ in encoder.encode_calls}
    expected_partials = {
        "aaaaaaaaaaa.mp3.partial",
        "bbbbbbbbbbb.mp3.partial",
        "ccccccccccc.mp3.partial",
    }
    assert encoded_dest_names == expected_partials

    # Final files use Sprint 2 title-based naming (FakeVideoSource synthesizes
    # title="Title for {id}") -- NOT the {id}.mp3 of Sprint 1.
    finals = sorted((tmp_path / "downloads").glob("*.mp3"))
    assert {p.name for p in finals} == {
        "Title for aaaaaaaaaaa.mp3",
        "Title for bbbbbbbbbbb.mp3",
        "Title for ccccccccccc.mp3",
    }

    # Raw files cleaned up (keep_raw default False).
    assert sorted((tmp_path / "downloads" / ".tmp").glob("*.fake")) == []


@pytest.mark.asyncio
async def test_failure_is_isolated_per_track(tmp_path: Path) -> None:
    """Sprint 1 AC: one failure doesn't kill the batch; partial success surfaced."""
    source = FakeVideoSource(fail_resolve_for=frozenset({_URL_B}))
    encoder = FakeAudioEncoder()
    progress = FakeProgressReporter()
    _m = FakeManifest()
    _fs = FakeFileSystem()
    use_case = BatchDownloadUseCase(
        sources=(source,),
        encoder=encoder,
        progress=progress,
        filename_resolver_factory=_resolver_factory,
        manifest=_m,
        filesystem=_fs,
        skip_existing=SkipExistingPolicy(
            manifest=_m, filesystem=_fs, output_dir=tmp_path / "downloads"
        ),
        reconciliation=ReconciliationPolicy(
            manifest=_m, filesystem=_fs, output_dir=tmp_path / "downloads"
        ),
    )

    result = await use_case.execute(
        BatchDownloadInput(
            urls=(_URL_A, _URL_B, _URL_C),
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=3,
        )
    )

    assert result.succeeded == 2
    assert result.failed == 1
    failed = next(r for r in result.results if r.status is TrackStatus.FAILED)
    assert failed.track is None  # resolve failed, no track resolved
    assert failed.error is not None
    assert "fake-fail resolve" in failed.error


@pytest.mark.asyncio
async def test_keep_raw_preserves_tmp_file(tmp_path: Path) -> None:
    source = FakeVideoSource()
    encoder = FakeAudioEncoder()
    progress = FakeProgressReporter()
    _m = FakeManifest()
    _fs = FakeFileSystem()
    use_case = BatchDownloadUseCase(
        sources=(source,),
        encoder=encoder,
        progress=progress,
        filename_resolver_factory=_resolver_factory,
        manifest=_m,
        filesystem=_fs,
        skip_existing=SkipExistingPolicy(
            manifest=_m, filesystem=_fs, output_dir=tmp_path / "downloads"
        ),
        reconciliation=ReconciliationPolicy(
            manifest=_m, filesystem=_fs, output_dir=tmp_path / "downloads"
        ),
    )

    await use_case.execute(
        BatchDownloadInput(
            urls=(_URL_A,),
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=1,
            keep_raw=True,
        )
    )

    raws = list((tmp_path / "downloads" / ".tmp").glob("*.fake"))
    assert len(raws) == 1


@pytest.mark.asyncio
async def test_no_source_can_handle_url_raises(tmp_path: Path) -> None:
    source = FakeVideoSource()
    encoder = FakeAudioEncoder()
    progress = FakeProgressReporter()
    _m = FakeManifest()
    _fs = FakeFileSystem()
    use_case = BatchDownloadUseCase(
        sources=(source,),
        encoder=encoder,
        progress=progress,
        filename_resolver_factory=_resolver_factory,
        manifest=_m,
        filesystem=_fs,
        skip_existing=SkipExistingPolicy(
            manifest=_m, filesystem=_fs, output_dir=tmp_path / "downloads"
        ),
        reconciliation=ReconciliationPolicy(
            manifest=_m, filesystem=_fs, output_dir=tmp_path / "downloads"
        ),
    )

    result = await use_case.execute(
        BatchDownloadInput(
            urls=("https://vimeo.com/12345",),  # FakeVideoSource won't claim this URL
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=1,
        )
    )

    # _process_one catches ValueError from _select_source and produces a FAILED
    # TrackResult — the batch survives, the unsupported URL is reported.
    assert result.succeeded == 0
    assert result.failed == 1
    assert result.results[0].status is TrackStatus.FAILED
    assert result.results[0].track is None
    assert result.results[0].error is not None
    assert "no source can handle" in result.results[0].error.lower()


@pytest.mark.asyncio
async def test_unexpected_exception_in_resolve_is_isolated(tmp_path: Path) -> None:
    """Sprint 1 non-functional: per-track failure isolation must catch ANY exception type."""

    class _ExplodingSource(FakeVideoSource):
        async def resolve(self, url: str):  # type: ignore[override]
            self.resolve_calls.append(url)
            raise RuntimeError("BOOM — non-ShokzError exception escaping resolve")

    source = _ExplodingSource()
    encoder = FakeAudioEncoder()
    progress = FakeProgressReporter()
    _m = FakeManifest()
    _fs = FakeFileSystem()
    use_case = BatchDownloadUseCase(
        sources=(source,),
        encoder=encoder,
        progress=progress,
        filename_resolver_factory=_resolver_factory,
        manifest=_m,
        filesystem=_fs,
        skip_existing=SkipExistingPolicy(
            manifest=_m, filesystem=_fs, output_dir=tmp_path / "downloads"
        ),
        reconciliation=ReconciliationPolicy(
            manifest=_m, filesystem=_fs, output_dir=tmp_path / "downloads"
        ),
    )

    result = await use_case.execute(
        BatchDownloadInput(
            urls=(_URL_A, _URL_B),
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=2,
        )
    )

    assert result.succeeded == 0
    assert result.failed == 2
    for r in result.results:
        assert r.status is TrackStatus.FAILED
        assert r.error is not None
        assert "BOOM" in r.error or "unexpected" in r.error.lower()


@pytest.mark.asyncio
async def test_name_ambiguous_raised_at_use_case_level(tmp_path: Path) -> None:
    """python-reviewer test-quality fix: cover BatchDownloadUseCase.execute()'s
    NameAmbiguous guard directly (not just the CLI layer's pre-check).
    """
    from shokz.domain.errors import NameAmbiguous

    source = FakeVideoSource()
    encoder = FakeAudioEncoder()
    progress = FakeProgressReporter()
    _m = FakeManifest()
    _fs = FakeFileSystem()
    use_case = BatchDownloadUseCase(
        sources=(source,),
        encoder=encoder,
        progress=progress,
        filename_resolver_factory=_resolver_factory,
        manifest=_m,
        filesystem=_fs,
        skip_existing=SkipExistingPolicy(
            manifest=_m, filesystem=_fs, output_dir=tmp_path / "downloads"
        ),
        reconciliation=ReconciliationPolicy(
            manifest=_m, filesystem=_fs, output_dir=tmp_path / "downloads"
        ),
    )

    with pytest.raises(NameAmbiguous, match="exactly one URL"):
        await use_case.execute(
            BatchDownloadInput(
                urls=(_URL_A, _URL_B),  # 2 URLs + name_override -> ambiguous
                output_dir=tmp_path / "downloads",
                spec=SWIM_STANDARD,
                concurrency=2,
                name_override="X",
            )
        )


# ============================================================
# Sprint 4 use-case-level integrity + manifest tests
# ============================================================


@pytest.mark.asyncio
async def test_atomic_write_integrity_unit_level_with_fakes(tmp_path: Path) -> None:
    """Sprint 4 AC: 'Atomic-write integrity (unit-level)'."""
    source = FakeVideoSource()
    encoder = FakeAudioEncoder(probe_duration_value=120.0)
    progress = FakeProgressReporter()
    manifest = FakeManifest()
    fs = FakeFileSystem()
    use_case = BatchDownloadUseCase(
        sources=(source,),
        encoder=encoder,
        progress=progress,
        filename_resolver_factory=_resolver_factory,
        manifest=manifest,
        filesystem=fs,
        skip_existing=SkipExistingPolicy(
            manifest=manifest, filesystem=fs, output_dir=tmp_path / "downloads"
        ),
        reconciliation=ReconciliationPolicy(
            manifest=manifest, filesystem=fs, output_dir=tmp_path / "downloads"
        ),
    )
    result = await use_case.execute(
        BatchDownloadInput(
            urls=(_URL_A,),
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=1,
        )
    )
    assert result.succeeded == 1
    # FakeFileSystem records file + parent-dir fsync calls per atomic_move
    assert len(fs.fsync_file_calls) == 1
    assert len(fs.fsync_dir_calls) == 1
    # Manifest got one entry
    assert len(manifest.successes) == 1
    entry = manifest.successes[0]
    assert entry.schema_version == 1
    assert entry.source == "youtube"


@pytest.mark.asyncio
async def test_post_download_size_check_rejects_0_byte_raw_file(tmp_path: Path) -> None:
    """Sprint 4 AC: 'Post-download size check rejects 0-byte raw file'."""
    source = FakeVideoSource(raw_bytes=b"")  # forces 0-byte raw -> SourceFileCorrupt
    encoder = FakeAudioEncoder()
    manifest = FakeManifest()
    fs = FakeFileSystem()
    use_case = BatchDownloadUseCase(
        sources=(source,),
        encoder=encoder,
        progress=FakeProgressReporter(),
        filename_resolver_factory=_resolver_factory,
        manifest=manifest,
        filesystem=fs,
        skip_existing=SkipExistingPolicy(
            manifest=manifest, filesystem=fs, output_dir=tmp_path / "downloads"
        ),
        reconciliation=ReconciliationPolicy(
            manifest=manifest, filesystem=fs, output_dir=tmp_path / "downloads"
        ),
    )
    result = await use_case.execute(
        BatchDownloadInput(
            urls=(_URL_A,),
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=1,
        )
    )
    assert result.failed == 1
    assert "SourceFileCorrupt" not in (result.results[0].error or "")  # we expose the message
    assert "raw download" in (result.results[0].error or "")
    assert len(manifest.successes) == 0
    assert len(manifest.failures) == 1
    assert manifest.failures[0].error_class == "SOURCE_FILE_CORRUPT"
    # Final mp3 must not exist
    assert list((tmp_path / "downloads").glob("*.mp3")) == []


@pytest.mark.asyncio
async def test_post_encode_duration_check_rejects_truncated_audio(tmp_path: Path) -> None:
    """Sprint 4 AC: 'Post-encode duration check rejects truncated audio'."""
    source = FakeVideoSource()  # duration_s=120 by default
    encoder = FakeAudioEncoder(probe_duration_value=30.0)  # 75% short -> EncodingFailed
    manifest = FakeManifest()
    _fs2 = FakeFileSystem()
    use_case = BatchDownloadUseCase(
        sources=(source,),
        encoder=encoder,
        progress=FakeProgressReporter(),
        filename_resolver_factory=_resolver_factory,
        manifest=manifest,
        filesystem=_fs2,
        skip_existing=SkipExistingPolicy(
            manifest=manifest, filesystem=_fs2, output_dir=tmp_path / "downloads"
        ),
        reconciliation=ReconciliationPolicy(
            manifest=manifest, filesystem=_fs2, output_dir=tmp_path / "downloads"
        ),
    )
    result = await use_case.execute(
        BatchDownloadInput(
            urls=(_URL_A,),
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=1,
        )
    )
    assert result.failed == 1
    err = (result.results[0].error or "").lower()
    assert "duration" in err or "deviates" in err
    assert len(manifest.successes) == 0
    assert len(manifest.failures) == 1
    assert manifest.failures[0].error_class == "ENCODING_FAILED"


@pytest.mark.asyncio
async def test_use_case_integrity_unit_level_with_fakes_pass(tmp_path: Path) -> None:
    """Sprint 4 AC: 'Use case integrity -- unit-level with fakes' (within-tolerance pass)."""
    source = FakeVideoSource()  # duration_s=120
    encoder = FakeAudioEncoder(probe_duration_value=118.5)  # 1.25% short -> within 2% tolerance
    _m5 = FakeManifest()
    _fs5 = FakeFileSystem()
    use_case = BatchDownloadUseCase(
        sources=(source,),
        encoder=encoder,
        progress=FakeProgressReporter(),
        filename_resolver_factory=_resolver_factory,
        manifest=_m5,
        filesystem=_fs5,
        skip_existing=SkipExistingPolicy(
            manifest=_m5, filesystem=_fs5, output_dir=tmp_path / "downloads"
        ),
        reconciliation=ReconciliationPolicy(
            manifest=_m5, filesystem=_fs5, output_dir=tmp_path / "downloads"
        ),
    )
    result = await use_case.execute(
        BatchDownloadInput(
            urls=(_URL_A,),
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=1,
        )
    )
    assert result.succeeded == 1


@pytest.mark.asyncio
async def test_failure_of_one_track_does_not_corrupt_manifest_of_others(tmp_path: Path) -> None:
    """Sprint 4 AC: 'Failure of one track does not corrupt manifest of others'."""
    source = FakeVideoSource(fail_resolve_for=frozenset({_URL_B}))
    encoder = FakeAudioEncoder()
    manifest = FakeManifest()
    _fs2 = FakeFileSystem()
    use_case = BatchDownloadUseCase(
        sources=(source,),
        encoder=encoder,
        progress=FakeProgressReporter(),
        filename_resolver_factory=_resolver_factory,
        manifest=manifest,
        filesystem=_fs2,
        skip_existing=SkipExistingPolicy(
            manifest=manifest, filesystem=_fs2, output_dir=tmp_path / "downloads"
        ),
        reconciliation=ReconciliationPolicy(
            manifest=manifest, filesystem=_fs2, output_dir=tmp_path / "downloads"
        ),
    )
    result = await use_case.execute(
        BatchDownloadInput(
            urls=(_URL_A, _URL_B, _URL_C),
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=3,
        )
    )
    assert result.succeeded == 2
    assert result.failed == 1
    # 2 manifest entries (URLs A + C), 1 failure entry (URL B)
    assert len(manifest.successes) == 2
    assert len(manifest.failures) == 1
    assert manifest.failures[0].url == _URL_B


@pytest.mark.asyncio
async def test_manifest_entry_preserves_original_title_separate_from_filename(
    tmp_path: Path,
) -> None:
    """Sprint 4 AC: 'Manifest entry preserves original_title separate from filename'."""

    class _OriginalTitleSource(FakeVideoSource):
        async def resolve(self, url: str):  # type: ignore[override]
            self.resolve_calls.append(url)
            from shokz.domain.models import Track

            return Track(
                id="abc123",
                title="Soft Piano: Sleep Music!",  # what filename_resolver sanitizes
                uploader="X",
                duration_s=120,
                source_url=url,
                source_name="youtube",
                original_title="Soft Piano: Sleep Music!",  # what manifest stores raw
            )

    source = _OriginalTitleSource()
    manifest = FakeManifest()
    _fs3 = FakeFileSystem()
    use_case = BatchDownloadUseCase(
        sources=(source,),
        encoder=FakeAudioEncoder(probe_duration_value=120.0),
        progress=FakeProgressReporter(),
        filename_resolver_factory=_resolver_factory,
        manifest=manifest,
        filesystem=_fs3,
        skip_existing=SkipExistingPolicy(
            manifest=manifest, filesystem=_fs3, output_dir=tmp_path / "downloads"
        ),
        reconciliation=ReconciliationPolicy(
            manifest=manifest, filesystem=_fs3, output_dir=tmp_path / "downloads"
        ),
    )
    await use_case.execute(
        BatchDownloadInput(
            urls=(_URL_A,),
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=1,
        )
    )
    assert len(manifest.successes) == 1
    entry = manifest.successes[0]
    # Original title preserved with colons + exclamations.
    assert entry.original_title == "Soft Piano: Sleep Music!"
    # Filename stem is sanitized (FAT-reserved chars stripped: colons gone).
    # pathvalidate retains `!` since it's filesystem-safe; that's correct.
    assert ":" not in entry.filename_stem
    assert "Soft Piano Sleep Music" in entry.filename_stem


# ============================================================
# Sprint 7: retry policy wiring + classification + circuit breaker
# ============================================================
# These tests construct the use case WITH a RetryPolicy (existing tests
# use the default None for backward compatibility).


def _fast_retry_policy(**overrides: object) -> RetryPolicy:
    """RetryPolicy with tiny waits so tests don't actually sleep minutes."""
    cfg = RetrySection(backoff_base_s=0.1, **overrides)  # type: ignore[arg-type]
    return RetryPolicy(cfg)


@pytest.fixture(autouse=True)
def _instant_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make retry policy's asyncio.sleep instant for every test in this file
    (autouse so we don't have to inject it into each test's parameters)."""

    async def _no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("shokz.application.policies.retry.asyncio.sleep", _no_sleep)


class _FlakyDownloadSource(FakeVideoSource):
    """FakeVideoSource that raises a configurable sequence on download_audio
    before eventually succeeding (or never succeeding when failures are
    inexhaustible)."""

    download_failures: list[Exception]

    def __init__(self, download_failures: list[Exception]) -> None:
        super().__init__()
        self.download_failures = download_failures

    async def download_audio(self, track: Any, dest_dir: Any) -> RawDownload:
        if self.download_failures:
            self.download_calls.append(track.id)
            raise self.download_failures.pop(0)
        return await super().download_audio(track, dest_dir)


def _wire(
    source: FakeVideoSource,
    tmp_path: Path,
    *,
    retry_policy: RetryPolicy | None = None,
) -> tuple[BatchDownloadUseCase, FakeManifest]:
    encoder = FakeAudioEncoder()
    progress = FakeProgressReporter()
    m = FakeManifest()
    fs = FakeFileSystem()
    use_case = BatchDownloadUseCase(
        sources=(source,),
        encoder=encoder,
        progress=progress,
        filename_resolver_factory=_resolver_factory,
        manifest=m,
        filesystem=fs,
        skip_existing=SkipExistingPolicy(
            manifest=m, filesystem=fs, output_dir=tmp_path / "downloads"
        ),
        reconciliation=ReconciliationPolicy(
            manifest=m, filesystem=fs, output_dir=tmp_path / "downloads"
        ),
        retry_policy=retry_policy,
    )
    return use_case, m


@pytest.mark.asyncio
async def test_rate_limited_retries_then_succeeds(
    tmp_path: Path,
) -> None:
    """Sprint 7 Gherkin: 429 -> 429 -> success ends SUCCESS, 3 download calls."""
    source = _FlakyDownloadSource(
        [RateLimited("HTTP Error 429"), RateLimited("HTTP Error 429")]
    )
    use_case, manifest = _wire(source, tmp_path, retry_policy=_fast_retry_policy())

    result = await use_case.execute(
        BatchDownloadInput(
            urls=(_URL_A,),
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=1,
        )
    )
    assert result.succeeded == 1
    assert result.failed == 0
    assert len(source.download_calls) == 3  # 2 failures + 1 success
    # No failure record because the track ultimately succeeded.
    assert len(manifest.failures) == 0


@pytest.mark.asyncio
async def test_auth_required_does_not_retry_and_classifies_correctly(
    tmp_path: Path,
) -> None:
    """Sprint 7 GAN C2: AUTH_REQUIRED in failures.jsonl, NOT UNEXPECTED_ERROR."""
    source = _FlakyDownloadSource([AuthRequired("Sign in to confirm your age")])
    use_case, manifest = _wire(source, tmp_path, retry_policy=_fast_retry_policy())

    result = await use_case.execute(
        BatchDownloadInput(
            urls=(_URL_A,),
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=1,
        )
    )
    assert result.failed == 1
    assert len(source.download_calls) == 1  # NO retry
    assert len(manifest.failures) == 1
    assert manifest.failures[0].error_class == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_exhausted_rate_limited_records_classified_class_not_retry_error(
    tmp_path: Path,
) -> None:
    """Sprint 7 GAN C2 / silent#1: original class survives the wrapper."""
    source = _FlakyDownloadSource([RateLimited("HTTP Error 429")] * 10)
    use_case, manifest = _wire(source, tmp_path, retry_policy=_fast_retry_policy())

    result = await use_case.execute(
        BatchDownloadInput(
            urls=(_URL_A,),
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=1,
        )
    )
    assert result.failed == 1
    # Default 3 retries = 4 total attempts.
    assert len(source.download_calls) == 4
    # ONE failure row regardless of retry count (Sprint 7 spec).
    assert len(manifest.failures) == 1
    # Stable error class is RATE_LIMITED, not RETRY_ERROR / UNEXPECTED_ERROR.
    assert manifest.failures[0].error_class == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_unclassified_download_failed_increments_counter(
    tmp_path: Path,
) -> None:
    """Sprint 7 GAN U8: BatchDownloadResult.unclassified_yt_dlp_errors
    counts terminal DownloadFailed (default fallback) tracks."""
    source = _FlakyDownloadSource([DownloadFailed("novel error")] * 10)
    use_case, _ = _wire(source, tmp_path, retry_policy=_fast_retry_policy())

    result = await use_case.execute(
        BatchDownloadInput(
            urls=(_URL_A,),
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=1,
        )
    )
    assert result.failed == 1
    assert result.unclassified_yt_dlp_errors == 1


@pytest.mark.asyncio
async def test_circuit_breaker_trips_after_3_consecutive_rate_limited(
    tmp_path: Path,
) -> None:
    """Sprint 7 GAN C4: 3rd consecutive RateLimited trips the breaker;
    the 4th and 5th URLs run with retries=0 (one attempt each)."""
    # Each URL fails terminally with RateLimited (10 failures > retry budget).
    source = _FlakyDownloadSource([RateLimited("429")] * 50)
    use_case, _ = _wire(source, tmp_path, retry_policy=_fast_retry_policy())

    result = await use_case.execute(
        BatchDownloadInput(
            urls=(_URL_A, _URL_B, _URL_C, "https://www.youtube.com/watch?v=ddddddddddd",
                  "https://www.youtube.com/watch?v=eeeeeeeeeee"),
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=1,
        )
    )
    assert result.failed == 5
    assert result.rate_limit_circuit_tripped is True
    # First 3 tracks: 4 attempts each (3 retries + 1 original) = 12.
    # 4th and 5th tracks: 1 attempt each (breaker tripped) = 2.
    assert len(source.download_calls) == 12 + 2


@pytest.mark.asyncio
async def test_circuit_breaker_resets_on_intermediate_success_across_tracks(
    tmp_path: Path,
) -> None:
    """Phase 4 GAN LOW#6: explicit multi-track reset coverage.

    Sequence:
      URL_A: 2 RateLimited then success on the 3rd attempt (track-level
             retry succeeds; consecutive_rate_limits is reset by the
             SUCCESS path, not by intra-track retry).
      URL_B + URL_C: succeed on first attempt (counter stays at 0).

    The breaker MUST NOT trip even though the batch has RateLimited
    activity -- a track that ultimately succeeds is evidence the throttle
    isn't sticking and `_consecutive_rate_limits` is reset to 0.
    """
    # 2 RateLimited (URL_A retry path) then exhaust the failure list;
    # FakeVideoSource's super().download_audio succeeds for the rest.
    source = _FlakyDownloadSource(
        [RateLimited("429"), RateLimited("429")]
    )
    use_case, _ = _wire(source, tmp_path, retry_policy=_fast_retry_policy())

    result = await use_case.execute(
        BatchDownloadInput(
            urls=(_URL_A, _URL_B, _URL_C),
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=1,
        )
    )
    # URL_A: 2 RateLimited (download_calls bumps each time) + 3rd success
    #        path doesn't increment download_calls (FakeVideoSource's
    #        super() call does) -> 2 + 1 = 3.
    # URL_B + URL_C: 1 attempt each -> 2.
    assert result.succeeded == 3
    assert result.failed == 0
    assert result.rate_limit_circuit_tripped is False


@pytest.mark.asyncio
async def test_source_file_corrupt_retry_calls_cleanup_hook(
    tmp_path: Path,
) -> None:
    """Sprint 7 GAN C6: SourceFileCorrupt retry MUST delete partial files
    before re-attempting (so yt-dlp can't resume against corrupt bytes)."""
    # Set raw_bytes to BELOW MIN_RAW_BYTES so the size-check raises
    # SourceFileCorrupt; on retry, FakeVideoSource produces the same
    # bytes again -> still corrupt -> exhausts the 1 retry budget.
    source = _FlakyDownloadSource([])
    source.raw_bytes = b"x" * 10  # below MIN_RAW_BYTES (1024)
    use_case, manifest = _wire(source, tmp_path, retry_policy=_fast_retry_policy())

    # Pre-create a fake partial in tmp_dir so we can verify cleanup ran.
    tmp_dir = tmp_path / "downloads" / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    sentinel = tmp_dir / "aaaaaaaaaaa.partial-from-attempt-1"
    sentinel.write_bytes(b"PARTIAL CRUFT")
    assert sentinel.exists()

    result = await use_case.execute(
        BatchDownloadInput(
            urls=(_URL_A,),
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=1,
        )
    )
    assert result.failed == 1  # 2 attempts both corrupt, no successes
    assert manifest.failures[0].error_class == "SOURCE_FILE_CORRUPT"
    # Cleanup hook should have removed the sentinel before the retry.
    assert not sentinel.exists()
