"""Sprint 7 Phase 1 GAN: tiny tests for the new error classes.

Verifies that the domain error taxonomy added in Phase 1 is structurally
correct (subclassing, optional constructor args). The Phase 2 classifier
+ Phase 3 RetryPolicy will exercise the semantic side; these are pure
shape tests so a regression on the class hierarchy is caught fast.
"""

from __future__ import annotations

import pytest

from shokz.domain.errors import (
    AnotherRunInProgress,
    AuthRequired,
    DiskFull,
    DownloadFailed,
    FormatUnavailable,
    LockOwnerUnknown,
    NetworkError,
    RateLimited,
    ShokzError,
    SourceUnavailable,
    StaleLock,
)


@pytest.mark.parametrize(
    "exc_class",
    [
        AuthRequired,
        FormatUnavailable,
        RateLimited,
        NetworkError,
        SourceUnavailable,
        DownloadFailed,
        # Sprint 8 additions
        AnotherRunInProgress,
        StaleLock,
        LockOwnerUnknown,
        DiskFull,
    ],
)
def test_classified_errors_are_shokz_errors(exc_class: type[Exception]) -> None:
    """All Sprint 7 classified errors MUST subclass ShokzError so the use
    case's `except ShokzError` block catches them and the failure log
    classifier can find them."""
    assert issubclass(exc_class, ShokzError)


def test_rate_limited_carries_optional_retry_after_hint() -> None:
    """Sprint 7 GAN HIGH#2: yt-dlp may surface Retry-After in the error
    message; carrying the parsed hint avoids re-parsing in RetryPolicy."""
    err_default = RateLimited("HTTP Error 429")
    assert err_default.retry_after_seconds is None
    assert str(err_default) == "HTTP Error 429"

    err_with_hint = RateLimited("HTTP Error 429", retry_after_seconds=42)
    assert err_with_hint.retry_after_seconds == 42


def test_rate_limited_default_message_is_empty() -> None:
    """Constructor accepts no args (e.g. for synthetic test raises)."""
    err = RateLimited()
    assert err.retry_after_seconds is None
    assert str(err) == ""


# Sprint 8 Phase 1 GAN: structured attributes on the new error classes


def test_disk_full_carries_need_and_have_bytes_for_structured_inspection() -> None:
    """Phase 1 GAN HIGH#1: DiskFull's numeric values stay accessible
    (not just buried in the formatted message) so the future --ui json
    event stream + unit tests can inspect them."""
    err = DiskFull(
        "insufficient disk", need_bytes=1_000_000_000, have_bytes=500_000_000
    )
    assert err.need_bytes == 1_000_000_000
    assert err.have_bytes == 500_000_000
    assert "insufficient" in str(err)


def test_disk_full_default_constructor_no_args_works() -> None:
    """For synthetic test raises and ENOSPC translation sites where the
    exact byte counts aren't easily knowable."""
    err = DiskFull()
    assert err.need_bytes is None
    assert err.have_bytes is None


def test_stale_lock_carries_raw_meta_bytes_for_diagnostic_logging() -> None:
    """Phase 1 GAN HIGH#2: corrupt-meta diagnosis keeps the unparseable
    bytes accessible for the WARNING log + Sprint 9 doctor."""
    truncated = b'{"pid": 1234, "started_at": 169'  # truncated mid-write
    err = StaleLock("lock meta corrupt", raw_meta_bytes=truncated)
    assert err.raw_meta_bytes == truncated


def test_stale_lock_default_constructor() -> None:
    """For the dead-PID case where there's nothing to attach."""
    err = StaleLock("dead PID 99999")
    assert err.raw_meta_bytes is None
