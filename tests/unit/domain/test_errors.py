"""Sprint 7 Phase 1 GAN: tiny tests for the new error classes.

Verifies that the domain error taxonomy added in Phase 1 is structurally
correct (subclassing, optional constructor args). The Phase 2 classifier
+ Phase 3 RetryPolicy will exercise the semantic side; these are pure
shape tests so a regression on the class hierarchy is caught fast.
"""

from __future__ import annotations

import pytest

from shokz.domain.errors import (
    AuthRequired,
    DownloadFailed,
    FormatUnavailable,
    NetworkError,
    RateLimited,
    ShokzError,
    SourceUnavailable,
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
