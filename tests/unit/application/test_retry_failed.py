"""Sprint 8.5 Phase B unit tests for `RetryFailedUseCase`.

Covers Gherkin scenarios from `docs/sprints/sprint-8.5.md`:
  1. retry only retryable error_class by default
  2. --since filters by failed_at timestamp (with C6 inclusive-edge case)
  3. dedup -- newest-wins per (source, track_id), losers in skipped_deduped
  4. null-identity (source/track_id None) keys on url instead (C2)
  5. url-variant collision warns + records skipped_url_variants (C3)
  6. --dry-run skips delegation to BatchDownloadUseCase
  7. --all bypasses terminal-class filter with WARNING
  8. --error-class restricts to explicit class
  9. empty / non-matching failures.jsonl returns empty plan, no batch invoked
 10. C5 scope-warn fires when --since=None and span > 7d / > 50 rows
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from shokz.application.use_cases.batch_download import BatchDownloadResult
from shokz.application.use_cases.retry_failed import (
    RetryFailedInput,
    RetryFailedUseCase,
    parse_since,
)
from shokz.domain.models import (
    AudioSpec,
    FailureEntry,
    ManifestEntry,
)

# ---------- fakes ----------


def _failure(
    *,
    track_id: str | None = "abc",
    source: str | None = "youtube",
    url: str = "https://x/y/abc",
    error_class: str = "NETWORK_ERROR",
    failed_at: str = "2026-04-30T12:00:00Z",
) -> FailureEntry:
    return FailureEntry(
        schema_version=1,
        source=source,
        track_id=track_id,
        url=url,
        error_class=error_class,
        error_message="synthetic",
        failed_at=failed_at,
    )


@dataclass
class _FakeManifest:
    failures: list[FailureEntry]

    async def record(self, _entry: ManifestEntry) -> None: ...
    async def record_failure(self, _entry: FailureEntry) -> None: ...
    async def find_by_track(
        self, _source: str, _track_id: str
    ) -> ManifestEntry | None:
        return None

    async def iter_all(self) -> AsyncIterator[ManifestEntry]:
        if False:
            yield  # pragma: no cover -- empty async generator

    async def iter_failures(self) -> AsyncIterator[FailureEntry]:
        for f in self.failures:
            yield f


def _spec() -> AudioSpec:
    return AudioSpec(codec="mp3", bitrate_kbps=64, channels=1, sample_rate_hz=44100)


def _success_batch_result() -> BatchDownloadResult:
    return BatchDownloadResult(
        results=(),
        elapsed_s=0.0,
    )


def _build_uc(
    failures: list[FailureEntry],
    *,
    batch_execute: AsyncMock | None = None,
) -> tuple[RetryFailedUseCase, AsyncMock]:
    """Wire RetryFailedUseCase against in-memory fakes. Returns the use
    case + the AsyncMock for `BatchDownloadUseCase.execute` so tests can
    assert call count + arguments."""
    manifest = _FakeManifest(failures=failures)
    bd = AsyncMock()
    bd.execute = batch_execute or AsyncMock(return_value=_success_batch_result())
    uc = RetryFailedUseCase(manifest=manifest, batch_download=bd)  # type: ignore[arg-type]
    return uc, bd.execute


def _input(**overrides: Any) -> RetryFailedInput:
    # Path is unused (BatchDownloadUseCase is mocked); use a sentinel
    # under cwd to dodge ruff S108 on /tmp paths.
    base: dict[str, Any] = {
        "output_dir": Path("./.unused-by-mock-bd"),
        "spec": _spec(),
    }
    base.update(overrides)
    return RetryFailedInput(**base)


# ---------- scenarios ----------


@pytest.mark.asyncio
async def test_default_filters_to_retryable_classes_only() -> None:
    """Gherkin 1: NETWORK_ERROR + RATE_LIMITED queued; AUTH_REQUIRED +
    FORMAT_UNAVAILABLE skipped to skipped_terminal."""
    failures = [
        _failure(track_id="A", url="https://x/A", error_class="NETWORK_ERROR"),
        _failure(track_id="B", url="https://x/B", error_class="RATE_LIMITED"),
        _failure(track_id="C", url="https://x/C", error_class="AUTH_REQUIRED"),
        _failure(track_id="D", url="https://x/D", error_class="FORMAT_UNAVAILABLE"),
    ]
    uc, bd_execute = _build_uc(failures)
    result = await uc.execute(_input())

    assert {p.url for p in result.planned} == {"https://x/A", "https://x/B"}
    assert {s.url for s in result.skipped_terminal} == {"https://x/C", "https://x/D"}
    assert bd_execute.await_count == 1
    assert bd_execute.await_args is not None
    submitted_urls = bd_execute.await_args.args[0].urls
    assert set(submitted_urls) == {"https://x/A", "https://x/B"}


@pytest.mark.asyncio
async def test_since_filters_by_failed_at_with_inclusive_boundary() -> None:
    """Gherkin 2 + C6: `failed_at == since` is INCLUDED. Datetime
    comparison must be timezone-aware UTC (no naive/aware TypeError)."""
    boundary = "2026-04-26T12:00:00Z"
    failures = [
        _failure(track_id="X", url="https://x/X", failed_at="2026-04-26T11:59:59Z"),
        _failure(track_id="Y", url="https://x/Y", failed_at=boundary),
        _failure(track_id="Z", url="https://x/Z", failed_at="2026-04-26T12:00:01Z"),
    ]
    uc, _ = _build_uc(failures)
    since = datetime.strptime(boundary, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    result = await uc.execute(_input(since=since))

    assert {p.url for p in result.planned} == {"https://x/Y", "https://x/Z"}
    assert [s.url for s in result.skipped_old] == ["https://x/X"]


@pytest.mark.asyncio
async def test_dedup_newest_wins_losers_in_skipped_deduped() -> None:
    """Gherkin 3 + U2: 3 retry attempts for the same track collapse to 1
    plan entry; the 2 losers populate skipped_deduped."""
    failures = [
        _failure(track_id="Z", url="https://x/Z", failed_at="2026-04-30T08:00:00Z"),
        _failure(track_id="Z", url="https://x/Z", failed_at="2026-04-30T09:00:00Z"),
        _failure(track_id="Z", url="https://x/Z", failed_at="2026-04-30T10:00:00Z"),
    ]
    uc, _ = _build_uc(failures)
    result = await uc.execute(_input())

    assert len(result.planned) == 1
    assert result.planned[0].failed_at == "2026-04-30T10:00:00Z"
    assert len(result.skipped_deduped) == 2
    assert {s.failed_at for s in result.skipped_deduped} == {
        "2026-04-30T08:00:00Z",
        "2026-04-30T09:00:00Z",
    }
    assert result.skipped_url_variants == ()


@pytest.mark.asyncio
async def test_null_identity_keys_on_url_no_collapse() -> None:
    """Gherkin 4 / C2: 4 resolve-time NETWORK_ERROR rows with
    source=None AND track_id=None MUST NOT collapse to 1. They key on
    `url` instead, so all 4 distinct urls survive."""
    failures = [
        _failure(track_id=None, source=None, url=f"https://x/{i}")
        for i in range(4)
    ]
    uc, _ = _build_uc(failures)
    result = await uc.execute(_input())

    assert len(result.planned) == 4
    assert {p.url for p in result.planned} == {
        "https://x/0", "https://x/1", "https://x/2", "https://x/3",
    }
    assert result.null_identity_count == 4
    assert result.skipped_deduped == ()


@pytest.mark.asyncio
async def test_url_variant_collision_warns_and_records(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Gherkin 5 / C3: same (source, track_id), DIFFERENT urls. WARNING
    logged + loser in skipped_url_variants."""
    failures = [
        _failure(
            track_id="W",
            url="https://youtu.be/W",
            failed_at="2026-04-26T12:00:00Z",
        ),
        _failure(
            track_id="W",
            url="https://m.youtube.com/watch?v=W",
            failed_at="2026-04-26T18:00:00Z",
        ),
    ]
    uc, _ = _build_uc(failures)
    with caplog.at_level(logging.WARNING, logger="shokz.usecase.retry_failed"):
        result = await uc.execute(_input())

    assert len(result.planned) == 1
    assert result.planned[0].url == "https://m.youtube.com/watch?v=W"
    assert len(result.skipped_url_variants) == 1
    assert result.skipped_url_variants[0].url == "https://youtu.be/W"
    assert any(
        "url-variant collision" in r.message and r.levelname == "WARNING"
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_dry_run_skips_batch_download() -> None:
    """Gherkin 6: --dry-run produces planned tuple but BatchDownloadUseCase
    is NEVER awaited."""
    failures = [
        _failure(track_id="A", url="https://x/A", error_class="NETWORK_ERROR"),
        _failure(track_id="B", url="https://x/B", error_class="RATE_LIMITED"),
    ]
    uc, bd_execute = _build_uc(failures)
    result = await uc.execute(_input(dry_run=True))

    assert len(result.planned) == 2
    assert result.batch_result is None
    assert bd_execute.await_count == 0


@pytest.mark.asyncio
async def test_all_flag_includes_terminal_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Gherkin 7: --all keeps AUTH_REQUIRED and emits a WARNING per
    terminal-class entry kept."""
    failures = [
        _failure(track_id="C", url="https://x/C", error_class="AUTH_REQUIRED"),
        _failure(track_id="B", url="https://x/B", error_class="RATE_LIMITED"),
    ]
    uc, bd_execute = _build_uc(failures)
    with caplog.at_level(logging.WARNING, logger="shokz.usecase.retry_failed"):
        result = await uc.execute(_input(include_terminal=True))

    assert {p.url for p in result.planned} == {"https://x/C", "https://x/B"}
    assert result.skipped_terminal == ()
    assert any(
        "--all bypasses terminal-class filter" in r.message
        and r.levelname == "WARNING"
        for r in caplog.records
    )
    assert bd_execute.await_count == 1


@pytest.mark.asyncio
async def test_explicit_error_classes_restrict_filter() -> None:
    """Gherkin 8: --error-class RATE_LIMITED queues only the
    RATE_LIMITED rows; both transient + terminal classes outside the
    explicit list go to skipped_terminal."""
    failures = [
        _failure(track_id="A", url="https://x/A", error_class="NETWORK_ERROR"),
        _failure(track_id="B", url="https://x/B", error_class="RATE_LIMITED"),
        _failure(track_id="D", url="https://x/D", error_class="DOWNLOAD_FAILED"),
    ]
    uc, _ = _build_uc(failures)
    result = await uc.execute(_input(error_classes=frozenset({"RATE_LIMITED"})))

    assert {p.url for p in result.planned} == {"https://x/B"}
    assert {s.url for s in result.skipped_terminal} == {"https://x/A", "https://x/D"}


@pytest.mark.asyncio
async def test_explicit_error_classes_can_include_normally_terminal_class() -> None:
    """Phase B GAN F5 + spec: --error-class OVERRIDES RETRYABLE_CLASSES.
    A user can explicitly opt into a normally-terminal class (e.g.
    AUTH_REQUIRED) without --all. This locks in the override semantics
    so a future refactor can't silently add `and not in TERMINAL_CLASSES`
    to the filter without breaking a test."""
    failures = [
        _failure(track_id="C", url="https://x/C", error_class="AUTH_REQUIRED"),
        _failure(track_id="A", url="https://x/A", error_class="NETWORK_ERROR"),
    ]
    uc, _ = _build_uc(failures)
    result = await uc.execute(
        _input(error_classes=frozenset({"AUTH_REQUIRED"}))
    )
    # AUTH_REQUIRED is normally terminal -- but explicit allow-list wins.
    assert {p.url for p in result.planned} == {"https://x/C"}
    assert {s.url for s in result.skipped_terminal} == {"https://x/A"}


@pytest.mark.asyncio
async def test_all_and_error_classes_are_mutually_exclusive() -> None:
    """Phase B GAN F6: --all + --error-class is ambiguous; the use case
    rejects the combo with ValueError so the CLI can surface a clean
    error rather than silently dropping one of the two flags."""
    uc, _ = _build_uc([])
    with pytest.raises(ValueError, match="mutually exclusive"):
        await uc.execute(
            _input(
                include_terminal=True,
                error_classes=frozenset({"NETWORK_ERROR"}),
            )
        )


@pytest.mark.asyncio
async def test_malformed_failed_at_is_skipped_not_crashed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Phase B GAN F2: a row with unparseable failed_at MUST be skipped +
    logged, NOT abort the entire run. Locks in the no-single-corrupt-row-
    aborts-everything invariant."""
    failures = [
        _failure(track_id="A", url="https://x/A", failed_at="not-a-timestamp"),
        _failure(track_id="B", url="https://x/B", failed_at="2026-04-30T00:00:00Z"),
    ]
    uc, _ = _build_uc(failures)
    with caplog.at_level(logging.WARNING, logger="shokz.usecase.retry_failed"):
        result = await uc.execute(_input())

    # Healthy row survives; corrupt row bucketed.
    assert {p.url for p in result.planned} == {"https://x/B"}
    assert [s.url for s in result.skipped_malformed] == ["https://x/A"]
    assert any(
        "unparseable failed_at" in r.message and r.levelname == "WARNING"
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_empty_planned_short_circuits_no_batch_invoke() -> None:
    """Gherkin 9 partial: failures.jsonl has rows, but every one is
    terminal-class (and --all not set), so the planned tuple is empty.
    BatchDownloadUseCase is NOT invoked; result.batch_result is None."""
    failures = [
        _failure(track_id="C", url="https://x/C", error_class="AUTH_REQUIRED"),
    ]
    uc, bd_execute = _build_uc(failures)
    result = await uc.execute(_input())

    assert result.planned == ()
    assert result.batch_result is None
    assert bd_execute.await_count == 0


@pytest.mark.asyncio
async def test_scope_warn_fires_at_50_plus_rows(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Gherkin 10 / C5: --since=None with > 50 candidates -> WARNING
    + result.scope_warned == True. Phase B GAN F8: use a recent
    failed_at (1 hour ago) so the test is testing the count branch
    independently of the date branch."""
    recent = (datetime.now(UTC) - timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    failures = [
        _failure(
            track_id=f"t{i}",
            url=f"https://x/{i}",
            error_class="NETWORK_ERROR",
            failed_at=recent,
        )
        for i in range(60)
    ]
    uc, _ = _build_uc(failures)
    with caplog.at_level(logging.WARNING, logger="shokz.usecase.retry_failed"):
        result = await uc.execute(_input())

    assert result.scope_warned is True
    # Phase B GAN F4: count + date context combined into one WARNING so
    # the date is always present.
    assert any(
        r.levelname == "WARNING" and "going back to" in r.message
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_scope_warn_fires_when_oldest_older_than_7_days(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Sprint 8.5 C5 alternate-trigger: even with < 50 rows, WARNING
    fires when the OLDEST row is more than 7 days old."""
    eight_days_ago = (datetime.now(UTC) - timedelta(days=8)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    failures = [
        _failure(track_id="A", url="https://x/A", failed_at=eight_days_ago),
    ]
    uc, _ = _build_uc(failures)
    with caplog.at_level(logging.WARNING, logger="shokz.usecase.retry_failed"):
        result = await uc.execute(_input())

    assert result.scope_warned is True
    assert any(
        "going back to" in r.message and r.levelname == "WARNING"
        for r in caplog.records
    )


# ---------- parse_since ----------


def test_parse_since_relative_units() -> None:
    """C6: each of [smhdw] resolves to the right timedelta."""
    now = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)
    cases = [
        ("30s", timedelta(seconds=30)),
        ("15m", timedelta(minutes=15)),
        ("3h", timedelta(hours=3)),
        ("2d", timedelta(days=2)),
        ("1w", timedelta(weeks=1)),
    ]
    for raw, delta in cases:
        assert parse_since(raw, now=now) == now - delta


def test_parse_since_iso_8601() -> None:
    """C6: both Z-suffixed datetime AND date-only forms parse to UTC."""
    assert parse_since("2026-04-30T12:00:00Z") == datetime(
        2026, 4, 30, 12, 0, 0, tzinfo=UTC
    )
    assert parse_since("2026-04-30") == datetime(2026, 4, 30, tzinfo=UTC)


def test_parse_since_returns_aware_datetime_no_tz_mismatch() -> None:
    """C6: parsed since is timezone-aware so comparison vs aware
    failed_at cannot raise TypeError."""
    aware = parse_since("2026-04-30T12:00:00Z")
    assert aware.tzinfo is not None
    # Smoke: must be comparable to another aware datetime.
    assert aware > datetime(2026, 4, 1, tzinfo=UTC)


def test_parse_since_rejects_garbage() -> None:
    """Garbage --since input MUST raise ValueError with the contract
    spelled out in the message (so the CLI can surface it)."""
    with pytest.raises(ValueError, match="must be relative"):
        parse_since("not-a-date")


def test_parse_since_rejects_naive_now() -> None:
    """Phase B GAN F11: caller passing a naive datetime as `now` would
    silently produce a naive `since`, then crash at comparison. Reject
    up front."""
    naive = datetime(2026, 4, 30, 12, 0, 0)  # no tzinfo
    with pytest.raises(ValueError, match="must be timezone-aware"):
        parse_since("2d", now=naive)


def test_parse_since_rejects_overflow_n() -> None:
    """Phase B GAN F10: huge n in relative duration would raise
    OverflowError from C-int conversion in timedelta multiplication.
    The docstring contract says ValueError; enforce it."""
    with pytest.raises(ValueError, match="out of range"):
        parse_since("9999999999d")
