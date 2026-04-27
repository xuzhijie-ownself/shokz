"""Sprint 7 Phase 3: RetryPolicy unit tests.

Drives the policy with synthetic coro factories that succeed/fail on demand,
patches asyncio.sleep to keep tests fast, and asserts:
  - Per-class attempt budgets (RateLimited 3+1 default, NetworkError 2+1, etc.)
  - Per-class wait sequences (5/30/120 for RateLimited; backoff_base for others)
  - Terminal classes re-raise immediately
  - Original exception class survives the wrapper (Sprint 7 C2)
  - Wall-clock budget breach raises before the wait
  - on_retry hook fires before each retry's sleep with (err, attempt)
  - coro_factory is called fresh on each attempt (NOT reused)
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from shokz.application.policies.retry import RetryPolicy
from shokz.config.schema import RetrySection
from shokz.domain.errors import (
    AuthRequired,
    DownloadFailed,
    EncodingFailed,
    NetworkError,
    RateLimited,
    SourceFileCorrupt,
    SourceUnavailable,
)

# Capture the REAL asyncio.sleep at module import time (BEFORE the autouse
# instant-sleep fixture replaces it). Used by the smoke test that exercises
# the actual sleep path to prove asyncio integration.
_REAL_ASYNCIO_SLEEP = asyncio.sleep


def _default_policy(**overrides: Any) -> RetryPolicy:
    cfg = RetrySection(**overrides)
    return RetryPolicy(cfg)


class _FlakyCoro:
    """Coro factory that raises `failures.pop(0)` on each call until the list
    is empty, then returns `success_value`. Each call gets a FRESH awaitable
    -- proves the use case isn't accidentally reusing one. Using a class
    instead of a function with `_call_count` attribute keeps mypy --strict
    happy without `# type: ignore` smells (Phase 3 GAN HIGH#3)."""

    def __init__(self, failures: list[Exception], success_value: str = "ok") -> None:
        self._failures = failures
        self._success_value = success_value
        self.call_count = 0

    async def __call__(self) -> str:
        self.call_count += 1
        if self._failures:
            raise self._failures.pop(0)
        return self._success_value


def _flaky(
    failures: list[Exception], success_value: str = "ok"
) -> _FlakyCoro:
    return _FlakyCoro(failures, success_value)


def _calls(coro: _FlakyCoro) -> int:
    return coro.call_count


@pytest.fixture(autouse=True)
def _instant_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make asyncio.sleep instant so tests don't actually wait minutes.
    Real wait sequencing is asserted via the sleep_log fixture below."""

    async def _no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("shokz.application.policies.retry.asyncio.sleep", _no_sleep)


@pytest.fixture
def sleep_log(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Records every asyncio.sleep duration the policy requested (overrides
    the autouse instant-sleep fixture for tests that want to inspect waits)."""
    log: list[float] = []

    async def _record(seconds: float) -> None:
        log.append(seconds)

    monkeypatch.setattr("shokz.application.policies.retry.asyncio.sleep", _record)
    return log


# --- terminal classes (no retry) -----------------------------------------


@pytest.mark.parametrize(
    "exc_class",
    [AuthRequired, SourceUnavailable, EncodingFailed],
)
@pytest.mark.asyncio
async def test_terminal_class_does_not_retry(exc_class: type[Exception]) -> None:
    """Sprint 7: AuthRequired / SourceUnavailable / EncodingFailed = 0 retries."""
    coro = _flaky([exc_class("nope")])
    with pytest.raises(exc_class):
        await _default_policy().run(coro)
    assert _calls(coro) == 1


# --- retryable classes ---------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limited_retries_3_times_total_4_attempts() -> None:
    """RateLimited budget is `max_attempts_rate_limited + 1` total attempts.
    Default config: 3 retries -> 4 total attempts."""
    coro = _flaky([RateLimited("429"), RateLimited("429"), RateLimited("429")])
    result = await _default_policy().run(coro)
    assert result == "ok"
    assert _calls(coro) == 4


@pytest.mark.asyncio
async def test_network_error_retries_2_times_total_3_attempts() -> None:
    coro = _flaky([NetworkError("503"), NetworkError("503")])
    result = await _default_policy().run(coro)
    assert result == "ok"
    assert _calls(coro) == 3


@pytest.mark.asyncio
async def test_source_file_corrupt_retries_1_time_total_2_attempts() -> None:
    coro = _flaky([SourceFileCorrupt("0 bytes")])
    result = await _default_policy().run(coro)
    assert result == "ok"
    assert _calls(coro) == 2


@pytest.mark.asyncio
async def test_download_failed_retries_1_time_total_2_attempts() -> None:
    """DownloadFailed (default fallback) gets the network budget (1 retry)."""
    coro = _flaky([DownloadFailed("unknown")])
    result = await _default_policy().run(coro)
    assert result == "ok"
    assert _calls(coro) == 2


# --- exhaustion: original exception class survives (Sprint 7 C2) --------


@pytest.mark.asyncio
async def test_exhausted_rate_limited_reraises_original_class() -> None:
    """Sprint 7 GAN C2 / silent#1: NO RetryError wrap. The use case's
    _stable_error_class must see RateLimited, not "RetryError" or
    "UNEXPECTED_ERROR"."""
    coro = _flaky([RateLimited("429")] * 10)  # never-succeeds
    with pytest.raises(RateLimited) as exc_info:
        await _default_policy().run(coro)
    # type() check (not isinstance) -- catches accidental wrapping
    assert type(exc_info.value) is RateLimited
    assert _calls(coro) == 4  # 3 retries + 1 original


@pytest.mark.asyncio
async def test_exhausted_network_error_reraises_original_class() -> None:
    coro = _flaky([NetworkError("503")] * 10)
    with pytest.raises(NetworkError) as exc_info:
        await _default_policy().run(coro)
    assert type(exc_info.value) is NetworkError


# --- wait sequencing -----------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limited_uses_exponential_backoff_5_30_120(
    sleep_log: list[float],
) -> None:
    """RateLimited waits: 5s before retry 1, 30s before retry 2, 120s before retry 3."""
    coro = _flaky([RateLimited("429")] * 10)
    with pytest.raises(RateLimited):
        await _default_policy().run(coro)
    assert sleep_log == [5.0, 30.0, 120.0]


@pytest.mark.asyncio
async def test_network_error_uses_linear_backoff(sleep_log: list[float]) -> None:
    """NetworkError waits: backoff_base_s (default 1.0) before each retry.
    Default 2 retries = 3 total attempts = 2 sleeps."""
    coro = _flaky([NetworkError("503")] * 10)
    with pytest.raises(NetworkError):
        await _default_policy().run(coro)
    assert sleep_log == [1.0, 1.0]


@pytest.mark.asyncio
async def test_source_file_corrupt_uses_linear_backoff(sleep_log: list[float]) -> None:
    coro = _flaky([SourceFileCorrupt("0b")] * 10)
    with pytest.raises(SourceFileCorrupt):
        await _default_policy().run(coro)
    assert sleep_log == [1.0]


# --- wall-clock budget ---------------------------------------------------


@pytest.mark.asyncio
async def test_wall_clock_budget_short_circuits_before_long_wait(
    monkeypatch: pytest.MonkeyPatch, sleep_log: list[float]
) -> None:
    """Sprint 7 GAN C4: if the next sleep would breach wall_clock_budget_s,
    re-raise immediately instead of starting the sleep."""
    fake_clock = [0.0]

    def _fake_monotonic() -> float:
        return fake_clock[0]

    monkeypatch.setattr(
        "shokz.application.policies.retry.time.monotonic", _fake_monotonic
    )
    policy = _default_policy(wall_clock_budget_s=10.0)
    coro_failures = [RateLimited("429")] * 5

    async def _attempt() -> str:
        fake_clock[0] += 6.0  # simulate 6s of attempt time
        if coro_failures:
            raise coro_failures.pop(0)
        return "ok"

    with pytest.raises(RateLimited):
        await policy.run(_attempt)
    # No sleeps recorded: even the first 5s wait would breach (6 + 5 = 11 > 10)
    assert sleep_log == []


# --- on_retry hook -------------------------------------------------------


@pytest.mark.asyncio
async def test_on_retry_hook_fires_before_each_retry() -> None:
    """Sprint 7 GAN C6: on_retry runs BEFORE the wait + next attempt so the
    use case can clean up partial .tmp/.webm files."""
    received: list[tuple[str, int]] = []

    async def _hook(err: BaseException, attempt: int) -> None:
        received.append((type(err).__name__, attempt))

    coro = _flaky([SourceFileCorrupt("0b")] * 10)
    with pytest.raises(SourceFileCorrupt):
        await _default_policy().run(coro, on_retry=_hook)
    # SourceFileCorrupt: 1 retry budget = 2 total attempts. Hook fires once
    # (after the 1st failure, before the 2nd attempt).
    assert received == [("SourceFileCorrupt", 1)]


@pytest.mark.asyncio
async def test_on_retry_hook_not_called_when_terminal() -> None:
    received: list[Any] = []

    async def _hook(err: BaseException, attempt: int) -> None:
        received.append((err, attempt))

    coro = _flaky([AuthRequired("auth")])
    with pytest.raises(AuthRequired):
        await _default_policy().run(coro, on_retry=_hook)
    assert received == []  # no retry, no hook


# --- success path --------------------------------------------------------


@pytest.mark.asyncio
async def test_success_on_first_attempt_no_sleep(sleep_log: list[float]) -> None:
    coro = _flaky([])  # no failures
    result = await _default_policy().run(coro)
    assert result == "ok"
    assert _calls(coro) == 1
    assert sleep_log == []


# --- non-ShokzError errors bubble up unchanged --------------------------


@pytest.mark.asyncio
async def test_non_shokz_error_does_not_retry_and_propagates() -> None:
    """RetryPolicy only catches ShokzError. A plain ValueError (programming
    bug) bubbles up unchanged -- the use case's catch-all handles it."""

    async def _attempt() -> str:
        raise ValueError("this is not a domain error")

    with pytest.raises(ValueError, match="not a domain error"):
        await _default_policy().run(_attempt)


# --- coro_factory called fresh per attempt --------------------------------


@pytest.mark.asyncio
async def test_coro_factory_called_fresh_each_attempt() -> None:
    """The wrap signature requires coro_factory be a callable returning
    a FRESH awaitable each call -- not a single awaitable reused. Reusing
    would raise RuntimeError ('cannot reuse already awaited coroutine')."""
    n = {"calls": 0}

    async def _attempt() -> str:
        n["calls"] += 1
        if n["calls"] < 3:
            raise NetworkError("503")
        return "ok"

    # Default config: NetworkError 2 retries -> 3 total attempts.
    result = await _default_policy().run(_attempt)
    assert result == "ok"
    assert n["calls"] == 3


# --- timing test (real but bounded) -------------------------------------


@pytest.mark.asyncio
async def test_rate_limited_gets_long_backoff_not_network_short_backoff(
    sleep_log: list[float],
) -> None:
    """Phase 4 GAN HIGH#1: ordered tuple ensures RateLimited matches its
    OWN spec (3 attempts, 5/30/120 backoff), NOT the NetworkError or
    DownloadFailed spec (2 attempts, 1s backoff). Regression-pin against
    a future edit reordering the spec table."""
    coro = _flaky([RateLimited("429")] * 10)
    with pytest.raises(RateLimited):
        await _default_policy().run(coro)
    # If RateLimited accidentally matched DownloadFailed/NetworkError,
    # we'd see [1.0] (one retry, short backoff) instead of [5,30,120].
    assert sleep_log == [5.0, 30.0, 120.0]
    assert _calls(coro) == 4  # 3 retries + 1 original


@pytest.mark.asyncio
async def test_deadline_rechecked_after_on_retry_hook(
    monkeypatch: pytest.MonkeyPatch, sleep_log: list[float]
) -> None:
    """Phase 4 GAN MED#5: a slow on_retry hook (filesystem cleanup on a
    slow mount) must re-trigger the wall-clock budget check before the
    sleep starts."""
    fake_clock = [0.0]

    def _fake_monotonic() -> float:
        return fake_clock[0]

    monkeypatch.setattr(
        "shokz.application.policies.retry.time.monotonic", _fake_monotonic
    )

    # Budget = 10s. First attempt instant, hook simulates 12s elapsed.
    policy = _default_policy(wall_clock_budget_s=10.0)

    async def _slow_hook(_err: BaseException, _attempt: int) -> None:
        fake_clock[0] += 12.0  # hook itself ate the budget

    coro = _flaky([NetworkError("503")] * 10)
    with pytest.raises(NetworkError):
        await policy.run(coro, on_retry=_slow_hook)

    # No sleep recorded: the post-on_retry deadline check fires first.
    assert sleep_log == []


@pytest.mark.asyncio
async def test_real_sleep_path_uses_actual_asyncio_sleep_when_unmocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Smoke test that with REAL asyncio.sleep + tiny backoff_base, the
    policy actually awaits something (proves U3: not blocking the loop).
    Bounded to <0.5s wall-clock."""
    cfg = RetrySection(
        max_attempts_rate_limited=0,
        max_attempts_network=2,
        max_attempts_corrupt=0,
        backoff_base_s=0.1,  # 100ms (lowest valid per Sprint 7 GAN U5 floor)
        wall_clock_budget_s=5.0,
    )
    policy = RetryPolicy(cfg)
    coro = _flaky([NetworkError("503"), NetworkError("503")])

    # Restore the real asyncio.sleep via monkeypatch (Phase 3 GAN HIGH#2:
    # direct module-attr mutation bypasses monkeypatch's restoration record
    # and could leak `_no_sleep` to subsequent tests). monkeypatch handles
    # the restoration on teardown.
    monkeypatch.setattr(
        "shokz.application.policies.retry.asyncio.sleep", _REAL_ASYNCIO_SLEEP
    )

    started = time.monotonic()
    result = await policy.run(coro)
    elapsed = time.monotonic() - started

    assert result == "ok"
    assert _calls(coro) == 3
    assert 0.15 <= elapsed < 1.0, (
        f"two 100ms sleeps should land ~0.2s, got {elapsed:.3f}"
    )
