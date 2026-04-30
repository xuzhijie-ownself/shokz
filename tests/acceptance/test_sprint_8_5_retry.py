"""Sprint 8.5 Phase C acceptance tests for `shokz retry`.

Covers Gherkin scenarios from `docs/sprints/sprint-8.5.md` that span the
RetryFailedUseCase + lock + signal-handler boundary:

  9. Lock contention: a second `shokz retry` against a held lock raises
     AnotherRunInProgress with the actionable message.
 10. SIGINT mid-batch: cancellation drains via the same shielded path
     used by `shokz download`.
 11. SIGINT during dedup pre-batch (U3): cancellation in the
     iter_failures phase aborts cleanly with no batch invoked, no
     partial state, lock released by `with` __exit__.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from shokz.application.policies.file_lock import FileLockPolicy
from shokz.application.use_cases.batch_download import BatchDownloadResult
from shokz.application.use_cases.retry_failed import (
    RetryFailedInput,
    RetryFailedUseCase,
)
from shokz.domain.errors import AnotherRunInProgress
from shokz.domain.models import (
    AudioSpec,
    FailureEntry,
    ManifestEntry,
)

# ---------- fakes ----------


def _failure(
    *,
    track_id: str = "abc",
    url: str = "https://x/abc",
    error_class: str = "NETWORK_ERROR",
    failed_at: str = "2026-04-30T12:00:00Z",
) -> FailureEntry:
    return FailureEntry(
        schema_version=1,
        source="youtube",
        track_id=track_id,
        url=url,
        error_class=error_class,
        error_message="synthetic",
        failed_at=failed_at,
    )


@dataclass
class _FakeManifest:
    failures: list[FailureEntry]
    # Optional Event the iterator awaits on first call -- lets a test
    # cancel mid-iter_failures (Gherkin 11 / U3). Default None == yield
    # synchronously. Conftest globally patches asyncio.sleep, so we use
    # an Event instead.
    park: asyncio.Event | None = None
    # M4 (Phase C GAN): tracks whether iter_failures was actually
    # entered, so SIGINT-during-dedup tests can verify the cancellation
    # point is the dedup phase (not an earlier pre-flight check).
    iter_failures_entered: bool = False

    async def record(self, _entry: ManifestEntry) -> None: ...
    async def record_failure(self, _entry: FailureEntry) -> None: ...
    async def find_by_track(self, _s: str, _t: str) -> ManifestEntry | None:
        return None

    async def iter_all(self) -> AsyncIterator[ManifestEntry]:
        if False:
            yield  # pragma: no cover

    async def iter_failures(self) -> AsyncIterator[FailureEntry]:
        self.iter_failures_entered = True
        if self.park is not None:
            await self.park.wait()
        for f in self.failures:
            yield f


def _spec() -> AudioSpec:
    return AudioSpec(codec="mp3", bitrate_kbps=64, channels=1, sample_rate_hz=44100)


def _build(
    failures: list[FailureEntry],
    *,
    park: asyncio.Event | None = None,
    batch_execute: AsyncMock | None = None,
) -> tuple[RetryFailedUseCase, AsyncMock, _FakeManifest]:
    manifest = _FakeManifest(failures=failures, park=park)
    bd = AsyncMock()
    bd.execute = batch_execute or AsyncMock(
        return_value=BatchDownloadResult(results=(), elapsed_s=0.0),
    )
    uc = RetryFailedUseCase(
        manifest=manifest,  # type: ignore[arg-type]
        batch_download=bd,  # type: ignore[arg-type]
    )
    return uc, bd.execute, manifest


def _input(output_dir: Path) -> RetryFailedInput:
    return RetryFailedInput(output_dir=output_dir, spec=_spec())


# ---------- scenarios ----------


def test_filelock_contention_blocks_concurrent_retry(tmp_path: Path) -> None:
    """Gherkin 9: a second FileLockPolicy acquire while the first is
    still held classifies as AnotherRunInProgress.

    `shokz retry` uses the same `with build_output_lock(config):` wrapper
    as `shokz download`, so this is the same lock-contention contract --
    we exercise the policy directly because spawning two CLI processes
    in a unit test is fragile.
    """
    lock_path = tmp_path / "shokz.lock"
    holder = FileLockPolicy(lock_path=lock_path, timeout_s=0.1)
    second = FileLockPolicy(lock_path=lock_path, timeout_s=0.1)

    with holder, pytest.raises(AnotherRunInProgress, match="another shokz process"), second:
        pass


@pytest.mark.asyncio
async def test_sigint_mid_batch_propagates_cancellation_cleanly(
    tmp_path: Path,
) -> None:
    """Gherkin 10: a SIGINT-shaped CancelledError DURING the
    BatchDownloadUseCase delegate phase propagates cleanly out of
    RetryFailedUseCase.execute(). The shield+drain pattern protecting
    manifest writes lives INSIDE BatchDownloadUseCase (covered by
    Sprint 8b's acceptance suite); here we verify that
    RetryFailedUseCase doesn't swallow the CancelledError or leave any
    partial state in its own bookkeeping.
    """
    failures = [
        _failure(track_id="A", url="https://x/A"),
        _failure(track_id="B", url="https://x/B"),
    ]

    # NOTE: tests/conftest.py autouse-patches asyncio.sleep to a no-yield
    # no-op, so `await asyncio.sleep(0)` doesn't drive the scheduler. We
    # use an asyncio.Event the test can set/wait on instead -- Event-
    # driven coordination is unaffected by the sleep patch.
    started = asyncio.Event()
    park = asyncio.Event()

    class _SlowBatch:
        call_count = 0

        async def execute(self, _inp: object) -> BatchDownloadResult:
            type(self).call_count += 1
            started.set()
            await park.wait()
            return BatchDownloadResult(results=(), elapsed_s=0.0)

    manifest = _FakeManifest(failures=failures)
    uc = RetryFailedUseCase(
        manifest=manifest,  # type: ignore[arg-type]
        batch_download=_SlowBatch(),  # type: ignore[arg-type]
    )

    task = asyncio.create_task(uc.execute(_input(tmp_path)))
    try:
        # Wait until the use case has reached _SlowBatch.execute (signaled
        # by `started.set()`). Event.wait suspends the test coroutine and
        # lets the scheduler run the task -- unaffected by the
        # asyncio.sleep monkeypatch in conftest.
        await started.wait()
        assert _SlowBatch.call_count == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        # M3 (Phase C GAN): if the use case ever wraps batch_download in
        # asyncio.shield, task.cancel() above wouldn't propagate to the
        # inner park.wait() -- this hook unblocks it deterministically
        # so the test can never deadlock the suite.
        park.set()


@pytest.mark.asyncio
async def test_sigint_during_dedup_aborts_before_batch_invoke(
    tmp_path: Path,
) -> None:
    """Gherkin 11 / U3: SIGINT-shaped CancelledError WHILE iter_failures
    is still yielding (BEFORE the dedup completes and BEFORE
    BatchDownloadUseCase.execute is invoked) propagates cleanly. The
    batch is NEVER invoked, the use case raises CancelledError, and no
    partial state is leaked into the underlying ports.
    """
    failures = [_failure(track_id=f"t{i}", url=f"https://x/{i}") for i in range(5)]
    park = asyncio.Event()  # never set -> iter_failures parks forever
    uc, bd_execute, manifest = _build(failures, park=park)

    task = asyncio.create_task(uc.execute(_input(tmp_path)))
    # Drive the scheduler by awaiting a future that yields once and
    # resolves. asyncio.sleep is patched to a no-yield in conftest, but
    # awaiting a manually-resolved future DOES yield to the scheduler.
    fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    asyncio.get_running_loop().call_soon(fut.set_result, None)
    await fut
    # M4 (Phase C GAN): verify the task ACTUALLY reached iter_failures
    # before we cancel; otherwise the bd_execute.await_count==0
    # assertion below would pass for the wrong reason (e.g. a pre-flight
    # check raised earlier).
    assert manifest.iter_failures_entered, (
        "task never reached iter_failures -- cancellation point is "
        "earlier than the dedup phase the test claims to exercise"
    )
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # CRITICAL invariant: BatchDownloadUseCase.execute was NEVER called
    # because cancellation fired pre-batch.
    assert bd_execute.await_count == 0
