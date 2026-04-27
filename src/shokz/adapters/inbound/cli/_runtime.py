"""Sprint 8b CLI-runtime helpers: cross-process lock + SIGINT handling.

Shared by `download.py` and `playlist.py` so the two CLI commands enforce
the same multi-process safety + interruption semantics.

  - build_output_lock(config): construct a FileLockPolicy on
    `<output_dir>/.shokz/locks/shokz.lock`. Caller uses it as a sync
    context manager *outside* asyncio.run (Sprint 8 GAN M1 -- the lock
    must NOT be held inside an event loop because a SIGINT-driven
    `loop.close()` would skip our `__exit__`).

  - run_async_with_sigint(coro): asyncio.run + a SIGINT handler that
    (a) cancels the main task (which the asyncio.shield in
    BatchDownloadUseCase converts into a clean drain instead of an orphan
    manifest), (b) self-removes so a second Ctrl+C kills hard (Sprint 8
    GAN L1).
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Coroutine
from typing import TypeVar

from shokz.application.policies.file_lock import FileLockPolicy
from shokz.config.schema import AppConfig

_T = TypeVar("_T")
_log = logging.getLogger("shokz.cli.runtime")


def build_output_lock(config: AppConfig) -> FileLockPolicy:
    """Construct the per-output_dir advisory lock from config."""
    state_dir = config.general.output_dir / ".shokz" / "locks"
    return FileLockPolicy(
        lock_path=state_dir / "shokz.lock",
        timeout_s=config.lock.timeout_s,
    )


def run_async_with_sigint(coro: Coroutine[object, object, _T]) -> _T:
    """Wrap asyncio.run with a single-shot SIGINT handler.

    First Ctrl+C: cancels the main task. The use-case's `asyncio.shield`
    around `manifest.record` drains pending writes before the cancellation
    propagates, so an interrupted batch leaves a consistent manifest.

    Second Ctrl+C: restores SIG_DFL so the user can force-kill.
    """
    sigint_count = [0]

    async def _main() -> _T:
        loop = asyncio.get_running_loop()
        main_task = asyncio.current_task()

        def _on_sigint() -> None:
            sigint_count[0] += 1
            if sigint_count[0] > 1:
                # Second Ctrl+C: restore default handler so the next signal
                # kills the process. We do NOT raise immediately; the next
                # SIGINT will hit SIG_DFL.
                signal.signal(signal.SIGINT, signal.SIG_DFL)
                _log.warning("second Ctrl+C: next signal will exit hard")
                return
            _log.warning(
                "Ctrl+C received: cancelling in-flight downloads "
                "(manifest writes are shielded; press Ctrl+C again to "
                "force-exit)"
            )
            if main_task is not None:
                main_task.cancel()

        try:
            loop.add_signal_handler(signal.SIGINT, _on_sigint)
        except (NotImplementedError, RuntimeError):
            # E.g. nested loop, alt-thread; fall back to default behaviour.
            _log.debug("loop.add_signal_handler unavailable; using default SIGINT")
        return await coro

    try:
        return asyncio.run(_main())
    except asyncio.CancelledError:
        # GAN HIGH#1: loop.add_signal_handler replaces asyncio.Runner's
        # internal SIGINT handler, so Runner never increments its
        # `_interrupt_count` and never converts our CancelledError back
        # to KeyboardInterrupt. We do that conversion here so the CLI's
        # `except KeyboardInterrupt` branch fires and we exit 130 cleanly.
        raise KeyboardInterrupt from None
