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
