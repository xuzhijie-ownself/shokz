"""Sprint 7 acceptance tests -- retry policy + classification + circuit breaker.

Most scenarios use the in-process use case directly (no subprocess) since
they need to inject classified failures and the CLI doesn't expose hooks
for that. The CLI surface is exercised by the unit smoke tests + the
integration-gated `shokz download <real-url>` self-demo.
"""

from __future__ import annotations

from pathlib import Path

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
    FormatUnavailable,
    NetworkError,
    RateLimited,
)
from shokz.domain.models import RawDownload, Track, TrackStatus
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


def _fast_policy(**overrides: object) -> RetryPolicy:
    cfg = RetrySection(backoff_base_s=0.1, **overrides)  # type: ignore[arg-type]
    return RetryPolicy(cfg)


@pytest.fixture(autouse=True)
def _instant_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("shokz.application.policies.retry.asyncio.sleep", _no_sleep)


class _ClassifiedSource(FakeVideoSource):
    """FakeVideoSource that injects classified failures into download_audio.

    Two modes:
      - `download_failures: list[Exception]` -- FIFO queue shared across URLs
        (use for single-URL tests where all calls hit the same track).
      - `failures_by_track_id: dict[str, list[Exception]]` -- per-track
        FIFO queues so multi-URL tests don't have URL_A's retry steal
        URL_B's failure.
    """

    download_failures: list[Exception]
    failures_by_track_id: dict[str, list[Exception]]

    def __init__(
        self,
        download_failures: list[Exception] | None = None,
        failures_by_track_id: dict[str, list[Exception]] | None = None,
    ) -> None:
        super().__init__()
        self.download_failures = download_failures or []
        self.failures_by_track_id = failures_by_track_id or {}

    async def download_audio(self, track: Track, dest_dir: Path) -> RawDownload:
        per_track = self.failures_by_track_id.get(track.id)
        if per_track:
            self.download_calls.append(track.id)
            raise per_track.pop(0)
        if self.download_failures:
            self.download_calls.append(track.id)
            raise self.download_failures.pop(0)
        return await super().download_audio(track, dest_dir)


def _wire(
    source: FakeVideoSource, tmp_path: Path, *, retry_policy: RetryPolicy | None
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


_URL = "https://www.youtube.com/watch?v=aaaaaaaaaaa"


# --- Sprint 7 Gherkin scenarios ----------------------------------------


@pytest.mark.asyncio
async def test_429_too_many_requests_retries_with_long_backoff(tmp_path: Path) -> None:
    """Scenario 1: 429 retries 3 times then succeeds."""
    source = _ClassifiedSource([RateLimited("HTTP Error 429")] * 2)
    use_case, manifest = _wire(source, tmp_path, retry_policy=_fast_policy())
    result = await use_case.execute(
        BatchDownloadInput(
            urls=(_URL,), output_dir=tmp_path / "downloads", spec=SWIM_STANDARD
        )
    )
    assert result.succeeded == 1
    assert len(source.download_calls) == 3
    assert manifest.failures == []


@pytest.mark.asyncio
async def test_5xx_network_error_retries_with_short_backoff(tmp_path: Path) -> None:
    """Scenario 2: 5xx retries once then succeeds."""
    source = _ClassifiedSource([NetworkError("HTTP Error 503")])
    use_case, _ = _wire(source, tmp_path, retry_policy=_fast_policy())
    result = await use_case.execute(
        BatchDownloadInput(
            urls=(_URL,), output_dir=tmp_path / "downloads", spec=SWIM_STANDARD
        )
    )
    assert result.succeeded == 1
    assert len(source.download_calls) == 2


@pytest.mark.asyncio
async def test_auth_required_fails_immediately_no_retry(tmp_path: Path) -> None:
    """Scenario 3: AuthRequired = 1 attempt, error_class AUTH_REQUIRED."""
    source = _ClassifiedSource([AuthRequired("Sign in to confirm your age")])
    use_case, manifest = _wire(source, tmp_path, retry_policy=_fast_policy())
    result = await use_case.execute(
        BatchDownloadInput(
            urls=(_URL,), output_dir=tmp_path / "downloads", spec=SWIM_STANDARD
        )
    )
    assert result.failed == 1
    assert len(source.download_calls) == 1
    assert manifest.failures[0].error_class == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_format_unavailable_fails_immediately(tmp_path: Path) -> None:
    """Scenario 4: FormatUnavailable terminal."""
    source = _ClassifiedSource([FormatUnavailable("Requested format not available")])
    use_case, manifest = _wire(source, tmp_path, retry_policy=_fast_policy())
    result = await use_case.execute(
        BatchDownloadInput(
            urls=(_URL,), output_dir=tmp_path / "downloads", spec=SWIM_STANDARD
        )
    )
    assert result.failed == 1
    assert len(source.download_calls) == 1
    assert manifest.failures[0].error_class == "FORMAT_UNAVAILABLE"


@pytest.mark.asyncio
async def test_one_failure_entry_per_track_regardless_of_retries(
    tmp_path: Path,
) -> None:
    """Scenario 12: exhausted retry leaves exactly ONE failures.jsonl row."""
    source = _ClassifiedSource([RateLimited("429")] * 10)
    use_case, manifest = _wire(source, tmp_path, retry_policy=_fast_policy())
    await use_case.execute(
        BatchDownloadInput(
            urls=(_URL,), output_dir=tmp_path / "downloads", spec=SWIM_STANDARD
        )
    )
    assert len(manifest.failures) == 1
    assert manifest.failures[0].error_class == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_per_track_failure_isolation_with_classified_errors(
    tmp_path: Path,
) -> None:
    """Scenario 13: A=429-then-success, B=AuthRequired (terminal), C=clean.
    Result: 2/3 succeeded (0 skipped, 1 failed). Per-track failure queues
    so URL_A's retry can't steal URL_B's failure (Phase 5 review pin)."""
    source = _ClassifiedSource(
        failures_by_track_id={
            "aaaaaaaaaaa": [RateLimited("429")],  # URL_A: 1 retry then success
            "bbbbbbbbbbb": [AuthRequired("Sign in")],  # URL_B: terminal
            # URL_C: no failures, succeeds first try
        }
    )
    use_case, manifest = _wire(source, tmp_path, retry_policy=_fast_policy())
    result = await use_case.execute(
        BatchDownloadInput(
            urls=(
                _URL,
                "https://www.youtube.com/watch?v=bbbbbbbbbbb",
                "https://www.youtube.com/watch?v=ccccccccccc",
            ),
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=1,
        )
    )
    assert result.succeeded == 2
    assert result.failed == 1
    assert len(manifest.failures) == 1
    assert manifest.failures[0].error_class == "AUTH_REQUIRED"
    # B failed; A and C succeeded.
    failed_track = next(r for r in result.results if r.status is TrackStatus.FAILED)
    assert failed_track.track is not None
    assert failed_track.track.id == "bbbbbbbbbbb"


class _FlakyResolveSource(FakeVideoSource):
    """FakeVideoSource that injects classified failures into resolve()
    (NOT download_audio). Drives the C3 resolve-phase retry path."""

    resolve_failures: list[Exception]

    def __init__(self, resolve_failures: list[Exception]) -> None:
        super().__init__()
        self.resolve_failures = resolve_failures

    async def resolve(self, url: str) -> Track:
        if self.resolve_failures:
            self.resolve_calls.append(url)
            raise self.resolve_failures.pop(0)
        return await super().resolve(url)


@pytest.mark.asyncio
async def test_resolve_phase_rate_limited_retries_then_succeeds(
    tmp_path: Path,
) -> None:
    """Sprint 7 Gherkin scenario 15 (C3 named DoD item): RateLimited
    during source.resolve() retries with the same budget as during
    download_audio. Without this coverage the C3 fix could silently
    regress to "no retry on resolve-phase 429s."""
    source = _FlakyResolveSource(
        [RateLimited("HTTP Error 429"), RateLimited("HTTP Error 429")]
    )
    use_case, manifest = _wire(source, tmp_path, retry_policy=_fast_policy())
    result = await use_case.execute(
        BatchDownloadInput(
            urls=(_URL,), output_dir=tmp_path / "downloads", spec=SWIM_STANDARD
        )
    )
    assert result.succeeded == 1
    # 2 failures + 1 success = 3 resolve calls; download_audio only fires once.
    assert len(source.resolve_calls) == 3
    assert manifest.failures == []


@pytest.mark.asyncio
async def test_unclassified_download_failed_increments_u8_counter_end_to_end(
    tmp_path: Path,
) -> None:
    """Phase 5 GAN MED#4: drive a terminal DownloadFailed (default fallback
    when no §7.1 pattern matches) and assert BatchDownloadResult.
    unclassified_yt_dlp_errors increments AND the track ends FAILED."""
    from shokz.domain.errors import DownloadFailed

    # DownloadFailed inherits the network budget: 2 retries -> 3 total
    # attempts. Inject more failures than the budget so all attempts fail.
    source = _ClassifiedSource(
        download_failures=[DownloadFailed("novel error")] * 5
    )
    use_case, manifest = _wire(source, tmp_path, retry_policy=_fast_policy())
    result = await use_case.execute(
        BatchDownloadInput(
            urls=(_URL,), output_dir=tmp_path / "downloads", spec=SWIM_STANDARD
        )
    )
    assert result.failed == 1
    assert result.unclassified_yt_dlp_errors == 1
    assert manifest.failures[0].error_class == "DOWNLOAD_FAILED"


@pytest.mark.asyncio
async def test_circuit_breaker_after_3_consecutive_rate_limited(
    tmp_path: Path,
) -> None:
    """Scenario 11 (C4): 3 consecutive RateLimited tracks trip the breaker;
    remaining tracks are NOT retried."""
    source = _ClassifiedSource([RateLimited("429")] * 50)
    use_case, _ = _wire(source, tmp_path, retry_policy=_fast_policy())
    urls = tuple(
        f"https://www.youtube.com/watch?v={c * 11}" for c in "abcde"
    )
    result = await use_case.execute(
        BatchDownloadInput(
            urls=urls,
            output_dir=tmp_path / "downloads",
            spec=SWIM_STANDARD,
            concurrency=1,
        )
    )
    assert result.failed == 5
    assert result.rate_limit_circuit_tripped is True
    # First 3 tracks: 4 attempts each (3 retries + 1) = 12.
    # Remaining 2 tracks: 1 attempt each (no retry, breaker tripped) = 2.
    assert len(source.download_calls) == 12 + 2
