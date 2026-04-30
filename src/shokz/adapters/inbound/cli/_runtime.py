"""Sprint 8b + 9 CLI-runtime helpers: cross-process lock + SIGINT handling
+ output-dir safety pre-check.

Shared by `download.py`, `playlist.py`, and `retry.py` so all three CLI
commands enforce the same multi-process safety + interruption +
symlink-rejection semantics.

  - assert_output_dir_safe(config): Sprint 9 / Sprint 8.5 Phase C M1.
    Reject a symlinked `--output` BEFORE the lock is acquired. Previously
    only `BatchDownloadUseCase` rejected symlinks, so `shokz retry`'s
    stat short-circuit could either (a) waste a lock acquire + iter_failures
    read on a symlinked target, or (b) silently exit 0 if the symlinked
    target lacked the failures file. Lifting the check here makes all
    three commands fail fast with the same actionable message.

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
from shokz.domain.errors import NameOutsideOutputDir

_T = TypeVar("_T")
_log = logging.getLogger("shokz.cli.runtime")


def assert_output_dir_safe(config: AppConfig) -> None:
    """Sprint 9 (M1 carry-forward from Sprint 8.5 Phase C): reject a
    symlinked `--output` BEFORE the lock is acquired.

    The output_dir itself OR any of its existing ancestors being a
    symlink raises `NameOutsideOutputDir` with an actionable message.
    A non-existent output_dir is fine -- the use case creates it later.

    Why ancestors, not just the leaf:
      A symlink anywhere in the path means `path.resolve()` may escape
      the user's intended output area, defeating the same protection
      `BatchDownloadUseCase` applies inside the use case. Defense in
      depth + symmetry across all 3 CLI commands.
    """
    output_dir = config.general.output_dir
    # Walk from the deepest existing ancestor outward, checking each
    # for being a symlink. We can't just check `output_dir.is_symlink()`
    # alone because an ancestor symlink would slip through -- but we
    # also can't `resolve()` and compare because resolution silently
    # follows the symlink. is_symlink() per-component is the only safe
    # check.
    path = output_dir
    while True:
        if path.is_symlink():
            raise NameOutsideOutputDir(
                f"output directory {output_dir} is (or has an ancestor that is) "
                f"a symlink at {path}; refusing to write through it. "
                "Pass --output to a real directory or `realpath`-resolve the path."
            )
        parent = path.parent
        if parent == path:
            # Reached filesystem root.
            return
        path = parent


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
