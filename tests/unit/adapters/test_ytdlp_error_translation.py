"""Sprint 7 Phase 2: §7.1 error translation table tests.

Enumerates every classification row + the multi-match precedence case
(Sprint 7 GAN C5) + the unclassified-fallback path. Without this test
file, drift between yt-dlp's evolving error message strings and our
translation table would silently regress to "DownloadFailed for everything".
"""

from __future__ import annotations

import logging

import pytest

from shokz.adapters.outbound.ytdlp_source import _classify_message
from shokz.domain.errors import (
    AuthRequired,
    DownloadFailed,
    FormatUnavailable,
    NetworkError,
    RateLimited,
    SourceUnavailable,
)


@pytest.mark.parametrize(
    ("msg", "expected"),
    [
        # AuthRequired -- terminal
        ("Sign in to confirm your age", AuthRequired),
        ("Sign in to confirm you're not a bot", AuthRequired),
        ("This video is not available in your country", AuthRequired),
        ("Members-only content", AuthRequired),
        ("This video is private", AuthRequired),
        # FormatUnavailable -- terminal
        ("Requested format is not available", FormatUnavailable),
        ("Requested format not available", FormatUnavailable),
        ("No audio formats found", FormatUnavailable),
        # SourceUnavailable -- terminal
        ("Private video", SourceUnavailable),
        ("Video unavailable", SourceUnavailable),
        ("This video has been removed", SourceUnavailable),
        ("Removed by the uploader", SourceUnavailable),
        ("Video has been deleted", SourceUnavailable),
        # RateLimited -- retry, long backoff
        ("HTTP Error 429: Too Many Requests", RateLimited),
        ("Too many requests, slow down", RateLimited),
        # NetworkError -- retry, short backoff
        ("HTTP Error 503: Service Unavailable", NetworkError),
        ("HTTP Error 502: Bad Gateway", NetworkError),
        ("HTTP Error 500: Internal Server Error", NetworkError),
        ("Connection reset by peer", NetworkError),
        ("Connection refused", NetworkError),
        ("Name or service not known", NetworkError),
        ("Read operation timed out", NetworkError),
    ],
)
def test_classification_table_matches_each_row(
    msg: str, expected: type[Exception]
) -> None:
    """Each §7.1 row classifies to its declared domain error class.

    Sprint 7 GAN review MED#3: also pin the hierarchy. If a future refactor
    accidentally moves AuthRequired (etc.) out from under ShokzError, this
    parametrized test alone wouldn't catch it (isinstance(X, X) is always
    true) -- so we assert ShokzError ancestry explicitly.
    """
    from shokz.domain.errors import ShokzError

    result = _classify_message(msg)
    assert isinstance(result, expected)
    assert isinstance(result, ShokzError), (
        f"{type(result).__name__} no longer subclasses ShokzError -- "
        "use case's `except ShokzError` would silently miss it"
    )
    assert str(result) == msg  # original message preserved for failure log


@pytest.mark.parametrize(
    ("msg", "expected"),
    [
        # GAN MED#4 extensions
        ("is not available in your country", AuthRequired),  # prefix-less
        ("This content is not available", SourceUnavailable),
        ("Premiere will begin shortly", SourceUnavailable),
        ("Live stream recording not available", SourceUnavailable),
        ("Failed to extract any player response", SourceUnavailable),
        ("Unable to extract initial player response", SourceUnavailable),
    ],
)
def test_extended_classification_rows(
    msg: str, expected: type[Exception]
) -> None:
    """Sprint 7 GAN review MED#4: real-world variants the original draft
    missed. These either retry pointlessly (premiere/live) or get the wrong
    error_class in failures.jsonl (region-lock without prefix)."""
    result = _classify_message(msg)
    assert isinstance(result, expected)


def test_full_stderr_blob_classifies_when_error_is_not_on_last_line() -> None:
    """Sprint 7 GAN review HIGH#1: the C1 fix passes the FULL stderr blob to
    _classify_message, NOT just the last line. yt-dlp commonly puts the
    actionable error on line N-1 and a generic advisory on line N. With
    last-line-only matching, age-gate / 429 errors silently miss the table."""
    multi_line = (
        "[debug] [youtube] dQw...: Downloading webpage\n"
        "ERROR: [youtube] dQw...: Sign in to confirm your age\n"
        "Try this with --verbose for more info"  # generic advisory on last line
    )
    result = _classify_message(multi_line)
    assert isinstance(result, AuthRequired), (
        "classifier must scan the full blob; classifying only the last "
        "line would miss the auth signal on line N-1"
    )


def test_case_insensitive_match() -> None:
    """yt-dlp message capitalization varies between extractor versions."""
    assert isinstance(_classify_message("HTTP ERROR 429"), RateLimited)
    assert isinstance(_classify_message("http error 429"), RateLimited)
    assert isinstance(_classify_message("private VIDEO"), SourceUnavailable)


def test_precedence_auth_beats_rate_limit_in_combined_message() -> None:
    """Sprint 7 GAN C5: terminal-first precedence. A real yt-dlp message
    can chain ('Sign in to confirm your age... HTTP Error 429'); the
    classifier MUST return AuthRequired, NOT RateLimited. Otherwise auth
    errors burn 3 retries pointlessly."""
    combined = "Sign in to confirm your age... [later] HTTP Error 429"
    result = _classify_message(combined)
    assert isinstance(result, AuthRequired)
    assert not isinstance(result, RateLimited)


def test_precedence_format_beats_network() -> None:
    """If a 5xx and a 'format not available' both surface, format wins
    (terminal -- retry won't help)."""
    combined = "Requested format is not available; HTTP Error 503"
    assert isinstance(_classify_message(combined), FormatUnavailable)


def test_precedence_source_unavailable_beats_rate_limit() -> None:
    """A 'private video' message that ALSO mentions 429 (some extractors
    chain the cause) classifies as SourceUnavailable; deleted videos
    don't come back from a retry."""
    combined = "Private video; HTTP Error 429"
    assert isinstance(_classify_message(combined), SourceUnavailable)


def test_unclassified_falls_through_to_download_failed() -> None:
    """A novel yt-dlp message must NOT crash; it must default to
    DownloadFailed (which retries ONCE per spec)."""
    novel = "Some completely new yt-dlp error message we have not seen"
    result = _classify_message(novel)
    assert isinstance(result, DownloadFailed)
    assert str(result) == novel


def test_unclassified_emits_warning_with_full_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Sprint 7 GAN U8 / silent#6: WARNING log carries the FULL raw message
    (not truncated) so a future §7.1 update can grep yesterday's logs."""
    novel = "Some completely new yt-dlp error message x" * 20  # long
    with caplog.at_level(logging.WARNING, logger="shokz.adapter.ytdlp"):
        _classify_message(novel)
    assert any(
        "unclassified" in rec.message.lower() and novel in rec.message
        for rec in caplog.records
    ), "WARNING must carry the unredacted message for §7.1 triage"
