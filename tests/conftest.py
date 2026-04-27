"""Shared pytest fixtures for the shokz test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def downloads_dir(tmp_path: Path) -> Path:
    """Per-test isolated `downloads/` root with `.tmp/` and `.shokz/` subdirs."""
    root = tmp_path / "downloads"
    (root / ".tmp").mkdir(parents=True)
    (root / ".shokz").mkdir(parents=True)
    return root


@pytest.fixture(autouse=True)
def _instant_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sprint 7 Phase 5 GAN HIGH#1: composition root now wires RetryPolicy
    unconditionally, so any test driving the use case end-to-end (unit OR
    acceptance) would silently incur backoff sleeps if it triggers a
    classified error. Patching `asyncio.sleep` in the retry module to a
    no-op session-wide protects every test from accidentally waiting on
    real backoff. Per-file fixtures with the same effect (in
    test_retry_policy.py + test_sprint_7_retry.py) become redundant but
    safe -- monkeypatch's stack restores cleanly."""

    async def _no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("shokz.application.policies.retry.asyncio.sleep", _no_sleep)
