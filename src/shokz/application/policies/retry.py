"""RetryPolicy -- Sprint 7. Classified retry with per-error-class budgets.

Wrap signature (Sprint 7 GAN U2): `await retry_policy.run(coro_factory,
on_retry=...) -> T`. Coro factory is a zero-arg callable returning a FRESH
awaitable per attempt. Pure application policy: imports only from
domain.errors, config.schema, and stdlib.

Why a custom asyncio loop instead of tenacity.AsyncRetrying:
  tenacity's `@retry` and `AsyncRetrying` take a SINGLE retry strategy per
  call (one stop predicate, one wait strategy). Our budgets vary by ERROR
  CLASS: RateLimited gets 3 attempts with exponential 5s/30s/120s,
  NetworkError gets 2 attempts with linear 1s, SourceFileCorrupt gets 2
  attempts with 1s, terminal classes (Auth/Format/SourceUnavailable) get
  no retry. Mapping that to tenacity would require a custom Stop and a
  custom Wait that both inspect the exception -- which is more code than
  the ~30 lines of pure asyncio below, and harder to test. tenacity stays
  in pyproject.toml for future use; this policy doesn't import it.

GAN findings addressed:
  - C2 (silent#1): no tenacity wrap means no RetryError to swallow the
    real domain error class; the original error type bubbles out unchanged.
  - C4 (architect#3, silent#5): wall-clock = total elapsed including the
    coroutine's own runtime; checked against `time.monotonic() + wait_s`
    BEFORE sleeping (don't start a sleep we know would breach budget).
  - C6 (architect#1, silent#3): `on_retry` hook fires BEFORE the next
    attempt and BEFORE the sleep, so the use case can clean up partial
    .tmp/.webm before yt-dlp re-attempts.
  - U3 (py-rev#1): asyncio.sleep, not time.sleep -- never blocks loop.
  - U4 (py-rev#3): retry-class match via isinstance, not __name__.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from shokz.config.schema import RetrySection
from shokz.domain.errors import (
    DownloadFailed,
    NetworkError,
    RateLimited,
    ShokzError,
    SourceFileCorrupt,
)

T = TypeVar("T")

_log = logging.getLogger("shokz.policy.retry")

# Backoff sequence for RateLimited (long, exponential). The use case only
# ever consumes the first `max_attempts_rate_limited` values; len(seq) >=
# the cap (5) so config.max_attempts_rate_limited <= 5 always indexes safely.
_RATE_LIMITED_BACKOFF_S: tuple[float, ...] = (5.0, 30.0, 120.0, 300.0, 600.0)


@dataclass(frozen=True, slots=True)
class _RetrySpec:
    """Per-class plan: total attempts (>=1) and per-retry wait sequence
    (len == max_attempts - 1; index i is the wait BEFORE attempt i+1)."""

    max_attempts: int
    waits: tuple[float, ...]


class RetryPolicy:
    """Classified retry with per-error-class budgets and wall-clock cap."""

    def __init__(self, config: RetrySection) -> None:
        self._config = config
        # Build the per-class spec table once; isinstance lookup at retry time.
        # Phase 4 GAN HIGH#1: ordered TUPLE (not dict) so future edits can't
        # accidentally reorder entries and silently swap retry budgets between
        # classes. Mirrors batch_download._ERROR_CLASS_MAP's design.
        rate_attempts = config.max_attempts_rate_limited + 1
        net_attempts = config.max_attempts_network + 1
        corrupt_attempts = config.max_attempts_corrupt + 1
        self._specs: tuple[tuple[type[ShokzError], _RetrySpec], ...] = (
            # Most-specific-first: today none of these subclass each other,
            # but if a future YoutubeRateLimited(RateLimited) lands, putting
            # subclasses BEFORE bases keeps isinstance resolution correct.
            (
                RateLimited,
                _RetrySpec(
                    max_attempts=rate_attempts,
                    waits=_RATE_LIMITED_BACKOFF_S[: rate_attempts - 1],
                ),
            ),
            (
                NetworkError,
                _RetrySpec(
                    max_attempts=net_attempts,
                    waits=(config.backoff_base_s,) * (net_attempts - 1),
                ),
            ),
            (
                SourceFileCorrupt,
                _RetrySpec(
                    max_attempts=corrupt_attempts,
                    waits=(config.backoff_base_s,) * (corrupt_attempts - 1),
                ),
            ),
            # DownloadFailed comes LAST: it's the catch-all default for
            # unrecognized yt-dlp errors and any future class that subclasses
            # it should hit a more-specific spec above.
            (
                DownloadFailed,
                _RetrySpec(
                    max_attempts=net_attempts,
                    waits=(config.backoff_base_s,) * (net_attempts - 1),
                ),
            ),
        )

    def _spec_for(self, err: BaseException) -> _RetrySpec | None:
        """Return retry spec for err's class, or None if class is terminal
        (no retry). Sprint 7 GAN U4: isinstance, not __name__ -- subclass-safe."""
        for exc_class, spec in self._specs:
            if isinstance(err, exc_class):
                return spec
        return None

    async def run(
        self,
        coro_factory: Callable[[], Awaitable[T]],
        on_retry: Callable[[BaseException, int], Awaitable[None]] | None = None,
    ) -> T:
        """Execute `coro_factory()` with classified retry.

        Returns the successful result. On exhaustion, re-raises the FINAL
        domain error UNWRAPPED (Sprint 7 C2). The retry class is determined
        by the FIRST exception thrown -- subsequent attempts that throw a
        different class do NOT change the budget (the alternative is more
        complex semantics for little gain).

        on_retry hook fires BEFORE each retry attempt's wait, with
        (exception, attempt_number_just_failed). Used by the use case to
        clean up partial files before yt-dlp re-attempts (Sprint 7 C6).

        IMPORTANT (Phase 3 GAN HIGH#1): on_retry does NOT fire on FINAL-
        failure raises -- neither when the class is terminal (no retry) nor
        when retries are exhausted nor when wall-clock budget breaches. The
        caller is responsible for cleanup AFTER `run` raises (e.g. via a
        try/finally around the call site). The hook is for "we're about to
        try again" cleanup only.

        IMPORTANT (Phase 3 GAN MED#1): the RateLimited backoff sequence
        (5s/30s/120s) is HARDCODED in `_RATE_LIMITED_BACKOFF_S`. The
        `backoff_base_s` config knob does NOT affect RateLimited waits;
        it only affects NetworkError, DownloadFailed, and SourceFileCorrupt.
        Rationale: 429 responses need long backoffs to actually escape
        the throttle window; making this config-tunable invites users to
        set it too low and re-trigger throttling.
        """
        deadline = time.monotonic() + self._config.wall_clock_budget_s
        attempt = 0
        spec: _RetrySpec | None = None

        while True:
            attempt += 1
            try:
                return await coro_factory()
            except ShokzError as err:
                # Classify on first failure; sticky for the rest of the loop.
                if spec is None:
                    spec = self._spec_for(err)
                # Terminal class OR exhausted retries -> re-raise original.
                if spec is None or attempt >= spec.max_attempts:
                    raise
                # Determine wait BEFORE potentially breaching wall-clock.
                wait_s = spec.waits[attempt - 1]
                now = time.monotonic()
                if now + wait_s > deadline:
                    _log.warning(
                        "retry budget breached for %s (attempt=%d, would wait %.1fs); giving up",
                        type(err).__name__,
                        attempt,
                        wait_s,
                    )
                    raise
                _log.info(
                    "retrying after %s (attempt %d -> %d, wait %.1fs)",
                    type(err).__name__,
                    attempt,
                    attempt + 1,
                    wait_s,
                )
                # Cleanup hook BEFORE the wait -- so a partial .tmp file
                # doesn't sit around during a long sleep.
                if on_retry is not None:
                    await on_retry(err, attempt)
                # Phase 4 GAN MED#5: re-check budget AFTER on_retry (which
                # may do unbounded I/O on slow mounts). Don't start a sleep
                # we now know would breach the deadline.
                if time.monotonic() + wait_s > deadline:
                    _log.warning(
                        "retry budget breached after on_retry hook for %s; giving up",
                        type(err).__name__,
                    )
                    raise
                await asyncio.sleep(wait_s)
